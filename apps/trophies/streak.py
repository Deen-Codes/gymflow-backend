"""Lifetime "active days" streak computation.

Definition (May 2026 rewrite — STREAK-PHILOSOPHY-V2):

    Streak = total number of distinct local-calendar days on which
    the user has logged at least one completed WorkoutSession.

That is: a lifetime counter of days-trained-on-Afletics. It can only
go up. Rest days are not "missed" — they simply don't add. There is
no notion of "breaking" a streak. A user who trained 3 times last
week, took a 10-day holiday, and trained today has streak = 4.

Why we changed from rolling-window:
  • The previous model (Option A — rolling 7-day window vs assigned
    plan's weekly target, plus an optional DOW-aware variant) broke
    for valid use cases: a brand-new ad-hoc user with 1 session on a
    5-day plan saw streak = 0 because 1 < 5.
  • Loss aversion (Duolingo-style "your streak will break!") can
    encourage daily logins, but it also drives anxiety and creates
    a cliff users fall off — once broken, the motivator vanishes
    entirely. Loss-aversion streaks are ideal for products with a
    daily-must-use rhythm (language learning); they are a poor fit
    for fitness, where rest days are part of doing it right.
  • A lifetime accumulation counter ("Days trained") behaves like
    Apple Fitness rings + Strava milestones: every active day is
    permanent, growing the number motivates continued use, and the
    user is never punished for the necessary off-day.
  • Constraint: "can only go up by one each day used" (Deen's
    formulation) — multiple sessions on the same day still count
    as one active day. This keeps the number honest.

Used by:
  * iOS Home stat tile (via /api/users/me/home-stats/)
  * Startup composite (_build_home_stats)
  * Trophy evaluators (`streak_days(N)` builder) — the tile and the
    streak trophies agree on the number.

Weekly target is no longer part of the computation — it is kept on
the response payload for now so iOS doesn't have to re-version the
home-stats decode, but it's no longer load-bearing. Future cleanup
can drop the field.
"""
from django.utils import timezone


# Hard cap to prevent runaway loops if data ever gets weird (e.g.
# session timestamps from the future). 5 years of distinct days is
# plenty for any real user.
_MAX_STREAK_DAYS = 365 * 5


def weekly_target_for(user):
    """Workouts/week the user should be hitting. No longer used by
    streak computation; retained for the home-stats payload + any
    callers that read it for display. Reads the assigned plan; falls
    back to 3 when nothing is set."""
    profile = getattr(user, "client_profile", None)
    if profile is None:
        return 3
    plan = getattr(profile, "assigned_workout_plan", None)
    if plan is None:
        return 3
    target = plan.days.count()
    return max(1, target) if target else 3


def compute_active_streak(user, weekly_target=None):
    """Distinct local-calendar days on which the user has logged a
    completed WorkoutSession. Returns int >= 0.

    The `weekly_target` parameter is accepted for backwards
    compatibility with older callers but is ignored — streak is now
    a pure count, independent of any plan.
    """
    # Local import to avoid an apps.trophies → apps.workouts circular
    # at module load time.
    from apps.workouts.models import WorkoutSession

    distinct_days = set()
    for ts in WorkoutSession.objects.filter(
        user=user, is_complete=True,
    ).values_list("completed_at", flat=True):
        d = timezone.localtime(ts).date()
        distinct_days.add(d)
        if len(distinct_days) >= _MAX_STREAK_DAYS:
            break

    return len(distinct_days)
