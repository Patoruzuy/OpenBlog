"""Admin settings service — site-wide configuration management."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from backend.extensions import db
from backend.models.admin import SiteSetting
from backend.models.user import User

# ── Default settings catalogue ────────────────────────────────────────────────
# (key, default_value, group, description)
_DEFAULTS: list[tuple[str, Any, str, str]] = [
    (
        "site_title",
        "OpenBlog",
        "general",
        "Public site title shown in browser tab and header.",
    ),
    (
        "site_description",
        "A collaborative developer blog, open to improvement.",
        "general",
        "Homepage meta-description used for SEO and social sharing.",
    ),
    ("contact_email", "", "general", "Public contact email shown in footer."),
    (
        "footer_copyright",
        "",
        "general",
        "Custom footer copyright line (leave blank to auto-generate).",
    ),
    ("registration_open", True, "auth", "Allow new user self-registration."),
    (
        "require_email_verify",
        True,
        "auth",
        "Require email verification before users can contribute.",
    ),
    (
        "default_post_status",
        "draft",
        "content",
        "Default status for newly created posts.",
    ),
    ("revisions_enabled", True, "content", "Allow contributors to submit revisions."),
    ("comments_enabled", True, "content", "Allow readers to post comments."),
    (
        "maintenance_mode",
        False,
        "ops",
        "When true, show a maintenance page to non-admin visitors.",
    ),
    (
        "seo_default_image",
        "",
        "seo",
        "Default Open Graph image URL used when a post has no cover.",
    ),
    (
        "analytics_enabled",
        True,
        "ops",
        "Enable server-side page-view analytics collection.",
    ),
]


class SiteSettingsService:
    @staticmethod
    def get(key: str, default: Any = None) -> Any:
        """Return the Python-native value for *key* (JSON-decoded)."""
        row = db.session.scalar(select(SiteSetting).where(SiteSetting.key == key))
        if row is None or row.value is None:
            return default
        return json.loads(row.value)

    @staticmethod
    def set(key: str, value: Any, actor: User | None = None) -> SiteSetting:
        """Persist *value* (JSON-encoded) for *key*, creating the row if absent."""
        row = db.session.scalar(select(SiteSetting).where(SiteSetting.key == key))
        if row is None:
            row = SiteSetting(key=key)
            db.session.add(row)
        row.value = json.dumps(value)
        row.updated_by_id = actor.id if actor else None
        db.session.commit()
        return row

    @staticmethod
    def get_all() -> dict[str, Any]:
        """Return all settings keyed by name as Python-native values."""
        rows = list(db.session.scalars(select(SiteSetting)).all())
        result = {
            r.key: json.loads(r.value) if r.value is not None else None for r in rows
        }
        # Fill in defaults for keys that haven't been persisted yet.
        for key, default, _, _ in _DEFAULTS:
            result.setdefault(key, default)
        return result

    @staticmethod
    def get_all_rows() -> list:
        rows = list(
            db.session.scalars(
                select(SiteSetting).order_by(SiteSetting.group, SiteSetting.key)
            ).all()
        )
        existing_keys = {r.key for r in rows}
        # Add stub rows for any default not yet in DB (display-only, not persisted)
        extras = []
        for key, default, group, desc in _DEFAULTS:
            if key not in existing_keys:
                import types as _types
                from datetime import UTC, datetime

                stub = _types.SimpleNamespace(
                    id=None,
                    key=key,
                    value=json.dumps(default),
                    group=group,
                    description=desc,
                    updated_by_id=None,
                    updated_by=None,
                    updated_at=datetime.now(UTC),
                )
                extras.append(stub)
        return rows + extras

    @staticmethod
    def seed_defaults(actor: User | None = None) -> None:
        """Insert default values for any missing settings keys."""
        for key, default, group, desc in _DEFAULTS:
            existing = db.session.scalar(
                select(SiteSetting).where(SiteSetting.key == key)
            )
            if existing is None:
                s = SiteSetting(
                    key=key,
                    value=json.dumps(default),
                    group=group,
                    description=desc,
                    updated_by_id=actor.id if actor else None,
                )
                db.session.add(s)
        db.session.commit()
