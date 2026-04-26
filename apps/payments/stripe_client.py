"""
Single place to initialise Stripe so every view in `apps/payments`
shares the same configured `stripe` module + the same API version.

Importing `stripe` from elsewhere works too — but going through this
helper means we never forget to pin `api_key` or `api_version` on a
per-request basis.
"""
import stripe
from django.conf import settings


def get_stripe():
    """Return the configured stripe SDK module."""
    if settings.STRIPE_SECRET_KEY:
        stripe.api_key = settings.STRIPE_SECRET_KEY
    # Pinning the API version means a Stripe-side dashboard upgrade
    # never silently changes our wire format. Bump deliberately.
    stripe.api_version = "2024-06-20"
    return stripe


def is_configured() -> bool:
    """True if the platform's Stripe creds are present."""
    return bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_CLIENT_ID)
