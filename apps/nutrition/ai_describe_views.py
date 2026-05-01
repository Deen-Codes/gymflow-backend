"""
N.1.2 — AI describe for food logging.

POST /api/nutrition/solo/ai-describe/
Body: { "image_b64": str?, "text": str?, "portion_hint": str? }

Either an image OR a text description (or both for max accuracy)
plus an optional portion hint ("medium plate", "100g", "1 cup",
etc). Returns:

  {
    "name":     "Chicken stir fry with rice",
    "calories": 620,
    "protein":  45,
    "carbs":    72,
    "fats":     14,
    "portion":  "1 plate (~450g)",
    "confidence": "medium",
    "warnings": ["No oil visible — actual fat may be higher"],
  }

Provider: Anthropic Claude (claude-sonnet-4-6 with vision). Chosen
over GPT-4o because:
  • Better structured output adherence at this token size.
  • Claude's vision is calibrated well for cooked-food estimation.
  • Lower per-call cost (~$0.003/call at our payload size) — gives
    us margin even on free-tier abuse.

Cost guardrails:
  • Pro AI required (entitlement check). Free / Pro tiers get a 402.
  • Max 5MB image post-base64.
  • 50 calls/day per user (rate limit).

The Anthropic API key lives in `settings.ANTHROPIC_API_KEY` (set as
a Render env var). Callers don't see it; we're a proxy.
"""
import base64
import json
import logging
import os

from django.conf import settings
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import User, SoloProfile
from apps.users.ai_caps import enforce_cap, increment

log = logging.getLogger(__name__)


# --------------------------------------------------------------------
# Provider config
# --------------------------------------------------------------------
ANTHROPIC_API_KEY = getattr(settings, "ANTHROPIC_API_KEY", None) or os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL   = "claude-sonnet-4-6"
ANTHROPIC_URL     = "https://api.anthropic.com/v1/messages"

MAX_IMAGE_BYTES   = 5 * 1024 * 1024   # 5MB after b64 decode
RATE_LIMIT_PER_DAY = 50


SYSTEM_PROMPT = """\
You are a precise nutrition estimator for a fitness app. The user
shows you a meal (image, text description, or both) and you return
their estimated macronutrients.

Return ONLY valid JSON, no prose. Schema:
{
  "name":       string,    // short label, e.g. "Grilled chicken with rice and broccoli"
  "portion":    string,    // human description, e.g. "1 plate (~450g)"
  "calories":   number,    // total calories for the visible portion
  "protein":    number,    // grams
  "carbs":      number,    // grams
  "fats":       number,    // grams
  "confidence": "high" | "medium" | "low",
  "warnings":   [string]   // 0–3 caveats, e.g. cooking oil, sauce hidden
}

Rules:
- Estimate the visible portion size, not "what people usually eat".
- Round calories to nearest 10, macros to nearest gram.
- If you can't see the food clearly OR the input is ambiguous,
  return confidence: "low" and a warning explaining why.
- Never refuse — give your best estimate even with limited info.
- Do NOT include any text outside the JSON object.
"""


# R7-1 — Rate limiting now via apps.users.ai_caps (persistent,
# per-user, monthly bucket on User.notification_prefs). The
# previous in-memory dict reset on dyno restart.


# --------------------------------------------------------------------
# Endpoint
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_ai_describe_food(request):
    user = request.user
    if user.role != User.SOLO:
        return Response({"detail": "Solo accounts only."}, status=status.HTTP_403_FORBIDDEN)

    profile, _ = SoloProfile.objects.get_or_create(user=user)
    if not profile.has_ai_access:
        return Response(
            {"detail": "AI describe is a Pro AI feature.", "upgrade_to": "pro_ai"},
            status=status.HTTP_402_PAYMENT_REQUIRED,
        )

    if not ANTHROPIC_API_KEY:
        log.error("solo_ai_describe_food: ANTHROPIC_API_KEY not configured")
        return Response({"detail": "AI describe is temporarily unavailable."}, status=503)

    # R7-1 caps — persistent monthly limit (100/month for describe).
    cap_ok, cap_info = enforce_cap(user, "describe")
    if not cap_ok:
        return Response(cap_info["error_response"], status=cap_info["status"])

    data = request.data or {}
    image_b64 = (data.get("image_b64") or "").strip()
    text      = (data.get("text") or "").strip()
    portion_hint = (data.get("portion_hint") or "").strip()

    if not image_b64 and not text:
        return Response({"detail": "Either image_b64 or text is required."}, status=400)

    # Validate image size (post-decode).
    image_media_type = None
    if image_b64:
        try:
            decoded = base64.b64decode(image_b64, validate=True)
        except Exception:
            return Response({"detail": "image_b64 is not valid base64."}, status=400)
        if len(decoded) > MAX_IMAGE_BYTES:
            return Response({"detail": "Image too large (5MB max)."}, status=413)
        # JPEG / PNG sniff.
        if decoded[:3] == b"\xff\xd8\xff":
            image_media_type = "image/jpeg"
        elif decoded[:8] == b"\x89PNG\r\n\x1a\n":
            image_media_type = "image/png"
        else:
            image_media_type = "image/jpeg"  # best guess

    # Build the user message.
    user_parts = []
    if image_b64 and image_media_type:
        user_parts.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image_media_type,
                "data": image_b64,
            },
        })
    user_text = []
    if text:
        user_text.append(f"Description: {text}")
    if portion_hint:
        user_text.append(f"Portion hint: {portion_hint}")
    if not user_text:
        user_text.append("Estimate the macros for this meal.")
    user_parts.append({"type": "text", "text": "\n".join(user_text)})

    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 400,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_parts}],
    }

    import requests
    try:
        resp = requests.post(
            ANTHROPIC_URL,
            json=body,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=20.0,
        )
    except Exception as exc:
        log.exception("AI describe request failed")
        return Response({"detail": f"AI provider unreachable: {exc}"}, status=503)

    if resp.status_code != 200:
        log.error("AI describe non-200: %s %s", resp.status_code, resp.text[:300])
        return Response({"detail": "AI provider returned an error."}, status=502)

    try:
        payload = resp.json()
        # Anthropic responses come back as content blocks; the JSON
        # we want is in the first text block.
        content = payload.get("content") or []
        text_block = next((c for c in content if c.get("type") == "text"), None)
        if not text_block:
            raise ValueError("No text block in response.")
        parsed = json.loads(text_block["text"])
    except Exception as exc:
        log.exception("AI describe parse failed: %s", exc)
        return Response({"detail": "Couldn't parse AI response."}, status=502)

    # Sanitise + clamp the numeric fields.
    def _num(key, default=0.0, max_val=10000):
        try:
            v = float(parsed.get(key, default))
        except (TypeError, ValueError):
            v = default
        return max(0.0, min(v, max_val))

    # R7-1 — bump the monthly counter only AFTER a successful parse,
    # so a failed Anthropic call doesn't burn a slot.
    new_remaining = increment(user, "describe")

    return Response({
        "name":            (parsed.get("name") or "Unknown meal")[:255],
        "portion":         (parsed.get("portion") or "")[:80],
        "calories":        round(_num("calories")),
        "protein":         round(_num("protein", max_val=500)),
        "carbs":           round(_num("carbs",   max_val=1000)),
        "fats":            round(_num("fats",    max_val=500)),
        "confidence":      parsed.get("confidence") if parsed.get("confidence") in {"high", "medium", "low"} else "medium",
        "warnings":        list(parsed.get("warnings") or [])[:3],
        "remaining_month": new_remaining,
    })
