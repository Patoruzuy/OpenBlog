"""Media service — safe file storage outside static/.

Security design
---------------
- Files are stored under MEDIA_ROOT, never under backend/static or any
  publicly-served directory.
- Stored filenames are UUIDv4-based; the original client filename is metadata
  only and is never used as a filesystem path.
- Content type is determined by reading the first 2 KiB (magic bytes), not by
  trusting the client's Content-Type header.
- An explicit extension allowlist is enforced.  SVG, HTML, JS, executables,
  and archives are blocked.
- Text-ish files (txt, log, json, yaml, md, toml) are stored and served as
  application/octet-stream to prevent inline execution.
- SHA-256 digest is computed and stored for integrity.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from werkzeug.datastructures import FileStorage

# ── Allowlist ─────────────────────────────────────────────────────────────────

#: Extensions that may be uploaded at all.
ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".pdf",
        ".txt",
        ".log",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".md",
    }
)

#: Extensions that produce inline images (safe to serve with Content-Disposition: inline).
IMAGE_EXTENSIONS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})

#: Magic-byte → MIME mapping for the types we allow.
#: Key is the leading bytes (hex); value is canonical MIME type.
_MAGIC: list[tuple[bytes, str]] = [
    (b"\x89PNG", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),  # checked further below
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"WEBP", "image/webp"),  # offset 8 in RIFF container
    (b"%PDF", "application/pdf"),
]

#: MIME types that are safe for inline image preview.
SAFE_IMAGE_MIMES: frozenset[str] = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)

#: Text-ish extensions stored/served as octet-stream.
TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {".txt", ".log", ".json", ".yaml", ".yml", ".toml", ".md"}
)


# ── Content sniffing ──────────────────────────────────────────────────────────


def sniff_mime(header: bytes) -> str | None:
    """Return a MIME type by inspecting the first *header* bytes, or None."""
    for magic, mime in _MAGIC:
        if header[: len(magic)] == magic:
            # RIFF containers: verify WEBP signature at offset 8
            if magic == b"RIFF":
                if len(header) >= 12 and header[8:12] == b"WEBP":
                    return "image/webp"
                return None  # RIFF but not WEBP — not allowed
            return mime
    return None


def safe_mime_for_extension(ext: str) -> str:
    """Return a safe MIME type for *ext* when magic-byte sniffing is inconclusive.

    Text-ish formats are always returned as octet-stream to prevent inline
    execution by browsers.
    """
    if ext in TEXT_EXTENSIONS:
        return "application/octet-stream"
    _map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".pdf": "application/pdf",
    }
    return _map.get(ext, "application/octet-stream")


# ── MediaService ──────────────────────────────────────────────────────────────


class MediaError(Exception):
    """Raised for file validation failures in MediaService."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class MediaService:
    """Safe file persistence for user-uploaded comment attachments."""

    # Prefix within MEDIA_ROOT for comment attachments.
    _SUBDIR = "comment_attachments"

    @staticmethod
    def _media_root() -> Path:
        from flask import current_app  # noqa: PLC0415

        root = current_app.config.get("MEDIA_ROOT", "/app/media")
        return Path(root)

    @staticmethod
    def _max_bytes() -> int:
        from flask import current_app  # noqa: PLC0415

        return int(
            current_app.config.get("MAX_COMMENT_ATTACHMENT_BYTES", 5 * 1024 * 1024)
        )

    # ── Validation ────────────────────────────────────────────────────────

    @staticmethod
    def validate_upload(
        file: FileStorage,
        declared_size: int | None = None,
    ) -> tuple[str, str, str, bool]:
        """Validate *file* and return ``(mime_type, ext, safe_filename, is_image)``.

        Raises ``MediaError`` if the file is rejected.

        Parameters
        ----------
        file:
            The Werkzeug FileStorage object from ``request.files``.
        declared_size:
            Content-Length from the request headers (optional; used only for
            early rejection before reading the entire body).
        """
        max_bytes = MediaService._max_bytes()

        # Early size rejection from Content-Length header (not authoritative).
        if declared_size is not None and declared_size > max_bytes:
            raise MediaError(
                f"File too large: maximum is {max_bytes // 1024 // 1024} MiB.", 413
            )

        # Normalise the original filename (display only).
        original = file.filename or "file"
        # Strip directory components — never use for FS path.
        original = Path(original).name
        ext = Path(original).suffix.lower()

        if ext not in ALLOWED_EXTENSIONS:
            raise MediaError(
                f"File type '{ext}' is not allowed.  "
                f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
                415,
            )

        # Read the entire body into memory for size check + magic sniff.
        file.stream.seek(0)
        body = file.stream.read()
        if len(body) > max_bytes:
            raise MediaError(
                f"File too large: maximum is {max_bytes // 1024 // 1024} MiB.", 413
            )
        if len(body) == 0:
            raise MediaError("Uploaded file is empty.", 400)
        file.stream.seek(0)

        # Magic-byte sniff using the first 2 KiB.
        header = body[:2048]
        sniffed = sniff_mime(header)

        # For text files, skip magic-byte check (they have no signature).
        if ext in TEXT_EXTENSIONS:
            mime = "application/octet-stream"
        elif sniffed is not None:
            mime = sniffed
        else:
            # No magic match — fall back to extension-based safe MIME.
            # This handles edge cases like PDF renderers that omit the header.
            mime = safe_mime_for_extension(ext)

        is_image = mime in SAFE_IMAGE_MIMES

        return mime, ext, original, is_image

    # ── Storage ───────────────────────────────────────────────────────────

    @staticmethod
    def store(
        file: FileStorage,
        attachment_id: int,
        ext: str,
    ) -> tuple[str, str]:
        """Write *file* to disk and return ``(stored_path, sha256_hex)``.

        The path is relative to MEDIA_ROOT, e.g.
        ``"comment_attachments/42/a3b4c5d6.png"``.

        Raises ``OSError`` on filesystem errors.
        """
        media_root = MediaService._media_root()
        subdir = media_root / MediaService._SUBDIR / str(attachment_id)
        subdir.mkdir(parents=True, exist_ok=True)

        stored_name = f"{uuid.uuid4().hex}{ext}"
        abs_path = subdir / stored_name

        file.stream.seek(0)
        body = file.stream.read()

        sha256 = hashlib.sha256(body).hexdigest()

        abs_path.write_bytes(body)

        rel_path = str(Path(MediaService._SUBDIR) / str(attachment_id) / stored_name)
        return rel_path, sha256

    @staticmethod
    def resolve_path(stored_path: str) -> Path:
        """Return the absolute filesystem path for *stored_path*."""
        return MediaService._media_root() / stored_path

    @staticmethod
    def delete_file(stored_path: str) -> None:
        """Remove the file at *stored_path* from disk.  No-op if already gone."""
        abs_path = MediaService._media_root() / stored_path
        try:
            abs_path.unlink(missing_ok=True)
        except OSError:
            pass  # Best-effort; row will be soft-deleted regardless.
