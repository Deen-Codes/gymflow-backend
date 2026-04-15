from django.urls import path
from django.contrib.auth import views as auth_views
from .dashboard_views import (
    landing_page,
    trainer_login_page,
    trainer_logout_page,
    trainer_dashboard,
    dashboard_create_client,
    dashboard_assign_workout_plan,
    test_email_page,
)

urlpatterns = [
    path("", landing_page, name="landing-page"),
    path("portal/login/", trainer_login_page, name="trainer-login-page"),
    path("portal/logout/", trainer_logout_page, name="trainer-logout-page"),
    path("dashboard/", trainer_dashboard, name="trainer-dashboard"),
    path("dashboard/create-client/", dashboard_create_client, name="dashboard-create-client"),
    path("dashboard/assign-workout-plan/", dashboard_assign_workout_plan, name="dashboard-assign-workout-plan"),
    path("portal/test-email/", test_email_page, name="test-email-page"),

    path(
        "portal/password-reset/",
        auth_views.PasswordResetView.as_view(
            template_name="auth/password_reset.html",
            email_template_name="auth/password_reset_email.txt",
            subject_template_name="auth/password_reset_subject.txt",
            success_url="/portal/password-reset/done/",
        ),
        name="password_reset",
    ),
    path(
        "portal/password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="auth/password_reset_done.html",
        ),
        name="password_reset_done",
    ),
    path(
        "portal/reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="auth/password_reset_confirm.html",
            success_url="/portal/reset/done/",
        ),
        name="password_reset_confirm",
    ),
    path(
        "portal/reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="auth/password_reset_complete.html",
        ),
        name="password_reset_complete",
    ),
]
