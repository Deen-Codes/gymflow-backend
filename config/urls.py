from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),

    # Public PT landing pages — Phase 7. Mounted at /p/<slug>/ for now;
    # subdomain routing (jared.gymflow.com) is Phase 7.5.
    path("p/", include("apps.sites.public_urls")),

    # Dashboard pages + landing
    path("", include("apps.users.dashboard_urls")),

    # Mobile API (iOS client)
    path("api/users/", include("apps.users.urls")),
    path("api/workouts/", include("apps.workouts.urls")),

    # Dashboard JSON APIs (drag-drop builders + check-ins + sites)
    path("api/workouts/dashboard/", include("apps.workouts.dashboard_api_urls")),
    path("api/nutrition/dashboard/", include("apps.nutrition.dashboard_api_urls")),
    path("api/progress/dashboard/", include("apps.progress.dashboard_api_urls")),
    path("api/sites/dashboard/", include("apps.sites.dashboard_api_urls")),
]
