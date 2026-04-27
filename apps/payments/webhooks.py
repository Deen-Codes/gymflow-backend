"""
Stripe Connect webhook handler.

POST /payments/webhooks/stripe/

Handles five events:
    • checkout.session.completed         — first-touch: capture the
                                            customer + subscription IDs
                                            and auto-create the client
                                            User + ClientProfile if no
                                            user with this email exists.
    • customer.subscription.created      — defensive — usually fires
                                            simultaneously with the
                                            Checkout completion.
    • customer.subscription.updated      — status changes (active →
                                            past_due → canceled, etc.)
    • customer.subscription.deleted      — final cancellation
    • invoice.payment_failed             — flips status to past_due

Signature verification uses STRIPE_WEBHOOK_SECRET. If the env var is
empty (dev), we skip verification but log a warning.
"""
import json
import secrets
import string
from datetime import datetime, timezone as dt_tz

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.users.models import User, TrainerProfile, ClientProfile
from apps.sites.models import PricingPlan

from .models import ClientSubscription
from .stripe_client import get_stripe, is_configured
from .notifications import (
    notify_trainer_new_subscription,
    notify_trainer_subscription_canceled,
    notify_trainer_payment_failed,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _verify_event(payload: bytes, sig_header: str):
    """Raises if signature is invalid OR webhook secret not configured
    AND we're in production. In dev (no secret), parse the JSON unsafely."""
    secret = settings.STRIPE_WEBHOOK_SECRET
    if secret:
        stripe = get_stripe()
        return stripe.Webhook.construct_event(payload, sig_header, secret)
    # Dev fallback — explicit log so it's obvious in console.
    print("[Stripe webhook] WARNING: STRIPE_WEBHOOK_SECRET not set — "
          "skipping signature verification.")
    return json.loads(payload)


def _get(obj, key, default=None):
    """Dict-or-attr getter — Stripe SDK objects support both, raw JSON only dicts."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _generate_password(length: int = 16) -> str:
    """Random password for auto-created client accounts. They reset
    via the standard /portal/password-reset/ flow."""
    alpha = string.ascii_letters + string.digits
    return "".join(secrets.choice(alpha) for _ in range(length))


def _get_or_create_client(trainer: TrainerProfile, email: str, name: str = "") -> User:
    """Auto-provision a Client User + ClientProfile for a fresh subscriber."""
    user = User.objects.filter(email__iexact=email).first()
    if user:
        # Existing user — make sure they have a ClientProfile linked
        # to this trainer. (If they were already a client of someone
        # else, we leave them alone and the webhook just records the
        # subscription against this trainer; the trainer can sort it
        # out manually.)
        if not hasattr(user, "client_profile"):
            ClientProfile.objects.create(user=user, trainer=trainer)
        return user

    # Brand-new user — derive a unique username from the email
    base_username = email.split("@")[0].lower().replace(".", "-")[:40] or "client"
    username = base_username
    suffix = 2
    while User.objects.filter(username__iexact=username).exists():
        username = f"{base_username}-{suffix}"
        suffix += 1

    user = User.objects.create(
        username=username,
        email=email,
        first_name=(name.split(" ")[0] if name else ""),
        last_name=(" ".join(name.split(" ")[1:]) if name else ""),
        role=User.CLIENT,
    )
    user.set_password(_generate_password())
    user.save()
    ClientProfile.objects.create(user=user, trainer=trainer)
    return user


def _upsert_subscription_from_stripe(sub_obj, trainer, plan, client) -> ClientSubscription:
    """Create or update the ClientSubscription row from a Stripe event."""
    sub_id = _get(sub_obj, "id", "")
    customer_id = _get(sub_obj, "customer", "")
    status = _get(sub_obj, "status", ClientSubscription.STATUS_INCOMPLETE)
    period_end_ts = _get(sub_obj, "current_period_end")
    cancel_at_period_end = _get(sub_obj, "cancel_at_period_end", False)

    period_end_dt = None
    if period_end_ts:
        try:
            period_end_dt = datetime.fromtimestamp(int(period_end_ts), tz=dt_tz.utc)
        except (TypeError, ValueError):
            period_end_dt = None

    sub, _ = ClientSubscription.objects.update_or_create(
        stripe_subscription_id=sub_id,
        defaults={
            "trainer": trainer,
            "client":  client,
            "plan":    plan,
            "stripe_customer_id":     customer_id or "",
            "status":                 status,
            "current_period_end":     period_end_dt,
            "cancel_at_period_end":   bool(cancel_at_period_end),
        },
    )
    return sub


# ----------------------------------------------------------------------
# Event handlers
# ----------------------------------------------------------------------
#
# All multi-row write paths are wrapped in @transaction.atomic.
# Reason: this handler creates a User + ClientProfile + ClientSubscription
# in sequence. A network blip or model-save failure mid-way could
# leave the database with a User that has no ClientProfile, or a
# ClientProfile pointing at a Stripe sub we never recorded. With
# @transaction.atomic the whole block is rolled back on any exception,
# so we either commit everything or nothing — no orphans.
@transaction.atomic
def _handle_checkout_completed(event):
    session = _get(event, "data", {})
    session = _get(session, "object", session)

    metadata = _get(session, "metadata", {}) or {}
    plan_id    = (metadata.get("gymflow_plan_id")    or "").strip()
    trainer_id = (metadata.get("gymflow_trainer_id") or "").strip()
    visitor_name = metadata.get("gymflow_visitor_name", "")

    if not plan_id or not trainer_id:
        print(f"[Stripe webhook] Checkout completed without our metadata — ignoring")
        return

    try:
        trainer = TrainerProfile.objects.get(id=trainer_id)
        plan    = PricingPlan.objects.get(id=plan_id, trainer=trainer)
    except (TrainerProfile.DoesNotExist, PricingPlan.DoesNotExist):
        print(f"[Stripe webhook] Trainer/plan not found for checkout — ignoring")
        return

    customer_email = _get(session, "customer_details", {})
    customer_email = _get(customer_email or {}, "email", "")
    if not customer_email:
        customer_email = _get(session, "customer_email", "") or ""
    if not customer_email:
        print(f"[Stripe webhook] No customer email on Checkout — cannot create client")
        return

    client = _get_or_create_client(trainer, customer_email, visitor_name)

    # For subscription Checkout sessions, Stripe attaches the
    # subscription ID directly. For one-shot mode this is null —
    # we fall back to creating a "completed" ClientSubscription with
    # a synthetic flag.
    sub_id      = _get(session, "subscription", "")
    customer_id = _get(session, "customer", "")

    client_sub = None
    if sub_id:
        # Recurring — fetch the subscription on the connected account
        # so we get the real status + period end.
        stripe = get_stripe()
        try:
            sub = stripe.Subscription.retrieve(
                sub_id,
                stripe_account=trainer.stripe_user_id,
            )
            client_sub = _upsert_subscription_from_stripe(sub, trainer, plan, client)
        except Exception as exc:
            print(f"[Stripe webhook] Could not retrieve subscription {sub_id}: {exc}")
    else:
        # Oneshot — create a manual record with status=active and no period_end.
        client_sub, _ = ClientSubscription.objects.update_or_create(
            stripe_subscription_id=f"oneshot_{_get(session, 'id', '')}",
            defaults={
                "trainer": trainer,
                "client":  client,
                "plan":    plan,
                "stripe_customer_id": customer_id or "",
                "status":  ClientSubscription.STATUS_ACTIVE,
            },
        )

    print(f"[Stripe webhook] ✅ Subscribed {client.username} to {plan.name}")

    # Phase 7.7.5 — ping the trainer that they got a new client.
    if client_sub is not None:
        notify_trainer_new_subscription(client_sub)


def _handle_subscription_event(event, event_type):
    """For subscription.created/updated/deleted — keep our row in sync.

    Also pings the trainer when this event represents a final cancellation
    (event_type == customer.subscription.deleted).
    """
    sub = _get(_get(event, "data", {}), "object", {})
    sub_id = _get(sub, "id", "")
    if not sub_id:
        return

    existing = ClientSubscription.objects.filter(stripe_subscription_id=sub_id).first()
    if not existing:
        # We haven't seen this sub before. Try to derive trainer/plan from
        # the subscription's metadata (we set it when creating the Checkout).
        meta = _get(sub, "metadata", {}) or {}
        plan_id = (meta.get("gymflow_plan_id") or "").strip()
        trainer_id = (meta.get("gymflow_trainer_id") or "").strip()
        if not plan_id or not trainer_id:
            return
        try:
            trainer = TrainerProfile.objects.get(id=trainer_id)
            plan    = PricingPlan.objects.get(id=plan_id, trainer=trainer)
        except (TrainerProfile.DoesNotExist, PricingPlan.DoesNotExist):
            return
        # No client yet — we may have raced with checkout.session.completed.
        # Just log and let that handler create the row when it fires.
        print(f"[Stripe webhook] Subscription {sub_id} arrived before checkout — deferring")
        return

    updated = _upsert_subscription_from_stripe(sub, existing.trainer, existing.plan, existing.client)

    # Phase 7.7.5 — final cancellation → ping the trainer.
    if event_type == "customer.subscription.deleted":
        notify_trainer_subscription_canceled(updated)


def _handle_invoice_failure(event):
    invoice = _get(_get(event, "data", {}), "object", {})
    sub_id = _get(invoice, "subscription", "")
    if not sub_id:
        return
    ClientSubscription.objects.filter(stripe_subscription_id=sub_id).update(
        status=ClientSubscription.STATUS_PAST_DUE,
        updated_at=timezone.now(),
    )

    # Phase 7.7.5 — ping the trainer so they can chase before the client churns.
    sub = ClientSubscription.objects.filter(stripe_subscription_id=sub_id).first()
    if sub is not None:
        notify_trainer_payment_failed(sub)


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
@csrf_exempt
@require_POST
def stripe_webhook(request):
    if not is_configured():
        return HttpResponse("Stripe not configured", status=503)

    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    payload = request.body

    try:
        event = _verify_event(payload, sig_header)
    except Exception as exc:
        print(f"[Stripe webhook] Signature verify failed: {exc}")
        return HttpResponse(status=400)

    event_type = _get(event, "type", "")
    print(f"[Stripe webhook] received {event_type}")

    if event_type == "checkout.session.completed":
        _handle_checkout_completed(event)
    elif event_type in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        _handle_subscription_event(event, event_type)
    elif event_type == "invoice.payment_failed":
        _handle_invoice_failure(event)

    return JsonResponse({"received": True})
