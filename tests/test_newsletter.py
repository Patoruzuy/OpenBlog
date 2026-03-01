"""Tests for the newsletter subscription lifecycle.

Scenarios covered
-----------------
- POST /newsletter/subscribe creates a pending subscription
- Confirm token activates the subscription (status → "active")
- Raw token is never stored in the DB (only the HMAC hash)
- Confirm with an already-active sub is idempotent
- Confirm with an expired token returns error page
- Unsubscribe changes status to "unsubscribed"
- Idempotent unsubscribe (second call is safe)
- Subscribe an already-active address shows same success message (enumeration-safe)
- Re-subscribe an unsubscribed address issues a new pending sub
- Invalid email is handled silently (enumeration-safe)
- NewsletterService unit: subscribe / confirm / unsubscribe / get_by_email
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.extensions import db as _db
from backend.services.newsletter_service import NewsletterError, NewsletterService

# ── Helpers ───────────────────────────────────────────────────────────────────


def _subscribe_post(client, email: str = "reader@example.com", **extra_form):
    data = {"email": email, "next": "/", **extra_form}
    return client.post("/newsletter/subscribe", data=data, follow_redirects=False)


# ── Service unit tests ────────────────────────────────────────────────────────


class TestNewsletterService:
    def test_subscribe_creates_pending_record(self, db_session):  # noqa: ARG002
        sub, token = NewsletterService.subscribe("unit@example.com")
        _db.session.commit()

        assert sub.id is not None
        assert sub.status == "pending"
        assert sub.email == "unit@example.com"
        assert token  # non-empty string

    def test_raw_token_not_stored(self, db_session):  # noqa: ARG002
        sub, token = NewsletterService.subscribe("safe@example.com")
        _db.session.commit()

        assert sub.confirm_token_hash != token
        assert sub.unsubscribe_token_hash != token

    def test_confirm_activates_subscription(self, db_session):  # noqa: ARG002
        sub, token = NewsletterService.subscribe("confirm@example.com")
        _db.session.commit()

        activated = NewsletterService.confirm(token)
        _db.session.commit()

        assert activated.status == "active"
        assert activated.confirmed_at is not None

    def test_confirm_invalid_token_raises(self, db_session):  # noqa: ARG002
        with pytest.raises(NewsletterError, match="Invalid"):
            NewsletterService.confirm("not-a-real-token")

    def test_confirm_expired_token_raises(self, db_session, app):  # noqa: ARG002
        original_ttl = app.config.get("NEWSLETTER_CONFIRM_TTL")
        app.config["NEWSLETTER_CONFIRM_TTL"] = 1  # 1 second TTL

        try:
            sub, token = NewsletterService.subscribe("expired@example.com")
            _db.session.commit()

            # Back-date the issued_at to simulate expiry
            sub.confirm_token_issued_at = datetime.now(UTC) - timedelta(seconds=10)
            _db.session.commit()

            with pytest.raises(NewsletterError, match="[Ee]xpired"):
                NewsletterService.confirm(token)
        finally:
            app.config["NEWSLETTER_CONFIRM_TTL"] = original_ttl

    def test_confirm_idempotent_for_active_sub(self, db_session):  # noqa: ARG002
        sub, token = NewsletterService.subscribe("idem@example.com")
        _db.session.commit()
        NewsletterService.confirm(token)
        _db.session.commit()

        # Confirming an already-active sub should not raise
        result = NewsletterService.confirm(token)
        assert result.status == "active"

    def test_unsubscribe_sets_status(self, db_session):  # noqa: ARG002
        sub, confirm_token = NewsletterService.subscribe("unsub@example.com")
        _db.session.commit()
        NewsletterService.confirm(confirm_token)
        _db.session.commit()

        unsub_token = NewsletterService.unsubscribe_token_for(sub)
        _db.session.commit()

        result = NewsletterService.unsubscribe(unsub_token)
        _db.session.commit()

        assert result.status == "unsubscribed"
        assert result.unsubscribed_at is not None

    def test_unsubscribe_idempotent(self, db_session):  # noqa: ARG002
        sub, confirm_token = NewsletterService.subscribe("idem2@example.com")
        _db.session.commit()
        NewsletterService.confirm(confirm_token)
        _db.session.commit()

        unsub_token = NewsletterService.unsubscribe_token_for(sub)
        _db.session.commit()

        NewsletterService.unsubscribe(unsub_token)
        _db.session.commit()

        # Should not raise on second call
        # (token rotated — generate new one for second call)
        new_unsub_token = NewsletterService.unsubscribe_token_for(sub)
        _db.session.commit()
        result = NewsletterService.unsubscribe(new_unsub_token)
        assert result.status == "unsubscribed"

    def test_unsubscribe_invalid_token_raises(self, db_session):  # noqa: ARG002
        with pytest.raises(NewsletterError, match="[Ii]nvalid"):
            NewsletterService.unsubscribe("garbage-token")

    def test_resubscribe_unsubscribed_address(self, db_session):  # noqa: ARG002
        sub, confirm_token = NewsletterService.subscribe("resub@example.com")
        _db.session.commit()
        NewsletterService.confirm(confirm_token)
        _db.session.commit()
        unsub_token = NewsletterService.unsubscribe_token_for(sub)
        _db.session.commit()
        NewsletterService.unsubscribe(unsub_token)
        _db.session.commit()

        # Re-subscribe
        sub2, token2 = NewsletterService.subscribe("resub@example.com")
        _db.session.commit()

        # Same row (by email uniqueness), new pending status
        assert sub2.id == sub.id
        assert sub2.status == "pending"
        assert token2  # fresh token issued

    def test_subscribe_already_active_is_idempotent(self, db_session):  # noqa: ARG002
        sub, token = NewsletterService.subscribe("already@example.com")
        _db.session.commit()
        NewsletterService.confirm(token)
        _db.session.commit()

        # Subscribe same active address — should return same sub without re-pending
        sub2, _ = NewsletterService.subscribe("already@example.com")
        _db.session.commit()
        assert sub2.id == sub.id
        assert sub2.status == "active"

    def test_get_by_email(self, db_session):  # noqa: ARG002
        NewsletterService.subscribe("lookup@example.com")
        _db.session.commit()

        result = NewsletterService.get_by_email("LOOKUP@EXAMPLE.COM")
        assert result is not None
        assert result.email == "lookup@example.com"

    def test_invalid_email_raises(self, db_session):  # noqa: ARG002
        with pytest.raises(NewsletterError, match="[Ii]nvalid"):
            NewsletterService.subscribe("not-an-email")

    def test_link_to_user(self, db_session):  # noqa: ARG002
        from backend.services.auth_service import AuthService

        user = AuthService.register("link@example.com", "link_user", "StrongPass123!!")
        _db.session.commit()

        NewsletterService.subscribe("link@example.com")
        _db.session.commit()

        NewsletterService.link_to_user("link@example.com", user.id)
        _db.session.commit()

        sub = NewsletterService.get_by_email("link@example.com")
        assert sub.user_id == user.id


# ── Route integration tests ───────────────────────────────────────────────────


class TestNewsletterRoutes:
    def test_subscribe_returns_redirect(self, auth_client, db_session):  # noqa: ARG002
        resp = _subscribe_post(auth_client, "route@example.com")
        # Redirects to next= URL
        assert resp.status_code == 302

    def test_subscribe_creates_pending_db_row(self, auth_client, db_session):  # noqa: ARG002
        _subscribe_post(auth_client, "pending@example.com", follow_redirects=False)

        sub = NewsletterService.get_by_email("pending@example.com")
        assert sub is not None
        assert sub.status == "pending"

    def test_subscribe_unknown_email_same_response(self, auth_client, db_session):  # noqa: ARG002
        """Subscribe always returns same redirect — enumeration-safe."""
        resp1 = _subscribe_post(auth_client, "first@example.com")
        resp2 = _subscribe_post(auth_client, "second@example.com")
        assert resp1.status_code == resp2.status_code == 302

    def test_subscribe_invalid_email_does_not_crash(self, auth_client, db_session):  # noqa: ARG002
        resp = _subscribe_post(auth_client, "not-an-email")
        # Still redirects (enumeration-safe)
        assert resp.status_code == 302

    def test_confirm_route_activates_sub(self, auth_client, db_session):  # noqa: ARG002
        sub, token = NewsletterService.subscribe("confirm_route@example.com")
        _db.session.commit()

        resp = auth_client.get(f"/newsletter/confirm?token={token}")
        assert resp.status_code == 200
        assert (
            b"confirm" in resp.data.lower()
            or b"success" in resp.data.lower()
            or b"subscribed" in resp.data.lower()
        )

        sub2 = NewsletterService.get_by_email("confirm_route@example.com")
        assert sub2.status == "active"

    def test_confirm_route_invalid_token_renders_error(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/newsletter/confirm?token=bad-token")
        assert resp.status_code == 400

    def test_confirm_route_missing_token_renders_error(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/newsletter/confirm")
        assert resp.status_code == 400

    def test_unsubscribe_route_changes_status(self, auth_client, db_session):  # noqa: ARG002
        sub, confirm_token = NewsletterService.subscribe("unsub_route@example.com")
        _db.session.commit()
        NewsletterService.confirm(confirm_token)
        _db.session.commit()

        unsub_token = NewsletterService.unsubscribe_token_for(sub)
        _db.session.commit()

        resp = auth_client.get(f"/newsletter/unsubscribe?token={unsub_token}")
        assert resp.status_code == 200

        sub2 = NewsletterService.get_by_email("unsub_route@example.com")
        assert sub2.status == "unsubscribed"

    def test_unsubscribe_route_invalid_token_renders_error(
        self, auth_client, db_session
    ):  # noqa: ARG002
        resp = auth_client.get("/newsletter/unsubscribe?token=bad-token")
        assert resp.status_code == 400

    def test_unsubscribe_route_missing_token_renders_error(
        self, auth_client, db_session
    ):  # noqa: ARG002
        resp = auth_client.get("/newsletter/unsubscribe")
        assert resp.status_code == 400
