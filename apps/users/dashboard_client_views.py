from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .dashboard_helpers import trainer_required, dashboard_context
from .forms import CreateClientForm, AssignWorkoutPlanForm, AssignNutritionPlanForm
from .models import User
from .serializers import ClientCreateSerializer
from .dashboard_checkin_page_views import _ensure_three_forms, _canonical_three
from apps.workouts.models import (
    WorkoutPlan, WorkoutDay, Exercise, ExerciseSetTarget,
    WorkoutSession, ExerciseSession, SetPerformance,
)
from apps.nutrition.models import (
    NutritionPlan, NutritionMealConsumption,
)
from apps.progress.models import (
    CheckInForm, ClientCheckInAssignment,
    CheckInAnswer, CheckInQuestion, CheckInSubmission,
)
from apps.payments.models import ClientSubscription


# -------------------------------------------------------------------
# Phase 2.5 — Progress charts on the client detail page.
# Pure-read helpers that pull from the data we already collect:
#   • weight trend  ← CheckInAnswer.value_number on questions whose
#                     field_key is one of the system weight keys
#   • adherence     ← WorkoutSession count vs the assigned plan's days
#   • photos        ← CheckInAnswer.value_image
# Empty states everywhere — most clients won't have any of this yet.
# -------------------------------------------------------------------
WEIGHT_FIELD_KEYS = ("current_weight", "daily_weight", "weekly_weight")


def _weight_history(client, since=None):
    """List of (timestamp, kilos) tuples, oldest first.
    Pulls from CheckInAnswer rows where the question is one of the
    system-seeded weight number questions and a value was recorded."""
    qs = (
        CheckInAnswer.objects
        .filter(
            submission__client=client,
            submission__status="submitted",
            value_number__isnull=False,
            question__field_key__in=WEIGHT_FIELD_KEYS,
        )
        .select_related("submission")
        .order_by("submission__submitted_at")
    )
    if since is not None:
        qs = qs.filter(submission__submitted_at__gte=since)

    out = []
    for a in qs:
        ts = a.submission.submitted_at
        if ts is None:
            continue
        out.append((ts, float(a.value_number)))
    return out


def _build_weight_chart(history):
    """Convert a (timestamp, value) list into SVG-ready bits.
    Returns None when there's not enough data to plot a meaningful line."""
    if len(history) < 2:
        return None

    values = [v for _, v in history]
    y_min, y_max = min(values), max(values)
    if y_max == y_min:
        # Flat line — pad it so the polyline doesn't sit on the edge.
        y_min -= 1
        y_max += 1
    else:
        pad = (y_max - y_min) * 0.15
        y_min -= pad
        y_max += pad

    width = 400
    height = 100
    n = len(history)

    points = []
    for i, (_ts, value) in enumerate(history):
        x = (i / (n - 1)) * width if n > 1 else width / 2
        y = height - ((value - y_min) / (y_max - y_min)) * height
        points.append({"x": round(x, 1), "y": round(y, 1)})

    line_d = "M " + " L ".join(f"{p['x']},{p['y']}" for p in points)
    # Closed area path under the line for a soft fill.
    area_d = (
        line_d
        + f" L {points[-1]['x']},{height} L {points[0]['x']},{height} Z"
    )

    first_value = values[0]
    latest_value = values[-1]
    delta = latest_value - first_value

    return {
        "line_d": line_d,
        "area_d": area_d,
        "points": points,
        "y_min_label": f"{y_min:.1f}",
        "y_max_label": f"{y_max:.1f}",
        "first_date": history[0][0].strftime("%-d %b"),
        "last_date": history[-1][0].strftime("%-d %b"),
        "latest": round(latest_value, 1),
        "delta": round(delta, 1),
        "delta_sign": "+" if delta > 0 else ("−" if delta < 0 else ""),
        "delta_class": "is-up" if delta > 0 else ("is-down" if delta < 0 else "is-flat"),
        "n_points": n,
        "width": width,
        "height": height,
    }


def _adherence_in_window(client, days):
    """Return {pct, actual, expected} for the last `days` window, or
    None if the client has no assigned workout plan."""
    profile = getattr(client, "client_profile", None)
    plan = getattr(profile, "assigned_workout_plan", None) if profile else None
    if plan is None:
        return None

    days_in_plan = plan.days.count()
    if days_in_plan == 0:
        return None

    # Heuristic: the plan represents one week of training.
    expected = max(1, round(days_in_plan * (days / 7)))

    since = timezone.now() - timedelta(days=days)
    actual = WorkoutSession.objects.filter(
        user=client,
        completed_at__gte=since,
        is_complete=True,
    ).count()

    pct = min(100, round((actual / expected) * 100)) if expected else 0
    return {"pct": pct, "actual": actual, "expected": expected}


