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
import re

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
from .deficit_math import three_variants, defensible_rationale

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
You are Afletics's nutrition coach. The user has completed their
onboarding. We've already computed the macro numbers from
established formulas (Mifflin-St Jeor + Helms cut depths +
ISSN protein bands) — your job is to RETURN THOSE NUMBERS
unchanged and write a brief, calm rationale around each.

You will see ANCHOR NUMBERS in the user message. Use them
EXACTLY for calories/protein/carbs/fats. Do not invent
different numbers — the math is defensible only when it
matches what we computed.

Return ONLY valid JSON, no prose. Schema:
{
  "variants": [
    {
      "id":         "cut" | "maintain" | "bulk",
      "label":      string,
      "calories":   number,    // copy from ANCHOR
      "protein":    number,    // copy from ANCHOR
      "carbs":      number,    // copy from ANCHOR
      "fats":       number,    // copy from ANCHOR
      "rationale":  string     // 1 sentence, calm coach voice
    },
    ... (exactly 3, in order cut, maintain, bulk)
  ]
}

Rationale voice:
- Plain English, not "macros optimised".
- Speak to THIS user — reference their goals, dietary pattern,
  experience level, or training frequency where it adds value.
- 1 sentence each. <30 words.
- No exclamation marks, no hype. Calm, confident, evidence-led.
- For cut: explain why ~0.5–0.75 kg/week is the safe range.
- For maintain: speak to "training quality without juggling a
  deficit/surplus".
- For bulk: explain why ~0.25 kg/week is sustainable.

Respect dietary_pattern — DO NOT recommend lowering protein
for plant-based users. They can hit the floor with legumes,
tofu, tempeh, seitan, or supplemented protein.

If experience is "just_starting", note it: brand-new trainees
often benefit from "Hold steady" while they build a training
habit.
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


