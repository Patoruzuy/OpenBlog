"""Request-ID middleware and structured request access logging.

Assigns a UUID4 request ID to every incoming HTTP request, stores it in
``flask.g.request_id``, and echoes it back via the ``X-Request-ID`` response
header so callers can correlate requests with log entries.

A structured log line is emitted after every response with:
    method, path, status_code, duration_ms, user_id, remote_addr, request_id

Usage
-----
Call ``init_request_logging(app)`` once from ``create_app()``.
"""

from __future__ import annotations

import time
import uuid

from flask import Flask, g, has_request_context, request

# Characters allowed in a client-supplied X-Request-ID header.
_SAFE_CHARS = frozenset("0123456789abcdefABCDEF-")


def init_request_logging(app: Flask) -> None:
    """Register before/after-request hooks for request IDs and access logs."""

    @app.before_request
    def _assign_request_id() -> None:
        """Assign a request ID; honour client-supplied header when safe."""
        incoming = request.headers.get("X-Request-ID", "")
        # Accept client ID only when it looks like a UUID4 (hex + hyphens,
        # max 36 chars).  Reject anything else to avoid header injection.
        if incoming and len(incoming) <= 36 and all(c in _SAFE_CHARS for c in incoming):
            g.request_id = incoming
        else:
            g.request_id = uuid.uuid4().hex
        g._req_start = time.perf_counter()

    @app.after_request
    def _finalize_request(response):  # type: ignore[return]
        """Echo request ID in the response and emit one structured access log."""
        rid: str = getattr(g, "request_id", "-")
        response.headers["X-Request-ID"] = rid

        elapsed_ms = round(
            (time.perf_counter() - getattr(g, "_req_start", time.perf_counter()))
            * 1000,
            2,
        )

        # Best-effort user identification — silently skipped for
        # unauthenticated requests or when auth is not available.
        user_id: int | None = None
        try:
            from backend.utils.auth import get_current_user  # noqa: PLC0415

            user = get_current_user()
            if user is not None:
                user_id = user.id
        except Exception:  # noqa: BLE001
            pass

        app.logger.info(
            "%s %s → %s (%.1f ms)",
            request.method,
            request.path,
            response.status_code,
            elapsed_ms,
            extra={
                "request_id": rid,
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "duration_ms": elapsed_ms,
                "user_id": user_id,
                "remote_addr": request.remote_addr,
            },
        )
        return response

    @app.teardown_request
    def _log_unhandled_exception(exc: BaseException | None) -> None:
        """Emit an error log line when a request raises an unhandled exception."""
        if exc is None:
            return
        rid: str = getattr(g, "request_id", "-") if has_request_context() else "-"
        method = request.method if has_request_context() else "?"
        path = request.path if has_request_context() else "?"
        app.logger.error(
            "Unhandled exception on %s %s",
            method,
            path,
            exc_info=exc,
            extra={"request_id": rid},
        )
