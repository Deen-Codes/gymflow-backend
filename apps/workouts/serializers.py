from rest_framework import serializers
from .models import (
    WorkoutPlan,
    WorkoutDay,
    Exercise,
    ExerciseSetTarget,
    WorkoutSession,
    ExerciseSession,
    SetPerformance,
)


class ExerciseSetTargetSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExerciseSetTarget
        fields = ["set_number", "reps"]


class ExerciseSerializer(serializers.ModelSerializer):
    set_targets = ExerciseSetTargetSerializer(source="sets", many=True, read_only=True)

    # EXERCISE-FOUNDATION — surface catalog details when the row is
    # linked. iOS uses these to render the form-demo on the
    # exercise card. All four fall back to empty strings when the
    # row isn't catalog-linked (custom / AI-generated exercises);
    # the iOS view degrades to an SF symbol in that case.
    image_url     = serializers.SerializerMethodField()
    animation_url = serializers.SerializerMethodField()
    instructions  = serializers.SerializerMethodField()
    muscle_group  = serializers.SerializerMethodField()
    catalog_id    = serializers.IntegerField(source="catalog_item_id", read_only=True)

    def _catalog(self, obj):
        return getattr(obj, "catalog_item", None)

    def get_image_url(self, obj):
        cat = self._catalog(obj)
        return (cat.image_url if cat else "") or ""

    def get_animation_url(self, obj):
        cat = self._catalog(obj)
        return (cat.animation_url if cat else "") or ""

    def get_instructions(self, obj):
        cat = self._catalog(obj)
        return (cat.instructions if cat else "") or ""

    def get_muscle_group(self, obj):
        cat = self._catalog(obj)
        return (cat.muscle_group if cat else "") or ""

    class Meta:
        model = Exercise
        fields = [
            "id",
            "label",
            "name",
            "order",
            "superset_group",
            "set_targets",
            "catalog_id",
            "image_url",
            "animation_url",
            "instructions",
            "muscle_group",
            # REST-ASSIGNABLE — per-exercise rest in seconds.
            "rest_seconds",
        ]


class WorkoutDaySerializer(serializers.ModelSerializer):
    exercises = ExerciseSerializer(many=True, read_only=True)

    class Meta:
        model = WorkoutDay
        fields = [
            "id",
            "title",
            "order",
            "exercises",
        ]


class WorkoutPlanSerializer(serializers.ModelSerializer):
    days = WorkoutDaySerializer(many=True, read_only=True)

    class Meta:
        model = WorkoutPlan
        fields = [
            "id",
            "name",
            "is_active",
            "days",
        ]


class SetPerformanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = SetPerformance
        fields = ["set_number", "weight", "reps"]


class ExerciseSessionSerializer(serializers.ModelSerializer):
    exercise_id = serializers.IntegerField(source="exercise.id", read_only=True)
    exercise_name = serializers.CharField(source="exercise.name", read_only=True)
    sets = SetPerformanceSerializer(many=True, read_only=True)

    class Meta:
        model = ExerciseSession
        fields = [
            "exercise_id",
            "exercise_name",
            "sets",
        ]


class WorkoutSessionSerializer(serializers.ModelSerializer):
    exercise_sessions = ExerciseSessionSerializer(many=True, read_only=True)

    class Meta:
        model = WorkoutSession
        fields = [
            "id",
            "workout_day",
            "completed_at",
            "duration",
            "exercise_sessions",
        ]


# ---------------------------
# Write serializers
# ---------------------------

class SetPerformanceInputSerializer(serializers.Serializer):
    set_number = serializers.IntegerField()
    weight = serializers.CharField(required=False, allow_blank=True)
    reps = serializers.CharField(required=False, allow_blank=True)


class ExerciseSessionInputSerializer(serializers.Serializer):
    exercise_id = serializers.IntegerField()
    sets = SetPerformanceInputSerializer(many=True)


class WorkoutSessionCreateSerializer(serializers.Serializer):
    workout_day_id = serializers.IntegerField()
    duration = serializers.IntegerField(default=0)
    is_complete = serializers.BooleanField(default=True)
    exercises = ExerciseSessionInputSerializer(many=True)
