"""Trophy evaluators — all 100 wired.

Each entry in `EVALUATORS` is a function that takes a user and returns
a tuple `(current, target)`. The trophy is earned when current >= target.
For locked trophies, iOS uses the same numbers to render progress bars
("12 / 25 toward 25 Workouts").

Why functions instead of JSON criteria:
    Some trophies have logic that doesn't fit a JSON spec without us
    inventing a half-baked DSL — "PR three weeks running", "lost 5 kg
    from your starting weight", "first session before 6am". Pure
    Python is the simplest place for this to live.

Performance note:
    Several of these evaluators iterate the user's full SetPerformance
    history (PR detection, volume aggregation). For a heavy user with
    1000 workouts × 5 exercises × 4 sets = 20k rows, that's still well
    under a second. If we ever scale past that, the right move is a
    denormalised "exercise PRs" table updated on workout save — but
    that's premature today.
"""
from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

from .streak import compute_active_streak, weekly_target_for


# =====================================================================
# Lift-name patterns for the bodyweight-relative PR trophies. Match
# against `Exercise.name` (lowercased) — substring search so "Barbell
# Bench Press", "Bench Press (Flat)", and "Incline Bench Press" all
# count for `bench_bodyweight`. Order matters where there could be
# ambiguity ("overhead press" listed before "press" so OHP doesn't
# accidentally match a pec deck).
# =====================================================================
_BENCH_PATTERNS    = ("bench press", "bench")
_SQUAT_PATTERNS    = ("back squat", "front squat", "squat")
_DEADLIFT_PATTERNS = ("deadlift",)
_OHP_PATTERNS      = ("overhead press", "ohp", "military press", "shoulder press")


# =====================================================================
# Internal helpers — shared queries used by multiple evaluators.
# Each is cached on the user object during a single request so an
# evaluator pass doesn't re-query the same rows N times.
# =====================================================================

def _all_set_rows(user):
    """All SetPerformance rows for this user, joined to session/exercise.
    Cached per-user-per-process so the rest of the evaluator pass
    doesn't re-fetch."""
    cache_key = "_trophy_set_rows_cache"
    cached = getattr(user, cache_key, None)
    if cached is not None:
        return cached
    from apps.workouts.models import SetPerformance
    rows = list(
        SetPerformance.objects.filter(
            exercise_session__workout_session__user=user,
            exercise_session__workout_session__is_complete=True,
        )
        .select_related(
            "exercise_session__exercise",
            "exercise_session__workout_session",
        )
        .order_by("exercise_session__workout_session__completed_at",
                  "exercise_session__id", "set_number")
    )
    setattr(user, cache_key, rows)
    return rows


def _all_session_dates(user):
    """List of completed_at datetimes for the user's sessions, sorted
    ascending. Cached per request."""
    cache_key = "_trophy_session_dates_cache"
    cached = getattr(user, cache_key, None)
    if cached is not None:
        return cached
    from apps.workouts.models import WorkoutSession
    dates = list(
        WorkoutSession.objects.filter(
            user=user, is_complete=True,
        ).order_by("completed_at").values_list("completed_at", flat=True)
    )
    setattr(user, cache_key, dates)
    return dates


def _bodyweight_kg(user):
    """Most recent weight (kg) from a check-in answer, or None."""
    from apps.progress.models import CheckInAnswer
    from apps.users.dashboard_client_views import WEIGHT_FIELD_KEYS
    latest = (
        CheckInAnswer.objects.filter(
            submission__client=user,
            submission__status="submitted",
            value_number__isnull=False,
            question__field_key__in=WEIGHT_FIELD_KEYS,
        )
        .order_by("-submission__submitted_at")
        .values_list("value_number", flat=True)
        .first()
    )
    return float(latest) if latest is not None else None


def _max_weight_on_pattern(user, patterns):
    """Max weight ever lifted on any exercise whose name (lowercased)
    contains any of the given substring patterns."""
    max_w = 0.0
    for sp in _all_set_rows(user):
        name = (sp.exercise_session.exercise.name or "").lower()
        if any(p in name for p in patterns):
            try:
                w = float(sp.weight)
                if w > max_w:
                    max_w = w
            except (TypeError, ValueError):
                continue
    return max_w


def _max_weight_any_exercise(user):
    """Max weight on any exercise across the user's history. Used for
    the 'first 100 kg lift' / 'first 200 kg lift' trophies."""
    max_w = 0.0
    for sp in _all_set_rows(user):
        try:
            w = float(sp.weight)
            if w > max_w:
                max_w = w
        except (TypeError, ValueError):
            continue
    return max_w


