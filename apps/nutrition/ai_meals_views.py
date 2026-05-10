"""T3.2 — Catalog-grounded AI meal suggestions.

Pro-AI gated. Asks Claude to assemble 3–5 meals for the user's
saved macro target using ONLY food rows from a pre-filtered slice
of CuratedFood (T2.4 candidate filter). The model returns
`food_id` references; the iOS Nutrition tab renders the actual
food names + macros from the catalog and exposes a one-tap "Log
this meal" button per slot.

Wire shape (POST /api/nutrition/solo/ai-meals/):

  request body (all optional):
    {
      "slots": ["breakfast", "lunch", "dinner", "snack",
                "pre_workout", "post_workout"]   // default all
    }

  response:
    {
      "meals": [
        {
          "slot": "breakfast",
          "items": [
            {"food_id": 4521, "portion_g": 80,  "name": "Oats", ...},
            ...
          ],
          "totals": {"calories": 480, "protein": 32, ...},
          "rationale": "1 sentence, calm coach voice"
        },
        ...
      ],
      "cap_remaining": 5
    }

Same cap bucket as the macro variant flow (`nutrition_build`).
Validation: every returned food_id must resolve in the candidate
slice; one retry on hallucination, hard 503 on second failure.
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

from apps.users.ai_caps import enforce_cap, increment
from apps.users.models import SoloProfile, User

from .models import CuratedFood

log = logging.getLogger(__name__)


ANTHROPIC_API_KEY = (
    getattr(settings, "ANTHROPIC_API_KEY", None)
    or os.environ.get("ANTHROPIC_API_KEY", "")
)
ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_URL   = "https://api.anthropic.com/v1/messages"


SYSTEM_PROMPT = """\
You are GymFlow's nutrition coach. The user has saved macro targets;
your job is to assemble 3–5 meals for the requested slots whose
TOTAL macros land at or near the daily target. Use ONLY food rows
from the CATALOG SLICE provided — every item you return must
include a `food_id` that appears in that slice.

Rules:
- Use the user's exact macro target across all meals (within ±5%).
- Distribute roughly evenly: breakfast ~25%, lunch ~30%, dinner
  ~30%, plus snacks / pre / post-workout for the remainder.
- Respect dietary pattern + allergies + dislikes — these have
  already filtered the slice; do NOT pick a food that isn't in
  the slice as a workaround.
- Quantities in grams. Round to nearest 10g for staples (rice,
  oats, chicken etc.), nearest 5g for protein powders / oils.
- Rationale per meal: ONE sentence, calm coach voice. Mention the
  user's pattern, slot timing, or what makes this combo work.
  No exclamation marks, no hype.

