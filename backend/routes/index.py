"""Index route — renders the home page."""

from __future__ import annotations

from flask import Blueprint, render_template

index_bp = Blueprint("index", __name__)


@index_bp.get("/")
def index():
    return render_template("base.html", title="OpenBlog"), 200
