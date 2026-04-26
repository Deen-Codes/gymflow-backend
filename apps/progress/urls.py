"""Mobile-facing progress endpoints (iOS client)."""
from django.urls import path
from .mobile_views import next_checkin_for_me

urlpatterns = [
    path("me/next-checkin/", next_checkin_for_me, name="me-next-checkin"),
]
