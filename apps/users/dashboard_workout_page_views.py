from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .dashboard_helpers import trainer_required, dashboard_context
from .forms import CreateWorkoutDayForm, AddExerciseToDayForm
from apps.workouts.models import WorkoutPlan


@login_required
def trainer_workout_plans_page(request):
    """
    Workout library page showing exercise presets and workout plan templates.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    context = dashboard_context(request, "Workout Plans")
    return render(request, "dashboard/dashboard_workout_plans.html", context)


@login_required
def trainer_workout_plan_detail_page(request, plan_id):
    """
    Detail editor for a single trainer-owned workout plan template.
    Trainers can add days and add exercise presets into each day.
    """
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

    context = dashboard_context(request, f"Plan: {plan.name}")
    context.update({
        "plan": plan,
        "days": days,
        "create_day_form": CreateWorkoutDayForm(),
        "add_exercise_forms": add_exercise_forms,
    })
    return render(request, "dashboard/workout_plan_detail.html", context)
