"""
Phase 7.7.4 — Stripe Customer Portal "magic link" flow.

The Customer Portal is a Stripe-hosted page where the *client* (not
the trainer) can update their card, view invoices, and cancel their
subscription. We don't embed it in the iOS app (Apple's reviewers
historically scrutinise SFSafariViewController flows that look like
they bypass IAP). Instead we email the link.

Three entry points, one shared backbone:

  1. Trainer-initiated  — POST  /payments/subscription/<id>/email-portal/
                          The trainer hits "Email portal link to client"
                          on the subscription panel; we email the URL
                          to the customer of that subscription.

  2. iOS-initiated      — POST  /payments/portal/email-me/  (auth required)
                          The iOS app's "Manage subscription" Settings
                          row asks the backend to email the client's
                          own portal link to their own address.

  3. Public-initiated   — POST  /p/<slug>/manage/  (no auth)
                          A client who isn't on iOS yet enters their
                          email on a public form. We look up their sub
                          + email them the link. Returns the same
                          "if we found a match we sent it" response
                          regardless to avoid email enumeration.

In all three cases, what we actually email is a Stripe `billing_portal.
Session.url`. Stripe makes that URL single-use and 24-hour-expiring;
we don't have to add our own token layer on top.
"""
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.dashboard_helpers import trainer_required
from apps.users.models import User

from .models import ClientSubscription
from .stripe_client import get_stripe, is_configured


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def _create_portal_session(sub: ClientSubscription, return_url: str) -> str | None:
    """
    Ask Stripe for a hosted billing-portal URL for this subscription.

    Returns None if Stripe isn't configured, the trainer isn't connected,
    or the subscription doesn't have a customer ID. Caller is expected
    to flash a friendly error in that case.
    """
    if not (is_configured() and sub.trainer.stripe_user_id and sub.stripe_customer_id):
        return None

    stripe = get_stripe()
    try:
        portal = stripe.billing_portal.Session.create(
            customer=sub.stripe_customer_id,
            return_url=return_url,
            stripe_account=sub.trainer.stripe_user_id,
        )
        return portal.url
    except Exception as exc:        # noqa: BLE001
        # Surfaced to caller; we don't want a crash to leak.
        print(f"[portal] Stripe portal session create failed: {exc}")
        return None


def _email_portal_link(sub: ClientSubscription, portal_url: str) -> bool:
    """Send the portal URL to the client's email. Returns True on success."""
    if not sub.client.email:
        return False

    trainer_name = (
        sub.trainer.business_name
        or sub.trainer.user.get_full_name()
        or sub.trainer.user.username
    )
    first_name = sub.client.first_name or sub.client.username

    context = {
        "first_name": first_name,
        "trainer_name": trainer_name,
        "portal_url": portal_url,
    }
    text_body = render_to_string("payments/portal_email.txt", context)
    html_body = render_to_string("payments/portal_email.html", context)

    try:
        send_mail(
            subject=f"Manage your {trainer_name} subscription",
            message=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[sub.client.email],
            html_message=html_body,
            fail_silently=False,
        )
        return True
    except Exception as exc:        # noqa: BLE001
        print(f"[portal] email send failed: {exc}")
        return False


def _public_site_url(request, sub: ClientSubscription) -> str:
    """Where Stripe sends the client back to once they finish in the portal."""
    slug = sub.trainer.slug
    return request.build_absolute_uri(reverse("public-site-page", args=[slug])) if slug else \
           request.build_absolute_uri("/")


