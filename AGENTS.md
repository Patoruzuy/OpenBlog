# AGENTS.md

This document defines architectural principles, review standards, and long-term vision for OpenBlog.

It is authoritative for contributors, AI agents, and reviewers.

---

# 1. Project Vision

OpenBlog is a **versioned, benchmarkable, graph-connected knowledge platform**.

It is not:
- A generic CMS
- A social media clone
- A simple blogging engine

It is:
- A structured knowledge system
- A prompt engineering laboratory
- A benchmark-driven improvement platform
- A workspace-scoped documentation engine

Core properties:
- Everything is versioned.
- Everything respects scope boundaries.
- Improvements are measurable.
- Deterministic behavior > magic.

---

# 2. Core Architectural Invariants

These rules must never be violated:

## 2.1 Scope Isolation

Public content:
- workspace_id IS NULL

Workspace content:
- workspace_id = ws.id

Hard rules:
- Public → Workspace access: FORBIDDEN
- Workspace A → Workspace B access: FORBIDDEN
- Service layer enforces isolation (never templates or routes)

All new services must:
- Accept workspace parameter explicitly
- Filter in SQL, not Python
- Fail closed (404 over 403 when appropriate)

---

## 2.2 Service Layer Authority

Business rules belong in services.

Routes:
- Must be thin.
- Must not contain business logic.
- Must not perform cross-scope filtering.

Templates:
- Never enforce security.
- Never decide visibility rules.

---

## 2.3 Determinism

All ranking and scoring systems must:

- Be deterministic.
- Define explicit tie-break rules.
- Avoid random ordering.
- Avoid implicit database ordering.

Tie-break example:
- version DESC
- updated_at DESC
- id DESC

---

## 2.4 Async Discipline

Celery tasks must:

- Re-fetch DB state before final write.
- Respect cancellation flags.
- Truncate error messages (max 400 chars).
- Never trust stale ORM state.

Never:
- Perform cross-scope access inside tasks.
- Assume visibility without re-checking.

---

## 2.5 No Leakage Rule

The following must NEVER leak:

- Workspace-only content in public feeds
- Workspace-only ontology mappings in public browse
- Benchmark results across workspaces
- A/B experiment data across workspaces
- Prompt recommendations across workspaces

Feed + sitemap services must only include:
- workspace_id IS NULL
- published content

---

# 3. Code Quality Standards

## 3.1 Complexity

- Avoid functions > 50 logical lines.
- Extract scoring logic into helpers.
- Avoid nested conditionals deeper than 3 levels.
- Prefer explicit over clever.

Run:
- ruff
- mypy (if enabled later)
- radon for complexity (optional)

---

## 3.2 Modularity

Every feature must include:

- Schema (migration)
- Model
- Service
- Routes
- Templates
- Tests

Services must be:
- Single responsibility
- Scope-aware
- Bounded query count

---

## 3.3 Tests

All features require:

- Public scope tests
- Workspace scope tests
- Isolation tests
- Permission tests
- Determinism tests

Never merge without:

- All tests passing
- No new N+1 queries
- Query count bounded where applicable

---

# 4. Security Requirements

## 4.1 Input Validation

- Validate JSON inputs strictly.
- Reject unknown keys in structured inputs.
- Enforce max lengths on text fields.
- Never render unescaped template data.

Prompt rendering:
- Must NOT execute Jinja.
- Must use controlled substitution.

---

## 4.2 Authorization

Rules must be enforced in services.

Never rely on:
- Frontend hiding buttons
- Template conditionals
- Route-only decorators

---

## 4.3 Error Handling

- Do not expose stack traces.
- Truncate stored error messages.
- Log detailed errors server-side only.

---

# 5. Performance Discipline

All list endpoints must:

- Use explicit LIMIT
- Avoid N+1
- Use indexed fields
- Preload related entities

New services must justify query count.

---

# 6. Benchmarking Philosophy

Prompts are measurable artifacts.

Signals include:
- Ratings
- Benchmark scores
- A/B win rates
- Execution volume
- Recency

Scoring models must:
- Normalize within family
- Be reproducible
- Be documented

---

# 7. Knowledge Graph Principles

Links must:

- Respect scope
- Avoid duplication
- Support reverse traversal
- Be grouped by type

Suggested links must:
- Be deterministic
- Be bounded
- Exclude already linked nodes

---

# 8. Ontology Philosophy

Ontology is:

- Structured
- Hierarchical
- Curated
- Not a tag replacement

Public ontology:
- Visible globally

Workspace overlay:
- Additional mappings
- Never visible publicly

---

# 9. Future Direction

Planned evolutions:

- Benchmark slices by ontology
- Cross-family benchmarking
- Prompt evolution analytics
- AI explanation layer
- Workspace knowledge maturity scoring

OpenBlog aims to become:

> A versioned, benchmarked, optimized knowledge infrastructure system.

---

# 10. Review Checklist

Before merging any feature:

- [ ] Scope isolation enforced in service
- [ ] Deterministic ordering
- [ ] No N+1 queries
- [ ] Tests cover public + workspace
- [ ] Async tasks re-fetch DB state
- [ ] Feeds/sitemap untouched unless intentional
- [ ] Cache headers correct for workspace routes
- [ ] Error messages truncated
- [ ] No leakage across workspaces

Failure to satisfy any of these blocks merge.