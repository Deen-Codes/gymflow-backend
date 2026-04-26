"""
Mobile API for the Check-Ins feature on the iOS Home screen.

Endpoint:
    GET /api/progress/me/next-checkin/
        Returns the soonest-due check-in assignment for the current
        client, or "all caught up" / "no assignments" if there isn't one.

Status taxonomy (mirrors what the iOS HomeCheckInCard.Status enum
expects so JSON → enum mapping is mechanical):
    "due_today"      — onboarding-not-yet-submitted, or daily-not-yet-
                       submitted-today, or routine past its next_due_at
    "due_in_days"    — routine assignment with next_due_at in the future
    "all_caught_up"  — at least one active assignment, none currently due
    "no_assignments" — trainer hasn't wired any forms for this client yet
"""
from datetime import timedelta

from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import User
from .models import CheckInForm, CheckInSubmission, ClientCheckInAssignment


def _is_submitted_today(client_user, form, now):
    """Has the client submitted this form already today?"""
    today_start = timezone.localtime(now).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return CheckInSubmission.objects.filter(
        client=client_user,
        form=form,
        status=CheckInSubmission.STATUS_SUBMITTED,
        submitted_at__gte=today_start,
    ).exists()


def _has_ever_submitted(client_user, form):
    return CheckInSubmission.objects.filter(
        client=client_user,
        form=form,
        status=CheckInSubmission.STATUS_SUBMITTED,
    ).exists()


def _evaluate_assignment(assignment, now):
    """Return (status, days_until_due, due_at) for one assignment.

    status is one of "due_today", "due_in_days", "all_caught_up".
    days_until_due is an int >= 0 when due_in_days, else None.
    due_at is a datetime or None.
    """
    form = assignment.form

    if form.form_type == CheckInForm.ONBOARDING:
        if _has_ever_submitted(assignment.client, form):
            return ("all_caught_up", None, None)
        return ("due_today", 0, None)

    if form.form_type == CheckInForm.DAILY:
        if _is_submitted_today(assignment.client, form, now):
            return ("all_caught_up", None, None)
        return ("due_today", 0, None)

    # ROUTINE — uses next_due_at.
    due_at = assignment.next_due_at
    if due_at is None:
        # Never seeded — treat as due today so the trainer / client
        # don't get stuck waiting on a stale row.
        return ("due_today", 0, None)

    if due_at <= now:
        return ("due_today", 0, due_at)

    delta_days = (due_at.date() - timezone.localtime(now).date()).days
    return ("due_in_days", max(delta_days, 1), due_at)


# Priority for picking the "most pressing" assignment when several
# qualify. Lower number = shown first.
_STATUS_PRIORITY = {
    "due_today": 0,
    "due_in_days": 1,
    "all_caught_up": 2,
}


@csrf_exempt
@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def next_checkin_for_me(request):
    """Return the most-relevant check-in for the current client."""
    user = request.user
    if user.role != User.CLIENT or not hasattr(user, "client_profile"):
        return Response({"status": "no_assignments"})

    assignments = list(
        ClientCheckInAssignment.objects
        .filter(client=user, is_active=True)
        .select_related("form")
    )
    if not assignments:
        return Response({"status": "no_assignments"})

    now = timezone.now()
    best_assignment = None
    best_status = None
    best_days = None
    best_due_at = None

    for assignment in assignments:
        status, days, due_at = _evaluate_assignment(assignment, now)

        # Decide whether this beats the current best:
        if best_status is None:
            best_assignment, best_status, best_days, best_due_at = assignment, status, days, due_at
            continue

        prio_new = _STATUS_PRIORITY[status]
        prio_old = _STATUS_PRIORITY[best_status]
        if prio_new < prio_old:
            best_assignment, best_status, best_days, best_due_at = assignment, status, days, due_at
        elif prio_new == prio_old and status == "due_in_days":
            # Earliest due wins.
            if days is not None and (best_days is None or days < best_days):
                best_assignment, best_status, best_days, best_due_at = assignment, status, days, due_at

    payload = {
        "status": best_status,
        "form_type": best_assignment.form.form_type,
        "form_name": best_assignment.form.name,
        "form_id":   best_assignment.form.id,
        "cadence":   best_assignment.cadence,
    }
    if best_status == "due_in_days":
        payload["days_until_due"] = best_days
    if best_due_at is not None:
        payload["next_due_at"] = best_due_at.isoformat()

    return Response(payload)
