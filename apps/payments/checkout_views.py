"""
Public-facing Stripe Checkout flow for the PT Site.

Two views:
    POST /p/<slug>/subscribe/<plan_id>/   → creates a Checkout Session
                                              on the trainer's connected
                                              account, redirects to it
    GET  /p/<slug>/subscribe/thanks/      → friendly success page

Webhook-driven account creation lives in webhooks.py — the Checkout
handler here only sets up the redirect.
"""
from django.conf import settings
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.users.models import TrainerProfile
from apps.sites.models import PricingPlan

from .stripe_client import get_stripe, is_configured
from .sync import get_or_create_price_for_plan


@csrf_exempt
@require_POST
def start_subscribe_checkout(request, slug, plan_id):
    """Create a Stripe Checkout Session and redirect the visitor."""
    if not is_configured():
        return render(request, "public/subscribe_error.html", {
            "error": "Stripe isn't configured on the platform yet.",
        }, status=503)

    trainer = get_object_or_404(TrainerProfile, slug=slug)
    plan    = get_object_or_404(PricingPlan, id=plan_id, trainer=trainer, is_active=True)

    if not trainer.stripe_user_id:
        # Trainer hasn't connected Stripe — fall back to the manual
        # onboarding form (same as the pre-Stripe flow). Lets the
        # site keep working while the trainer's still setting up.
        return redirect(f"/p/{slug}/?plan={plan.id}#apply")

    price_id, err = get_or_create_price_for_plan(plan)
    if err is not None or not price_id:
        return render(request, "public/subscribe_error.html", {
            "error": err or "Could not create Stripe price for this tier.",
        }, status=502)

    # Pull the email + name the visitor entered on the public form
    # (if they filled in the application before clicking Subscribe).
    # For this v1, Stripe Checkout collects the email itself, so we
    # just hand it the price + customer creation flag.
    visitor_email = (request.POST.get("email") or "").strip() or None
    visitor_name  = (request.POST.get("full_name") or "").strip() or None

    stripe = get_stripe()
    base = request.build_absolute_uri("/").rstrip("/")
    success_url = f"{base}/p/{slug}/subscribe/thanks/?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url  = f"{base}/p/{slug}/?plan={plan.id}#pay"

    # `application_fee_percent` only applies to recurring (subscription)
    # mode. For one-shot Prices we'd use `payment_intent_data.application_fee_amount`
    # instead. We use mode="subscription" by default for monthly/weekly/
    # yearly intervals; oneshot tiers fall back to mode="payment".
    is_recurring = plan.interval != PricingPlan.INTERVAL_ONESHOT
    mode = "subscription" if is_recurring else "payment"

    session_kwargs = {
        "mode": mode,
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url":  cancel_url,
        "stripe_account": trainer.stripe_user_id,
        "metadata": {
            "gymflow_plan_id":    str(plan.id),
            "gymflow_trainer_id": str(trainer.id),
            "gymflow_visitor_name": visitor_name or "",
        },
    }
    if visitor_email:
        session_kwargs["customer_email"] = visitor_email

    fee_pct = settings.STRIPE_APPLICATION_FEE_PERCENT
    if is_recurring:
        session_kwargs["subscription_data"] = {
            "application_fee_percent": fee_pct,
            "metadata": {
                "gymflow_plan_id":    str(plan.id),
                "gymflow_trainer_id": str(trainer.id),
            },
        }
    else:
        # Convert percent → integer pennies for one-shot
        amount_fee = int(round(plan.price_pennies * fee_pct / 100.0))
        session_kwargs["payment_intent_data"] = {
            "application_fee_amount": amount_fee,
            "metadata": {
                "gymflow_plan_id":    str(plan.id),
                "gymflow_trainer_id": str(trainer.id),
            },
        }

    try:
        session = stripe.checkout.Session.create(**session_kwargs)
    except Exception as exc:
        return render(request, "public/subscribe_error.html", {
            "error": f"Stripe declined the Checkout request: {exc}",
        }, status=502)

    return redirect(session.url)


def subscribe_thanks(request, slug):
    """Friendly success page after Stripe redirects the new client back."""
    trainer = get_object_or_404(TrainerProfile, slug=slug)
    return render(request, "public/subscribe_thanks.html", {
        "trainer": trainer,
        "session_id": request.GET.get("session_id", ""),
    })
