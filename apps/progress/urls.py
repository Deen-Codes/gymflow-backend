"""Mobile-facing progress endpoints (iOS client)."""
from django.urls import path
from .mobile_views import (
    next_checkin_for_me,
    form_detail_for_me,
    submit_form_for_me,
    hydration_for_me,
)
from .solo_views import (
    solo_progress_sessions,
    solo_progress_weight,
    solo_progress_prs,
    solo_progress_streak,
    solo_progress_photos_list,
    solo_progress_photo_detail,
    solo_progress_photo_create,
    solo_progress_photo_delete,
)

urlpatterns = [
    path("me/next-checkin/", next_checkin_for_me, name="me-next-checkin"),
    path("me/hydration/",    hydration_for_me,    name="me-hydration"),

    # Phase C.1 — iOS check-in form rendering + submission.
    path("forms/<int:form_id>/",        form_detail_for_me, name="me-form-detail"),
    path("forms/<int:form_id>/submit/", submit_form_for_me, name="me-form-submit"),

    # D.2.1 — Solo progress endpoints.
    path("solo/sessions/", solo_progress_sessions, name="solo-progress-sessions"),
    path("solo/weight/",   solo_progress_weight,   name="solo-progress-weight"),
    path("solo/prs/",      solo_progress_prs,      name="solo-progress-prs"),
    path("solo/streak/",   solo_progress_streak,   name="solo-progress-streak"),

    # D.2.2 — progress photos.
    path("solo/photos/",                  solo_progress_photos_list,    name="solo-progress-photos-list"),
    path("solo/photos/<int:photo_id>/",   solo_progress_photo_detail,   name="solo-progress-photo-detail"),
    path("solo/photos/upload/",           solo_progress_photo_create,   name="solo-progress-photo-create"),
    path("solo/photos/<int:photo_id>/delete/", solo_progress_photo_delete, name="solo-progress-photo-delete"),
]
