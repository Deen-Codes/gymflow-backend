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

    context = dashboard_context(request, "Dashboard")
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
