from .forms import (
    CreateClientForm,
    AssignWorkoutPlanForm,
    CreateExerciseLibraryItemForm,
    CreateWorkoutPlanForm,
)
from .models import User
from apps.workouts.models import WorkoutPlan, ExerciseLibraryItem


def trainer_required(request):
    """
    Return True only for authenticated trainers with a trainer profile.
    """
    return request.user.role == User.TRAINER and hasattr(request.user, "trainer_profile")


def get_trainer_clients(request):
    """
    Return only clients owned by the logged-in trainer.
    """
    return User.objects.filter(
        role=User.CLIENT,
        client_profile__trainer=request.user.trainer_profile
    ).select_related(
        "client_profile",
        "client_profile__assigned_workout_plan"
    ).order_by("username")


def dashboard_context(request, page_title):
    """
    Shared dashboard context used across trainer pages.
    Keeps sidebar counters and common forms consistent.
    """
    trainer_profile = request.user.trainer_profile
    clients = get_trainer_clients(request)
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
