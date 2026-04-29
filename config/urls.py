from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

from apps.users.views import magic_link_web_handler

urlpatterns = [
    path("admin/", admin.site.urls),

    # Magic-link web handler — the email's lime CTA points at
    # gymflow://magic/<token> for direct app launch, but several
    # email clients (Gmail in particular) rewrite custom schemes
    # to https:// for safety. This route catches those rewrites,
    # detects iOS, and redirects to the deep link so the app
    # opens. Non-iOS visitors get a friendly "open this on your
    # phone" page with App Store guidance.
    path("magic/<str:token>/", magic_link_web_handler, name="magic-link-web"),
    path("magic/<str:token>",  magic_link_web_handler),  # trailing-slash optional

    # Phase #21 — legal pages. Static templates, no DB. Linked from
    # public-site footer + Stripe Customer Portal "Public business
    # information" so they're discoverable from anywhere a user pays.
    path("legal/privacy/",
         TemplateView.as_view(template_name="legal/privacy.html"),
         name="legal-privacy"),
    path("legal/terms/",
         TemplateView.as_view(template_name="legal/terms.html"),
         name="legal-terms"),

    # Public PT landing pages — Phase 7. Mounted at /p/<slug>/ for now;
    # subdomain routing (jared.gymflow.com) is Phase 7.5.
    path("p/", include("apps.sites.public_urls")),

    # Dashboard pages + landing
    path("", include("apps.users.dashboard_urls")),

    # Mobile API (iOS client)
    path("api/users/", include("apps.users.urls")),
    path("api/workouts/", include("apps.workouts.urls")),
    path("api/progress/", include("apps.progress.urls")),
    path("api/nutrition/", include("apps.nutrition.urls")),
    path("api/trophies/", include("apps.trophies.urls")),

    # Dashboard JSON APIs (drag-drop builders + check-ins + sites)
    path("api/workouts/dashboard/", include("apps.workouts.dashboard_api_urls")),
    path("api/nutrition/dashboard/", include("apps.nutrition.dashboard_api_urls")),
    path("api/progress/dashboard/", include("apps.progress.dashboard_api_urls")),
    path("api/sites/dashboard/", include("apps.sites.dashboard_api_urls")),

    # Phase 7.7.1 — Stripe Connect (OAuth + Checkout + webhooks)
    path("payments/", include("apps.payments.urls")),
]
