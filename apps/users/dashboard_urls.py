from django.urls import path
from .dashboard_views import (
    landing_page,
    trainer_login_page,
    trainer_logout_page,
    trainer_dashboard,
    dashboard_create_client,
    dashboard_assign_workout_plan,
)

urlpatterns = [
    path("", landing_page, name="landing-page"),
    path("portal/login/", trainer_login_page, name="trainer-login-page"),
    path("portal/logout/", trainer_logout_page, name="trainer-logout-page"),
    path("dashboard/", trainer_dashboard, name="trainer-dashboard"),
    path("dashboard/create-client/", dashboard_create_client, name="dashboard-create-client"),
    path("dashboard/assign-workout-plan/", dashboard_assign_workout_plan, name="dashboard-assign-workout-plan"),
]
