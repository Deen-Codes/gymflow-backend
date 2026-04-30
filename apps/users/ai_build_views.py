"""
R3-1 — AI build programme + assign.

Two endpoints:

  • POST /api/users/solo/ai-build/preview/
        Generates a structured programme JSON via Claude Sonnet 4.6
        with tool-use. Free users get ONE preview lifetime (tracked
        on User.notification_prefs); Pro AI users unlimited.
        Returns: {"programme": {...}, "preview_remaining": 0|1, "ai_generated": true}

  • POST /api/users/solo/ai-build/assign/
        Body: {"programme": {...}}
        Validates the JSON shape, creates a WorkoutPlan + WorkoutDays
        + Exercises + ExerciseSetTargets, swaps the user's active plan.
        Pro-AI gated (assignment is paid; preview is the freemium).

Why two endpoints (not one): keeps the freemium preview cheap (no
DB writes) while gating the actual assignment behind Pro AI. The
client holds the JSON between calls — no server-side state to
synchronise.

Tool-use via Anthropic's `tools` parameter forces the model to
return strictly schema-conformant JSON. Beats prose-parsing for
reliability.
"""
import json
import logging
import os

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import User, SoloProfile
from .ai_pt_views import _build_user_context, ANTHROPIC_API_KEY, ANTHROPIC_MODEL, ANTHROPIC_URL

log = logging.getLogger(__name__)


# Track preview usage on User.notification_prefs (existing JSONField,
# no migration needed). The iOS UserDefaults gate is the first line
# of defence; this is the server-side belt-and-braces.
PREVIEW_USED_KEY = "solo_ai_build_preview_used"


# Tool spec — forces Claude to return a strictly-shaped programme.
# Names/labels/sets follow the same conventions as
# `seed_solo_programmes.py` so the existing custom-programme creation
# path can ingest the result without translation.
PROGRAMME_TOOL = {
    "name": "submit_programme",
    "description": (
        "Submit the final programme as structured JSON. Call this "
        "once you've decided the full programme. Do not call any "
        "other tool. The user is shown the result of this call."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Short title, 3-6 words, e.g. 'Upper/Lower Strength'",
            },
            "tagline": {
                "type": "string",
                "description": "One-line subtitle, e.g. 'Twice-weekly muscle frequency'",
            },
            "summary": {
                "type": "string",
                "description": "2-3 sentence prose summary of what the programme is and why it suits this user.",
            },
            "days_per_week": {"type": "integer", "minimum": 1, "maximum": 7},
            "weeks": {"type": "integer", "minimum": 4, "maximum": 16},
            "progression_rule": {
                "type": "string",
                "description": "How weight or reps go up over time.",
            },
            "deload_strategy": {
                "type": "string",
                "description": "When and how to back off if accumulated fatigue stacks up.",
            },
            "days": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Day name, e.g. 'Push A', 'Lower body strength'",
                        },
                        "exercises": {
                            "type": "array",
                            "minItems": 2,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "label": {
                                        "type": "string",
                                        "description": "Single letter label A-G",
                                    },
                                    "sets": {
                                        "type": "array",
                                        "minItems": 1,
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "set_number": {"type": "integer", "minimum": 1},
                                                "reps": {"type": "string"},
                                            },
                                            "required": ["set_number", "reps"],
                                        },
                                    },
                                },
                                "required": ["name", "label", "sets"],
                            },
                        },
                    },
                    "required": ["title", "exercises"],
                },
            },
        },
        "required": ["name", "summary", "days_per_week", "weeks", "days"],
    },
}


SYSTEM_TEMPLATE = """\
You are GymFlow's AI personal trainer building a programme for one
specific user. Use the USER CONTEXT below to make every decision —
goals, experience, equipment, days/week, bodyweight when available.

Your output is a single tool call to `submit_programme`. Don't write
prose around it.

Programme rules:
- Match the user's days_per_week exactly. If unspecified, pick 3
  for beginners, 4 for intermediate, 5-6 for advanced.
- Pick exercises that fit the user's equipment:
    • full_gym → barbell, machines, cables, dumbbells all available
    • home_with_weights → dumbbells, bench, no rack/cables
    • bodyweight_only → no equipment, progressive calisthenics
    • mixed → bias toward bodyweight + dumbbell variants for travel
- Programme structure follows the research-backed templates in
  Schoenfeld 2019 (≥10 sets per muscle per week, twice-weekly
  frequency for hypertrophy) and Helms 2018 (RIR-based
  progression for trained lifters; linear for novices).
- Pick 4-7 exercises per day. Avoid filler.
- Reps: use ranges like "5-7", "8-12", "10-15". Use single
  numbers like "5" only for max-strength programming (Starting
  Strength / 5/3/1).
- Source attribution: if the programme is a recognised template
  (Starting Strength, 5/3/1, PPL), name it. Otherwise call it a
  synthesis, e.g. "Upper/Lower split, Schoenfeld-Helms synthesis".

Tone for `tagline` and `summary`:
- Calm coach. No "crush it", "smash it", "beast mode", "let's go",
  "you got this", "no pain no gain", "warrior", "grind". No
  exclamation marks unless genuinely warranted.
- Honest. If the user picked goals that conflict (e.g. lose_fat
  + get_stronger as a beginner with 2 days/week), name the
  trade-off in the summary.

USER CONTEXT:
{context}
"""


