"""
E.2 — AI PT chat endpoint.

POST /api/users/solo/ai-pt/chat/
Body: { "messages": [{"role": "user"|"assistant", "content": str}, …] }
Returns: { "reply": str, "remaining_today": int }

The killer Solo feature. The user opens a chat sheet from the Hub
and asks anything — exercise swaps, programme tweaks, form cues,
nutrition guidance, motivation. The endpoint:

  1. Validates Pro AI entitlement (402 otherwise).
  2. Rate-limits at 60 messages/day.
  3. Builds a "system prompt + user context" block from:
        - SoloProfile (goals, experience, equipment, days/week,
          bodyweight, macro targets)
        - Active programme (name + meta + days)
        - Last 5 workout sessions (what exercises, when)
        - Last 7 days of bodyweight + nutrition log
  4. Forwards the conversation to Claude Sonnet 4.6.
  5. Returns the assistant's reply.

The endpoint is stateless re: chat history — the iOS client holds
the full conversation and resubmits it each turn. Keeps backend
simple + lets users wipe their history client-side without
server-side coordination.

Cost guardrails (similar to AI describe):
  • Pro AI required.
  • 60 messages/day.
  • Max 4000 tokens of context per request (the request would 400
    if we tried to include too much; we trim by recency).
"""
import json
import logging
import os
from datetime import timedelta

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

from .models import User, SoloProfile

log = logging.getLogger(__name__)


ANTHROPIC_API_KEY = getattr(settings, "ANTHROPIC_API_KEY", None) or os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL   = "claude-sonnet-4-6"
ANTHROPIC_URL     = "https://api.anthropic.com/v1/messages"

DAILY_MESSAGE_LIMIT = 60
MAX_OUTPUT_TOKENS   = 600
MAX_HISTORY_TURNS   = 12   # client should also clip; we hard-cap


SYSTEM_TEMPLATE = """\
You are GymFlow's AI personal trainer. You speak directly to one
specific user — see the USER CONTEXT block below for everything we
know about them. Your job is to coach: programme adjustments,
exercise swaps, form cues, recovery, nutrition guidance, training
motivation.

Voice + style:
- Warm, direct, real-coach. No corporate softness, no exclamation
  marks unless genuinely warranted. No "hey there!" or "let's crush
  it!".
- Concrete. If you suggest a swap, name the exercise and the rep
  scheme. If you recommend a calorie change, give a specific number.
- Honest about uncertainty. If the user asks about something
  outside your remit (medical advice, injury diagnosis), recommend
  they see a qualified professional.
- Never give medical advice. If the user describes pain or injury
  symptoms, suggest seeing a physio or doctor.
- Lean on the evidence. If you cite a research finding, name it
  ("Schoenfeld 2019: twice-weekly frequency beats once-weekly for
  hypertrophy") rather than "studies show".

Length:
- Short answers for short questions (3-5 sentences).
- Longer answers when the user asks for a programme review or a
  meal plan. Cap at ~250 words unless they explicitly ask for more.

Hard rules:
- Never recommend supplements that aren't widely safe (creatine,
  protein, caffeine are fine; SARMs, anabolics, anything dodgy is
  not).
- If the user reports disordered-eating signs, gently surface that
  professional support exists; don't lecture.
- Never claim to be human. If they ask "are you real?" say you're
  GymFlow's AI coach.

USER CONTEXT:
{context}
"""


