from django.urls import path
from .views import (
    active_workout_plan,
    workout_day_detail,
    next_workout,
    latest_workout_session_for_day,
    create_workout_session,
    update_workout_session_notes,
)
from .solo_catalog_views import (
    solo_programmes_list,
    solo_programmes_assign,
    solo_programmes_create_custom,
)

urlpatterns = [
    path("plan/active/", active_workout_plan, name="active_workout_plan"),
    path("days/<int:day_id>/", workout_day_detail, name="workout_day_detail"),
    path("next/", next_workout, name="next_workout"),
    path("days/<int:day_id>/latest-session/", latest_workout_session_for_day, name="latest_workout_session_for_day"),
    path("sessions/create/", create_workout_session, name="create_workout_session"),
    path(
        "sessions/<int:session_id>/notes/",
        update_workout_session_notes,
        name="update_workout_session_notes",
    ),

    # SOLO-02 — public programmes catalog. List is open to any
    # authenticated user (PT or solo); assign is solo-only.
    path("solo/programmes/",                       solo_programmes_list,   name="solo-programmes-list"),
    path("solo/programmes/<int:programme_id>/assign/", solo_programmes_assign, name="solo-programmes-assign"),
    path("solo/programmes/custom/",                solo_programmes_create_custom, name="solo-programmes-create"),
]
