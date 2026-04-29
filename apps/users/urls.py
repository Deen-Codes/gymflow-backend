from django.urls import path
from .views import (
    login_view,
    logout_view,
    me_view,
    home_stats_for_me,
    required_actions_for_me,
    profile_update_for_me,
    create_client_view,
    trainer_clients_view,
    assign_workout_plan_view,
    magic_link_request_view,
    magic_link_verify_view,
)
from .sso_views import sso_apple_view, sso_google_view

urlpatterns = [
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("me/", me_view, name="me"),
    path("me/home-stats/", home_stats_for_me, name="me-home-stats"),
    path("me/required-actions/", required_actions_for_me, name="me-required-actions"),
    path("me/profile-update/", profile_update_for_me, name="me-profile-update"),
    # Magic-link sign-in (task #25). Both endpoints are unauthenticated
    # by design — they're how a logged-out user gets logged in.
    path("magic-link/request/", magic_link_request_view, name="magic-link-request"),
    path("magic-link/verify/", magic_link_verify_view, name="magic-link-verify"),

    # SSO sign-in (task #44). iOS exchanges Apple/Google identity
    # tokens for a DRF auth token + user payload via these.
    path("sso/apple/",  sso_apple_view,  name="sso-apple"),
    path("sso/google/", sso_google_view, name="sso-google"),
    path("clients/create/", create_client_view, name="create-client"),
    path("clients/", trainer_clients_view, name="trainer-clients"),
    path("clients/assign-workout-plan/", assign_workout_plan_view, name="assign-workout-plan"),
]