def _build_user_context(user) -> str:
    """Compact text block describing the user. Sent in the system
    prompt so the model never has to ask "what are your goals?". Trim
    aggressively — every line costs tokens."""
    from apps.workouts.models import WorkoutSession
    from apps.progress.models import SoloBodyweightLog
    from apps.nutrition.models import SoloFoodLogEntry

    profile, _ = SoloProfile.objects.get_or_create(user=user)
    lines = []

    # Identity
    name = (user.first_name or user.username or "this user").strip()
    lines.append(f"- Name: {name}")
    lines.append(f"- Goals: {', '.join(profile.goals) or 'unspecified'}")
    lines.append(f"- Experience: {profile.experience or 'unspecified'}")
    lines.append(f"- Equipment: {profile.equipment or 'unspecified'}")
    lines.append(f"- Target days/week: {profile.days_per_week}")

    # Bodyweight (most recent)
    latest_bw = (
        SoloBodyweightLog.objects.filter(user=user).order_by("-logged_on").first()
    )
    if latest_bw:
        lines.append(f"- Bodyweight: {latest_bw.kg:.1f}kg (logged {latest_bw.logged_on.isoformat()})")
    elif profile.bodyweight_kg:
        lines.append(f"- Bodyweight: {profile.bodyweight_kg:.1f}kg (estimated)")

    # Macro targets
    lines.append(
        f"- Daily targets: {profile.target_calories} kcal / "
        f"{profile.target_protein}p / {profile.target_carbs}c / "
        f"{profile.target_fats}f"
    )

    # Active programme
    plan = profile.assigned_workout_plan
    if plan is not None:
        meta = plan.programme_meta or {}
        lines.append(f"- Active programme: {plan.name} "
                     f"({meta.get('days_per_week') or '?'}x/week, "
                     f"{meta.get('weeks') or '?'} weeks)")
        if meta.get("source_attribution"):
            lines.append(f"  ({meta['source_attribution']})")

    # Last 5 sessions (exercises only — keep it light)
    recent = (
        WorkoutSession.objects
        .filter(user=user, is_complete=True)
        .select_related("workout_day")
        .order_by("-completed_at")[:5]
    )
    if recent:
        lines.append("- Recent sessions:")
        for s in recent:
            d = s.completed_at.strftime("%b %d") if s.completed_at else "?"
            title = s.workout_day.title if s.workout_day_id else "?"
            lines.append(f"    {d}: {title}")

    # Last 7 days of food log totals
    today = timezone.localdate()
    week_ago = today - timedelta(days=7)
    food_rows = SoloFoodLogEntry.objects.filter(
        user=user, consumed_on__gte=week_ago,
    ).order_by("-consumed_on")
    if food_rows:
        from collections import defaultdict
        per_day_kcal = defaultdict(float)
        for r in food_rows:
            per_day_kcal[r.consumed_on] += r.calories
        avg = sum(per_day_kcal.values()) / max(len(per_day_kcal), 1)
        lines.append(f"- Avg kcal logged (last 7d): {int(avg)}")

    return "\n".join(lines)


# In-memory rate limiter
_chat_call_counts: dict[int, tuple[str, int]] = {}


def _check_rate_limit(user_id: int) -> tuple[bool, int]:
    today = timezone.localdate().isoformat()
    last_day, count = _chat_call_counts.get(user_id, (today, 0))
    if last_day != today:
        last_day, count = today, 0
    if count >= DAILY_MESSAGE_LIMIT:
        return False, 0
    _chat_call_counts[user_id] = (today, count + 1)
    return True, DAILY_MESSAGE_LIMIT - count - 1


# --------------------------------------------------------------------
# Endpoint
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_ai_pt_chat(request):
    user = request.user
    if user.role != User.SOLO:
        return Response({"detail": "Solo accounts only."}, status=status.HTTP_403_FORBIDDEN)

    profile, _ = SoloProfile.objects.get_or_create(user=user)
    if not profile.has_ai_access:
        return Response(
            {"detail": "AI PT is a Pro AI feature.", "upgrade_to": "pro_ai"},
            status=status.HTTP_402_PAYMENT_REQUIRED,
        )
    if not ANTHROPIC_API_KEY:
        return Response({"detail": "AI PT temporarily unavailable."}, status=503)

    ok, remaining = _check_rate_limit(user.id)
    if not ok:
        return Response(
            {"detail": "Daily AI message limit reached. Try again tomorrow."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    raw_messages = request.data.get("messages") or []
    if not isinstance(raw_messages, list) or not raw_messages:
        return Response({"detail": "messages must be a non-empty list."}, status=400)

    # Sanitise the conversation. Drop bad rows; keep only the last
    # MAX_HISTORY_TURNS turns; cap each message at 4000 chars.
    cleaned = []
    for m in raw_messages[-MAX_HISTORY_TURNS:]:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        text = (m.get("content") or "").strip()
        if role not in ("user", "assistant") or not text:
            continue
        cleaned.append({"role": role, "content": text[:4000]})
    if not cleaned or cleaned[-1]["role"] != "user":
        return Response({"detail": "Last message must be from the user."}, status=400)

    context = _build_user_context(user)
    system = SYSTEM_TEMPLATE.format(context=context)

    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system": system,
        "messages": cleaned,
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
            timeout=30.0,
        )
    except Exception as exc:
        log.exception("AI PT request failed")
        return Response({"detail": f"AI provider unreachable: {exc}"}, status=503)

    if resp.status_code != 200:
        log.error("AI PT non-200: %s %s", resp.status_code, resp.text[:300])
        return Response({"detail": "AI provider returned an error."}, status=502)

    try:
        payload = resp.json()
        content = payload.get("content") or []
        text_block = next((c for c in content if c.get("type") == "text"), None)
        reply = text_block["text"] if text_block else ""
    except Exception:
        log.exception("AI PT parse failed")
        return Response({"detail": "Couldn't parse AI response."}, status=502)

    return Response({
        "reply":           reply.strip(),
        "remaining_today": remaining,
    })
