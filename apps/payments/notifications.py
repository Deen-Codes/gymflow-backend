"""
Phase 7.7.5 — Trainer email notifications.

Out-of-band pings to the trainer when something noteworthy happens
on Stripe: new subscriber, cancellation, failed payment. Sent via
the same Resend email backend used for the customer portal links.

Called from the webhook handlers on the same request as the Stripe
event delivery, but each call is wrapped in try/except so a flaky
email never blocks the webhook from returning 200 to Stripe (which
would cause Stripe to retry deliveries forever and double-create state).
"""
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from .models import ClientSubscription


def _trainer_email(client_sub: ClientSubscription) -> str:
    """The trainer's email address, or empty string if missing."""
    return (client_sub.trainer.user.email or "").strip()


def _trainer_name(client_sub: ClientSubscription) -> str:
    """Friendly trainer name for greetings."""
    return (
        client_sub.trainer.user.first_name
        or client_sub.trainer.business_name
        or client_sub.trainer.user.username
    )


def _client_display(client_sub: ClientSubscription) -> str:
    """Friendly client name for the email body."""
    full = client_sub.client.get_full_name().strip()
    if full and client_sub.client.email:
        return f"{full} ({client_sub.client.email})"
    if full:
        return full
    return client_sub.client.email or client_sub.client.username


def _send(template_base: str, subject: str, client_sub: ClientSubscription,
          extra_ctx: dict | None = None) -> None:
    """Common sender — renders txt + html, mails the trainer.

    Wrapped in try/except so webhook never fails because of a flaky email.
    """
    to_email = _trainer_email(client_sub)
    if not to_email:
        return  # No trainer email on file — silently drop, nothing to do.

    ctx = {
        "trainer_name": _trainer_name(client_sub),
        "client_display": _client_display(client_sub),
        "client_username": client_sub.client.username,
        "plan_name": client_sub.plan.name if client_sub.plan else "(deleted plan)",
        "plan_price_display": (
            client_sub.plan.price_display if client_sub.plan else ""
        ),
        "plan_interval": (
            client_sub.plan.get_interval_display() if client_sub.plan else ""
        ),
        "current_period_end": client_sub.current_period_end,
        "trainer_clients_url": "https://gymflow.coach/dashboard/clients/",
        "client_detail_url": f"https://gymflow.coach/dashboard/clients/{client_sub.client_id}/",
    }
    if extra_ctx:
        ctx.update(extra_ctx)

    try:
        text_body = render_to_string(f"payments/notifications/{template_base}.txt", ctx)
        html_body = render_to_string(f"payments/notifications/{template_base}.html", ctx)
        send_mail(
            subject=subject,
            message=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[to_email],
            html_message=html_body,
            fail_silently=True,
        )
    except Exception as exc:        # noqa: BLE001
        print(f"[notifications] {template_base} send failed: {exc}")


# -------------------------------------------------------------------
# Public API — called from webhook handlers.
# -------------------------------------------------------------------
def notify_trainer_new_subscription(client_sub: ClientSubscription) -> None:
    plan_name = client_sub.plan.name if client_sub.plan else "your tier"
    _send(
        template_base="trainer_new_sub",
        subject=f"New client — {client_sub.client.username} subscribed to {plan_name}",
        client_sub=client_sub,
    )


def notify_trainer_subscription_canceled(client_sub: ClientSubscription) -> None:
    _send(
        template_base="trainer_canceled",
        subject=f"Subscription canceled — {client_sub.client.username}",
        client_sub=client_sub,
    )


def notify_trainer_payment_failed(client_sub: ClientSubscription) -> None:
    _send(
        template_base="trainer_payment_failed",
        subject=f"Payment failed — {client_sub.client.username}",
        client_sub=client_sub,
    )
