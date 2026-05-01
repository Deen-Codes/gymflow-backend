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
from .ai_build_views import solo_ai_build_preview, solo_ai_build_assign
from .mutation_views import mutation_apply, mutation_decline
from .checkin_ai_views import checkin_suggestions
from .ai_diag_views import ai_diag
from .debug_views import solo_debug_set_state, solo_debug_factory_reset
from .coach_code_views import coach_code_redeem
from .push_views import register_apns_token, deregister_apns_token
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

    # Phase A — AI mutation apply / decline endpoints. Hit by the
    # iOS proposal-card buttons. ?type=workout|nutrition picks
    # which mutation table.
    path(
        "solo/ai-pt/mutations/<int:mutation_id>/apply/",
        mutation_apply,
        name="solo-ai-pt-mutation-apply",
    ),
    path(
        "solo/ai-pt/mutations/<int:mutation_id>/decline/",
        mutation_decline,
        name="solo-ai-pt-mutation-decline",
    ),

    # Phase C — CHECKIN-APPLIES (R7-4). After a check-in submission,
    # iOS POSTs here to get AI-generated proposals tied to that
    # submission. Idempotent: same submission_id returns the same
    # proposals on re-call (no extra AI cap burn).
    path(
        "solo/checkin-suggestions/<int:submission_id>/",
        checkin_suggestions,
        name="solo-checkin-suggestions",
    ),

    # R3-1 — AI build programme: preview is one-shot for Free
    # users; assign is Pro-AI gated.
    path("solo/ai-build/preview/", solo_ai_build_preview,  name="solo-ai-build-preview"),
    path("solo/ai-build/assign/",  solo_ai_build_assign,   name="solo-ai-build-assign"),

    # R7-DIAG — temporary no-auth endpoint to diagnose the AI 503.
    # Confirms ANTHROPIC_API_KEY is actually set on the live dyno
    # and lets us fire a 1-token Anthropic ping. Remove once the
    # 503 is fully nailed.
    path("_diag/ai/", ai_diag, name="ai-diag"),

    # Debug — flip subscription tier + reset AI usage state without
    # touching Stripe / IAP. ONLY active when settings.DEBUG=True or
    # ENABLE_DEBUG_RESET=1. Authenticated; users can only mutate
    # their own row. iOS Profile screen's debug panel calls this.
    path("_debug/set-state/", solo_debug_set_state, name="solo-debug-set-state"),

    # RESET-FRESH — true factory restart. Wipes the user's training
    # history end-to-end (assigned plan, completed sessions, food
    # logs, bodyweight, AI caches) and resets SoloProfile onboarding
    # answers + macro targets to defaults. Same DEBUG-only gating.
    path(
        "_debug/factory-reset/",
        solo_debug_factory_reset,
        name="solo-debug-factory-reset",
    ),

    # R3-7 — Coach code redemption (no auth; trainer-side
    # generator endpoints live in dashboard_urls.py).
    path("coach-code/redeem/",     coach_code_redeem,      name="coach-code-redeem"),

    # R3-9 — APNs device-token registration. Stores tokens against
    # User.notification_prefs; the send pipeline reads them from
    # there.
    path("push/register/",   register_apns_token,    name="push-register-apns"),
    path("push/deregister/", deregister_apns_token,  name="push-deregister-apns"),

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
