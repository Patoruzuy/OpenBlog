"""Email delivery Celery tasks.

Tasks in this module handle asynchronous email delivery so that
neither registration nor password-reset flows block on SMTP latency.

All tasks use ``apply_async`` semantics and are retried up to 3 times
with exponential back-off on transient failures.
"""

from __future__ import annotations

from celery import shared_task
from flask import render_template, url_for


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_password_reset_email(self, email: str, token: str) -> None:  # type: ignore[override]
    """Send a password-reset link to *email*.

    Parameters
    ----------
    email:
        Recipient email address.
    token:
        Signed itsdangerous token returned by
        ``AuthService.generate_password_reset_token()``.
    """
    from flask import current_app  # noqa: PLC0415

    from backend.extensions import mail  # noqa: PLC0415

    try:
        reset_url = url_for("auth.reset_password", token=token, _external=True)
        subject = "Reset your OpenBlog password"
        body = render_template("email/password_reset.html", reset_url=reset_url)
        from flask_mail import Message  # noqa: PLC0415

        msg = Message(
            subject=subject,
            recipients=[email],
            html=body,
            sender=current_app.config.get("MAIL_DEFAULT_SENDER", "noreply@openblog.dev"),
        )
        mail.send(msg)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_verification_email(self, email: str, token: str) -> None:  # type: ignore[override]
    """Send an email-verification link to *email*.

    Parameters
    ----------
    email:
        Recipient email address.
    token:
        Signed itsdangerous token returned by
        ``AuthService.generate_email_verification_token()``.
    """
    from flask import current_app  # noqa: PLC0415

    from backend.extensions import mail  # noqa: PLC0415

    try:
        verify_url = url_for("auth.verify_email", token=token, _external=True)
        subject = "Verify your OpenBlog email address"
        body = render_template("email/verify_email.html", verify_url=verify_url)
        from flask_mail import Message  # noqa: PLC0415

        msg = Message(
            subject=subject,
            recipients=[email],
            html=body,
            sender=current_app.config.get("MAIL_DEFAULT_SENDER", "noreply@openblog.dev"),
        )
        mail.send(msg)
    except Exception as exc:
        raise self.retry(exc=exc)
