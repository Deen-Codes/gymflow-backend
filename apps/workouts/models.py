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
    workout_day = models.ForeignKey(WorkoutDay, on_delete=models.CASCADE)
    completed_at = models.DateTimeField(auto_now_add=True)
    duration = models.IntegerField(default=0)
    is_complete = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.user} - {self.workout_day}"


class ExerciseSession(models.Model):
    workout_session = models.ForeignKey(WorkoutSession, on_delete=models.CASCADE, related_name="exercise_sessions")
    exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE)


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
    SOURCE_CHOICES = [
        (SOURCE_CURATED, "Curated"),
        (SOURCE_WGER, "wger"),
    ]

    name = models.CharField(max_length=255, db_index=True)
    muscle_group = models.CharField(max_length=64, blank=True, db_index=True)
    equipment = models.CharField(max_length=64, blank=True, db_index=True)
    instructions = models.TextField(blank=True)
    video_url = models.URLField(blank=True)
    image_url = models.URLField(blank=True)

    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_CURATED)
    external_id = models.CharField(max_length=64, blank=True, db_index=True)

    is_published = models.BooleanField(default=True)
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
