from .forms import (
    CreateClientForm,
    AssignWorkoutPlanForm,
    AssignNutritionPlanForm,
    CreateExerciseLibraryItemForm,
    CreateWorkoutPlanForm,
    CreateNutritionPlanForm,
)
from .models import User
from apps.workouts.models import WorkoutPlan, ExerciseLibraryItem
from apps.nutrition.models import NutritionPlan


def trainer_required(request):
    """
    Return True only for authenticated trainers with a trainer profile.
    """
    return request.user.role == User.TRAINER and hasattr(request.user, "trainer_profile")


def trainer_required_view(view_func):
    """Decorator equivalent of `if not trainer_required(request): redirect`.

    Replaces the boilerplate that opens nearly every dashboard view:

        @login_required
        def my_view(request, ...):
            if not trainer_required(request):
                return redirect("landing-page")
            ...

    With:

        @trainer_required_view
        def my_view(request, ...):
            ...

    Stacks `@login_required` automatically so callers don't need both —
    a non-authenticated request goes through Django's standard login
    flow first, an authenticated-but-not-a-trainer request bounces to
    the landing page.

    Why a decorator: the inline check is repeated 40+ times across the
    dashboard view files. Forgetting the check is a security bug
    (a client could probe trainer-only endpoints by URL guessing).
    Decorator-as-default makes the safe path the easy path.
    """
    from functools import wraps
    from django.contrib.auth.decorators import login_required
    from django.shortcuts import redirect

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not trainer_required(request):
            return redirect("landing-page")
        return view_func(request, *args, **kwargs)

    return login_required(wrapper)


def get_trainer_clients(request):
    """
    Return only clients owned by the logged-in trainer.
    """
    return User.objects.filter(
        role=User.CLIENT,
        client_profile__trainer=request.user.trainer_profile
    ).select_related(
        "client_profile",
        "client_profile__assigned_workout_plan",
        "client_profile__assigned_nutrition_plan",
    ).order_by("username")


def _action_needed_count(clients):
    """How many "needs a plan" items across the roster — used by the
    top-nav Clients pill so the trainer always sees what's outstanding."""
    n = 0
    for c in clients:
        profile = getattr(c, "client_profile", None)
        if not profile:
            continue
        if not getattr(profile, "assigned_workout_plan", None):
            n += 1
        if not getattr(profile, "assigned_nutrition_plan", None):
            n += 1
    return n


def dashboard_context(request, page_title):
    """
    Shared dashboard context used across trainer pages.
    Keeps sidebar counters and common forms consistent.
    """
    trainer_profile = request.user.trainer_profile
    clients = get_trainer_clients(request)
    workout_plans = WorkoutPlan.objects.filter(user=request.user).order_by("name")
    exercise_library = ExerciseLibraryItem.objects.filter(user=request.user).order_by("name")
    nutrition_plans = NutritionPlan.objects.filter(user=request.user).order_by("name")

    assign_forms = {
        client.id: AssignWorkoutPlanForm(
            trainer_user=request.user,
            initial={"client_user_id": client.id}
        )
        for client in clients
    }

    nutrition_assign_forms = {
        client.id: AssignNutritionPlanForm(
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
        "nutrition_assign_forms": nutrition_assign_forms,
        "client_count": clients.count(),
        "action_needed_count": _action_needed_count(clients),
        "workout_plans": workout_plans,
        "exercise_library": exercise_library,
        "exercise_library_form": CreateExerciseLibraryItemForm(),
        "create_workout_plan_form": CreateWorkoutPlanForm(),
        "nutrition_plans": nutrition_plans,
        "create_nutrition_plan_form": CreateNutritionPlanForm(),
    }
