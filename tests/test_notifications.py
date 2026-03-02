"""Tests for the Notifications MVP.

Coverage
--------
- subscribe / unsubscribe / is_subscribed (permission checks, idempotency)
- emit + fanout task (synchronous via CELERY_TASK_ALWAYS_EAGER)
  - revision.accepted notifies author (direct participant) + watchers
  - revision.rejected notifies author
  - ai_review.completed notifies requester
  - actor never receives self-notification
  - dedup fingerprint prevents double rows on retries
  - workspace privacy: non-member never receives notification
- NotificationService inbox helpers (mark_read, mark_all_read, unread_count)
- SSR routes: auth-gated, Cache-Control: private, no-store, POST mark-read, POST read-all
- Backward compat: notification_type == "revision_accepted" still set by fanout
"""

from __future__ import annotations

import pytest

from backend.extensions import db
from backend.models.notification import Notification
from backend.models.post import Post, PostStatus
from backend.services.notification_service import (
    NotificationError,
    NotificationService,
    compute_fingerprint,
    create_notification_for_user,
    emit,
    filter_recipients_by_access,
    get_recipients,
    is_subscribed,
    subscribe,
    unsubscribe,
)


# ── Shared fixtures ───────────────────────────────────────────────────────────


@pytest.fixture()
def alice(make_user_token, db_session):  # noqa: ARG001
    user, _ = make_user_token("alice@example.com", "alice", role="contributor")
    return user


@pytest.fixture()
def bob(make_user_token, db_session):  # noqa: ARG001
    user, _ = make_user_token("bob@example.com", "bob", role="contributor")
    return user


@pytest.fixture()
def editor(make_user_token, db_session):  # noqa: ARG001
    user, _ = make_user_token("editor@example.com", "editor", role="editor")
    return user


@pytest.fixture()
def pub_post(alice):
    """Public, published post authored by alice."""
    post = Post(
        author_id=alice.id,
        title="Test Post",
        slug="test-post",
        markdown_body="# Hello world",
        status=PostStatus.published,
    )
    db.session.add(post)
    db.session.commit()
    return post


@pytest.fixture()
def workspace_and_doc(alice, bob):
    """Workspace owned by alice with bob as contributor.  Returns (ws, doc)."""
    from backend.models.workspace import WorkspaceMemberRole
    from backend.services import workspace_service as ws_svc

    ws = ws_svc.create_workspace(name="TestWS", owner=alice)
    ws_svc.add_member(ws, bob, WorkspaceMemberRole.editor)

    doc = ws_svc.create_workspace_document(
        workspace=ws,
        author=alice,
        title="Design Doc",
        markdown_body="# Design",
    )
    db.session.commit()
    return ws, doc


# ── subscribe ─────────────────────────────────────────────────────────────────


class TestSubscribe:
    def test_subscribe_to_public_post(self, alice, pub_post, db_session):  # noqa: ARG002
        sub = subscribe(alice, "post", pub_post.id)
        assert sub.user_id == alice.id
        assert sub.target_type == "post"
        assert sub.target_id == pub_post.id

    def test_subscribe_public_post_is_idempotent(self, alice, pub_post, db_session):  # noqa: ARG002
        sub1 = subscribe(alice, "post", pub_post.id)
        sub2 = subscribe(alice, "post", pub_post.id)
        assert sub1.id == sub2.id

    def test_subscribe_to_nonexistent_post_raises_404(self, alice, db_session):  # noqa: ARG002
        with pytest.raises(NotificationError) as exc_info:
            subscribe(alice, "post", 99999)
        assert exc_info.value.status_code == 404

    def test_subscribe_to_unpublished_post_raises_400(self, alice, db_session):  # noqa: ARG002
        draft = Post(
            author_id=alice.id,
            title="Draft",
            slug="my-draft",
            markdown_body="Draft content",
            status=PostStatus.draft,
        )
        db.session.add(draft)
        db.session.commit()

        with pytest.raises(NotificationError) as exc_info:
            subscribe(alice, "post", draft.id)
        assert exc_info.value.status_code == 400

    def test_non_member_cannot_subscribe_to_workspace(
        self, bob, workspace_and_doc, db_session  # noqa: ARG002
    ):
        ws, _ = workspace_and_doc
        # Create an outsider user.
        from backend.services.auth_service import AuthService

        outsider = AuthService.register("out@example.com", "outsider", "StrongPass123!!")
        with pytest.raises(NotificationError) as exc_info:
            subscribe(outsider, "workspace", ws.id)
        assert exc_info.value.status_code == 403

    def test_workspace_member_can_subscribe_to_workspace(
        self, alice, workspace_and_doc, db_session  # noqa: ARG002
    ):
        ws, _ = workspace_and_doc
        sub = subscribe(alice, "workspace", ws.id)
        assert sub.target_type == "workspace"
        assert sub.target_id == ws.id

    def test_non_member_cannot_subscribe_to_workspace_doc(
        self, workspace_and_doc, db_session  # noqa: ARG002
    ):
        ws, doc = workspace_and_doc
        from backend.services.auth_service import AuthService

        outsider = AuthService.register("out2@example.com", "outsider2", "StrongPass123!!")
        with pytest.raises(NotificationError) as exc_info:
            subscribe(outsider, "post", doc.id)
        assert exc_info.value.status_code == 403

    def test_workspace_member_can_subscribe_to_doc(
        self, bob, workspace_and_doc, db_session  # noqa: ARG002
    ):
        _, doc = workspace_and_doc
        sub = subscribe(bob, "post", doc.id)
        assert sub.target_id == doc.id

    def test_invalid_target_type_raises_400(self, alice, db_session):  # noqa: ARG002
        with pytest.raises(NotificationError) as exc_info:
            subscribe(alice, "banana", 1)
        assert exc_info.value.status_code == 400


