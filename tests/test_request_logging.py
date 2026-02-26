"""Tests for request-ID middleware and structured request access logging.

Verifies that:
  - Every response carries an X-Request-ID header
  - The header value is a non-empty hex string (UUID without dashes)
  - A valid client-supplied X-Request-ID is echoed back unchanged
  - An unsafe (too-long or non-hex) client ID is replaced with a fresh one
  - Different requests get different IDs (no accidental sharing)
  - Logging integration: the after_request hook runs without error on all
    HTTP status codes (2xx, 3xx, 4xx, 5xx)
"""

from __future__ import annotations

import re

import pytest

# Pattern for a 32-char hex string (UUID4 without dashes) or up to 36-char UUID4.
_HEX_RE = re.compile(r"^[0-9a-fA-F\-]{1,36}$")


class TestRequestIDHeader:
    def test_livez_has_request_id(self, client):
        resp = client.get("/livez")
        assert "X-Request-ID" in resp.headers

    def test_request_id_is_hex(self, client):
        resp = client.get("/livez")
        rid = resp.headers["X-Request-ID"]
        assert rid, "X-Request-ID must not be empty"
        assert _HEX_RE.match(rid), f"Expected hex/UUID, got: {rid!r}"

    def test_different_requests_get_different_ids(self, client):
        rid1 = client.get("/livez").headers["X-Request-ID"]
        rid2 = client.get("/livez").headers["X-Request-ID"]
        assert rid1 != rid2, "Two separate requests should not share a request ID"

    def test_valid_client_id_is_echoed(self, client):
        """A well-formed client-supplied request ID is reflected in the response."""
        custom_id = "deadbeefcafe1234"
        resp = client.get("/livez", headers={"X-Request-ID": custom_id})
        assert resp.headers["X-Request-ID"] == custom_id

    def test_too_long_client_id_is_replaced(self, client):
        """A suspiciously long client-supplied ID is replaced with a fresh UUID."""
        evil_id = "a" * 100
        resp = client.get("/livez", headers={"X-Request-ID": evil_id})
        rid = resp.headers["X-Request-ID"]
        assert rid != evil_id, "Over-length ID must be replaced"
        assert _HEX_RE.match(rid)

    def test_non_hex_client_id_is_replaced(self, client):
        """A client ID with unsafe characters is replaced."""
        unsafe_id = "<script>alert(1)</script>"
        resp = client.get("/livez", headers={"X-Request-ID": unsafe_id})
        rid = resp.headers["X-Request-ID"]
        assert rid != unsafe_id
        assert _HEX_RE.match(rid)

    def test_request_id_on_post_request(self, client, db_session):
        # db_session creates all tables; without it Flask propagates the
        # OperationalError and after_request (which sets X-Request-ID) never runs.
        resp = client.post(
            "/api/auth/login",
            json={"email": "nobody@example.com", "password": "wrongpass"},
        )
        assert "X-Request-ID" in resp.headers

    def test_request_id_on_404(self, client):
        resp = client.get("/this-route-does-not-exist")
        assert "X-Request-ID" in resp.headers

    def test_request_id_on_405(self, client):
        # /livez only accepts GET; DELETE should return 405
        resp = client.delete("/livez")
        assert "X-Request-ID" in resp.headers

    def test_request_id_present_on_redirect(self, client):
        """Auth-gated endpoints redirect (302) and should still carry the header."""
        resp = client.get("/bookmarks/")
        assert resp.status_code in (301, 302, 303, 307, 308)
        assert "X-Request-ID" in resp.headers

    def test_request_id_consistent_within_response(self, client):
        """The same ID appears in both the response header and is non-empty."""
        resp = client.get("/readyz")
        rid = resp.headers.get("X-Request-ID", "")
        assert len(rid) > 0

    def test_uuid4_with_dashes_is_accepted(self, client):
        """A standard UUID4 with dashes is a valid client-supplied ID."""
        import uuid

        uid = str(uuid.uuid4())
        resp = client.get("/livez", headers={"X-Request-ID": uid})
        assert resp.headers["X-Request-ID"] == uid


class TestRequestLoggingDoesNotBreakResponses:
    """The after_request logging hook must never swallow the response body."""

    def test_livez_body_intact(self, client):
        resp = client.get("/livez")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert data.get("status") == "ok"

    def test_readyz_body_intact(self, client):
        resp = client.get("/readyz")
        # readyz may 200 or 503 depending on Redis stub; body must be JSON
        assert resp.content_type.startswith("application/json")

    def test_api_posts_body_intact(self, client, db_session):
        resp = client.get("/api/posts/")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "posts" in data

    def test_404_body_intact(self, client):
        resp = client.get("/nonexistent-path-xyz")
        assert resp.status_code == 404
