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

    Computation:
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
