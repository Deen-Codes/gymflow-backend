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
from .exercise_catalog_views import exercise_catalog_search
from .exercise_edit_views import (
    exercise_edit_view,
    exercise_swap_view,
    workout_day_add_exercise_view,
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

    # T2.7 — ExerciseCatalog search for the iOS picker (custom
    # builder + in-place edit affordance). Pure read; supports
    # ?q=, ?muscle=, ?equipment=, ?level=.
    path("catalog/search/",                        exercise_catalog_search,
         name="exercise-catalog-search"),

    # T2.8 — User-side edit endpoints on an assigned programme.
    # Bypass the AI mutation propose/apply flow; stamp provenance=
    # user_edit + write a RecentEditLog row.
    path("exercise/<int:exercise_id>/swap/",       exercise_swap_view,
         name="exercise-swap"),
    path("exercise/<int:exercise_id>/",            exercise_edit_view,
         name="exercise-edit"),
    path("days/<int:day_id>/exercises/",           workout_day_add_exercise_view,
         name="workout-day-add-exercise"),
]
