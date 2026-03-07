"""SSR — workspace routes (private document containers).

URL structure
-------------
GET  /w/<workspace_slug>                                 dashboard
GET  /w/<workspace_slug>/docs/new                        new document form  [editor+]
POST /w/<workspace_slug>/docs/new                        create document    [editor+]
GET  /w/<workspace_slug>/docs/<doc_slug>                 view document      [viewer+]
GET  /w/<workspace_slug>/docs/<doc_slug>/edit            edit form          [editor+]
POST /w/<workspace_slug>/docs/<doc_slug>/edit            apply edits        [editor+]
POST /w/<workspace_slug>/docs/<doc_slug>/clone-to-public clone to draft     [editor+]
GET  /w/<workspace_slug>/compare                         compare versions   [viewer+]
GET  /w/<workspace_slug>/changelog                       revision history   [viewer+]

GET  /w/<workspace_slug>/members                         member list        [owner/admin]
POST /w/<workspace_slug>/members/<int:user_id>/role      change role        [owner/admin]
POST /w/<workspace_slug>/members/<int:user_id>/remove    remove member      [owner/admin]
GET  /w/<workspace_slug>/invites                         invite list        [owner/admin]
POST /w/<workspace_slug>/invites/new                     create invite      [owner/admin]
GET  /w/<workspace_slug>/invites/<int:invite_id>/created show invite link   [owner/admin]

Cache policy
------------
ALL workspace responses carry ``Cache-Control: private, no-store`` via an
``after_request`` hook so intermediary caches never store private content.
This is enforced blueprint-wide; individual handlers do not need to set it.

Permission enforcement
----------------------
Every handler calls :func:`~backend.services.workspace_service.get_workspace_for_user`
as its **first** action — 404 on non-membership (never 403, no existence hint).
"""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from backend.extensions import db
from backend.models.workspace import WorkspaceMemberRole
from backend.security.permissions import PermissionService
from backend.services import invite_service as inv_svc
from backend.services import playbook_service as pb_svc
from backend.services import workspace_service as ws_svc
from backend.services.workspace_health_service import WorkspaceHealthService
from backend.utils.auth import get_current_user, require_auth
from backend.utils.diff import compute_diff, parse_diff_lines

workspace_bp = Blueprint("workspace", __name__, url_prefix="/w")


# ── Blueprint-wide Cache-Control ──────────────────────────────────────────────


@workspace_bp.after_request
def _no_store(response):
    """Enforce private, no-store on every workspace response (INV-cache)."""
    response.headers["Cache-Control"] = "private, no-store"
    response.headers.pop("Expires", None)
    response.headers.pop("Pragma", None)
    return response


# ── Helpers ───────────────────────────────────────────────────────────────────


def _current_member_role(workspace, user):
    """Return the calling user's WorkspaceMember or None."""
    if user is None:
        return None
    return ws_svc.get_member(workspace, user)


@workspace_bp.get("/<workspace_slug>")
@require_auth
def dashboard(workspace_slug: str):
    """List all documents in the workspace."""
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(workspace_slug, user)
    member = _current_member_role(workspace, user)

    documents = ws_svc.list_workspace_documents(workspace)

    return render_template(
        "workspace/dashboard.html",
        workspace=workspace,
        documents=documents,
        member=member,
    )


# ── Create document ───────────────────────────────────────────────────────────


@workspace_bp.route("/<workspace_slug>/docs/new", methods=["GET", "POST"])
@require_auth
def new_document(workspace_slug: str):
    """Create a new workspace document; requires `editor` role or above."""
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(
        workspace_slug, user, required_role=WorkspaceMemberRole.editor
    )
    member = _current_member_role(workspace, user)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        markdown_body = request.form.get("markdown_body", "").strip()
        raw_tags = request.form.get("tags", "").strip()
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()] if raw_tags else []
        seo_description = request.form.get("seo_description", "").strip() or None
        custom_slug = request.form.get("slug", "").strip() or None

        if not title:
            flash("Title is required.", "error")
            return render_template(
                "workspace/new_document.html",
                workspace=workspace,
                member=member,
            )

        try:
            post = ws_svc.create_workspace_document(
                workspace=workspace,
                author=user,
                title=title,
                markdown_body=markdown_body,
                tag_names=tags or None,
                seo_description=seo_description,
                slug=custom_slug,
            )
            db.session.commit()
            flash(f"Document \u201c{post.title}\u201d created.", "success")
            return redirect(
                url_for(
                    "workspace.document",
                    workspace_slug=workspace_slug,
                    doc_slug=post.slug,
                )
            )
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            flash(str(exc), "error")

    return render_template(
        "workspace/new_document.html",
        workspace=workspace,
        member=member,
    )