def _pr_history(user):
    """Return a list of PR events: each time a new all-time max is set
    on any exercise. Each event is (completed_at, session_id, exercise_id, weight).
    Walks sessions chronologically and tracks running max per exercise."""
    running_max = {}   # exercise_id -> max weight seen so far
    prs = []
    for sp in _all_set_rows(user):
        try:
            w = float(sp.weight)
        except (TypeError, ValueError):
            continue
        ex_id = sp.exercise_session.exercise_id
        prev = running_max.get(ex_id, 0.0)
        if w > prev:
            running_max[ex_id] = w
            prs.append((
                sp.exercise_session.workout_session.completed_at,
                sp.exercise_session.workout_session_id,
                ex_id,
                w,
            ))
    return prs


def _session_volume_map(user):
    """Map of workout_session_id -> total volume (sum of weight*reps)."""
    by_session = defaultdict(float)
    for sp in _all_set_rows(user):
        try:
            w = float(sp.weight)
            r = int(sp.reps)
            by_session[sp.exercise_session.workout_session_id] += w * r
        except (TypeError, ValueError):
            continue
    return by_session


def _session_exercise_reps_map(user):
    """Map of (session_id, exercise_id) -> total reps in that pair."""
    counts = defaultdict(int)
    for sp in _all_set_rows(user):
        try:
            counts[(sp.exercise_session.workout_session_id,
                    sp.exercise_session.exercise_id)] += int(sp.reps)
        except (TypeError, ValueError):
            continue
    return counts


# =====================================================================
# Evaluator builders / inline evaluators.
# =====================================================================

# ---- Workout count / volume ---------------------------------------

def _workout_count(threshold):
    def evaluator(user):
        return (len(_all_session_dates(user)), threshold)
    return evaluator


def _total_volume_kg(threshold_kg):
    def evaluator(user):
        total = sum(_session_volume_map(user).values())
        return (int(total), threshold_kg)
    return evaluator


# ---- Streaks ------------------------------------------------------

def _streak_days(threshold):
    def evaluator(user):
        return (compute_active_streak(user), threshold)
    return evaluator


def _comeback(user):
    """Resumed training after a 7+ day gap. Looks for any pair of
    consecutive sessions where the gap is >= 7 days."""
    dates = [timezone.localtime(d).date() for d in _all_session_dates(user)]
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days >= 7:
            return (1, 1)
    return (0, 1)


def _phoenix(user):
    """Lost a 30+ day streak and rebuilt one. Walk session dates
    looking for: a stretch where the rolling streak hit >=30, then
    broke (gap or weekly-target miss), then re-built to >=30."""
    target = weekly_target_for(user)
    dates = sorted(set(timezone.localtime(d).date() for d in _all_session_dates(user)))
    if not dates:
        return (0, 1)
    # Build per-day session count over the relevant range.
    counts = defaultdict(int)
    for d in (timezone.localtime(d2).date() for d2 in _all_session_dates(user)):
        counts[d] += 1
    # Sweep through every day from first to last + 30 days.
    start = dates[0]
    end = max(dates[-1], timezone.localdate())
    cur = start
    rolling = 0
    has_been_above = False
    has_dropped_after = False
    while cur <= end:
        # Window of past 7 days ending today.
        win = sum(counts.get(cur - timedelta(days=i), 0) for i in range(7))
        if win >= target:
            rolling += 1
        else:
            if rolling >= 30:
                has_been_above = True
                has_dropped_after = True
            rolling = 0
        if has_dropped_after and rolling >= 30:
            return (1, 1)
        cur += timedelta(days=1)
    return (0, 1)


def _weekend_warrior(user):
    """Trained on at least one weekend day (Sat or Sun) in each of the
    most recent 4 consecutive weekends."""
    dates = set(timezone.localtime(d).date() for d in _all_session_dates(user))
    today = timezone.localdate()
    # Walk back 4 weekends — for each, check if any weekend day has a session.
    days_to_last_sunday = (today.weekday() + 1) % 7   # Monday=0 → 1, Sunday=6 → 0
    last_sunday = today - timedelta(days=days_to_last_sunday)
    weekends_hit = 0
    for w in range(4):
        sat = last_sunday - timedelta(days=1) - timedelta(weeks=w)
        sun = last_sunday - timedelta(weeks=w)
        if sat in dates or sun in dates:
            weekends_hit += 1
        else:
            break    # streak must be consecutive
    return (weekends_hit, 4)


def _iron_discipline(user):
    """Hit your weekly target every week of a calendar month. We
    measure the most recent 4 consecutive ISO weeks ending this week."""
    target = weekly_target_for(user)
    today = timezone.localdate()
    # Anchor on the start of this week (Monday).
    monday_this = today - timedelta(days=today.weekday())
    weeks_passed = 0
    for w in range(4):
        wk_start = monday_this - timedelta(weeks=w)
        wk_end = wk_start + timedelta(days=6)
        count = sum(
            1 for d in (timezone.localtime(d2).date() for d2 in _all_session_dates(user))
            if wk_start <= d <= wk_end
        )
        if count >= target:
            weeks_passed += 1
        else:
            break
    return (weeks_passed, 4)


# ---- Frequency ----------------------------------------------------

