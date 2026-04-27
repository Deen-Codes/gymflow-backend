"""Mobile-facing nutrition endpoints (iOS client)."""
from django.urls import path
from .mobile_views import (
    nutrition_today_for_me,
    consumption_for_me,
)

urlpatterns = [
    path("me/today/",        nutrition_today_for_me, name="me-nutrition-today"),

    # Phase C.2 — server-of-record meal consumption.
    # Single URL handles GET (list ticks for date) / POST (tick) /
    # DELETE (untick). Method dispatch lives in the view itself.
    path("me/consumption/",  consumption_for_me,     name="me-consumption"),
]
