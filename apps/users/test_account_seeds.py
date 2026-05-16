"""TEST-ACCOUNTS — provisioning + history-seeding helpers shared by
the `seed_reviewer_account` management command and by the magic-link
verify view's reset-token branch.

Three modes for `history_mode`:

  "none"        — empty account (cold-start). Wipes any existing
                  WorkoutSession + SoloBodyweightLog the account
                  may have accumulated.
  "single_day"  — exactly one workout + one weight entry, both
                  logged today. Used to test the "one data point,
                  no comparisons yet" UI state.
  "full"        — ~30 days of realistic history. Used by the
                  reviewer account so App Store review reviewers
                  see a populated Progress tab.

All write paths filter by the spec's email — they will NEVER touch a
real user account by mistake.
"""
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from django.db import transaction
from django.utils import timezone

from apps.users.models import User, SoloProfile
from apps.workouts.models import (
    WorkoutPlan,
    WorkoutSession,
    ExerciseSession,
    SetPerformance,
    ExerciseCatalog,
)
from apps.progress.models import SoloBodyweightLog


# ----------------------------------------------------------------------
# Spec + return tuple
# ----------------------------------------------------------------------
@dataclass
class TestAccountSpec:
    email: str
    first_name: str
    last_name: str
    history_mode: str                       # "none" | "single_day" | "full"
    assign_programme: Optional[str]         # WorkoutPlan name (template) or None
    days_per_week: int = 3


# ----------------------------------------------------------------------
# Session seed data — used by history_mode="full"
# ----------------------------------------------------------------------
SESSION_PROGRAMMES = [
    {
        "title": "Push Day — Chest / Shoulders / Triceps",
        "lifts": [
            ("Barbell Bench Press",      80.0, 6),
            ("Overhead Press",           45.0, 8),
            ("Incline Dumbbell Press",   28.0, 10),
            ("Tricep Pushdown",          25.0, 12),
        ],
    },
    {
        "title": "Pull Day — Back / Biceps",
        "lifts": [
            ("Deadlift",                120.0, 5),
            ("Pull-Up",                   0.0, 8),   # bodyweight
            ("Barbell Row",              70.0, 8),
            ("Barbell Curl",             30.0, 10),
        ],
    },
    {
        "title": "Leg Day — Quads / Hamstrings / Glutes",
        "lifts": [
            ("Back Squat",              100.0, 6),
            ("Romanian Deadlift",        80.0, 8),
            ("Leg Press",               140.0, 10),
            ("Standing Calf Raise",      60.0, 15),
        ],
    },
]


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
def provision_test_account(spec: TestAccountSpec) -> tuple[User, str]:
    """Create or refresh a test account end-to-end. Returns (user, summary).

    Idempotent: running again wipes the account's history and re-seeds
    against today. Never touches real user data — every query is
    scoped to spec.email.
    """
    with transaction.atomic():
        user = _provision_user_and_profile(spec)
        wipe_test_account_history(user)

        if spec.history_mode == "none":
            summary = "Pro AI, no history."
        elif spec.history_mode == "single_day":
            _seed_single_day(user)
            summary = "Pro AI, 1 workout + 1 weight (today)."
        elif spec.history_mode == "full":
            sessions = _seed_thirty_day_history(user)
            summary = f"Pro AI, 30 days of bodyweight + {sessions} workouts."
        else:
            raise ValueError(f"Unknown history_mode: {spec.history_mode}")

    return user, summary


