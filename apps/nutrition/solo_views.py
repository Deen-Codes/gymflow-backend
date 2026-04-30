"""
N.1.1 — Solo nutrition endpoints.

Five endpoints:

  • GET    /api/nutrition/solo/today/?date=YYYY-MM-DD
        Daily totals + targets + logged entries. Default date = today.
        Powers the Solo Nutrition tab (replaces the trainer-meal-plan
        flow `nutrition_today_for_me` for SOLO accounts).

  • POST   /api/nutrition/solo/log/
        Body: {food_id?, name?, portion?, calories?, protein?,
               carbs?, fats?, consumed_on?}
        Either: pass `food_id` to log a known FoodLibraryItem
                (snapshot copies its macros at the chosen portion);
        Or:     pass freeform name + macros for an ad-hoc one-off.

  • DELETE /api/nutrition/solo/log/<entry_id>/
        Untick a logged entry.

  • GET    /api/nutrition/solo/barcode/<code>/
        Lookup a barcode against Open Food Facts. Returns macros for
        a 100g reference. Doesn't auto-add to the user's library —
        the iOS client decides whether to log it directly or save it
        first.

  • POST   /api/nutrition/solo/foods/
        Create a custom FoodLibraryItem (typed in by the user). The
        existing trainer-side endpoint requires a different role; we
        expose a Solo-friendly mirror.

Macro targets live on SoloProfile and are surfaced inside the
`today` payload (no separate endpoint needed for v1).
"""
import logging

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import User, SoloProfile

from .models import SoloFoodLogEntry, FoodLibraryItem

log = logging.getLogger(__name__)


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
def _parse_date(raw):
    if not raw:
        return timezone.localdate()
    from datetime import datetime
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return timezone.localdate()


def _entry_payload(entry: SoloFoodLogEntry) -> dict:
    return {
        "id":          entry.id,
        "name":        entry.name,
        "portion":     entry.portion,
        "calories":    round(entry.calories, 1),
        "protein":     round(entry.protein, 1),
        "carbs":       round(entry.carbs, 1),
        "fats":        round(entry.fats, 1),
        "consumed_on": entry.consumed_on.isoformat(),
        "food_id":     entry.food_id,
    }


def _ensure_solo(user) -> SoloProfile | None:
    """Get-or-create + ensure macro targets exist. Returns None for
    non-solo callers."""
    if user.role != User.SOLO:
        return None
    profile, created = SoloProfile.objects.get_or_create(user=user)
    if created or profile.target_calories == 0:
        profile.compute_default_macro_targets(save=True)
    return profile


# --------------------------------------------------------------------
# R5-2 — Macro target update (lets the iOS first-time setup flow
# write user-chosen targets without going through the full
# `solo_onboarding_update_view` — that one expects all the profile
# fields and we only want to touch macros here).
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_macro_targets_update(request):
    """Body: {calories, protein, carbs, fats} — all required, all ints.

    Saves to SoloProfile.target_*. Used by the iOS first-time
    nutrition setup flow ("Set them myself" path) and by future
    AI-coach paths that suggest targets and let the user accept
    them with one tap.
    """
    profile = _ensure_solo(request.user)
    if profile is None:
        return Response({"detail": "Solo accounts only."}, status=status.HTTP_403_FORBIDDEN)

    data = request.data or {}
    try:
        calories = int(data.get("calories") or 0)
        protein  = int(data.get("protein")  or 0)
        carbs    = int(data.get("carbs")    or 0)
        fats     = int(data.get("fats")     or 0)
    except (TypeError, ValueError):
        return Response({"detail": "Targets must be integers."}, status=400)

    # Sanity-cap. Refusing 0 calories keeps the "macros set" check
    # (`target_calories > 0`) reliable across the app.
    if calories < 800 or calories > 6000:
        return Response(
            {"detail": "Calorie target should be between 800 and 6000 kcal."},
            status=400,
        )
    if protein < 20 or protein > 500:
        return Response({"detail": "Protein target out of range."}, status=400)
    if carbs < 0 or carbs > 800:
        return Response({"detail": "Carb target out of range."}, status=400)
    if fats < 0 or fats > 250:
        return Response({"detail": "Fat target out of range."}, status=400)

    profile.target_calories = calories
    profile.target_protein  = protein
    profile.target_carbs    = carbs
    profile.target_fats     = fats
    profile.save(update_fields=[
        "target_calories", "target_protein", "target_carbs", "target_fats",
    ])

    return Response({
        "ok":            True,
        "target_calories": profile.target_calories,
        "target_protein":  profile.target_protein,
        "target_carbs":    profile.target_carbs,
        "target_fats":     profile.target_fats,
    })


