"""
PHOTO-COACHING (#106) — Claude Vision analysis of progress photos.

Endpoint: POST /api/progress/solo/photos/<photo_id>/analyze/

Calls Claude Sonnet 4.6 with the photo's base64 payload and a strict
calm-coach system prompt. Persists the response onto the
`ProgressPhoto.ai_commentary` field and stamps `ai_analyzed_at`.

Idempotency: if `ai_analyzed_at` is already set and `?refresh=1`
isn't passed, we return the cached commentary without burning a
fresh AI cap. Cost guardrail: this hits the `describe` bucket —
typical user takes a photo monthly, so the budget is generous
enough not to need a dedicated bucket.

Safety system prompt rules (the meat of #106):
  • Never comment on attractiveness, weight, "fitness", or any
    appearance value judgement.
  • Focus on objective measurable observations: visible posture,
    symmetry, definition compared with the previous photo of the
    same category. NEVER absolute-state the user's body.
  • If the photo is unsuitable (too dark, off-frame, multiple
    people), say so plainly and don't fabricate observations.
  • If the user appears distressed, very young, or the photo
    looks not-of-themselves, refuse and return a soft message.
"""
from __future__ import annotations

import logging
import os

import requests
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import ProgressPhoto
from apps.users.ai_caps import enforce_cap, increment

log = logging.getLogger(__name__)

ANTHROPIC_API_KEY = getattr(settings, "ANTHROPIC_API_KEY", None) or os.environ.get(
    "ANTHROPIC_API_KEY", "",
)
ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MAX_OUTPUT_TOKENS = 600


SAFETY_SYSTEM_PROMPT = """\
You are Afletics's coach reviewing a progress photo the user just
uploaded. Your job is to write a SHORT, CALM, OBJECTIVE observation
that helps the user see their own progress without judging their
appearance.

ABSOLUTE RULES — never break these:
1. NEVER comment on attractiveness, beauty, sexiness, weight loss
   "looking better", or any appearance judgement positive or negative.
2. NEVER use the words: skinny, fat, slim, lean, ripped, jacked,
   shredded, thicc, snatched, transformed, glow-up, beast.
3. NEVER state the user's body weight, BMI, or body fat percentage
   from the photo. You can't measure these from a photo.
4. Focus on OBJECTIVE OBSERVATIONS only: visible posture, symmetry,
   muscular definition relative to a previous photo if context is
   provided. Use clinical-neutral language.
5. If the photo isn't suitable for review (too dark, off-frame,
   multiple people, not the user, distressing content), say so
   plainly: "I can't read this photo clearly — try a brighter,
   front-on shot." Don't invent observations.
6. Keep it SHORT. 2 sentences. ≤ 50 words total.
7. End with a forward-looking line, not an evaluation. e.g. "Hold
   this lighting + angle for the next one and the comparison gets
   easier."

VOICE CARD — calm coach:
- Like a sports physiotherapist, not a hype-up trainer.
- No exclamation marks. No emoji.
- Banned phrases (also banned in the app): crush, smash, beast,
  level up, transform, ultimate.

Output format: plain prose. No JSON. No bullet points. No headers.
"""


@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def analyze_progress_photo(request, photo_id: int):
    """POST /api/progress/solo/photos/<photo_id>/analyze/?refresh=0|1

    Returns:
        {"commentary": str, "cached": bool, "analyzed_at": iso8601}
    """
    user = request.user
    photo = get_object_or_404(ProgressPhoto, id=photo_id, user=user)

    refresh = request.query_params.get("refresh", "0") == "1"
    if photo.ai_commentary and photo.ai_analyzed_at and not refresh:
        return Response({
            "commentary":  photo.ai_commentary,
            "cached":      True,
            "analyzed_at": photo.ai_analyzed_at.isoformat(),
        })

    if not ANTHROPIC_API_KEY:
        return Response(
            {"detail": "AI service not configured."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    # Cap — uses the describe bucket. Photo analysis is comparable
    # in cost to a food-describe call (single Claude Vision round,
    # ~600 output tokens) and the typical cadence is monthly, so
    # adding a dedicated bucket would be overkill.
    cap_ok, cap_info = enforce_cap(user, "describe")
    if not cap_ok:
        return Response(
            cap_info["error_response"],
            status=cap_info["status"],
        )

    # Pull the most recent prior photo of the same category so the
    # AI can compare. If none, single-photo mode (no comparison).
    prior = (
        ProgressPhoto.objects
        .filter(user=user, category=photo.category)
        .exclude(id=photo.id)
        .order_by("-taken_on", "-created_at")
        .first()
    )

    user_message_text = (
        f"Category: {photo.get_category_display()}.\n"
        f"Taken: {photo.taken_on.isoformat()}.\n"
    )
    if photo.bodyweight_kg:
        user_message_text += f"Self-reported bodyweight at time of photo: {photo.bodyweight_kg:.1f} kg.\n"
    if prior:
        user_message_text += (
            f"\nPrevious photo of the same category was {prior.taken_on.isoformat()}"
            f" ({(photo.taken_on - prior.taken_on).days} days earlier)."
        )
        if prior.bodyweight_kg:
            user_message_text += f" Bodyweight then: {prior.bodyweight_kg:.1f} kg."

    # Build the multimodal message. Claude Vision accepts base64
    # via the `image` content block with `type: "base64"`.
    content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",   # iOS uploads as JPEG; PNG also fine here
                "data": photo.image_base64,
            },
        },
        {"type": "text", "text": user_message_text},
    ]
    if prior:
        content.insert(0, {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": prior.image_base64,
            },
        })
        content.insert(1, {"type": "text", "text": "Previous photo (older), then current photo:"})

    headers = {
        "x-api-key":         ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
    }
    body = {
        "model":      ANTHROPIC_MODEL,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system":     SAFETY_SYSTEM_PROMPT,
        "messages":   [{"role": "user", "content": content}],
    }

    try:
        resp = requests.post(ANTHROPIC_URL, headers=headers, json=body, timeout=60)
        resp.raise_for_status()
        result = resp.json()
    except requests.HTTPError as e:
        log.exception("photo_ai analyze failed: %s", e)
        return Response(
            {"detail": "AI service is busy — try again in a minute."},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    text = ""
    for block in result.get("content", []):
        if block.get("type") == "text":
            text += block.get("text", "")
    text = text.strip()

    if not text:
        return Response(
            {"detail": "AI returned an empty response. Try again."},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    photo.ai_commentary = text[:1500]    # belt-and-braces cap
    photo.ai_analyzed_at = timezone.now()
    photo.save(update_fields=["ai_commentary", "ai_analyzed_at"])
    increment(user, "describe")

    return Response({
        "commentary":  photo.ai_commentary,
        "cached":      False,
        "analyzed_at": photo.ai_analyzed_at.isoformat(),
    })
