from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .dashboard_helpers import trainer_required, dashboard_context


@login_required
def trainer_dashboard_home(request):
    """
    Trainer dashboard overview page.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    trainer_profile = request.user.trainer_profile

    context = dashboard_context(request, "Dashboard")

    clients = context.get("clients")
    if clients is None:
        clients = trainer_profile.clients.select_related(
            "client_profile",
            "client_profile__assigned_workout_plan",
            "client_profile__assigned_nutrition_plan",
        ).order_by("username")

    missing_workout_clients = []
    missing_nutrition_clients = []
    action_needed_count = 0

    for client in clients:
        profile = getattr(client, "client_profile", None)
        if not profile:
            continue

        if not getattr(profile, "assigned_workout_plan", None):
            missing_workout_clients.append(client)
            action_needed_count += 1

        if not getattr(profile, "assigned_nutrition_plan", None):
            missing_nutrition_clients.append(client)
            action_needed_count += 1

    context.update(
        {
            "clients": clients,
            "client_count": clients.count() if hasattr(clients, "count") else len(clients),
            "action_needed_count": action_needed_count,
            "missing_workout_clients": missing_workout_clients,
            "missing_nutrition_clients": missing_nutrition_clients,
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