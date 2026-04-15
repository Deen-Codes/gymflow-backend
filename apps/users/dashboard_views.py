from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import (
    TrainerLoginForm,
    CreateClientForm,
    AssignWorkoutPlanForm,
    CreateExerciseLibraryItemForm,
    CreateWorkoutPlanForm,
    CreateWorkoutDayForm,
    AddExerciseToDayForm,
)
from .models import User
from .serializers import ClientCreateSerializer, AssignWorkoutPlanSerializer
from apps.workouts.models import (
    WorkoutPlan,
    WorkoutDay,
    Exercise,
    ExerciseSetTarget,
    ExerciseLibraryItem,
)


def landing_page(request):
    if request.user.is_authenticated:
        if getattr(request.user, "role", "") == User.TRAINER:
            return redirect("trainer-dashboard-home")
    return render(request, "landing.html")


def trainer_login_page(request):
    if request.user.is_authenticated and getattr(request.user, "role", "") == User.TRAINER:
        return redirect("trainer-dashboard-home")

    form = TrainerLoginForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        username = form.cleaned_data["username"]
        password = form.cleaned_data["password"]

        user = authenticate(request, username=username, password=password)

        if user is None:
            messages.error(request, "Invalid username or password.")
        elif user.role != User.TRAINER:
            messages.error(request, "This login is for trainers only.")
        else:
            login(request, user)
            return redirect("trainer-dashboard-home")

    return render(request, "auth/trainer_login.html", {"form": form})


@login_required
def trainer_logout_page(request):
    logout(request)
    return redirect("landing-page")


def _trainer_required(request):
    return request.user.role == User.TRAINER and hasattr(request.user, "trainer_profile")


def _get_trainer_clients(request):
    return User.objects.filter(
        role=User.CLIENT,
        client_profile__trainer=request.user.trainer_profile
    ).select_related("client_profile", "client_profile__assigned_workout_plan").order_by("username")


def _dashboard_context(request, page_title):
    trainer_profile = request.user.trainer_profile
    clients = _get_trainer_clients(request)
    workout_plans = WorkoutPlan.objects.filter(user=request.user).order_by("name")
    exercise_library = ExerciseLibraryItem.objects.filter(user=request.user).order_by("name")

    assign_forms = {
        client.id: AssignWorkoutPlanForm(
            trainer_user=request.user,
            initial={"client_user_id": client.id}
        )
        for client in clients
    }

    return {
        "trainer_profile": trainer_profile,
        "page_title": page_title,
        "clients": clients,
        "create_client_form": CreateClientForm(),
        "assign_forms": assign_forms,
        "client_count": clients.count(),
        "workout_plans": workout_plans,
        "exercise_library": exercise_library,
        "exercise_library_form": CreateExerciseLibraryItemForm(),
        "create_workout_plan_form": CreateWorkoutPlanForm(),
    }


@login_required
def trainer_dashboard_home(request):
    if not _trainer_required(request):
        return redirect("landing-page")

    context = _dashboard_context(request, "Dashboard")
    return render(request, "dashboard/dashboard_home.html", context)


@login_required
def trainer_dashboard(request):
    if not _trainer_required(request):
        return redirect("landing-page")

    context = _dashboard_context(request, "Clients")
    return render(request, "dashboard/trainer_dashboard.html", context)


@login_required
def trainer_client_detail_page(request, client_id):
    if not _trainer_required(request):
        return redirect("landing-page")

    client = get_object_or_404(
        User.objects.select_related("client_profile", "client_profile__assigned_workout_plan"),
        id=client_id,
        role=User.CLIENT,
        client_profile__trainer=request.user.trainer_profile,
    )

    context = _dashboard_context(request, "Client Details")
    context.update({
        "client": client,
        "client_assign_form": AssignWorkoutPlanForm(
            trainer_user=request.user,
            initial={"client_user_id": client.id}
        ),
    })
    return render(request, "dashboard/client_detail.html", context)


@login_required
def trainer_workout_plans_page(request):
    if not _trainer_required(request):
        return redirect("landing-page")

    context = _dashboard_context(request, "Workout Plans")
    return render(request, "dashboard/dashboard_workout_plans.html", context)


@login_required
def trainer_workout_plan_detail_page(request, plan_id):
    if not _trainer_required(request):
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

    context = _dashboard_context(request, f"Plan: {plan.name}")
    context.update({
        "plan": plan,
        "days": days,
        "create_day_form": CreateWorkoutDayForm(),
        "add_exercise_forms": add_exercise_forms,
    })
    return render(request, "dashboard/workout_plan_detail.html", context)


@login_required
def trainer_nutrition_plans_page(request):
    if not _trainer_required(request):
        return redirect("landing-page")

    context = _dashboard_context(request, "Nutrition Plans")
    return render(request, "dashboard/dashboard_nutrition_plans.html", context)


@login_required
def trainer_settings_page(request):
    if not _trainer_required(request):
        return redirect("landing-page")

    context = _dashboard_context(request, "Settings")
    return render(request, "dashboard/dashboard_settings.html", context)


@login_required
def dashboard_create_client(request):
    if not _trainer_required(request):
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
    if not _trainer_required(request):
        return redirect("landing-page")

    if request.method != "POST":
        return redirect("trainer-dashboard")

    form = AssignWorkoutPlanForm(request.POST, trainer_user=request.user)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-dashboard")

    serializer = AssignWorkoutPlanSerializer(
        data={
            "client_user_id": form.cleaned_data["client_user_id"],
            "workout_plan_id": form.cleaned_data["workout_plan_id"],
        }
    )

    if serializer.is_valid():
        serializer.assign(request.user)
        messages.success(request, "Workout plan assigned successfully.")
    else:
        for _, errors in serializer.errors.items():
            if isinstance(errors, list):
                for error in errors:
                    messages.error(request, str(error))
            else:
                messages.error(request, str(errors))

    return redirect("trainer-client-detail", client_id=form.cleaned_data["client_user_id"])


@login_required
def dashboard_create_exercise_library_item(request):
    if not _trainer_required(request):
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
    if not _trainer_required(request):
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
    if not _trainer_required(request):
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
    if not _trainer_required(request):
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
