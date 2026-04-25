"""Serializers used exclusively by the Phase 3 nutrition dashboard
JSON endpoints (drag-drop meal builder, food catalog search via
Open Food Facts, per-meal item CRUD).
"""
from rest_framework import serializers

from .models import FoodLibraryItem, NutritionMealItem


class FoodLibraryItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = FoodLibraryItem
        fields = [
            "id",
            "name",
            "brand",
            "reference_grams",
            "calories",
            "protein",
            "carbs",
            "fats",
            "source",
            "external_id",
            "created_at",
        ]


class FoodCatalogResultSerializer(serializers.Serializer):
    """Normalized OFF search result. Not backed by a model — populated
    on the fly by `food_search` from the live OFF response."""
    external_id = serializers.CharField()
    name = serializers.CharField()
    brand = serializers.CharField(allow_blank=True, required=False, default="")
    reference_grams = serializers.FloatField(default=100)
    calories = serializers.FloatField(default=0)
    protein = serializers.FloatField(default=0)
    carbs = serializers.FloatField(default=0)
    fats = serializers.FloatField(default=0)
    in_library = serializers.BooleanField(default=False)


class MealItemCreateSerializer(serializers.Serializer):
    """Drop a food onto a meal. Either `library_item_id` (existing
    library entry) OR a full OFF payload that we'll snapshot into the
    library first."""
    meal_id = serializers.IntegerField()
    grams = serializers.FloatField(min_value=0.01, default=100)

    # Path A: drop from library
    library_item_id = serializers.IntegerField(required=False)

    # Path B: drop from Open Food Facts catalog
    external_id = serializers.CharField(required=False, allow_blank=True)
    name = serializers.CharField(required=False, allow_blank=True)
    brand = serializers.CharField(required=False, allow_blank=True, default="")
    reference_grams = serializers.FloatField(required=False, default=100)
    calories = serializers.FloatField(required=False, default=0)
    protein = serializers.FloatField(required=False, default=0)
    carbs = serializers.FloatField(required=False, default=0)
    fats = serializers.FloatField(required=False, default=0)

    def validate(self, attrs):
        if not attrs.get("library_item_id") and not (attrs.get("external_id") and attrs.get("name")):
            raise serializers.ValidationError(
                "Provide either library_item_id, or external_id + name (OFF snapshot)."
            )
        return attrs


class MealItemUpdateSerializer(serializers.Serializer):
    grams = serializers.FloatField(min_value=0.01)


class MealReorderSerializer(serializers.Serializer):
    meal_id = serializers.IntegerField()
    ordered_item_ids = serializers.ListField(
        child=serializers.IntegerField(), allow_empty=True
    )


class MealItemReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = NutritionMealItem
        fields = [
            "id",
            "food_name",
            "reference_grams",
            "grams",
            "calories",
            "protein",
            "carbs",
            "fats",
            "order",
        ]
