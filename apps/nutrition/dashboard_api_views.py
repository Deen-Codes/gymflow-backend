"""Phase 3 — nutrition dashboard JSON endpoints.

Powers the trainer's drag-drop meal builder + food picker.

**Internal-only food catalog.** The dashboard food search queries the
`CuratedFood` table (NUTRITION-DB #105) — our 200+ hand-curated whole
foods, UK supermarket items and restaurant chains. No Open Food Facts,
no USDA live API, no third-party calls.

If a trainer can't find a food in the catalog, they can either
(a) create a custom library item via `/library/custom/`, or
(b) ask the AI describe path on the client side to estimate macros.

Auth: trainer with role==TRAINER and a related trainer_profile.
Catalog reads come from CuratedFood; writes are scoped to the calling
trainer's own data.
"""
import logging

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import User

from .models import (
    CuratedFood,
    FoodLibraryItem,
    NutritionMeal,
    NutritionMealItem,
)
from .dashboard_serializers import (
    FoodLibraryItemSerializer,
    MealItemCreateSerializer,
    MealItemReadSerializer,
    MealItemUpdateSerializer,
    MealReorderSerializer,
)

log = logging.getLogger(__name__)


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def _require_trainer(request):
    user = request.user
    if user.role != User.TRAINER or not hasattr(user, "trainer_profile"):
        return None, Response(
            {"detail": "Only trainers can use the dashboard API."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return user, None


def _trainer_owns_meal(trainer, meal):
    return meal.nutrition_plan.user_id == trainer.id


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _scale_macros(library_item, grams):
    """Scale a library item's per-reference macros to the requested
    grams-equivalent amount. The reference is stored on the library
    item (default 100 g; for unit-portion items it's the named unit)."""
    ref = library_item.reference_grams or 100.0
    factor = (grams or 0.0) / ref if ref else 0.0
    return {
        "calories": (library_item.calories or 0) * factor,
        "protein":  (library_item.protein  or 0) * factor,
        "carbs":    (library_item.carbs    or 0) * factor,
        "fats":     (library_item.fats     or 0) * factor,
    }


def _curated_to_row(food):
    """Render a CuratedFood as a dashboard food-picker row.

    The shape mirrors what the trainer dashboard frontend already
    consumes — keeps the React/Vue components untouched while we
    swap the data source.

    `external_id` becomes `curated:<pk>` so meal-item snapshots
    can dedupe per-trainer the same way they used to with OFF
    barcodes.
    """
    return {
        "external_id":     f"curated:{food.id}",
        "name":            food.name,
        "brand":           food.brand or "",
        "reference_grams": 100.0,
        "calories":        round(food.kcal_per_100g,    1),
        "protein":         round(food.protein_per_100g, 1),
        "carbs":           round(food.carbs_per_100g,   1),
        "fats":            round(food.fat_per_100g,     1),
        "serving_grams":   food.serving_grams,
        "serving_label":   food.serving_label or "",
        # FOOD-DB-V2 — portion units. Trainer dashboard surfaces
        # the same unit affordance as iOS so a "1 egg" selection
        # makes it through end-to-end.
        "portion_unit":    food.portion_unit or "grams",
        "unit_grams":      food.unit_grams,
    }


def _snapshot_food_into_library(trainer, payload):
    """Idempotently copy a search-result row into the trainer's
    private FoodLibraryItem table.

    Trainers can edit / rename library items without affecting the
    canonical CuratedFood entry — the snapshot model gives them a
    private workspace. Re-search of the same food returns the existing
    row so we don't grow the library on every drag.

    Replaces the old OFF-snapshot path (`source="off"`); new snapshots
    use `source="gymflow"`. Legacy `source="off"` rows in the database
    keep working — `external_id` lookups still resolve them.
    """
    external_id = (payload.get("external_id") or "").strip()
    if external_id:
        existing = (
            FoodLibraryItem.objects
            .filter(user=trainer, external_id=external_id)
            .first()
        )
        if existing:
            return existing

    return FoodLibraryItem.objects.create(
        user=trainer,
        name=str(payload.get("name") or "")[:255],
        brand=str(payload.get("brand") or "")[:255],
        reference_grams=_safe_float(payload.get("reference_grams"), 100.0) or 100.0,
        calories=_safe_float(payload.get("calories")),
        protein=_safe_float(payload.get("protein")),
        carbs=_safe_float(payload.get("carbs")),
        fats=_safe_float(payload.get("fats")),
        portion_type=FoodLibraryItem.PORTION_GRAMS,
        unit_label="",
        source=FoodLibraryItem.SOURCE_GYMFLOW,
        external_id=external_id,
    )


def _annotate_in_library(trainer, items):
    """Tag each row with `in_library=True` if this trainer has already
    snapshotted that food. Match is by `external_id` so the same
    `curated:<id>` resolves on re-search.
    """
    if not items:
        return []
    external_ids = [it["external_id"] for it in items if it.get("external_id")]
    in_lib = set()
    if external_ids:
        in_lib = set(
            FoodLibraryItem.objects.filter(
                user=trainer,
                external_id__in=external_ids,
            ).values_list("external_id", flat=True)
        )
    out = []
    for it in items:
        copy = dict(it)
        copy["in_library"] = it.get("in_library") or (it.get("external_id") in in_lib)
        out.append(copy)
    return out


# -------------------------------------------------------------------
# Food search — internal CuratedFood query
# -------------------------------------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def food_search(request):
    """GET /api/nutrition/dashboard/catalog/?q=apple&region=gb

    Search the internal CuratedFood catalog. Returns up to 20 rows
    in the dashboard food-picker shape.

    Empty query → trainer's recent library items so the picker
    always has something to scroll through.

    Response shape: `{results: [...], source: "library"|"catalog"}`
    """
    trainer, err = _require_trainer(request)
    if err:
        return err

    q = (request.query_params.get("q") or "").strip()

    # Empty search → recent library items, like a "recently used" tray.
    if not q:
        recent = list(
            FoodLibraryItem.objects.filter(user=trainer)
            .order_by("-created_at")[:20]
        )
        rows = [
            {
                "external_id":     f.external_id,
                "name":            f.name,
                "brand":           f.brand,
                "reference_grams": f.reference_grams,
                "calories":        f.calories,
                "protein":         f.protein,
                "carbs":           f.carbs,
                "fats":            f.fats,
                "in_library":      True,
                "library_id":      f.id,
                "portion_type":    f.portion_type,
                "unit_label":      f.unit_label,
            }
            for f in recent
        ]
        return Response({"results": rows, "source": "library"})

    if len(q) > 80:
        q = q[:80]

    region = (request.query_params.get("region") or "").strip().lower()

    # Tiered ranking — same shape as the Solo search, so trainers and
    # clients see the same results for the same query.
    base = CuratedFood.objects.all()
    exact      = list(base.filter(name__iexact=q)[:20])
    starts     = list(base.filter(name__istartswith=q).exclude(name__iexact=q)[:20])
    contains   = list(base.filter(name__icontains=q).exclude(name__istartswith=q)[:20])
    brand_hits = list(base.filter(brand__icontains=q).exclude(name__icontains=q)[:20])
    tag_hits   = list(base.filter(tags__icontains=q)
                          .exclude(name__icontains=q)
                          .exclude(brand__icontains=q)[:20])

    ordered = []
    seen = set()
    for bucket in (exact, starts, contains, brand_hits, tag_hits):
        for f in bucket:
            if f.id in seen:
                continue
            seen.add(f.id)
            ordered.append(f)

    # Region prioritisation — if the trainer passed a locale region,
    # bubble matching items above world-wide ones. Items NOT tagged
    # for that region still appear, just lower.
    if region:
        in_region = [f for f in ordered if region in (f.region_codes or "").lower().split(",")]
        rest      = [f for f in ordered if f not in in_region]
        ordered   = in_region + rest

    rows = [_curated_to_row(f) for f in ordered[:20]]
    rows = _annotate_in_library(trainer, rows)
    return Response({"results": rows, "source": "catalog"})


# -------------------------------------------------------------------
# Per-trainer food library
# -------------------------------------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def library_list(request):
    """GET /api/nutrition/dashboard/library/?q="""
    trainer, err = _require_trainer(request)
    if err:
        return err

    q = (request.query_params.get("q") or "").strip()
    qs = FoodLibraryItem.objects.filter(user=trainer)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(brand__icontains=q))
    qs = qs.order_by("name")
    return Response({"results": FoodLibraryItemSerializer(qs, many=True).data})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def library_create_custom(request):
    """POST /api/nutrition/dashboard/library/custom/

    Create a custom (`source=custom`) food in the trainer's library.
    Drives the inline "+ Create custom food" form on the dashboard
    food picker. Body shape:

        {
            "name":            "Olive oil",                 (required)
            "portion_type":    "tbsp",                       (one of PORTION_CHOICES)
            "reference_grams": 1,                            (default 100 for grams, 1 otherwise)
            "unit_label":      "egg",                        (only for portion_type=unit)
            "calories":        120,
            "protein":         0,
            "carbs":           0,
            "fats":            14
        }

    Returns the created `FoodLibraryItem` shaped exactly like
    `library_list` results so the frontend can drop it straight into
    the picker without a separate refetch.
    """
    trainer, err = _require_trainer(request)
    if err:
        return err

    body = request.data or {}
    name = (body.get("name") or "").strip()
    if not name:
        return Response({"detail": "Name is required."}, status=status.HTTP_400_BAD_REQUEST)

    valid_types = {choice[0] for choice in FoodLibraryItem.PORTION_CHOICES}
    portion_type = body.get("portion_type") or FoodLibraryItem.PORTION_GRAMS
    if portion_type not in valid_types:
        return Response(
            {"detail": f"portion_type must be one of {sorted(valid_types)}."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Sensible defaults: gram foods reference 100g (industry standard
    # for nutrition labelling), everything else references 1 of the
    # named unit. Frontend can override via the form.
    default_reference = 100.0 if portion_type == FoodLibraryItem.PORTION_GRAMS else 1.0
    try:
        reference = float(body.get("reference_grams") or default_reference)
    except (TypeError, ValueError):
        reference = default_reference

    item = FoodLibraryItem.objects.create(
        user=trainer,
        name=name[:255],
        reference_grams=max(0.001, reference),    # avoid div-by-zero in scaling
        calories=_safe_float(body.get("calories")),
        protein=_safe_float(body.get("protein")),
        carbs=_safe_float(body.get("carbs")),
        fats=_safe_float(body.get("fats")),
        portion_type=portion_type,
        unit_label=(body.get("unit_label") or "").strip()[:40],
        source=FoodLibraryItem.SOURCE_CUSTOM,
        external_id="",
        brand="",
    )

    return Response(
        FoodLibraryItemSerializer(item).data,
        status=status.HTTP_201_CREATED,
    )


# -------------------------------------------------------------------
# Meal-item CRUD (drag-drop builder)
# -------------------------------------------------------------------
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def meal_item_add(request):
    """POST /api/nutrition/dashboard/meal-items/

    Body: either {meal_id, library_item_id, grams}
    OR {meal_id, external_id, name, brand?, reference_grams?, calories?,
        protein?, carbs?, fats?, grams}

    The catalog path implicitly snapshots the food into the trainer's
    library before creating the meal item. Re-dropping the same
    `external_id` reuses the existing snapshot so the library doesn't
    grow on every drag.
    """
    trainer, err = _require_trainer(request)
    if err:
        return err

    serializer = MealItemCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    payload = serializer.validated_data

    meal = get_object_or_404(NutritionMeal, pk=payload["meal_id"])
    if not _trainer_owns_meal(trainer, meal):
        return Response({"detail": "Not your plan."}, status=status.HTTP_403_FORBIDDEN)

    grams = float(payload["grams"])

    if payload.get("library_item_id"):
        library_item = get_object_or_404(
            FoodLibraryItem, pk=payload["library_item_id"], user=trainer
        )
    else:
        library_item = _snapshot_food_into_library(trainer, {
            "external_id":     payload.get("external_id", ""),
            "name":            payload["name"],
            "brand":           payload.get("brand", ""),
            "reference_grams": payload.get("reference_grams", 100.0) or 100.0,
            "calories":        payload.get("calories", 0.0) or 0.0,
            "protein":         payload.get("protein", 0.0) or 0.0,
            "carbs":           payload.get("carbs", 0.0) or 0.0,
            "fats":            payload.get("fats", 0.0) or 0.0,
        })

    macros = _scale_macros(library_item, grams)

    with transaction.atomic():
        order = meal.items.count()
        item = NutritionMealItem.objects.create(
            meal=meal,
            food_library_item=library_item,
            food_name=library_item.name,
            reference_grams=library_item.reference_grams or 100.0,
            grams=grams,
            calories=macros["calories"],
            protein=macros["protein"],
            carbs=macros["carbs"],
            fats=macros["fats"],
            order=order,
        )

    return Response(MealItemReadSerializer(item).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def meal_item_update(request, item_id):
    """PATCH /api/nutrition/dashboard/meal-items/<id>/  body: {grams}"""
    trainer, err = _require_trainer(request)
    if err:
        return err

    item = get_object_or_404(NutritionMealItem, pk=item_id)
    if not _trainer_owns_meal(trainer, item.meal):
        return Response({"detail": "Not your plan."}, status=status.HTTP_403_FORBIDDEN)

    serializer = MealItemUpdateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    grams = float(serializer.validated_data["grams"])

    # Recompute macros from the snapshot's reference_grams
    ref = item.reference_grams or 100.0
    factor = grams / ref if ref else 0

    # If the original library item still exists, prefer its current
    # macros (handles edits to the library after the drop). Otherwise
    # scale the existing snapshot proportionally.
    src = item.food_library_item
    if src is not None:
        item.calories = (src.calories or 0) * factor * (src.reference_grams or 100.0) / ref
        item.protein  = (src.protein  or 0) * factor * (src.reference_grams or 100.0) / ref
        item.carbs    = (src.carbs    or 0) * factor * (src.reference_grams or 100.0) / ref
        item.fats     = (src.fats     or 0) * factor * (src.reference_grams or 100.0) / ref
    else:
        # Proportional rescale: new_macro = old_macro * (new_grams / old_grams)
        old_grams = item.grams or ref
        scale = grams / old_grams if old_grams else 0
        item.calories = (item.calories or 0) * scale
        item.protein  = (item.protein  or 0) * scale
        item.carbs    = (item.carbs    or 0) * scale
        item.fats     = (item.fats     or 0) * scale

    item.grams = grams
    item.save()

    return Response(MealItemReadSerializer(item).data)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def meal_item_delete(request, item_id):
    """DELETE /api/nutrition/dashboard/meal-items/<id>/"""
    trainer, err = _require_trainer(request)
    if err:
        return err

    item = get_object_or_404(NutritionMealItem, pk=item_id)
    if not _trainer_owns_meal(trainer, item.meal):
        return Response({"detail": "Not your plan."}, status=status.HTTP_403_FORBIDDEN)

    meal = item.meal
    with transaction.atomic():
        item.delete()
        for index, remaining in enumerate(meal.items.order_by("order")):
            if remaining.order != index:
                remaining.order = index
                remaining.save(update_fields=["order"])

    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def meal_item_reorder(request):
    """POST /api/nutrition/dashboard/meal-items/reorder/

    Body: {meal_id, ordered_item_ids: [...]}
    """
    trainer, err = _require_trainer(request)
    if err:
        return err

    serializer = MealReorderSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    payload = serializer.validated_data

    meal = get_object_or_404(NutritionMeal, pk=payload["meal_id"])
    if not _trainer_owns_meal(trainer, meal):
        return Response({"detail": "Not your plan."}, status=status.HTTP_403_FORBIDDEN)

    ids = payload["ordered_item_ids"]
    existing = list(meal.items.values_list("id", flat=True))
    if set(ids) != set(existing):
        return Response(
            {"detail": "ordered_item_ids must contain exactly the meal's items."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        for index, item_id in enumerate(ids):
            NutritionMealItem.objects.filter(pk=item_id).update(order=index)

    refreshed = meal.items.order_by("order")
    return Response({"results": MealItemReadSerializer(refreshed, many=True).data})