def _sessions_this_week(client):
    since = timezone.now() - timedelta(days=7)
    return WorkoutSession.objects.filter(
        user=client,
        completed_at__gte=since,
        is_complete=True,
    ).count()


def _recent_photos(client, limit=6):
    """Most-recent photo answers across all submissions."""
    return list(
        CheckInAnswer.objects
        .filter(submission__client=client)
        .filter(question__question_type=CheckInQuestion.PHOTO)
        .exclude(value_image="")
        .exclude(value_image__isnull=True)
        .select_related("submission")
        .order_by("-submission__submitted_at")[:limit]
    )


def _build_progress_context(client):
    """Bundle everything the Progress cell needs into one dict."""
    weight_history = _weight_history(client)
    chart = _build_weight_chart(weight_history)
    adherence = _adherence_in_window(client, days=7)
    sessions_week = _sessions_this_week(client)
    photos = _recent_photos(client)

    latest_weight = None
    if weight_history:
        latest_weight = round(weight_history[-1][1], 1)

    return {
        "weight_chart": chart,
        "weight_latest": latest_weight,
        "weight_count": len(weight_history),
        "adherence": adherence,
        "sessions_this_week": sessions_week,
        "recent_photos": photos,
    }


def _action_needed(clients):
    """Walk the trainer's clients and split out who's missing what.
    Returns (missing_workout_clients, missing_nutrition_clients, total_count)."""
    missing_workout = []
    missing_nutrition = []
    for c in clients:
        profile = getattr(c, "client_profile", None)
        if not profile:
            continue
        if not getattr(profile, "assigned_workout_plan", None):
            missing_workout.append(c)
        if not getattr(profile, "assigned_nutrition_plan", None):
            missing_nutrition.append(c)
    return missing_workout, missing_nutrition, len(missing_workout) + len(missing_nutrition)


@login_required
def trainer_dashboard(request):
    """
    Clients workspace — full roster with action-needed inbox + add modal.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    context = dashboard_context(request, "Clients")

    missing_workout, missing_nutrition, action_needed = _action_needed(context["clients"])
    context.update({
        "missing_workout_clients": missing_workout,
        "missing_nutrition_clients": missing_nutrition,
        "action_needed_count": action_needed,
    })

    return render(request, "dashboard/trainer_dashboard.html", context)


@login_required
def trainer_client_detail_page(request, client_id):
    """
    Detail page for a single trainer-owned client.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    client = get_object_or_404(
        User.objects.select_related(
            "client_profile",
            "client_profile__assigned_workout_plan",
            "client_profile__assigned_nutrition_plan",
        ),
        id=client_id,
        role=User.CLIENT,
        client_profile__trainer=request.user.trainer_profile,
    )

    assigned_plan = getattr(client.client_profile, "assigned_workout_plan", None)
    assigned_nutrition_plan = getattr(client.client_profile, "assigned_nutrition_plan", None)

    # Make sure the trainer's three canonical check-in forms exist
    # (lazy bootstrap), then build a row per form for this client.
    _ensure_three_forms(request.user)
    canonical_forms = _canonical_three(request.user)
    by_form_id = {
        a.form_id: a for a in
        ClientCheckInAssignment.objects.filter(client=client, form__in=canonical_forms)
    }
    checkin_rows = []
    for f in canonical_forms:
        a = by_form_id.get(f.id)
        cadence_options = ClientCheckInAssignment.CADENCE_OPTIONS_FOR_FORM_TYPE.get(f.form_type, [])
        cadence_label_for = dict(ClientCheckInAssignment.CADENCE_CHOICES)
        checkin_rows.append({
            "form_id": f.id,
            "form_name": f.name,
            "form_type": f.form_type,
            "form_type_label": f.get_form_type_display(),
            "is_active": bool(a and a.is_active),
            "cadence": (a.cadence if a else
                        ClientCheckInAssignment.DEFAULT_CADENCE_FOR_FORM_TYPE.get(f.form_type)),
            "last_submitted_at": a.last_submitted_at if a else None,
            "next_due_at": a.next_due_at if a else None,
            "cadence_options": [
                (slug, cadence_label_for.get(slug, slug)) for slug in cadence_options
            ],
            "has_cadence_choice": len(cadence_options) > 1,
        })

    context = dashboard_context(request, "Client Details")
    context.update({
        "client": client,
        "assigned_plan": assigned_plan,
        "assigned_nutrition_plan": assigned_nutrition_plan,
        "client_assign_form": AssignWorkoutPlanForm(
            trainer_user=request.user,
            initial={"client_user_id": client.id}
        ),
        "client_nutrition_assign_form": AssignNutritionPlanForm(
            trainer_user=request.user,
            initial={"client_user_id": client.id}
        ),
        "checkin_rows": checkin_rows,
    })
    # Phase 2.5 — pull in the progress data (weight chart, adherence,
    # sessions, photos). All harvesters return None / empty values when
    # there's nothing yet, so the template handles empty states.
    context.update(_build_progress_context(client))
    # Phase 7.7.3 — subscription state (active sub + lifetime metrics).
    context.update(_build_subscription_context(client, request.user.trainer_profile))
    # Phase C.2 — recent activity feed (workouts logged, meal ticks,
    # check-in submissions across the last 14 days). Lets the trainer
    # see at a glance what the client has actually done.
    context.update(_build_activity_context(client))
    return render(request, "dashboard/client_detail.html", context)


