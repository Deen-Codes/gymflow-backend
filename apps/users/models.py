from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    TRAINER = "trainer"
    CLIENT = "client"

    ROLE_CHOICES = [
        (TRAINER, "Trainer"),
        (CLIENT, "Client"),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    # Used by the "Birthday Workout" trophy and (eventually) by any
    # birthday-aware notifications. Optional — most existing users
    # haven't supplied this so we never want to require it.
    date_of_birth = models.DateField(null=True, blank=True)

    # SSO subject identifiers — stable per-provider user id from the
    # ID token's `sub` claim. Indexed unique so we can look up an
    # incoming SSO sign-in in O(1). Both nullable because most
    # existing users authenticate via password / magic-link only.
    apple_sub = models.CharField(
        max_length=255, null=True, blank=True, unique=True, db_index=True,
    )
    google_sub = models.CharField(
        max_length=255, null=True, blank=True, unique=True, db_index=True,
    )

    # Profile P.1.1 — avatar stored as base64 directly on the row.
    # Caps at ~1.4MB after b64 (≈ 1MB raw image), client-side
    # downsizing in iOS keeps payloads small. Cheap to migrate to
    # S3 later: replace this column with avatar_url, run a one-shot
    # migrator. For now, zero external infra and survives redeploys.
    avatar_base64 = models.TextField(null=True, blank=True)

    # Per-channel notification toggles + quiet hours. JSON to keep
    # the schema flexible — adding a new channel is a no-op
    # migration. Shape (defaulted by .get on the iOS side):
    #   { push_enabled, workout_reminders, check_in_nudges,
    #     coach_messages, marketing,
    #     quiet_hours_enabled, quiet_hours_start_min,
    #     quiet_hours_end_min }
    notification_prefs = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.username} ({self.role})"


class TrainerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="trainer_profile")
    business_name = models.CharField(max_length=255, blank=True)
    slug = models.SlugField(unique=True)

    # Phase 7.7.1 — Stripe Connect. Populated after the trainer
    # completes OAuth at /payments/oauth/connect/. Empty = not
    # connected. We never store secrets here, only the connected
    # account ID (acct_…) which is safe to keep in the DB.
    stripe_user_id = models.CharField(max_length=64, blank=True, default="")

    def __str__(self):
        return self.business_name or self.user.username

    @property
    def stripe_connected(self) -> bool:
        return bool(self.stripe_user_id)


class ClientProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="client_profile")
    trainer = models.ForeignKey(
        TrainerProfile,
        on_delete=models.CASCADE,
        related_name="clients"
    )
    assigned_workout_plan = models.ForeignKey(
        "workouts.WorkoutPlan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_clients"
    )
    assigned_nutrition_plan = models.ForeignKey(
        "nutrition.NutritionPlan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_clients"
    )
    # Trainer-set goal weight. Powers the "Goal Weight Reached" trophy
    # and is exposed on the client detail page. Optional — many clients
    # don't have a fixed kilo target (e.g. recomp goals), so the field
    # stays nullable.
    goal_weight_kg = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True,
    )

    def __str__(self):
        return self.user.username


# ----------------------------------------------------------------------
# Hub content — DB-backed Changelog + Coaching Tips so we can post
# updates without redeploying. Both are admin-managed; the trainer
# hub view picks the latest published rows on every render.
#
# Was hardcoded as Python lists in dashboard_hub_views.py — replaced
# in task #37 with these two tiny models. Kept dead-simple; if either
# grows audience targeting, scheduling, or rich-text bodies, split
# into a dedicated `apps.cms` app.
# ----------------------------------------------------------------------