# ── View / edit document ──────────────────────────────────────────────────────


@workspace_bp.get("/<workspace_slug>/docs/<doc_slug>")
@require_auth
def document(workspace_slug: str, doc_slug: str):
    """View a workspace document; any member may read."""
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(workspace_slug, user)
    member = _current_member_role(workspace, user)

    post = ws_svc.get_workspace_document(workspace, doc_slug)
    if post is None:
        abort(404)

    from backend.utils.markdown import get_rendered_html  # noqa: PLC0415

    rendered_html = get_rendered_html(post.id, post.markdown_body)
    revisions = ws_svc.list_workspace_document_revisions(post)
    release_notes = ws_svc.list_workspace_document_release_notes(post)

    # AI reviews — load latest 5 requests with their results.
    from backend.services import ai_review_service as ai_svc  # noqa: PLC0415

    ai_reviews = ai_svc.get_latest_reviews_for_post(post.id, limit=5)

    from backend.services.notification_service import is_subscribed  # noqa: PLC0415

    is_watching_post = is_subscribed(user, "post", post.id) if user else False

    return render_template(
        "workspace/document.html",
        workspace=workspace,
        post=post,
        rendered_html=rendered_html,
        revisions=revisions,
        release_notes=release_notes,
        member=member,
        ai_reviews=ai_reviews,
        ai_review_types=["clarity", "architecture", "security", "full"],
        is_watching_post=is_watching_post,
    )


@workspace_bp.route("/<workspace_slug>/docs/<doc_slug>/edit", methods=["GET", "POST"])
@require_auth
def edit_document(workspace_slug: str, doc_slug: str):
    """Edit a workspace document; requires `editor` role or above."""
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(
        workspace_slug, user, required_role=WorkspaceMemberRole.editor
    )
    member = _current_member_role(workspace, user)

    post = ws_svc.get_workspace_document(workspace, doc_slug)
    if post is None:
        abort(404)

    if request.method == "POST":
        title = request.form.get("title", "").strip() or None
        markdown_body = request.form.get("markdown_body")
        raw_tags = request.form.get("tags", "").strip()
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()] if raw_tags else []
        seo_description = request.form.get("seo_description", "").strip() or None

        try:
            ws_svc.update_workspace_document(
                post,
                title=title,
                markdown_body=markdown_body,
                seo_description=seo_description,
                tag_names=tags,
            )
            db.session.commit()
            flash("Document updated.", "success")
            return redirect(
                url_for(
                    "workspace.document",
                    workspace_slug=workspace_slug,
                    doc_slug=post.slug,
                )
            )
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            flash(str(exc), "error")

    return render_template(
        "workspace/edit_document.html",
        workspace=workspace,
        post=post,
        member=member,
    )


# ── Clone to public ───────────────────────────────────────────────────────────