def _sessions_in_calendar_window(threshold, days):
    def evaluator(user):
        from apps.workouts.models import WorkoutSession
        since = timezone.now() - timedelta(days=days)
        count = WorkoutSession.objects.filter(
            user=user, is_complete=True, completed_at__gte=since,
        ).count()
        return (count, threshold)
    return evaluator


def _has_n_sessions_on_same_day(n):
    def evaluator(user):
        per_day = defaultdict(int)
        for ts in _all_session_dates(user):
            per_day[timezone.localtime(ts).date()] += 1
            if per_day[timezone.localtime(ts).date()] >= n:
                return (1, 1)
        return (0, 1)
    return evaluator


def _perfect_week(user):
    """Hit every workout the plan called for in the most recent ISO week."""
    target = weekly_target_for(user)
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    count = sum(
        1 for d in (timezone.localtime(d2).date() for d2 in _all_session_dates(user))
        if monday <= d <= sunday
    )
    return (min(count, target), target)


def _perfect_month(user):
    """4 consecutive perfect weeks ending in the current week."""
    return _iron_discipline(user)   # same definition for now


# ---- Personal Records --------------------------------------------

def _pr_count(threshold):
    def evaluator(user):
        return (len(_pr_history(user)), threshold)
    return evaluator


def _three_prs_session(user):
    """Max number of distinct PRs set in any single workout session."""
    by_session = defaultdict(int)
    for _ts, session_id, _ex, _w in _pr_history(user):
        by_session[session_id] += 1
    max_in_one = max(by_session.values()) if by_session else 0
    return (min(max_in_one, 3), 3)


def _pr_three_weeks(user):
    """At least one PR in three consecutive ISO weeks."""
    weeks_with_pr = set()
    for ts, _sid, _ex, _w in _pr_history(user):
        local = timezone.localtime(ts).date()
        iso_year, iso_week, _ = local.isocalendar()
        weeks_with_pr.add((iso_year, iso_week))
    if not weeks_with_pr:
        return (0, 3)
    sorted_weeks = sorted(weeks_with_pr)
    best_run = 1
    cur_run = 1
    for i in range(1, len(sorted_weeks)):
        prev_y, prev_w = sorted_weeks[i - 1]
        cur_y, cur_w = sorted_weeks[i]
        # Consecutive ISO weeks: (year, week+1) or year roll-over.
        if (cur_y == prev_y and cur_w == prev_w + 1) or \
           (cur_y == prev_y + 1 and prev_w >= 52 and cur_w == 1):
            cur_run += 1
            best_run = max(best_run, cur_run)
        else:
            cur_run = 1
    return (min(best_run, 3), 3)


def _bodyweight_relative(patterns, multiplier):
    """User's max lift on any exercise matching `patterns` is at least
    `multiplier` times their bodyweight. Locked when bodyweight unknown."""
    def evaluator(user):
        bw = _bodyweight_kg(user)
        if bw is None or bw <= 0:
            return (0, 1)
        max_lift = _max_weight_on_pattern(user, patterns)
        return (1 if max_lift >= bw * multiplier else 0, 1)
    return evaluator


def _max_weight_threshold(threshold_kg):
    def evaluator(user):
        return (1 if _max_weight_any_exercise(user) >= threshold_kg else 0, 1)
    return evaluator


# ---- Reps & sets --------------------------------------------------

def _set_count(threshold):
    def evaluator(user):
        return (len(_all_set_rows(user)), threshold)
    return evaluator


def _rep_count(threshold):
    def evaluator(user):
        total = 0
        for sp in _all_set_rows(user):
            try:
                total += int(sp.reps)
            except (TypeError, ValueError):
                continue
        return (total, threshold)
    return evaluator


def _max_session_volume(threshold_kg):
    def evaluator(user):
        m = _session_volume_map(user)
        peak = int(max(m.values())) if m else 0
        return (peak, threshold_kg)
    return evaluator


def _max_reps_one_exercise_session(threshold):
    def evaluator(user):
        m = _session_exercise_reps_map(user)
        peak = max(m.values()) if m else 0
        return (peak, threshold)
    return evaluator


# ---- Time-of-day & special days ----------------------------------

def _session_finished_in_hour_range(start_hour, end_hour):
    def evaluator(user):
        for ts in _all_session_dates(user):
            h = timezone.localtime(ts).hour
            if start_hour <= h < end_hour:
                return (1, 1)
        return (0, 1)
    return evaluator


def _session_on_specific_date(month, day):
    def evaluator(user):
        for ts in _all_session_dates(user):
            local = timezone.localtime(ts).date()
            if local.month == month and local.day == day:
                return (1, 1)
        return (0, 1)
    return evaluator


def _session_on_weekday(weekday):
    def evaluator(user):
        for ts in _all_session_dates(user):
            if timezone.localtime(ts).weekday() == weekday:
                return (1, 1)
        return (0, 1)
    return evaluator


