"""Low-level email delivery helper.

This module owns the single point of contact with the SMTP transport.
All higher-level code (digest service, notification delivery) calls
:func:`send_email` so that:

- Tests mock exactly one symbol.
- ``EMAIL_ENABLED=False`` suppresses ALL outbound mail from one place.
- Transport errors are logged and re-raised so callers can record failures.

Flask-Mail is used for the actual SMTP connection; its configuration
(``MAIL_SERVER``, ``MAIL_PORT``, ``MAIL_USE_TLS``, etc.) lives in
:class:`~backend.config.BaseConfig`.  ``MAIL_SUPPRESS_SEND=True`` in
:class:`~backend.config.TestingConfig` ensures no emails leave the process
during test runs even if ``EMAIL_ENABLED`` is ``True``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from flask import current_app
from flask_mail import Message

from backend.extensions import mail

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


def send_email(
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> None:
    """Send a transactional email.

    Parameters
    ----------
    to:
        Recipient address.
    subject:
        Email subject line.
    text_body:
        Plain-text body (always required as a fallback).
    html_body:
        Optional HTML body.  When supplied a ``multipart/alternative``
        message is sent; otherwise plain-text only.

    Raises
    ------
    Exception  (re-raised after logging)
        Any SMTP or Flask-Mail error so the caller can record a failure.

    Notes
    -----
    When ``EMAIL_ENABLED`` is ``False`` (the default in development and
    TestingConfig) the call is a no-op — it logs at DEBUG level and returns.
    ``MAIL_SUPPRESS_SEND=True`` (TestingConfig) provides a second safety net
    at the Flask-Mail layer.
    """
    if not current_app.config.get("EMAIL_ENABLED", False):
        log.debug("Email disabled; skipping send to %s subject=%r", to, subject)
        return

    sender: str = current_app.config.get("MAIL_DEFAULT_SENDER", "noreply@openblog.dev")

    msg = Message(
        subject=subject,
        recipients=[to],
        body=text_body,
        html=html_body,
        sender=sender,
    )

    try:
        mail.send(msg)
        log.debug("Email sent to %s subject=%r", to, subject)
    except Exception:
        log.exception("Failed to send email to %s subject=%r", to, subject)
        raise
