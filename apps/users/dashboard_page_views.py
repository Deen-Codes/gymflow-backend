from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .dashboard_helpers import trainer_required, dashboard_context

User = get_user_model()


@login_required
def trainer_dashboard_home(request):
    """
    Trainer dashboard overview page.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    clients = (
        User.objects.filter(
            role="client",
            client_profile__trainer=request.user,
        )
        .select_related(
            "client_profile",
            "client_profile__assigned_workout_plan",
            "client_profile__assigned_nutrition_plan",
        )
        .order_by("username")
    )

    action_needed_count = 0

    for client in clients:
        profile = getattr(client, "client_profile", None)
        if not profile:
            continue

        if not profile.assigned_workout_plan:
            action_needed_count += 1

        if not profile.assigned_nutrition_plan:
            action_needed_count += 1

    context = dashboard_context(request, "Dashboard")
    context.update(
        {
            "clients": clients,
            "client_count": clients.count(),
            "action_needed_count": action_needed_count,
        }
    )

    return render(request, "dashboard/dashboard_home.html", context)


@login_required
def trainer_settings_page(request):
    """
    Placeholder page for trainer/business/account settings.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    context = dashboard_context(request, "Settings")
    return render(request, "dashboard/dashboard_settings.html", context)
