"""
Phase C — CHECKIN-APPLIES (R7-4).

After a user submits a weekly check-in, this module runs a one-shot
AI pass that:
  1. Builds a context block (profile + the check-in's answers).
  2. Calls Claude with the existing Phase A propose_* tools but caps
     the agentic loop at 1 round — the AI either proposes ZERO or
     ONE mutation, then we stop.
  3. Persists the proposal as a WorkoutMutation / NutritionMutation /
     CardioMutation row with status="proposed".
  4. Returns proposal IDs + summaries to iOS so the post-submit
     screen can render Phase A's MutationProposalCard.

Decoupled from the submit endpoint by design: submit returns 201
fast; iOS calls this endpoint separately to surface suggestions.
That way a slow / failed AI call never blocks the user closing
their check-in.

Apply path is unchanged — the iOS Apply button hits the same
/api/users/solo/ai-pt/mutations/<id>/apply/ endpoint Phase A
already exposes.
"""
from __future__ import annotations

import json
import logging
import os

import requests
from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.progress.models import (
    CheckInSubmission, CheckInAnswer,
)
from .ai_pt_tools import TOOLS, dispatch_tool

log = logging.getLogger(__name__)


ANTHROPIC_API_KEY = getattr(settings, "ANTHROPIC_API_KEY", None) or os.environ.get(
    "ANTHROPIC_API_KEY", "",
)
ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MAX_OUTPUT_TOKENS = 1200


SYSTEM_PROMPT = """\
You are GymFlow's AI coach reviewing a single weekly check-in
submission. The user has just answered a structured form about how
their last week went. Your job: decide if their answers warrant
ONE small adjustment to their programme — and if so, propose it
via the appropriate `propose_*_mutation` tool.

Hard rules:
- Output ONE proposal maximum per check-in. If two issues stand out,
  pick the one that matters most this week.
- If nothing in the check-in justifies a change, output a short
  text response explaining why (no tool call). Examples: "Your
  energy and sleep both dropped — let's hold the plan steady and
  prioritise rest this week" or "Everything's tracking well, no
  changes needed."
- Safety floors: never propose a mutation that would push protein
  below 1.2g/kg, calories below 1500 (women) / 1800 (men), or sets
  per muscle below 10/week. The Phase A tool handlers re-check
  these at apply time, but you should not propose them in the
  first place.
- Stay calm and warm. The user just gave you 5 minutes of
  introspective effort — meet that with a real, considered
  response, not boilerplate.

You see the same USER CONTEXT block as the chat coach. The
CHECK-IN block below contains this submission's answers.
"""


def _format_checkin_block(submission: CheckInSubmission) -> str:
    """Render the submission's answers as a compact text block.
    Keep it short — every line costs tokens."""
    lines = [f"CHECK-IN — submitted {submission.submitted_at:%b %d}"]
    answers = (
        CheckInAnswer.objects
        .filter(submission=submission)
        .select_related("question")
        .order_by("question__order")
    )
    for ans in answers:
        q = ans.question
        if q is None:
            continue
        # Polymorphic value pick — first non-empty wins.
        val = (
            ans.value_text
            or (str(ans.value_number) if ans.value_number is not None else "")
            or ("yes" if ans.value_yes_no is True else
                "no" if ans.value_yes_no is False else "")
            or (ans.value_option.label if getattr(ans, "value_option_id", None) else "")
        )
        if not val:
            continue
        # Trim to a reasonable size per answer so a verbose user
        # doesn't blow our token budget.
        val_str = str(val)[:300]
        lines.append(f"- {q.label}: {val_str}")
    return "\n".join(lines)


def _build_context_with_checkin(user, submission: CheckInSubmission) -> str:
    """User profile + this check-in's answers, concatenated."""
    # Re-use the chat coach's context builder for consistency. Lazy
    # import to avoid the module-load circular dependency that would
    # otherwise come from ai_pt_views importing this file.
    from .ai_pt_views import _build_user_context
    base = _build_user_context(user)
    return f"{base}\n\n{_format_checkin_block(submission)}"


def _call_anthropic_once(system: str, messages: list) -> dict:
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system": system,
        "messages": messages,
        "tools": TOOLS,
    }
    response = requests.post(
        ANTHROPIC_URL, headers=headers, json=body, timeout=60,
    )
    response.raise_for_status()
    return response.json()


@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def checkin_suggestions(request, submission_id):
    """POST /api/users/solo/checkin-suggestions/<submission_id>/

    Idempotent. If proposals already exist tied to this check-in
    (we tag them via chat_turn_ref="checkin:<id>"), return them
    instead of calling the AI again.
    """
    user = request.user
    submission = get_object_or_404(
        CheckInSubmission, id=submission_id, client=user,
    )

    chat_turn_ref = f"checkin:{submission.id}"

    # Idempotency — if we've already generated proposals for this
    # submission, return them. This means iOS can re-trigger this
    # endpoint on view-foreground without burning extra AI cap.
    from .mutation_models import (
        WorkoutMutation, NutritionMutation, CardioMutation,
    )
    existing = []
    for Model, kind in [
        (WorkoutMutation, "workout"),
        (NutritionMutation, "nutrition"),
        (CardioMutation, "cardio"),
    ]:
        for m in Model.objects.filter(
            user=user, chat_turn_ref=chat_turn_ref,
        ):
            existing.append({
                "id":           m.id,
                "type":         kind,
                "kind":         m.kind,
                "ai_rationale": m.ai_rationale,
                "new_value":    m.new_value,
                "status":       m.status,
            })
    if existing:
        return Response({
            "submission_id": submission.id,
            "proposals":     existing,
            "text_response": "",
            "cached":        True,
        })

    if not ANTHROPIC_API_KEY:
        return Response(
            {"detail": "AI service not configured."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    system = SYSTEM_PROMPT + "\n\nUSER CONTEXT:\n" + _build_context_with_checkin(
        user, submission,
    )
    messages = [
        {"role": "user",
         "content": "Review my check-in. If anything justifies a small change to my programme, propose it. Otherwise tell me to hold steady."},
    ]

    try:
        result = _call_anthropic_once(system, messages)
    except requests.HTTPError as e:
        log.exception("checkin AI call failed: %s", e)
        return Response(
            {"detail": "AI service is busy — try again in a minute."},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    text_response = ""
    proposals = []

    for block in result.get("content", []):
        if block.get("type") == "text":
            text_response += block.get("text", "")
        elif block.get("type") == "tool_use":
            tool_name = block.get("name", "")
            tool_input = block.get("input", {}) or {}
            # dispatch_tool persists the proposal row + returns
            # (text_summary, proposal_dict). It already tags the
            # row with chat_turn_ref when we pass it.
            try:
                _result, proposal = dispatch_tool(
                    user, tool_name, tool_input,
                    chat_turn_ref=chat_turn_ref,
                )
                if proposal is not None:
                    proposals.append(proposal)
            except Exception as e:
                log.exception("dispatch_tool failed in checkin: %s", e)

    return Response({
        "submission_id": submission.id,
        "proposals":     proposals,
        "text_response": text_response.strip(),
        "cached":        False,
    })
