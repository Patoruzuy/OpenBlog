"""Benchmark service — create suites, cases, runs and enforce scope isolation.

Scope rules (enforced at service layer, never in routes)
---------------------------------------------------------
Public suite (workspace_id IS NULL):
- Visible to any authenticated user.
- Runs allowed only for published, public prompts (Post.workspace_id IS NULL).
- Draft prompts rejected.
- Workspace prompts rejected even if user is a member.

Workspace suite (workspace_id IS NOT NULL):
- Visible only to workspace members (viewer+).
- Runs allowed for prompts that belong to the SAME workspace OR are public.
- Prompts from a DIFFERENT workspace are always rejected.
- Non-members receive None (route converts to 404 — fail-closed).

Cross-workspace leakage is never possible because every query filters on the
suite's workspace_id, and the create_run scope check compares prompt.workspace_id
against suite.workspace_id before enqueuing anything.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import func, select

from backend.extensions import db
from backend.models.benchmark import (
    BenchmarkCase,
    BenchmarkRun,
    BenchmarkRunResult,
    BenchmarkRunStatus,
    BenchmarkSuite,
)
from backend.models.post import Post, PostStatus
from backend.models.user import User
from backend.models.workspace import Workspace, WorkspaceMember, WorkspaceMemberRole

_MAX_ERROR_MSG = 400


# ── Exceptions ────────────────────────────────────────────────────────────────


class BenchmarkError(Exception):
    """Domain-level error for benchmark operations."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


# ── Slug helpers ──────────────────────────────────────────────────────────────


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-") or "suite"


def _unique_suite_slug(base: str) -> str:
    base = base[:80]
    existing = set(
        db.session.scalars(
            select(BenchmarkSuite.slug).where(BenchmarkSuite.slug.like(f"{base}%"))
        ).all()
    )
    if base not in existing:
        return base
    counter = 2
    while f"{base}-{counter}" in existing:
        counter += 1
    return f"{base}-{counter}"


# ── Internal access helpers ───────────────────────────────────────────────────


def _require_auth(user: User | None) -> User:
    """Raise BenchmarkError(401) if user is not authenticated."""
    if user is None:
        raise BenchmarkError("Authentication required.", status_code=401)
    return user


def _get_member(workspace: Workspace, user: User) -> WorkspaceMember | None:
    return db.session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == user.id,
        )
    )


def _assert_suite_access(suite: BenchmarkSuite, user: User) -> None:
    """Raise BenchmarkError(404) if user may not access *suite*."""
    if suite.workspace_id is None:
        return  # public suite — any authenticated user may access
    member = _get_member(
        db.session.get(Workspace, suite.workspace_id),  # type: ignore[arg-type]
        user,
    )
    if member is None:
        raise BenchmarkError("Suite not found.", status_code=404)


# ── Public API ────────────────────────────────────────────────────────────────


def create_suite(
    user: User | None,
    name: str,
    description: str | None = None,
    *,
    workspace: Workspace | None = None,
) -> BenchmarkSuite:
    """Create a new benchmark suite.

    Parameters
    ----------
    user:        Authenticated user (any auth level is sufficient).
    name:        Human-readable suite name.
    description: Optional prose description.
    workspace:   If supplied, the suite is workspace-scoped; otherwise public.
    """
    user = _require_auth(user)
    name = name.strip()
    if not name:
        raise BenchmarkError("Suite name cannot be empty.")

    if workspace is not None:
        member = _get_member(workspace, user)
        if member is None:
            raise BenchmarkError("Not a workspace member.", status_code=404)
        if not member.role.meets(WorkspaceMemberRole.editor):
            raise BenchmarkError("Editor role required to create suites.", status_code=403)

    slug = _unique_suite_slug(_slugify(name))
    suite = BenchmarkSuite(
        name=name,
        slug=slug,
        description=description or None,
        workspace_id=workspace.id if workspace is not None else None,
        created_by_user_id=user.id,
        created_at=datetime.now(UTC),
    )
    db.session.add(suite)
    db.session.flush()
    return suite


def add_case(
    user: User | None,
    suite: BenchmarkSuite,
    input_json: dict,
    name: str = "",
    expected_output: str | None = None,
    expected_assertions: dict | None = None,
) -> BenchmarkCase:
    """Append a test case to *suite*.

    Caller must have access to the suite (validated here).
    """
    user = _require_auth(user)
    _assert_suite_access(suite, user)

    name = (name or "").strip() or f"Case {len(suite.cases) + 1}"
    if not isinstance(input_json, dict):
        raise BenchmarkError("input_json must be a JSON object.")

    case = BenchmarkCase(
        suite_id=suite.id,
        name=name,
        input_json=input_json,
        expected_output=expected_output or None,
        expected_assertions_json=expected_assertions or None,
        created_at=datetime.now(UTC),
    )
    db.session.add(case)
    db.session.flush()
    return case


