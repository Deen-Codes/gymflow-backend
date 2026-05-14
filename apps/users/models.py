from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    TRAINER = "trainer"
    CLIENT = "client"
    # E.1 / SOLO MVP — self-serve users on a Solo subscription. They
    # never have a TrainerProfile linking them to a coach; their
    # programmes come from the public catalog. Kept distinct from
    # CLIENT so trainer-side queries (`User.objects.filter(role=CLIENT)`)
    # don't accidentally enumerate solo accounts.
    SOLO = "solo"

    ROLE_CHOICES = [
        (TRAINER, "Trainer"),
        (CLIENT, "Client"),
        (SOLO,   "Solo"),
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

    # M.2 — programmatic SEO city directory pages. Free-text city
    # name set by the trainer (e.g. "London", "Manchester", "New
    # York"). We slugify on read for the URL. Optional — older
    # trainers won't have one set, and they simply won't appear in
    # any city directory until they fill this in. Indexed for the
    # cheap city listing GROUP BY.
    city = models.CharField(max_length=80, blank=True, default="", db_index=True)
    country = models.CharField(max_length=80, blank=True, default="")

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
# E.1 / SOLO MVP — self-serve user profile.
#
# Solo users sign up without a trainer; their programme comes from the
# public catalog and (eventually) the AI PT adapts it weekly. The
# fields below are everything we capture in the 5-screen onboarding
# flow — intentionally minimal because every extra field drops
# completion 5–10% (research-backed).
#
# Why a separate model rather than putting the answers on the User
# row:
#   1. Keeps the User table tight — these fields only matter for
#      Solo accounts and shouldn't bloat the row for trainers/clients.
#   2. Lets the iOS app PATCH the answers later without an unrelated
#      User serializer touching them.
#   3. Future AI PT (#59) reads from this model directly — clean
#      contract.
# ----------------------------------------------------------------------


class SoloProfile(models.Model):
    """Solo user's onboarding answers + subscription state."""

    # Goal multi-select. Stored as JSON so adding/removing options is
    # a no-op migration. iOS posts a list of strings from a fixed
    # vocabulary: build_muscle, lose_fat, get_stronger, stay_consistent,
    # train_for_sport.
    GOAL_CHOICES = [
        ("build_muscle",     "Build muscle"),
        ("lose_fat",         "Lose fat"),
        ("get_stronger",     "Get stronger"),
        ("stay_consistent",  "Stay consistent"),
        ("train_for_sport",  "Train for a sport"),
    ]

    EXPERIENCE_CHOICES = [
        ("just_starting",    "Just starting"),
        ("under_one_year",   "0–1 year"),
        ("one_to_three",     "1–3 years"),
        ("three_plus",       "3+ years"),
    ]

    EQUIPMENT_CHOICES = [
        ("full_gym",         "Full gym"),
        ("home_with_weights","Home with weights"),
        ("bodyweight_only",  "Bodyweight only"),
        ("mixed",            "Mixed"),
    ]

    # SIGNUP-RESTRUCTURE (D-AFK.4) — identity fields captured at
    # signup. Gender uses a single inclusive list. Sex-at-birth is
    # a separate optional field used ONLY by macro calc (BMR formulas
    # are sex-keyed at the biology level). Most users see the gender
    # question and skip the sex-at-birth one — it's labelled as
    # "Used for accurate calorie calculations" so the framing is
    # opt-in for accuracy, not pry.
    GENDER_CHOICES = [
        ("male",         "Male"),
        ("female",       "Female"),
        ("non_binary",   "Non-binary"),
        ("prefer_not",   "Prefer not to say"),
    ]
    SEX_BIRTH_CHOICES = [
        ("male",   "Male"),
        ("female", "Female"),
        ("",       "Unspecified"),
    ]

    # Subscription tier. Drives feature gating across iOS + backend.
    TIER_FREE = "free"
    TIER_PRO = "pro"
    TIER_PRO_AI = "pro_ai"
    TIER_CHOICES = [
        (TIER_FREE,   "Free"),
        (TIER_PRO,    "Pro"),
        (TIER_PRO_AI, "Pro AI"),
    ]

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="solo_profile",
    )

    goals      = models.JSONField(default=list, blank=True)
    experience = models.CharField(max_length=20, choices=EXPERIENCE_CHOICES, blank=True, default="")
    equipment  = models.CharField(max_length=20, choices=EQUIPMENT_CHOICES, blank=True, default="")
    days_per_week = models.PositiveSmallIntegerField(default=3)

    # SIGNUP-RESTRUCTURE (D-AFK.4) — identity captured at signup.
    # All blank-by-default so existing users + Apple Health users
    # who skip these still flow through with no breakage.
    gender         = models.CharField(
        max_length=16, choices=GENDER_CHOICES, blank=True, default="",
    )
    sex_at_birth   = models.CharField(
        max_length=8, choices=SEX_BIRTH_CHOICES, blank=True, default="",
    )
    height_cm      = models.PositiveSmallIntegerField(null=True, blank=True)

    # SOLO-02 — assigned programme. Null on signup; set when the user
    # picks one from the catalog. Mirrors ClientProfile.assigned_workout_plan
    # so the existing workouts pipeline (next_workout, plan_active,
    # etc.) Just Works once `get_user_active_plan` is extended to
    # check this. on_delete=SET_NULL so deleting a programme doesn't
    # cascade-delete the user.
    assigned_workout_plan = models.ForeignKey(
        "workouts.WorkoutPlan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_solo_users",
    )

    # Subscription — defaults to free; SOLO-03 (Stripe billing) flips
    # this when a Checkout session completes. `tier_active_until` is
    # null while on free or while a paid sub is current; populated
    # only when a sub has been cancelled but is still in its paid-
    # through window.
    tier               = models.CharField(max_length=10, choices=TIER_CHOICES, default=TIER_FREE)
    tier_active_until  = models.DateTimeField(null=True, blank=True)
    trial_started_at   = models.DateTimeField(null=True, blank=True)
    trial_ends_at      = models.DateTimeField(null=True, blank=True)

    # Stripe subscription ID — never store the secret, only the public
    # ID that lets us look up + cancel via the Stripe API. Empty when
    # on free.
    stripe_subscription_id = models.CharField(max_length=64, blank=True, default="")

    # N.1.1 — daily macro targets. NUTRITION-ONBOARDING-FIX —
    # defaults dropped to 0 so the iOS Nutrition tab can use
    # `target_calories == 0` as the "first-time setup" gate. The
    # cinematic onboarding (NutritionAIOnboardingFlow) writes
    # explicit values via /api/nutrition/solo/macro-targets/ once
    # the user picks an AI / manual / unsure path — until then,
    # the empty-state card is what the user sees.
    #
    # Previously these defaulted to 2200/140/240/70, which meant
    # every fresh signup landed straight on a populated macro hero
    # with bogus default targets and the onboarding never showed.
    #
    # Bodyweight is needed for protein-per-kg calc; we don't capture
    # it in onboarding (per the user's "minimal questions" pivot) so
    # it lands either via Apple Health sync (#81) or when the user
    # logs their first weight check-in. Until then `compute_default_
    # macro_targets` falls back to 75 kg.
    target_calories = models.PositiveIntegerField(default=0)
    target_protein  = models.PositiveSmallIntegerField(default=0)  # grams
    target_carbs    = models.PositiveSmallIntegerField(default=0)  # grams
    target_fats     = models.PositiveSmallIntegerField(default=0)  # grams
    bodyweight_kg   = models.FloatField(null=True, blank=True)

    # HK-AUTOSYNC-TIMESTAMPS — per-field "last touched in-app" stamps.
    # The Apple Health sync uses these to decide source-of-truth: if
    # HK's most-recent sample's endDate is newer than the relevant
    # stamp by more than ~1h, HK wins; otherwise the in-app value
    # wins. Without these, the sync defaulted to overwriting fresh
    # in-app input with stale Health data — fixed in HK-AUTOSYNC-TS.
    # Set on every write through setup-progress and personal-details.
    bodyweight_updated_at = models.DateTimeField(null=True, blank=True)
    height_updated_at     = models.DateTimeField(null=True, blank=True)

    # DAILY-MEAL-PLAN — two top-level nutrition modes:
    #   "ad_hoc"     → log foods freely to hit macros (default)
    #   "meal_plan"  → fixed daily plan; the same set of MealTemplate
    #                 rows show every day as the planned meals, each
    #                 with one-tap "Log" buttons.
    # Switchable anytime from the Meals hub. The plan itself is
    # encoded by `MealTemplate.is_in_daily_plan` flags rather than a
    # separate join table — keeps the data model boring + matches
    # the "saved meals are the unit" mental model.
    NUTRITION_MODE_AD_HOC    = "ad_hoc"
    NUTRITION_MODE_MEAL_PLAN = "meal_plan"
    NUTRITION_MODE_CHOICES   = [
        (NUTRITION_MODE_AD_HOC,    "Eat as you go"),
        (NUTRITION_MODE_MEAL_PLAN, "Set meal plan"),
    ]
    nutrition_mode = models.CharField(
        max_length=16,
        choices=NUTRITION_MODE_CHOICES,
        default=NUTRITION_MODE_AD_HOC,
    )

    # ONBOARDING-QUICK-START — per-step completion flags for the
    # in-app setup strip. The strip on Home shows progress of 1/5
    # through 5/5 and disappears at 5/5. These are explicit booleans
    # (not derived from other fields) so we can distinguish "user
    # filled this in via the setup hub" from "field happens to be
    # non-null for another reason". Each step's `_done` flag flips
    # true via PATCH /api/users/me/setup-progress/ or via the data
    # migration backfill for users who'd already filled the relevant
    # fields before this feature shipped.
    setup_apple_health_done    = models.BooleanField(default=False)
    setup_body_stats_done      = models.BooleanField(default=False)
    setup_goal_done            = models.BooleanField(default=False)
    setup_training_done        = models.BooleanField(default=False)
    setup_nutrition_style_done = models.BooleanField(default=False)

    @property
    def setup_complete(self) -> bool:
        """All five setup steps marked done. Drives the trophy
        `set_up_strong` award and the iOS strip's visibility."""
        return (
            self.setup_apple_health_done
            and self.setup_body_stats_done
            and self.setup_goal_done
            and self.setup_training_done
            and self.setup_nutrition_style_done
        )

    # Phase A — goal weight. Optional; the user sets it from the
    # Profile → Personal Details sheet (or by replying in chat —
    # the AI surfaces a Profile shortcut). The AI PT context block
    # always includes (current_kg, goal_kg, delta_to_goal) when both
    # are known so the model can frame progress against the target,
    # not in absolute kg ("you're 1.6 kg from goal" beats "you've
    # lost 1.6 kg" for motivation framing — Locke & Latham 1990,
    # specific + measurable goals drive adherence).
    goal_weight_kg  = models.FloatField(null=True, blank=True)

    # AI-BUILD-ONBOARDING — captured during the AI workout build's
    # cinematic onboarding flow. Surfaces in the AI PT user context
    # so the model schedules around the user's life and respects
    # avoidances. Each is optional so users who never run AI build
    # don't get nagged for them.
    #
    # `training_days` — list of weekday short codes the user trains
    # on. Values from {"mon","tue","wed","thu","fri","sat","sun"}.
    # Combined with `days_per_week` becomes the calendar-strip
    # source-of-truth for HOME-CALENDAR-INTERACTIVE. Empty when
    # not yet set.
    training_days   = models.JSONField(default=list, blank=True)
    # `session_minutes` — typical session length (30 / 45 / 60 /
    # 75 / 90+ minutes). Drives the AI build's exercise count +
    # the ON DECK card's `~X MIN` estimate.
    session_minutes = models.PositiveSmallIntegerField(default=0)
    # `avoidances` — free-form list of things the user wants to
    # skip: "knee pain", "shoulder issues", "no overhead press",
    # "hate running", etc. Mix of curated chips + user free-text.
    # The AI PT system prompt's safety + preference rules read
    # this list and route around it.
    avoidances      = models.JSONField(default=list, blank=True)

    # NUTRITION-AI-ONBOARDING — captured during the cinematic
    # nutrition setup flow (parallel to AI-BUILD-ONBOARDING for
    # workouts). Surfaces in the AI PT user context so meal
    # suggestions respect dietary patterns, allergies, dislikes.
    #
    # `dietary_pattern` — single-select. None means no
    # restriction; the rest cover the major patterns a PT would
    # ask about. Free-text variants land in `dietary_other`.
    DIETARY_NONE         = "none"
    DIETARY_PESCATARIAN  = "pescatarian"
    DIETARY_VEGETARIAN   = "vegetarian"
    DIETARY_VEGAN        = "vegan"
    DIETARY_HALAL        = "halal"
    DIETARY_KOSHER       = "kosher"
    DIETARY_OTHER        = "other"
    DIETARY_CHOICES = [
        (DIETARY_NONE,        "None"),
        (DIETARY_PESCATARIAN, "Pescatarian"),
        (DIETARY_VEGETARIAN,  "Vegetarian"),
        (DIETARY_VEGAN,       "Vegan"),
        (DIETARY_HALAL,       "Halal"),
        (DIETARY_KOSHER,      "Kosher"),
        (DIETARY_OTHER,       "Other"),
    ]
    dietary_pattern = models.CharField(
        max_length=16, choices=DIETARY_CHOICES, blank=True, default="",
    )
    # Free-text describing the "other" pattern when dietary_pattern
    # is "other" (e.g. "low FODMAP", "ketogenic"). Optional.
    dietary_other     = models.CharField(max_length=120, blank=True, default="")
    # Allergies + restrictions (multi-select + free-text). Mix of
    # common chips ("Nuts", "Dairy", "Gluten", etc.) and user
    # free-text. The AI PT routes around these when suggesting
    # meals or food swaps.
    food_restrictions = models.JSONField(default=list, blank=True)
    # Foods the user doesn't enjoy. Separate from restrictions
    # because "I dislike broccoli" is different from "I'm allergic
    # to peanuts" — the AI handles them with different urgency.
    food_dislikes     = models.JSONField(default=list, blank=True)
    # Meals per day. 0 = unspecified; 2-6 are the valid range.
    meals_per_day     = models.PositiveSmallIntegerField(default=0)
    # Cooking comfort. Drives meal-suggestion complexity.
    COOKING_LOVE         = "love"
    COOKING_COMFORTABLE  = "comfortable"
    COOKING_PREASSEMBLED = "preassembled"
    COOKING_EATING_OUT   = "eating_out"
    COOKING_CHOICES = [
        (COOKING_LOVE,         "Love cooking"),
        (COOKING_COMFORTABLE,  "Comfortable"),
        (COOKING_PREASSEMBLED, "Mostly pre-assembled"),
        (COOKING_EATING_OUT,   "Eat out a lot"),
    ]
    cooking_comfort = models.CharField(
        max_length=16, choices=COOKING_CHOICES, blank=True, default="",
    )

    # Phase A — working "phase" the user is currently in. Distinct
    # from `goals` (which are sacred + long-term and only change via
    # explicit Profile edits). The phase is HOW the user is moving
    # toward the goal right now: actively cutting / holding /
    # bulking. The AI PT proposes phase transitions when the data
    # supports them ("you've held this weight for 3 weeks, want to
    # ease out of the cut?") via the `change_goal_phase` mutation.
    PHASE_CUT         = "cut"
    PHASE_MAINTENANCE = "maintenance"
    PHASE_BULK        = "bulk"
    PHASE_CHOICES = [
        (PHASE_CUT,         "Cut"),
        (PHASE_MAINTENANCE, "Maintenance"),
        (PHASE_BULK,        "Bulk"),
    ]
    phase = models.CharField(
        max_length=12, choices=PHASE_CHOICES,
        default=PHASE_MAINTENANCE,
    )
    phase_started_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Solo {self.user.username} ({self.tier})"

    @property
    def has_ai_access(self) -> bool:
        """True iff the user is on Pro AI (paid or in trial)."""
        return self.tier == self.TIER_PRO_AI

    @property
    def has_pro_access(self) -> bool:
        """True iff the user has Pro OR Pro AI."""
        return self.tier in (self.TIER_PRO, self.TIER_PRO_AI)

    def compute_default_macro_targets(self, *, save: bool = True) -> dict:
        """Evidence-based defaults from the user's goal + bodyweight.

        Logic — derived from the macro research in SOLO_MVP_DESIGN.md
        sources (Helms et al. / RP-style cutting-bulking):

          • Bodyweight (kg). Falls back to 75kg if not set.
          • TDEE estimate: 30 kcal/kg/day for moderate activity.
          • Goal modifier:
              build_muscle      → +250 kcal (lean surplus)
              get_stronger      → +250 kcal
              lose_fat          → −400 kcal (sustainable cut)
              stay_consistent   → maintenance
              train_for_sport   → maintenance
          • Protein:  1.8 g/kg (research minimum for hypertrophy in
                                deficit; sufficient at maintenance).
          • Fat:      0.8 g/kg (~25–30% of total kcal at most goals).
          • Carbs:    remainder of kcal / 4.

        Returns the calculated dict; mutates + saves the SoloProfile
        unless save=False (useful for previews / dry-run).
        """
        bw = self.bodyweight_kg or 75.0
        tdee = bw * 30.0
        goals = self.goals or []
        modifier = 0
        if "lose_fat" in goals:
            modifier = -400
        elif "build_muscle" in goals or "get_stronger" in goals:
            modifier = 250
        target_kcal = max(1200, int(tdee + modifier))

        protein_g = round(bw * 1.8)
        fat_g     = round(bw * 0.8)
        # carbs are whatever kcal is left after protein (4) + fat (9)
        used_kcal = (protein_g * 4) + (fat_g * 9)
        carb_kcal = max(0, target_kcal - used_kcal)
        carbs_g   = max(0, round(carb_kcal / 4))

        targets = {
            "target_calories": target_kcal,
            "target_protein":  protein_g,
            "target_carbs":    carbs_g,
            "target_fats":     fat_g,
        }
        if save:
            for k, v in targets.items():
                setattr(self, k, v)
            self.save(update_fields=list(targets.keys()))
        return targets


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