# ── unsubscribe ───────────────────────────────────────────────────────────────


class TestUnsubscribe:
    def test_unsubscribe_returns_true_when_found(
        self, alice, pub_post, db_session  # noqa: ARG002
    ):
        subscribe(alice, "post", pub_post.id)
        result = unsubscribe(alice, "post", pub_post.id)
        assert result is True

    def test_unsubscribe_is_idempotent(self, alice, pub_post, db_session):  # noqa: ARG002
        result = unsubscribe(alice, "post", pub_post.id)
        assert result is False  # never subscribed

    def test_subscription_gone_after_unsubscribe(
        self, alice, pub_post, db_session  # noqa: ARG002
    ):
        subscribe(alice, "post", pub_post.id)
        unsubscribe(alice, "post", pub_post.id)
        assert is_subscribed(alice, "post", pub_post.id) is False


# ── is_subscribed ─────────────────────────────────────────────────────────────


class TestIsSubscribed:
    def test_false_before_subscribe(self, alice, pub_post, db_session):  # noqa: ARG002
        assert is_subscribed(alice, "post", pub_post.id) is False

    def test_true_after_subscribe(self, alice, pub_post, db_session):  # noqa: ARG002
        subscribe(alice, "post", pub_post.id)
        assert is_subscribed(alice, "post", pub_post.id) is True

    def test_other_user_not_subscribed(self, alice, bob, pub_post, db_session):  # noqa: ARG002
        subscribe(alice, "post", pub_post.id)
        assert is_subscribed(bob, "post", pub_post.id) is False


# ── Fanout ────────────────────────────────────────────────────────────────────


