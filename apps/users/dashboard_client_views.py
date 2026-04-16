from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from .dashboard_helpers import trainer_required, dashboard_context
from .forms import CreateClientForm, AssignWorkoutPlanForm
from .models import User
from .serializers import ClientCreateSerializer
from apps.workouts.models import WorkoutPlan, WorkoutDay, Exercise, ExerciseSetTarget


@login_required
def trainer_dashboard(request):
    """
    Clients page showing trainer-owned clients.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    context = dashboard_context(request, "Clients")
    return render(request, "dashboard/trainer_dashboard.html", context)


@login_required
def trainer_client_detail_page(request, client_id):
    """
    Detail page for a single trainer-owned client.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    client = get_object_or_404(
        User.objects.select_related("client_profile", "client_profile__assigned_workout_plan"),
        id=client_id,
        role=User.CLIENT,
        client_profile__trainer=request.user.trainer_profile,
    )

    assigned_plan = getattr(client.client_profile, "assigned_workout_plan", None)

    context = dashboard_context(request, "Client Details")
    context.update({
        "client": client,
        "assigned_plan": assigned_plan,
        "client_assign_form": AssignWorkoutPlanForm(
            trainer_user=request.user,
            initial={"client_user_id": client.id}
        ),
    })
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
def dashboard_delete_client(request, client_id):
    """
    Delete a trainer-owned client account.
    Client-specific workout plans linked to that client will also be removed automatically.
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
