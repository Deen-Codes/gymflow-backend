"""Serializers used exclusively by the Phase 1 trainer dashboard
JSON endpoints (drag-drop workout builder, exercise catalog search,
per-day exercise CRUD).

Kept in their own module so the existing `serializers.py` (consumed by
the iOS client) is not coupled to dashboard-only fields.
"""
from rest_framework import serializers

from .models import (
    Exercise,
    ExerciseCatalog,
    ExerciseLibraryItem,
    ExerciseSetTarget,
)


# ---------------------------------------------------------------
# Catalog (read-only, surfaced to the right-rail search)
# ---------------------------------------------------------------
class ExerciseCatalogSerializer(serializers.ModelSerializer):
    in_library = serializers.SerializerMethodField()

    class Meta:
        model = ExerciseCatalog
        fields = [
            "id",
            "name",
            "muscle_group",
            "equipment",
            "instructions",
            "video_url",
            "image_url",
            "source",
            "in_library",
        ]

    def get_in_library(self, obj):
        """True when the requesting trainer has already snapshotted
        this catalog item into their personal library."""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return ExerciseLibraryItem.objects.filter(
            user=request.user,
            source_catalog_item=obj,
        ).exists()


# ---------------------------------------------------------------
# Per-trainer library
# ---------------------------------------------------------------
class ExerciseLibraryItemSerializer(serializers.ModelSerializer):
    catalog_id = serializers.IntegerField(
        source="source_catalog_item_id", read_only=True
    )

    class Meta:
        model = ExerciseLibraryItem
        fields = [
            "id",
            "name",
            "video_url",
            "coaching_notes",
            "muscle_group",
            "equipment",
            "catalog_id",
            "created_at",
        ]


# ---------------------------------------------------------------
# Day exercise CRUD (drag-drop builder)
# ---------------------------------------------------------------
class ExerciseSetTargetWriteSerializer(serializers.Serializer):
    set_number = serializers.IntegerField()
    reps = serializers.CharField(max_length=20)


class DayExerciseCreateSerializer(serializers.Serializer):
    """Payload for "drop a library item onto a day" or "drop a catalog
    item onto a day" (the latter implicitly snapshots it first).

    Either `library_item_id` OR `catalog_id` must be provided.
    """
    workout_day_id = serializers.IntegerField()
    library_item_id = serializers.IntegerField(required=False)
    catalog_id = serializers.IntegerField(required=False)
    label = serializers.CharField(max_length=10, required=False, allow_blank=True)
    superset_group = serializers.IntegerField(required=False, allow_null=True)
    set_targets = ExerciseSetTargetWriteSerializer(many=True, required=False)

    def validate(self, attrs):
        if not attrs.get("library_item_id") and not attrs.get("catalog_id"):
            raise serializers.ValidationError(
                "Provide either `library_item_id` or `catalog_id`."
            )
        return attrs


class DayExerciseUpdateSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=10, required=False, allow_blank=True)
    superset_group = serializers.IntegerField(required=False, allow_null=True)
    set_targets = ExerciseSetTargetWriteSerializer(many=True, required=False)


class DayReorderSerializer(serializers.Serializer):
    """Bulk reorder of all exercises within a single day.
    `ordered_exercise_ids` is the new order, top → bottom."""
    workout_day_id = serializers.IntegerField()
    ordered_exercise_ids = serializers.ListField(
        child=serializers.IntegerField(), allow_empty=True
    )


class DayExerciseReadSerializer(serializers.ModelSerializer):
    """Compact representation echoed back after a successful drop /
    reorder so the dashboard JS can re-render without a full refetch."""
    set_targets = serializers.SerializerMethodField()

    class Meta:
        model = Exercise
        fields = [
            "id",
            "name",
            "label",
            "order",
            "superset_group",
            "set_targets",
        ]

    def get_set_targets(self, obj):
        return [
            {"set_number": s.set_number, "reps": s.reps}
            for s in obj.sets.all().order_by("set_number")
        ]