@workspace_bp.post("/<workspace_slug>/docs/<doc_slug>/clone-to-public")
@require_auth
def clone_to_public(workspace_slug: str, doc_slug: str):
    """Clone a workspace document to a new public draft.

    Requires workspace editor or owner role (or platform admin).  Creates a
    brand-new ``Post(workspace_id=NULL, status=draft)`` — the original is
    untouched and the clone is never auto-published.

    Returns a redirect to the new public post's edit page on success, or
    aborts with 403 if the caller lacks permission.
    """
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(workspace_slug, user)

    post = ws_svc.get_workspace_document(workspace, doc_slug)
    if post is None:
        abort(404)

    if not PermissionService.can_clone_to_public(user, post):
        # Return 404 rather than 403 to avoid leaking existence to non-members;
        # editors already passed get_workspace_for_user, so this branch is only
        # reached by contributors/viewers who were already granted workspace view.
        abort(403)

    try:
        clone = ws_svc.clone_to_public(post, user)
        db.session.commit()
        flash(
            f"Document cloned to public draft: '{clone.title}'. "
            "Review and publish when ready.",
            "success",
        )
        return redirect(url_for("posts.edit_post", slug=clone.slug))
    except (ValueError, PermissionError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(
            url_for(
                "workspace.document",
                workspace_slug=workspace_slug,
                doc_slug=doc_slug,
            )
        )


# ── Compare versions ──────────────────────────────────────────────────────────


@workspace_bp.get("/<workspace_slug>/compare")
@require_auth
def compare(workspace_slug: str):
    """Compare two stored versions of a workspace document.

    Query params:
      slug   — document slug (required)
      from   — base version number  (required)
      to     — target version number (required)
    """
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(workspace_slug, user)
    member = _current_member_role(workspace, user)

    doc_slug = request.args.get("slug", "").strip()
    try:
        from_ver = int(request.args.get("from", 0))
        to_ver = int(request.args.get("to", 0))
    except (TypeError, ValueError):
        abort(400)

    if not doc_slug or from_ver <= 0 or to_ver <= 0:
        abort(400)

    post = ws_svc.get_workspace_document(workspace, doc_slug)
    if post is None:
        abort(404)

    versions = ws_svc.list_workspace_document_versions(post)
    ver_map = {v.version_number: v for v in versions}

    base_ver = ver_map.get(from_ver)
    target_ver = ver_map.get(to_ver)

    if base_ver is None or target_ver is None:
        abort(404)

    diff_text = compute_diff(
        base_ver.markdown_body,
        target_ver.markdown_body,
        fromfile=f"v{from_ver}",
        tofile=f"v{to_ver}",
    )
    diff_lines = parse_diff_lines(diff_text)
    additions = sum(1 for ln in diff_lines if ln["kind"] == "add")
    deletions = sum(1 for ln in diff_lines if ln["kind"] == "del")

    return render_template(
        "workspace/compare.html",
        workspace=workspace,
        post=post,
        versions=versions,
        from_ver=from_ver,
        to_ver=to_ver,
        diff_lines=diff_lines,
        additions=additions,
        deletions=deletions,
        member=member,
    )


# ── Changelog ─────────────────────────────────────────────────────────────────


@workspace_bp.get("/<workspace_slug>/changelog")
@require_auth
def changelog(workspace_slug: str):
    """Show the version changelog for a workspace document.

    Query params:
      slug — document slug (required)
    """
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(workspace_slug, user)
    member = _current_member_role(workspace, user)

    doc_slug = request.args.get("slug", "").strip()
    if not doc_slug:
        abort(400)

    post = ws_svc.get_workspace_document(workspace, doc_slug)
    if post is None:
        abort(404)

    release_notes = ws_svc.list_workspace_document_release_notes(post)
    versions = ws_svc.list_workspace_document_versions(post)

    return render_template(
        "workspace/changelog.html",
        workspace=workspace,
        post=post,
        release_notes=release_notes,
        versions=versions,
        member=member,
    )


# ── Members management ────────────────────────────────────────────────────────


@workspace_bp.get("/<workspace_slug>/members")
@require_auth
def members(workspace_slug: str):
    """List workspace members with their roles.  Owner/admin only."""
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(
        workspace_slug, user, required_role=WorkspaceMemberRole.owner
    )
    try:
        member_list = ws_svc.list_members(workspace, user)
    except PermissionError:
        abort(404)

    return render_template(
        "workspace/members.html",
        workspace=workspace,
        members=member_list,
        member=ws_svc.get_member(workspace, user),
        roles=WorkspaceMemberRole,
    )


@workspace_bp.post("/<workspace_slug>/members/<int:user_id>/role")
@require_auth
def change_role(workspace_slug: str, user_id: int):
    """Change a member's role.  Owner/admin only."""
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(
        workspace_slug, user, required_role=WorkspaceMemberRole.owner
    )

    new_role_str = request.form.get("role", "").strip().lower()
    try:
        new_role = WorkspaceMemberRole(new_role_str)
    except ValueError:
        flash(f"Invalid role: {new_role_str!r}.", "error")
        return redirect(url_for("workspace.members", workspace_slug=workspace_slug))

    try:
        ws_svc.change_member_role(workspace, user, user_id, new_role)
        db.session.commit()
        flash("Role updated.", "success")
    except (PermissionError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("workspace.members", workspace_slug=workspace_slug))


@workspace_bp.post("/<workspace_slug>/members/<int:user_id>/remove")
@require_auth
def remove_member(workspace_slug: str, user_id: int):
    """Remove a member from the workspace.  Owner/admin only."""
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(
        workspace_slug, user, required_role=WorkspaceMemberRole.owner
    )

    try:
        ws_svc.remove_member(workspace, user, user_id)
        db.session.commit()
        flash("Member removed.", "success")
    except (PermissionError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("workspace.members", workspace_slug=workspace_slug))


# ── Invitations management ────────────────────────────────────────────────────


@workspace_bp.get("/<workspace_slug>/invites")
@require_auth
def invites(workspace_slug: str):
    """List workspace invitations.  Owner/admin only."""
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(
        workspace_slug, user, required_role=WorkspaceMemberRole.owner
    )

    try:
        invite_list = inv_svc.list_invites(workspace, user)
    except PermissionError:
        abort(404)

    return render_template(
        "workspace/invites.html",
        workspace=workspace,
        invites=invite_list,
        member=ws_svc.get_member(workspace, user),
    )


@workspace_bp.post("/<workspace_slug>/invites/new")
@require_auth
def create_invite(workspace_slug: str):
    """Create a new workspace invitation.  Owner/admin only.

    On success, flashes the raw invite token and redirects to the
    ``invite_created`` confirmation page (so the token appears exactly once).
    """
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(
        workspace_slug, user, required_role=WorkspaceMemberRole.owner
    )

    role = request.form.get("role", "viewer").strip().lower()
    try:
        expires_in_days = int(request.form.get("expires_in_days", "7"))
    except (TypeError, ValueError):
        expires_in_days = 7
    try:
        max_uses = int(request.form.get("max_uses", "1"))
    except (TypeError, ValueError):
        max_uses = 1

    try:
        invite, raw_token = inv_svc.create_invite(
            workspace,
            user,
            role,
            expires_in_days=expires_in_days,
            max_uses=max_uses,
        )
        db.session.commit()
        # Flash the raw token ONCE — it is never re-derivable from the DB.
        flash(raw_token, "invite_token")
        return redirect(
            url_for(
                "workspace.invite_created",
                workspace_slug=workspace_slug,
                invite_id=invite.id,
            )
        )
    except (PermissionError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("workspace.invites", workspace_slug=workspace_slug))


@workspace_bp.get("/<workspace_slug>/invites/<int:invite_id>/created")
@require_auth
def invite_created(workspace_slug: str, invite_id: int):
    """Confirmation page after creating an invite — shows the link exactly once.

    The raw token is consumed from the flash message queue and rendered here.
    After this page is loaded (or dismissed), the raw token is gone.
    """
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(
        workspace_slug, user, required_role=WorkspaceMemberRole.owner
    )

    from backend.models.workspace import WorkspaceInvitation  # noqa: PLC0415

    invite = db.session.get(WorkspaceInvitation, invite_id)
    if invite is None or invite.workspace_id != workspace.id:
        abort(404)

    # Consume the raw token from the flash queue (stored by create_invite POST).
    # get_flashed_messages() removes items from the session on first call.
    from flask import get_flashed_messages  # noqa: PLC0415

    raw_token: str | None = None
    for category, msg in get_flashed_messages(with_categories=True):
        if category == "invite_token":
            raw_token = msg
            break

    return render_template(
        "workspace/invite_created.html",
        workspace=workspace,
        invite=invite,
        raw_token=raw_token,
        member=ws_svc.get_member(workspace, user),
    )


@workspace_bp.post("/<workspace_slug>/invites/<int:invite_id>/revoke")
@require_auth
def revoke_invite(workspace_slug: str, invite_id: int):
    """Revoke an invitation.  Owner/admin only."""
    user = get_current_user()
    ws_svc.get_workspace_for_user(
        workspace_slug, user, required_role=WorkspaceMemberRole.owner
    )

    try:
        inv_svc.revoke_invite(invite_id, user)
        db.session.commit()
        flash("Invitation revoked.", "success")
    except (PermissionError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("workspace.invites", workspace_slug=workspace_slug))


# ── Playbook routes ───────────────────────────────────────────────────────────


@workspace_bp.get("/<workspace_slug>/playbooks")
@require_auth
def playbooks_list(workspace_slug: str):
    """List all playbooks in the workspace; any member may view."""
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(workspace_slug, user)
    member = _current_member_role(workspace, user)

    playbooks = pb_svc.list_workspace_playbooks(workspace)

    return render_template(
        "workspace/playbooks_list.html",
        workspace=workspace,
        playbooks=playbooks,
        member=member,
    )


@workspace_bp.route("/<workspace_slug>/playbooks/new", methods=["GET", "POST"])
@require_auth
def new_playbook(workspace_slug: str):
    """Create a new playbook; requires ``editor`` role or above."""
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(
        workspace_slug, user, required_role=WorkspaceMemberRole.editor
    )
    member = _current_member_role(workspace, user)

    templates = pb_svc.list_templates(public_only=True)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        seo_description = request.form.get("seo_description", "").strip() or None
        custom_slug = request.form.get("slug", "").strip() or None
        raw_template_version_id = request.form.get("template_version_id", "").strip()
        template_version_id: int | None = None
        if raw_template_version_id.isdigit():
            template_version_id = int(raw_template_version_id)

        if not title:
            flash("Title is required.", "error")
            return render_template(
                "workspace/playbook_new.html",
                workspace=workspace,
                member=member,
                templates=templates,
            )

        try:
            post = pb_svc.create_workspace_playbook(
                workspace=workspace,
                creator=user,
                title=title,
                seo_description=seo_description,
                slug=custom_slug,
                template_version_id=template_version_id,
            )
            db.session.commit()
            flash(f"Playbook \u201c{post.title}\u201d created.", "success")
            return redirect(
                url_for(
                    "workspace.playbook_detail",
                    workspace_slug=workspace_slug,
                    playbook_slug=post.slug,
                )
            )
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            flash(str(exc), "error")

    return render_template(
        "workspace/playbook_new.html",
        workspace=workspace,
        member=member,
        templates=templates,
    )


@workspace_bp.get("/<workspace_slug>/playbooks/<playbook_slug>")
@require_auth
def playbook_detail(workspace_slug: str, playbook_slug: str):
    """View a workspace playbook; any member may read."""
    user = get_current_user()
    workspace = ws_svc.get_workspace_for_user(workspace_slug, user)
    member = _current_member_role(workspace, user)

    post = pb_svc.get_workspace_playbook(workspace, playbook_slug)
    if post is None:
        abort(404)

    from backend.utils.markdown import get_rendered_html  # noqa: PLC0415

    rendered_html = get_rendered_html(post.id, post.markdown_body)
    revisions = ws_svc.list_workspace_document_revisions(post)
    release_notes = ws_svc.list_workspace_document_release_notes(post)

    return render_template(
        "workspace/playbook_detail.html",
        workspace=workspace,
        post=post,
        rendered_html=rendered_html,
        revisions=revisions,
        release_notes=release_notes,
        member=member,
    )


# ---------------------------------------------------------------------------
# Workspace Knowledge Health Dashboard
# ---------------------------------------------------------------------------


@workspace_bp.get("/<workspace_slug>/health")
@require_auth
def workspace_health(workspace_slug: str):
    """Render the knowledge health dashboard for a workspace.

    Access: workspace members only (get_workspace_for_user aborts 404
    for non-members and require_auth redirects unauthenticated users).
    Cache-Control: set to private, no-store by the blueprint after_request hook.
    """
    user = get_current_user()
    ws = ws_svc.get_workspace_for_user(workspace_slug, user)
    member = _current_member_role(ws, user)

    summary = WorkspaceHealthService.get_health_summary(ws)
    coverage = WorkspaceHealthService.get_ontology_coverage(ws)
    unbenchmarked = WorkspaceHealthService.get_unbenchmarked_prompts(ws)
    stale = WorkspaceHealthService.get_stale_content(ws)
    unimproved = WorkspaceHealthService.get_unimproved_content(ws)
    gaps = WorkspaceHealthService.get_contributor_gaps(ws)
    actions = WorkspaceHealthService.get_recommended_actions(ws)

    return render_template(
        "workspace/health.html",
        workspace=ws,
        member=member,
        summary=summary,
        coverage=coverage,
        unbenchmarked=unbenchmarked,
        stale=stale,
        unimproved=unimproved,
        gaps=gaps,
        actions=actions,
    )
