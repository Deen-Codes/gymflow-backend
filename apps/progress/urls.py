"""Mobile-facing progress endpoints (iOS client)."""
from django.urls import path
from .mobile_views import (
    next_checkin_for_me,
    form_detail_for_me,
    submit_form_for_me,
)

urlpatterns = [
    path("me/next-checkin/", next_checkin_for_me, name="me-next-checkin"),

    # Phase C.1 — iOS check-in form rendering + submission.
    path("forms/<int:form_id>/",        form_detail_for_me, name="me-form-detail"),
    path("forms/<int:form_id>/submit/", submit_form_for_me, name="me-form-submit"),
]