# --------------------------------------------------------------------
# Daily totals + targets
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_nutrition_today(request):
    profile = _ensure_solo(request.user)
    if profile is None:
        return Response({"detail": "Solo accounts only."}, status=status.HTTP_403_FORBIDDEN)

    target_date = _parse_date(request.query_params.get("date"))
    rows = list(SoloFoodLogEntry.objects.filter(
        user=request.user, consumed_on=target_date,
    ).order_by("created_at"))

    eaten = {
        "calories": round(sum(r.calories for r in rows), 1),
        "protein":  round(sum(r.protein  for r in rows), 1),
        "carbs":    round(sum(r.carbs    for r in rows), 1),
        "fats":     round(sum(r.fats     for r in rows), 1),
    }
    targets = {
        "calories": profile.target_calories,
        "protein":  profile.target_protein,
        "carbs":    profile.target_carbs,
        "fats":     profile.target_fats,
    }
    return Response({
        "date":     target_date.isoformat(),
        "targets":  targets,
        "eaten":    eaten,
        "entries":  [_entry_payload(r) for r in rows],
    })


# --------------------------------------------------------------------
# Log create / delete
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_nutrition_log_create(request):
    """Append a row to the user's daily log."""
    profile = _ensure_solo(request.user)
    if profile is None:
        return Response({"detail": "Solo accounts only."}, status=status.HTTP_403_FORBIDDEN)

    data = request.data or {}
    consumed_on = _parse_date(data.get("consumed_on"))

    food_id = data.get("food_id")
    if food_id:
        # Logging a saved food. Snapshot its macros so the row stays
        # valid even if the FoodLibraryItem is later edited.
        food = get_object_or_404(FoodLibraryItem, id=food_id)
        # Compute the actual macros for the consumed portion. The
        # FoodLibraryItem stores macros "per reference_grams"; iOS
        # passes `portion` as how-many-grams (or how-many-units for
        # non-gram portions).
        try:
            portion = float(data.get("portion") or food.reference_grams or 100)
        except (TypeError, ValueError):
            portion = food.reference_grams or 100
        ratio = (portion or 0) / max(food.reference_grams or 1, 1)
        entry = SoloFoodLogEntry.objects.create(
            user=request.user,
            food=food,
            name=food.name,
            portion=portion,
            calories=food.calories * ratio,
            protein=food.protein * ratio,
            carbs=food.carbs * ratio,
            fats=food.fats * ratio,
            consumed_on=consumed_on,
        )
    else:
        # Ad-hoc — user typed everything in.
        name = (data.get("name") or "").strip()[:255]
        if not name:
            return Response({"detail": "Either food_id or name is required."}, status=400)
        try:
            portion = float(data.get("portion") or 100)
            calories = float(data.get("calories") or 0)
            protein  = float(data.get("protein")  or 0)
            carbs    = float(data.get("carbs")    or 0)
            fats     = float(data.get("fats")     or 0)
        except (TypeError, ValueError):
            return Response({"detail": "Macros must be numbers."}, status=400)
        entry = SoloFoodLogEntry.objects.create(
            user=request.user, food=None, name=name,
            portion=portion, calories=calories, protein=protein,
            carbs=carbs, fats=fats, consumed_on=consumed_on,
        )
    return Response(_entry_payload(entry), status=status.HTTP_201_CREATED)


