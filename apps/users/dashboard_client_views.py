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
from apps.workouts.models import WorkoutPlan, WorkoutDay, Exercise, ExerciseSetTarget, WorkoutSession
from apps.nutrition.models import NutritionPlan
from apps.progress.models import CheckInForm, ClientCheckInAssignment, CheckInAnswer, CheckInQuestion


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
    return render(request, "dashboard/client_detail.html", context)


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
    Client-specific workout/nutrition plans linked to that client will also be removed automatically.
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

    client_username = client.username
    client.delete()

    messages.success(request, f'Client "{client_username}" deleted successfully.')
    return redirect("trainer-dashboard")
