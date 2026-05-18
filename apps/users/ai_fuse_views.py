"""T4.1 — Fused programme: workout + nutrition + supplements in one
Anthropic call.

Per AI_FUSION_ARCHITECTURE.md §2 layer 3, the user's "Build my plan"
moment is one fused artefact rather than two disconnected AI calls.
This endpoint:

  1. Pre-fetches candidate exercises (T2.3) + candidate foods (T2.4)
     for the user.
  2. Sends both to Claude in a single tool-use call.
  3. Validates that every catalog id (exercise + food) resolves —
     retries once on hallucination.
  4. Returns a FusedProgramme JSON to iOS without committing
     anything yet. The user reviews and confirms via a separate
     apply step (mirrors the existing AI workout build flow).

The fused programme includes:
  - workout_plan: days × exercises (catalog-grounded)
  - nutrition_plan.daily_macros: calories/protein/carbs/fats
  - nutrition_plan.meals: list of {slot, items[food_id+portion_g]}
  - nutrition_plan.supplement_protocol: timed pre/intra/post-workout
    items linked to training_days

Pro AI gated. Burns one `nutrition_build` cap (the workout side
doesn't burn build cap because the same call covers both — would
double-charge a user otherwise).

POST /api/users/solo/ai-fuse/
GET  body: {} (user context pulled server-side)

Response (200):
    {
      "fused_programme": { ...FusedProgramme... },
      "cap_remaining": <int>,
      "generated_at": "<iso8601>"
    }

Errors mirror solo_ai_build_preview's error mapping (401/402/429/503).
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

from apps.nutrition.models import CuratedFood
from apps.users.ai_caps import enforce_cap, increment
from apps.users.ai_pt_views import _build_user_context
from apps.users.models import SoloProfile, User
from apps.workouts.models import ExerciseCatalog

log = logging.getLogger(__name__)


ANTHROPIC_API_KEY = (
    getattr(settings, "ANTHROPIC_API_KEY", None)
    or os.environ.get("ANTHROPIC_API_KEY", "")
)
ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_URL   = "https://api.anthropic.com/v1/messages"


SYSTEM_PROMPT = """\
You are Afletics's AI personal trainer. The user has just opened
"Build my whole plan" — your job is to assemble a fused programme
covering BOTH workouts AND nutrition (meals + supplement protocol)
in a single response.

Constraints — non-negotiable:

1. **Catalog grounding.** Every exercise must include an
   `exercise_catalog_id` from the EXERCISE SLICE. Every food (in
   meals + supplements) must include a `food_id` from the FOOD
   SLICE. NEVER invent IDs. Hallucinated IDs cause the response
   to be rejected and retried.

2. **Cross-domain coherence.** The supplement_protocol's
   `on_days` array must match the workout_plan's training day
   names (e.g. if workout has "Push A" / "Pull A" / "Legs",
   supplements happen on those days, not arbitrary weekdays).

3. **Scaling.** Macros add up to within ±5% of the daily target
   computed from the user's bodyweight + goal phase.

4. **Voice.** Calm coach for any prose (rationale fields). No
   exclamation marks. Honest about trade-offs.

