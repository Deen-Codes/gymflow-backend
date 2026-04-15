from django.urls import path
from .views import (
    active_workout_plan,
    workout_day_detail,
    next_workout,
    latest_workout_session_for_day,
    create_workout_session,
)

urlpatterns = [
    path("plan/active/", active_workout_plan, name="active_workout_plan"),
    path("days/<int:day_id>/", workout_day_detail, name="workout_day_detail"),
    path("next/", next_workout, name="next_workout"),
    path("days/<int:day_id>/latest-session/", latest_workout_session_for_day, name="latest_workout_session_for_day"),
    path("sessions/create/", create_workout_session, name="create_workout_session"),
]