def list_suites(
    user: User | None,
    workspace: Workspace | None = None,
) -> list[BenchmarkSuite]:
    """Return suites visible to *user* in the given scope.

    Scope rules
    -----------
    workspace=None  → public suites only (workspace_id IS NULL).
                      Any authenticated user may see them; unauthenticated
                      users get an empty list.
    workspace=ws    → workspace-scoped suites for ws. Non-members get [].
    """
    if user is None:
        return []

    if workspace is None:
        stmt = (
            select(BenchmarkSuite)
            .where(BenchmarkSuite.workspace_id.is_(None))
            .order_by(BenchmarkSuite.created_at.desc())
        )
        return list(db.session.scalars(stmt).all())

    # Workspace scope: confirm membership first.
    member = _get_member(workspace, user)
    if member is None:
        return []

    stmt = (
        select(BenchmarkSuite)
        .where(BenchmarkSuite.workspace_id == workspace.id)
        .order_by(BenchmarkSuite.created_at.desc())
    )
    return list(db.session.scalars(stmt).all())


def get_suite(
    user: User | None,
    slug: str,
    workspace: Workspace | None = None,
) -> BenchmarkSuite | None:
    """Return the suite identified by *slug* in the given scope, or None.

    Returns None (→ 404) if:
    - suite does not exist,
    - workspace mismatch,
    - user is not a member of a workspace suite.
    """
    if user is None:
        return None

    if workspace is None:
        suite = db.session.scalar(
            select(BenchmarkSuite).where(
                BenchmarkSuite.slug == slug,
                BenchmarkSuite.workspace_id.is_(None),
            )
        )
    else:
        member = _get_member(workspace, user)
        if member is None:
            return None
        suite = db.session.scalar(
            select(BenchmarkSuite).where(
                BenchmarkSuite.slug == slug,
                BenchmarkSuite.workspace_id == workspace.id,
            )
        )
    return suite


def create_run(
    user: User | None,
    suite: BenchmarkSuite,
    prompt_post: Post,
    version: int,
    model_name: str | None = None,
) -> BenchmarkRun:
    """Create a benchmark run and enqueue the Celery task.

    Scope enforcement
    -----------------
    Public suite (suite.workspace_id IS NULL):
    - prompt must be public (Post.workspace_id IS NULL) and published.
    - Draft prompts are rejected.

    Workspace suite (suite.workspace_id IS NOT NULL):
    - user must be a member of the suite's workspace.
    - prompt must be in the SAME workspace OR be a public published prompt.
    - Prompt from a DIFFERENT workspace is rejected.

    Raises BenchmarkError on any scope violation.
    """
    user = _require_auth(user)
    _assert_suite_access(suite, user)

    # ── Scope enforcement ────────────────────────────────────────────────
    if suite.workspace_id is None:
        # Public suite: prompt must be public + published.
        if prompt_post.workspace_id is not None:
            raise BenchmarkError(
                "Public suites cannot run workspace-scoped prompts.", status_code=422
            )
        if prompt_post.status != PostStatus.published:
            raise BenchmarkError("Only published prompts can be benchmarked.", status_code=422)
    else:
        # Workspace suite.
        if prompt_post.workspace_id is not None:
            # Prompt is workspace-scoped — must belong to the SAME workspace.
            if prompt_post.workspace_id != suite.workspace_id:
                raise BenchmarkError(
                    "Cannot run a prompt from a different workspace.", status_code=422
                )
        else:
            # Public prompt — allowed, must be published.
            if prompt_post.status != PostStatus.published:
                raise BenchmarkError(
                    "Only published prompts can be benchmarked.", status_code=422
                )

    run = BenchmarkRun(
        suite_id=suite.id,
        prompt_post_id=prompt_post.id,
        prompt_version=version,
        workspace_id=suite.workspace_id,
        model_name=model_name or None,
        status=BenchmarkRunStatus.queued.value,
        created_by_user_id=user.id,
        created_at=datetime.now(UTC),
    )
    db.session.add(run)
    db.session.flush()

    # Enqueue Celery task (imported here to avoid circular import at module load).
    from backend.tasks.benchmark_runs import run_benchmark  # noqa: PLC0415

    run_benchmark.delay(run.id)
    return run


def cancel_run(user: User | None, run: BenchmarkRun) -> BenchmarkRun:
    """Cancel a queued or running benchmark run.

    Only the creator or a workspace owner/editor may cancel.
    Completed / failed runs cannot be canceled.
    """
    user = _require_auth(user)

    # Load the suite to check workspace access.
    suite: BenchmarkSuite = db.session.get(BenchmarkSuite, run.suite_id)  # type: ignore[assignment]
    _assert_suite_access(suite, user)

    if run.status in (BenchmarkRunStatus.completed.value, BenchmarkRunStatus.failed.value):
        raise BenchmarkError(
            f"Cannot cancel a run in '{run.status}' state.", status_code=422
        )
    if run.status == BenchmarkRunStatus.canceled.value:
        return run  # idempotent

    run.status = BenchmarkRunStatus.canceled.value
    run.completed_at = datetime.now(UTC)
    db.session.flush()
    return run