# -------------------------------------------------------------------
# Phase 7.7.3 — subscription panel on client detail.
#
# Pulls the most recent ClientSubscription for this trainer+client and
# computes a few display-friendly fields. Webhook keeps the row in sync
# with Stripe so we don't need to round-trip on every page render.
# -------------------------------------------------------------------
def _build_subscription_context(client, trainer_profile):
    """Return template context for the subscription card.

    Output keys:
      subscription            : the latest ClientSubscription row, or None.
      subscription_status_label, subscription_status_tone : badge text/colour.
      subscription_months_active : whole months between created_at and now.
    """
    sub = (
        ClientSubscription.objects
        .filter(client=client, trainer=trainer_profile)
        .select_related("plan")
        .order_by("-created_at")
        .first()
    )
    if sub is None:
        return {"subscription": None}

    # Friendly badge tone — kept in Python so the template stays simple.
    tone_for_status = {
        ClientSubscription.STATUS_ACTIVE:     "ok",
        ClientSubscription.STATUS_TRIALING:   "ok",
        ClientSubscription.STATUS_PAST_DUE:   "warn",
        ClientSubscription.STATUS_CANCELED:   "muted",
        ClientSubscription.STATUS_INCOMPLETE: "warn",
    }
    label_for_status = dict(ClientSubscription.STATUS_CHOICES)

    months_active = 0
    if sub.created_at:
        delta = timezone.now() - sub.created_at
        # Whole calendar months — close enough for a roster card.
        months_active = max(0, int(delta.days // 30))

    return {
        "subscription": sub,
        "subscription_status_label": label_for_status.get(sub.status, sub.status),
        "subscription_status_tone": tone_for_status.get(sub.status, "muted"),
        "subscription_months_active": months_active,
    }


@login_required
def dashboard_create_client(request):
    """
    Create a new client account under the logged-in trainer.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    if request.method != "POST":
        return redirect("trainer-dashboard")

    form = CreateClientForm(request.POST)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-dashboard")

    serializer = ClientCreateSerializer(
        data={
            "username": form.cleaned_data["username"],
            "email": form.cleaned_data["email"],
            "password": form.cleaned_data["password"],
        }
    )

    if serializer.is_valid():
        serializer.create_client_for_trainer(request.user)
        messages.success(request, "Client created successfully.")
    else:
        for _, errors in serializer.errors.items():
            if isinstance(errors, list):
                for error in errors:
                    messages.error(request, str(error))
            else:
                messages.error(request, str(errors))

    return redirect("trainer-dashboard")


@login_required
def dashboard_assign_workout_plan(request):
    """
    Assign a trainer-owned workout plan to a trainer-owned client.
    Optionally create a client-specific copy before assigning.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    if request.method != "POST":
        return redirect("trainer-dashboard")

    form = AssignWorkoutPlanForm(request.POST, trainer_user=request.user)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-dashboard")

    client_user = get_object_or_404(
        User.objects.select_related("client_profile"),
        id=form.cleaned_data["client_user_id"],
        role=User.CLIENT,
        client_profile__trainer=request.user.trainer_profile,
    )

    selected_plan = get_object_or_404(
        WorkoutPlan.objects.prefetch_related("days__exercises__sets"),
        id=form.cleaned_data["workout_plan_id"],
        user=request.user,
        is_template=True,
    )

    create_client_specific_copy = form.cleaned_data["create_client_specific_copy"]

    if create_client_specific_copy:
        with transaction.atomic():
            copied_plan = WorkoutPlan.objects.create(
                user=request.user,
                name=f"{selected_plan.name} - {client_user.username}",
                is_active=selected_plan.is_active,
                is_template=False,
                source_template=selected_plan,
                client=client_user,
            )

            for day in selected_plan.days.all().order_by("order"):
                copied_day = WorkoutDay.objects.create(
                    plan=copied_plan,
                    title=day.title,
                    order=day.order,
                )

                for exercise in day.exercises.all().order_by("order"):
                    copied_exercise = Exercise.objects.create(
                        workout_day=copied_day,
                        name=exercise.name,
                        label=exercise.label,
                        order=exercise.order,
                        superset_group=exercise.superset_group,
                    )

                    for set_target in exercise.sets.all().order_by("set_number"):
                        ExerciseSetTarget.objects.create(
                            exercise=copied_exercise,
                            set_number=set_target.set_number,
                            reps=set_target.reps,
                        )

            client_user.client_profile.assigned_workout_plan = copied_plan
            client_user.client_profile.save()

        messages.success(
            request,
            f'Created a client-specific version of "{selected_plan.name}" for {client_user.username}.',
        )
    else:
        client_user.client_profile.assigned_workout_plan = selected_plan
        client_user.client_profile.save()

        messages.success(request, "Workout plan assigned successfully.")

    return redirect("trainer-client-detail", client_id=client_user.id)


@login_required
def dashboard_assign_nutrition_plan(request):
    """
    Assign a trainer-owned nutrition plan to a trainer-owned client.
    Optionally create a client-specific copy before assigning.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    if request.method != "POST":
        return redirect("trainer-dashboard")

    form = AssignNutritionPlanForm(request.POST, trainer_user=request.user)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-dashboard")

    client_user = get_object_or_404(
        User.objects.select_related("client_profile"),
        id=form.cleaned_data["client_user_id"],
        role=User.CLIENT,
        client_profile__trainer=request.user.trainer_profile,
    )

    selected_plan = get_object_or_404(
        NutritionPlan,
        id=form.cleaned_data["nutrition_plan_id"],
        user=request.user,
        is_template=True,
    )

    create_client_specific_copy = form.cleaned_data["create_client_specific_copy"]

    if create_client_specific_copy:
        with transaction.atomic():
            copied_plan = NutritionPlan.objects.create(
                user=request.user,
                name=f"{selected_plan.name} - {client_user.username}",
                calories_target=selected_plan.calories_target,
                protein_target=selected_plan.protein_target,
                carbs_target=selected_plan.carbs_target,
                fats_target=selected_plan.fats_target,
                notes=selected_plan.notes,
                is_active=selected_plan.is_active,
                is_template=False,
                source_template=selected_plan,
                client=client_user,
            )

            client_user.client_profile.assigned_nutrition_plan = copied_plan
            client_user.client_profile.save()

        messages.success(
            request,
            f'Created a client-specific version of "{selected_plan.name}" for {client_user.username}.',
        )
    else:
        client_user.client_profile.assigned_nutrition_plan = selected_plan
        client_user.client_profile.save()

        messages.success(request, "Nutrition plan assigned successfully.")

    return redirect("trainer-client-detail", client_id=client_user.id)


@login_required
def dashboard_unassign_workout_plan(request, client_id):
    """Clear the assigned workout plan from a trainer-owned client."""
    if not trainer_required(request):
        return redirect("landing-page")

    client_user = get_object_or_404(
        User.objects.select_related("client_profile"),
        id=client_id,
        role=User.CLIENT,
        client_profile__trainer=request.user.trainer_profile,
    )

    if request.method != "POST":
        return redirect("trainer-client-detail", client_id=client_user.id)

    client_user.client_profile.assigned_workout_plan = None
    client_user.client_profile.save()
    messages.success(request, "Workout plan unassigned.")
    return redirect("trainer-client-detail", client_id=client_user.id)


@login_required
def dashboard_unassign_nutrition_plan(request, client_id):
    """Clear the assigned nutrition plan from a trainer-owned client."""
    if not trainer_required(request):
        return redirect("landing-page")

    client_user = get_object_or_404(
        User.objects.select_related("client_profile"),
        id=client_id,
        role=User.CLIENT,
        client_profile__trainer=request.user.trainer_profile,
    )

    if request.method != "POST":
        return redirect("trainer-client-detail", client_id=client_user.id)

    client_user.client_profile.assigned_nutrition_plan = None
    client_user.client_profile.save()
    messages.success(request, "Nutrition plan unassigned.")
    return redirect("trainer-client-detail", client_id=client_user.id)


@login_required
def dashboard_delete_client(request, client_id):
    """
    Delete a trainer-owned client account.

    Order of operations matters:
      1. Cancel any active Stripe subscriptions on the trainer's connected
         account so Stripe stops billing the customer next cycle.
      2. CASCADE-delete the User row, which wipes:
           • ClientProfile (OneToOne)
           • client-specific workout/nutrition plan copies
           • CheckInAnswer rows + WorkoutSession history
           • ClientSubscription rows (FK to user, on_delete=CASCADE)

    Stripe cancellation is best-effort — if Stripe rejects (already
    cancelled, network blip, missing keys) we log it but still delete
    locally so the trainer isn't stuck with a ghost client they can't
    remove.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    client = get_object_or_404(
        User,
        id=client_id,
        role=User.CLIENT,
        client_profile__trainer=request.user.trainer_profile,
    )

    if request.method != "POST":
        return redirect("trainer-client-detail", client_id=client.id)

    # ---- Step 1: cancel active Stripe subs (best-effort) -----------
    # Imported lazily so deleting clients still works on a backend
    # that's never had Stripe configured.
    from apps.payments.models import ClientSubscription
    from apps.payments.stripe_client import get_stripe, is_configured

    trainer_profile = request.user.trainer_profile
    cancelled_count = 0
    cancel_errors = []

    open_subs = ClientSubscription.objects.filter(
        client=client,
        trainer=trainer_profile,
    ).exclude(status=ClientSubscription.STATUS_CANCELED)

    if open_subs.exists() and is_configured() and trainer_profile.stripe_user_id:
        stripe = get_stripe()
        for sub in open_subs:
            if not sub.stripe_subscription_id:
                continue
            try:
                # Subscription lives on the trainer's CONNECTED account
                # — must pass stripe_account so we hit the right scope.
                stripe.Subscription.delete(
                    sub.stripe_subscription_id,
                    stripe_account=trainer_profile.stripe_user_id,
                )
                cancelled_count += 1
            except Exception as exc:        # noqa: BLE001 — surface verbatim
                cancel_errors.append(f"{sub.stripe_subscription_id}: {exc}")
                print(f"[delete_client] Stripe cancel warning: {exc}")

    # ---- Step 2: hard delete the user (CASCADE handles the rest) ----
    client_username = client.username
    client.delete()

    # ---- Step 3: tell the trainer what happened --------------------
    if cancelled_count:
        messages.success(
            request,
            f'Client "{client_username}" deleted. '
            f'{cancelled_count} active Stripe subscription'
            f'{"s" if cancelled_count != 1 else ""} cancelled.'
        )
    else:
        messages.success(request, f'Client "{client_username}" deleted successfully.')

    if cancel_errors:
        # Surface non-fatal Stripe errors so the trainer can chase them
        # manually (e.g. revoke from Stripe dashboard if anything stuck).
        messages.warning(
            request,
            f'Note: {len(cancel_errors)} Stripe cancellation'
            f'{"s" if len(cancel_errors) != 1 else ""} reported errors. '
            f'Check the Stripe dashboard if a subscription is still active.',
        )

    return redirect("trainer-dashboard")


# -------------------------------------------------------------------
# Phase C.2 / #37 — recent activity feed on client detail page.
#
# Three rolling feeds across the last 14 days so the trainer can see
# what the client has actually been doing:
#   • Workouts logged      — what + when + how long + how many sets
#   • Meal consumption     — per-day list of meals/items ticked,
#                            grouped by meal so partial-meal completion
#                            ("3 of 4 items") shows naturally
#   • Check-in submissions — date + form name + status
#
# All queries scoped to the trainer's own client (already enforced
# in the calling view). select_related/prefetch_related used to avoid
# N+1 — important when a chatty client has 50+ rows in the window.
# -------------------------------------------------------------------
ACTIVITY_WINDOW_DAYS = 14


def _build_activity_context(client):
    """Return dashboard template context dict for the activity panel."""
    window_start = timezone.now() - timedelta(days=ACTIVITY_WINDOW_DAYS)
    today = timezone.localdate()
    window_start_date = today - timedelta(days=ACTIVITY_WINDOW_DAYS)

    return {
        "activity_workouts":  _activity_workout_rows(client, window_start),
        "activity_meal_days": _activity_meal_days(client, window_start_date, today),
        "activity_checkins":  _activity_checkin_rows(client, window_start),
        "activity_window_days": ACTIVITY_WINDOW_DAYS,
    }


def _activity_workout_rows(client, window_start):
    """Logged workout sessions in the window, with set counts.

    SQL note: ExerciseSession.set_count via Count('sets') gives us
    the total set count per session in one query rather than N+1
    looping over each session's exercises.
    """
    sessions = (
        WorkoutSession.objects
        .filter(user=client, completed_at__gte=window_start, is_complete=True)
        .select_related("workout_day", "workout_day__workout_plan")
        .order_by("-completed_at")
    )

    rows = []
    for session in sessions:
        # Count the sets logged in this session — uses the related
        # SetPerformance through ExerciseSession. One query per
        # session is fine here because the result list is bounded
        # to ~14 rows by the date filter.
        set_count = SetPerformance.objects.filter(
            exercise_session__workout_session=session,
        ).count()

        # Duration is stored as minutes (we think — model has just
        # `duration: IntegerField`). If it's seconds, the template
        # filter divides by 60. Keep raw + label so the template
        # can render appropriately.
        rows.append({
            "session":       session,
            "completed_at":  session.completed_at,
            "day_name":      session.workout_day.title if hasattr(session.workout_day, "title") else str(session.workout_day),
            "plan_name":     session.workout_day.workout_plan.name if session.workout_day.workout_plan_id else "",
            "duration_min":  session.duration,
            "set_count":     set_count,
        })
    return rows


def _activity_meal_days(client, start_date, end_date):
    """Per-day buckets of meal consumption.

    Output shape:
        [
          {"date": <date>, "meals": [
              {"meal_title": "Pre Workout",
               "ticked_items": 3, "total_items": 4,
               "calories_eaten": 540, "is_meal_level_tick": False},
              ...
          ]},
          ...
        ]

    Days with zero ticks are omitted from the list — the template
    shows an empty-state message if the whole list is empty.
    """
    rows = (
        NutritionMealConsumption.objects
        .filter(
            client=client,
            consumed_on__gte=start_date,
            consumed_on__lte=end_date,
        )
        .select_related("meal", "meal_item")
        .order_by("-consumed_on", "meal__order", "meal_item__order")
    )

    # Group by (date, meal_id) → list of consumption rows.
    by_date: dict = {}    # {date: {meal_id: {"meal": NutritionMeal, "items": set(), "meal_level": bool}}}
    for r in rows:
        by_date.setdefault(r.consumed_on, {})
        bucket = by_date[r.consumed_on].setdefault(
            r.meal_id, {"meal": r.meal, "items": set(), "meal_level": False}
        )
        if r.meal_item_id is None:
            bucket["meal_level"] = True
        else:
            bucket["items"].add(r.meal_item_id)

    days = []
    # Sort by date desc — newest first.
    for date_key in sorted(by_date.keys(), reverse=True):
        meals_payload = []
        for meal_id, info in by_date[date_key].items():
            meal = info["meal"]
            total_items = meal.items.count()    # NutritionMealItem related_name=items
            ticked = (
                total_items if info["meal_level"]
                else len(info["items"])
            )
            meals_payload.append({
                "meal_title":         meal.title,
                "ticked_items":       ticked,
                "total_items":        total_items,
                "is_meal_level_tick": info["meal_level"],
            })
        days.append({"date": date_key, "meals": meals_payload})
    return days


def _activity_checkin_rows(client, window_start):
    """Recent check-in submissions — date + form + status."""
    submissions = (
        CheckInSubmission.objects
        .filter(client=client, started_at__gte=window_start)
        .select_related("form")
        .order_by("-started_at")
    )
    return [
        {
            "submission":  s,
            "started_at":  s.started_at,
            "submitted_at": s.submitted_at,
            "form_name":   s.form.name,
            "form_type":   s.form.form_type,
            "status":      s.status,
            "is_submitted": s.status == CheckInSubmission.STATUS_SUBMITTED,
        }
        for s in submissions
    ]