def _has_used_preview(user) -> bool:
    prefs = user.notification_prefs or {}
    return bool(prefs.get(PREVIEW_USED_KEY))


def _mark_preview_used(user) -> None:
    prefs = user.notification_prefs or {}
    prefs[PREVIEW_USED_KEY] = True
    user.notification_prefs = prefs
    user.save(update_fields=["notification_prefs"])


def _call_claude_for_programme(user) -> tuple[dict | None, str | None]:
    """Returns (programme_json, error_string)."""
    import requests

    if not ANTHROPIC_API_KEY:
        # Loud log so this shows up in Render logs the moment a
        # call lands. Silent return previously meant 503s with
        # zero diagnostic trail.
        log.error(
            "AI build: ANTHROPIC_API_KEY env var is missing or empty on this "
            "deploy. Set it in Render → Environment → Environment Variables."
        )
        return None, "AI build temporarily unavailable."

    context = _build_user_context(user)
    system = SYSTEM_TEMPLATE.format(context=context)

    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 2500,
        "system": system,
        "tools": [PROGRAMME_TOOL],
        "tool_choice": {"type": "tool", "name": "submit_programme"},
        "messages": [
            {"role": "user", "content": "Build me a programme."},
        ],
    }
    try:
        resp = requests.post(
            ANTHROPIC_URL,
            json=body,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            # R6-1 — bumped from 45s to 70s. Anthropic tool-use
            # generation with a 2500-token max output regularly
            # hits 30-50s on first call; 45s was cutting off
            # legitimate responses. iOS timeout is 75s; backend
            # at 70s leaves a 5s buffer for our own response
            # serialisation.
            timeout=70.0,
        )
    except requests.exceptions.Timeout:
        log.error("AI build timed out talking to Anthropic")
        return None, "AI provider took too long to respond. Please try again."
    except Exception as exc:
        log.exception("AI build request failed")
        return None, f"AI provider unreachable: {exc}"

    if resp.status_code != 200:
        log.error("AI build non-200: %s %s", resp.status_code, resp.text[:300])
        # Surface the actual Anthropic error code + parsed reason
        # so iOS can show something more useful than a generic 503.
        # Common reasons we want the user / dev to see:
        #   401 invalid_authentication / authentication_error → API key wrong on Render
        #   402 payment_required → Anthropic account out of credit
        #   429 rate_limit_error → too many requests / spend cap hit
        #   500/529 → Anthropic having an outage
        try:
            err = (resp.json().get("error") or {})
            err_type = err.get("type") or "error"
            err_msg = err.get("message") or "AI provider returned an error."
        except Exception:
            err_type, err_msg = "error", "AI provider returned an error."
        if resp.status_code == 401:
            return None, "AI provider rejected our API key — it may be missing or wrong on Render."
        if resp.status_code == 402:
            return None, "AI provider account is out of credits — top up at console.anthropic.com."
        if resp.status_code == 429:
            return None, "AI provider rate-limited the request. Try again in a minute."
        return None, f"AI provider {resp.status_code} ({err_type}): {err_msg[:160]}"

    payload = resp.json()
    # The reply is a list of content blocks; we want the tool_use
    # block, NOT any text block (the system prompt asked for tool-
    # only output, but we defend anyway).
    content = payload.get("content") or []
    tool_block = next(
        (c for c in content if c.get("type") == "tool_use"
         and c.get("name") == "submit_programme"),
        None,
    )
    if tool_block is None:
        log.error("AI build missing tool_use block: %s", payload)
        return None, "Couldn't parse the AI response."
    programme = tool_block.get("input") or {}
    if not programme.get("days"):
        return None, "AI returned an empty programme."
    return programme, None


