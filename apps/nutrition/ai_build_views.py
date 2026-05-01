"""
NUTRITION-3-OPTIONS — AI nutrition plan builder.

POST /api/nutrition/solo/ai-build/
Body: {}  (the user's onboarding answers + profile context are
            read server-side from SoloProfile — no need to repeat
            them in the body)

Returns THREE macro plan variants the user picks from after the
cinematic loader. Mirrors the workout AI build pattern (single
AI call, structured response) — Claude returns three labelled
variants in one shot, no triple-call cap burn.

Wire shape:
{
  "variants": [
    { "id":"cut",      "label":"Lean down",
      "calories":1900, "protein":160, "carbs":160, "fats":60,
      "rationale":"~0.5kg/week deficit. Protein floor for muscle preservation." },
    { "id":"maintain", "label":"Hold steady", ... },
    { "id":"bulk",     "label":"Lean gain", ... },
  ],
  "remaining_month": int,
}

The user's `goals`, `dietary_pattern`, `bodyweight_kg`,
`height_cm`, `gender`, `experience` and `days_per_week` from
SoloProfile inform the AI's macro suggestions. Without
bodyweight (HealthKit not yet synced), the AI uses a sensible
default + flags low confidence in the rationale.

Apply path: iOS picks one variant → calls existing
`POST /api/nutrition/solo/macro-targets/` to commit the choice.
This view is generation-only; no profile mutation here.

Free first generation: matches AI-FREE-FIRST-GEN logic from
ai_build_views (workouts). First nutrition AI build is free for
any account; subsequent require Pro AI.
"""
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
# Provider config — same plumbing as ai_describe_views.
# --------------------------------------------------------------------
ANTHROPIC_API_KEY = getattr(settings, "ANTHROPIC_API_KEY", None) or os.environ.get(
    "ANTHROPIC_API_KEY", "",
)
ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# AI-FREE-FIRST-GEN — re-uses the same flag the workout side
# uses. Different namespace so a user's first workout AI build
# and first nutrition AI build are both free.
NUTRITION_FIRST_USED_KEY = "solo_ai_nutrition_build_used"


SYSTEM_PROMPT = """\
You are GymFlow's nutrition planner. Given a user's profile and
onboarding answers, return THREE macro target variants for them
to choose from.

Return ONLY valid JSON, no prose. Schema:
{
  "variants": [
    {
      "id":         "cut" | "maintain" | "bulk",
      "label":      string,    // 2-3 word coach-voice label
      "calories":   number,
      "protein":    number,    // grams
      "carbs":      number,    // grams
      "fats":       number,    // grams
      "rationale":  string     // ONE sentence on why someone picks this
    },
    ... (exactly 3)
  ]
}

Rules:
- ALWAYS return three variants in the order cut, maintain, bulk.
- Use the user's bodyweight + goals as the anchor. With no
  bodyweight, default to 75 kg and flag in rationale.
- Protein floor: 1.6 g/kg for general training, 2.0 g/kg if the
  user's goal stack includes "build_muscle" or "get_stronger".
- Calorie deltas from the AI's maintenance estimate:
  cut = -400 to -500, maintain = 0, bulk = +250 to +350.
- Round calories to the nearest 50, macros to the nearest 5.
- Respect dietary_pattern (vegan, vegetarian, halal, etc.) — DO
  NOT lower protein for plant-based users. They can hit the
  protein floor with legumes/tofu/tempeh.
- Voice: warm, plain English. NOT "macros optimised" — say
  things like "Lean down ~0.5kg/week" or "Steady weight while
  you focus on training".
- If experience is "just_starting", lean toward maintain (don't
  recommend an aggressive cut to a brand-new trainee).
"""


def _build_user_context(profile: SoloProfile) -> str:
    """Compact text block describing the user, fed to Claude in
    the user message. Same shape as the AI PT context but
    nutrition-focused."""
    bw = profile.bodyweight_kg or 75.0
    bw_confidence = "exact" if profile.bodyweight_kg else "default 75 kg, no measurement"
    goals = ", ".join(profile.goals) if profile.goals else "unspecified"
    gender = profile.gender or "unspecified"
    height = f"{profile.height_cm} cm" if profile.height_cm else "unknown"
    age_text = "unknown"
    dob = getattr(profile.user, "date_of_birth", None)
    if dob is not None:
        years = (timezone.localdate() - dob).days // 365
        age_text = f"{years} years"
    diet = profile.dietary_pattern or "unspecified"
    diet_other = profile.dietary_other or ""
    if diet == "other" and diet_other:
        diet = diet_other
    restrictions = ", ".join(profile.food_restrictions) if profile.food_restrictions else "none"
    dislikes = ", ".join(profile.food_dislikes) if profile.food_dislikes else "none"
    cooking = profile.cooking_comfort or "unspecified"
    meals = profile.meals_per_day or "unspecified"
    exp = profile.experience or "unspecified"
    days = profile.days_per_week or 3

    return (
        f"Bodyweight: {bw:.1f} kg ({bw_confidence})\n"
        f"Height: {height}\n"
        f"Age: {age_text}\n"
        f"Gender: {gender}\n"
        f"Goals: {goals}\n"
        f"Experience: {exp}\n"
        f"Training: {days} days/week\n"
        f"Dietary pattern: {diet}\n"
        f"Restrictions: {restrictions}\n"
        f"Dislikes: {dislikes}\n"
        f"Meals/day: {meals}\n"
        f"Cooking comfort: {cooking}\n"
    )


