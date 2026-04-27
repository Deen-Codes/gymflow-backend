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

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    parser_classes,
    permission_classes,
)
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import User
from .models import (
    CheckInForm,
    CheckInQuestion,
    CheckInQuestionOption,
    CheckInAnswer,
    CheckInSubmission,
    ClientCheckInAssignment,
)


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


# ====================================================================
# Phase C.1 — iOS check-in submissions
#
# Two endpoints powering the iOS check-in detail flow:
#
#   GET  /api/progress/forms/<form_id>/
#        Return the form's questions + options so iOS can render the
#        appropriate input per question_type. Auth-checked: the form
#        must belong to a trainer this client has an active
#        assignment for, otherwise 403.
#
#   POST /api/progress/forms/<form_id>/submit/
#        Accept a multipart form-data body of answers and create a
#        CheckInSubmission with status=submitted plus a CheckInAnswer
#        per question. Polymorphic answer field selection follows the
#        question's type. After persisting, bumps the assignment's
#        last_submitted_at + next_due_at (for routine forms) so the
#        Home check-in card refreshes correctly.
# ====================================================================


def _client_can_access_form(client_user, form):
    """The iOS user is allowed to read/submit a form only if they have
    an active ClientCheckInAssignment for that form. Stops one trainer's
    client from probing another trainer's form by ID."""
    return ClientCheckInAssignment.objects.filter(
        client=client_user,
        form=form,
        is_active=True,
    ).exists()


@csrf_exempt
@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def form_detail_for_me(request, form_id):
    """Return the full structure of a form so iOS can render it."""
    user = request.user
    if user.role != User.CLIENT or not hasattr(user, "client_profile"):
        return Response({"detail": "Not a client."}, status=403)

    form = get_object_or_404(CheckInForm, id=form_id)
    if not _client_can_access_form(user, form):
        return Response({"detail": "Form not assigned to you."}, status=403)

    questions = list(
        form.questions.all().prefetch_related("options").order_by("order", "id")
    )

    return Response({
        "id":          form.id,
        "name":        form.name,
        "form_type":   form.form_type,
        "description": form.description or "",
        "questions": [
            {
                "id":            q.id,
                "question_text": q.question_text,
                "question_type": q.question_type,
                "is_required":   q.is_required,
                "field_key":     q.field_key,
                "order":         q.order,
                "options": [
                    {"id": o.id, "value": o.value, "order": o.order}
                    for o in q.options.all().order_by("order", "id")
                ],
            }
            for q in questions
        ],
    })


@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def submit_form_for_me(request, form_id):
    """Persist a client's answers to a form as a single CheckInSubmission.

    Request body (multipart/form-data — required because of file uploads):
        q_<question_id>           : answer value
                                    - text fields   → string
                                    - number        → number string
                                    - yes_no        → "true" / "false"
                                    - dropdown      → option id (integer string)
                                    - photo         → image File part
                                    - video         → video File part
                                    Missing keys are treated as unanswered.

    Response:
        201 with the created submission's id + submitted_at on success.
        400 with field errors if a required question has no answer.
        403 if the form isn't assigned to this client.
    """
    user = request.user
    if user.role != User.CLIENT or not hasattr(user, "client_profile"):
        return Response({"detail": "Not a client."}, status=403)

    form = get_object_or_404(CheckInForm, id=form_id)
    if not _client_can_access_form(user, form):
        return Response({"detail": "Form not assigned to you."}, status=403)

    questions = list(form.questions.all().prefetch_related("options"))

    # ---- Validate required questions before any DB writes -----------
    errors = {}
    for q in questions:
        key = f"q_{q.id}"
        if not q.is_required:
            continue
        # Photo/video keys live in request.FILES; everything else in POST.
        if q.question_type in (CheckInQuestion.PHOTO, CheckInQuestion.VIDEO):
            if key not in request.FILES:
                errors[key] = "Required"
        else:
            value = request.data.get(key, "")
            if value in ("", None):
                errors[key] = "Required"
    if errors:
        return Response({"errors": errors}, status=400)

    # ---- Create submission + answers atomically ---------------------
    submission = CheckInSubmission.objects.create(
        form=form,
        client=user,
        status=CheckInSubmission.STATUS_SUBMITTED,
        submitted_at=timezone.now(),
    )

    for q in questions:
        key = f"q_{q.id}"
        kwargs = {"submission": submission, "question": q}

        if q.question_type == CheckInQuestion.SHORT_TEXT or q.question_type == CheckInQuestion.LONG_TEXT:
            text = request.data.get(key, "")
            if not text:
                continue
            kwargs["value_text"] = text

        elif q.question_type == CheckInQuestion.NUMBER:
            raw = request.data.get(key, "")
            if raw in ("", None):
                continue
            try:
                kwargs["value_number"] = float(raw)
            except (TypeError, ValueError):
                # Don't fail the whole submission for one bad number;
                # just drop the answer. Could be tightened later.
                continue

        elif q.question_type == CheckInQuestion.YES_NO:
            raw = (request.data.get(key, "") or "").strip().lower()
            if raw in ("", None):
                continue
            kwargs["value_yes_no"] = raw in ("true", "yes", "1")

        elif q.question_type == CheckInQuestion.DROPDOWN:
            raw = request.data.get(key, "")
            if raw in ("", None):
                continue
            try:
                option_id = int(raw)
            except (TypeError, ValueError):
                continue
            option = CheckInQuestionOption.objects.filter(
                id=option_id, question=q
            ).first()
            if option is None:
                continue
            kwargs["value_option"] = option

        elif q.question_type == CheckInQuestion.PHOTO:
            uploaded = request.FILES.get(key)
            if uploaded is None:
                continue
            kwargs["value_image"] = uploaded

        elif q.question_type == CheckInQuestion.VIDEO:
            uploaded = request.FILES.get(key)
            if uploaded is None:
                continue
            kwargs["value_video"] = uploaded

        else:
            # Unknown question type — skip rather than crash.
            continue

        # update_or_create so a re-submit on the same submission
        # row replaces values rather than UniqueConstraint-erroring.
        # (Submissions are otherwise one-shot — re-submit isn't a
        # supported flow yet but this keeps things safe.)
        CheckInAnswer.objects.update_or_create(
            submission=submission,
            question=q,
            defaults={k: v for k, v in kwargs.items()
                      if k not in ("submission", "question")},
        )

    # ---- Bump the assignment so the Home card refreshes properly ----
    assignment = ClientCheckInAssignment.objects.filter(
        client=user, form=form, is_active=True,
    ).first()
    if assignment is not None:
        now = timezone.now()
        assignment.last_submitted_at = now
        # For routine cadences, advance next_due_at by the cadence
        # interval. The mapping mirrors the dashboard's logic.
        cadence_to_days = {
            ClientCheckInAssignment.CADENCE_DAILY:   1,
            ClientCheckInAssignment.CADENCE_WEEKLY:  7,
            ClientCheckInAssignment.CADENCE_BIWEEKLY: 14,
            ClientCheckInAssignment.CADENCE_MONTHLY: 30,
        }
        days = cadence_to_days.get(assignment.cadence)
        if days is not None:
            assignment.next_due_at = now + timedelta(days=days)
        assignment.save(update_fields=["last_submitted_at", "next_due_at"])

    return Response({
        "id":           submission.id,
        "submitted_at": submission.submitted_at.isoformat(),
        "status":       "submitted",
    }, status=201)
