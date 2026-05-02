from django.conf import settings
from django.db import models
from django.utils import timezone


class CheckInForm(models.Model):
    """One of three fixed forms per trainer: Onboarding (one-shot intake),
    Daily check-in, Routine check-in. Cadence is no longer set on the
    form itself — it lives on `ClientCheckInAssignment` so the same
    Routine form can run weekly for one client and monthly for another."""

    ONBOARDING = "onboarding"
    DAILY = "daily"
    ROUTINE = "routine"
    # Legacy: kept on the model for backwards-compat with rows already
    # in the DB. New code should use ROUTINE; the data migration in
    # 0004 rewrites existing 'weekly' rows.
    WEEKLY = "weekly"

    FORM_TYPE_CHOICES = [
        (ONBOARDING, "Onboarding"),
        (DAILY, "Daily check-in"),
        (ROUTINE, "Routine check-in"),
    ]

    # The exact set of form types every trainer should always have
    # exactly one of. Used by the workspace bootstrap.
    REQUIRED_FORM_TYPES = (ONBOARDING, DAILY, ROUTINE)

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
    DATE = "date"

    QUESTION_TYPE_CHOICES = [
        (SHORT_TEXT, "Short Text"),
        (LONG_TEXT, "Long Text"),
        (NUMBER, "Number"),
        (YES_NO, "Yes / No"),
        (DROPDOWN, "Dropdown"),
        (PHOTO, "Photo Upload"),
        (VIDEO, "Video Upload"),
        (DATE, "Date"),
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

    # `db_index=True` because the dashboard's progress charts filter
    # answers by question.field_key (e.g. WEIGHT_FIELD_KEYS) on every
    # render — without an index that's a sequential scan of every
    # question row in the trainer's account.
    field_key = models.CharField(max_length=100, blank=True, db_index=True)
    is_system_question = models.BooleanField(default=False)

    # When non-empty, this question's answer is automatically applied
    # to the named attribute on the answering client's User or
    # ClientProfile row (see `apps.users.profile_schema.SYSTEM_REQUIRED_FIELDS`
    # for the registry of allowed keys + their target models). Lets us
    # capture system-required fields like `date_of_birth` directly via
    # the trainer's onboarding form rather than a separate screen.
    system_field_key = models.CharField(max_length=80, blank=True, default="")

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


# ============================================================
# Phase 4 — Submissions (one row per client-completed form)
# ============================================================

class CheckInSubmission(models.Model):
    """One client's submission of a CheckInForm.

    A submission begins as `started` (incomplete) and moves to `submitted`
    once the client confirms. We keep both timestamps so the trainer can
    spot abandoned forms (started but not submitted) in their feed.
    """

    STATUS_STARTED = "started"
    STATUS_SUBMITTED = "submitted"
    STATUS_CHOICES = [
        (STATUS_STARTED, "Started"),
        (STATUS_SUBMITTED, "Submitted"),
    ]

    form = models.ForeignKey(
        CheckInForm,
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="checkin_submissions",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_STARTED,
    )
    started_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["form", "client", "-started_at"]),
        ]

    def __str__(self):
        return f"{self.client.username} — {self.form.name} ({self.status})"


class CheckInAnswer(models.Model):
    """One answer to one question in a submission. Polymorphic value
    columns let us store text, numbers, photos, videos, or dropdown
    selections without a join table per type."""

    submission = models.ForeignKey(
        CheckInSubmission,
        on_delete=models.CASCADE,
        related_name="answers",
    )
    question = models.ForeignKey(
        CheckInQuestion,
        on_delete=models.CASCADE,
        related_name="answers",
    )

    # Polymorphic value storage — only the matching column gets populated
    value_text = models.TextField(blank=True, default="")
    value_number = models.FloatField(null=True, blank=True)
    value_image = models.ImageField(upload_to="checkin_answers/photos/", null=True, blank=True)
    value_video = models.FileField(upload_to="checkin_answers/videos/", null=True, blank=True)
    value_yes_no = models.BooleanField(null=True, blank=True)
    value_date = models.DateField(null=True, blank=True)
    value_option = models.ForeignKey(
        CheckInQuestionOption,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="selected_in_answers",
    )

    answered_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["question__order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "question"],
                name="unique_answer_per_question_per_submission",
            ),
        ]

    def __str__(self):
        return f"Answer to {self.question.question_text[:40]}"


# ============================================================
# Phase 4.5 — Per-client check-in assignments + cadence
# ============================================================