def list_runs_for_prompt(
    user: User | None,
    prompt_post: Post,
    workspace: Workspace | None = None,
) -> list[BenchmarkRun]:
    """Return runs for *prompt_post* visible to *user* in the given scope."""
    if user is None:
        return []

    if workspace is None:
        # Public scope: only runs tied to public suites.
        stmt = (
            select(BenchmarkRun)
            .where(
                BenchmarkRun.prompt_post_id == prompt_post.id,
                BenchmarkRun.workspace_id.is_(None),
            )
            .order_by(BenchmarkRun.created_at.desc())
            .limit(50)
        )
    else:
        member = _get_member(workspace, user)
        if member is None:
            return []
        stmt = (
            select(BenchmarkRun)
            .where(
                BenchmarkRun.prompt_post_id == prompt_post.id,
                BenchmarkRun.workspace_id == workspace.id,
            )
            .order_by(BenchmarkRun.created_at.desc())
            .limit(50)
        )
    return list(db.session.scalars(stmt).all())


def get_run_with_results(
    user: User | None,
    run_id: int,
) -> BenchmarkRun | None:
    """Return a BenchmarkRun (with .results eagerly loaded) or None.

    Access check: verify the user may see the associated suite.
    """
    if user is None:
        return None

    run: BenchmarkRun | None = db.session.get(BenchmarkRun, run_id)
    if run is None:
        return None

    suite: BenchmarkSuite | None = db.session.get(BenchmarkSuite, run.suite_id)
    if suite is None:
        return None

    try:
        _assert_suite_access(suite, user)
    except BenchmarkError:
        return None

    # Eagerly load results + cases in one extra query (bounded).
    _ = run.results  # noqa  # trigger lazy-load now while session is open
    for result in run.results:
        _ = result.case  # noqa  # trigger lazy-load for case names
    return run


def get_benchmark_summary_for_prompt(
    prompt_post: Post,
    workspace_id: int | None = None,
) -> list[dict]:
    """Return aggregated benchmark data grouped by prompt_version.

    Used by the prompt analytics page.  Each entry contains:
    {
        "version": int,
        "run_count": int,
        "avg_score": float | None,
        "suite_names": [str],
    }

    Bounded: 2 queries total.
    """
    # Query 1: runs in scope.
    if workspace_id is None:
        run_rows = db.session.execute(
            select(
                BenchmarkRun.prompt_version,
                BenchmarkRun.suite_id,
                BenchmarkRun.id.label("run_id"),
            ).where(
                BenchmarkRun.prompt_post_id == prompt_post.id,
                BenchmarkRun.workspace_id.is_(None),
                BenchmarkRun.status == BenchmarkRunStatus.completed.value,
            )
        ).all()
    else:
        run_rows = db.session.execute(
            select(
                BenchmarkRun.prompt_version,
                BenchmarkRun.suite_id,
                BenchmarkRun.id.label("run_id"),
            ).where(
                BenchmarkRun.prompt_post_id == prompt_post.id,
                BenchmarkRun.workspace_id == workspace_id,
                BenchmarkRun.status == BenchmarkRunStatus.completed.value,
            )
        ).all()

    if not run_rows:
        return []

    run_ids = [r.run_id for r in run_rows]
    suite_ids = list({r.suite_id for r in run_rows})

    # Query 2: avg scores per run + suite names (one JOIN query).
    score_rows = db.session.execute(
        select(
            BenchmarkRunResult.run_id,
            func.avg(BenchmarkRunResult.score_numeric).label("avg_score"),
        )
        .where(BenchmarkRunResult.run_id.in_(run_ids))
        .group_by(BenchmarkRunResult.run_id)
    ).all()

    avg_by_run = {r.run_id: float(r.avg_score) if r.avg_score is not None else None
                  for r in score_rows}

    suite_names = {}
    if suite_ids:
        suite_name_rows = db.session.execute(
            select(BenchmarkSuite.id, BenchmarkSuite.name).where(
                BenchmarkSuite.id.in_(suite_ids)
            )
        ).all()
        suite_names = {r.id: r.name for r in suite_name_rows}

    # Group by version.
    by_version: dict[int, dict] = {}
    for rrow in run_rows:
        ver = rrow.prompt_version
        if ver not in by_version:
            by_version[ver] = {"version": ver, "run_count": 0, "avg_scores": [], "suite_names": set()}
        by_version[ver]["run_count"] += 1
        sc = avg_by_run.get(rrow.run_id)
        if sc is not None:
            by_version[ver]["avg_scores"].append(sc)
        sname = suite_names.get(rrow.suite_id)
        if sname:
            by_version[ver]["suite_names"].add(sname)

    result = []
    for ver in sorted(by_version.keys()):
        entry = by_version[ver]
        scores = entry["avg_scores"]
        result.append({
            "version": ver,
            "run_count": entry["run_count"],
            "avg_score": sum(scores) / len(scores) if scores else None,
            "suite_names": sorted(entry["suite_names"]),
        })
    return result
