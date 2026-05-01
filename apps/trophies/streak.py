"""Rolling 7-day "active streak" computation.

Definition (Option A from the design discussion):

    Streak = number of consecutive calendar days, ending today, where
    the user's rolling 7-day window contained at least `weekly_target`
    completed workouts.

    `weekly_target` = number of training days in the user's currently-
    assigned WorkoutPlan. A 5-day plan has target=5, a 3-day plan has
    target=3, etc. Falls back to a default if the user has no plan.

This naturally handles rest days — they don't break the streak as long
as the workout *frequency* over any 7-day window is on target. Swapping
your rest day from Thursday to Wednesday is fine; skipping a session
and not making it up within 7 days breaks the streak.

Used by both:
  * The iOS Home stat tile (via /api/users/me/home-stats/)
  * Trophy evaluators (`streak_days(N)` builder)

so the streak shown on Home is the same number used to award streak
trophies — no risk of "the tile says 7 but I never got the 7-Day
Streak trophy."
"""
from datetime import timedelta

from django.utils import timezone


# Sensible default when a user has no assigned plan yet (e.g. brand
# new client who hasn't been onboarded). 3/week is the conventional
# minimum for general fitness — we don't want to set the bar at 0 or
# infinity.
_DEFAULT_WEEKLY_TARGET = 3
# Hard cap to prevent runaway loops if data ever gets weird (e.g.
# session timestamps from the future). 5 years of streak is plenty.
_MAX_STREAK_DAYS = 365 * 5


def weekly_target_for(user):
    """Workouts/week the user should be hitting. Reads from the
    currently-assigned plan; falls back to a sensible default."""
    profile = getattr(user, "client_profile", None)
    if profile is None:
        return _DEFAULT_WEEKLY_TARGET
    plan = getattr(profile, "assigned_workout_plan", None)
    if plan is None:
        return _DEFAULT_WEEKLY_TARGET
    target = plan.days.count()
    return max(1, target) if target else _DEFAULT_WEEKLY_TARGET


def compute_active_streak(user, weekly_target=None):
    """Number of consecutive days where the past-7-day window meets
    the user's weekly workout target. Returns int >= 0.

    STREAK-WIRING (May 2026): when SoloProfile.training_days is set
    (e.g. ["mon","wed","fri"]), we use a day-of-week-aware streak
    instead of the rolling-window. The DOW-aware streak counts
    backwards from today: training-day requires a session that
    day; rest-day requires nothing. Breaks on the first training-
    day miss. This matches user intuition ("I worked out on every
    day I was supposed to") and explains why a user who ran 2
    sessions in their first week saw streak=0 under the old
    rolling-window logic (window count 2 < target 4).

    Falls back to the rolling-window logic when training_days
    isn't configured (older users / pre-AI-build accounts).

    Computation (rolling-window fallback):
      1. Build a multiset (Counter-style dict) of session-counts per
         calendar day.
      2. Initialise a rolling window over the last 7 days ending today.
      3. While the window count >= target, increment the streak and
         slide the window back one day.
    """
    # Local import to avoid an apps.trophies → apps.workouts circular
    # at module load time (workouts doesn't depend on us, but a side
    # consumer might).
    from apps.workouts.models import WorkoutSession

    # STREAK-WIRING — DOW-aware streak when training_days is set.
    profile = getattr(user, "client_profile", None)
    training_days = getattr(profile, "training_days", None) if profile else None
    if training_days:
        return _compute_dow_aware_streak(user, training_days)

    target = weekly_target if weekly_target is not None else weekly_target_for(user)

    # Bucket session timestamps into local-calendar dates → count.
    sessions_by_date = {}
    for ts in WorkoutSession.objects.filter(
        user=user, is_complete=True
    ).values_list("completed_at", flat=True):
        d = timezone.localtime(ts).date()
        sessions_by_date[d] = sessions_by_date.get(d, 0) + 1

    if not sessions_by_date:
        return 0

    today = timezone.localdate()
    # Initial window: today and the 6 preceding days inclusive.
    window_count = sum(
        sessions_by_date.get(today - timedelta(days=i), 0) for i in range(7)
    )

    streak = 0
    cursor = today
    while window_count >= target:
        streak += 1
        if streak >= _MAX_STREAK_DAYS:
            break
        # Slide window back by 1 day:
        #   - we drop the day at the FRONT of the window (cursor)
        #   - we add the day at the new BACK of the window (cursor - 7)
        old_front = cursor
        cursor -= timedelta(days=1)
        new_back = cursor - timedelta(days=6)
        window_count -= sessions_by_date.get(old_front, 0)
        window_count += sessions_by_date.get(new_back, 0)

    return streak


# Day-of-week names — Python's calendar.day_abbr is locale-aware so
# we pin our own list to avoid surprises in non-English deployments.
_DOW_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _compute_dow_aware_streak(user, training_days):
    """Walk back from today. Streak counts every TRAINING day with at
    least one logged session, plus every REST day in between. Breaks
    the moment we hit a training day with no session.

    Example — training_days=["mon","wed","fri"], user did Mon + Wed:
      today = Wed → has session → streak = 1, walk to Tue
      Tue is rest → streak = 2, walk to Mon
      Mon → has session → streak = 3, walk to Sun
      Sun is rest → streak = 4, walk to Sat
      Sat is rest → streak = 5, walk to Fri
      Fri → no session → STOP, return 5

    Notably this DOES count rest days in the streak number, so the
    user's streak grows daily as long as they nail their plan.

    If today is a training day with no session yet, we still allow
    that day in the streak — we don't break on "today not yet
    completed" because the day isn't over. We start counting from
    yesterday.
    """
    from apps.workouts.models import WorkoutSession

    # Bucket completed sessions into local-calendar dates.
    sessions_by_date = {}
    for ts in WorkoutSession.objects.filter(
        user=user, is_complete=True,
    ).values_list("completed_at", flat=True):
        d = timezone.localtime(ts).date()
        sessions_by_date[d] = sessions_by_date.get(d, 0) + 1

    today = timezone.localdate()
    # Normalise the training_days input — accept ["mon","wed",...]
    # or full names; lower-case and trim to the first 3 chars.
    training_set = set()
    for d in training_days or []:
        s = str(d).strip().lower()[:3]
        if s in _DOW_KEYS:
            training_set.add(s)

    if not training_set:
        return 0  # malformed — nothing to compute against.

    streak = 0
    cursor = today
    # Special-case TODAY — if today is a training day and not yet
    # logged, that's not a "miss"; the day isn't over. Skip today
    # in the count and start from yesterday.
    today_dow = _DOW_KEYS[today.weekday()]
    if today_dow in training_set and today not in sessions_by_date:
        cursor = today - timedelta(days=1)
    else:
        # Today is either a rest day OR a logged training day —
        # count it.
        streak += 1
        cursor = today - timedelta(days=1)

    # Walk backwards. Cap at _MAX_STREAK_DAYS as a safety net.
    while streak < _MAX_STREAK_DAYS:
        dow = _DOW_KEYS[cursor.weekday()]
        if dow in training_set:
            if cursor in sessions_by_date:
                streak += 1
            else:
                # Missed a planned training day — streak ends.
                break
        else:
            # Rest day — counts toward the streak unconditionally.
            streak += 1
        cursor -= timedelta(days=1)

    return streak