class TestFanout:
    """Tests for the async fanout task (runs eagerly in tests)."""

    @pytest.fixture()
    def _setup(self, make_user_token, db_session):  # noqa: ARG002
        """Return (post_author, contributor, editor, pub_post) for revision tests."""
        post_author, _ = make_user_token("pa@example.com", "post_author", role="editor")
        contributor, _ = make_user_token("contrib@example.com", "contrib", role="contributor")
        editor_user, _ = make_user_token("ed@example.com", "ed", role="editor")

        post = Post(
            author_id=post_author.id,
            title="Fanout Post",
            slug="fanout-post",
            markdown_body="# Fanout",
            status=PostStatus.published,
        )
        db.session.add(post)
        db.session.commit()
        return post_author, contributor, editor_user, post

    def test_revision_accepted_notifies_author_even_without_subscription(
        self, _setup, db_session  # noqa: ARG002
    ):
        """revision author is a direct participant → notified without subscribing."""
        from backend.services.revision_service import RevisionService
        from sqlalchemy import select

        _, contributor, editor_user, post = _setup

        revision = RevisionService.submit(
            post_id=post.id,
            author_id=contributor.id,
            proposed_markdown="# Fanout\n\nImproved.",
            summary="Improve prose",
        )
        RevisionService.accept(revision.id, reviewer_id=editor_user.id)

        notif = db.session.scalar(
            select(Notification).where(
                Notification.user_id == contributor.id,
                Notification.event_type == "revision.accepted",
            )
        )
        assert notif is not None
        assert notif.is_read is False

    def test_revision_accepted_notifies_watcher(
        self, _setup, make_user_token, db_session  # noqa: ARG002
    ):
        """A post watcher receives the revision.accepted notification."""
        from backend.services.revision_service import RevisionService
        from sqlalchemy import select

        _, contributor, editor_user, post = _setup
        watcher, _ = make_user_token("watcher@example.com", "watcher", role="contributor")

        subscribe(watcher, "post", post.id)
        revision = RevisionService.submit(
            post_id=post.id,
            author_id=contributor.id,
            proposed_markdown="# Fanout\n\nChanged.",
            summary="Minor change",
        )
        RevisionService.accept(revision.id, reviewer_id=editor_user.id)

        notif = db.session.scalar(
            select(Notification).where(
                Notification.user_id == watcher.id,
                Notification.event_type == "revision.accepted",
            )
        )
        assert notif is not None

    def test_revision_rejected_notifies_author(
        self, _setup, db_session  # noqa: ARG002
    ):
        from backend.services.revision_service import RevisionService
        from sqlalchemy import select

        _, contributor, editor_user, post = _setup

        revision = RevisionService.submit(
            post_id=post.id,
            author_id=contributor.id,
            proposed_markdown="# Fanout\n\nBad change.",
            summary="Bad change",
        )
        RevisionService.reject(revision.id, reviewer_id=editor_user.id, note="Not good enough")

        notif = db.session.scalar(
            select(Notification).where(
                Notification.user_id == contributor.id,
                Notification.event_type == "revision.rejected",
            )
        )
        assert notif is not None
        assert "Not good enough" in notif.body

    def test_actor_does_not_self_notify(
        self, _setup, db_session  # noqa: ARG002
    ):
        """The reviewing editor should not receive a notification for their own action."""
        from backend.services.revision_service import RevisionService
        from sqlalchemy import select

        _, contributor, editor_user, post = _setup

        revision = RevisionService.submit(
            post_id=post.id,
            author_id=contributor.id,
            proposed_markdown="# Fanout\n\nEdited.",
            summary="Edit",
        )
        # Subscribe the editor to the post so they would normally be notified.
        subscribe(editor_user, "post", post.id)
        RevisionService.accept(revision.id, reviewer_id=editor_user.id)

        notif = db.session.scalar(
            select(Notification).where(
                Notification.user_id == editor_user.id,
                Notification.event_type == "revision.accepted",
            )
        )
        assert notif is None

    def test_notification_dedup_fingerprint(
        self, alice, pub_post, db_session  # noqa: ARG002
    ):
        """Second call with the same payload must not create a duplicate row."""
        payload = {
            "post_id": pub_post.id,
            "post_title": pub_post.title,
            "revision_id": 1,
            "revision_author_id": alice.id,
        }
        create_notification_for_user(
            alice.id, "revision.accepted", None, "revision", 1, payload
        )
        db.session.commit()

        # Flush session so the next insert will encounter the unique constraint.
        db.session.expire_all()
        result = create_notification_for_user(
            alice.id, "revision.accepted", None, "revision", 1, payload
        )
        assert result is None  # dedup — skipped

        from sqlalchemy import func, select

        count = db.session.scalar(
            select(func.count()).where(
                Notification.user_id == alice.id,
                Notification.event_type == "revision.accepted",
            )
        )
        assert count == 1


# ── Access filtering ──────────────────────────────────────────────────────────


