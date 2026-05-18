"""SSO sign-in endpoints — Apple + Google.

Both providers issue a signed ID token (JWT, RS256) the iOS client
ships up to us. We verify the signature against the provider's JWKS,
extract the stable `sub` claim, and either log in the matched user
or create a new one. The response shape is identical to a password
login so iOS can drop it into `currentUser` without translation.

Account-linking precedence:
    1. exact match on apple_sub / google_sub (user has signed in
       with this provider before — return them)
    2. fall back to email match (user has the same email but
       hasn't yet linked this provider — link it onto the
       existing row, no duplicate account)
    3. brand-new user — create a row with a generated unique
       username, role=client (clients are the SSO target — trainers
       provision via dashboard).
"""

import json
import logging
import os
import secrets
from datetime import datetime, timezone

import jwt
import requests
from django.conf import settings
from django.contrib.auth import login
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import User
from .serializers import UserMeSerializer

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Apple Sign In
#
# JWKS:        https://appleid.apple.com/auth/keys
# Issuer:      https://appleid.apple.com
# Audience:    bundle ID of the iOS app (our `com.afletics.app`)
# Algorithm:   RS256
#
# Apple sends the user's full name only on the FIRST sign-in. On
# subsequent sign-ins the name is omitted from both the credential
# and the ID token, so iOS includes any first-time name as a
# separate `full_name` field on the request body.
#
# AFLETICS-RENAME (May 2026, Deen QC) — bundle ID flipped from
# coach.afletics.com → com.afletics.app. Apple's ID token `aud` claim
# now carries the new bundle, so verify against the new value.
# Override via APPLE_AUDIENCE env var on Render if needed.
# ---------------------------------------------------------------------

APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"
APPLE_AUDIENCE = os.environ.get("APPLE_AUDIENCE", "com.afletics.app")

# JWKS cache — Apple/Google rotate keys but slowly. We cache the
# fetched key set in-process for 6 hours; on cache miss we re-fetch.
# Worst case after a key rotation: one failed verify, then a fresh
# JWKS pull recovers.
_jwks_cache: dict[str, tuple[dict, datetime]] = {}
_JWKS_TTL_SECONDS = 6 * 60 * 60


def _fetch_jwks(url: str) -> dict:
    """Return the JWKS dict for `url`, using the in-process cache."""
    cached = _jwks_cache.get(url)
    if cached is not None:
        keys, fetched_at = cached
        if (datetime.now(timezone.utc) - fetched_at).total_seconds() < _JWKS_TTL_SECONDS:
            return keys
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    keys = resp.json()
    _jwks_cache[url] = (keys, datetime.now(timezone.utc))
    return keys


def _public_key_for_kid(jwks: dict, kid: str):
    """Pull the matching key from the JWKS and convert it to a
    cryptography public key object usable by `jwt.decode`."""
    for key_dict in jwks.get("keys", []):
        if key_dict.get("kid") == kid:
            return jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_dict))
    raise ValueError(f"No matching key for kid={kid}")


def _verify_apple_id_token(id_token: str) -> dict:
    """Verify an Apple identity token against Apple's JWKS. Returns
    the decoded claims dict on success, raises `ValueError` on any
    failure (signature, issuer, audience, expiry — all checked)."""
    unverified = jwt.get_unverified_header(id_token)
    kid = unverified.get("kid")
    if not kid:
        raise ValueError("missing kid header")
    public_key = _public_key_for_kid(_fetch_jwks(APPLE_JWKS_URL), kid)
    return jwt.decode(
        id_token,
        public_key,
        algorithms=["RS256"],
        audience=APPLE_AUDIENCE,
        issuer=APPLE_ISSUER,
    )


# ---------------------------------------------------------------------
# Google Sign In
#
# JWKS:       https://www.googleapis.com/oauth2/v3/certs
# Issuer:     https://accounts.google.com  (or just "accounts.google.com")
# Audience:   the iOS OAuth client_id from Google Cloud Console.
# Algorithm:  RS256
# ---------------------------------------------------------------------

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}


def _verify_google_id_token(id_token: str) -> dict:
    """Verify a Google ID token against Google's JWKS. Audience is
    pulled from settings (`GOOGLE_OAUTH_IOS_CLIENT_ID`) so the
    same backend works for both iOS + a future web client."""
    audience = getattr(settings, "GOOGLE_OAUTH_IOS_CLIENT_ID", None)
    if not audience:
        raise ValueError("GOOGLE_OAUTH_IOS_CLIENT_ID not configured")
    unverified = jwt.get_unverified_header(id_token)
    kid = unverified.get("kid")
    if not kid:
        raise ValueError("missing kid header")
    public_key = _public_key_for_kid(_fetch_jwks(GOOGLE_JWKS_URL), kid)
    claims = jwt.decode(
        id_token,
        public_key,
        algorithms=["RS256"],
        audience=audience,
        # PyJWT only accepts a single string for `issuer`, so we
        # pre-validate against the set ourselves.
    )
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise ValueError(f"unexpected iss: {claims.get('iss')}")
    return claims


