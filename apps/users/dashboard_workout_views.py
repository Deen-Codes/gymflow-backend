from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .dashboard_helpers import trainer_required, dashboard_context
from .forms import (
    CreateExerciseLibraryItemForm,
    CreateWorkoutPlanForm,
    CreateWorkoutDayForm,
    AddExerciseToDayForm,
)
from apps.workouts.models import (
    WorkoutPlan,
    WorkoutDay,
    Exercise,
    ExerciseSetTarget,
    ExerciseLibraryItem,
)


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


@login_required
def dashboard_create_exercise_library_item(request):
    """
    Create a reusable exercise preset owned by the trainer.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    if request.method != "POST":
        return redirect("trainer-workout-plans-page")

    form = CreateExerciseLibraryItemForm(request.POST)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-workout-plans-page")

    ExerciseLibraryItem.objects.create(
        user=request.user,
        name=form.cleaned_data["name"],
        video_url=form.cleaned_data["video_url"],
        coaching_notes=form.cleaned_data["coaching_notes"],
    )

    messages.success(request, "Exercise preset created successfully.")
    return redirect("trainer-workout-plans-page")


@login_required
def dashboard_create_workout_plan(request):
    """
    Create a reusable workout plan template for the trainer.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    if request.method != "POST":
        return redirect("trainer-workout-plans-page")

    form = CreateWorkoutPlanForm(request.POST)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-workout-plans-page")

    plan = WorkoutPlan.objects.create(
        user=request.user,
        name=form.cleaned_data["name"],
        is_active=True,
    )

    messages.success(request, "Workout plan created successfully.")
    return redirect("trainer-workout-plan-detail", plan_id=plan.id)


@login_required
def dashboard_create_workout_day(request, plan_id):
    """
    Add a workout day to a trainer-owned workout plan template.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    plan = get_object_or_404(WorkoutPlan, id=plan_id, user=request.user)

    if request.method != "POST":
        return redirect("trainer-workout-plan-detail", plan_id=plan.id)

    form = CreateWorkoutDayForm(request.POST)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-workout-plan-detail", plan_id=plan.id)

    WorkoutDay.objects.create(
        plan=plan,
        title=form.cleaned_data["title"],
        order=form.cleaned_data["order"],
    )

    messages.success(request, "Workout day added successfully.")
    return redirect("trainer-workout-plan-detail", plan_id=plan.id)


@login_required
def dashboard_add_exercise_to_day(request, plan_id):
    """
    Add a trainer-owned exercise preset into a specific day within a trainer-owned plan.
    This stores the exercise name in the plan so it remains stable even if the preset changes later.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    plan = get_object_or_404(WorkoutPlan, id=plan_id, user=request.user)

    if request.method != "POST":
        return redirect("trainer-workout-plan-detail", plan_id=plan.id)

    form = AddExerciseToDayForm(request.POST, trainer_user=request.user)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-workout-plan-detail", plan_id=plan.id)

    workout_day = get_object_or_404(
        WorkoutDay,
        id=form.cleaned_data["workout_day_id"],
        plan=plan,
    )

    library_item = get_object_or_404(
        ExerciseLibraryItem,
        id=form.cleaned_data["exercise_library_item_id"],
        user=request.user,
    )

    exercise = Exercise.objects.create(
        workout_day=workout_day,
        name=library_item.name,
        label=form.cleaned_data["label"],
        order=form.cleaned_data["order"],
        superset_group=form.cleaned_data["superset_group"] or None,
    )

    for set_number in range(1, form.cleaned_data["set_count"] + 1):
        ExerciseSetTarget.objects.create(
            exercise=exercise,
            set_number=set_number,
            reps=form.cleaned_data["reps"],
        )

    messages.success(request, "Exercise added to workout day successfully.")
    return redirect("trainer-workout-plan-detail", plan_id=plan.id)