class TestAccessFilter:
    def test_non_member_filtered_from_workspace_notification(
        self, alice, bob, workspace_and_doc, db_session  # noqa: ARG002
    ):
        """An outsider subscribed at the DB level is stripped by the access filter."""
        ws, doc = workspace_and_doc

        # Manually add both subscription rows, bypassing permission check.
        from backend.models.subscription import Subscription

        outsider_sub = Subscription(user_id=bob.id, target_type="workspace", target_id=ws.id)
        db.session.add(outsider_sub)

        # bob is a workspace member per workspace_and_doc fixture, but let's also
        # test with a freshly created non-member.
        from backend.services.auth_service import AuthService

        non_member = AuthService.register("nm@example.com", "nonmember", "StrongPass123!!")

        recipients = {alice.id, non_member.id}
        accessible = filter_recipients_by_access(recipients, "workspace", ws.id)

        # alice (owner) is a member; non_member is not
        assert alice.id in accessible
        assert non_member.id not in accessible

    def test_non_member_filtered_from_workspace_doc_notification(
        self, alice, workspace_and_doc, db_session  # noqa: ARG002
    ):
        ws, doc = workspace_and_doc

        from backend.services.auth_service import AuthService

        non_member = AuthService.register("nm2@example.com", "nonmember2", "StrongPass123!!")

        recipients = {alice.id, non_member.id}
        accessible = filter_recipients_by_access(recipients, "post", doc.id)

        assert alice.id in accessible
        assert non_member.id not in accessible

    def test_published_public_post_allows_all(self, alice, bob, pub_post, db_session):  # noqa: ARG002
        recipients = {alice.id, bob.id}
        accessible = filter_recipients_by_access(recipients, "post", pub_post.id)
        assert accessible == recipients


# ── get_recipients ────────────────────────────────────────────────────────────


class TestGetRecipients:
    def test_includes_post_watchers(self, alice, bob, pub_post, db_session):  # noqa: ARG002
        subscribe(bob, "post", pub_post.id)
        recipients = get_recipients(
            "post.published",
            "post",
            pub_post.id,
            {"post_title": pub_post.title},
        )
        assert bob.id in recipients

    def test_includes_revision_author_as_direct_participant(
        self, alice, pub_post, db_session  # noqa: ARG002
    ):
        payload = {
            "post_id": pub_post.id,
            "revision_author_id": alice.id,
        }
        recipients = get_recipients("revision.accepted", "revision", 1, payload)
        assert alice.id in recipients

    def test_includes_ai_review_requester(self, alice, pub_post, db_session):  # noqa: ARG002
        payload = {"requester_id": alice.id, "post_title": pub_post.title}
        recipients = get_recipients("ai_review.completed", "post", pub_post.id, payload)
        assert alice.id in recipients


# ── Inbox helpers ─────────────────────────────────────────────────────────────


class TestInboxHelpers:
    def _make_notif(self, user_id, *, is_read=False):
        n = Notification(
            user_id=user_id,
            notification_type="test",
            title="Test Notification",
            is_read=is_read,
        )
        db.session.add(n)
        db.session.commit()
        return n

    def test_unread_count_initial(self, alice, db_session):  # noqa: ARG002
        assert NotificationService.unread_count(alice.id) == 0

    def test_unread_count_increments(self, alice, db_session):  # noqa: ARG002
        self._make_notif(alice.id)
        self._make_notif(alice.id)
        assert NotificationService.unread_count(alice.id) == 2

    def test_unread_count_excludes_read(self, alice, db_session):  # noqa: ARG002
        self._make_notif(alice.id, is_read=True)
        self._make_notif(alice.id, is_read=False)
        assert NotificationService.unread_count(alice.id) == 1

    def test_mark_read_sets_is_read(self, alice, db_session):  # noqa: ARG002
        n = self._make_notif(alice.id)
        NotificationService.mark_read(n.id, alice.id)
        db.session.expire(n)
        assert n.is_read is True

    def test_mark_read_wrong_user_raises_404(self, alice, bob, db_session):  # noqa: ARG002
        n = self._make_notif(alice.id)
        with pytest.raises(NotificationError) as exc_info:
            NotificationService.mark_read(n.id, bob.id)
        assert exc_info.value.status_code == 404

    def test_mark_all_read(self, alice, db_session):  # noqa: ARG002
        self._make_notif(alice.id)
        self._make_notif(alice.id)
        count = NotificationService.mark_all_read(alice.id)
        assert count == 2
        assert NotificationService.unread_count(alice.id) == 0

    def test_mark_all_read_returns_zero_when_none(self, alice, db_session):  # noqa: ARG002
        count = NotificationService.mark_all_read(alice.id)
        assert count == 0


# ── SSR routes ────────────────────────────────────────────────────────────────


