"""
Check-Ins workspace page views.

Restructure v2 + Phase 4.5: every trainer always has exactly THREE
forms (Onboarding · Daily · Routine). The workspace bootstraps these
on first visit, so the trainer never has to "create a form" — they
just edit the questions on the three that already exist.

Routes:
    /dashboard/checkin-forms/         → Onboarding pinned + auto-selected
    /dashboard/checkin-forms/<id>/    → that specific form in the canvas
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .dashboard_helpers import trainer_required, dashboard_context
from .forms import UpdateCheckInFormForm
from .dashboard_checkin_action_views import (
    _create_default_onboarding_questions,
    _create_default_daily_questions,
    _create_default_weekly_questions,
)
from apps.progress.models import CheckInForm


def _seed_questions(form):
    """Seed system questions for a freshly bootstrapped form. Maps the
    new ROUTINE slug to the existing weekly-question seeder (same
    questions, new label)."""
    if form.form_type == CheckInForm.ONBOARDING:
        _create_default_onboarding_questions(form)
    elif form.form_type == CheckInForm.DAILY:
        _create_default_daily_questions(form)
    elif form.form_type in (CheckInForm.ROUTINE, CheckInForm.WEEKLY):
        _create_default_weekly_questions(form)


# Default human-friendly names per form type. Created once on bootstrap;
# the trainer can rename in-place after.
DEFAULT_FORM_NAMES = {
    CheckInForm.ONBOARDING: "Onboarding",
    CheckInForm.DAILY:      "Daily check-in",
    CheckInForm.ROUTINE:    "Routine check-in",
}


def _ensure_three_forms(trainer):
    """Make sure the trainer has exactly one of each required form
    type. If a type is missing, create it and seed system questions."""
    for form_type in CheckInForm.REQUIRED_FORM_TYPES:
        existing = (
            CheckInForm.objects
            .filter(user=trainer, form_type=form_type)
            .order_by("created_at")
            .first()
        )
        if existing is None:
            form = CheckInForm.objects.create(
                user=trainer,
                name=DEFAULT_FORM_NAMES[form_type],
                form_type=form_type,
                is_active=True,
            )
            # Seed the mandatory system questions for the type.
            _seed_questions(form)


def _canonical_three(trainer):
    """Return exactly one form per required type — the most-recently
    created one, in the canonical order Onboarding → Daily → Routine.
    Older trainers may still have multiple weekly/daily forms from the
    pre-Phase-4.5 era; we just surface the canonical one in the UI."""
    by_type = {}
    qs = (
        CheckInForm.objects
        .filter(user=trainer, form_type__in=CheckInForm.REQUIRED_FORM_TYPES)
        .order_by("-created_at")
    )
    for f in qs:
        if f.form_type not in by_type:
            by_type[f.form_type] = f
    return [by_type[t] for t in CheckInForm.REQUIRED_FORM_TYPES if t in by_type]


def _render_checkins_workspace(request, form_id=None):
    _ensure_three_forms(request.user)
    forms = _canonical_three(request.user)

    active = None
    if form_id is not None:
        active = get_object_or_404(
            CheckInForm.objects.prefetch_related("questions__options"),
            id=form_id,
            user=request.user,
        )
    elif forms:
        # Always default to Onboarding first.
        active = (
            CheckInForm.objects
            .filter(id=forms[0].id)
            .prefetch_related("questions__options")
            .first()
        )

    page_title = f"Check-Ins: {active.name}" if active else "Check-Ins"
    context = dashboard_context(request, page_title)
    context.update({
        "checkin_forms": forms,
        "active_form": active,
        "checkin_form_edit_form": UpdateCheckInFormForm(
            initial={
                "name": active.name,
                "form_type": active.form_type,
                "is_active": active.is_active,
            }
        ) if active else None,
    })
    return render(request, "dashboard/dashboard_checkin_forms.html", context)


@login_required
def trainer_checkin_forms_page(request):
    """Front of the Check-Ins workspace — Onboarding auto-selected."""
    if not trainer_required(request):
        return redirect("landing-page")
    return _render_checkins_workspace(request, form_id=None)


@login_required
def trainer_checkin_form_detail_page(request, form_id):
    """Deep-link to a specific form in the Check-Ins workspace."""
    if not trainer_required(request):
        return redirect("landing-page")
    return _render_checkins_workspace(request, form_id=form_id)