# --------------------------------------------------------------------
# Preview endpoint
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_ai_build_preview(request):
    user = request.user
    if user.role != User.SOLO:
        return Response({"detail": "Solo accounts only."}, status=status.HTTP_403_FORBIDDEN)

    profile, _ = SoloProfile.objects.get_or_create(user=user)
    has_ai = profile.has_ai_access

    if not has_ai:
        if _has_used_preview(user):
            return Response(
                {
                    "detail": "Free preview used. Start a 14-day Pro AI trial for unlimited.",
                    "upgrade_to": "pro_ai",
                },
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )

    programme, error = _call_claude_for_programme(user)
    if error:
        return Response({"detail": error}, status=503)

    # Mark preview used AFTER a successful generation. We don't
    # burn the preview on a network failure.
    if not has_ai:
        _mark_preview_used(user)

    return Response({
        "programme":         programme,
        "preview_remaining": 0 if not has_ai else None,
        "ai_generated":      True,
    })


# --------------------------------------------------------------------
# Assign endpoint
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_ai_build_assign(request):
    """Pro-AI gated. Takes the programme JSON from the preview,
    creates a WorkoutPlan + days + exercises + sets, swaps the
    user's active plan."""
    from apps.workouts.models import (
        WorkoutPlan, WorkoutDay, Exercise, ExerciseSetTarget,
    )

    user = request.user
    if user.role != User.SOLO:
        return Response({"detail": "Solo accounts only."}, status=status.HTTP_403_FORBIDDEN)

    profile, _ = SoloProfile.objects.get_or_create(user=user)
    if not profile.has_ai_access:
        return Response(
            {"detail": "Pro AI required to assign AI-built programmes.",
             "upgrade_to": "pro_ai"},
            status=status.HTTP_402_PAYMENT_REQUIRED,
        )

    programme = (request.data or {}).get("programme")
    if not isinstance(programme, dict):
        return Response({"detail": "Body must contain a 'programme' object."}, status=400)

    name = (programme.get("name") or "AI programme").strip()[:255]
    days = programme.get("days") or []
    if not isinstance(days, list) or not days:
        return Response({"detail": "Programme has no days."}, status=400)

    # Build the programme_meta for catalog-style display surfaces
    # (Hub hero, share cards). Mirrors the seed_solo_programmes.py
    # shape so the AI-built plan reads natively next to seed plans.
    meta = {
        "goals":              [g for g in (profile.goals or [])],
        "experience":         profile.experience or "",
        "equipment":          profile.equipment or "",
        "days_per_week":      programme.get("days_per_week") or len(days),
        "weeks":              programme.get("weeks") or 8,
        "tagline":            (programme.get("tagline") or "Built by AI for you")[:120],
        "summary":            (programme.get("summary") or "")[:500],
        "progression_rule":   (programme.get("progression_rule") or "")[:500],
        "deload_strategy":    (programme.get("deload_strategy") or "")[:500],
        "evidence":           ["Generated by GymFlow AI from your goals + equipment."],
        "source_attribution": "GymFlow AI",
        "ai_generated":       True,
        "generated_at":       timezone.now().isoformat(),
    }

    with transaction.atomic():
        # Deactivate any prior plan, same as the catalog assign path.
        previous = profile.assigned_workout_plan
        if previous is not None:
            previous.is_active = False
            previous.save(update_fields=["is_active"])

        plan = WorkoutPlan.objects.create(
            user=user,
            name=name,
            is_active=True,
            is_template=False,
            is_solo_template=False,
            programme_meta=meta,
        )
        for day_index, day in enumerate(days):
            if not isinstance(day, dict):
                continue
            day_title = (day.get("title") or f"Day {day_index + 1}")[:100]
            new_day = WorkoutDay.objects.create(
                plan=plan, title=day_title, order=day_index,
            )
            exercises = day.get("exercises") or []
            for ex_idx, ex in enumerate(exercises):
                if not isinstance(ex, dict):
                    continue
                ex_name = (ex.get("name") or "Exercise")[:255]
                ex_label = (ex.get("label") or chr(ord("A") + ex_idx))[:10]
                new_ex = Exercise.objects.create(
                    workout_day=new_day,
                    name=ex_name,
                    label=ex_label,
                    order=ex_idx,
                )
                sets = ex.get("sets") or []
                for set_idx, st in enumerate(sets):
                    if not isinstance(st, dict):
                        continue
                    set_number = int(st.get("set_number") or (set_idx + 1))
                    reps = (st.get("reps") or "")[:20]
                    ExerciseSetTarget.objects.create(
                        exercise=new_ex,
                        set_number=set_number,
                        reps=reps,
                    )

        profile.assigned_workout_plan = plan
        profile.save(update_fields=["assigned_workout_plan"])

    return Response({
        "ok":        True,
        "plan_id":   plan.id,
        "plan_name": plan.name,
    })
