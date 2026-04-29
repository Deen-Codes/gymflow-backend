import secrets

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse

from .forms import TrainerLoginForm
from .models import MagicLoginToken, User


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


# -------------------------------------------------------------------
# Trainer magic-link sign-in (task #45 / L.1.1.2)
#
# Mirrors the iOS magic-link flow but for the web dashboard. POST
# an email → we create a MagicLoginToken (reusing the existing
# model) and email a sign-in link. The link points at the same
# `/magic/<token>/` bridge view as the iOS one — that view detects
# the user's role and routes accordingly: trainer → consume the
# token + create Django session + redirect to /dashboard, client
# → existing iOS deep-link bridge.
#
# This means trainers don't need to remember a password. The
# legacy username/password form stays alongside for the rollover
# window — once everyone's adapted, we can rip the password form
# out.
# -------------------------------------------------------------------


def trainer_magic_link_request(request):
    """POST { email } from the trainer login page. Always renders
    the success state regardless of whether the email is on file —
    same security posture as the iOS endpoint."""
    if request.method != "POST":
        return redirect("trainer-login-page")

    email = (request.POST.get("email") or "").strip().lower()
    if not email or "@" not in email:
        messages.error(request, "Enter a valid email address.")
        return redirect("trainer-login-page")

    user = User.objects.filter(email__iexact=email, role=User.TRAINER).first()
    if user is not None:
        token_str = secrets.token_urlsafe(32)
        record = MagicLoginToken.objects.create(
            user=user,
            token=token_str,
            requested_ip=_client_ip(request),
        )
        try:
            from .views import _magic_link_urls, _send_magic_link_email
            _, web_link = _magic_link_urls(record.token)
            # Reuse the same email template + sender — the bridge
            # view differentiates trainer vs client at click time.
            _send_magic_link_email(user=user, deep_link=web_link, web_link=web_link)
        except Exception:
            import logging
            logging.exception("Trainer magic-link email send failed for %s", email)

    messages.success(
        request,
        "If that email is on file, a sign-in link is on its way. The link expires in 10 minutes.",
    )
    return redirect("trainer-login-page")


def _client_ip(request):
    """Best-effort client IP — same helper shape the API views use."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
