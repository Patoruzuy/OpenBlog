"""Tests for UserService."""

from __future__ import annotations

import pytest

from backend.models.notification import Notification
from backend.services.user_service import UserService, UserServiceError

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token, db_session):
    user, _tok = make_user_token("alice@example.com", "alice")
    return user


@pytest.fixture()
def bob(make_user_token, db_session):
    user, _tok = make_user_token("bob@example.com", "bob")
    return user


@pytest.fixture()
def carol(make_user_token, db_session):
    user, _tok = make_user_token("carol@example.com", "carol")
    return user


# ── get_by_username ───────────────────────────────────────────────────────────


class TestGetByUsername:
    def test_returns_user_when_found(self, alice, db_session):
        found = UserService.get_by_username("alice")
        assert found is not None
        assert found.id == alice.id

    def test_returns_none_for_unknown(self, db_session):
        assert UserService.get_by_username("nobody") is None

    def test_case_insensitive(self, alice, db_session):
        # Usernames are stored lowercase; lookup should still work
        found = UserService.get_by_username("Alice")
        # SQLite LIKE is case-insensitive; PostgreSQL uses lower()
        # Our service does .lower() on lookup
        assert found is not None
        assert found.username == "alice"


# ── update_profile ────────────────────────────────────────────────────────────


class TestUpdateProfile:
    def test_updates_bio(self, alice, db_session):
        updated = UserService.update_profile(alice, bio="Hello world")
        assert updated.bio == "Hello world"

    def test_updates_multiple_fields(self, alice, db_session):
        updated = UserService.update_profile(
            alice,
            display_name="Alice A.",
            location="Berlin",
            tech_stack="Python, Flask",
        )
        assert updated.display_name == "Alice A."
        assert updated.location == "Berlin"
        assert updated.tech_stack == "python, flask"

    def test_none_kwargs_are_ignored(self, alice, db_session):
        # Pre-set a value, then pass None — should stay unchanged
        alice = UserService.update_profile(alice, bio="Keep me")
        updated = UserService.update_profile(alice, bio=None, location="NYC")
        assert updated.bio == "Keep me"
        assert updated.location == "NYC"

    def test_empty_string_clears_field(self, alice, db_session):
        alice = UserService.update_profile(alice, bio="Old")
        updated = UserService.update_profile(alice, bio="")
        assert updated.bio == ""


# ── published_post_count / counts ─────────────────────────────────────────────


class TestPublishedPostCount:
    def test_zero_by_default(self, alice, db_session):
        assert UserService.published_post_count(alice.id) == 0

    def test_counts_only_published(self, alice, db_session):
        from backend.extensions import db
        from backend.models.post import Post, PostStatus

        # Draft post — should not count
        draft = Post(
            author_id=alice.id,
            title="Draft",
            slug="draft-alice",
            markdown_body="# Draft",
            status=PostStatus.draft,
        )
        # Published post — should count
        pub = Post(
            author_id=alice.id,
            title="Published",
            slug="pub-alice",
            markdown_body="# Pub",
            status=PostStatus.published,
        )
        db.session.add_all([draft, pub])
        db.session.commit()
        assert UserService.published_post_count(alice.id) == 1


# ── follow / unfollow ─────────────────────────────────────────────────────────


class TestFollowUnfollow:
    def test_follow_creates_record(self, alice, bob, db_session):
        from backend.extensions import db

        UserService.follow(alice.id, bob.id)
        db.session.expire_all()
        assert UserService.is_following(alice.id, bob.id) is True

    def test_follow_creates_notification(self, alice, bob, db_session):
        from sqlalchemy import select

        from backend.extensions import db

        UserService.follow(alice.id, bob.id)
        db.session.expire_all()
        notif = db.session.scalars(
            select(Notification).where(Notification.user_id == bob.id)
        ).first()
        assert notif is not None
        assert notif.notification_type == "new_follower"

    def test_self_follow_raises(self, alice, db_session):
        with pytest.raises(UserServiceError) as exc_info:
            UserService.follow(alice.id, alice.id)
        assert exc_info.value.status_code == 400

    def test_duplicate_follow_raises_409(self, alice, bob, db_session):
        UserService.follow(alice.id, bob.id)
        with pytest.raises(UserServiceError) as exc_info:
            UserService.follow(alice.id, bob.id)
        assert exc_info.value.status_code == 409

    def test_follow_unknown_user_raises_404(self, alice, db_session):
        with pytest.raises(UserServiceError) as exc_info:
            UserService.follow(alice.id, 99999)
        assert exc_info.value.status_code == 404

    def test_unfollow_removes_record(self, alice, bob, db_session):
        from backend.extensions import db

        UserService.follow(alice.id, bob.id)
        UserService.unfollow(alice.id, bob.id)
        db.session.expire_all()
        assert UserService.is_following(alice.id, bob.id) is False

    def test_unfollow_not_following_raises_404(self, alice, bob, db_session):
        with pytest.raises(UserServiceError) as exc_info:
            UserService.unfollow(alice.id, bob.id)
        assert exc_info.value.status_code == 404


# ── follower / following counts ───────────────────────────────────────────────


class TestFollowCounts:
    def test_follower_count(self, alice, bob, carol, db_session):
        UserService.follow(alice.id, bob.id)
        UserService.follow(carol.id, bob.id)
        assert UserService.follower_count(bob.id) == 2

    def test_following_count(self, alice, bob, carol, db_session):
        UserService.follow(alice.id, bob.id)
        UserService.follow(alice.id, carol.id)
        assert UserService.following_count(alice.id) == 2

    def test_counts_zero_by_default(self, alice, db_session):
        assert UserService.follower_count(alice.id) == 0
        assert UserService.following_count(alice.id) == 0


# ── get_followers / get_following ─────────────────────────────────────────────


class TestGetFollowersFollowing:
    def test_get_followers_returns_list(self, alice, bob, carol, db_session):
        UserService.follow(alice.id, bob.id)
        UserService.follow(carol.id, bob.id)
        users, total = UserService.get_followers(bob.id, page=1, per_page=10)
        assert total == 2
        usernames = {u.username for u in users}
        assert "alice" in usernames
        assert "carol" in usernames

    def test_get_following_returns_list(self, alice, bob, carol, db_session):
        UserService.follow(alice.id, bob.id)
        UserService.follow(alice.id, carol.id)
        users, total = UserService.get_following(alice.id, page=1, per_page=10)
        assert total == 2
        usernames = {u.username for u in users}
        assert "bob" in usernames
        assert "carol" in usernames

    def test_pagination_per_page(self, alice, bob, carol, db_session):
        UserService.follow(alice.id, bob.id)
        UserService.follow(carol.id, bob.id)
        users, total = UserService.get_followers(bob.id, page=1, per_page=1)
        assert total == 2
        assert len(users) == 1

    def test_page_two(self, alice, bob, carol, db_session):
        UserService.follow(alice.id, bob.id)
        UserService.follow(carol.id, bob.id)
        users_p1, _ = UserService.get_followers(bob.id, page=1, per_page=1)
        users_p2, _ = UserService.get_followers(bob.id, page=2, per_page=1)
        assert users_p1[0].id != users_p2[0].id
