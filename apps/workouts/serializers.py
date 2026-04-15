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

    class Meta:
        model = Exercise
        fields = [
            "id",
            "label",
            "name",
            "order",
            "superset_group",
            "set_targets",
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