def _monday_motivated(user):
    """Trained on Monday in 4 consecutive recent weeks."""
    monday_dates = set()
    for ts in _all_session_dates(user):
        d = timezone.localtime(ts).date()
        if d.weekday() == 0:
            monday_dates.add(d)
    today = timezone.localdate()
    last_monday = today - timedelta(days=today.weekday())
    count = 0
    for w in range(4):
        if (last_monday - timedelta(weeks=w)) in monday_dates:
            count += 1
        else:
            break
    return (count, 4)


def _birthday_workout(user):
    dob = getattr(user, "date_of_birth", None)
    if dob is None:
        return (0, 1)
    for ts in _all_session_dates(user):
        d = timezone.localtime(ts).date()
        if d.month == dob.month and d.day == dob.day:
            return (1, 1)
    return (0, 1)


def _session_with_duration(min_seconds):
    def evaluator(user):
        from apps.workouts.models import WorkoutSession
        exists = WorkoutSession.objects.filter(
            user=user, is_complete=True, duration__gte=min_seconds,
        ).exists()
        return (1 if exists else 0, 1)
    return evaluator


def _session_under_duration(max_seconds):
    def evaluator(user):
        from apps.workouts.models import WorkoutSession
        exists = WorkoutSession.objects.filter(
            user=user, is_complete=True,
            duration__gt=0, duration__lt=max_seconds,
        ).exists()
        return (1 if exists else 0, 1)
    return evaluator


# ---- Check-ins ---------------------------------------------------

def _checkin_count(threshold):
    def evaluator(user):
        from apps.progress.models import CheckInSubmission
        count = CheckInSubmission.objects.filter(
            client=user, status="submitted",
        ).count()
        return (count, threshold)
    return evaluator


def _checkin_with_photo(user):
    from apps.progress.models import CheckInAnswer
    exists = CheckInAnswer.objects.filter(
        submission__client=user,
        submission__status="submitted",
    ).exclude(value_image="").exclude(value_image__isnull=True).exists()
    return (1 if exists else 0, 1)


def _onboarding_complete(user):
    from apps.progress.models import CheckInSubmission
    exists = CheckInSubmission.objects.filter(
        client=user,
        status="submitted",
        form__form_type="onboarding",
    ).exists()
    return (1 if exists else 0, 1)


def _photo_comparison(user):
    """Two photo-bearing check-ins at least 28 days apart."""
    from apps.progress.models import CheckInAnswer
    photo_dates = sorted(
        a.submission.submitted_at.date()
        for a in CheckInAnswer.objects.filter(
            submission__client=user,
            submission__status="submitted",
        )
        .exclude(value_image="").exclude(value_image__isnull=True)
        .select_related("submission")
    )
    if len(photo_dates) < 2:
        return (0, 1)
    if (photo_dates[-1] - photo_dates[0]).days >= 28:
        return (1, 1)
    return (0, 1)


def _consecutive_routine_checkins(user):
    """Most recent run of consecutive weekly check-ins, no gaps. We
    use form_type=routine assignments and check that every cadence
    period since the first one has a submission."""
    from apps.progress.models import CheckInSubmission, CheckInForm
    submitted = sorted(
        ts.date() for ts in CheckInSubmission.objects.filter(
            client=user,
            status="submitted",
            form__form_type=CheckInForm.ROUTINE,
        ).values_list("submitted_at", flat=True)
    )
    if not submitted:
        return (0, 4)
    # Walk dates, allowing up to 9 days between consecutive routine
    # check-ins (covers weekly-with-a-day-of-slack).
    run = 1
    best = 1
    for i in range(1, len(submitted)):
        if (submitted[i] - submitted[i - 1]).days <= 9:
            run += 1
            best = max(best, run)
        else:
            run = 1
    return (min(best, 4), 4)


def _consecutive_daily_checkins(user):
    """Longest run of consecutive calendar-day daily check-ins."""
    from apps.progress.models import CheckInSubmission, CheckInForm
    submitted_days = sorted(set(
        timezone.localtime(ts).date()
        for ts in CheckInSubmission.objects.filter(
            client=user,
            status="submitted",
            form__form_type=CheckInForm.DAILY,
        ).values_list("submitted_at", flat=True)
    ))
    if not submitted_days:
        return (0, 30)
    best = 1
    run = 1
    for i in range(1, len(submitted_days)):
        if (submitted_days[i] - submitted_days[i - 1]).days == 1:
            run += 1
            best = max(best, run)
        else:
            run = 1
    return (min(best, 30), 30)