# ----------------------------------------------------------------------
# Phase A — AI mutation audit trail. Models live in `mutation_models.py`
# so the import surface for `apps.users.models` stays focussed. Re-
# exported here so existing `from .models import ...` patterns keep
# working without touching dozens of call sites.
# ----------------------------------------------------------------------

from .mutation_models import (  # noqa: E402  (intentional bottom-of-file import)
    MutationStatus,
    WorkoutMutation,
    NutritionMutation,
    CardioMutation,
)


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


# ====================================================================
# EMAIL-EDIT — EmailChangeRequest
#
# Six-digit OTP code emailed to the user's NEW address. Verifies they
# control the new inbox before rotating User.email. Picked OTP over
# deep-link verification because:
#   • No app-switching during a single in-app flow.
#   • Same pattern users already know from banks / Apple ID.
#   • Code is short enough to type by hand if mail client mangles it.
#
# TTL 15 min. Single-use. Old un-used codes for the same user are
# invalidated when a new code is requested.
# ====================================================================
class EmailChangeRequest(models.Model):
    DEFAULT_TTL_MINUTES = 15

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="email_change_requests",
    )
    new_email = models.EmailField()
    # 6-digit zero-padded string; stored as text so leading zeros are
    # preserved and so a future change to a longer code (e.g. 8) won't
    # require a migration.
    code = models.CharField(max_length=12, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    # Cosmetic — for the rare "I never asked for this" support case.
    requested_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Email change request"
        verbose_name_plural = "Email change requests"

    def __str__(self):
        state = "used" if self.used_at else ("expired" if self.is_expired else "live")
        return f"{self.user.username} → {self.new_email} · {state}"

    @property
    def is_expired(self):
        from django.utils import timezone
        return self.expires_at <= timezone.now()

    @property
    def is_consumable(self):
        return self.used_at is None and not self.is_expired

    def save(self, *args, **kwargs):
        if not self.expires_at:
            from datetime import timedelta
            from django.utils import timezone
            self.expires_at = timezone.now() + timedelta(minutes=self.DEFAULT_TTL_MINUTES)
        super().save(*args, **kwargs)


# ====================================================================
# T2.10 — RecentEditLog
#
# Lightweight log of user-side edits (workout swaps, sets/reps
# changes, manual meal builds, macro target overrides). Powers the
# AI PT context line "Recent user edits: ..." so chat / weekly
# review can comment intelligently on what the user changed without
# the AI having to ask. Capped to the last 30 days, 50 rows per
# user — bigger windows arent useful and uncapped logs grow
# unbounded.
#
# Write sites:
#   • T2.8 in-place workout edits (swap, set/rep change, add/remove)
#   • T2.9 nutrition manual builder (meal create/edit/delete)
#   • /api/nutrition/solo/macro-targets/ direct user-typed updates
# ====================================================================
class RecentEditLog(models.Model):
    user = models.ForeignKey(
        "User", on_delete=models.CASCADE, related_name="recent_edits",
    )
    KIND_WORKOUT_SWAP    = "workout_swap"
    KIND_WORKOUT_SET     = "workout_set"
    KIND_WORKOUT_REPS    = "workout_reps"
    KIND_WORKOUT_REST    = "workout_rest"
    KIND_WORKOUT_ADD     = "workout_add"
    KIND_WORKOUT_REMOVE  = "workout_remove"
    KIND_NUTRITION_MEAL  = "nutrition_meal"
    KIND_NUTRITION_MACRO = "nutrition_macro"
    KIND_OTHER           = "other"
    KIND_CHOICES = [
        (KIND_WORKOUT_SWAP,    "Workout: swap exercise"),
        (KIND_WORKOUT_SET,     "Workout: change sets"),
        (KIND_WORKOUT_REPS,    "Workout: change reps"),
        (KIND_WORKOUT_REST,    "Workout: change rest"),
        (KIND_WORKOUT_ADD,     "Workout: add exercise"),
        (KIND_WORKOUT_REMOVE,  "Workout: remove exercise"),
        (KIND_NUTRITION_MEAL,  "Nutrition: edit meal"),
        (KIND_NUTRITION_MACRO, "Nutrition: edit macros"),
        (KIND_OTHER,           "Other"),
    ]
    kind     = models.CharField(max_length=24, choices=KIND_CHOICES)
    summary  = models.CharField(max_length=240)
    payload  = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.user_id}/{self.kind}@{self.created_at:%Y-%m-%d}"

    @classmethod
    def record(cls, user, kind: str, summary: str, payload: dict | None = None) -> "RecentEditLog":
        """Create a row + prune the oldest rows beyond the 50-row cap.

        Best-effort: swallows any DB errors so a failed log write
        never breaks the user-facing edit. The log is a coaching
        nicety, not source-of-truth.
        """
        try:
            row = cls.objects.create(
                user=user, kind=kind,
                summary=(summary or "")[:240],
                payload=payload or {},
            )
            # Prune anything past the 50-row cap.
            ids = list(
                cls.objects.filter(user=user)
                .order_by("-created_at")
                .values_list("id", flat=True)[50:]
            )
            if ids:
                cls.objects.filter(id__in=ids).delete()
            return row
        except Exception:
            return None


