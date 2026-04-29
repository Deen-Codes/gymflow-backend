from django.urls import path
from .views import (
    login_view,
    logout_view,
    me_view,
    startup_for_me,
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
from .solo_views import (
    solo_signup_view,
    solo_onboarding_update_view,
    solo_me_view,
    solo_convert_view,
)
from .ai_pt_views import solo_ai_pt_chat
from .iap_views import solo_iap_verify, solo_iap_webhook
from .profile_views import (
    lifetime_stats_for_me,
    avatar_for_me,
    username_check_view,
    change_username_view,
    notification_prefs_for_me,
    delete_account_view,
)

urlpatterns = [
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("me/", me_view, name="me"),
    path("me/startup/", startup_for_me, name="me-startup"),
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

    # SOLO MVP (task #53 — E.1). Self-serve signup + entitlement
    # endpoint that gates Pro/Pro AI features on iOS.
    path("solo/signup/",     solo_signup_view,             name="solo-signup"),
    path("solo/onboarding/", solo_onboarding_update_view,  name="solo-onboarding"),
    path("solo/me/",         solo_me_view,                 name="solo-me"),
    path("solo/convert/",    solo_convert_view,            name="solo-convert"),
    # E.2 — AI PT chat (Pro AI gated)
    path("solo/ai-pt/chat/", solo_ai_pt_chat,              name="solo-ai-pt-chat"),

    # SOLO-03 — Apple IAP receipt validation + webhook for renewals.
    path("solo/iap/verify/",  solo_iap_verify,             name="solo-iap-verify"),
    path("solo/iap/webhook/", solo_iap_webhook,            name="solo-iap-webhook"),

    # Profile P.1.1 — wires up the SOON pills on the iOS Profile
    # tab (lifetime stats, avatar upload, username change,
    # notification prefs sync, account deletion).
    path("me/lifetime-stats/",      lifetime_stats_for_me,    name="me-lifetime-stats"),
    path("me/avatar/",              avatar_for_me,            name="me-avatar"),
    path("me/username/",            change_username_view,     name="me-username"),
    path("username/check/",         username_check_view,      name="username-check"),
    path("me/notification-prefs/",  notification_prefs_for_me, name="me-notification-prefs"),
    path("me/delete/",              delete_account_view,      name="me-delete"),
    path("clients/create/", create_client_view, name="create-client"),
    path("clients/", trainer_clients_view, name="trainer-clients"),
    path("clients/assign-workout-plan/", assign_workout_plan_view, name="assign-workout-plan"),
]
