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
    HydrationLog,
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

# Within the same status (specifically "due_today"), prefer assignments
# in this order. Onboarding is blocking, so it wins. Routine cadences
# (weekly/biweekly/monthly) are persistent — a missed weekly should
# still be surfaced for catch-up — so they outrank daily, which is
# fire-and-forget (a missed daily just rolls into tomorrow's). Once the
# routine is submitted it drops out of contention and daily takes over.
_TYPE_PRIORITY = {
    "onboarding": 0,
    "routine":    1,
    "daily":      2,
}


def _days_overdue(due_at, now):
    """Whole calendar days between `due_at` and `now`, never negative.

    Used so the iOS card can switch to "overdue, catch up" copy when a
    routine cadence has slipped past its due date. We measure in local-
    calendar days (not raw timedelta) so a routine that was due "yesterday
    at 11pm" reads as "1 day overdue" the moment the clock crosses
    midnight, rather than waiting another 23 hours.
    """
    if due_at is None:
        return 0
    delta = (timezone.localtime(now).date() - timezone.localtime(due_at).date()).days
    return max(delta, 0)


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
        elif prio_new == prio_old and status == "due_today":
            # Both due right now — fall back to form-type priority so a
            # missed weekly outranks today's daily (the daily can wait
            # until the persistent routine is caught up).
            new_type = _TYPE_PRIORITY.get(assignment.form.form_type, 9)
            old_type = _TYPE_PRIORITY.get(best_assignment.form.form_type, 9)
            if new_type < old_type:
                best_assignment, best_status, best_days, best_due_at = assignment, status, days, due_at
            elif new_type == old_type and due_at is not None and (best_due_at is None or due_at < best_due_at):
                # Same type → older due_at wins (longer overdue first).
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
    # Overdue counter — only meaningful when the assignment is a routine
    # past its due date. Daily/onboarding don't have a "days late" notion
    # since they're either submitted or not. iOS uses this to flip the
    # card into "Weekly check-in overdue" copy.
    if (
        best_status == "due_today"
        and best_assignment.form.form_type == CheckInForm.ROUTINE
        and best_due_at is not None
    ):
        payload["days_overdue"] = _days_overdue(best_due_at, now)

    # New: a list of EVERY currently-due assignment so the iOS Home
    # screen can render one card per due check-in. Previously the
    # endpoint only surfaced the highest-priority one, which meant
    # a daily check-in always hid a same-day weekly. Older iOS builds
    # still rely on the top-level fields above and ignore this array.
    due_now = []
    for assignment in assignments:
        status, days, due_at = _evaluate_assignment(assignment, now)
        if status != "due_today":
            continue
        item = {
            "form_id":   assignment.form.id,
            "form_name": assignment.form.name,
            "form_type": assignment.form.form_type,
            "cadence":   assignment.cadence,
            "next_due_at": due_at.isoformat() if due_at else None,
        }
        # Only routines have a meaningful "days overdue" — daily forms
        # are evaluated by has-it-been-submitted-today, not by a stored
        # due_at, so reporting overdue days for them would be noise.
        if assignment.form.form_type == CheckInForm.ROUTINE and due_at is not None:
            item["days_overdue"] = _days_overdue(due_at, now)
        due_now.append(item)
    # Same priority used by the single-best picker — onboarding blocks
    # everything, then routines (persistent — must be caught up), then
    # daily (fire-and-forget). Older overdue routines surface first
    # within the routine bucket.
    due_now.sort(key=lambda item: (
        _TYPE_PRIORITY.get(item["form_type"], 9),
        # Negative `days_overdue` so larger overdue counts sort first;
        # `next_due_at` only used as a final stable tiebreak.
        -(item.get("days_overdue") or 0),
        item["form_id"],
    ))
    payload["due_now"] = due_now

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
        # CheckInForm doesn't actually have a `description` field
        # (only name + form_type). The iOS Decodable expects the key
        # to exist though — return empty string so JSON shape stays
        # stable. If/when we add a description field on the model,
        # this just starts populating without an iOS change.
        "description": getattr(form, "description", "") or "",
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

    # Trophy evaluation — runs after all answers are saved so the
    # check-in/photo/weight evaluators see the new data. Imported
    # lazily to keep apps.progress free of an apps.trophies hard
    # dependency at module load. Wrapped in try so a buggy evaluator
    # never fails the submission.
    newly_earned = []
    try:
        from apps.trophies.services import evaluate_and_award
        for trophy in evaluate_and_award(user):
            newly_earned.append({
                "code":     trophy.code,
                "name":     trophy.name,
                "rarity":   trophy.rarity,
                "icon":     trophy.icon,
                "category": trophy.category,
            })
    except Exception as exc:
        print(f"[trophies] post-checkin evaluation failed: {exc!r}")

    return Response({
        "id":           submission.id,
        "submitted_at": submission.submitted_at.isoformat(),
        "status":       "submitted",
        "newly_earned_trophies": newly_earned,
    }, status=201)


# ====================================================================
# Hydration sync — server-of-record for the iOS HomeWaterCard.
#
#   GET  /api/progress/me/hydration/       → today's cups + goal
#   POST /api/progress/me/hydration/       → set today's cups
#
# Only one row per client per day (UniqueConstraint), upserted via
# update_or_create. Triggers trophy evaluation after a POST so
# hydration trophies can unlock the moment the user finishes their
# day's water.
# ====================================================================


@csrf_exempt
@api_view(["GET", "POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def hydration_for_me(request):
    user = request.user
    if user.role != User.CLIENT or not hasattr(user, "client_profile"):
        return Response({"detail": "Not a client."}, status=403)

    today = timezone.localdate()

    if request.method == "GET":
        log = HydrationLog.objects.filter(client=user, logged_on=today).first()
        return Response({
            "logged_on": today.isoformat(),
            "cups":      log.cups if log else 0,
            "goal_cups": log.goal_cups if log else 8,
        })

    # POST — body: {"cups": <int>, "goal_cups": <int> (optional)}
    try:
        cups = int(request.data.get("cups", 0))
    except (TypeError, ValueError):
        return Response({"detail": "cups must be an integer."}, status=400)
    cups = max(0, min(cups, 32))   # sanity-cap at 32 to prevent overflow nonsense

    goal_raw = request.data.get("goal_cups")
    defaults = {"cups": cups}
    if goal_raw is not None:
        try:
            defaults["goal_cups"] = max(1, int(goal_raw))
        except (TypeError, ValueError):
            pass

    log, _ = HydrationLog.objects.update_or_create(
        client=user, logged_on=today, defaults=defaults,
    )

    # Trophy evaluation — hydration trophies depend on these rows so
    # we run after the upsert. Wrapped defensively as elsewhere.
    newly_earned = []
    try:
        from apps.trophies.services import evaluate_and_award
        for trophy in evaluate_and_award(user):
            newly_earned.append({
                "code":     trophy.code,
                "name":     trophy.name,
                "rarity":   trophy.rarity,
                "icon":     trophy.icon,
                "category": trophy.category,
            })
    except Exception as exc:
        print(f"[trophies] post-hydration evaluation failed: {exc!r}")

    return Response({
        "logged_on": log.logged_on.isoformat(),
        "cups":      log.cups,
        "goal_cups": log.goal_cups,
        "newly_earned_trophies": newly_earned,
    })