# --------------------------------------------------------------------
# REPORT-A-BUG (May 2026, Deen QC)
# --------------------------------------------------------------------

class BugReport(models.Model):
    """User-submitted bug reports.

    Lives as its own model rather than a free-form Resend email so we
    can triage later (most-reported flow, repro from a real user, etc.).
    Resend still fires on create for the inbox notification — but the
    DB row is the canonical record.

    The `screenshot_base64` field is a TextField rather than an
    ImageField for the same reason `User.avatar_base64` and
    `ProgressPhoto.image_base64` are TextFields: zero external storage
    infra, survives redeploys, easy to migrate to S3 later. Capped at
    ~3 MB raw (≈ 4 MB after base64) — anything bigger gets rejected at
    the endpoint with a friendly 413.
    """
    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="bug_reports",
    )
    what_happened = models.TextField()
    expected = models.TextField(blank=True, default="")

    # Auto-attached metadata, captured by iOS at submit time. Stored
    # as plain text fields (not JSON) so admin / triage SQL queries
    # stay readable — these are the keys we filter / sort by most.
    app_version  = models.CharField(max_length=32, blank=True, default="")
    app_build    = models.CharField(max_length=32, blank=True, default="")
    os_version   = models.CharField(max_length=32, blank=True, default="")
    device_model = models.CharField(max_length=64, blank=True, default="")

    # Optional in-app trail: the last ~10 user actions before submit
    # (e.g. ["opened nutrition", "tapped add food", "search 'milk'",
    # "tap Whole milk", "tap Save"]). Free-form JSON list to keep the
    # iOS side flexible about what counts as an "action".
    recent_actions = models.JSONField(default=list, blank=True)

    # Optional screenshot — base64-encoded image bytes. Empty string
    # when the user didn't attach one.
    screenshot_base64 = models.TextField(blank=True, default="")

    # Triage state — moves "open" → "resolved" / "wontfix" / "dupe" in
    # Django admin. Keeps the inbox digestible.
    STATUS_OPEN     = "open"
    STATUS_RESOLVED = "resolved"
    STATUS_WONTFIX  = "wontfix"
    STATUS_DUPE     = "dupe"
    STATUS_CHOICES = [
        (STATUS_OPEN,     "Open"),
        (STATUS_RESOLVED, "Resolved"),
        (STATUS_WONTFIX,  "Won't fix"),
        (STATUS_DUPE,     "Duplicate"),
    ]
    status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        truncated = (self.what_happened or "")[:60]
        return f"BugReport #{self.id} — {truncated}"

