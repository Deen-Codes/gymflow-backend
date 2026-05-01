"""
Phase A — AI PT tool spec + dispatch.

Five tools the AI PT can call inside a chat turn. Three are
read-only (the AI can call them as often as it needs without
side-effects); two are PROPOSAL tools (the AI can only call ONE
proposal per turn, and it doesn't apply anything — it just writes
a row to the audit table; the iOS Apply button hits a separate
endpoint to commit).

Tool definitions follow Anthropic's tool-use format.
See: https://docs.anthropic.com/en/docs/build-with-claude/tool-use

Why proposal-only (not "AI applies directly"):
  - Behavioural: SDT autonomy support — user decides, not AI.
  - Trust: every change has a card the user explicitly approves.
  - Reversibility: applied changes carry the original value, so
    Profile → "AI changes" can offer revert.
  - Safety: floors are enforced twice — at proposal time AND at
    apply time (defense in depth).

The dispatcher returns (tool_result_str, optional_proposal_dict).
The chat endpoint surfaces the proposal dict on the wire so iOS
can render the proposal card. Read-only tools return None for the
proposal slot.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from django.utils import timezone

from .models import SoloProfile
from .mutation_models import (
    MutationStatus, WorkoutMutation, NutritionMutation, CardioMutation,
)

log = logging.getLogger(__name__)


# ====================================================================
# Tool definitions — sent to Anthropic in every chat turn.
# ====================================================================
#
# Token cost ≈ 600. We trim payload schemas to the essential shape;
# the system prompt has the editorial context (when to call which
# tool, refusal patterns, single-proposal-per-turn rule).
# ====================================================================


TOOLS = [
    # --- READ-ONLY ---------------------------------------------------
    {
        "name": "get_active_programme_detail",
        "description": (
            "Fetch the user's currently assigned workout plan in full "
            "(days, exercises, set/rep schemes). Use when the user asks "
            "specific questions about their plan structure that aren't "
            "already in the USER CONTEXT block."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_recent_sessions",
        "description": (
            "Fetch the user's last N completed workout sessions, including "
            "exercises, sets, reps, weights, and any RPE feedback. Use when "
            "the user asks about their recent training ('how was my last "
            "leg day', 'have I been progressing on bench')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {
                    "type": "integer",
                    "description": "How many recent sessions to fetch (1-10).",
                    "minimum": 1, "maximum": 10,
                },
            },
            "required": ["n"],
        },
    },
    {
        "name": "get_macro_history",
        "description": (
            "Fetch the user's logged macros over the last N days plus their "
            "current targets. Use when the user asks about adherence ('have "
            "I been hitting my protein this week') or wants a data-grounded "
            "macro adjustment recommendation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Days of history to fetch (1-28).",
                    "minimum": 1, "maximum": 28,
                },
            },
            "required": ["days"],
        },
    },
    # --- PROPOSAL TOOLS ---------------------------------------------
    {
        "name": "propose_workout_mutation",
        "description": (
            "Propose a single change to the user's workout plan. Doesn't "
            "apply anything — the user clicks Apply on the proposal card "
            "in the chat. ONE call per chat turn. The 'rationale' field "
            "appears verbatim on the proposal card, so write it for the "
            "user, not for yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "swap_exercise", "change_set_scheme",
                        "reorder_days", "deload_week",
                        "add_day", "remove_day",
                    ],
                    "description": "Which kind of mutation.",
                },
                "summary": {
                    "type": "string",
                    "description": (
                        "One-line headline for the proposal card "
                        "(e.g. 'Swap dumbbell rows for cable rows')."
                    ),
                },
                "rationale": {
                    "type": "string",
                    "description": (
                        "2-3 sentences. Calm coach voice. State the "
                        "trade-off honestly."
                    ),
                },
                "payload": {
                    "type": "object",
                    "description": (
                        "Kind-specific details. The apply handler is "
                        "tolerant of partial payloads — current_exercise_name "
                        "is enough to identify a swap target; exact IDs are "
                        "preferred when known. Examples:\n"
                        "  swap_exercise={current_exercise_name, "
                        "new_exercise_name, day_id?, exercise_id?};\n"
                        "  change_set_scheme={current_exercise_name OR "
                        "exercise_id, sets?, reps?, rest_seconds?};\n"
                        "  deload_week={scope: 'this_week'|'next_week'};\n"
                        "  reorder_days={new_order: [day_id,...]};\n"
                        "  remove_day={day_id};\n"
                        "  add_day=(prefer the custom-builder UI; this kind "
                        "isn't auto-applicable yet).\n"
                        "REST-ASSIGNABLE: rest_seconds is clamped 0-600s. "
                        "Use this when the user complains about pace "
                        "(\"I'm rushing between sets\") or wants more "
                        "between heavy compounds."
                    ),
                },
            },
            "required": ["kind", "summary", "rationale", "payload"],
        },
    },
    {
        "name": "propose_cardio_mutation",
        "description": (
            "Propose a single change to the user's cardio "
            "prescription. Same propose-then-apply pattern as "
            "the workout / nutrition mutations. ONE call per chat "
            "turn. Use this when the user asks about cardio, "
            "running pace, intervals, or wants to swap modalities."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "assign_session_type",
                        "adjust_volume",
                        "swap_modality",
                        "change_priority",
                    ],
                    "description": "Which kind of cardio change.",
                },
                "summary": {"type": "string"},
                "rationale": {"type": "string"},
                "payload": {
                    "type": "object",
                    "description": (
                        "Kind-specific. Examples:\n"
                        "  assign_session_type={"
                        "session: 'z2'|'threshold'|'intervals'|'long', "
                        "duration_min, frequency_per_week};\n"
                        "  adjust_volume={"
                        "minutes_per_week OR distance_km_per_week};\n"
                        "  swap_modality={"
                        "from: 'run'|'bike'|'row'|'walk'|'elliptical', "
                        "to: same enum};\n"
                        "  change_priority={"
                        "priority: 'runner_first'|'lifter_first'|'balanced'}."
                    ),
                },
            },
            "required": ["kind", "summary", "rationale", "payload"],
        },
    },
    {
        "name": "propose_nutrition_mutation",
        "description": (
            "Propose a single change to the user's nutrition (macro "
            "targets, dietary preferences, meal frequency, or phase). "
            "Doesn't apply anything — the user clicks Apply on the "
            "proposal card. ONE call per chat turn."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "adjust_macros", "swap_preference",
                        "change_meal_freq", "change_goal_phase",
                    ],
                    "description": "Which kind of mutation.",
                },
                "summary": {"type": "string"},
                "rationale": {"type": "string"},
                "payload": {
                    "type": "object",
                    "description": (
                        "Kind-specific. Examples: "
                        "adjust_macros={calories, protein_g, carbs_g, fats_g}; "
                        "swap_preference={exclude:[...], include:[...]}; "
                        "change_meal_freq={meals_per_day: 2-6}; "
                        "change_goal_phase={phase: 'cut'|'maintenance'|'bulk'}."
                    ),
                },
            },
            "required": ["kind", "summary", "rationale", "payload"],
        },
    },
]


# ====================================================================
# Safety floors — enforced at PROPOSAL time, again at APPLY time.
# ====================================================================
#
# Returning a refusal payload from the tool keeps the AI inside the
# loop and lets it rephrase. The AI sees `{"refused": true, ...}`
# and surfaces the reason in chat naturally.
# ====================================================================


def _refusal(reason: str, detail: str) -> dict:
    """Structured refusal that the AI can read and rephrase from."""
    return {"refused": True, "reason": reason, "detail": detail}


def _check_macro_floors(profile: SoloProfile, calories, protein_g, carbs_g, fats_g):
    """Returns a refusal dict on breach, or None on pass.

    KB-grounded floors (AI_PT_KNOWLEDGE_BASE.md §4.2-4.3):
      - Protein 1.4 g/kg/day general; 1.6 g/kg/day MINIMUM if any
        muscle/strength goal.
      - Fat ≥ 0.8 g/kg/day.
      - Calorie floor 1800 kcal men / 1500 kcal women.
      - Cut depth ≤ 25% from maintenance (2700 kcal/kg-target).
    """
    bw = profile.bodyweight_kg or 75.0
    goals = profile.goals or []
    is_muscle_seeking = any(
        g in goals for g in ("build_muscle", "get_stronger", "train_for_sport")
    )
    protein_floor_per_kg = 1.6 if is_muscle_seeking else 1.4
    protein_floor_g = round(bw * protein_floor_per_kg)
    fat_floor_g     = round(bw * 0.8)
    # Calorie floor — assume men by default (no sex field). Conservative.
    # If the user is female + flagged in profile, this becomes 1500. For
    # tonight we use the safer (higher) floor as universal default.
    kcal_floor      = 1800

    if int(protein_g) < protein_floor_g:
        return _refusal(
            "protein_floor_breach",
            f"Protein at {protein_g}g is below the "
            f"{protein_floor_g}g floor (≥{protein_floor_per_kg} g/kg "
            f"for {bw:.0f}kg bodyweight at this goal stack).",
        )
    if int(fats_g) < fat_floor_g:
        return _refusal(
            "fat_floor_breach",
            f"Fat at {fats_g}g is below the {fat_floor_g}g floor "
            f"(0.8 g/kg minimum for hormone production).",
        )
    if int(calories) < kcal_floor:
        return _refusal(
            "kcal_floor_breach",
            f"Calories at {calories} kcal/day are below the "
            f"{kcal_floor} kcal floor. Below this without medical "
            f"supervision is unsafe.",
        )
    return None


def _check_phase_coherence(profile: SoloProfile, target_phase: str):
    """Phase must be coherent with stated goals."""
    goals = profile.goals or []
    if target_phase == "bulk" and "lose_fat" in goals and "build_muscle" not in goals:
        return _refusal(
            "phase_goal_mismatch",
            "Bulking while goal=lose_fat would work against the user's "
            "stated goal. Suggest maintenance instead, or have the user "
            "update their goals from Profile first.",
        )
    if target_phase == "cut" and "build_muscle" in goals and "lose_fat" not in goals:
        return _refusal(
            "phase_goal_mismatch",
            "Cutting while goal=build_muscle (without lose_fat) would "
            "work against muscle gain. Suggest maintenance.",
        )
    if target_phase not in ("cut", "maintenance", "bulk"):
        return _refusal("invalid_phase", f"Phase '{target_phase}' isn't valid.")
    return None


# ====================================================================
# Tool dispatch — called by the agentic loop in ai_pt_views.py.
# ====================================================================


def dispatch_tool(
    user, tool_name: str, tool_input: dict, *,
    chat_turn_ref: str = "",
    proposals_this_turn: int = 0,
):
    """Execute one tool call. Returns (result_dict, proposal_payload?).

    `result_dict` is what the AI sees as the tool_result content.
    `proposal_payload` (when non-None) is what iOS renders as the
    proposal card — the chat endpoint folds it into the events
    array on the wire.
    """
    profile, _ = SoloProfile.objects.get_or_create(user=user)

    if tool_name == "get_active_programme_detail":
        return _tool_get_active_programme_detail(profile), None

    if tool_name == "get_recent_sessions":
        n = int(tool_input.get("n", 5))
        return _tool_get_recent_sessions(user, n), None

    if tool_name == "get_macro_history":
        days = int(tool_input.get("days", 7))
        return _tool_get_macro_history(user, profile, days), None

    if tool_name == "propose_workout_mutation":
        if proposals_this_turn >= 1:
            return (_refusal(
                "multi_proposal_blocked",
                "Only one proposal per chat turn. The previous proposal "
                "is already on the user's screen.",
            ), None)
        return _tool_propose_workout_mutation(user, profile, tool_input, chat_turn_ref)

    if tool_name == "propose_nutrition_mutation":
        if proposals_this_turn >= 1:
            return (_refusal(
                "multi_proposal_blocked",
                "Only one proposal per chat turn. The previous proposal "
                "is already on the user's screen.",
            ), None)
        return _tool_propose_nutrition_mutation(user, profile, tool_input, chat_turn_ref)

    if tool_name == "propose_cardio_mutation":
        if proposals_this_turn >= 1:
            return (_refusal(
                "multi_proposal_blocked",
                "Only one proposal per chat turn. The previous proposal "
                "is already on the user's screen.",
            ), None)
        return _tool_propose_cardio_mutation(user, profile, tool_input, chat_turn_ref)

    return ({"error": f"unknown_tool: {tool_name}"}, None)


# --------------------------------------------------------------------
# Read-only tool handlers
# --------------------------------------------------------------------


def _tool_get_active_programme_detail(profile: SoloProfile) -> dict:
    """Return the user's active plan in a shape the AI can reason
    about + reference by id when it proposes a swap.

    Schema: WorkoutPlan → WorkoutDay (related_name="days") →
    Exercise (related_name="exercises") → ExerciseSetTarget
    (related_name="sets"). Sets/reps live on the per-set rows;
    we surface them as a compact set count + first-set reps
    string for the AI to anchor on.
    """
    plan = profile.assigned_workout_plan
    if plan is None:
        return {"plan": None, "note": "No active programme."}
    days_payload = []
    for day in plan.days.all().order_by("order"):
        ex_payload = []
        for ex in day.exercises.all().order_by("order"):
            sets = list(ex.sets.all().order_by("set_number"))
            ex_payload.append({
                "id":       ex.id,
                "name":     ex.name,
                "sets":     len(sets),
                # Reps as a representative string. Most plans use the
                # same rep target across sets; if not, the AI can
                # call again or work with the first one.
                "reps":     sets[0].reps if sets else "",
            })
        days_payload.append({
            "id":    day.id,
            "title": day.title,
            "exercises": ex_payload,
        })
    return {
        "plan_id":   plan.id,
        "plan_name": plan.name,
        "days":      days_payload,
    }


def _tool_get_recent_sessions(user, n: int) -> dict:
    from apps.workouts.models import WorkoutSession
    n = max(1, min(n, 10))
    sessions = (
        WorkoutSession.objects
        .filter(user=user, is_complete=True)
        .select_related("workout_day")
        .order_by("-completed_at")[:n]
    )
    payload = []
    for s in sessions:
        sets = []
        # Best-effort — schema may vary; we only surface what's stable.
        for log in getattr(s, "exercise_logs", []).all() if hasattr(s, "exercise_logs") else []:
            sets.append({
                "exercise": getattr(log, "exercise_name", None),
                "weight":   getattr(log, "weight", None),
                "reps":     getattr(log, "reps", None),
                "rir":      getattr(log, "rir", None),
            })
        payload.append({
            "date":  s.completed_at.strftime("%Y-%m-%d") if s.completed_at else None,
            "title": s.workout_day.title if s.workout_day_id else None,
            "sets":  sets,
        })
    return {"sessions": payload}


def _tool_get_macro_history(user, profile: SoloProfile, days: int) -> dict:
    from apps.nutrition.models import SoloFoodLogEntry
    from collections import defaultdict
    days = max(1, min(days, 28))
    cutoff = timezone.localdate() - timedelta(days=days)
    rows = SoloFoodLogEntry.objects.filter(
        user=user, consumed_on__gte=cutoff,
    )
    per_day = defaultdict(lambda: {"kcal": 0.0, "p": 0.0, "c": 0.0, "f": 0.0})
    for r in rows:
        d = per_day[r.consumed_on.isoformat()]
        d["kcal"] += r.calories
        d["p"]    += r.protein
        d["c"]    += r.carbs
        d["f"]    += r.fats
    return {
        "targets": {
            "calories": profile.target_calories,
            "protein":  profile.target_protein,
            "carbs":    profile.target_carbs,
            "fats":     profile.target_fats,
        },
        "per_day": dict(per_day),
    }


# --------------------------------------------------------------------
# Proposal tool handlers
# --------------------------------------------------------------------


def _tool_propose_workout_mutation(user, profile: SoloProfile, tool_input: dict, chat_turn_ref: str):
    kind     = tool_input.get("kind", "")
    summary  = (tool_input.get("summary") or "").strip()[:200]
    rationale = (tool_input.get("rationale") or "").strip()[:500]
    payload   = tool_input.get("payload") or {}

    valid_kinds = {k for k, _ in WorkoutMutation.KIND_CHOICES}
    if kind not in valid_kinds:
        return (_refusal("invalid_kind", f"Unknown workout mutation kind: {kind}"), None)
    if not summary or not rationale:
        return (_refusal(
            "missing_explanation",
            "Both summary and rationale are required so the user knows "
            "what's changing and why.",
        ), None)

    plan = profile.assigned_workout_plan
    if plan is None:
        return (_refusal(
            "no_active_plan",
            "User has no active programme — can't propose a mutation. "
            "Direct them to pick a programme from the catalog first.",
        ), None)

    # Capture the original_value snapshot — kind-specific. Best-effort;
    # used for revert + audit display.
    original = _snapshot_for_workout_kind(plan, kind, payload)

    mutation = WorkoutMutation.objects.create(
        user=user,
        kind=kind,
        status=MutationStatus.PROPOSED,
        original_value=original,
        new_value=payload,
        ai_rationale=rationale,
        chat_turn_ref=chat_turn_ref or "",
    )
    proposal = {
        "kind":          "workout",
        "id":            mutation.id,
        "mutation_kind": kind,
        "summary":       summary,
        "rationale":     rationale,
        "payload":       payload,
    }
    # Tool-result the AI sees — keep terse so it doesn't repeat the
    # rationale verbatim in chat (the proposal card already shows it).
    return ({
        "proposed":     True,
        "proposal_id":  mutation.id,
        "kind":         kind,
        "summary":      summary,
    }, proposal)


def _tool_propose_nutrition_mutation(user, profile: SoloProfile, tool_input: dict, chat_turn_ref: str):
    kind      = tool_input.get("kind", "")
    summary   = (tool_input.get("summary") or "").strip()[:200]
    rationale = (tool_input.get("rationale") or "").strip()[:500]
    payload   = tool_input.get("payload") or {}

    valid_kinds = {k for k, _ in NutritionMutation.KIND_CHOICES}
    if kind not in valid_kinds:
        return (_refusal("invalid_kind", f"Unknown nutrition mutation kind: {kind}"), None)
    if not summary or not rationale:
        return (_refusal(
            "missing_explanation",
            "Both summary and rationale are required.",
        ), None)

    # Safety floors — kind-specific.
    if kind == "adjust_macros":
        floor_ref = _check_macro_floors(
            profile,
            calories=payload.get("calories", profile.target_calories),
            protein_g=payload.get("protein_g", profile.target_protein),
            carbs_g=payload.get("carbs_g", profile.target_carbs),
            fats_g=payload.get("fats_g", profile.target_fats),
        )
        if floor_ref:
            return (floor_ref, None)
    elif kind == "change_goal_phase":
        floor_ref = _check_phase_coherence(profile, payload.get("phase", ""))
        if floor_ref:
            return (floor_ref, None)
    elif kind == "change_meal_freq":
        meals = int(payload.get("meals_per_day", 0))
        if meals < 2 or meals > 6:
            return (_refusal(
                "invalid_meal_freq",
                "Meals per day must be 2-6. Below 2 makes per-meal protein "
                "exceed the per-meal MPS ceiling; above 6 is impractical.",
            ), None)

    original = _snapshot_for_nutrition_kind(profile, kind)

    mutation = NutritionMutation.objects.create(
        user=user,
        kind=kind,
        status=MutationStatus.PROPOSED,
        original_value=original,
        new_value=payload,
        ai_rationale=rationale,
        chat_turn_ref=chat_turn_ref or "",
    )
    proposal = {
        "kind":          "nutrition",
        "id":            mutation.id,
        "mutation_kind": kind,
        "summary":       summary,
        "rationale":     rationale,
        "payload":       payload,
    }
    return ({
        "proposed":    True,
        "proposal_id": mutation.id,
        "kind":        kind,
        "summary":     summary,
    }, proposal)


# --------------------------------------------------------------------
# CARDIO-MUTATIONS — propose handler. Mirrors the workout +
# nutrition propose patterns. No safety floors yet (cardio
# safety floors are looser than nutrition; the AI's KB-driven
# voice is the primary guardrail). Future enhancement: refuse
# changes that conflict with the user's `goals` (e.g. proposing
# 5+ Z2 hours/wk for a `lose_fat` user is fine; proposing 90-min
# tempo runs for a beginner is dangerous).
# --------------------------------------------------------------------


def _tool_propose_cardio_mutation(user, profile: SoloProfile, tool_input: dict, chat_turn_ref: str):
    kind      = tool_input.get("kind", "")
    summary   = (tool_input.get("summary") or "").strip()[:200]
    rationale = (tool_input.get("rationale") or "").strip()[:500]
    payload   = tool_input.get("payload") or {}

    valid_kinds = {k for k, _ in CardioMutation.KIND_CHOICES}
    if kind not in valid_kinds:
        return (_refusal("invalid_kind", f"Unknown cardio mutation kind: {kind}"), None)
    if not summary or not rationale:
        return (_refusal(
            "missing_explanation",
            "Both summary and rationale are required.",
        ), None)

    # Snapshot — for now just store an empty dict; a future revert
    # surface can read the user's previous cardio prescription
    # from notification_prefs once that lives somewhere stable.
    mutation = CardioMutation.objects.create(
        user=user,
        kind=kind,
        status=MutationStatus.PROPOSED,
        original_value={},
        new_value=payload,
        ai_rationale=rationale,
        chat_turn_ref=chat_turn_ref or "",
    )
    proposal = {
        "kind":          "cardio",
        "id":            mutation.id,
        "mutation_kind": kind,
        "summary":       summary,
        "rationale":     rationale,
        "payload":       payload,
    }
    return ({
        "proposed":    True,
        "proposal_id": mutation.id,
        "kind":        kind,
        "summary":     summary,
    }, proposal)


# --------------------------------------------------------------------
# Snapshot helpers — capture the canonical value the mutation will
# replace, so revert / audit can show a clean diff.
# --------------------------------------------------------------------


def _snapshot_for_workout_kind(plan, kind: str, payload: dict) -> dict:
    """Capture the canonical value the mutation will replace, so
    revert / audit can show a clean diff. Tolerant of partial AI
    payloads — when an `exercise_id` is missing we fall back to
    matching by `current_exercise_name`.
    """
    if kind == "swap_exercise":
        ex_id        = payload.get("exercise_id")
        current_name = (payload.get("current_exercise_name") or "").strip().lower()
        for day in plan.days.all():
            for ex in day.exercises.all():
                hit = (ex_id and ex.id == ex_id) or (
                    current_name and ex.name.lower() == current_name
                )
                if hit:
                    set_count = ex.sets.count()
                    first_set = ex.sets.order_by("set_number").first()
                    return {
                        "day_id":        day.id,
                        "exercise_id":   ex.id,
                        "exercise_name": ex.name,
                        "sets":          set_count,
                        "reps":          first_set.reps if first_set else "",
                    }
    if kind == "reorder_days":
        return {
            "current_order": [d.id for d in plan.days.all().order_by("order")],
        }
    # Other kinds: snapshot is informational; payload itself is enough
    # for revert in most cases. Empty dict is fine.
    return {}


def _snapshot_for_nutrition_kind(profile: SoloProfile, kind: str) -> dict:
    if kind == "adjust_macros":
        return {
            "calories": profile.target_calories,
            "protein_g": profile.target_protein,
            "carbs_g":  profile.target_carbs,
            "fats_g":   profile.target_fats,
        }
    if kind == "change_goal_phase":
        return {"phase": profile.phase}
    if kind == "change_meal_freq":
        # Meal frequency isn't stored on SoloProfile yet — store the
        # current default for symmetry.
        return {"meals_per_day": 4}
    if kind == "swap_preference":
        # Preferences live in notification_prefs as a JSON list. Best
        # effort.
        prefs = profile.user.notification_prefs or {}
        return {
            "exclude": prefs.get("dietary_exclude", []),
            "include": prefs.get("dietary_include", []),
        }
    return {}
