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
from .ai_caps import enforce_cap, increment

log = logging.getLogger(__name__)


# Track preview usage on User.notification_prefs (existing JSONField,
# no migration needed). The iOS UserDefaults gate is the first line
# of defence; this is the server-side belt-and-braces.
PREVIEW_USED_KEY = "solo_ai_build_preview_used"

# AI-FREE-FIRST-GEN — the FIRST AI-built programme assignment is
# free for every user; subsequent assignments require Pro AI. The
# free assignment is the conversion lever — user gets a real
# working plan without paying, then hits the paywall when they
# want chat coaching, mutations, weekly check-ins, or to re-roll.
# Per Deen's call ("Pattern B for v1, Pattern C as Phase E").
ASSIGN_USED_KEY = "solo_ai_build_assign_used"


def _has_used_first_free_assign(user) -> bool:
    """True iff this user has previously assigned at least one
    AI-built programme. Used to gate Pro AI on subsequent assigns."""
    prefs = user.notification_prefs or {}
    return bool(prefs.get(ASSIGN_USED_KEY))


def _mark_assign_used(user) -> None:
    """Flip the flag once the user assigns their first AI plan.
    Subsequent assigns will hit the Pro AI gate."""
    prefs = user.notification_prefs or {}
    prefs[ASSIGN_USED_KEY] = True
    user.notification_prefs = prefs
    user.save(update_fields=["notification_prefs"])


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
                                    "exercise_catalog_id": {
                                        "type": "integer",
                                        "description": "T3.1 — REQUIRED. Must be one of the IDs in the CATALOG SLICE block. Hallucinated IDs cause the build to be rejected.",
                                    },
                                    "label": {
                                        "type": "string",
                                        "description": "Single letter label A-G",
                                    },
                                    "rest_seconds": {
                                        "type": "integer",
                                        "description": "Optional rest between sets in seconds. Default 90 if omitted.",
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
                                "required": ["name", "exercise_catalog_id", "label", "sets"],
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


def _call_claude_for_programme(user, *, retry: bool = False) -> tuple[dict | None, str | None]:
    """Returns (programme_json, error_string).

    T3.1 — catalog-grounded: pre-fetches a ~200-row slice of
    ExerciseCatalog (filtered by user equipment / level / avoidances,
    ranked by goal-aligned muscle priority) and injects it into the
    Claude system prompt as the candidate set. The tool spec then
    requires every returned exercise to include an
    `exercise_catalog_id` from the slice. After the response we
    validate every id; on hallucination we retry once with a
    stricter prompt; on second failure we return a clean 503.
    """
    import json
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

    # T3.1 — catalog candidate slice. Pulled per-call from the
    # workouts.ai_filter helper. Falls back to an empty list
    # gracefully if the helper / models aren't importable so the
    # AI build never hard-fails on a catalog issue.
    try:
        from apps.workouts.ai_filter import candidate_exercises
        from apps.users.models import SoloProfile
        profile, _ = SoloProfile.objects.get_or_create(user=user)
        candidates = candidate_exercises(profile, max_n=200)
    except Exception:
        log.exception("AI build: catalog slice failed, sending empty candidates")
        candidates = []

    context = _build_user_context(user)
    catalog_block = json.dumps(candidates, separators=(",", ":"))
    grounding_clause = (
        "\n\nCATALOG SLICE (use ONLY these exercise_catalog_id values):\n"
        + catalog_block
        + "\n\nEvery exercise in your output MUST include an "
        "`exercise_catalog_id` that appears in the CATALOG SLICE above. "
        "If no row in the slice fits a slot, pick the closest match — "
        "do not invent IDs. The user's UI renders animations + form "
        "copy from these catalog rows, so a hallucinated ID = a "
        "blank exercise card."
    )
    if retry:
        grounding_clause += (
            "\n\nThis is a RETRY. The previous response contained "
            "hallucinated catalog IDs. Be precise this time."
        )
    system = SYSTEM_TEMPLATE.format(context=context) + grounding_clause

    body = {
        "model": ANTHROPIC_MODEL,
        # R7-DIAG fix — bumped from 2500 to 8000. With 5 training
        # days × 4-7 exercises × 3-4 sets, the JSON for `days` alone
        # can easily run 2-3k tokens. Once you add prose for name +
        # tagline + summary + progression_rule + deload_strategy
        # (~500-800 tokens), 2500 max_tokens hit `stop_reason:
        # max_tokens` before Claude even started writing `days`.
        # 8000 leaves ~5k tokens of headroom; Sonnet 4.6 supports
        # up to 64k output anyway. Cost impact is negligible
        # because we only pay for actual tokens generated.
        "max_tokens": 8000,
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
        # Loud server log so we can see *why* days is empty if it
        # ever happens again — usually `stop_reason: max_tokens`
        # (already mitigated by max_tokens=8000 above) but could
        # also be a refusal or malformed schema. Logs the keys
        # and stop_reason — NOT the full prompt or user context,
        # to avoid leaking PII to production logs.
        log.error(
            "AI build empty programme: keys=%s stop_reason=%s",
            list(programme.keys()),
            payload.get("stop_reason"),
        )
        return None, "AI returned an empty programme."

    # T3.1 — validate every catalog_id resolves. If any are
    # hallucinated, retry once with the stricter "this is a retry"
    # prompt; on second failure return a clean error so iOS shows a
    # meaningful try-again rather than silently storing a bad plan.
    if candidates:
        valid_ids = {c["id"] for c in candidates}
        bad_ids: list[int] = []
        for day in (programme.get("days") or []):
            for ex in (day.get("exercises") or []):
                cid = ex.get("exercise_catalog_id")
                if cid is None or int(cid) not in valid_ids:
                    bad_ids.append(cid)
        if bad_ids:
            log.warning(
                "AI build: %d hallucinated catalog ids (retry=%s): %s",
                len(bad_ids), retry, bad_ids[:8],
            )
            if not retry:
                # Single retry with stricter prompt.
                return _call_claude_for_programme(user, retry=True)
            # Second hallucination — surface a clean 503 so the iOS
            # error panel can prompt try-again rather than persisting
            # broken catalog refs.
            return None, (
                "AI couldn't pick from the catalog cleanly. "
                "Try again — this usually works second time."
            )

    return programme, None


# --------------------------------------------------------------------
# Preview endpoint
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_ai_build_preview(request):
    # Defence in depth — keep the catch-all so an unexpected
    # exception still produces a useful 503 instead of a silent
    # gunicorn-killed worker. Log via log.exception (auto-captures
    # full traceback) to Render's stderr stream.
    try:
        return _solo_ai_build_preview_inner(request)
    except Exception as exc:
        log.exception("AI build preview: unhandled exception for user_id=%s",
                      getattr(request.user, "id", None))
        return Response(
            {"detail": f"AI build crashed: {type(exc).__name__}: {str(exc)[:200]}"},
            status=503,
        )


def _solo_ai_build_preview_inner(request):
    user = request.user
    if user.role != User.SOLO:
        return Response({"detail": "Solo accounts only."}, status=status.HTTP_403_FORBIDDEN)

    profile, _ = SoloProfile.objects.get_or_create(user=user)
    has_ai = profile.has_ai_access

    # AI-PRO-AI-ONLY (2026-05-15) — Smart Assist is Pro AI only. No
    # free AI builds. Previously free users got ONE preview lifetime
    # as a freemium hook; per the App Store positioning rewrite, AI
    # is positioned as a Pro AI feature in the listing, so giving
    # it away for free undermines the value prop. Free users see a
    # 402 with an upgrade prompt.
    if not has_ai:
        return Response(
            {
                "detail": "Pro AI required to use Smart Assist. Start a 14-day trial.",
                "upgrade_to": "pro_ai",
            },
            status=status.HTTP_402_PAYMENT_REQUIRED,
        )

    # R7-1 — Pro AI users hit the build cap (4/month). Without this
    # a heavy power user could rebuild daily and run up the unit-
    # economics math.
    cap_ok, cap_info = enforce_cap(user, "build")
    if not cap_ok:
        return Response(cap_info["error_response"], status=cap_info["status"])

    programme, error = _call_claude_for_programme(user)
    if error:
        return Response({"detail": error}, status=503)

    # R7-1 — bump the monthly counter only AFTER a successful Pro AI
    # build, so a failed Anthropic call doesn't burn a slot.
    increment(user, "build")

    return Response({
        "programme":         programme,
        "preview_remaining": None,
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

    # AI-PRO-AI-ONLY (2026-05-15) — All AI-built assignments require
    # Pro AI. Previously the FIRST assignment was free for every user
    # as a conversion hook; that's been retired so Smart Assist
    # cleanly lives on the Pro AI tier in line with the App Store
    # positioning.
    if not profile.has_ai_access:
        return Response(
            {"detail": "Pro AI required to assign an AI-built programme.",
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
                # T3.1 — wire catalog_item FK from the AI's
                # exercise_catalog_id when present. iOS reads this
                # to render animations + form copy directly without
                # fuzzy name matching.
                catalog_item = None
                cid = ex.get("exercise_catalog_id")
                if cid:
                    try:
                        from apps.workouts.models import ExerciseCatalog
                        catalog_item = ExerciseCatalog.objects.filter(pk=int(cid)).first()
                    except Exception:
                        catalog_item = None
                rest_secs = 90
                try:
                    rs = int(ex.get("rest_seconds") or 0)
                    if 0 < rs <= 600:
                        rest_secs = rs
                except (TypeError, ValueError):
                    pass
                new_ex = Exercise.objects.create(
                    workout_day=new_day,
                    name=ex_name,
                    label=ex_label,
                    order=ex_idx,
                    provenance=Exercise.PROVENANCE_AI,
                    catalog_item=catalog_item,
                    rest_seconds=rest_secs,
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

        # AI-FREE-FIRST-GEN — flip the "first assignment used" flag
        # only after the assign has succeeded. So if the build
        # transaction fails partway, the user keeps their free
        # assignment for next try.
        _mark_assign_used(user)

    return Response({
        "ok":        True,
        "plan_id":   plan.id,
        "plan_name": plan.name,
    })
