"""Phase 4 — check-ins dashboard JSON endpoints.

Powers the drag-drop form builder + per-client submissions feed.

Auth: trainer with role==TRAINER and a related trainer_profile.
Reads scoped to the calling trainer's own forms; client submissions
require the client to belong to this trainer.
"""
from django.db import transaction
from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import User

from datetime import timedelta
from django.utils import timezone

from apps.users.models import User as UserModel

from .models import (
    CheckInForm,
    CheckInQuestion,
    CheckInQuestionOption,
    CheckInSubmission,
    ClientCheckInAssignment,
)
from .dashboard_serializers import (
    CheckInFormReadSerializer,
    CheckInQuestionCreateSerializer,
    CheckInQuestionReadSerializer,
    CheckInQuestionReorderSerializer,
    CheckInQuestionUpdateSerializer,
    CheckInSubmissionReadSerializer,
    ClientCheckInAssignmentReadSerializer,
    ClientCheckInAssignmentWriteSerializer,
)


CADENCE_INTERVAL_DAYS = {
    ClientCheckInAssignment.CADENCE_DAILY:    1,
    ClientCheckInAssignment.CADENCE_WEEKLY:   7,
    ClientCheckInAssignment.CADENCE_BIWEEKLY: 14,
    ClientCheckInAssignment.CADENCE_MONTHLY:  30,
    # ONESHOT has no recurring interval; next_due_at stays null.
}


def _next_due_for_cadence(cadence, anchor=None):
    """Compute the next due timestamp for a cadence. `anchor` is the
    last submission (or now if there isn't one yet)."""
    if cadence == ClientCheckInAssignment.CADENCE_ONESHOT:
        return None
    days = CADENCE_INTERVAL_DAYS.get(cadence)
    if not days:
        return None
    base = anchor or timezone.now()
    return base + timedelta(days=days)


def _trainer_owns_client(trainer, client):
    return getattr(client, "client_profile", None) and \
           client.client_profile.trainer_id == trainer.trainer_profile.id


