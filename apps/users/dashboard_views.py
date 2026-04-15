from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import TrainerLoginForm, CreateClientForm, AssignWorkoutPlanForm
from .models import User
from .serializers import ClientCreateSerializer, AssignWorkoutPlanSerializer


def landing_page(request):
    if request.user.is_authenticated:
        if getattr(request.user, "role", "") == User.TRAINER:
            return redirect("trainer-dashboard")
    return render(request, "landing.html")


def trainer_login_page(request):
    if request.user.is_authenticated and getattr(request.user, "role", "") == User.TRAINER:
        return redirect("trainer-dashboard")

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
            return redirect("trainer-dashboard")

    return render(request, "auth/trainer_login.html", {"form": form})


@login_required
def trainer_logout_page(request):
    logout(request)
    return redirect("landing-page")


def _trainer_required(request):
    return request.user.role == User.TRAINER and hasattr(request.user, "trainer_profile")


def _dashboard_context(request, page_title):
    trainer_profile = request.user.trainer_profile
    clients = User.objects.filter(
        role=User.CLIENT,
        client_profile__trainer=trainer_profile
    ).select_related("client_profile", "client_profile__assigned_workout_plan").order_by("username")

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
    }


@login_required
def trainer_dashboard(request):
    if not _trainer_required(request):
        return redirect("landing-page")

    context = _dashboard_context(request, "Clients")
    return render(request, "dashboard/trainer_dashboard.html", context)


@login_required
def trainer_dashboard_home(request):
    if not _trainer_required(request):
        return redirect("landing-page")

    context = _dashboard_context(request, "Dashboard")
    return render(request, "dashboard/dashboard_home.html", context)


@login_required
def trainer_workout_plans_page(request):
    if not _trainer_required(request):
        return redirect("landing-page")

    context = _dashboard_context(request, "Workout Plans")
    return render(request, "dashboard/dashboard_workout_plans.html", context)


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

    return redirect("trainer-dashboard")