def _spotless_month(user):
    """Every active assignment in the last 30 days has at least one
    submission within its expected window. Pragmatic check — for
    daily, we want >=25 submissions in 30 days; for weekly, >=4."""
    from apps.progress.models import (
        ClientCheckInAssignment, CheckInSubmission, CheckInForm,
    )
    now = timezone.now()
    since = now - timedelta(days=30)
    assignments = list(
        ClientCheckInAssignment.objects
        .filter(client=user, is_active=True)
        .select_related("form")
    )
    if not assignments:
        return (0, 1)
    for a in assignments:
        if a.form.form_type == CheckInForm.ONBOARDING:
            continue
        expected = {
            ClientCheckInAssignment.CADENCE_DAILY:    25,    # ~30 days, allow 5 grace
            ClientCheckInAssignment.CADENCE_WEEKLY:   4,
            ClientCheckInAssignment.CADENCE_BIWEEKLY: 2,
            ClientCheckInAssignment.CADENCE_MONTHLY:  1,
        }.get(a.cadence, 1)
        actual = CheckInSubmission.objects.filter(
            client=user, form=a.form, status="submitted",
            submitted_at__gte=since,
        ).count()
        if actual < expected:
            return (0, 1)
    return (1, 1)


def _one_year_client(user):
    """First submitted check-in >= 1 year ago AND a check-in in the
    last 90 days (still actively engaged)."""
    from apps.progress.models import CheckInSubmission
    submissions = list(
        CheckInSubmission.objects.filter(
            client=user, status="submitted",
        ).order_by("submitted_at").values_list("submitted_at", flat=True)
    )
    if not submissions:
        return (0, 1)
    now = timezone.now()
    if (now - submissions[0]).days < 365:
        return (0, 1)
    if (now - submissions[-1]).days > 90:
        return (0, 1)   # client lapsed — not actively a 1-year client
    return (1, 1)


# ---- Nutrition & hydration ---------------------------------------

def _meal_consumption_count(threshold):
    def evaluator(user):
        from apps.nutrition.models import NutritionMealConsumption
        count = NutritionMealConsumption.objects.filter(client=user).count()
        return (count, threshold)
    return evaluator


def _full_day_logged(user):
    """At least one calendar day where every meal in the user's plan
    had a consumption row (item-level or meal-level)."""
    from apps.nutrition.models import (
        NutritionMealConsumption, NutritionMeal,
    )
    profile = getattr(user, "client_profile", None)
    plan = getattr(profile, "assigned_nutrition_plan", None) if profile else None
    if plan is None:
        return (0, 1)
    plan_meal_ids = set(NutritionMeal.objects.filter(
        plan=plan,
    ).values_list("id", flat=True))
    if not plan_meal_ids:
        return (0, 1)
    # Group consumption rows by date → set of meal_ids that had any
    # tick that day. A "full day" is one where every plan meal id is
    # represented.
    by_day = defaultdict(set)
    for c in NutritionMealConsumption.objects.filter(
        client=user, meal_id__in=plan_meal_ids,
    ).values("consumed_on", "meal_id"):
        by_day[c["consumed_on"]].add(c["meal_id"])
    for day, meals_ticked in by_day.items():
        if meals_ticked >= plan_meal_ids:
            return (1, 1)
    return (0, 1)


def _consecutive_full_days_logged(threshold):
    def evaluator(user):
        from apps.nutrition.models import (
            NutritionMealConsumption, NutritionMeal,
        )
        profile = getattr(user, "client_profile", None)
        plan = getattr(profile, "assigned_nutrition_plan", None) if profile else None
        if plan is None:
            return (0, threshold)
        plan_meal_ids = set(NutritionMeal.objects.filter(
            plan=plan,
        ).values_list("id", flat=True))
        if not plan_meal_ids:
            return (0, threshold)
        by_day = defaultdict(set)
        for c in NutritionMealConsumption.objects.filter(
            client=user, meal_id__in=plan_meal_ids,
        ).values("consumed_on", "meal_id"):
            by_day[c["consumed_on"]].add(c["meal_id"])
        full_days = sorted(
            day for day, meals in by_day.items() if meals >= plan_meal_ids
        )
        if not full_days:
            return (0, threshold)
        # Longest run of consecutive full days.
        best = 1
        run = 1
        for i in range(1, len(full_days)):
            if (full_days[i] - full_days[i - 1]).days == 1:
                run += 1
                best = max(best, run)
            else:
                run = 1
        return (min(best, threshold), threshold)
    return evaluator


