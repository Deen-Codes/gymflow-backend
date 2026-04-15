from django.urls import path
from .views import (
    login_view,
    logout_view,
    me_view,
    create_client_view,
    trainer_clients_view,
    assign_workout_plan_view,
)

urlpatterns = [
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("me/", me_view, name="me"),
    path("clients/create/", create_client_view, name="create-client"),
    path("clients/", trainer_clients_view, name="trainer-clients"),
    path("clients/assign-workout-plan/", assign_workout_plan_view, name="assign-workout-plan"),
]