class TestNotificationRoutes:
    def test_inbox_requires_auth(self, auth_client, db_session):  # noqa: ARG002
        resp = auth_client.get("/notifications/", follow_redirects=False)
        assert resp.status_code == 302

    def test_inbox_has_cache_control(
        self, auth_client, make_user_token, db_session  # noqa: ARG002
    ):
        user, token = make_user_token("inbox@example.com", "inbox_user")
        with auth_client.session_transaction() as sess:
            sess["user_id"] = user.id
        resp = auth_client.get("/notifications/")
        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc

    def test_mark_notification_read_post(
        self, auth_client, make_user_token, db_session  # noqa: ARG002
    ):
        user, _ = make_user_token("rdr@example.com", "rdr_user")
        notif = Notification(
            user_id=user.id,
            notification_type="test",
            title="Hello",
            is_read=False,
        )
        db.session.add(notif)
        db.session.commit()

        with auth_client.session_transaction() as sess:
            sess["user_id"] = user.id

        resp = auth_client.post(
            f"/notifications/{notif.id}/read",
            follow_redirects=False,
        )
        # Should redirect after marking read.
        assert resp.status_code in (302, 200)

        db.session.expire(notif)
        assert notif.is_read is True

    def test_mark_all_read_post(
        self, auth_client, make_user_token, db_session  # noqa: ARG002
    ):
        user, _ = make_user_token("rda@example.com", "rda_user")
        for _ in range(3):
            db.session.add(
                Notification(
                    user_id=user.id,
                    notification_type="test",
                    title="Unread",
                    is_read=False,
                )
            )
        db.session.commit()

        with auth_client.session_transaction() as sess:
            sess["user_id"] = user.id

        resp = auth_client.post("/notifications/read-all", follow_redirects=False)
        assert resp.status_code in (302, 200)
        assert NotificationService.unread_count(user.id) == 0


# ── Backward compat ───────────────────────────────────────────────────────────


class TestBackwardCompat:
    def test_notification_type_set_to_revision_accepted(
        self, bob, editor, pub_post, db_session  # noqa: ARG002
    ):
        """notification_type must equal 'revision_accepted' for existing tests."""
        from backend.services.revision_service import RevisionService
        from sqlalchemy import select

        revision = RevisionService.submit(
            post_id=pub_post.id,
            author_id=bob.id,
            proposed_markdown="# Hello world\n\nNew content.",
            summary="Fix content",
        )
        RevisionService.accept(revision.id, reviewer_id=editor.id)

        notif = db.session.scalar(
            select(Notification).where(
                Notification.user_id == bob.id,
                Notification.notification_type == "revision_accepted",
            )
        )
        assert notif is not None, (
            "notification_type='revision_accepted' must be set for backward compat"
        )

    def test_notification_type_set_to_revision_rejected(
        self, bob, editor, pub_post, db_session  # noqa: ARG002
    ):
        from backend.services.revision_service import RevisionService
        from sqlalchemy import select

        revision = RevisionService.submit(
            post_id=pub_post.id,
            author_id=bob.id,
            proposed_markdown="# Hello world\n\nBad.",
            summary="Bad change",
        )
        RevisionService.reject(revision.id, reviewer_id=editor.id, note="")

        notif = db.session.scalar(
            select(Notification).where(
                Notification.user_id == bob.id,
                Notification.notification_type == "revision_rejected",
            )
        )
        assert notif is not None


# ── compute_fingerprint ───────────────────────────────────────────────────────


class TestComputeFingerprint:
    def test_same_inputs_same_fingerprint(self):
        fp1 = compute_fingerprint("revision.accepted", "revision", 1, {"revision_id": 5})
        fp2 = compute_fingerprint("revision.accepted", "revision", 1, {"revision_id": 5})
        assert fp1 == fp2

    def test_different_version_different_fingerprint(self):
        fp1 = compute_fingerprint("post.published", "post", 1, {"version": 1})
        fp2 = compute_fingerprint("post.published", "post", 1, {"version": 2})
        assert fp1 != fp2

    def test_different_user_different_fingerprint(self):
        # Fingerprint is per-event, not per-user — but different target_id → different
        fp1 = compute_fingerprint("revision.accepted", "revision", 1, {})
        fp2 = compute_fingerprint("revision.accepted", "revision", 2, {})
        assert fp1 != fp2
