"""
R7-1 — Per-user monthly AI usage caps.

Without monthly caps a heavy Pro AI user could rack up ~$60/mo of
Anthropic spend on a £19.99 subscription, which inverts the unit
economics. Caps enforce a reasonable monthly ceiling per channel:

    • build           —  4 / month   (~once a week — like a real PT swap)
    • chat            — 150 / month  (~5/day — covers nutrition + workout chat)
    • describe        — 100 / month  (~3/day — typical photo logger)
    • nutrition_build —  6 / month   (macro variant generation; was on
                                     `describe` but a single user re-running
                                     the AI nutrition setup chewed through
                                     the photo-describe budget — POLISH-AICAP)
    • checkin         —   5 / month  (one ISO-week + 1 spare)

Total worst-case spend per user: ~$2.07/mo on a £19.99 subscription
= ~92% gross margin on a power user. Anything over the cap returns
402 with `{"upgrade_to": "pro_ai_pack", "channel": "...", "resets_on": "..."}`.

State is stored on `User.notification_prefs["ai_usage"]` — a JSON
dict keyed by year-month. No migration needed (notification_prefs
has been a JSONField since the SOLO MVP migration). Old months
are pruned at increment time so the dict stays small.

Usage:

    from .ai_caps import enforce_cap, increment

    ok, info = enforce_cap(user, channel="build")
    if not ok:
        return Response(info["error_response"], status=info["status"])

    # ... do the AI call ...

    increment(user, channel="build")
    return Response({..., "remaining_this_month": info["remaining"] - 1})
"""
import logging
from datetime import datetime, timedelta
from django.utils import timezone

log = logging.getLogger(__name__)


# --------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------
USAGE_KEY       = "ai_usage"
PREVIOUS_MONTHS_TO_KEEP = 1  # drop anything older than current + 1 prior

DEFAULT_CAPS: dict[str, int] = {
    "build":            4,
    "chat":           150,
    "describe":       100,
    "nutrition_build":  6,
    "checkin":          5,
}

CHANNEL_LABELS: dict[str, str] = {
    "build":           "AI build programme",
    "chat":            "AI coach chat",
    "describe":        "AI describe",
    "nutrition_build": "AI nutrition setup",
    "checkin":         "Weekly check-in",
}


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
def _ym(now=None) -> str:
    """Return YYYY-MM for the user's local-clock current month."""
    now = now or timezone.now()
    return now.strftime("%Y-%m")


def _next_month_first(now=None) -> str:
    """ISO date of the first of next month — used in the
    'resets_on' field surfaced to iOS so the user knows when
    their cap rolls over."""
    now = now or timezone.now()
    if now.month == 12:
        nxt = now.replace(year=now.year + 1, month=1, day=1)
    else:
        nxt = now.replace(month=now.month + 1, day=1)
    return nxt.strftime("%Y-%m-%d")


def _load(user) -> dict:
    prefs = user.notification_prefs or {}
    usage = prefs.get(USAGE_KEY) or {}
    return usage if isinstance(usage, dict) else {}


def _save(user, usage: dict) -> None:
    prefs = user.notification_prefs or {}
    prefs[USAGE_KEY] = usage
    user.notification_prefs = prefs
    user.save(update_fields=["notification_prefs"])


def _prune(usage: dict) -> dict:
    """Keep only the current month + the immediately-preceding month
    so the JSON blob never grows unbounded."""
    if not usage:
        return usage
    keys = sorted(usage.keys(), reverse=True)
    keep = set(keys[: PREVIOUS_MONTHS_TO_KEEP + 1])
    return {k: v for k, v in usage.items() if k in keep}


# --------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------
def remaining(user, channel: str) -> int:
    """How many calls left in `channel` for this calendar month."""
    if channel not in DEFAULT_CAPS:
        return 0
    usage = _load(user)
    used = (usage.get(_ym()) or {}).get(channel, 0)
    return max(0, DEFAULT_CAPS[channel] - used)


def enforce_cap(user, channel: str) -> tuple[bool, dict]:
    """Returns (ok, info).
        ok = True   → caller proceeds, then calls increment()
        ok = False  → caller returns Response(info['error_response'],
                       status=info['status'])

    `info` always contains `remaining` (after the prospective call,
    so a fresh 0-used user calling with cap=4 gets remaining=3).
    """
    if channel not in DEFAULT_CAPS:
        return False, {"status": 400,
                       "error_response": {"detail": f"Unknown channel: {channel}"}}

    rem = remaining(user, channel)
    if rem <= 0:
        return False, {
            "status": 402,
            "remaining": 0,
            "error_response": {
                "detail": (
                    f"You've used your monthly {CHANNEL_LABELS[channel]} "
                    f"limit. Resets on {_next_month_first()}."
                ),
                "upgrade_to":  "pro_ai_pack",  # future: top-up packs
                "channel":     channel,
                "resets_on":   _next_month_first(),
            },
        }
    return True, {"status": 200, "remaining": rem - 1}


def increment(user, channel: str) -> int:
    """Bump the counter and persist. Returns the NEW remaining count
    so the caller can surface it to iOS in one round-trip."""
    if channel not in DEFAULT_CAPS:
        return 0
    usage = _load(user)
    ym = _ym()
    month_bucket = usage.get(ym) or {}
    month_bucket[channel] = (month_bucket.get(channel) or 0) + 1
    usage[ym] = month_bucket
    usage = _prune(usage)
    _save(user, usage)
    return max(0, DEFAULT_CAPS[channel] - month_bucket[channel])


def usage_summary(user) -> dict:
    """Snapshot of all channels for the current month — used by
    iOS to render a compact "X of Y this month" footer."""
    ym = _ym()
    usage = _load(user)
    bucket = usage.get(ym) or {}
    out = {}
    for ch, cap in DEFAULT_CAPS.items():
        used = bucket.get(ch, 0)
        out[ch] = {
            "label":     CHANNEL_LABELS[ch],
            "used":      used,
            "cap":       cap,
            "remaining": max(0, cap - used),
        }
    out["resets_on"] = _next_month_first()
    return out