Submit the result via the `submit_fused_programme` tool — single
call, no prose around it.
"""


FUSED_TOOL = {
    "name": "submit_fused_programme",
    "description": (
        "Submit the fused programme. Workout + nutrition macros + "
        "meals + supplement protocol in one structured object. "
        "Every exercise_catalog_id must come from the EXERCISE SLICE; "
        "every food_id from the FOOD SLICE."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name":    {"type": "string"},
            "summary": {"type": "string"},
            "rationale": {"type": "string"},
            "workout_plan": {
                "type": "object",
                "properties": {
                    "days_per_week": {"type": "integer", "minimum": 1, "maximum": 7},
                    "weeks":         {"type": "integer", "minimum": 4, "maximum": 16},
                    "days": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "exercises": {
                                    "type": "array",
                                    "minItems": 2,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "exercise_catalog_id": {"type": "integer"},
                                            "label": {"type": "string"},
                                            "rest_seconds": {"type": "integer"},
                                            "sets": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "set_number": {"type": "integer"},
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
                "required": ["days"],
            },
            "nutrition_plan": {
                "type": "object",
                "properties": {
                    "daily_macros": {
                        "type": "object",
                        "properties": {
                            "calories": {"type": "integer"},
                            "protein":  {"type": "integer"},
                            "carbs":    {"type": "integer"},
                            "fats":     {"type": "integer"},
                        },
                        "required": ["calories", "protein", "carbs", "fats"],
                    },
                    "meals": {
                        "type": "array",
                        "minItems": 3,
                        "items": {
                            "type": "object",
                            "properties": {
                                "slot": {
                                    "type": "string",
                                    "enum": ["breakfast", "lunch", "dinner", "snack"],
                                },
                                "items": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "food_id":   {"type": "integer"},
                                            "portion_g": {"type": "number"},
                                        },
                                        "required": ["food_id", "portion_g"],
                                    },
                                },
                                "rationale": {"type": "string"},
                            },
                            "required": ["slot", "items"],
                        },
                    },
                    "supplement_protocol": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "timing": {
                                    "type": "string",
                                    "enum": ["pre_workout", "intra_workout", "post_workout"],
                                },
                                "on_day_titles": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "Day titles from workout_plan.days that "
                                        "this supplement timing applies to."
                                    ),
                                },
                                "items": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "food_id":   {"type": "integer"},
                                            "portion_g": {"type": "number"},
                                        },
                                        "required": ["food_id", "portion_g"],
                                    },
                                },
                                "rationale": {"type": "string"},
                            },
                            "required": ["timing", "items"],
                        },
                    },
                },
                "required": ["daily_macros", "meals"],
            },
        },
        "required": ["name", "workout_plan", "nutrition_plan"],
    },
}


def _hydrate_meal_items(items: list[dict]) -> list[dict]:
    """Look up CuratedFood per food_id + scale macros by portion_g."""
    ids = {int(it["food_id"]) for it in items if "food_id" in it}
    rows = {f.id: f for f in CuratedFood.objects.filter(id__in=list(ids))}
    out = []
    for it in items:
        try:
            fid = int(it["food_id"])
            grams = float(it["portion_g"])
        except (KeyError, TypeError, ValueError):
            continue
        f = rows.get(fid)
        if f is None:
            continue
        scale = grams / 100.0
        out.append({
            "food_id":   f.id,
            "name":      f.name,
            "brand":     f.brand or "",
            "portion_g": round(grams, 1),
            "calories":  round(f.kcal_per_100g    * scale, 1),
            "protein":   round(f.protein_per_100g * scale, 1),
            "carbs":     round(f.carbs_per_100g   * scale, 1),
            "fats":      round(f.fat_per_100g     * scale, 1),
        })
    return out


def _hydrate_response(programme: dict) -> dict:
    """Walk the AI response and inject names + scaled macros so iOS
    doesn't need a follow-up round-trip per food/exercise."""
    out = dict(programme)

    # Workout — exercise names already populated; also surface
    # catalog metadata if available.
    cat_ids = set()
    for day in (out.get("workout_plan", {}).get("days") or []):
        for ex in (day.get("exercises") or []):
            cid = ex.get("exercise_catalog_id")
            if cid:
                cat_ids.add(int(cid))
    cat_rows = {
        c.id: c for c in ExerciseCatalog.objects.filter(id__in=list(cat_ids))
    }
    for day in (out.get("workout_plan", {}).get("days") or []):
        for ex in (day.get("exercises") or []):
            cid = ex.get("exercise_catalog_id")
            if cid and int(cid) in cat_rows:
                c = cat_rows[int(cid)]
                ex["primary_muscle"] = c.muscle_group
                ex["equipment"]       = c.equipment
                ex["image_url"]       = c.image_url
                ex["animation_url"]   = c.animation_url

    # Nutrition meals + supplements
    nutrition = out.get("nutrition_plan") or {}
    meals_in  = nutrition.get("meals") or []
    sup_in    = nutrition.get("supplement_protocol") or []
    meals_out = []
    for m in meals_in:
        items_h = _hydrate_meal_items(m.get("items") or [])
        totals = {
            "calories": round(sum(i["calories"] for i in items_h), 1),
            "protein":  round(sum(i["protein"]  for i in items_h), 1),
            "carbs":    round(sum(i["carbs"]    for i in items_h), 1),
            "fats":     round(sum(i["fats"]     for i in items_h), 1),
        }
        meals_out.append({
            "slot":      m.get("slot", "snack"),
            "items":     items_h,
            "totals":    totals,
            "rationale": (m.get("rationale") or "")[:240],
        })
    sup_out = []
    for s in sup_in:
        items_h = _hydrate_meal_items(s.get("items") or [])
        sup_out.append({
            "timing":         s.get("timing", "post_workout"),
            "on_day_titles":  s.get("on_day_titles") or [],
            "items":          items_h,
            "rationale":      (s.get("rationale") or "")[:240],
        })
    nutrition["meals"]               = meals_out
    nutrition["supplement_protocol"] = sup_out
    out["nutrition_plan"] = nutrition
    return out


