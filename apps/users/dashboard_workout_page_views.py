"""
Workouts workspace page views.

Restructure v2: collapses the old "list of plans" page and the
"plan detail" page into a single Workouts workspace. The same template
renders both routes:

    /dashboard/                       → newest plan auto-selected
    /dashboard/workout-plans/         → newest plan auto-selected
    /dashboard/workout-plans/<id>/    → that specific plan in the canvas

Both routes go through `_render_workouts_workspace` so the behaviour
stays in one place and the JS builder boots the same way regardless of
how the user landed.
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .dashboard_helpers import trainer_required, dashboard_context
from .forms import (
    CreateWorkoutDayForm,
    AddExerciseToDayForm,
    UpdateWorkoutPlanForm,
    UpdateWorkoutDayForm,
    UpdateExerciseForm,
)
from apps.workouts.models import WorkoutPlan


def _render_workouts_workspace(request, plan_id=None):
    """
    Render the Workouts workspace template with a target plan selected.

    If `plan_id` is None we pick the most-recently-created plan owned by
    the trainer. If the trainer has no plans yet we render the same
    template with `plan=None`, which triggers the first-run create-a-plan
    card inside `dashboard_workouts.html`.
    """
    plans_qs = (
        WorkoutPlan.objects
        .filter(user=request.user)
        .order_by("-id")
    )

    plan = None
    if plan_id is not None:
        plan = get_object_or_404(
            WorkoutPlan.objects.prefetch_related("days__exercises__sets"),
            id=plan_id,
            user=request.user,
        )
    else:
        plan = plans_qs.prefetch_related("days__exercises__sets").first()

    days = []
    add_exercise_forms = {}
    day_edit_forms = {}
    exercise_edit_forms = {}

    if plan is not None:
        days = plan.days.all().order_by("order")

        add_exercise_forms = {
            day.id: AddExerciseToDayForm(
                trainer_user=request.user,
                initial={"workout_day_id": day.id},
            )
            for day in days
        }

        day_edit_forms = {
            day.id: UpdateWorkoutDayForm(
                initial={"title": day.title, "order": day.order},
            )
            for day in days
        }

        for day in days:
            for exercise in day.exercises.all().order_by("order"):
                first_set = exercise.sets.order_by("set_number").first()
                exercise_edit_forms[exercise.id] = UpdateExerciseForm(
                    initial={
                        "label": exercise.label,
                        "order": exercise.order,
                        "superset_group": exercise.superset_group,
                        "set_count": exercise.sets.count(),
                        "reps": first_set.reps if first_set else "",
                    }
                )

    page_title = f"Plan: {plan.name}" if plan else "Workouts"
    context = dashboard_context(request, page_title)
    context.update({
        "plan": plan,
        "days": days,
        "create_day_form": CreateWorkoutDayForm(),
        "add_exercise_forms": add_exercise_forms,
        "plan_edit_form": UpdateWorkoutPlanForm(initial={"name": plan.name}) if plan else None,
        "day_edit_forms": day_edit_forms,
        "exercise_edit_forms": exercise_edit_forms,
    })
    return render(request, "dashboard/dashboard_workouts.html", context)


@login_required
def trainer_workout_plans_page(request):
    """
    Legacy route /dashboard/workout-plans/ — same as the dashboard home.
    Kept so old bookmarks don't 404 and so the URL name stays valid.
    """
    if not trainer_required(request):
        return redirect("landing-page")
    return _render_workouts_workspace(request, plan_id=None)


@login_required
def trainer_workout_plan_detail_page(request, plan_id):
    """
    Deep-link to a specific plan in the Workouts workspace.
    """
    if not trainer_required(request):
        return redirect("landing-page")
    return _render_workouts_workspace(request, plan_id=plan_id)