Output via the `submit_meal_plan` tool. Do not write prose around
the call.
"""


MEAL_TOOL = {
    "name": "submit_meal_plan",
    "description": (
        "Submit the meal plan. Each meal references food rows by "
        "their `food_id` from the CATALOG SLICE only. Hallucinated "
        "IDs cause the build to be rejected and retried."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "meals": {
                "type": "array",
                "minItems": 1,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "properties": {
                        "slot": {
                            "type": "string",
                            "enum": [
                                "breakfast", "lunch", "dinner", "snack",
                                "pre_workout", "intra_workout", "post_workout",
                            ],
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
        },
        "required": ["meals"],
    },
}


def _build_meal_user_context(profile: SoloProfile) -> str:
    bw = profile.bodyweight_kg or 75.0
    return (
        f"Bodyweight: {bw:.1f} kg\n"
        f"Daily targets: {profile.target_calories} kcal, "
        f"{profile.target_protein} g protein, "
        f"{profile.target_carbs} g carbs, "
        f"{profile.target_fats} g fat\n"
        f"Dietary pattern: {profile.dietary_pattern or 'unspecified'}\n"
        f"Restrictions: {', '.join(profile.food_restrictions) or 'none'}\n"
        f"Dislikes: {', '.join(profile.food_dislikes) or 'none'}\n"
        f"Cooking comfort: {profile.cooking_comfort or 'unspecified'}\n"
    )


def _call_claude_for_meals(profile: SoloProfile, slots: list[str], *, retry: bool = False):
    """Returns (meals_json, error_string)."""
    import requests

    if not ANTHROPIC_API_KEY:
        log.error("AI meals: ANTHROPIC_API_KEY missing")
        return None, "AI meals temporarily unavailable."

    # T3.2 — pull candidate foods. Slot-aware: per-slot we cap to
    # the top ~80 rows so the union across requested slots stays
    # under ~300 (at the budget we set in T2.4). Slot dedupes by
    # food_id so a row in two categories doesn't double-count.
    try:
        from .ai_filter import candidate_foods
        seen: dict[int, dict] = {}
        per_slot_n = max(40, 240 // max(1, len(slots)))
        for slot in slots:
            rows = candidate_foods(profile, slot=slot, max_n=per_slot_n)
            for r in rows:
                seen.setdefault(r["id"], r)
        candidates = list(seen.values())
    except Exception:
        log.exception("AI meals: catalog slice failed")
        candidates = []

    catalog_block = json.dumps(candidates, separators=(",", ":"))
    grounding = (
        "\n\nCATALOG SLICE (use ONLY these food_id values):\n"
        + catalog_block
        + "\n\nRequested slots: " + ", ".join(slots)
    )
    if retry:
        grounding += (
            "\n\nThis is a RETRY. The previous response contained "
            "food_id values not in the slice. Be precise this time."
        )
    system = SYSTEM_PROMPT + "\n\nUser:\n" + _build_meal_user_context(profile) + grounding

    body = {
        "model":       ANTHROPIC_MODEL,
        "max_tokens":  4000,
        "system":      system,
        "tools":       [MEAL_TOOL],
        "tool_choice": {"type": "tool", "name": "submit_meal_plan"},
        "messages": [
            {"role": "user", "content": "Build me a day of meals."},
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
            timeout=70.0,
        )
    except requests.exceptions.Timeout:
        return None, "AI provider took too long to respond."
    except Exception as exc:
        log.exception("AI meals request failed")
        return None, f"AI provider unreachable: {exc}"

    if resp.status_code != 200:
        log.error("AI meals non-200: %s %s", resp.status_code, resp.text[:300])
        return None, f"AI provider {resp.status_code}: try again in a minute."

    payload = resp.json()
    tool = next(
        (c for c in (payload.get("content") or [])
         if c.get("type") == "tool_use"
         and c.get("name") == "submit_meal_plan"),
        None,
    )
    if tool is None:
        return None, "Couldn't parse the AI response."

    meals = (tool.get("input") or {}).get("meals") or []

    # Validate every food_id resolves in the candidate slice.
    if candidates:
        valid_ids = {c["id"] for c in candidates}
        bad: list[int] = []
        for m in meals:
            for it in (m.get("items") or []):
                fid = it.get("food_id")
                if fid is None or int(fid) not in valid_ids:
                    bad.append(fid)
        if bad:
            log.warning("AI meals: %d hallucinated ids (retry=%s): %s",
                        len(bad), retry, bad[:8])
            if not retry:
                return _call_claude_for_meals(profile, slots, retry=True)
            return None, "AI couldn't pick from the catalog cleanly. Try again."

    return meals, None


def _hydrate_meals(meals: list[dict]) -> list[dict]:
    """Resolve each food_id → name + macros + portion_unit from the
    catalog so iOS doesn't need a follow-up round-trip per food."""
    all_ids: set[int] = set()
    for m in meals:
        for it in (m.get("items") or []):
            try:
                all_ids.add(int(it["food_id"]))
            except (KeyError, TypeError, ValueError):
                pass
    food_rows = {
        r.id: r for r in CuratedFood.objects.filter(id__in=list(all_ids))
    }

    hydrated = []
    for m in meals:
        items_out = []
        totals = {"calories": 0.0, "protein": 0.0, "carbs": 0.0, "fats": 0.0}
        for it in (m.get("items") or []):
            try:
                fid = int(it["food_id"])
                grams = float(it["portion_g"])
            except (KeyError, TypeError, ValueError):
                continue
            f = food_rows.get(fid)
            if f is None:
                continue
            scale = grams / 100.0
            kcal_i  = round(f.kcal_per_100g    * scale, 1)
            prot_i  = round(f.protein_per_100g * scale, 1)
            carb_i  = round(f.carbs_per_100g   * scale, 1)
            fat_i   = round(f.fat_per_100g     * scale, 1)
            items_out.append({
                "food_id":      f.id,
                "name":         f.name,
                "brand":        f.brand or "",
                "portion_g":    round(grams, 1),
                "calories":     kcal_i,
                "protein":      prot_i,
                "carbs":        carb_i,
                "fats":         fat_i,
            })
            totals["calories"] += kcal_i
            totals["protein"]  += prot_i
            totals["carbs"]    += carb_i
            totals["fats"]     += fat_i
        hydrated.append({
            "slot":      m.get("slot", "snack"),
            "items":     items_out,
            "totals":    {k: round(v, 1) for k, v in totals.items()},
            "rationale": (m.get("rationale") or "")[:240],
        })
    return hydrated


# --------------------------------------------------------------------
# Endpoint
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_ai_meals_suggest(request):
    user = request.user
    if user.role != User.SOLO:
        return Response({"detail": "Solo accounts only."},
                        status=status.HTTP_403_FORBIDDEN)

    profile, _ = SoloProfile.objects.get_or_create(user=user)
    if not (profile.target_calories or 0) > 0:
        return Response(
            {"detail": "Set your macro targets first."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    has_ai = profile.has_ai_access
    if not has_ai:
        return Response(
            {"detail": "Pro AI required for meal suggestions.",
             "upgrade_to": "pro_ai"},
            status=status.HTTP_402_PAYMENT_REQUIRED,
        )

    cap_ok, cap_info = enforce_cap(user, "nutrition_build")
    if not cap_ok:
        return Response(cap_info["error_response"], status=cap_info["status"])

    body = request.data or {}
    slots = body.get("slots") or [
        "breakfast", "lunch", "dinner", "snack",
    ]
    if not isinstance(slots, list) or not slots:
        return Response({"detail": "slots must be a non-empty list."},
                        status=400)

    meals, error = _call_claude_for_meals(profile, slots)
    if error:
        return Response({"detail": error}, status=503)

    hydrated = _hydrate_meals(meals or [])
    new_remaining = increment(user, "nutrition_build")
    return Response({
        "meals":         hydrated,
        "cap_remaining": new_remaining,
        "generated_at":  timezone.now().isoformat(),
    })
