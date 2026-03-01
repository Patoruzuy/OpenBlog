"""Tests for Flask-Babel i18n — locale selection and basic translation rendering.

Covers:
  - Default English locale (no Accept-Language, no session key)
  - Spanish locale via /lang/es route persists in session
  - Unsupported locale via /lang/<x> is ignored; falls back to English
  - Accept-Language header is respected when no session key is set
  - Translated UI strings appear on pages when ES locale is active

``auth_client`` is used for tests that render full HTML pages (home, tags,
revisions) because those routes run DB queries and need ``db_session`` set up.
``client`` is fine for tests that only exercise the ``/lang/*`` redirect route
or check session state without DB-backed page renders.
"""

from __future__ import annotations

# ── Default English ───────────────────────────────────────────────────────────


def test_default_locale_is_english(auth_client):
    """Home page renders English strings with no locale set."""
    resp = auth_client.get("/", follow_redirects=True)
    assert resp.status_code == 200
    data = resp.data.decode()
    assert "How it works" in data
    assert "Cómo funciona" not in data


def test_index_lang_attr_default_english(auth_client):
    """<html lang="en"> is the default."""
    resp = auth_client.get("/", follow_redirects=True)
    assert b'lang="en"' in resp.data


# ── /lang/<locale> route ──────────────────────────────────────────────────────


def test_set_lang_es_redirect(client):
    """/lang/es redirects (302) — no DB required."""
    resp = client.get("/lang/es")
    assert resp.status_code == 302


def test_set_lang_es_persists_in_session(client):
    """After visiting /lang/es the session locale is 'es'."""
    with client.session_transaction() as sess:
        assert sess.get("locale") != "es"  # not set yet

    client.get("/lang/es")

    with client.session_transaction() as sess:
        assert sess.get("locale") == "es"


def test_set_lang_unsupported_ignored(client):
    """/lang/xx (unsupported) does NOT set the session locale."""
    client.get("/lang/xx")
    with client.session_transaction() as sess:
        assert sess.get("locale") != "xx"


def test_set_lang_back_to_en(client):
    """Switching from ES back to EN updates the session."""
    client.get("/lang/es")
    with client.session_transaction() as sess:
        assert sess.get("locale") == "es"

    client.get("/lang/en")
    with client.session_transaction() as sess:
        assert sess.get("locale") == "en"


# ── Spanish translation rendering ─────────────────────────────────────────────


def test_home_page_in_spanish(auth_client):
    """After setting locale to ES, home page contains Spanish strings."""
    auth_client.get("/lang/es")
    resp = auth_client.get("/", follow_redirects=True)
    assert resp.status_code == 200
    data = resp.data.decode()
    assert "Cómo funciona" in data
    assert "Empezar a leer" in data


def test_home_page_html_lang_es(auth_client):
    """After setting ES, <html lang="es"> is rendered."""
    auth_client.get("/lang/es")
    resp = auth_client.get("/", follow_redirects=True)
    assert b'lang="es"' in resp.data


def test_nav_sign_in_translated(auth_client):
    """Sign in nav link is translated to Spanish."""
    auth_client.get("/lang/es")
    resp = auth_client.get("/", follow_redirects=True)
    assert "Iniciar sesión" in resp.data.decode()


def test_footer_translated(auth_client):
    """Footer tagline appears in Spanish."""
    auth_client.get("/lang/es")
    resp = auth_client.get("/", follow_redirects=True)
    assert "Notas de ingeniería, abiertas a mejora." in resp.data.decode()


def test_revisions_list_in_spanish(auth_client):
    """Revision list page renders Spanish tab labels when locale=es."""
    auth_client.get("/lang/es")
    resp = auth_client.get("/revisions/", follow_redirects=True)
    assert resp.status_code == 200
    data = resp.data.decode()
    assert "Pendiente" in data or "Cola de revisión" in data


def test_tags_page_in_spanish(auth_client):
    """Tags index page renders Spanish when locale=es."""
    auth_client.get("/lang/es")
    resp = auth_client.get("/tags/", follow_redirects=True)
    assert resp.status_code == 200
    assert "Temas" in resp.data.decode()


# ── Accept-Language header ────────────────────────────────────────────────────


def test_accept_language_es_no_session(auth_client):
    """Accept-Language: es is respected when no session locale is set."""
    resp = auth_client.get(
        "/", follow_redirects=True, headers={"Accept-Language": "es,en;q=0.5"}
    )
    assert resp.status_code == 200
    assert "Cómo funciona" in resp.data.decode()


def test_accept_language_unsupported_falls_back_to_en(auth_client):
    """Accept-Language with unsupported locale falls back to English."""
    resp = auth_client.get(
        "/", follow_redirects=True, headers={"Accept-Language": "fr,de;q=0.9"}
    )
    assert resp.status_code == 200
    assert "How it works" in resp.data.decode()
