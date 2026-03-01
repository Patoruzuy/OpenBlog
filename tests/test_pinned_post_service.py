"""Tests for PinnedPostService."""

from __future__ import annotations

import pytest

from backend.models.post import Post, PostStatus
from backend.services.pinned_post_service import PinnedPostError, PinnedPostService

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token, db_session):
    user, _ = make_user_token("alice@example.com", "alice")
    return user


@pytest.fixture()
def bob(make_user_token, db_session):
    user, _ = make_user_token("bob@example.com", "bob")
    return user


def _make_post(db, author_id: int, slug: str) -> Post:
    post = Post(
        author_id=author_id,
        title=f"Post {slug}",
        slug=slug,
        markdown_body="# Body",
        status=PostStatus.published,
    )
    db.session.add(post)
    db.session.commit()
    return post


# ── pin ───────────────────────────────────────────────────────────────────────


class TestPin:
    def test_pin_own_post(self, alice, db_session):
        from backend.extensions import db

        post = _make_post(db, alice.id, "alpha-post")
        pinned = PinnedPostService.pin(alice.id, post.id)
        assert pinned.user_id == alice.id
        assert pinned.post_id == post.id

    def test_cannot_pin_others_post(self, alice, bob, db_session):
        from backend.extensions import db

        post = _make_post(db, alice.id, "beta-post")
        with pytest.raises(PinnedPostError) as exc:
            PinnedPostService.pin(bob.id, post.id)
        assert exc.value.status_code == 403

    def test_duplicate_pin_raises_409(self, alice, db_session):
        from backend.extensions import db

        post = _make_post(db, alice.id, "gamma-post")
        PinnedPostService.pin(alice.id, post.id)
        with pytest.raises(PinnedPostError) as exc:
            PinnedPostService.pin(alice.id, post.id)
        assert exc.value.status_code == 409

    def test_pin_limit_raises_400(self, alice, db_session):
        from backend.extensions import db
        from backend.models.pinned_post import _MAX_PINNED

        # Fill up to the limit
        posts = [
            _make_post(db, alice.id, f"limit-post-{i}") for i in range(_MAX_PINNED)
        ]
        for post in posts:
            PinnedPostService.pin(alice.id, post.id)

        overflow = _make_post(db, alice.id, "overflow-post")
        with pytest.raises(PinnedPostError) as exc:
            PinnedPostService.pin(alice.id, overflow.id)
        assert exc.value.status_code == 400


# ── unpin ─────────────────────────────────────────────────────────────────────


class TestUnpin:
    def test_unpin_removes_pin(self, alice, db_session):
        from backend.extensions import db

        post = _make_post(db, alice.id, "delta-post")
        PinnedPostService.pin(alice.id, post.id)
        PinnedPostService.unpin(alice.id, post.id)
        assert post not in PinnedPostService.get_pinned(alice.id)

    def test_unpin_no_op_if_not_pinned(self, alice, db_session):
        from backend.extensions import db

        post = _make_post(db, alice.id, "epsilon-post")
        # Should not raise
        PinnedPostService.unpin(alice.id, post.id)


# ── get_pinned ────────────────────────────────────────────────────────────────


class TestGetPinned:
    def test_returns_empty_initially(self, alice, db_session):
        assert PinnedPostService.get_pinned(alice.id) == []

    def test_returns_pinned_posts(self, alice, db_session):
        from backend.extensions import db

        p1 = _make_post(db, alice.id, "pin-first")
        p2 = _make_post(db, alice.id, "pin-second")
        PinnedPostService.pin(alice.id, p1.id)
        PinnedPostService.pin(alice.id, p2.id)
        pinned_ids = [p.id for p in PinnedPostService.get_pinned(alice.id)]
        assert p1.id in pinned_ids
        assert p2.id in pinned_ids

    def test_only_returns_users_own_pins(self, alice, bob, db_session):
        from backend.extensions import db

        pa = _make_post(db, alice.id, "alice-pin-post")
        pb = _make_post(db, bob.id, "bob-pin-post")
        PinnedPostService.pin(alice.id, pa.id)
        PinnedPostService.pin(bob.id, pb.id)
        alice_pins = [p.id for p in PinnedPostService.get_pinned(alice.id)]
        assert pb.id not in alice_pins