def _call_claude_for_fused(user, *, retry: bool = False):
    """Returns (programme_dict, error_string)."""
    import requests

    if not ANTHROPIC_API_KEY:
        log.error("AI fuse: ANTHROPIC_API_KEY missing")
        return None, "AI fuse temporarily unavailable."

    profile, _ = SoloProfile.objects.get_or_create(user=user)

    # Pull both candidate slices.
    try:
        from apps.workouts.ai_filter import candidate_exercises
        from apps.nutrition.ai_filter import candidate_foods
        ex_candidates = candidate_exercises(profile, max_n=180)
        # For fused we want broad food coverage across slots.
        seen: dict[int, dict] = {}
        for slot in ("breakfast", "lunch", "dinner", "snack"):
            for r in candidate_foods(profile, slot=slot, max_n=70):
                seen.setdefault(r["id"], r)
        for slot in ("pre_workout", "intra_workout", "post_workout"):
            for r in candidate_foods(profile, slot=slot, max_n=20):
                seen.setdefault(r["id"], r)
        food_candidates = list(seen.values())
    except Exception:
        log.exception("AI fuse: candidate slice failed")
        ex_candidates = []
        food_candidates = []

    context = _build_user_context(user)
    grounding = (
        "\n\nEXERCISE SLICE (use ONLY these exercise_catalog_id values):\n"
        + json.dumps(ex_candidates, separators=(",", ":"))
        + "\n\nFOOD SLICE (use ONLY these food_id values):\n"
        + json.dumps(food_candidates, separators=(",", ":"))
    )
    if retry:
        grounding += (
            "\n\nThis is a RETRY. The previous response contained "
            "hallucinated catalog ids. Pick from the slices above only."
        )

    system = SYSTEM_PROMPT + "\n\nUSER CONTEXT:\n" + context + grounding

    body = {
        "model":       ANTHROPIC_MODEL,
        "max_tokens":  8000,
        "system":      system,
        "tools":       [FUSED_TOOL],
        "tool_choice": {"type": "tool", "name": "submit_fused_programme"},
        "messages": [
            {"role": "user", "content": "Build my fused plan."},
        ],
    }
    try:
        resp = requests.post(
            ANTHROPIC_URL, json=body,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=90.0,
        )
    except requests.exceptions.Timeout:
        return None, "AI provider took too long to respond."
    except Exception as exc:
        log.exception("AI fuse request failed")
        return None, f"AI provider unreachable: {exc}"

    if resp.status_code != 200:
        log.error("AI fuse non-200: %s %s", resp.status_code, resp.text[:300])
        if resp.status_code == 401:
            return None, "AI provider rejected our API key."
        if resp.status_code == 402:
            return None, "AI provider account is out of credits."
        if resp.status_code == 429:
            return None, "AI provider rate-limited. Try again in a minute."
        return None, f"AI provider {resp.status_code}: try again."

    payload = resp.json()
    tool = next(
        (c for c in (payload.get("content") or [])
         if c.get("type") == "tool_use"
         and c.get("name") == "submit_fused_programme"),
        None,
    )
    if tool is None:
        return None, "Couldn't parse the AI response."

    programme = tool.get("input") or {}
    if not programme.get("workout_plan", {}).get("days"):
        return None, "AI returned an empty workout."
    if not programme.get("nutrition_plan", {}).get("meals"):
        return None, "AI returned no meals."

    # Validate catalog ids resolve in the slices we sent.
    valid_ex_ids   = {c["id"] for c in ex_candidates}
    valid_food_ids = {c["id"] for c in food_candidates}

    bad: list = []
    if valid_ex_ids:
        for day in (programme.get("workout_plan", {}).get("days") or []):
            for ex in (day.get("exercises") or []):
                cid = ex.get("exercise_catalog_id")
                if cid is None or int(cid) not in valid_ex_ids:
                    bad.append(("exercise", cid))
    if valid_food_ids:
        for m in (programme.get("nutrition_plan", {}).get("meals") or []):
            for it in (m.get("items") or []):
                fid = it.get("food_id")
                if fid is None or int(fid) not in valid_food_ids:
                    bad.append(("food", fid))
        for s in (programme.get("nutrition_plan", {}).get("supplement_protocol") or []):
            for it in (s.get("items") or []):
                fid = it.get("food_id")
                if fid is None or int(fid) not in valid_food_ids:
                    bad.append(("supplement_food", fid))

    if bad:
        log.warning("AI fuse: %d hallucinated ids (retry=%s): %s",
                    len(bad), retry, bad[:6])
        if not retry:
            return _call_claude_for_fused(user, retry=True)
        return None, (
            "AI couldn't pick from the catalog cleanly. "
            "Try again — usually works second time."
        )

    return programme, None


# --------------------------------------------------------------------
# Endpoint
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_ai_fuse_preview(request):
    user = request.user
    if user.role != User.SOLO:
        return Response({"detail": "Solo accounts only."}, status=403)

    profile, _ = SoloProfile.objects.get_or_create(user=user)
    if not profile.has_ai_access:
        return Response(
            {"detail": "Pro AI required for fused programme.",
             "upgrade_to": "pro_ai"},
            status=status.HTTP_402_PAYMENT_REQUIRED,
        )

    cap_ok, cap_info = enforce_cap(user, "nutrition_build")
    if not cap_ok:
        return Response(cap_info["error_response"], status=cap_info["status"])

    programme, error = _call_claude_for_fused(user)
    if error:
        return Response({"detail": error}, status=503)

    hydrated = _hydrate_response(programme)
    new_remaining = increment(user, "nutrition_build")
    return Response({
        "fused_programme": hydrated,
        "cap_remaining":   new_remaining,
        "generated_at":    timezone.now().isoformat(),
    })
