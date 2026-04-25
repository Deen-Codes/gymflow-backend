"""Serializers for the Phase 4 check-ins dashboard JSON endpoints
(drag-drop form builder + per-client submissions feed)."""
from rest_framework import serializers

from .models import (
    CheckInForm,
    CheckInQuestion,
    CheckInQuestionOption,
    CheckInSubmission,
    CheckInAnswer,
    ClientCheckInAssignment,
)


class CheckInQuestionOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CheckInQuestionOption
        fields = ["id", "value", "order"]


class CheckInQuestionReadSerializer(serializers.ModelSerializer):
    options = CheckInQuestionOptionSerializer(many=True, read_only=True)

    class Meta:
        model = CheckInQuestion
        fields = [
            "id",
            "question_text",
            "question_type",
            "is_required",
            "is_system_question",
            "field_key",
            "order",
            "options",
        ]


class CheckInQuestionCreateSerializer(serializers.Serializer):
    """Create a non-system question on a form. Order auto-computed
    server-side (appended to the bottom)."""
    form_id = serializers.IntegerField()
    question_text = serializers.CharField(max_length=255)
    question_type = serializers.ChoiceField(choices=CheckInQuestion.QUESTION_TYPE_CHOICES)
    is_required = serializers.BooleanField(required=False, default=False)
    dropdown_options = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        default=list,
    )


class CheckInQuestionUpdateSerializer(serializers.Serializer):
    """Patch any subset of editable fields on a question."""
    question_text = serializers.CharField(max_length=255, required=False)
    question_type = serializers.ChoiceField(
        choices=CheckInQuestion.QUESTION_TYPE_CHOICES, required=False
    )
    is_required = serializers.BooleanField(required=False)
    dropdown_options = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
    )


class CheckInQuestionReorderSerializer(serializers.Serializer):
    form_id = serializers.IntegerField()
    ordered_question_ids = serializers.ListField(
        child=serializers.IntegerField(), allow_empty=True
    )


# ---- Submissions (read-only for the dashboard until iOS submits) ----

class CheckInAnswerReadSerializer(serializers.ModelSerializer):
    question_text = serializers.CharField(source="question.question_text", read_only=True)
    question_type = serializers.CharField(source="question.question_type", read_only=True)
    selected_option = serializers.CharField(source="value_option.value", read_only=True, default=None)

    class Meta:
        model = CheckInAnswer
        fields = [
            "id",
            "question_text",
            "question_type",
            "value_text",
            "value_number",
            "value_yes_no",
            "value_image",
            "value_video",
            "selected_option",
            "answered_at",
        ]


class CheckInSubmissionReadSerializer(serializers.ModelSerializer):
    form_name = serializers.CharField(source="form.name", read_only=True)
    form_type = serializers.CharField(source="form.form_type", read_only=True)
    client_username = serializers.CharField(source="client.username", read_only=True)
    answers = CheckInAnswerReadSerializer(many=True, read_only=True)

    class Meta:
        model = CheckInSubmission
        fields = [
            "id",
            "form_name",
            "form_type",
            "client_username",
            "status",
            "started_at",
            "submitted_at",
            "answers",
        ]


class CheckInFormReadSerializer(serializers.ModelSerializer):
    questions = CheckInQuestionReadSerializer(many=True, read_only=True)
    question_count = serializers.SerializerMethodField()

    class Meta:
        model = CheckInForm
        fields = [
            "id",
            "name",
            "form_type",
            "is_active",
            "created_at",
            "questions",
            "question_count",
        ]

    def get_question_count(self, obj):
        return obj.questions.count()


# ---- Per-client check-in assignments + cadence -----------------

class ClientCheckInAssignmentReadSerializer(serializers.ModelSerializer):
    form_name = serializers.CharField(source="form.name", read_only=True)
    form_type = serializers.CharField(source="form.form_type", read_only=True)
    cadence_label = serializers.CharField(source="get_cadence_display", read_only=True)

    class Meta:
        model = ClientCheckInAssignment
        fields = [
            "id",
            "form",
            "form_name",
            "form_type",
            "cadence",
            "cadence_label",
            "is_active",
            "last_submitted_at",
            "next_due_at",
        ]


class ClientCheckInAssignmentWriteSerializer(serializers.Serializer):
    """Create or update an assignment. Idempotent on (client, form):
    if one exists we update; if not we create."""
    client_id = serializers.IntegerField()
    form_id = serializers.IntegerField()
    cadence = serializers.ChoiceField(
        choices=ClientCheckInAssignment.CADENCE_CHOICES,
        required=False,
    )
    is_active = serializers.BooleanField(required=False, default=True)
