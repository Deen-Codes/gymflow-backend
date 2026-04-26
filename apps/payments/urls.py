"""URL routes for Stripe Connect (Phase 7.7.1+)."""
from django.urls import path
from .views import (
    stripe_oauth_connect,
    stripe_oauth_callback,
    stripe_oauth_disconnect,
)
from .webhooks import stripe_webhook
from .subscription_actions import (
    cancel_at_period_end,
    resume,
    cancel_immediately,
)

urlpatterns = [
    path("oauth/connect/",    stripe_oauth_connect,    name="stripe-oauth-connect"),
    path("oauth/callback/",   stripe_oauth_callback,   name="stripe-oauth-callback"),
    path("oauth/disconnect/", stripe_oauth_disconnect, name="stripe-oauth-disconnect"),

    # Inbound webhooks from Stripe — register the URL in your Stripe
    # dashboard → Developers → Webhooks. Set STRIPE_WEBHOOK_SECRET on
    # Render once configured.
    path("webhooks/stripe/", stripe_webhook, name="stripe-webhook"),

    # Phase 7.7.3 — per-subscription actions from the trainer dashboard.
    path("subscription/<int:sub_id>/cancel/",     cancel_at_period_end, name="subscription-cancel"),
    path("subscription/<int:sub_id>/resume/",     resume,                name="subscription-resume"),
    path("subscription/<int:sub_id>/cancel-now/", cancel_immediately,    name="subscription-cancel-now"),
]