def _macro_hits_count(user):
    """Number of distinct calendar days the user hit calorie + protein
    targets. A 'hit' means: total kcal eaten >= 90% of target AND
    total protein eaten >= 90% of target. 90% threshold gives some
    slack — exact targets are usually unrealistic."""
    from apps.nutrition.models import (
        NutritionMealConsumption, NutritionMealItem, NutritionMeal,
    )
    profile = getattr(user, "client_profile", None)
    plan = getattr(profile, "assigned_nutrition_plan", None) if profile else None
    if plan is None:
        return 0
    target_kcal = getattr(plan, "calories_target", 0) or 0
    target_protein = getattr(plan, "protein_target", 0) or 0
    if target_kcal <= 0 or target_protein <= 0:
        return 0

    # Pre-compute each meal item's macros so we don't ping the DB for
    # each consumption row.
    items = list(
        NutritionMealItem.objects.filter(meal__plan=plan)
        .values("id", "meal_id", "calories", "protein")
    )
    item_macros = {it["id"]: (it["calories"] or 0, it["protein"] or 0) for it in items}
    meal_macros = defaultdict(lambda: [0, 0])
    for it in items:
        meal_macros[it["meal_id"]][0] += it["calories"] or 0
        meal_macros[it["meal_id"]][1] += it["protein"] or 0

    by_day = defaultdict(lambda: [0.0, 0.0])
    for c in NutritionMealConsumption.objects.filter(client=user).values(
        "consumed_on", "meal_id", "meal_item_id",
    ):
        if c["meal_item_id"] is not None:
            kcal, p = item_macros.get(c["meal_item_id"], (0, 0))
        else:
            kcal, p = meal_macros.get(c["meal_id"], [0, 0])
        by_day[c["consumed_on"]][0] += kcal
        by_day[c["consumed_on"]][1] += p

    hit_days = 0
    for day, (kcal, p) in by_day.items():
        if kcal >= target_kcal * 0.9 and p >= target_protein * 0.9:
            hit_days += 1
    return hit_days


def _macro_hit_day(user):
    return (1 if _macro_hits_count(user) >= 1 else 0, 1)


def _macro_consecutive_days(threshold):
    """Longest run of consecutive macro-hit days."""
    def evaluator(user):
        from apps.nutrition.models import (
            NutritionMealConsumption, NutritionMealItem,
        )
        profile = getattr(user, "client_profile", None)
        plan = getattr(profile, "assigned_nutrition_plan", None) if profile else None
        if plan is None:
            return (0, threshold)
        target_kcal = getattr(plan, "calories_target", 0) or 0
        target_protein = getattr(plan, "protein_target", 0) or 0
        if target_kcal <= 0 or target_protein <= 0:
            return (0, threshold)

        items = list(
            NutritionMealItem.objects.filter(meal__plan=plan)
            .values("id", "meal_id", "calories", "protein")
        )
        item_macros = {it["id"]: (it["calories"] or 0, it["protein"] or 0) for it in items}
        meal_macros = defaultdict(lambda: [0, 0])
        for it in items:
            meal_macros[it["meal_id"]][0] += it["calories"] or 0
            meal_macros[it["meal_id"]][1] += it["protein"] or 0

        by_day = defaultdict(lambda: [0.0, 0.0])
        for c in NutritionMealConsumption.objects.filter(client=user).values(
            "consumed_on", "meal_id", "meal_item_id",
        ):
            if c["meal_item_id"] is not None:
                kcal, p = item_macros.get(c["meal_item_id"], (0, 0))
            else:
                kcal, p = meal_macros.get(c["meal_id"], [0, 0])
            by_day[c["consumed_on"]][0] += kcal
            by_day[c["consumed_on"]][1] += p

        hit_days = sorted(
            d for d, (k, p) in by_day.items()
            if k >= target_kcal * 0.9 and p >= target_protein * 0.9
        )
        if not hit_days:
            return (0, threshold)
        best = 1
        run = 1
        for i in range(1, len(hit_days)):
            if (hit_days[i] - hit_days[i - 1]).days == 1:
                run += 1
                best = max(best, run)
            else:
                run = 1
        return (min(best, threshold), threshold)
    return evaluator


def _hydration_goal_day(user):
    """At least one HydrationLog row where cups >= goal_cups."""
    from apps.progress.models import HydrationLog
    exists = HydrationLog.objects.filter(client=user).extra(
        where=["cups >= goal_cups"],
    ).exists()
    return (1 if exists else 0, 1)


def _hydration_streak(threshold):
    def evaluator(user):
        from apps.progress.models import HydrationLog
        rows = list(
            HydrationLog.objects.filter(client=user)
            .order_by("logged_on")
            .values("logged_on", "cups", "goal_cups")
        )
        hit_days = sorted(
            r["logged_on"] for r in rows if r["cups"] >= r["goal_cups"]
        )
        if not hit_days:
            return (0, threshold)
        best = 1
        run = 1
        for i in range(1, len(hit_days)):
            if (hit_days[i] - hit_days[i - 1]).days == 1:
                run += 1
                best = max(best, run)
            else:
                run = 1
        return (min(best, threshold), threshold)
    return evaluator


# ---- Body composition --------------------------------------------

def _first_weight_logged(user):
    from apps.progress.models import CheckInAnswer
    from apps.users.dashboard_client_views import WEIGHT_FIELD_KEYS
    exists = CheckInAnswer.objects.filter(
        submission__client=user,
        submission__status="submitted",
        value_number__isnull=False,
        question__field_key__in=WEIGHT_FIELD_KEYS,
    ).exists()
    return (1 if exists else 0, 1)


