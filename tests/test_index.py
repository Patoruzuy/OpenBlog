"""Unit tests for the index (home page) route."""

from __future__ import annotations


def test_index_returns_200(auth_client):
    response = auth_client.get("/")
    assert response.status_code == 200


def test_index_contains_openblog_title(auth_client):
    response = auth_client.get("/")
    assert b"OpenBlog" in response.data


def test_index_content_type_is_html(auth_client):
    response = auth_client.get("/")
    assert "text/html" in response.content_type


def test_index_contains_hero_text(auth_client):
    response = auth_client.get("/")
    assert b"developers" in response.data.lower()