def _has_used_first_free(user) -> bool:
    prefs = user.notification_prefs or {}
    return bool(prefs.get(NUTRITION_FIRST_USED_KEY))


def _mark_first_used(user) -> None:
    prefs = user.notification_prefs or {}
    prefs[NUTRITION_FIRST_USED_KEY] = True
    user.notification_prefs = prefs
    user.save(update_fields=["notification_prefs"])


def _clamp_int(v, lo: int, hi: int) -> int:
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return lo
    return max(lo, min(n, hi))


def _sanitise_variant(raw: dict) -> dict:
    """Coerce AI output to the strict response schema. Defends
    against the AI returning plaintext labels, missing fields,
    nonsense numbers, etc. Falls back to safe defaults so iOS
    never decodes a malformed payload."""
    vid = (raw.get("id") or "").lower()
    if vid not in {"cut", "maintain", "bulk"}:
        vid = "maintain"
    label = (raw.get("label") or {
        "cut": "Lean down", "maintain": "Hold steady", "bulk": "Lean gain",
    }[vid]).strip()[:48]
    rationale = (raw.get("rationale") or "").strip()[:240]
    return {
        "id":        vid,
        "label":     label,
        "calories":  _clamp_int(raw.get("calories"), 1200, 5000),
        "protein":   _clamp_int(raw.get("protein"),  60, 400),
        "carbs":     _clamp_int(raw.get("carbs"),    50, 800),
        "fats":      _clamp_int(raw.get("fats"),     30, 250),
        "rationale": rationale,
    }


# --------------------------------------------------------------------
# Endpoint
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_ai_nutrition_build(request):
    user = request.user
    if user.role != User.SOLO:
        return Response(
            {"detail": "Solo accounts only."},
            status=status.HTTP_403_FORBIDDEN,
        )

    profile, _ = SoloProfile.objects.get_or_create(user=user)

    # AI-FREE-FIRST-GEN — first nutrition AI build is free for any
    # tier; subsequent require Pro AI. Mirrors the workout side's
    # gate in ai_build_views.py.
    if not profile.has_ai_access and _has_used_first_free(user):
        return Response(
            {
                "detail": "Pro AI required to re-build your nutrition plan. "
                          "First plan is on us.",
                "upgrade_to": "pro_ai",
            },
            status=status.HTTP_402_PAYMENT_REQUIRED,
        )

    if not ANTHROPIC_API_KEY:
        log.error("solo_ai_nutrition_build: ANTHROPIC_API_KEY not configured")
        return Response(
            {"detail": "AI nutrition build is temporarily unavailable."},
            status=503,
        )

    # Cap — uses the existing 'describe' bucket since both are
    # nutrition-AI calls and we don't want to multiply the cap
    # surface unnecessarily. Future: split if usage patterns
    # justify it.
    cap_ok, cap_info = enforce_cap(user, "describe")
    if not cap_ok:
        return Response(cap_info["error_response"], status=cap_info["status"])

    user_context = _build_user_context(profile)
    user_message = (
        "Generate three macro target variants for me. My profile:\n\n"
        f"{user_context}"
    )

    body = {
        "model":     ANTHROPIC_MODEL,
        "max_tokens": 700,
        "system":    SYSTEM_PROMPT,
        "messages":  [{"role": "user", "content": user_message}],
    }

    import requests
    try:
        resp = requests.post(
            ANTHROPIC_URL,
            json=body,
            headers={
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            timeout=45.0,
        )
    except Exception as exc:
        log.exception("AI nutrition build request failed")
        return Response(
            {"detail": f"AI provider unreachable: {exc}"},
            status=503,
        )

    if resp.status_code != 200:
        log.error(
            "AI nutrition build non-200: %s %s",
            resp.status_code, resp.text[:300],
        )
        return Response(
            {"detail": "AI provider returned an error."},
            status=502,
        )

    try:
        payload = resp.json()
        content = payload.get("content") or []
        text_block = next((c for c in content if c.get("type") == "text"), None)
        if not text_block:
            raise ValueError("No text block in response.")
        parsed = json.loads(text_block["text"])
        raw_variants = parsed.get("variants") or []
        if not isinstance(raw_variants, list) or len(raw_variants) != 3:
            raise ValueError(
                f"Expected 3 variants, got {len(raw_variants) if isinstance(raw_variants, list) else 'non-list'}"
            )
    except Exception as exc:
        log.exception("AI nutrition build parse failed: %s", exc)
        return Response(
            {"detail": "Couldn't parse AI response. Try again."},
            status=502,
        )

    variants = [_sanitise_variant(v) for v in raw_variants]

    # Order — always cut → maintain → bulk so the iOS picker
    # renders left-to-right consistently regardless of the AI's
    # output order.
    order = {"cut": 0, "maintain": 1, "bulk": 2}
    variants.sort(key=lambda v: order.get(v["id"], 99))

    # Mark the free-first slot used + bump the cap counter ONLY
    # after a successful parse, so a failed Anthropic call doesn't
    # burn the user's free slot.
    if not profile.has_ai_access:
        _mark_first_used(user)
    new_remaining = increment(user, "describe")

    return Response({
        "variants":        variants,
        "remaining_month": new_remaining,
    })
