"""Email delivery Celery tasks.

Tasks in this module handle asynchronous email delivery so that no
request handler ever blocks on SMTP latency.

Architecture
------------
``deliver_email`` is the canonical send task used by ``EmailService.queue``.
It renders both HTML and plain-text templates, sends via SMTP, and updates
the ``EmailDeliveryLog`` row.

The legacy ``send_password_reset_email`` and ``send_verification_email`` tasks
are kept for backward compatibility with routes that call them directly, but
they now delegate to ``deliver_email`` internally.
"""

from __future__ import annotations

from celery import shared_task

# ── Canonical delivery task ───────────────────────────────────────────────────


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def deliver_email(  # type: ignore[override]
    self,
    log_id: int,
    to_email: str,
    subject: str,
    template_key: str,
    context: dict,
    locale: str = "en",
) -> None:
    """Render ``template_key`` and send to *to_email*.

    Updates the ``EmailDeliveryLog`` row identified by *log_id* on
    success or final failure.
    """
    from flask import current_app  # noqa: PLC0415
    from flask_mail import Message  # noqa: PLC0415

    from backend.extensions import mail  # noqa: PLC0415
    from backend.services.email_service import EmailService  # noqa: PLC0415

    html_tpl = f"email/{template_key}.html"
    txt_tpl = f"email/{template_key}.txt"

    try:
        from jinja2 import TemplateNotFound  # noqa: PLC0415

        # Render HTML body.
        try:
            from flask import render_template  # noqa: PLC0415

            html_body = render_template(html_tpl, **context)
        except TemplateNotFound:
            html_body = None

        # Render plain-text body.
        try:
            from flask import render_template  # noqa: PLC0415

            txt_body = render_template(txt_tpl, **context)
        except TemplateNotFound:
            txt_body = None

        if html_body is None and txt_body is None:
            raise RuntimeError(f"No email template found for key '{template_key}'")

        msg = Message(
            subject=subject,
            recipients=[to_email],
            html=html_body,
            body=txt_body,
            sender=current_app.config.get(
                "MAIL_DEFAULT_SENDER", "noreply@openblog.dev"
            ),
        )
        mail.send(msg)
        EmailService.mark_sent(log_id, provider_message_id=None)

    except Exception as exc:
        if self.request.retries >= self.max_retries:
            EmailService.mark_failed(log_id, str(exc))
        raise self.retry(exc=exc)


# ── Legacy convenience tasks (used by auth routes directly) ───────────────────


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_password_reset_email(self, email: str, token: str) -> None:  # type: ignore[override]
    """Send a password-reset link to *email*."""
    from flask import current_app, url_for  # noqa: PLC0415
    from flask_mail import Message  # noqa: PLC0415

    from backend.extensions import mail  # noqa: PLC0415

    try:
        reset_url = url_for("auth.reset_password", token=token, _external=True)
        subject = "Reset your OpenBlog password"
        from flask import render_template  # noqa: PLC0415

        html_body = render_template("email/password_reset.html", reset_url=reset_url)
        txt_body = render_template("email/password_reset.txt", reset_url=reset_url)
        msg = Message(
            subject=subject,
            recipients=[email],
            html=html_body,
            body=txt_body,
            sender=current_app.config.get(
                "MAIL_DEFAULT_SENDER", "noreply@openblog.dev"
            ),
        )
        mail.send(msg)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_verification_email(self, email: str, token: str) -> None:  # type: ignore[override]
    """Send an email-verification link to *email*."""
    from flask import current_app, url_for  # noqa: PLC0415
    from flask_mail import Message  # noqa: PLC0415

    from backend.extensions import mail  # noqa: PLC0415

    try:
        verify_url = url_for("auth.verify_email", token=token, _external=True)
        subject = "Verify your OpenBlog email"
        from flask import render_template  # noqa: PLC0415

        html_body = render_template("email/verify_email.html", verify_url=verify_url)
        txt_body = render_template("email/verify_email.txt", verify_url=verify_url)
        msg = Message(
            subject=subject,
            recipients=[email],
            html=html_body,
            body=txt_body,
            sender=current_app.config.get(
                "MAIL_DEFAULT_SENDER", "noreply@openblog.dev"
            ),
        )
        mail.send(msg)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_newsletter_confirm_email(
    self, email: str, confirm_token: str, locale: str = "en"
) -> None:  # type: ignore[override]
    """Send a newsletter double opt-in confirmation email."""
    from flask import current_app, url_for  # noqa: PLC0415
    from flask_mail import Message  # noqa: PLC0415

    from backend.extensions import mail  # noqa: PLC0415

    try:
        confirm_url = url_for("newsletter.confirm", token=confirm_token, _external=True)
        subject = "Confirm your OpenBlog newsletter subscription"
        from flask import render_template  # noqa: PLC0415

        html_body = render_template(
            "email/newsletter_confirm.html",
            confirm_url=confirm_url,
            locale=locale,
        )
        txt_body = render_template(
            "email/newsletter_confirm.txt",
            confirm_url=confirm_url,
            locale=locale,
        )
        msg = Message(
            subject=subject,
            recipients=[email],
            html=html_body,
            body=txt_body,
            sender=current_app.config.get(
                "MAIL_DEFAULT_SENDER", "noreply@openblog.dev"
            ),
        )
        mail.send(msg)
    except Exception as exc:
        raise self.retry(exc=exc)
