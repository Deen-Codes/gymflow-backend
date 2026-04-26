"""URL routes for Stripe Connect (Phase 7.7.1)."""
from django.urls import path
from .views import (
    stripe_oauth_connect,
    stripe_oauth_callback,
    stripe_oauth_disconnect,
)

urlpatterns = [
    path("oauth/connect/",    stripe_oauth_connect,    name="stripe-oauth-connect"),
    path("oauth/callback/",   stripe_oauth_callback,   name="stripe-oauth-callback"),
    path("oauth/disconnect/", stripe_oauth_disconnect, name="stripe-oauth-disconnect"),
]
