from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect

from .dashboard_helpers import trainer_required
from .forms import (
    CreateExerciseLibraryItemForm,
    CreateWorkoutPlanForm,
    UpdateWorkoutPlanForm,
    CreateWorkoutDayForm,
    UpdateWorkoutDayForm,
    AddExerciseToDayForm,
    UpdateExerciseForm,
)
from apps.workouts.models import (
    WorkoutPlan,
    WorkoutDay,
    Exercise,
    ExerciseSetTarget,
    ExerciseLibraryItem,
)


@login_required
def dashboard_create_exercise_library_item(request):
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
def dashboard_duplicate_exercise_library_item(request, exercise_id):
    """
    Duplicate a trainer-owned exercise preset so it can be quickly tweaked.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    exercise = get_object_or_404(
        ExerciseLibraryItem,
        id=exercise_id,
        user=request.user,
    )

    if request.method != "POST":
        return redirect("trainer-workout-plans-page")

    ExerciseLibraryItem.objects.create(
        user=request.user,
        name=f"{exercise.name} Copy",
        video_url=exercise.video_url,
        coaching_notes=exercise.coaching_notes,
    )

    messages.success(request, f'Exercise preset "{exercise.name}" duplicated successfully.')
    return redirect("trainer-workout-plans-page")


@login_required
def dashboard_create_workout_plan(request):
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
def dashboard_duplicate_workout_plan(request, plan_id):
    """
    Duplicate a full trainer-owned workout plan template, including days,
    exercises, and set targets.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    source_plan = get_object_or_404(
        WorkoutPlan.objects.prefetch_related("days__exercises__sets"),
        id=plan_id,
        user=request.user,
    )

    if request.method != "POST":
        return redirect("trainer-workout-plan-detail", plan_id=source_plan.id)

    new_plan = WorkoutPlan.objects.create(
        user=request.user,
        name=f"{source_plan.name} Copy",
        is_active=source_plan.is_active,
    )

    for day in source_plan.days.all().order_by("order"):
        new_day = WorkoutDay.objects.create(
            plan=new_plan,
            title=day.title,
            order=day.order,
        )

        for exercise in day.exercises.all().order_by("order"):
            new_exercise = Exercise.objects.create(
                workout_day=new_day,
                name=exercise.name,
                label=exercise.label,
                order=exercise.order,
                superset_group=exercise.superset_group,
            )

            for set_target in exercise.sets.all().order_by("set_number"):
                ExerciseSetTarget.objects.create(
                    exercise=new_exercise,
                    set_number=set_target.set_number,
                    reps=set_target.reps,
                )

    messages.success(request, f'Workout plan "{source_plan.name}" duplicated successfully.')
    return redirect("trainer-workout-plan-detail", plan_id=new_plan.id)


@login_required
def dashboard_update_workout_plan(request, plan_id):
    if not trainer_required(request):
        return redirect("landing-page")

    plan = get_object_or_404(WorkoutPlan, id=plan_id, user=request.user)

    if request.method != "POST":
        return redirect("trainer-workout-plan-detail", plan_id=plan.id)

    form = UpdateWorkoutPlanForm(request.POST)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-workout-plan-detail", plan_id=plan.id)

    plan.name = form.cleaned_data["name"]
    plan.save()

    messages.success(request, "Workout plan updated successfully.")
    return redirect("trainer-workout-plan-detail", plan_id=plan.id)


@login_required
def dashboard_delete_workout_plan(request, plan_id):
    if not trainer_required(request):
        return redirect("landing-page")

    plan = get_object_or_404(WorkoutPlan, id=plan_id, user=request.user)

    if request.method != "POST":
        return redirect("trainer-workout-plan-detail", plan_id=plan.id)

    plan_name = plan.name
    plan.delete()

    messages.success(request, f'Workout plan "{plan_name}" deleted successfully.')
    return redirect("trainer-workout-plans-page")


@login_required
def dashboard_create_workout_day(request, plan_id):
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
def dashboard_update_workout_day(request, plan_id, day_id):
    if not trainer_required(request):
        return redirect("landing-page")

    plan = get_object_or_404(WorkoutPlan, id=plan_id, user=request.user)
    day = get_object_or_404(WorkoutDay, id=day_id, plan=plan)

    if request.method != "POST":
        return redirect("trainer-workout-plan-detail", plan_id=plan.id)

    form = UpdateWorkoutDayForm(request.POST)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-workout-plan-detail", plan_id=plan.id)

    day.title = form.cleaned_data["title"]
    day.order = form.cleaned_data["order"]
    day.save()

    messages.success(request, "Workout day updated successfully.")
    return redirect("trainer-workout-plan-detail", plan_id=plan.id)


@login_required
def dashboard_delete_workout_day(request, plan_id, day_id):
    if not trainer_required(request):
        return redirect("landing-page")

    plan = get_object_or_404(WorkoutPlan, id=plan_id, user=request.user)
    day = get_object_or_404(WorkoutDay, id=day_id, plan=plan)

    if request.method != "POST":
        return redirect("trainer-workout-plan-detail", plan_id=plan.id)

    day_title = day.title
    day.delete()

    messages.success(request, f'Workout day "{day_title}" deleted successfully.')
    return redirect("trainer-workout-plan-detail", plan_id=plan.id)


@login_required
def dashboard_add_exercise_to_day(request, plan_id):
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


@login_required
def dashboard_update_exercise(request, plan_id, exercise_id):
    if not trainer_required(request):
        return redirect("landing-page")

    plan = get_object_or_404(WorkoutPlan, id=plan_id, user=request.user)
    exercise = get_object_or_404(
        Exercise.objects.select_related("workout_day", "workout_day__plan").prefetch_related("sets"),
        id=exercise_id,
        workout_day__plan=plan,
    )

    if request.method != "POST":
        return redirect("trainer-workout-plan-detail", plan_id=plan.id)

    form = UpdateExerciseForm(request.POST)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-workout-plan-detail", plan_id=plan.id)

    exercise.label = form.cleaned_data["label"]
    exercise.order = form.cleaned_data["order"]
    exercise.superset_group = form.cleaned_data["superset_group"] or None
    exercise.save()

    new_set_count = form.cleaned_data["set_count"]
    reps_value = form.cleaned_data["reps"]

    exercise.sets.all().delete()

    for set_number in range(1, new_set_count + 1):
        ExerciseSetTarget.objects.create(
            exercise=exercise,
            set_number=set_number,
            reps=reps_value,
        )

    messages.success(request, "Exercise updated successfully.")
    return redirect("trainer-workout-plan-detail", plan_id=plan.id)


@login_required
def dashboard_delete_exercise(request, plan_id, exercise_id):
    if not trainer_required(request):
        return redirect("landing-page")

    plan = get_object_or_404(WorkoutPlan, id=plan_id, user=request.user)
    exercise = get_object_or_404(
        Exercise.objects.select_related("workout_day", "workout_day__plan"),
        id=exercise_id,
        workout_day__plan=plan,
    )

    if request.method != "POST":
        return redirect("trainer-workout-plan-detail", plan_id=plan.id)

    exercise_name = exercise.name
    exercise.delete()

    messages.success(request, f'Exercise "{exercise_name}" deleted successfully.')
    return redirect("trainer-workout-plan-detail", plan_id=plan.id)