def wipe_test_account_history(user: User) -> None:
    """Delete every WorkoutSession + SoloBodyweightLog row for this user.

    Used by:
      • provision_test_account (called on every re-seed)
      • magic_link_verify_view's reset-token branch (called on every
        sign-in for the reset account)

    Cascade rules on the FKs handle ExerciseSession + SetPerformance
    automatically — no separate cleanup needed there.
    """
    WorkoutSession.objects.filter(user=user).delete()
    SoloBodyweightLog.objects.filter(user=user).delete()


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------
def _provision_user_and_profile(spec: TestAccountSpec) -> User:
    """Create or refresh the User + SoloProfile for a test account.

    Always lands on Pro AI tier so the full feature surface is
    available without the paywall blocking anything mid-test.
    """
    user, created = User.objects.get_or_create(
        email=spec.email,
        defaults={
            "username":   spec.email,
            "first_name": spec.first_name,
            "last_name":  spec.last_name,
            "role":       User.SOLO,
            "is_active":  True,
        },
    )

    if not created:
        updated_fields = []
        if user.role != User.SOLO:
            user.role = User.SOLO
            updated_fields.append("role")
        if not user.is_active:
            user.is_active = True
            updated_fields.append("is_active")
        if updated_fields:
            user.save(update_fields=updated_fields)

    if user.has_usable_password():
        user.set_unusable_password()
        user.save(update_fields=["password"])

    profile, _ = SoloProfile.objects.get_or_create(user=user)
    profile.goals = ["build_muscle", "get_stronger"]
    profile.experience = "one_to_three"
    profile.equipment = "full_gym"
    profile.days_per_week = spec.days_per_week
    profile.gender = "prefer_not"
    profile.tier = SoloProfile.TIER_PRO_AI
    profile.tier_active_until = None
    profile.trial_started_at = profile.trial_started_at or timezone.now()

    # BODY-STATS-RESET (May 2026, Deen QC) — overwrite height /
    # weight / DOB on every seed so a previous test session's
    # answers (filled via the now-retired mandatory gate) don't
    # leak into the polished reviewer demo. Generic placeholder
    # values when history_mode is "full" or "single_day" (the
    # accounts that need a complete-feeling profile for screens
    # that read body stats), blank for cold-start accounts.
    # NOTE: date_of_birth lives on User, not SoloProfile — handled
    # below alongside the user.save() block.
    if spec.history_mode in ("full", "single_day"):
        profile.height_cm = 178
        profile.bodyweight_kg = 78.0
        dob_value = date(1995, 1, 1)
    else:
        profile.height_cm = None
        profile.bodyweight_kg = None
        dob_value = None

    if user.date_of_birth != dob_value:
        user.date_of_birth = dob_value
        user.save(update_fields=["date_of_birth"])

    if spec.assign_programme:
        programme = WorkoutPlan.objects.filter(
            name__iexact=spec.assign_programme,
            is_solo_template=True,
        ).first()
        profile.assigned_workout_plan = programme
    else:
        profile.assigned_workout_plan = None

    # SETUP-PROGRESS-FLAGS — reset the 5 booleans on every re-seed so
    # a previous session's accidental clicks don't persist into the
    # next QC run. Per-mode behaviour:
    #
    #   "full"        — reviewer: all 5 done. Strip is hidden. Reviewer
    #                   sees the polished steady-state experience.
    #   "single_day"  — day1: 3 done (body stats, goal, training). The
    #                   strip will show 3/5 so the "partial setup" UI
    #                   is still QC-able.
    #   "none"        — day0 / reset: all 5 False. Strip shows 0/5.
    #                   Day 0 cold-start experience.
    profile.setup_apple_health_done    = (spec.history_mode == "full")
    profile.setup_body_stats_done      = (spec.history_mode in ("full", "single_day"))
    profile.setup_goal_done            = (spec.history_mode in ("full", "single_day"))
    profile.setup_training_done        = (spec.history_mode in ("full", "single_day"))
    profile.setup_nutrition_style_done = (spec.history_mode == "full")

    # MACRO-SEED (May 2026, Deen QC) — pre-populated daily macro targets
    # for accounts that should feel "set up" out of the gate so the
    # reviewer / day1 demo never shows an empty Nutrition tab. Numbers
    # are calibrated for the 178 cm / 78 kg / DOB 1995 male body stats
    # we also seed (TDEE ~2,710 kcal via Mifflin-St Jeor + 1.5x activity,
    # protein 1.8 g/kg for the build-muscle goal). day0 / reset stay at
    # zero so the cold-start experience is still QC-able.
    if spec.history_mode in ("full", "single_day"):
        profile.target_calories = 2700
        profile.target_protein  = 140
        profile.target_carbs    = 310
        profile.target_fats     = 69
    else:
        profile.target_calories = 0
        profile.target_protein  = 0
        profile.target_carbs    = 0
        profile.target_fats     = 0

    profile.save()
    return user