class Changelog(models.Model):
    """A short "what's new" announcement shown at the top of the
    trainer Hub. Only the most-recent published entry renders.

    Audience field lets us target trainers vs. clients with different
    copy when the iOS client also starts reading from a Changelog
    feed (currently iOS doesn't, but the field future-proofs the
    model so we don't migrate it later)."""

    AUDIENCE_TRAINERS = "trainers"
    AUDIENCE_CLIENTS = "clients"
    AUDIENCE_ALL = "all"
    AUDIENCE_CHOICES = [
        (AUDIENCE_TRAINERS, "Trainers"),
        (AUDIENCE_CLIENTS,  "Clients"),
        (AUDIENCE_ALL,      "Everyone"),
    ]

    title = models.CharField(max_length=120)
    body = models.TextField()
    audience = models.CharField(
        max_length=10,
        choices=AUDIENCE_CHOICES,
        default=AUDIENCE_TRAINERS,
    )
    # Optional CTA link + label. Used by the hub card's "Set up
    # tiers →" style affordance. Empty = no CTA, just the title +
    # body.
    cta_url = models.CharField(max_length=255, blank=True, default="")
    cta_label = models.CharField(max_length=80, blank=True, default="")
    # Drafts can sit unpublished while we iterate the copy. Only
    # `published=True` entries surface on the hub.
    published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]
        verbose_name = "Changelog entry"
        verbose_name_plural = "Changelog"

    def __str__(self):
        return f"{self.title} ({'live' if self.published else 'draft'})"


class CoachingTip(models.Model):
    """A short coaching/business tip shown in the Hub's tips card.

    Tips rotate — the hub picks N most recent published entries (or
    a deterministic per-trainer rotation later). Categories keep
    them organisable and let us segment if the rotation logic gets
    smarter ("show finance tips to trainers without Stripe set up",
    etc.)."""

    CATEGORY_BUSINESS = "business"
    CATEGORY_PROGRAMMING = "programming"
    CATEGORY_NUTRITION = "nutrition"
    CATEGORY_RETENTION = "retention"
    CATEGORY_MINDSET = "mindset"
    CATEGORY_CHOICES = [
        (CATEGORY_BUSINESS,    "Business"),
        (CATEGORY_PROGRAMMING, "Programming"),
        (CATEGORY_NUTRITION,   "Nutrition"),
        (CATEGORY_RETENTION,   "Retention"),
        (CATEGORY_MINDSET,     "Mindset"),
    ]

    icon = models.CharField(
        max_length=8,
        default="✦",
        help_text="Single glyph rendered before the tip title. Keep tasteful.",
    )
    title = models.CharField(max_length=120)
    body = models.TextField()
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default=CATEGORY_BUSINESS,
    )
    published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]
        verbose_name = "Coaching tip"
        verbose_name_plural = "Coaching tips"

    def __str__(self):
        return f"{self.icon} {self.title}"


# ----------------------------------------------------------------------
# Magic-link login (task #25 / L.1.1)
#
# One row per outstanding sign-in link. Tokens are URL-safe random
# strings of ~43 chars (secrets.token_urlsafe(32)) so they're
# infeasible to guess but short enough to fit in a deep-link URL.
# Single-use + 10-minute TTL — once `used_at` is stamped or
# `expires_at` is in the past, the verify endpoint refuses to
# exchange the token for a session.
# ----------------------------------------------------------------------


class MagicLoginToken(models.Model):
    DEFAULT_TTL_MINUTES = 10

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="magic_login_tokens",
    )
    # The opaque token the iOS app receives in the email + sends back
    # to verify. Indexed unique because that's the lookup column.
    token = models.CharField(max_length=128, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Auto-set on save() if not supplied.
    expires_at = models.DateTimeField()
    # Nullable — stamped when the verify endpoint exchanges this
    # token for a session. Single-use enforcement.
    used_at = models.DateTimeField(null=True, blank=True)
    # Optional metadata for security forensics ("link sent from IP X,
    # used from IP Y, 6 minutes later"). Both columns are best-effort.
    requested_ip = models.GenericIPAddressField(null=True, blank=True)
    consumed_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Magic-login token"
        verbose_name_plural = "Magic-login tokens"

    def __str__(self):
        state = "used" if self.used_at else ("expired" if self.is_expired else "live")
        return f"{self.user.username} · {state}"

    @property
    def is_expired(self):
        from django.utils import timezone
        return self.expires_at <= timezone.now()

    @property
    def is_consumable(self):
        return self.used_at is None and not self.is_expired

    def save(self, *args, **kwargs):
        # Auto-compute expires_at on first save so callers don't need
        # to know the TTL.
        if not self.expires_at:
            from datetime import timedelta
            from django.utils import timezone
            self.expires_at = timezone.now() + timedelta(minutes=self.DEFAULT_TTL_MINUTES)
        super().save(*args, **kwargs)
