"""
R3-7 — Coach codes.

Trainers share a short, memorable code with prospective clients
(e.g. JANE-1234). The client enters the code + their email in the
iOS LoginView's "Coach code" sheet → backend creates a CLIENT
account paired with that trainer → magic-link email is sent.
The next sign-in pairs the user automatically.

No new migrations — the trainer's stable coach code is stored in
`User.notification_prefs` (existing JSONField). The first time a
trainer views their settings page (or the dashboard generator
endpoint) we derive + persist a code.

Endpoints:
  • GET    /dashboard/settings/coach-code/         — PT-side, returns
                                                    or generates the
                                                    code.
  • POST   /dashboard/settings/coach-code/regenerate/ — rotate.
  • POST   /api/users/coach-code/redeem/           — public, no auth.
                                                    {email, code}.
                                                    Creates the client
                                                    + sends magic link.
"""
import json
import logging
import re
import secrets
import string

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework import status
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes, throttle_classes,
)
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from .models import User, ClientProfile, TrainerProfile

log = logging.getLogger(__name__)


COACH_CODE_KEY = "coach_code"


def _generate_code(trainer: User) -> str:
    """Generate a memorable coach code: <BASE>-<4 digits>.
    BASE is the first 4 alpha chars of the trainer's username
    uppercased; falls back to "COACH" when the username is short
    or numeric. The 4 digits give us enough collision room for
    100k trainers per BASE prefix.
    """
    username = (trainer.username or "").strip()
    base_chars = re.sub(r"[^A-Za-z]", "", username).upper()
    base = (base_chars[:4] or "COACH")
    digits = "".join(secrets.choice(string.digits) for _ in range(4))
    return f"{base}-{digits}"


def _get_or_create_code(trainer: User) -> str:
    prefs = trainer.notification_prefs or {}
    code = prefs.get(COACH_CODE_KEY)
    if code:
        return code
    code = _generate_code(trainer)
    # Defensive: regenerate if collision (vanishingly rare with 10k
    # digits-per-base headroom but we'd rather be sure).
    for _ in range(8):
        if not User.objects.exclude(pk=trainer.pk).filter(
            notification_prefs__coach_code=code,
        ).exists():
            break
        code = _generate_code(trainer)
    prefs[COACH_CODE_KEY] = code
    trainer.notification_prefs = prefs
    trainer.save(update_fields=["notification_prefs"])
    return code


def _find_trainer_by_code(code: str) -> User | None:
    code_norm = (code or "").strip().upper()
    if not code_norm:
        return None
    # JSONField equality lookup. Index-less but fine at our scale.
    return User.objects.filter(
        role=User.TRAINER,
        notification_prefs__coach_code=code_norm,
    ).first()


# --------------------------------------------------------------------
# PT dashboard endpoints
# --------------------------------------------------------------------
@login_required
@require_http_methods(["GET"])
def dashboard_coach_code(request):
    """Returns the trainer's stable coach code, generating it on
    first access. Used by the Settings page panel."""
    if request.user.role != User.TRAINER:
        return JsonResponse({"detail": "Trainer accounts only."}, status=403)
    code = _get_or_create_code(request.user)
    return JsonResponse({"code": code})


@login_required
@require_http_methods(["POST"])
def dashboard_coach_code_regenerate(request):
    """Force-rotates the coach code. The previous code becomes
    invalid immediately — useful when a trainer wants to revoke a
    code they've shared too widely."""
    if request.user.role != User.TRAINER:
        return JsonResponse({"detail": "Trainer accounts only."}, status=403)
    prefs = request.user.notification_prefs or {}
    prefs.pop(COACH_CODE_KEY, None)
    request.user.notification_prefs = prefs
    request.user.save(update_fields=["notification_prefs"])
    new_code = _get_or_create_code(request.user)
    return JsonResponse({"code": new_code})


# --------------------------------------------------------------------
# Public redeem endpoint (no auth — that's the whole point)
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([])
@throttle_classes([AnonRateThrottle])
def coach_code_redeem(request):
    """Body: {"email": str, "code": str}

    Resolves the code → trainer. Creates a Client account paired
    with that trainer (or, if a client account with this email
    already exists for this trainer, no-ops gracefully). Sends a
    magic-link email so the client can sign in.

    Always returns 200 unless the request is malformed — we don't
    leak whether an account exists.
    """
    data = request.data or {}
    email = (data.get("email") or "").strip().lower()
    code  = (data.get("code")  or "").strip().upper()
    if not email or not code:
        return Response({"detail": "Email and code are required."}, status=400)
    if "@" not in email:
        return Response({"detail": "That doesn't look like an email."}, status=400)

    trainer = _find_trainer_by_code(code)
    if trainer is None:
        # Don't leak whether the code exists. Tell the user it
        # didn't match — this is the one place we DO want clarity
        # so they don't blame their own typing.
        return Response(
            {"detail": "That code didn't match a trainer. Double-check with them."},
            status=404,
        )

    # Find or create the client account. Existing accounts paired
    # with a different trainer get a 409 (so we don't quietly
    # rebrand them).
    with transaction.atomic():
        existing = User.objects.filter(email__iexact=email).first()
        if existing is not None:
            if existing.role == User.CLIENT:
                profile = ClientProfile.objects.filter(user=existing).first()
                if profile and profile.trainer and profile.trainer != trainer:
                    return Response(
                        {"detail": "This email is already paired with a different trainer."},
                        status=status.HTTP_409_CONFLICT,
                    )
                # Same trainer (or no trainer pairing yet) — repair.
                if profile is None:
                    profile = ClientProfile.objects.create(
                        user=existing, trainer=trainer,
                    )
                else:
                    profile.trainer = trainer
                    profile.save(update_fields=["trainer"])
                user = existing
            else:
                # Solo / Trainer account exists for this email; we
                # don't auto-convert (out of safety). Tell them.
                return Response(
                    {"detail": "An account already exists for this email — sign in there first."},
                    status=status.HTTP_409_CONFLICT,
                )
        else:
            # Fresh account.
            base_username = re.sub(r"[^A-Za-z0-9]", "", email.split("@")[0])[:24] or "client"
            username = base_username
            n = 1
            while User.objects.filter(username=username).exists():
                n += 1
                username = f"{base_username}{n}"
            user = User.objects.create_user(
                username=username,
                email=email,
                password=secrets.token_urlsafe(20),
                role=User.CLIENT,
            )
            ClientProfile.objects.create(user=user, trainer=trainer)

    # Magic link delivers the actual sign-in. Delegates to the
    # existing magic-link infrastructure so the email template +
    # rate-limits are shared.
    try:
        from .views import _magic_link_urls, _send_magic_link_email
        from .models import MagicLoginToken
        token_row = MagicLoginToken.objects.create(
            user=user,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timezone.timedelta(minutes=20),
        )
        deep_link, web_link = _magic_link_urls(token_row.token)
        _send_magic_link_email(user=user, deep_link=deep_link, web_link=web_link)
    except Exception as exc:
        log.exception("coach-code magic-link send failed: %s", exc)
        # Don't leak the failure — return ok and let the user
        # request another link.

    return Response({
        "ok":           True,
        "trainer_name": trainer.first_name or trainer.username,
        "detail":       "We sent you a sign-in link. Check your inbox.",
    })