# -------------------------------------------------------------------
# 1. Trainer-initiated — from the subscription panel on client detail
# -------------------------------------------------------------------
@login_required
@require_POST
def email_portal_link_to_client(request, sub_id):
    """Trainer hits "Email portal link" → we email the URL to the client."""
    if not trainer_required(request):
        return redirect("landing-page")

    sub = get_object_or_404(
        ClientSubscription.objects.select_related("client", "trainer", "trainer__user"),
        id=sub_id,
        trainer=request.user.trainer_profile,
    )

    if not sub.client.email:
        messages.error(
            request,
            f"No email on file for {sub.client.username} — can't send the portal link.",
        )
        return redirect("trainer-client-detail", client_id=sub.client_id)

    return_url = _public_site_url(request, sub)
    portal_url = _create_portal_session(sub, return_url)
    if not portal_url:
        messages.error(
            request,
            "Couldn't create the portal session — check that Stripe is "
            "connected and this subscription has a Stripe customer ID.",
        )
        return redirect("trainer-client-detail", client_id=sub.client_id)

    if _email_portal_link(sub, portal_url):
        messages.success(
            request,
            f"Portal link emailed to {sub.client.email}. "
            f"It's single-use and expires in 24 hours.",
        )
    else:
        messages.error(
            request,
            "Stripe gave us a portal URL but the email failed to send. "
            "Check Resend logs / RESEND_API_KEY env var.",
        )
    return redirect("trainer-client-detail", client_id=sub.client_id)


# -------------------------------------------------------------------
# 2. iOS-initiated — DRF endpoint, token-auth gated
# -------------------------------------------------------------------
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def request_portal_link_for_me(request):
    """
    Authenticated client asks: "email me my own portal link."
    Used by the iOS Settings → Manage subscription row.
    """
    user = request.user
    if not user.email:
        return Response(
            {"detail": "Your account has no email — ask your trainer to set one."},
            status=400,
        )

    sub = (
        ClientSubscription.objects
        .select_related("client", "trainer", "trainer__user")
        .filter(client=user)
        .exclude(status=ClientSubscription.STATUS_CANCELED)
        .order_by("-created_at")
        .first()
    )
    if sub is None:
        return Response(
            {"detail": "You don't have an active subscription."},
            status=404,
        )

    return_url = _public_site_url(request, sub)
    portal_url = _create_portal_session(sub, return_url)
    if not portal_url:
        return Response(
            {"detail": "Stripe is unavailable right now. Try again in a minute."},
            status=503,
        )

    if not _email_portal_link(sub, portal_url):
        return Response(
            {"detail": "We couldn't send the email. Try again or contact your trainer."},
            status=500,
        )

    return Response({"detail": f"Portal link sent to {user.email}. Check your inbox."})


# -------------------------------------------------------------------
# 3. Public-initiated — anonymous client enters email on public form
# -------------------------------------------------------------------
@require_POST
def request_portal_link_public(request, slug):
    """
    Public form on /p/<slug>/manage/ — client enters their email,
    we look up a ClientSubscription matching that email under this trainer
    and email them the portal URL.

    SECURITY: returns the same success message regardless of whether
    we found a match, so attackers can't enumerate which emails have
    active subscriptions with this trainer.
    """
    from apps.users.models import TrainerProfile

    email = (request.POST.get("email") or "").strip().lower()
    if not email:
        messages.error(request, "Enter the email you used at signup.")
        return redirect("public-manage-subscription", slug=slug)

    trainer = TrainerProfile.objects.filter(slug=slug).first()
    if trainer is None:
        # Slug is bogus → bounce to root, not worth a special error
        return redirect("landing-page")

    sub = (
        ClientSubscription.objects
        .select_related("client", "trainer", "trainer__user")
        .filter(client__email__iexact=email, trainer=trainer)
        .exclude(status=ClientSubscription.STATUS_CANCELED)
        .order_by("-created_at")
        .first()
    )

    # Even if sub is None we show a success-style page so we don't
    # leak which emails are subscribed.
    if sub is not None:
        return_url = _public_site_url(request, sub)
        portal_url = _create_portal_session(sub, return_url)
        if portal_url:
            _email_portal_link(sub, portal_url)

    messages.success(
        request,
        f"If {email} matches an active subscription, we've sent a portal link there. "
        f"It expires in 24 hours.",
    )
    return redirect("public-manage-subscription", slug=slug)
