"""Mobile-facing nutrition endpoints (iOS client)."""
from django.urls import path
from .mobile_views import nutrition_today_for_me

urlpatterns = [
    path("me/today/", nutrition_today_for_me, name="me-nutrition-today"),
]