def _weight_loss_kg(threshold_kg):
    def evaluator(user):
        from apps.progress.models import CheckInAnswer
        from apps.users.dashboard_client_views import WEIGHT_FIELD_KEYS
        weights = list(
            CheckInAnswer.objects.filter(
                submission__client=user,
                submission__status="submitted",
                value_number__isnull=False,
                question__field_key__in=WEIGHT_FIELD_KEYS,
            )
            .order_by("submission__submitted_at")
            .values_list("value_number", flat=True)
        )
        if len(weights) < 2:
            return (0, threshold_kg)
        loss = float(weights[0]) - float(weights[-1])
        return (max(0, int(min(loss, threshold_kg))), threshold_kg)
    return evaluator


def _reached_goal_weight(user):
    """Latest weight is at or below the trainer-set goal_weight_kg
    (handles weight-loss goals; for weight-gain goals we'd flip the
    inequality but the v1 product is loss-oriented)."""
    profile = getattr(user, "client_profile", None)
    if profile is None:
        return (0, 1)
    goal = getattr(profile, "goal_weight_kg", None)
    if goal is None:
        return (0, 1)
    bw = _bodyweight_kg(user)
    if bw is None:
        return (0, 1)
    # Treat "within 0.2 kg" as reached so the user isn't punished for
    # daily noise the moment they hit it.
    return (1 if bw <= float(goal) + 0.2 else 0, 1)


def _six_month_transform(user):
    """Has weight history spanning >= 180 days."""
    from apps.progress.models import CheckInAnswer
    from apps.users.dashboard_client_views import WEIGHT_FIELD_KEYS
    timestamps = list(
        CheckInAnswer.objects.filter(
            submission__client=user,
            submission__status="submitted",
            value_number__isnull=False,
            question__field_key__in=WEIGHT_FIELD_KEYS,
        )
        .order_by("submission__submitted_at")
        .values_list("submission__submitted_at", flat=True)
    )
    if len(timestamps) < 2:
        return (0, 1)
    span_days = (timestamps[-1] - timestamps[0]).days
    return (1 if span_days >= 180 else 0, 1)


# =====================================================================
# Onboarding — fires once the user has marked all five setup-strip
# steps done. Read from SoloProfile flags so the evaluator stays
# cheap (no joins).
# =====================================================================
def _setup_strong(user):
    profile = getattr(user, "solo_profile", None)
    if profile is None:
        return (0, 1)
    done = (
        bool(profile.setup_apple_health_done)
        + bool(profile.setup_body_stats_done)
        + bool(profile.setup_goal_done)
        + bool(profile.setup_training_done)
        + bool(profile.setup_nutrition_style_done)
    )
    return (1 if done == 5 else 0, 1)


