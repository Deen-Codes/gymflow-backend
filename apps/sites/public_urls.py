"""URL routing for the public PT landing pages.

Mounted at /p/<slug>/ in the project urls so we keep a clean
namespace until subdomain routing lands in Phase 7.5."""
from django.urls import path

from . import views as v
from apps.payments.checkout_views import (
    start_subscribe_checkout,
    subscribe_thanks,
)

urlpatterns = [
    path("<slug:slug>/",         v.public_site_page,   name="public-site-page"),
    path("<slug:slug>/signup/",  v.public_site_signup, name="public-site-signup"),

    # Phase 7.7.1 — Stripe Checkout subscribe flow.
    # `thanks` route declared first so it isn't shadowed by the
    # `<int:plan_id>` pattern below.
    path("<slug:slug>/subscribe/thanks/",
         subscribe_thanks,
         name="public-subscribe-thanks"),
    path("<slug:slug>/subscribe/<int:plan_id>/",
         start_subscribe_checkout,
         name="public-subscribe-start"),
]
