"""
Phase 7.7.1 — Stripe Connect OAuth.

Three views in this turn:
  * connect      — POST  /payments/oauth/connect/
                    Generates a state nonce + redirects the trainer
                    to Stripe's authorize URL.
  * callback     — GET   /payments/oauth/callback/?code=...&state=...
                    Stripe sends the trainer back here. We verify
                    the state, swap the code for the connected
                    account ID, and stash it on TrainerProfile.
  * disconnect   — POST  /payments/oauth/disconnect/
                    Revokes our access (Stripe-side) and clears the
                    stripe_user_id locally.

Webhooks + Checkout flow ship in the next batch.
"""
import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.users.dashboard_helpers import trainer_required

from .models import StripeOAuthState
from .stripe_client import get_stripe, is_configured


@login_required
@require_POST
def stripe_oauth_connect(request):
    """Kick off the Stripe Connect OAuth flow for the logged-in trainer."""
    if not trainer_required(request):
        return redirect("landing-page")

    if not is_configured():
        messages.error(
            request,
            "Stripe isn't configured yet. Set STRIPE_SECRET_KEY + "
            "STRIPE_CLIENT_ID env vars on the server first.",
        )
        return redirect("trainer-settings-page")

    profile = request.user.trainer_profile

    # Burn any old states so the trainer can't reuse a stale nonce.
    StripeOAuthState.objects.filter(trainer=profile).delete()

    state = secrets.token_urlsafe(32)
    StripeOAuthState.objects.create(state=state, trainer=profile)

    # Standard Stripe Connect authorize URL. Read-write scope is
    # required to create products, prices and subscriptions on the
    # trainer's behalf.
    params = {
        "response_type":   "code",
        "client_id":       settings.STRIPE_CLIENT_ID,
        "scope":           "read_write",
        "state":           state,
        "redirect_uri":    settings.STRIPE_OAUTH_REDIRECT_URI,
        "stripe_user[email]": request.user.email or "",
        "stripe_user[business_name]": profile.business_name or request.user.username,
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items() if v)
    url = f"https://connect.stripe.com/oauth/authorize?{qs}"
    return redirect(url)


@login_required
def stripe_oauth_callback(request):
    """Stripe redirects back here. Swap code → connected account."""
    if not trainer_required(request):
        return redirect("landing-page")

    error = request.GET.get("error") or request.GET.get("error_description")
    if error:
        messages.error(request, f"Stripe declined: {error}")
        return redirect("trainer-settings-page")

    state = request.GET.get("state", "")
    code = request.GET.get("code", "")
    if not state or not code:
        messages.error(request, "Stripe callback was missing state or code.")
        return redirect("trainer-settings-page")

    profile = request.user.trainer_profile
    record = StripeOAuthState.objects.filter(state=state, trainer=profile).first()
    if record is None:
        messages.error(request, "OAuth state mismatch — please try connecting again.")
        return redirect("trainer-settings-page")
    record.delete()

    stripe = get_stripe()
    try:
        # POST /oauth/token with grant_type=authorization_code
        response = stripe.OAuth.token(
            grant_type="authorization_code",
            code=code,
        )
    except Exception as exc:    # pragma: no cover — surface Stripe errors verbatim
        messages.error(request, f"Stripe token exchange failed: {exc}")
        return redirect("trainer-settings-page")

    connected_account = response.get("stripe_user_id", "")
    if not connected_account:
        messages.error(request, "Stripe didn't return an account id.")
        return redirect("trainer-settings-page")

    profile.stripe_user_id = connected_account
    profile.save(update_fields=["stripe_user_id"])
    messages.success(request, "Stripe connected. You can now take live payments.")
    return redirect("trainer-settings-page")


@login_required
@require_POST
def stripe_oauth_disconnect(request):
    """Clear the trainer's Stripe link (best-effort revoke + DB wipe)."""
    if not trainer_required(request):
        return redirect("landing-page")

    profile = request.user.trainer_profile
    if not profile.stripe_user_id:
        return redirect("trainer-settings-page")

    if is_configured():
        stripe = get_stripe()
        try:
            stripe.OAuth.deauthorize(
                client_id=settings.STRIPE_CLIENT_ID,
                stripe_user_id=profile.stripe_user_id,
            )
        except Exception as exc:
            # Best effort — even if Stripe rejects (e.g. already revoked),
            # we still wipe locally so the trainer isn't stuck.
            print(f"[Stripe] deauthorize warning: {exc}")

    profile.stripe_user_id = ""
    profile.save(update_fields=["stripe_user_id"])
    messages.success(request, "Stripe disconnected.")
    return redirect("trainer-settings-page")