class ClientCheckInAssignment(models.Model):
    """Connects a client to one of their trainer's three forms with a
    cadence. The same Routine form can run weekly for one client and
    monthly for another — that's why cadence lives here, not on the
    form. Onboarding and Daily forms have a fixed cadence; only Routine
    exposes the dropdown."""

    CADENCE_ONESHOT = "oneshot"     # for onboarding
    CADENCE_DAILY = "daily"         # for daily form
    CADENCE_WEEKLY = "weekly"       # routine
    CADENCE_BIWEEKLY = "biweekly"   # routine
    CADENCE_MONTHLY = "monthly"     # routine

    CADENCE_CHOICES = [
        (CADENCE_ONESHOT,  "One-shot"),
        (CADENCE_DAILY,    "Every day"),
        (CADENCE_WEEKLY,   "Every week"),
        (CADENCE_BIWEEKLY, "Every 2 weeks"),
        (CADENCE_MONTHLY,  "Every month"),
    ]

    # Cadence options the trainer can pick FROM in the UI for each
    # form type. Onboarding + Daily are fixed; routine is a real choice.
    CADENCE_OPTIONS_FOR_FORM_TYPE = {
        CheckInForm.ONBOARDING: [CADENCE_ONESHOT],
        CheckInForm.DAILY:      [CADENCE_DAILY],
        CheckInForm.ROUTINE:    [CADENCE_WEEKLY, CADENCE_BIWEEKLY, CADENCE_MONTHLY],
    }

    DEFAULT_CADENCE_FOR_FORM_TYPE = {
        CheckInForm.ONBOARDING: CADENCE_ONESHOT,
        CheckInForm.DAILY:      CADENCE_DAILY,
        CheckInForm.ROUTINE:    CADENCE_WEEKLY,
    }

    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="checkin_assignments",
    )
    form = models.ForeignKey(
        CheckInForm,
        on_delete=models.CASCADE,
        related_name="client_assignments",
    )
    cadence = models.CharField(max_length=20, choices=CADENCE_CHOICES)
    is_active = models.BooleanField(default=True)

    last_submitted_at = models.DateTimeField(null=True, blank=True)
    next_due_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["client__username", "form__form_type"]
        constraints = [
            models.UniqueConstraint(
                fields=["client", "form"],
                name="unique_assignment_per_client_per_form",
            ),
        ]

    def __str__(self):
        return f"{self.client.username} → {self.form.name} ({self.cadence})"


# ============================================================
# Hydration log
#
# One row per client per calendar day, tracking the cup count for
# that day. Backed the previously iOS-local `HomeWaterCard` so:
#   • Water trophies (8 cups in a day, 7-day hydration streak,
#     100 days hydrated) can be evaluated server-side from a single
#     source of truth.
#   • Multi-device users (phone + iPad) see the same cup count.
#   • Trainers can eventually surface hydration on the client detail
#     page if we want.
#
# `cups` is an int — the iOS UI is 8 cup-shaped tiles so capping at
# 8 (or whatever the goal is) makes sense; we leave the column
# tolerant up to PositiveSmallIntegerField's range in case the goal
# changes later.
# ============================================================
# --------------------------------------------------------------------
# D.2.1 — Solo body-weight history.
#
# PT-coded clients log weight via CheckInAnswer rows tied to their
# trainer's check-in form. Solo users have no trainer / no form, so
# they need a first-class store. One row per (user, date); same-day
# updates upsert in place.
# --------------------------------------------------------------------


class SoloBodyweightLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="solo_bodyweight_logs",
    )
    logged_on = models.DateField(default=timezone.localdate, db_index=True)
    kg = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-logged_on"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "logged_on"],
                name="unique_solo_bodyweight_per_day",
            ),
        ]


# --------------------------------------------------------------------
# D.2.2 — Progress photos.
#
# One row per uploaded photo. Stored base64 on the row for v1 (same
# trade-off as User.avatar_base64 — no S3 dependency, survives every
# Render redeploy, easy to migrate later by replacing the column with
# `storage_url` + a one-shot migrator). Cap at ~3MB raw / ~4MB after
# b64 — bigger than that and we ship Imagemagick to compress on
# upload.
#
# `category` lets the user organise their gallery (front/side/back).
# Free tier gets 1 photo / month; Pro + Pro AI get unlimited. Cap is
# enforced in the view, not on the model — keeps the data clean.
# --------------------------------------------------------------------


class ProgressPhoto(models.Model):
    CAT_FRONT = "front"
    CAT_SIDE  = "side"
    CAT_BACK  = "back"
    CAT_OTHER = "other"
    CATEGORY_CHOICES = [
        (CAT_FRONT, "Front"),
        (CAT_SIDE,  "Side"),
        (CAT_BACK,  "Back"),
        (CAT_OTHER, "Other"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="progress_photos",
    )
    category = models.CharField(
        max_length=8, choices=CATEGORY_CHOICES, default=CAT_FRONT,
    )
    image_base64 = models.TextField()
    bodyweight_kg = models.FloatField(null=True, blank=True)  # snapshot for compare cards
    note = models.CharField(max_length=255, blank=True, default="")
    taken_on = models.DateField(default=timezone.localdate, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # PHOTO-COACHING (#106) — AI Vision commentary. The photo is
    # sent to Claude Vision with a calm-coach system prompt that
    # ABSOLUTELY rules out appearance/attractiveness commentary.
    # Returns short objective text: visible posture / definition /
    # composition observations + a one-liner takeaway. Nullable —
    # photos uploaded before #106 shipped (and any photo where the
    # user opts out) leave these empty.
    ai_commentary = models.TextField(blank=True, default="")
    ai_analyzed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-taken_on", "-created_at"]


class HydrationLog(models.Model):
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hydration_logs",
    )
    logged_on = models.DateField(default=timezone.localdate)
    cups = models.PositiveSmallIntegerField(default=0)

    # Tracks the user's daily goal at the moment of logging so the
    # "8 cups in a day" trophy keeps working even if we later change
    # the default goal globally. Snapshot of the target the user was
    # working against that day.
    goal_cups = models.PositiveSmallIntegerField(default=8)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["client", "logged_on"],
                name="unique_hydration_per_client_per_day",
            ),
        ]
        indexes = [
            # "How has this client been hydrating recently?" — both
            # the iOS sync endpoint and any future trainer dashboard.
            models.Index(fields=["client", "-logged_on"]),
        ]
        ordering = ["-logged_on"]

    def __str__(self):
        return f"{self.client.username} {self.logged_on}: {self.cups}/{self.goal_cups}"

    @property
    def goal_met(self) -> bool:
        return self.cups >= self.goal_cups