# ---------------------------------------------------------------------
# Shared create-or-link helper
# ---------------------------------------------------------------------


def _generate_unique_username(base: str) -> str:
    """Generate a username unique against the User table. Falls
    back to `client_<random>` when the base is empty (Apple often
    doesn't share email)."""
    base = (base or "").split("@")[0].strip().lower() or f"client_{secrets.token_hex(4)}"
    candidate = base
    suffix = 0
    while User.objects.filter(username=candidate).exists():
        suffix += 1
        candidate = f"{base}_{suffix}"
    return candidate


@transaction.atomic
def _resolve_or_create_user(*, sub_field: str, sub: str, email: str | None, full_name: str | None):
    """Find or create a User for the given provider sub.

    `sub_field` is "apple_sub" or "google_sub" — the column we
    match on first. `email` may be None (Apple Hide-My-Email users
    sometimes have an empty email after first login).
    """
    # Pass 1: matched by sub on this column — already linked.
    user = User.objects.filter(**{sub_field: sub}).first()
    if user is not None:
        return user

    # Pass 2: matched by email — link this provider onto the
    # existing account.
    if email:
        user = User.objects.filter(email__iexact=email).first()
        if user is not None:
            setattr(user, sub_field, sub)
            user.save(update_fields=[sub_field])
            return user

    # Pass 3: brand-new account.
    username_base = (email or "").split("@")[0] if email else ""
    username = _generate_unique_username(username_base)
    new_user = User.objects.create(
        username=username,
        email=(email or ""),
        role=User.CLIENT,
        # Random unusable password — SSO is the only way in. Keeps
        # the `set_unusable_password` semantics so legacy password
        # paths can't be brute-forced against an empty hash.
    )
    new_user.set_unusable_password()
    setattr(new_user, sub_field, sub)
    if full_name:
        from .profile_schema import _split_full_name
        first, last = _split_full_name(full_name)
        new_user.first_name = first
        new_user.last_name = last
    new_user.save()
    return new_user


def _login_response(request, user) -> Response:
    """Common success payload — same shape as `login_view`."""
    login(request, user)
    token, _ = Token.objects.get_or_create(user=user)
    return Response(
        {
            "message": "SSO sign-in successful.",
            "token": token.key,
            "user": UserMeSerializer(user).data,
        },
        status=status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def sso_apple_view(request):
    """POST { identity_token, full_name? } → session.

    `full_name` only arrives on the user's first ever Apple sign-in
    (Apple stops including the name in subsequent ID tokens). iOS
    pulls it off the credential's `fullName` property and forwards
    it on first auth so we can capture display_name; subsequent
    sign-ins omit it.
    """
    identity_token = (request.data.get("identity_token") or "").strip()
    if not identity_token:
        return Response({"detail": "Missing identity_token."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        claims = _verify_apple_id_token(identity_token)
    except Exception as e:
        log.warning("Apple SSO token rejected: %s", e)
        return Response({"detail": "Invalid Apple sign-in."}, status=status.HTTP_401_UNAUTHORIZED)

    sub = claims.get("sub")
    email = claims.get("email")
    if not sub:
        return Response({"detail": "Token missing sub."}, status=status.HTTP_401_UNAUTHORIZED)

    full_name = (request.data.get("full_name") or "").strip() or None
    user = _resolve_or_create_user(
        sub_field="apple_sub",
        sub=sub,
        email=email,
        full_name=full_name,
    )
    return _login_response(request, user)


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def sso_google_view(request):
    """POST { id_token } → session.

    Google's ID token always carries the user's email (verified)
    and name, so unlike Apple we never need a `full_name` payload
    field — we pull `name` directly off the verified claims.
    """
    id_token = (request.data.get("id_token") or "").strip()
    if not id_token:
        return Response({"detail": "Missing id_token."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        claims = _verify_google_id_token(id_token)
    except Exception as e:
        log.warning("Google SSO token rejected: %s", e)
        return Response({"detail": "Invalid Google sign-in."}, status=status.HTTP_401_UNAUTHORIZED)

    sub = claims.get("sub")
    email = claims.get("email")
    if not sub:
        return Response({"detail": "Token missing sub."}, status=status.HTTP_401_UNAUTHORIZED)

    full_name = (claims.get("name") or "").strip() or None
    user = _resolve_or_create_user(
        sub_field="google_sub",
        sub=sub,
        email=email,
        full_name=full_name,
    )
    return _login_response(request, user)
