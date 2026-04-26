from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import TrainerLoginForm
from .models import User


def landing_page(request):
    """
    Public landing page.
    If an authenticated trainer visits, send them straight to the dashboard.
    """
    if request.user.is_authenticated:
        if getattr(request.user, "role", "") == User.TRAINER:
            return redirect("trainer-hub-page")
    return render(request, "landing.html")


def trainer_login_page(request):
    """
    Trainer-only login page for the web dashboard.
    Clients should use the mobile app flow instead.
    """
    if request.user.is_authenticated and getattr(request.user, "role", "") == User.TRAINER:
        return redirect("trainer-hub-page")

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
            return redirect("trainer-hub-page")

    return render(request, "auth/trainer_login.html", {"form": form})


@login_required
def trainer_logout_page(request):
    """
    End the current dashboard session and return to the landing page.
    """
    logout(request)
    return redirect("landing-page")
