from django.conf import settings
from django.db import models


class CheckInForm(models.Model):
    ONBOARDING = "onboarding"
    DAILY = "daily"
    WEEKLY = "weekly"

    FORM_TYPE_CHOICES = [
        (ONBOARDING, "Onboarding"),
        (DAILY, "Daily"),
        (WEEKLY, "Weekly"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="checkin_forms",
    )
    name = models.CharField(max_length=255)
    form_type = models.CharField(max_length=20, choices=FORM_TYPE_CHOICES)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["form_type", "name"]

    def __str__(self):
        return f"{self.name} ({self.form_type})"


class CheckInQuestion(models.Model):
    SHORT_TEXT = "short_text"
    LONG_TEXT = "long_text"
    NUMBER = "number"
    YES_NO = "yes_no"
    DROPDOWN = "dropdown"
    PHOTO = "photo"
    VIDEO = "video"

    QUESTION_TYPE_CHOICES = [
        (SHORT_TEXT, "Short Text"),
        (LONG_TEXT, "Long Text"),
        (NUMBER, "Number"),
        (YES_NO, "Yes / No"),
        (DROPDOWN, "Dropdown"),
        (PHOTO, "Photo Upload"),
        (VIDEO, "Video Upload"),
    ]

    form = models.ForeignKey(
        CheckInForm,
        on_delete=models.CASCADE,
        related_name="questions",
    )
    question_text = models.CharField(max_length=255)
    question_type = models.CharField(max_length=50, choices=QUESTION_TYPE_CHOICES)
    is_required = models.BooleanField(default=False)
    order = models.IntegerField(default=1)

    field_key = models.CharField(max_length=100, blank=True)
    is_system_question = models.BooleanField(default=False)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.question_text


class CheckInQuestionOption(models.Model):
    question = models.ForeignKey(
        CheckInQuestion,
        on_delete=models.CASCADE,
        related_name="options",
    )
    value = models.CharField(max_length=100)
    order = models.IntegerField(default=1)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.value