def _require_trainer(request):
    user = request.user
    if user.role != User.TRAINER or not hasattr(user, "trainer_profile"):
        return None, Response(
            {"detail": "Only trainers can use the dashboard API."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return user, None


def _trainer_owns_form(trainer, form):
    return form.user_id == trainer.id


def _trainer_owns_question(trainer, question):
    return question.form.user_id == trainer.id


def _replace_dropdown_options(question, values):
    question.options.all().delete()
    for index, value in enumerate(values, start=1):
        v = (value or "").strip()
        if not v:
            continue
        CheckInQuestionOption.objects.create(
            question=question, value=v[:100], order=index
        )


# -------------------------------------------------------------------
# Forms (read-only — create/update/delete still go through the
# existing form-POST endpoints so the system-question seed runs)
# -------------------------------------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def form_list(request):
    """GET /api/progress/dashboard/forms/

    All forms owned by the calling trainer, with their full question
    list inlined. Response is small enough to fetch once per page load.
    """
    trainer, err = _require_trainer(request)
    if err:
        return err

    qs = (
        CheckInForm.objects
        .filter(user=trainer)
        .prefetch_related("questions__options")
        .order_by("form_type", "name")
    )
    return Response({"results": CheckInFormReadSerializer(qs, many=True).data})


# -------------------------------------------------------------------
# Question CRUD
# -------------------------------------------------------------------
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def question_add(request):
    """POST /api/progress/dashboard/questions/

    Body: {form_id, question_text, question_type, is_required?, dropdown_options?}
    Order is auto-computed (appended to the bottom of the form).
    `is_system_question` is always false for trainer-added questions.
    """
    trainer, err = _require_trainer(request)
    if err:
        return err

    serializer = CheckInQuestionCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    payload = serializer.validated_data

    form = get_object_or_404(CheckInForm, pk=payload["form_id"])
    if not _trainer_owns_form(trainer, form):
        return Response({"detail": "Not your form."}, status=status.HTTP_403_FORBIDDEN)

    with transaction.atomic():
        next_order = (form.questions.count() + 1)
        question = CheckInQuestion.objects.create(
            form=form,
            question_text=payload["question_text"],
            question_type=payload["question_type"],
            is_required=bool(payload.get("is_required")),
            order=next_order,
            field_key="",
            is_system_question=False,
        )
        if question.question_type == CheckInQuestion.DROPDOWN:
            _replace_dropdown_options(question, payload.get("dropdown_options") or [])

    return Response(
        CheckInQuestionReadSerializer(question).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def question_update(request, question_id):
    """PATCH /api/progress/dashboard/questions/<id>/

    System questions can update text + required flag, but their type
    stays locked (the iOS client expects specific shapes for system
    fields like weight / age).
    """
    trainer, err = _require_trainer(request)
    if err:
        return err

    question = get_object_or_404(CheckInQuestion, pk=question_id)
    if not _trainer_owns_question(trainer, question):
        return Response({"detail": "Not your form."}, status=status.HTTP_403_FORBIDDEN)

    serializer = CheckInQuestionUpdateSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    payload = serializer.validated_data

    with transaction.atomic():
        if "question_text" in payload:
            question.question_text = payload["question_text"]
        if "is_required" in payload:
            # System questions stay required regardless of input
            question.is_required = True if question.is_system_question else bool(payload["is_required"])
        if "question_type" in payload and not question.is_system_question:
            question.question_type = payload["question_type"]
        question.save()

        if "dropdown_options" in payload:
            if question.question_type == CheckInQuestion.DROPDOWN:
                _replace_dropdown_options(question, payload["dropdown_options"])
            else:
                question.options.all().delete()
        elif question.question_type != CheckInQuestion.DROPDOWN:
            question.options.all().delete()

    return Response(CheckInQuestionReadSerializer(question).data)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def question_delete(request, question_id):
    """DELETE /api/progress/dashboard/questions/<id>/delete/

    System questions can't be deleted — we silently 403 rather than
    let a stray click wipe an onboarding intake field.
    """
    trainer, err = _require_trainer(request)
    if err:
        return err

    question = get_object_or_404(CheckInQuestion, pk=question_id)
    if not _trainer_owns_question(trainer, question):
        return Response({"detail": "Not your form."}, status=status.HTTP_403_FORBIDDEN)

    if question.is_system_question:
        return Response(
            {"detail": "System questions cannot be deleted."},
            status=status.HTTP_403_FORBIDDEN,
        )

    form = question.form
    with transaction.atomic():
        question.delete()
        # Compact `order` so future reorders stay sane.
        for index, remaining in enumerate(form.questions.order_by("order"), start=1):
            if remaining.order != index:
                remaining.order = index
                remaining.save(update_fields=["order"])

    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def question_reorder(request):
    """POST /api/progress/dashboard/questions/reorder/

    Body: {form_id, ordered_question_ids: [...]}
    """
    trainer, err = _require_trainer(request)
    if err:
        return err

    serializer = CheckInQuestionReorderSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    payload = serializer.validated_data

    form = get_object_or_404(CheckInForm, pk=payload["form_id"])
    if not _trainer_owns_form(trainer, form):
        return Response({"detail": "Not your form."}, status=status.HTTP_403_FORBIDDEN)

    ids = payload["ordered_question_ids"]
    existing = list(form.questions.values_list("id", flat=True))
    if set(ids) != set(existing):
        return Response(
            {"detail": "ordered_question_ids must contain exactly the form's questions."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        for index, qid in enumerate(ids, start=1):
            CheckInQuestion.objects.filter(pk=qid).update(order=index)

    refreshed = form.questions.order_by("order")
    return Response({
        "results": CheckInQuestionReadSerializer(refreshed, many=True).data,
    })


# -------------------------------------------------------------------
# Submissions feed (read-only — populated when iOS submits in Phase 7)
# -------------------------------------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def submission_list(request):
    """GET /api/progress/dashboard/submissions/?client_id=&form_id=&limit=

    Lists submissions for the trainer's clients, filterable by client
    and form. Used by the client detail page's Check-Ins cell once
    submissions start coming in.
    """
    trainer, err = _require_trainer(request)
    if err:
        return err

    qs = CheckInSubmission.objects.filter(form__user=trainer).select_related(
        "form", "client"
    ).prefetch_related("answers__question", "answers__value_option")

    client_id = request.query_params.get("client_id")
    if client_id:
        qs = qs.filter(client_id=client_id)
    form_id = request.query_params.get("form_id")
    if form_id:
        qs = qs.filter(form_id=form_id)

    try:
        limit = min(int(request.query_params.get("limit", 50)), 200)
    except ValueError:
        limit = 50

    qs = qs[:limit]
    return Response({
        "results": CheckInSubmissionReadSerializer(qs, many=True).data,
    })


# -------------------------------------------------------------------
# Per-client check-in assignments (Phase 4.5)
# -------------------------------------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def client_assignment_list(request):
    """GET /api/progress/dashboard/client-assignments/?client_id=

    Returns the trainer's three forms with each one's assignment for
    the requested client (or null if not yet assigned). Stable shape
    for the client detail page's Check-Ins cell.
    """
    trainer, err = _require_trainer(request)
    if err:
        return err

    client_id = request.query_params.get("client_id")
    if not client_id:
        return Response({"detail": "client_id required."}, status=status.HTTP_400_BAD_REQUEST)

    client = get_object_or_404(UserModel, pk=client_id, role=UserModel.CLIENT)
    if not _trainer_owns_client(trainer, client):
        return Response({"detail": "Not your client."}, status=status.HTTP_403_FORBIDDEN)

    forms = (
        CheckInForm.objects
        .filter(user=trainer, form_type__in=CheckInForm.REQUIRED_FORM_TYPES)
        .order_by("form_type")
    )

    by_form_id = {
        a.form_id: a for a in
        ClientCheckInAssignment.objects.filter(client=client, form__in=forms)
    }

    rows = []
    for f in forms:
        a = by_form_id.get(f.id)
        if a is None:
            rows.append({
                "id": None,
                "form": f.id,
                "form_name": f.name,
                "form_type": f.form_type,
                "cadence": ClientCheckInAssignment.DEFAULT_CADENCE_FOR_FORM_TYPE.get(f.form_type),
                "cadence_label": "",
                "is_active": False,
                "last_submitted_at": None,
                "next_due_at": None,
            })
        else:
            rows.append(ClientCheckInAssignmentReadSerializer(a).data)

    return Response({"results": rows})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def client_assignment_set(request):
    """POST /api/progress/dashboard/client-assignments/

    Body: {client_id, form_id, cadence?, is_active?}
    Idempotent: creates or updates the assignment. Recomputes
    next_due_at based on cadence.
    """
    trainer, err = _require_trainer(request)
    if err:
        return err

    serializer = ClientCheckInAssignmentWriteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    payload = serializer.validated_data

    client = get_object_or_404(UserModel, pk=payload["client_id"], role=UserModel.CLIENT)
    if not _trainer_owns_client(trainer, client):
        return Response({"detail": "Not your client."}, status=status.HTTP_403_FORBIDDEN)

    form = get_object_or_404(CheckInForm, pk=payload["form_id"], user=trainer)

    # Force cadence to a value that's valid for this form type.
    valid_cadences = ClientCheckInAssignment.CADENCE_OPTIONS_FOR_FORM_TYPE.get(form.form_type, [])
    requested_cadence = payload.get("cadence")
    if requested_cadence and requested_cadence in valid_cadences:
        cadence = requested_cadence
    else:
        cadence = ClientCheckInAssignment.DEFAULT_CADENCE_FOR_FORM_TYPE[form.form_type]

    is_active = payload.get("is_active", True)

    with transaction.atomic():
        assignment, _created = ClientCheckInAssignment.objects.get_or_create(
            client=client,
            form=form,
            defaults={
                "cadence": cadence,
                "is_active": is_active,
                "next_due_at": _next_due_for_cadence(cadence),
            },
        )
        # Update fields whether created or pre-existing.
        cadence_changed = assignment.cadence != cadence
        assignment.cadence = cadence
        assignment.is_active = is_active
        if cadence_changed or assignment.next_due_at is None:
            assignment.next_due_at = _next_due_for_cadence(
                cadence, anchor=assignment.last_submitted_at,
            )
        assignment.save()

    return Response(ClientCheckInAssignmentReadSerializer(assignment).data)
