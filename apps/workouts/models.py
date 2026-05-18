from django.conf import settings
from django.db import models


class WorkoutPlan(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    # Template vs client-specific versioning
    is_template = models.BooleanField(default=True)
    source_template = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="client_versions",
    )
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="client_specific_workout_plans",
    )

    # SOLO-02 — the public programmes catalog Solo users browse and
    # self-assign. When True, this plan is visible to ALL solo users
    # under /api/solo/programmes/ regardless of `user`. Assignment
    # deep-clones the plan into a per-user instance (with the new
    # plan's `source_template` set to this row), so the catalog row
    # stays read-only and can be edited centrally without affecting
    # already-assigned users.
    #
    # `programme_meta` is a small JSON blob the catalog filter uses:
    #   {
    #     "goals":         ["build_muscle", "get_stronger"],
    #     "experience":    "one_to_three",
    #     "equipment":     "full_gym",
    #     "days_per_week": 4,
    #     "weeks":         6,
    #     "tagline":       "Push Pull Legs split",
    #     "summary":       "Classic 4-day hypertrophy programme...",
    #   }
    is_solo_template = models.BooleanField(default=False, db_index=True)
    programme_meta   = models.JSONField(blank=True, default=dict)

    # Phase 5: timestamp so the Activity feed can show plan-created events.
    created_at = models.DateTimeField(auto_now_add=True, null=True)

    def __str__(self):
        return self.name


class WorkoutDay(models.Model):
    plan = models.ForeignKey(WorkoutPlan, on_delete=models.CASCADE, related_name="days")
    title = models.CharField(max_length=100)
    order = models.IntegerField()

    def __str__(self):
        return f"{self.plan.name} - {self.title}"


class Exercise(models.Model):
    workout_day = models.ForeignKey(WorkoutDay, on_delete=models.CASCADE, related_name="exercises")
    name = models.CharField(max_length=255)
    label = models.CharField(max_length=10)
    order = models.IntegerField()
    superset_group = models.IntegerField(null=True, blank=True)

    # REST-ASSIGNABLE — per-exercise rest in seconds. Drives the
    # rest-timer banner in the active workout. Default 90s matches
    # what the active workout used as a hardcoded fallback before
    # this field existed. Trainers set it via the existing edit-
    # programme UI; AI PT can mutate it via the change_set_scheme
    # tool (extended to accept rest_seconds in the payload).
    rest_seconds = models.PositiveSmallIntegerField(default=90)

    # Phase 5+ — link back to the global ExerciseCatalog so the
    # iOS workout view can surface the catalog's image_url +
    # animation_url + instructions on this row. Nullable + on_delete
    # SET_NULL so a catalog deletion doesn't cascade-kill plans;
    # blank=True so AI-generated / custom rows that don't match a
    # catalog entry stay valid. Population:
    #   • Phase A AI-build view sets it when a catalog match exists.
    #   • Migration 0007 backfills existing rows by name match.
    #   • Trainer-built plans set it when a library item is added.
    catalog_item = models.ForeignKey(
        "ExerciseCatalog",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="workout_exercises",
    )

    # T1.9 / EDIT-PROVENANCE-TRACKING — where did this exercise row
    # originate? Used by the AI PT context surface so weekly review +
    # chat can comment on user-made edits intelligently ("I see you
    # swapped Bench for Incline DB Press 4 days ago — any reason?").
    # `ai_generated`: created by Phase A AI build / mutation.
    # `template`:     cloned from a programme catalog template.
    # `user_edit`:    last touched by a user-side edit (custom builder
    #                 or in-place edit on assigned programme).
    PROVENANCE_AI       = "ai_generated"
    PROVENANCE_TEMPLATE = "template"
    PROVENANCE_USER     = "user_edit"
    PROVENANCE_CHOICES = [
        (PROVENANCE_AI,       "AI generated"),
        (PROVENANCE_TEMPLATE, "Template"),
        (PROVENANCE_USER,     "User edit"),
    ]
    provenance = models.CharField(
        max_length=16,
        choices=PROVENANCE_CHOICES,
        default=PROVENANCE_TEMPLATE,
        blank=True,
    )

    def __str__(self):
        return self.name


class ExerciseSetTarget(models.Model):
    exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE, related_name="sets")
    set_number = models.IntegerField()
    reps = models.CharField(max_length=20)

    def __str__(self):
        return f"{self.exercise.name} - Set {self.set_number}"


