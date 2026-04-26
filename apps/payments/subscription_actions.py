"""
Phase 7.7.3 — Per-subscription actions from the trainer dashboard.

Lets the trainer cancel / resume / immediately-cancel a client's
subscription without going to the Stripe dashboard. Three views:

  * cancel_at_period_end  — POST  /payments/subscription/<id>/cancel/
                             Sets cancel_at_period_end=True. Client
                             keeps access until current_period_end,
                             then Stripe stops billing.
  * resume                — POST  /payments/subscription/<id>/resume/
                             Sets cancel_at_period_end=False. Undoes
                             the above before period_end hits.
  * cancel_immediately    — POST  /payments/subscription/<id>/cancel-now/
                             Calls Subscription.delete — immediate.
                             Use sparingly; usually period-end is the
                             right call so the client gets what they paid for.

All three use stripe_account=trainer.stripe_user_id because the
subscription lives on the trainer's CONNECTED account, not on the
platform. After Stripe accepts, we mirror the new state into the
local ClientSubscription row so the UI updates without waiting for
the webhook to round-trip.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.users.dashboard_helpers import trainer_required

from .models import ClientSubscription
from .stripe_client import get_stripe, is_configured


def _back_to_client(sub):
    """Redirect target — the client detail page this sub belongs to."""
    return redirect("trainer-client-detail", client_id=sub.client_id)


def _load_subscription_for_trainer(request, sub_id):
    """Fetch a ClientSubscription owned by the logged-in trainer."""
    return get_object_or_404(
        ClientSubscription.objects.select_related("client", "plan"),
        id=sub_id,
        trainer=request.user.trainer_profile,
    )


@login_required
@require_POST
def cancel_at_period_end(request, sub_id):
    """Cancel at the end of the current billing period (most common)."""
    if not trainer_required(request):
        return redirect("landing-page")

    sub = _load_subscription_for_trainer(request, sub_id)

    if not sub.stripe_subscription_id:
        messages.error(request, "This subscription has no Stripe ID — cannot cancel.")
        return _back_to_client(sub)

    if sub.cancel_at_period_end:
        messages.info(request, "Already set to cancel at period end.")
        return _back_to_client(sub)

    if not is_configured() or not request.user.trainer_profile.stripe_user_id:
        messages.error(request, "Stripe isn't configured / connected.")
        return _back_to_client(sub)

    stripe = get_stripe()
    try:
        stripe.Subscription.modify(
            sub.stripe_subscription_id,
            cancel_at_period_end=True,
            stripe_account=request.user.trainer_profile.stripe_user_id,
        )
    except Exception as exc:    # noqa: BLE001 — surface Stripe errors verbatim
        messages.error(request, f"Stripe rejected the cancel: {exc}")
        return _back_to_client(sub)

    sub.cancel_at_period_end = True
    sub.save(update_fields=["cancel_at_period_end", "updated_at"])

    when = sub.current_period_end.strftime("%-d %b %Y") if sub.current_period_end else "the end of the current period"
    messages.success(request, f'Subscription will cancel on {when}.')
    return _back_to_client(sub)


@login_required
@require_POST
def resume(request, sub_id):
    """Undo a previously-scheduled cancellation."""
    if not trainer_required(request):
        return redirect("landing-page")

    sub = _load_subscription_for_trainer(request, sub_id)

    if not sub.cancel_at_period_end:
        messages.info(request, "Subscription wasn't scheduled to cancel.")
        return _back_to_client(sub)

    if not sub.stripe_subscription_id:
        messages.error(request, "No Stripe ID on this subscription.")
        return _back_to_client(sub)

    if not is_configured() or not request.user.trainer_profile.stripe_user_id:
        messages.error(request, "Stripe isn't configured / connected.")
        return _back_to_client(sub)

    stripe = get_stripe()
    try:
        stripe.Subscription.modify(
            sub.stripe_subscription_id,
            cancel_at_period_end=False,
            stripe_account=request.user.trainer_profile.stripe_user_id,
        )
    except Exception as exc:    # noqa: BLE001
        messages.error(request, f"Stripe rejected the resume: {exc}")
        return _back_to_client(sub)

    sub.cancel_at_period_end = False
    sub.save(update_fields=["cancel_at_period_end", "updated_at"])

    messages.success(request, "Subscription resumed — billing continues as normal.")
    return _back_to_client(sub)


@login_required
@require_POST
def cancel_immediately(request, sub_id):
    """End the subscription right now — Stripe stops further charges immediately.

    Use sparingly. Most cancellations should be cancel_at_period_end so the
    client gets to keep what they already paid for. Immediate cancel is for
    cases like fraud, refund, or "client emailed me an emergency".
    """
    if not trainer_required(request):
        return redirect("landing-page")

    sub = _load_subscription_for_trainer(request, sub_id)

    if not sub.stripe_subscription_id:
        messages.error(request, "No Stripe ID on this subscription.")
        return _back_to_client(sub)

    if not is_configured() or not request.user.trainer_profile.stripe_user_id:
        messages.error(request, "Stripe isn't configured / connected.")
        return _back_to_client(sub)

    stripe = get_stripe()
    try:
        stripe.Subscription.delete(
            sub.stripe_subscription_id,
            stripe_account=request.user.trainer_profile.stripe_user_id,
        )
    except Exception as exc:    # noqa: BLE001
        messages.error(request, f"Stripe rejected the immediate cancel: {exc}")
        return _back_to_client(sub)

    sub.status = ClientSubscription.STATUS_CANCELED
    sub.cancel_at_period_end = False
    sub.save(update_fields=["status", "cancel_at_period_end", "updated_at"])

    messages.success(request, "Subscription canceled immediately. No further charges.")
    return _back_to_client(sub)
