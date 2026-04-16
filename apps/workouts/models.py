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


class ExerciseLibraryItem(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="exercise_library_items",
    )
    name = models.CharField(max_length=255)
    video_url = models.URLField(blank=True)
    coaching_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
