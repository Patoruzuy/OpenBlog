"""Tests for /badges catalog route and profile badge rendering."""

from __future__ import annotations

import pytest

from backend.services.badge_service import BadgeService


@pytest.fixture()
def user(make_user_token, db_session):
    u, token = make_user_token()
    return u, token


@pytest.fixture()
def seeded(db_session):
    return BadgeService.seed_defaults()


# -- /badges catalog ----------------------------------------------------------


class TestBadgeCatalogRoute:
    def test_catalog_200(self, auth_client, seeded, db_session):
        resp = auth_client.get("/badges")
        assert resp.status_code == 200

    def test_catalog_cache_control(self, auth_client, seeded, db_session):
        resp = auth_client.get("/badges")
        cc = resp.headers.get("Cache-Control", "")
        assert "public" in cc
        assert "max-age=300" in cc

    def test_catalog_contains_badge_names(self, auth_client, seeded, db_session):
        resp = auth_client.get("/badges")
        body = resp.get_data(as_text=True)
        assert "First Contribution" in body
        assert "Benchmarker" in body

    def test_catalog_unauthenticated_allowed(self, client, seeded, db_session):
        resp = client.get("/badges")
        assert resp.status_code == 200

    def test_catalog_empty_graceful(self, auth_client, db_session):
        """Catalog renders even with no badges seeded."""
        resp = auth_client.get("/badges")
        assert resp.status_code == 200


# -- Profile badge visibility -------------------------------------------------


class TestProfileBadgeVisibility:
    def test_own_profile_shows_public_badge(
        self, auth_client, make_user_token, seeded, db_session
    ):
        user, token = make_user_token()
        BadgeService.award(user.id, "first_accepted_revision")
        resp = auth_client.get(
            f"/users/{user.username}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "First Contribution" in body

    def test_public_viewer_cannot_see_workspace_badge(
        self, auth_client, make_user_token, seeded, db_session
    ):
        """A workspace-scoped badge must not appear on public profile view."""
        from backend.extensions import db  # noqa: PLC0415
        from backend.models.workspace import Workspace  # noqa: PLC0415

        profile_user, _ = make_user_token()
        # Create a real workspace to satisfy FK
        ws = Workspace(name="Prof WS", slug="prof-ws-1", owner_id=profile_user.id)
        db.session.add(ws)
        db.session.flush()
        # Award only a workspace-scoped badge
        BadgeService.award(
            profile_user.id, "first_accepted_revision", workspace_id=ws.id
        )

        # Anonymous GET of the profile
        resp = auth_client.get(f"/users/{profile_user.username}")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # The badge section should either be absent or not contain this badge key
        # (public_only=True filters workspace badges)
        assert "badge-chip--first_accepted_revision" not in body

    def test_public_viewer_sees_public_badge(
        self, auth_client, make_user_token, seeded, db_session
    ):
        profile_user, _ = make_user_token()
        BadgeService.award(profile_user.id, "first_accepted_revision")

        resp = auth_client.get(f"/users/{profile_user.username}")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "First Contribution" in body

    def test_profile_no_badges_no_section(
        self, auth_client, make_user_token, seeded, db_session
    ):
        """Profile without any badges should not render the badges section."""
        profile_user, _ = make_user_token()
        resp = auth_client.get(f"/users/{profile_user.username}")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # No badge chip rendered when user has no badges
        assert "badge-chip" not in body


# -- list_definitions_by_category ---------------------------------------------


class TestListDefinitionsByCategory:
    def test_returns_dict_with_categories(self, db_session, seeded):
        groups = BadgeService.list_definitions_by_category()
        assert isinstance(groups, dict)
        assert len(groups) > 0

    def test_contribution_category_present(self, db_session, seeded):
        groups = BadgeService.list_definitions_by_category()
        assert "contribution" in groups

    def test_experimentation_category_present(self, db_session, seeded):
        groups = BadgeService.list_definitions_by_category()
        assert "experimentation" in groups

    def test_all_categories_present(self, db_session, seeded):
        groups = BadgeService.list_definitions_by_category()
        expected = {
            "contribution",
            "knowledge",
            "experimentation",
            "impact",
            "activity",
        }
        assert expected.issubset(set(groups.keys()))