def _seed_single_day(user: User) -> None:
    """Seed exactly one workout + one weight entry, both dated today."""
    today = timezone.localdate()
    SoloBodyweightLog.objects.create(user=user, logged_on=today, kg=82.0)
    programme = SESSION_PROGRAMMES[0]  # Push day, first session
    _seed_one_session(
        user=user,
        when=today,
        title=programme["title"],
        lifts=programme["lifts"],
        progression_factor=1.0,
    )


def _seed_thirty_day_history(user: User) -> int:
    """30 daily weights trending down + 12 workouts on a M/W/F pattern.

    Returns the number of workout sessions created (≤12 — sessions
    in the partial current week may be skipped if the date is in
    the future).
    """
    _seed_bodyweight_curve(user)
    return _seed_workout_history(user)


def _seed_bodyweight_curve(user: User) -> None:
    """30 daily weights trending from 82.5 → 81.0 kg with ±0.3 noise.

    Deterministic seed so the curve looks identical across redeploys
    rather than wandering.
    """
    today = timezone.localdate()
    start_kg = 82.5
    end_kg = 81.0
    rng = random.Random(20260515)

    logs = []
    for offset in range(30, 0, -1):
        day = today - timedelta(days=offset - 1)
        t = (30 - offset) / 29  # 0.0 → 1.0
        trend_kg = start_kg + (end_kg - start_kg) * t
        noise = rng.uniform(-0.3, 0.3)
        kg = round(trend_kg + noise, 1)
        logs.append(SoloBodyweightLog(user=user, logged_on=day, kg=kg))
    SoloBodyweightLog.objects.bulk_create(logs)


def _seed_workout_history(user: User) -> int:
    """12 sessions on a Mon/Wed/Fri pattern over the past 4 weeks."""
    today = timezone.localdate()
    anchor = today
    while anchor.weekday() != 0:  # 0 = Monday
        anchor -= timedelta(days=1)

    session_count = 0
    for week in range(4):
        for weekday_offset, programme_idx in [(0, 0), (2, 1), (4, 2)]:
            day_offset = (week * 7) + weekday_offset
            session_date = anchor - timedelta(days=(21 - day_offset))
            if session_date > today:
                continue
            programme = SESSION_PROGRAMMES[programme_idx]
            progression_factor = 1.0 + (week * 0.01)  # gentle 1%/week uptick
            _seed_one_session(
                user=user,
                when=session_date,
                title=programme["title"],
                lifts=programme["lifts"],
                progression_factor=progression_factor,
            )
            session_count += 1
    return session_count


def _seed_one_session(user, when, title, lifts, progression_factor) -> None:
    """One WorkoutSession with 3-4 exercises, each 3 sets.

    Backdates `completed_at` via the .update() bypass so auto_now_add
    doesn't override us. completed_at clusters around 18:00 local to
    look like an evening lifter's pattern.
    """
    completed_at = timezone.make_aware(
        datetime.combine(when, datetime.min.time())
        + timedelta(hours=18, minutes=random.randint(0, 59))
    )

    session = WorkoutSession.objects.create(
        user=user,
        workout_day=None,  # ad-hoc — no plan FK
        title=title,
        duration=random.randint(45 * 60, 75 * 60),  # 45-75 min in seconds
        is_complete=True,
        rpe=random.choice([6, 7, 7, 7, 8, 8]),  # bias toward "honest 7"
    )
    WorkoutSession.objects.filter(pk=session.pk).update(completed_at=completed_at)

    for lift_name, base_weight, base_reps in lifts:
        catalog = ExerciseCatalog.objects.filter(name__iexact=lift_name).first()
        ex_session = ExerciseSession.objects.create(
            workout_session=session,
            exercise=None,
            name=lift_name,
            catalog=catalog,
        )
        for set_num in range(1, 4):
            weight = round(base_weight * progression_factor, 1) if base_weight > 0 else 0
            reps = max(1, base_reps - (set_num // 3))
            SetPerformance.objects.create(
                exercise_session=ex_session,
                set_number=set_num,
                weight=str(weight) if weight > 0 else "",
                reps=str(reps),
            )
