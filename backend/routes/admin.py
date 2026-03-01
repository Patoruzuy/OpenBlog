"""Admin control center blueprint.

All routes require the ``admin`` or ``editor`` role via
``@require_admin_access``.  Individual routes further restrict with
``@require_capability`` where needed.

Route map
---------
GET  /admin                      → redirect to /admin/dashboard
GET  /admin/dashboard            → dashboard snapshot
GET  /admin/posts                → post list (filter/search/paginate)
GET  /admin/posts/<id>           → post detail + revision list
POST /admin/posts/<id>/status    → publish/unpublish/archive
POST /admin/posts/<id>/feature   → toggle featured
POST /admin/posts/<id>/delete    → soft/hard delete (admin only)
GET  /admin/revisions            → revision queue
POST /admin/revisions/<id>/accept→ accept revision
POST /admin/revisions/<id>/reject→ reject revision
GET  /admin/comments             → comment moderation list
POST /admin/comments/<id>/hide   → hide a comment
POST /admin/comments/<id>/unflag → clear moderation flag
GET  /admin/topics               → tag/topic list
POST /admin/topics/create        → create a tag
POST /admin/topics/<slug>/edit   → edit a tag
POST /admin/topics/<slug>/delete → delete a tag
GET  /admin/users                → user list
GET  /admin/users/<id>           → user detail
POST /admin/users/<id>/suspend   → suspend user
POST /admin/users/<id>/reactivate→ reactivate user
POST /admin/users/<id>/verify    → mark email verified
POST /admin/users/<id>/role      → change role (admin only)
POST /admin/users/<id>/shadowban → toggle shadow-ban (admin only)
GET  /admin/analytics            → analytics overview
GET  /admin/settings             → site settings form
POST /admin/settings             → save site settings
GET  /admin/audit                → audit log
GET  /admin/system               → system health
"""

