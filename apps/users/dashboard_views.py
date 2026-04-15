from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.conf import settings
from django.core.mail import send_mail
from django.http import HttpResponse

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


@login_required
def trainer_dashboard(request):
    if request.user.role != User.TRAINER or not hasattr(request.user, "trainer_profile"):
        return redirect("landing-page")

    trainer_profile = request.user.trainer_profile
    clients = User.objects.filter(
        role=User.CLIENT,
        client_profile__trainer=trainer_profile
    ).select_related("client_profile").order_by("username")

    create_client_form = CreateClientForm()
    assign_forms = {
        client.id: AssignWorkoutPlanForm(
            trainer_user=request.user,
            initial={"client_user_id": client.id}
        )
        for client in clients
    }

    return render(
        request,
        "dashboard/trainer_dashboard.html",
        {
            "trainer_profile": trainer_profile,
            "clients": clients,
            "create_client_form": create_client_form,
            "assign_forms": assign_forms,
        },
    )


@login_required
def dashboard_create_client(request):
    if request.user.role != User.TRAINER or not hasattr(request.user, "trainer_profile"):
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
    if request.user.role != User.TRAINER or not hasattr(request.user, "trainer_profile"):
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


def test_email_page(request):
    try:
        send_mail(
            subject="GymFlow test email",
            message="This is a test email from GymFlow.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=["deenali3@outlook.com"],
            fail_silently=False,
        )
        return HttpResponse("Test email sent")
    except Exception as e:
        return HttpResponse(f"Email error: {type(e).__name__}: {e}", status=500)