class WorkoutSession(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    # V0-LIMIT-3 — nullable for ad-hoc (plan-less) sessions logged
    # via the iOS as-you-go flow. SET_NULL preserves the session
    # record if the source WorkoutDay is later deleted.
    workout_day = models.ForeignKey(
        WorkoutDay,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    # V0-LIMIT-3 — display title for ad-hoc sessions. Plan-mode
    # sessions can leave this blank; the workout_day.title is the
    # source of truth for those.
    title = models.CharField(max_length=255, blank=True, default="")
    completed_at = models.DateTimeField(auto_now_add=True)
    duration = models.IntegerField(default=0)
    is_complete = models.BooleanField(default=True)
    # Optional free-text "anything else?" note written from the
    # post-cinematic prompt. Surfaces back to the AI PT in
    # _build_user_context as "Last session note: ...". Defaults
    # to empty so older clients keep working.
    notes = models.TextField(blank=True, default="")

    # R7-2 (#59) — post-session feedback pills.
    #
    # rpe: 1–10 Rate of Perceived Exertion (Borg CR-10). Optional; if
    #   present, feeds into the AI PT context for future programming
    #   ("they reported RPE 9 on Wednesday, ease Friday's volume").
    #
    # mood: short categorical label for "how did it feel" — calmer
    #   than the numeric RPE. Free-form CharField rather than choices
    #   so the iOS pill set can evolve without a schema change.
    #   Values today: "good" | "fine" | "off" | "tough" — but
    #   anything ≤16 chars is accepted server-side.
    rpe = models.SmallIntegerField(null=True, blank=True)
    mood = models.CharField(max_length=16, blank=True, default="")

    def __str__(self):
        return f"{self.user} - {self.workout_day}"


class ExerciseSession(models.Model):
    workout_session = models.ForeignKey(WorkoutSession, on_delete=models.CASCADE, related_name="exercise_sessions")
    # V0-LIMIT-3 — nullable for ad-hoc lifts that aren't tied to a
    # planned Exercise row. SET_NULL preserves the historical
    # record if the source Exercise is later deleted.
    exercise = models.ForeignKey(
        Exercise,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    # V0-LIMIT-3 — display name captured at log-time so the
    # historical row survives an Exercise rename / delete. Empty
    # for plan-mode rows where exercise.name is the source.
    name = models.CharField(max_length=255, blank=True, default="")
    # V0-LIMIT-3 — optional FK to ExerciseCatalog when the ad-hoc
    # lift was picked from the catalog picker. Lets the historical
    # record link back to animation_url + form copy.
    catalog = models.ForeignKey(
        "ExerciseCatalog",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exercise_sessions",
    )


class SetPerformance(models.Model):
    exercise_session = models.ForeignKey(ExerciseSession, on_delete=models.CASCADE, related_name="sets")
    set_number = models.IntegerField()
    weight = models.CharField(max_length=20, blank=True)
    reps = models.CharField(max_length=20, blank=True)


# -------------------------------------------------------------------
# Phase 1 — Global ExerciseCatalog
#
# A read-mostly catalog populated by:
#   * `seed_exercises` (curated, ~40 entries — the obvious ones)
#   * `import_wger_exercises` (bulk import from the wger public API)
#
# Trainers don't edit these directly. When a trainer drops a catalog
# entry into their library or onto a workout day, we copy it into a
# per-trainer ExerciseLibraryItem (snapshot) so:
#   * future catalog edits don't silently change a published plan
#   * each trainer can override coaching_notes / video_url
#
# `external_id` lets us de-dupe wger imports across re-runs.
# -------------------------------------------------------------------
class ExerciseCatalog(models.Model):
    SOURCE_CURATED = "curated"
    SOURCE_WGER = "wger"
    # EXERCISE-DB (per #105 sibling) — owned multi-source exercise
    # catalog. Free Exercise DB (yuhonas) is public domain, ~800
    # exercises with instructions + images we can re-derive into
    # our own animated pose stills. Afletics source is for our own
    # additions / curated overrides. wger is intentionally NOT
    # ingested at runtime — its data is AGPL (viral copyleft) and
    # would force the catalog DB itself under AGPL too.
    SOURCE_FREE_EXERCISE_DB = "free_exercise_db"
    SOURCE_AFLETICS = "afletics"
    SOURCE_CHOICES = [
        (SOURCE_CURATED, "Curated"),
        (SOURCE_WGER, "wger"),
        (SOURCE_FREE_EXERCISE_DB, "Free Exercise DB"),
        (SOURCE_AFLETICS, "Afletics curated"),
    ]

    # === EXERCISE-LIB-1500 (#210) ============================
    # Levels / mechanic / force / category mirror Free Exercise DB
    # taxonomy so we can ingest its data losslessly. Filters in
    # iOS use these for chips: equipment ("at-home"/"barbell"/etc),
    # primary-muscle, level (beginner→advanced), category.
    LEVEL_BEGINNER = "beginner"
    LEVEL_INTERMEDIATE = "intermediate"
    LEVEL_EXPERT = "expert"
    LEVEL_CHOICES = [
        (LEVEL_BEGINNER, "Beginner"),
        (LEVEL_INTERMEDIATE, "Intermediate"),
        (LEVEL_EXPERT, "Expert / Advanced"),
    ]
    MECHANIC_COMPOUND = "compound"
    MECHANIC_ISOLATION = "isolation"
    MECHANIC_CHOICES = [
        (MECHANIC_COMPOUND, "Compound"),
        (MECHANIC_ISOLATION, "Isolation"),
    ]
    FORCE_PUSH = "push"
    FORCE_PULL = "pull"
    FORCE_STATIC = "static"
    FORCE_CHOICES = [
        (FORCE_PUSH, "Push"),
        (FORCE_PULL, "Pull"),
        (FORCE_STATIC, "Static"),
    ]
    CATEGORY_STRENGTH = "strength"
    CATEGORY_STRETCHING = "stretching"
    CATEGORY_PLYOMETRICS = "plyometrics"
    CATEGORY_POWERLIFTING = "powerlifting"
    CATEGORY_CARDIO = "cardio"
    CATEGORY_OLYMPIC = "olympic_weightlifting"
    CATEGORY_STRONGMAN = "strongman"
    CATEGORY_CHOICES = [
        (CATEGORY_STRENGTH, "Strength"),
        (CATEGORY_STRETCHING, "Stretching / Mobility"),
        (CATEGORY_PLYOMETRICS, "Plyometrics"),
        (CATEGORY_POWERLIFTING, "Powerlifting"),
        (CATEGORY_CARDIO, "Cardio"),
        (CATEGORY_OLYMPIC, "Olympic Weightlifting"),
        (CATEGORY_STRONGMAN, "Strongman"),
    ]

    name = models.CharField(max_length=255, db_index=True)
    muscle_group = models.CharField(max_length=64, blank=True, db_index=True)
    # Comma-separated secondary muscles (e.g. "triceps,shoulders")
    # — kept as text instead of JSONField so SQLite/Postgres
    # behave the same and __icontains filters work for the AI PT.
    secondary_muscles = models.CharField(max_length=255, blank=True)
    equipment = models.CharField(max_length=64, blank=True, db_index=True)

    level = models.CharField(
        max_length=16, blank=True, db_index=True,
        choices=LEVEL_CHOICES,
    )
    mechanic = models.CharField(
        max_length=16, blank=True, db_index=True,
        choices=MECHANIC_CHOICES,
    )
    force = models.CharField(
        max_length=16, blank=True, db_index=True,
        choices=FORCE_CHOICES,
    )
    category = models.CharField(
        max_length=24, blank=True, db_index=True,
        choices=CATEGORY_CHOICES,
    )

    # Free Exercise DB ships terse "instructions" (numbered steps).
    # We surface that as the basic execution. The richer fields
    # below are written by the curation/AI pass and shown in the
    # enlarged exercise view per Deen's spec:
    #   form_description   — paragraph on proper setup, key cues
    #   common_mistakes    — "Don't…" list (newline-delimited)
    #   breathing_cues     — when to inhale/exhale
    #   primary_benefit    — short "why this exercise is worth doing"
    #                        paragraph. Surfaced in the enlarged
    #                        exercise view as a "Why this lift?"
    #                        section so the user understands what
    #                        the movement actually develops (and
    #                        why their plan picked it).
    instructions = models.TextField(blank=True)
    form_description = models.TextField(blank=True)
    common_mistakes = models.TextField(blank=True)
    breathing_cues = models.TextField(blank=True)
    primary_benefit = models.TextField(blank=True)

    video_url = models.URLField(blank=True)
    image_url = models.URLField(blank=True)
    # Phase 5+ — cinematic animation URL. Populated when the
    # commissioned animation library lands (Lottie .json / .lottie
    # preferred; .mp4 fallback). The iOS `ExerciseAnimationView`
    # component picks the renderer by URL extension. Empty for
    # uncommissioned exercises — the view falls back to image_url
    # then to an SF symbol placeholder. See
    # `EXERCISE_ANIMATION_LIBRARY.md` (iOS repo) for sourcing
    # strategy + brand spec.
    animation_url = models.URLField(blank=True)

    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_CURATED)
    external_id = models.CharField(max_length=64, blank=True, db_index=True)

    is_published = models.BooleanField(default=True)

    # DEEN-PLAN — Icon production priority. 0 = default queue. Higher
    # values bubble to the top of the EXERCISE-ICONS commission queue
    # (#237). The first batch of 30 lifts come from Deen's own PT-built
    # plan so the founder dogfood loop is fully visual end-to-end before
    # we expand to the long tail of the 1,500-row catalog.
    icon_priority = models.PositiveSmallIntegerField(default=0, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "external_id"],
                condition=models.Q(external_id__gt=""),
                name="unique_catalog_source_external_id",
            ),
        ]

    def __str__(self):
        return self.name


class ExerciseLibraryItem(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="exercise_library_items",
    )
    name = models.CharField(max_length=255)
    video_url = models.URLField(blank=True)
    coaching_notes = models.TextField(blank=True)

    # Phase 1: copy-on-add provenance — null for items the trainer
    # created from scratch in their library, set when the item was
    # snapshotted from the global ExerciseCatalog.
    source_catalog_item = models.ForeignKey(
        ExerciseCatalog,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="library_snapshots",
    )
    muscle_group = models.CharField(max_length=64, blank=True)
    equipment = models.CharField(max_length=64, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
