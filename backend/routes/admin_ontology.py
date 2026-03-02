"""Admin Ontology blueprint.

Route map
---------
GET  /admin/ontology              → list all nodes (tree)
GET  /admin/ontology/new          → create form
POST /admin/ontology/new          → create node
GET  /admin/ontology/<node_id>    → edit form
POST /admin/ontology/<node_id>    → save edits

All routes require the admin or editor role via ``@require_admin_access``.
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
from backend.models.ontology import OntologyNode
from backend.models.revision import Revision, RevisionStatus
from backend.services.ontology_service import (
    OntologyError,
    create_node,
    list_tree,
    update_node,
)
from backend.services.report_service import ReportService
from backend.utils.admin_auth import (
    can,
    current_admin_user,
    require_admin_access,
)

admin_ontology_bp = Blueprint("admin_ontology", __name__, url_prefix="/admin")


# ── Context processor ─────────────────────────────────────────────────────────


@admin_ontology_bp.context_processor
def _admin_context() -> dict:
    pending = 0
    open_reports = 0
    try:
        pending = (
            db.session.scalar(
                select(db.func.count(Revision.id)).where(
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


# ── Helpers ────────────────────────────────────────────────────────────────────


def _flat_nodes() -> list[OntologyNode]:
    """All nodes ordered by name, used for the 'parent' select list."""
    return list(
        db.session.scalars(select(OntologyNode).order_by(OntologyNode.name))
    )


# ── Routes ────────────────────────────────────────────────────────────────────


@admin_ontology_bp.get("/ontology")
@require_admin_access
def ontology_list():
    """Show all nodes as a tree (public + private visible to admins)."""
    tree = list_tree(public_only=False)
    return render_template("admin/ontology_list.html", tree=tree)


@admin_ontology_bp.route("/ontology/new", methods=["GET", "POST"])
@require_admin_access
def ontology_new():
    """Create a new ontology node."""
    user = current_admin_user()
    all_nodes = _flat_nodes()

    if request.method == "POST":
        slug = request.form.get("slug", "").strip()
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip() or None
        parent_id_raw = request.form.get("parent_id", "").strip()
        parent_id = int(parent_id_raw) if parent_id_raw else None
        sort_order = int(request.form.get("sort_order", 0) or 0)
        is_public = bool(request.form.get("is_public"))

        try:
            create_node(
                user,
                slug,
                name,
                description=description,
                parent_id=parent_id,
                sort_order=sort_order,
                is_public=is_public,
            )
            db.session.commit()
            flash(f"Ontology node '{name}' created.", "success")
            return redirect(url_for("admin_ontology.ontology_list"))
        except OntologyError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return render_template(
                "admin/ontology_edit.html",
                node=None,
                all_nodes=all_nodes,
                form_data=request.form,
            )

    return render_template(
        "admin/ontology_edit.html",
        node=None,
        all_nodes=all_nodes,
        form_data={},
    )


@admin_ontology_bp.route("/ontology/<int:node_id>", methods=["GET", "POST"])
@require_admin_access
def ontology_edit(node_id: int):
    """Edit an existing ontology node."""
    user = current_admin_user()
    node = db.session.get(OntologyNode, node_id)
    if node is None:
        flash("Ontology node not found.", "error")
        return redirect(url_for("admin_ontology.ontology_list"))

    all_nodes = _flat_nodes()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip() or None
        parent_id_raw = request.form.get("parent_id", "").strip()
        parent_id = int(parent_id_raw) if parent_id_raw else None
        sort_order = int(request.form.get("sort_order", 0) or 0)
        is_public = bool(request.form.get("is_public"))

        # Decide whether to explicitly pass parent_id or leave sentinel
        try:
            update_node(
                user,
                node.id,
                name=name if name else None,
                description=description,
                parent_id=parent_id,  # explicit None = clear parent
                sort_order=sort_order,
                is_public=is_public,
            )
            db.session.commit()
            flash(f"Ontology node '{node.name}' updated.", "success")
            return redirect(url_for("admin_ontology.ontology_list"))
        except OntologyError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return render_template(
                "admin/ontology_edit.html",
                node=node,
                all_nodes=all_nodes,
                form_data=request.form,
            )

    return render_template(
        "admin/ontology_edit.html",
        node=node,
        all_nodes=all_nodes,
        form_data={},
    )
