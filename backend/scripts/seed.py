"""Database seed script.

Creates demo data for development and testing:
  - 1 admin user  (admin@openblog.dev / admin1234)
  - 1 contributor user (alice@openblog.dev / alice1234)
  - 2 sample tags
  - 2 sample posts authored by admin

Safe to run multiple times — existing records are detected by unique key and
skipped (idempotent).

Usage
-----
Via Flask CLI (preferred — uses the app context)::

    flask --app "backend.app:create_app()" seed

Direct (for CI or Docker entrypoint)::

    python -m backend.scripts.seed
"""

from __future__ import annotations

from argon2 import PasswordHasher
from sqlalchemy.exc import IntegrityError

from backend.extensions import db
from backend.models.post import Post, PostStatus
from backend.models.post_version import PostVersion
from backend.models.tag import Tag
from backend.models.user import User, UserRole

_ph = PasswordHasher()

# ── Demo users ─────────────────────────────────────────────────────────────────

ADMIN = {
    "email": "admin@openblog.dev",
    "username": "admin",
    "display_name": "OpenBlog Admin",
    "password": "admin1234",
    "role": UserRole.admin,
    "is_email_verified": True,
    "bio": "Platform administrator.",
}

CONTRIBUTOR = {
    "email": "alice@openblog.dev",
    "username": "alice",
    "display_name": "Alice Dev",
    "password": "alice1234",
    "role": UserRole.contributor,
    "is_email_verified": True,
    "bio": "Contributor and open-source enthusiast.",
    "github_url": "https://github.com/alice",
    "tech_stack": "Python,Flask,PostgreSQL,Docker",
}

# ── Demo tags ──────────────────────────────────────────────────────────────────

TAGS = [
    {"name": "Python", "slug": "python", "color": "#3776ab"},
    {"name": "Flask", "slug": "flask", "color": "#000000"},
    {"name": "Open Source", "slug": "open-source", "color": "#58a6ff"},
]

# ── Demo posts ─────────────────────────────────────────────────────────────────

POSTS = [
    {
        "slug": "welcome-to-openblog",
        "title": "Welcome to OpenBlog",
        "tag_slugs": ["python", "open-source"],
        "status": PostStatus.published,
        "reading_time_minutes": 2,
        "seo_description": "An introduction to the OpenBlog platform.",
        "markdown_body": """\
# Welcome to OpenBlog

OpenBlog is a production-ready developer blogging platform with **GitHub-style
collaborative editing**.

## What you can do

- Write Markdown posts with syntax highlighting
- Propose edits to any published post
- Review and accept community revisions
- Build your developer portfolio

## Getting started

Clone the repo, run `make up`, and start writing.

```bash
make up
curl http://localhost/livez
```

Happy blogging!
""",
    },
    {
        "slug": "collaborative-editing-guide",
        "title": "How Collaborative Editing Works",
        "tag_slugs": ["flask", "open-source"],
        "status": PostStatus.published,
        "reading_time_minutes": 4,
        "seo_description": "A guide to the GitHub-style revision workflow in OpenBlog.",
        "markdown_body": """\
# How Collaborative Editing Works

OpenBlog uses a **GitHub pull-request inspired** workflow for article revisions.

## The workflow

1. A reader spots an error or improvement opportunity in a published post.
2. They click **Propose Edit** and write their improved version in the editor.
3. They provide a one-line summary (like a commit message).
4. The revision appears in the author's review queue as a pending proposal.
5. The author reviews the unified diff and either **accepts** or **rejects** it.
6. On acceptance:
   - A `PostVersion` snapshot is created for the audit trail.
   - The post body is updated to the contributor's version.
   - The contributor earns reputation points and may unlock a badge.

## Version history

Every accepted revision is preserved as an immutable `PostVersion` record.
You can compare any two versions side-by-side using the diff viewer.

## Reputation

Contributing quality edits earns you reputation points, which unlock badges
and increase your visibility on the leaderboard.
""",
    },
]


# ── Seed helpers ───────────────────────────────────────────────────────────────


def _get_or_create_user(data: dict) -> tuple[User, bool]:
    """Return (user, created).  Does not flush/commit."""
    existing = db.session.query(User).filter_by(email=data["email"]).first()
    if existing:
        return existing, False
    user = User(
        email=data["email"],
        username=data["username"],
        display_name=data.get("display_name"),
        password_hash=_ph.hash(data["password"]),
        role=data["role"],
        is_email_verified=data.get("is_email_verified", False),
        bio=data.get("bio"),
        github_url=data.get("github_url"),
        tech_stack=data.get("tech_stack"),
    )
    db.session.add(user)
    return user, True


def _get_or_create_tag(data: dict) -> tuple[Tag, bool]:
    existing = db.session.query(Tag).filter_by(slug=data["slug"]).first()
    if existing:
        return existing, False
    tag = Tag(name=data["name"], slug=data["slug"], color=data.get("color"))
    db.session.add(tag)
    return tag, True


def _get_or_create_post(data: dict, author: User, tags: list[Tag]) -> tuple[Post, bool]:
    existing = db.session.query(Post).filter_by(slug=data["slug"]).first()
    if existing:
        return existing, False
    post = Post(
        slug=data["slug"],
        title=data["title"],
        markdown_body=data["markdown_body"],
        status=data["status"],
        reading_time_minutes=data["reading_time_minutes"],
        seo_description=data.get("seo_description"),
        author_id=author.id,
        version=1,
    )
    db.session.add(post)
    db.session.flush()  # assign post.id before touching M2M or creating PostVersion

    # Append tags after flush so the post has a real PK — avoids StaleDataError
    # from SQLAlchemy attempting to DELETE phantom secondary rows on a new object.
    for tag in tags:
        post.tags.append(tag)
    db.session.flush()

    # Seed the initial PostVersion (v1) for audit trail completeness.
    v1 = PostVersion(
        post_id=post.id,
        version_number=1,
        markdown_body=data["markdown_body"],
        accepted_by_id=author.id,
    )
    db.session.add(v1)
    return post, True


# ── Main entrypoint ────────────────────────────────────────────────────────────


def run_seed() -> None:
    """Seed the database.  Prints a summary of created records."""
    try:
        # Users
        admin, admin_created = _get_or_create_user(ADMIN)
        alice, alice_created = _get_or_create_user(CONTRIBUTOR)
        db.session.flush()

        # Tags
        tag_objects: dict[str, Tag] = {}
        for tag_data in TAGS:
            tag, _ = _get_or_create_tag(tag_data)
            db.session.flush()
            tag_objects[tag_data["slug"]] = tag

        # Posts
        for post_data in POSTS:
            post_tags = [
                tag_objects[s] for s in post_data["tag_slugs"] if s in tag_objects
            ]
            post, created = _get_or_create_post(post_data, admin, post_tags)
            db.session.flush()
            print(f"  {'[created]' if created else '[exists] '} Post: {post.slug!r}")

        db.session.commit()

        print(f"  {'[created]' if admin_created else '[exists] '} Admin: {admin.email}")
        print(f"  {'[created]' if alice_created else '[exists] '} User:  {alice.email}")

    except IntegrityError as exc:
        db.session.rollback()
        print(f"Seed failed (IntegrityError): {exc.orig}")
        raise


if __name__ == "__main__":
    # Allow running directly: python -m backend.scripts.seed
    from backend.app import create_app

    app = create_app()
    with app.app_context():
        run_seed()