# =====================================================================
# Master mapping. Every code in seed.TROPHY_CATALOGUE must have a key
# here — `assert_evaluators_match_catalogue()` enforces this in the
# data migration.
# =====================================================================
EVALUATORS = {
    # Workout volume
    "first_workout":         _workout_count(1),
    "five_workouts":         _workout_count(5),
    "ten_workouts":          _workout_count(10),
    "twentyfive_workouts":   _workout_count(25),
    "fifty_workouts":        _workout_count(50),
    "hundred_workouts":      _workout_count(100),
    "twofifty_workouts":     _workout_count(250),
    "fivehundred_workouts":  _workout_count(500),
    "thousand_workouts":     _workout_count(1000),

    "first_thousand_kg":         _total_volume_kg(1_000),
    "ten_thousand_kg":           _total_volume_kg(10_000),
    "fifty_thousand_kg":         _total_volume_kg(50_000),
    "hundred_thousand_kg":       _total_volume_kg(100_000),
    "five_hundred_thousand_kg":  _total_volume_kg(500_000),
    "million_kg_club":           _total_volume_kg(1_000_000),

    # Streaks
    "streak_3":          _streak_days(3),
    "streak_7":          _streak_days(7),
    "streak_14":         _streak_days(14),
    "streak_30":         _streak_days(30),
    "streak_60":         _streak_days(60),
    "streak_100":        _streak_days(100),
    "streak_200":        _streak_days(200),
    "streak_365":        _streak_days(365),
    "comeback":          _comeback,
    "phoenix":           _phoenix,
    "weekend_warrior":   _weekend_warrior,
    "iron_discipline":   _iron_discipline,

    # Frequency
    "three_in_week":     _sessions_in_calendar_window(3, 7),
    "five_in_week":      _sessions_in_calendar_window(5, 7),
    "full_week":         _sessions_in_calendar_window(7, 7),
    "twelve_in_month":   _sessions_in_calendar_window(12, 30),
    "twenty_in_month":   _sessions_in_calendar_window(20, 30),
    "thirty_in_month":   _sessions_in_calendar_window(30, 30),
    "two_a_day":         _has_n_sessions_on_same_day(2),
    "triple_threat":     _has_n_sessions_on_same_day(3),
    "perfect_week":      _perfect_week,
    "perfect_month":     _perfect_month,

    # Personal records
    "first_pr":              _pr_count(1),
    "five_prs":              _pr_count(5),
    "twentyfive_prs":        _pr_count(25),
    "hundred_prs":           _pr_count(100),
    "bench_bodyweight":      _bodyweight_relative(_BENCH_PATTERNS,    1.0),
    "squat_1_5x":            _bodyweight_relative(_SQUAT_PATTERNS,    1.5),
    "deadlift_2x":           _bodyweight_relative(_DEADLIFT_PATTERNS, 2.0),
    "ohp_bodyweight":        _bodyweight_relative(_OHP_PATTERNS,      1.0),
    "three_prs_session":     _three_prs_session,
    "pr_three_weeks":        _pr_three_weeks,
    "triple_digit":          _max_weight_threshold(100),
    "double_triple":         _max_weight_threshold(200),

    # Reps & sets
    "hundred_sets":            _set_count(100),
    "thousand_sets":           _set_count(1_000),
    "ten_thousand_sets":       _set_count(10_000),
    "thousand_reps":           _rep_count(1_000),
    "ten_thousand_reps":       _rep_count(10_000),
    "centurion":               _rep_count(100_000),
    "hundred_reps_exercise":   _max_reps_one_exercise_session(100),
    "five_thousand_session":   _max_session_volume(5_000),

    # Time-of-day & special days
    "early_bird":         _session_finished_in_hour_range(0, 6),
    "night_owl":          _session_finished_in_hour_range(22, 24),
    "midnight_iron":      _session_finished_in_hour_range(0, 1),
    "lunch_hero":         _session_finished_in_hour_range(12, 13),
    "sunday_soldier":     _session_on_weekday(6),
    "monday_motivated":   _monday_motivated,
    "christmas_day":      _session_on_specific_date(12, 25),
    "new_years_day":      _session_on_specific_date(1, 1),
    "birthday_workout":   _birthday_workout,
    "quick_finisher":     _session_under_duration(30 * 60),
    "endurance_test":     _session_with_duration(90 * 60),
    "two_hour_beast":     _session_with_duration(120 * 60),

    # Check-ins
    "first_checkin":          _checkin_count(1),
    "ten_checkins":           _checkin_count(10),
    "twentyfive_checkins":    _checkin_count(25),
    "fifty_checkins":         _checkin_count(50),
    "hundred_checkins":       _checkin_count(100),
    "first_photo":            _checkin_with_photo,
    "photo_comparison":       _photo_comparison,
    "onboarding_complete":    _onboarding_complete,
    "four_weekly_streak":     _consecutive_routine_checkins,
    "thirty_daily_streak":    _consecutive_daily_checkins,
    "spotless_month":         _spotless_month,
    "one_year_client":        _one_year_client,

    # Nutrition & hydration
    "first_meal_logged":     _meal_consumption_count(1),
    "full_day_logged":       _full_day_logged,
    "seven_days_logged":     _consecutive_full_days_logged(7),
    "thirty_days_logged":    _consecutive_full_days_logged(30),
    "hundred_meals":         _meal_consumption_count(100),
    "thousand_meals":        _meal_consumption_count(1_000),
    "macro_hit_day":         _macro_hit_day,
    "macro_week":            _macro_consecutive_days(7),
    "macro_month":           _macro_consecutive_days(30),
    "eight_cups_day":        _hydration_goal_day,
    "seven_day_hydration":   _hydration_streak(7),
    "hundred_days_hydrated": _hydration_streak(100),

    # Body composition
    "first_weight_logged":     _first_weight_logged,
    "lost_2_5":                _weight_loss_kg(2.5),
    "lost_5":                  _weight_loss_kg(5),
    "lost_10":                 _weight_loss_kg(10),
    "lost_20":                 _weight_loss_kg(20),
    "reached_goal_weight":     _reached_goal_weight,
    "six_month_transform":     _six_month_transform,

    # Onboarding
    "set_up_strong":           _setup_strong,
}


def assert_evaluators_match_catalogue():
    """Verify every catalogue code has an evaluator and vice versa.
    Called from the data migration so a missing evaluator can never
    ship as a silent bug."""
    from .seed import TROPHY_CATALOGUE
    catalogue_codes = {entry[0] for entry in TROPHY_CATALOGUE}
    evaluator_codes = set(EVALUATORS.keys())
    missing_evaluators = catalogue_codes - evaluator_codes
    extra_evaluators   = evaluator_codes - catalogue_codes
    if missing_evaluators:
        raise ValueError(
            f"Trophies in catalogue without evaluators: {sorted(missing_evaluators)}"
        )
    if extra_evaluators:
        raise ValueError(
            f"Evaluators without matching catalogue entry: {sorted(extra_evaluators)}"
        )
    return True
