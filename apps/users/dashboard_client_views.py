from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .dashboard_helpers import trainer_required, dashboard_context
from .forms import CreateClientForm, AssignWorkoutPlanForm
from .models import User
from .serializers import ClientCreateSerializer, AssignWorkoutPlanSerializer


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

    context = dashboard_context(request, "Client Details")
    context.update({
        "client": client,
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
    Redirect back to the client detail page after saving.
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