from __future__ import annotations

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import select

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.revision import Revision
from backend.models.user import User, UserRole
from backend.services.admin_analytics_service import AdminAnalyticsService
from backend.services.admin_dashboard_service import AdminDashboardService
from backend.services.admin_post_service import AdminPostService
from backend.services.admin_settings_service import SiteSettingsService
from backend.services.admin_tag_service import AdminTagError, AdminTagService
from backend.services.admin_user_service import AdminUserError, AdminUserService
from backend.services.audit_log_service import AuditLogService
from backend.services.moderation_service import ModerationError, ModerationService
from backend.services.report_service import ReportError, ReportService
from backend.services.system_health_service import SystemHealthService
from backend.utils.admin_auth import (
    can,
    current_admin_user,
    require_admin,
    require_admin_access,
    require_capability,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# ── Context helper ────────────────────────────────────────────────────────────


@admin_bp.context_processor
def _admin_context() -> dict:
    """Inject ``can`` helper, pending revision count, and open report count."""
    pending = 0
    open_reports = 0
    try:
        from sqlalchemy import func  # noqa: PLC0415

        from backend.models.revision import RevisionStatus  # noqa: PLC0415

        pending = (
            db.session.scalar(
                select(func.count(Revision.id)).where(
                    Revision.status == RevisionStatus.pending
                )
            )
            or 0
        )
    except Exception:
        pass
    try:
        open_reports = ReportService.open_count()
    except Exception:
        pass
    return {
        "can": can,
        "admin_pending_revisions": pending,
        "admin_open_reports": open_reports,
    }


# ── Index redirect ────────────────────────────────────────────────────────────


@admin_bp.route("/")
@require_admin_access
def index():
    return redirect(url_for("admin.dashboard"))


# ── Dashboard ─────────────────────────────────────────────────────────────────


@admin_bp.route("/dashboard")
@require_admin_access
def dashboard():
    snap = AdminDashboardService.get_snapshot()
    return render_template("admin/dashboard.html", **snap)


# ── Posts ─────────────────────────────────────────────────────────────────────


@admin_bp.route("/posts")
@require_admin_access
@require_capability("manage_content")
def posts():
    status = request.args.get("status")
    sort = request.args.get("sort", "updated_desc")
    q = request.args.get("q", "").strip() or None
    page = max(1, int(request.args.get("page", 1)))
    featured = None
    if request.args.get("featured") == "1":
        featured = True

    items, total = AdminPostService.list_posts(
        status=status, q=q, featured=featured, sort=sort, page=page
    )
    return render_template(
        "admin/posts/list.html",
        posts=items,
        total=total,
        page=page,
        page_size=30,
        statuses=[s.value for s in PostStatus],
    )


@admin_bp.route("/posts/<int:post_id>")
@require_admin_access
@require_capability("manage_content")
def post_detail(post_id: int):
    post = AdminPostService.get_with_revisions(post_id)
    if post is None:
        return render_template("admin/404.html"), 404
    return render_template("admin/posts/detail.html", post=post)


@admin_bp.route("/posts/<int:post_id>/status", methods=["POST"])
@require_admin_access
@require_capability("manage_content")
def post_set_status(post_id: int):
    actor = current_admin_user()
    post = db.session.get(Post, post_id)
    if post is None:
        flash("Post not found.", "error")
        return redirect(url_for("admin.posts"))
    new_status = request.form.get("status", "")
    try:
        status_val = PostStatus(new_status)
    except ValueError:
        flash("Invalid status.", "error")
        return redirect(url_for("admin.post_detail", post_id=post_id))

    before = {"status": post.status.value}
    AdminPostService.set_status(post, status_val, actor)
    AuditLogService.log(
        actor=actor,
        action=f"post.{new_status}",
        target_type="post",
        target_id=post.id,
        target_repr=post.title,
        before=before,
        after={"status": new_status},
    )
    db.session.commit()
    flash(f"Post status set to {new_status}.", "success")
    return redirect(url_for("admin.post_detail", post_id=post_id))


@admin_bp.route("/posts/<int:post_id>/feature", methods=["POST"])
@require_admin_access
@require_capability("manage_content")
def post_toggle_feature(post_id: int):
    actor = current_admin_user()
    post = db.session.get(Post, post_id)
    if post is None:
        flash("Post not found.", "error")
        return redirect(url_for("admin.posts"))
    AdminPostService.toggle_featured(post)
    AuditLogService.log(
        actor=actor,
        action="post.feature_toggled",
        target_type="post",
        target_id=post.id,
        target_repr=post.title,
        after={"is_featured": post.is_featured},
    )
    db.session.commit()
    flash("Featured flag toggled.", "success")
    return redirect(url_for("admin.post_detail", post_id=post_id))


@admin_bp.route("/posts/<int:post_id>/delete", methods=["POST"])
@require_admin
def post_delete(post_id: int):
    actor = current_admin_user()
    post = db.session.get(Post, post_id)
    if post is None:
        flash("Post not found.", "error")
        return redirect(url_for("admin.posts"))
    title = post.title
    AuditLogService.log(
        actor=actor,
        action="post.deleted",
        target_type="post",
        target_id=post_id,
        target_repr=title,
        before={"status": post.status.value, "title": title},
    )
    AdminPostService.delete_post(post)  # commits internally
    db.session.commit()
    flash(f'Post "{title}" deleted.', "success")
    return redirect(url_for("admin.posts"))


# ── Revisions ─────────────────────────────────────────────────────────────────


@admin_bp.route("/revisions")
@require_admin_access
@require_capability("moderate")
def revisions():
    status = request.args.get("status", "pending")
    q = request.args.get("q", "").strip() or None
    page = max(1, int(request.args.get("page", 1)))
    post_id = request.args.get("post_id", type=int)
    author_id = request.args.get("author_id", type=int)

    items, total = ModerationService.list_revisions(
        status=status, q=q, page=page, post_id=post_id, author_id=author_id
    )
    return render_template(
        "admin/revisions/list.html",
        revisions=items,
        total=total,
        page=page,
        page_size=30,
        current_status=status,
    )


@admin_bp.route("/revisions/<int:revision_id>")
@require_admin_access
@require_capability("moderate")
def revision_detail(revision_id: int):
    rev = db.session.get(Revision, revision_id)
    if rev is None:
        return render_template("admin/404.html"), 404
    db.session.refresh(rev)
    return render_template("admin/revisions/detail.html", revision=rev)


@admin_bp.route("/revisions/<int:revision_id>/accept", methods=["POST"])
@require_admin_access
@require_capability("moderate")
def revision_accept(revision_id: int):
    actor = current_admin_user()
    note = request.form.get("note", "").strip() or None
    try:
        rev = ModerationService.accept_revision(revision_id, actor, note=note)
        AuditLogService.log(
            actor=actor,
            action="revision.accepted",
            target_type="revision",
            target_id=revision_id,
            target_repr=f"Revision #{revision_id} on post {rev.post_id}",
            note=note,
        )
        db.session.commit()
        flash("Revision accepted.", "success")
    except ModerationError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.revisions"))


@admin_bp.route("/revisions/<int:revision_id>/reject", methods=["POST"])
@require_admin_access
@require_capability("moderate")
def revision_reject(revision_id: int):
    actor = current_admin_user()
    note = request.form.get("note", "").strip() or None
    try:
        rev = ModerationService.reject_revision(revision_id, actor, note=note)
        AuditLogService.log(
            actor=actor,
            action="revision.rejected",
            target_type="revision",
            target_id=revision_id,
            target_repr=f"Revision #{revision_id} on post {rev.post_id}",
            note=note,
        )
        db.session.commit()
        flash("Revision rejected.", "success")
    except ModerationError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.revisions"))


# ── Comments ──────────────────────────────────────────────────────────────────


@admin_bp.route("/comments")
@require_admin_access
@require_capability("moderate")
def comments():
    flagged_only = request.args.get("flagged") == "1"
    q = request.args.get("q", "").strip() or None
    page = max(1, int(request.args.get("page", 1)))

    items, total = ModerationService.list_comments(
        flagged_only=flagged_only, q=q, page=page
    )
    return render_template(
        "admin/comments/list.html",
        comments=items,
        total=total,
        page=page,
        page_size=30,
        flagged_only=flagged_only,
    )


@admin_bp.route("/comments/<int:comment_id>/hide", methods=["POST"])
@require_admin_access
@require_capability("moderate")
def comment_hide(comment_id: int):
    actor = current_admin_user()
    try:
        ModerationService.hide_comment(comment_id)
        AuditLogService.log(
            actor=actor,
            action="comment.hidden",
            target_type="comment",
            target_id=comment_id,
        )
        db.session.commit()
        flash("Comment hidden.", "success")
    except ModerationError as exc:
        flash(str(exc), "error")
    return redirect(request.referrer or url_for("admin.comments"))


@admin_bp.route("/comments/<int:comment_id>/unflag", methods=["POST"])
@require_admin_access
@require_capability("moderate")
def comment_unflag(comment_id: int):
    actor = current_admin_user()
    try:
        ModerationService.unflag_comment(comment_id)
        AuditLogService.log(
            actor=actor,
            action="comment.unflagged",
            target_type="comment",
            target_id=comment_id,
        )
        db.session.commit()
        flash("Comment flag cleared.", "success")
    except ModerationError as exc:
        flash(str(exc), "error")
    return redirect(request.referrer or url_for("admin.comments"))


# ── Topics / Tags ─────────────────────────────────────────────────────────────


@admin_bp.route("/topics")
@require_admin_access
@require_capability("manage_content")
def topics():
    q = request.args.get("q", "").strip() or None
    page = max(1, int(request.args.get("page", 1)))
    items, total = AdminTagService.list_tags(q=q, page=page)
    return render_template(
        "admin/topics/list.html",
        topics=items,
        total=total,
        page=page,
        page_size=50,
        q=q or "",
    )


@admin_bp.route("/topics/create", methods=["POST"])
@require_admin_access
@require_capability("manage_content")
def topic_create():
    actor = current_admin_user()
    name = request.form.get("name", "").strip()
    desc = request.form.get("description", "").strip() or None
    color = request.form.get("color", "").strip() or None
    try:
        tag = AdminTagService.create(name=name, description=desc, color=color)
        AuditLogService.log(
            actor=actor,
            action="tag.created",
            target_type="tag",
            target_id=tag.id,
            target_repr=tag.name,
        )
        db.session.commit()
        flash(f"Tag '{tag.name}' created.", "success")
    except AdminTagError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.topics"))


@admin_bp.route("/topics/<slug>/edit", methods=["POST"])
@require_admin_access
@require_capability("manage_content")
def topic_edit(slug: str):
    actor = current_admin_user()
    tag = AdminTagService.get_by_slug(slug)
    if tag is None:
        flash("Tag not found.", "error")
        return redirect(url_for("admin.topics"))
    name = request.form.get("name", "").strip() or None
    desc = request.form.get("description", "").strip()
    color = request.form.get("color", "").strip()
    try:
        AdminTagService.update(tag, name=name, description=desc, color=color)
        AuditLogService.log(
            actor=actor,
            action="tag.updated",
            target_type="tag",
            target_id=tag.id,
            target_repr=tag.name,
        )
        db.session.commit()
        flash(f"Tag '{tag.name}' updated.", "success")
    except AdminTagError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.topics"))


@admin_bp.route("/topics/<slug>/delete", methods=["POST"])
@require_admin
def topic_delete(slug: str):
    actor = current_admin_user()
    tag = AdminTagService.get_by_slug(slug)
    if tag is None:
        flash("Tag not found.", "error")
        return redirect(url_for("admin.topics"))
    name, tag_id = tag.name, tag.id
    AuditLogService.log(
        actor=actor,
        action="tag.deleted",
        target_type="tag",
        target_id=tag_id,
        target_repr=name,
    )
    AdminTagService.delete(tag)  # commits internally
    db.session.commit()
    flash(f"Tag '{name}' deleted.", "success")
    return redirect(url_for("admin.topics"))


# ── Users ─────────────────────────────────────────────────────────────────────


@admin_bp.route("/users")
@require_admin_access
@require_capability("manage_users")
def users():
    q = request.args.get("q", "").strip() or None
    role = request.args.get("role") or None
    verified = None
    if request.args.get("verified") == "1":
        verified = True
    elif request.args.get("verified") == "0":
        verified = False
    active = None
    if request.args.get("active") == "1":
        active = True
    elif request.args.get("active") == "0":
        active = False
    sort = request.args.get("sort", "created_desc")
    page = max(1, int(request.args.get("page", 1)))

    items, total = AdminUserService.list_users(
        q=q, role=role, verified=verified, active=active, sort=sort, page=page
    )
    return render_template(
        "admin/users/list.html",
        users=items,
        total=total,
        page=page,
        page_size=40,
        roles=[r.value for r in UserRole],
    )


@admin_bp.route("/users/<int:user_id>")
@require_admin_access
@require_capability("manage_users")
def user_detail(user_id: int):
    detail = AdminUserService.get_user_detail(user_id)
    if detail is None:
        return render_template("admin/404.html"), 404
    return render_template("admin/users/detail.html", **detail)


@admin_bp.route("/users/<int:user_id>/suspend", methods=["POST"])
@require_admin_access
@require_capability("manage_users")
def user_suspend(user_id: int):
    actor = current_admin_user()
    user = db.session.get(User, user_id)
    if user is None:
        flash("User not found.", "error")
        return redirect(url_for("admin.users"))
    try:
        AdminUserService.set_active(user, False, actor)
        AuditLogService.log(
            actor=actor,
            action="user.suspended",
            target_type="user",
            target_id=user_id,
            target_repr=user.username,
        )
        db.session.commit()
        flash(f"User '{user.username}' suspended.", "success")
    except AdminUserError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@admin_bp.route("/users/<int:user_id>/reactivate", methods=["POST"])
@require_admin_access
@require_capability("manage_users")
def user_reactivate(user_id: int):
    actor = current_admin_user()
    user = db.session.get(User, user_id)
    if user is None:
        flash("User not found.", "error")
        return redirect(url_for("admin.users"))
    try:
        AdminUserService.set_active(user, True, actor)
        AuditLogService.log(
            actor=actor,
            action="user.reactivated",
            target_type="user",
            target_id=user_id,
            target_repr=user.username,
        )
        db.session.commit()
        flash(f"User '{user.username}' reactivated.", "success")
    except AdminUserError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@admin_bp.route("/users/<int:user_id>/verify", methods=["POST"])
@require_admin_access
@require_capability("manage_users")
def user_verify_email(user_id: int):
    actor = current_admin_user()
    user = db.session.get(User, user_id)
    if user is None:
        flash("User not found.", "error")
        return redirect(url_for("admin.users"))
    AdminUserService.verify_email(user)
    AuditLogService.log(
        actor=actor,
        action="user.email_verified",
        target_type="user",
        target_id=user_id,
        target_repr=user.username,
    )
    db.session.commit()
    flash(f"Email verified for '{user.username}'.", "success")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@admin_bp.route("/users/<int:user_id>/role", methods=["POST"])
@require_admin
def user_set_role(user_id: int):
    actor = current_admin_user()
    user = db.session.get(User, user_id)
    if user is None:
        flash("User not found.", "error")
        return redirect(url_for("admin.users"))
    new_role_str = request.form.get("role", "")
    try:
        new_role = UserRole(new_role_str)
    except ValueError:
        flash("Invalid role.", "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))
    try:
        old_role = user.role.value
        AdminUserService.set_role(user, new_role, actor)
        AuditLogService.log(
            actor=actor,
            action="user.role_changed",
            target_type="user",
            target_id=user_id,
            target_repr=user.username,
            before={"role": old_role},
            after={"role": new_role_str},
        )
        db.session.commit()
        flash(f"Role for '{user.username}' changed to {new_role_str}.", "success")
    except AdminUserError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@admin_bp.route("/users/<int:user_id>/shadowban", methods=["POST"])
@require_admin
def user_shadowban(user_id: int):
    actor = current_admin_user()
    user = db.session.get(User, user_id)
    if user is None:
        flash("User not found.", "error")
        return redirect(url_for("admin.users"))
    try:
        new_state = not user.is_shadow_banned
        AdminUserService.set_shadow_ban(user, new_state, actor)
        action = "user.shadow_banned" if new_state else "user.shadow_ban_lifted"
        AuditLogService.log(
            actor=actor,
            action=action,
            target_type="user",
            target_id=user_id,
            target_repr=user.username,
        )
        db.session.commit()
        label = "shadow-banned" if new_state else "shadow-ban lifted"
        flash(f"User '{user.username}' {label}.", "success")
    except AdminUserError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.user_detail", user_id=user_id))


# ── Analytics ─────────────────────────────────────────────────────────────────


@admin_bp.route("/analytics")
@require_admin_access
@require_capability("view_analytics")
def analytics():
    days = min(90, max(7, int(request.args.get("days", 30))))
    data = AdminAnalyticsService.overview(days=days)
    return render_template("admin/analytics.html", **data)


# ── Settings ──────────────────────────────────────────────────────────────────


@admin_bp.route("/settings", methods=["GET", "POST"])
@require_admin
def settings():
    actor = current_admin_user()
    if request.method == "POST":
        changed = []
        for key, _default, _group, _desc in _settings_catalogue():
            raw = request.form.get(key)
            if raw is not None:
                # Booleans come from checkboxes — blank = False
                current = SiteSettingsService.get(key)
                if isinstance(current, bool) or isinstance(_default, bool):
                    value = raw.lower() in ("1", "true", "on")
                else:
                    value = raw.strip()
                SiteSettingsService.set(key, value, actor)
                changed.append(key)
        if changed:
            AuditLogService.log(
                actor=actor,
                action="settings.updated",
                note=f"Updated: {', '.join(changed)}",
            )
            db.session.commit()
            flash("Settings saved.", "success")
        return redirect(url_for("admin.settings"))

    rows = SiteSettingsService.get_all_rows()
    current = SiteSettingsService.get_all()
    return render_template("admin/settings.html", rows=rows, current=current)


def _settings_catalogue():
    from backend.services.admin_settings_service import _DEFAULTS  # noqa: PLC0415

    return _DEFAULTS


# ── Audit log ─────────────────────────────────────────────────────────────────


# ── Reports ──────────────────────────────────────────────────────────────────


@admin_bp.route("/reports")
@require_admin_access
@require_capability("moderate")
def reports():
    """List and filter user-submitted moderation reports."""
    from backend.models.comment import Comment  # noqa: PLC0415

    status = request.args.get("status", "open")
    if status not in ("open", "resolved", "dismissed", "all"):
        status = "open"
    target_type = request.args.get("target_type", "").strip() or None
    page = max(1, request.args.get("page", 1, type=int))

    report_list, total = ReportService.list_reports(
        status=status,
        target_type=target_type,
        page=page,
    )

    # Batch-fetch targets to avoid N+1 on the template.
    post_ids = {r.target_id for r in report_list if r.target_type == "post"}
    comment_ids = {r.target_id for r in report_list if r.target_type == "comment"}
    posts_by_id: dict[int, Post] = {}
    comments_by_id: dict[int, object] = {}
    if post_ids:
        posts_by_id = {
            p.id: p
            for p in db.session.scalars(select(Post).where(Post.id.in_(post_ids))).all()
        }
    if comment_ids:
        comments_by_id = {
            c.id: c
            for c in db.session.scalars(
                select(Comment).where(Comment.id.in_(comment_ids))
            ).all()
        }

    return render_template(
        "admin/reports/list.html",
        reports=report_list,
        total=total,
        page=page,
        per_page=30,
        status=status,
        target_type=target_type,
        posts_by_id=posts_by_id,
        comments_by_id=comments_by_id,
    )


@admin_bp.route("/reports/<int:report_id>/resolve", methods=["POST"])
@require_admin_access
@require_capability("moderate")
def report_resolve(report_id: int):
    """Mark report as resolved and append an audit log entry."""
    actor = current_admin_user()
    try:
        report = ReportService.resolve(report_id, actor.id, dismiss=False)
    except ReportError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.reports"))

    AuditLogService.log(
        actor=actor,
        action="report.resolve",
        target_type=report.target_type,
        target_id=report.target_id,
        target_repr=f"report #{report.id} ({report.reason})",
        note="resolved",
    )
    db.session.commit()
    flash("Report marked as resolved.", "success")
    return redirect(request.referrer or url_for("admin.reports"))


@admin_bp.route("/reports/<int:report_id>/dismiss", methods=["POST"])
@require_admin_access
@require_capability("moderate")
def report_dismiss(report_id: int):
    """Mark report as dismissed (no action taken) and append an audit log entry."""
    actor = current_admin_user()
    try:
        report = ReportService.resolve(report_id, actor.id, dismiss=True)
    except ReportError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.reports"))

    AuditLogService.log(
        actor=actor,
        action="report.dismiss",
        target_type=report.target_type,
        target_id=report.target_id,
        target_repr=f"report #{report.id} ({report.reason})",
        note="dismissed",
    )
    db.session.commit()
    flash("Report dismissed.", "success")
    return redirect(request.referrer or url_for("admin.reports"))


# ── Audit ─────────────────────────────────────────────────────────────────────


@admin_bp.route("/audit")
@require_admin_access
@require_capability("view_audit")
def audit():
    actor_id = request.args.get("actor_id", type=int)
    action_prefix = request.args.get("action", "").strip() or None
    target_type = request.args.get("target_type", "").strip() or None
    page = max(1, int(request.args.get("page", 1)))

    entries, total = AuditLogService.list_entries(
        actor_id=actor_id,
        action_prefix=action_prefix,
        target_type=target_type,
        page=page,
    )
    return render_template(
        "admin/audit.html",
        entries=entries,
        total=total,
        page=page,
        page_size=50,
    )


# ── System ────────────────────────────────────────────────────────────────────


@admin_bp.route("/system")
@require_admin
def system():
    status = SystemHealthService.get_status()
    return render_template("admin/system.html", **status)
