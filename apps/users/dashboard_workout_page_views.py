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


@login_required
def trainer_workout_plans_page(request):
    if not trainer_required(request):
        return redirect("landing-page")

    context = dashboard_context(request, "Workout Plans")
    return render(request, "dashboard/dashboard_workout_plans.html", context)


@login_required
def trainer_workout_plan_detail_page(request, plan_id):
    if not trainer_required(request):
        return redirect("landing-page")

    plan = get_object_or_404(
        WorkoutPlan.objects.prefetch_related("days__exercises__sets"),
        id=plan_id,
        user=request.user,
    )

    days = plan.days.all().order_by("order")

    add_exercise_forms = {
        day.id: AddExerciseToDayForm(
            trainer_user=request.user,
            initial={"workout_day_id": day.id}
        )
        for day in days
    }

    day_edit_forms = {
        day.id: UpdateWorkoutDayForm(
            initial={
                "title": day.title,
                "order": day.order,
            }
        )
        for day in days
    }

    exercise_edit_forms = {}
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

    context = dashboard_context(request, f"Plan: {plan.name}")
    context.update({
        "plan": plan,
        "days": days,
        "create_day_form": CreateWorkoutDayForm(),
        "add_exercise_forms": add_exercise_forms,
        "plan_edit_form": UpdateWorkoutPlanForm(initial={"name": plan.name}),
        "day_edit_forms": day_edit_forms,
        "exercise_edit_forms": exercise_edit_forms,
    })
    return render(request, "dashboard/workout_plan_detail.html", context)