@csrf_exempt
@api_view(["DELETE"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_nutrition_log_delete(request, entry_id: int):
    """Remove a logged row. Only the owning user can delete."""
    if request.user.role != User.SOLO:
        return Response({"detail": "Solo accounts only."}, status=status.HTTP_403_FORBIDDEN)
    entry = get_object_or_404(SoloFoodLogEntry, id=entry_id, user=request.user)
    entry.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------
# Barcode lookup (Open Food Facts proxy)
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_nutrition_barcode_lookup(request, code: str):
    """Look up a barcode against Open Food Facts. Returns:
        { found: bool, name, brand, calories, protein, carbs, fats,
          reference_grams, off_id }
    Macros normalized to per-100g."""
    import requests
    code = (code or "").strip()
    if not code or not code.isdigit() or len(code) > 32:
        return Response({"detail": "Invalid barcode."}, status=400)

    url = f"https://world.openfoodfacts.org/api/v2/product/{code}.json"
    try:
        resp = requests.get(
            url, timeout=6.0,
            headers={"User-Agent": "GymFlow/1.0 (gymflow.coach)"},
        )
        data = resp.json()
    except Exception as exc:
        log.warning("OFF barcode lookup failed for %s: %s", code, exc)
        return Response({"detail": "Open Food Facts unavailable."}, status=503)

    product = data.get("product") or {}
    if data.get("status") != 1 or not product:
        return Response({"found": False})

    nutriments = product.get("nutriments") or {}
    def _g(key: str, default: float = 0.0) -> float:
        try:
            return float(nutriments.get(key) or default)
        except (TypeError, ValueError):
            return default

    return Response({
        "found":           True,
        "off_id":          code,
        "name":            (product.get("product_name") or "").strip()[:255] or "Unknown product",
        "brand":           (product.get("brands") or "").split(",")[0].strip()[:255],
        "calories":        round(_g("energy-kcal_100g"), 1),
        "protein":         round(_g("proteins_100g"),    1),
        "carbs":           round(_g("carbohydrates_100g"),1),
        "fats":            round(_g("fat_100g"),         1),
        "reference_grams": 100,
    })


# --------------------------------------------------------------------
# Custom food creation
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_nutrition_food_create(request):
    """Create a FoodLibraryItem owned by the calling user. Mirrors the
    trainer dashboard endpoint but checks role=SOLO."""
    if request.user.role != User.SOLO:
        return Response({"detail": "Solo accounts only."}, status=status.HTTP_403_FORBIDDEN)

    data = request.data or {}
    name = (data.get("name") or "").strip()[:255]
    if not name:
        return Response({"detail": "Name is required."}, status=400)
    try:
        ref = float(data.get("reference_grams") or 100)
        cal = float(data.get("calories") or 0)
        pro = float(data.get("protein")  or 0)
        car = float(data.get("carbs")    or 0)
        fat = float(data.get("fats")     or 0)
    except (TypeError, ValueError):
        return Response({"detail": "Macros must be numbers."}, status=400)

    portion_type = (data.get("portion_type") or "grams").lower()
    valid = {p for p, _ in FoodLibraryItem.PORTION_CHOICES}
    if portion_type not in valid:
        portion_type = "grams"

    item = FoodLibraryItem.objects.create(
        user=request.user,
        name=name,
        reference_grams=ref,
        calories=cal, protein=pro, carbs=car, fats=fat,
        portion_type=portion_type,
        unit_label=(data.get("unit_label") or "").strip()[:40],
        source=FoodLibraryItem.SOURCE_CUSTOM,
    )
    return Response({
        "id":              item.id,
        "name":            item.name,
        "reference_grams": item.reference_grams,
        "calories":        item.calories,
        "protein":         item.protein,
        "carbs":           item.carbs,
        "fats":            item.fats,
        "portion_type":    item.portion_type,
        "unit_label":      item.unit_label,
    }, status=status.HTTP_201_CREATED)