def _extract_json_object(text: str) -> dict | None:
    """NUTRITION-AI-BUILD-FIX — robust JSON extraction.

    Claude is asked for raw JSON but sometimes wraps the response
    in a ```json``` code fence or prefixes a one-line acknowledgement
    ("Here's your variants:\\n{...}"). Plain `json.loads(text)` then
    blows up with 502s the user can't recover from.

    This helper tries, in order:
      1. Strip a leading ```json (or plain ```) fence, parse the
         payload between fences.
      2. Find the FIRST `{` and LAST `}` in the string and parse
         the substring between them. Handles "Here are your
         variants: {...}" prefixes.
      3. Direct `json.loads(text)`.

    Returns the parsed dict on success, None on failure (caller
    surfaces a 502 with the raw payload logged for debugging).
    """
    if not text:
        return None
    text = text.strip()

    # 1. Code fence — handle ```json ... ``` and ``` ... ```
    fence = re.match(r"^```(?:json)?\s*\n(.*?)\n```\s*$", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass

    # 2. First-{ to last-} substring. Slightly fragile if Claude
    # writes prose containing braces, but works for the common
    # "prefix + JSON" failure mode.
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        try:
            return json.loads(text[first : last + 1])
        except json.JSONDecodeError:
            pass

    # 3. Direct parse — covers the happy path where Claude obeyed.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


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

    # POLISH-AICAP — own bucket. Previously shared `describe` with
    # photo food logging, which collided: a single user retrying the
    # AI nutrition setup could exhaust the 100/mo describe budget and
    # the next photo log returned an instant cap error. Splitting it
    # off gives nutrition setup its own modest 6/mo limit and protects
    # the describe budget for daily use.
    cap_ok, cap_info = enforce_cap(user, "nutrition_build")
    if not cap_ok:
        return Response(cap_info["error_response"], status=cap_info["status"])

    user_context = _build_user_context(profile)

    # DEFICIT-MATH (#127) — compute the anchor numbers
    # deterministically before the AI call. Claude returns those
    # numbers verbatim and writes the prose around them.
    dob = profile.user.date_of_birth
    age_years = None
    if dob is not None:
        age_years = (timezone.localdate() - dob).days // 365
    inputs = {
        "weight_kg":     profile.bodyweight_kg,
        "height_cm":     profile.height_cm,
        "age_years":     age_years,
        "sex":           (profile.sex_at_birth or profile.gender or None),
        "goals":         profile.goals or [],
        "experience":    profile.experience or "",
        "days_per_week": profile.days_per_week or 3,
    }
    anchors = three_variants(inputs)
    anchors_block = "\n".join(
        f"- {a['id']}: calories={a['calories']}, protein={a['protein']}g, "
        f"carbs={a['carbs']}g, fats={a['fats']}g"
        for a in anchors
    )

    user_message = (
        "Generate three macro target variants for me.\n\n"
        f"USER PROFILE:\n{user_context}\n"
        "ANCHOR NUMBERS (use these EXACTLY for each variant — do not "
        "compute different ones):\n"
        f"{anchors_block}\n\n"
        "Return all three variants in the schema specified by the "
        "system prompt, with rationale prose written around these "
        "anchor numbers."
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
        # NUTRITION-AI-BUILD-FIX — robust extraction. The previous
        # version did a bare `json.loads` on text_block["text"] and
        # 502'd whenever Claude wrapped its reply in a ```json
        # fence. Helper now tries fence → substring → direct, and
        # we log the raw payload on total failure so we can see
        # what Claude actually returned.
        raw_text = text_block.get("text") or ""
        parsed = _extract_json_object(raw_text)
        if parsed is None:
            log.error(
                "AI nutrition build: JSON extraction failed. Raw text: %s",
                raw_text[:600],
            )
            raise ValueError("Couldn't extract a JSON object from the AI reply.")
        raw_variants = parsed.get("variants") or []
        if not isinstance(raw_variants, list):
            raise ValueError(f"variants is not a list: {type(raw_variants).__name__}")
        # Allow 1–3 variants; we backfill missing IDs from anchors
        # below so a partial Claude response doesn't 502 the user.
        # >3 truncate to first 3.
        if len(raw_variants) > 3:
            raw_variants = raw_variants[:3]
        if not raw_variants:
            raise ValueError("Empty variants list — no usable AI output.")
    except Exception as exc:
        log.exception("AI nutrition build parse failed: %s", exc)
        return Response(
            {"detail": "Couldn't parse AI response. Try again."},
            status=502,
        )

    variants = [_sanitise_variant(v) for v in raw_variants]

    # NUTRITION-AI-BUILD-FIX — backfill any missing variant IDs
    # from the anchor data so iOS always gets a complete cut /
    # maintain / bulk trio. The AI's rationale prose is the
    # value-add; if Claude only returned cut+maintain, we still
    # surface bulk via the deterministic rationale.
    seen_ids = {v["id"] for v in variants}
    for required_id in ("cut", "maintain", "bulk"):
        if required_id not in seen_ids:
            anchor = next((a for a in anchors if a["id"] == required_id), None)
            if anchor is None:
                continue
            label = {
                "cut":      "Lean down",
                "maintain": "Hold steady",
                "bulk":     "Lean gain",
            }[required_id]
            variants.append({
                "id":        required_id,
                "label":     label,
                "calories":  anchor["calories"],
                "protein":   anchor["protein"],
                "carbs":     anchor["carbs"],
                "fats":      anchor["fats"],
                "rationale": defensible_rationale(required_id, inputs, anchor),
            })

    # Order — always cut → maintain → bulk so the iOS picker
    # renders left-to-right consistently regardless of the AI's
    # output order.
    order = {"cut": 0, "maintain": 1, "bulk": 2}
    variants.sort(key=lambda v: order.get(v["id"], 99))

    # DEFICIT-MATH guard — the AI is *supposed* to copy the
    # anchor numbers verbatim, but if it ever drifts (Claude
    # rounds creatively, returns 1990 instead of 2000, etc.) we
    # overwrite from the anchors. The math is the source of
    # truth; the AI provides voice. Defense in depth.
    anchor_by_id = {a["id"]: a for a in anchors}
    for v in variants:
        anchor = anchor_by_id.get(v["id"])
        if anchor is None:
            continue
        v["calories"] = anchor["calories"]
        v["protein"]  = anchor["protein"]
        v["carbs"]    = anchor["carbs"]
        v["fats"]     = anchor["fats"]
        # Keep the AI's rationale + label — that's the value-add.
        # If the AI returned no rationale, fall back to the
        # deterministic one from defensible_rationale.
        if not v.get("rationale"):
            v["rationale"] = defensible_rationale(v["id"], inputs, anchor)

    # Mark the free-first slot used + bump the cap counter ONLY
    # after a successful parse, so a failed Anthropic call doesn't
    # burn the user's free slot.
    if not profile.has_ai_access:
        _mark_first_used(user)
    new_remaining = increment(user, "nutrition_build")

    return Response({
        "variants":        variants,
        "remaining_month": new_remaining,
    })
