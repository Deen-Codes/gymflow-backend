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

from .models import SoloFoodLogEntry, FoodLibraryItem, CuratedFood

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
    """Get-or-create the SoloProfile for solo callers. Returns None
    for non-solo callers.

    NUTRITION-ONBOARDING-FIX — previously this auto-populated
    macro targets on first read by calling
    `compute_default_macro_targets`. That meant every fresh signup
    landed on the Nutrition tab with bogus 2200 kcal targets and
    the empty-state onboarding never surfaced. Now targets stay at
    0 until the user explicitly completes the cinematic onboarding
    (which posts the chosen macros via /macro-targets/) or via the
    AI build path. Both /solo/today/ and the synthesised /me/today/
    response gate on `target_calories > 0`, so 0 cleanly signals
    "needs onboarding".
    """
    if user.role != User.SOLO:
        return None
    profile, _ = SoloProfile.objects.get_or_create(user=user)
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

    # T4.2 — capture the previous kcal target so the cross-domain
    # alignment helper can compare old vs new and surface a chip
    # if the delta is meaningful (>= 300 kcal in either direction
    # while training volume stays high or low).
    old_calories = profile.target_calories or 0

    profile.target_calories = calories
    profile.target_protein  = protein
    profile.target_carbs    = carbs
    profile.target_fats     = fats
    profile.save(update_fields=[
        "target_calories", "target_protein", "target_carbs", "target_fats",
    ])

    # T4.2 — soft cross-domain chip. iOS renders it under the
    # success toast; user taps to either fire the suggested action
    # or open AI PT chat with a seed prompt.
    chip = None
    try:
        from apps.users.cross_domain_alignment import (
            alignment_chip_after_nutrition_change,
        )
        chip = alignment_chip_after_nutrition_change(
            request.user,
            old_calories=old_calories,
            new_calories=calories,
        )
    except Exception:
        pass

    return Response({
        "ok":            True,
        "chip":          chip,
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
# NUTRITION-DB search (#105) — CuratedFood text search.
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_nutrition_food_search(request):
    """Text search across the CuratedFood catalog.

    Query: ?q=chicken&limit=25&region=gb
    Returns: { results: [{name, brand, calories, protein, carbs, fats,
                          reference_grams, serving_grams, serving_label,
                          tags, allergens}, ...] }

    Ranking (descending priority):
      1. Exact name match (case-insensitive)
      2. Name STARTS WITH query
      3. Brand STARTS WITH query
      4. Name CONTAINS query
      5. Brand CONTAINS query
      6. Tags CONTAIN query

    Region filter: when provided, items whose `region_codes` contains
    the user's region rank higher; items NOT tagged for that region
    still appear (e.g. world-wide whole foods) but lower.

    INTERNAL-ONLY. No external DB fallback. If no results, iOS
    can offer the AI Describe path.
    """
    q = (request.query_params.get("q") or "").strip()
    if not q or len(q) < 2:
        return Response({"results": []})
    if len(q) > 80:
        q = q[:80]

    try:
        limit = int(request.query_params.get("limit") or 25)
    except (TypeError, ValueError):
        limit = 25
    limit = max(1, min(50, limit))

    region = (request.query_params.get("region") or "").strip().lower()[:8]

    # Normalise: lowercase + strip non-alphanumeric. This makes
    # "nandos" match "Nando's", "mcdonalds" match "McDonald's",
    # "lyles" match "Lyle's", "cadburys" match "Cadbury's".
    import re
    def _normalise(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

    q_norm = _normalise(q)
    if not q_norm:
        return Response({"results": []})
    q_tokens = [t for t in q_norm.split() if t]

    # Pull a wide candidate pool. For multi-token queries we want
    # items where ANY token matches name OR brand OR tags so we
    # can rank across the combined hits.
    #
    # Apostrophe handling: the DB stores brands like "Nando's" /
    # "McDonald's" / "Lyle's" with literal apostrophes, but users
    # type "nandos" / "mcdonalds". `__icontains` on the raw column
    # won't match because the apostrophe is in the middle. So for
    # every query token we ALSO add an apostrophe-bearing variant
    # — typically the form `<token-without-trailing-s>'s` — so the
    # SQL prefilter can find apostrophe-rich brand names.
    from django.db.models import Q
    def _apostrophe_variants(tok: str) -> list[str]:
        out = [tok]
        # "nandos" → "nando's"; "mcdonalds" → "mcdonald's";
        # "lyles" → "lyle's"; "cadburys" → "cadbury's".
        if len(tok) >= 3 and tok.endswith("s"):
            out.append(f"{tok[:-1]}'s")
        # Also handle "nando" (no trailing s) → "nando's" for
        # users who skip the s entirely.
        if len(tok) >= 3 and not tok.endswith("s"):
            out.append(f"{tok}'s")
        return out
    qfilter = Q()
    for tok in q_tokens:
        for variant in _apostrophe_variants(tok):
            qfilter |= (
                Q(name__icontains=variant)
                | Q(brand__icontains=variant)
                | Q(tags__icontains=variant)
            )
    candidates = CuratedFood.objects.filter(qfilter)[:400]

    ranked = []
    for f in candidates:
        name_n = _normalise(f.name)
        brand_n = _normalise(f.brand)
        tags_n = _normalise(f.tags)
        haystack = f"{name_n} {brand_n} {tags_n}".strip()
        regions_l = (f.regions_l if hasattr(f, 'regions_l') else (f.region_codes or "").lower())

        # Tokens must ALL appear somewhere across name+brand+tags for
        # the row to be considered relevant. This is what enables
        # multi-word queries — "nandos chicken" requires both tokens.
        if not all(tok in haystack for tok in q_tokens):
            continue

        # Tier — best match shape wins:
        #   0  exact name match
        #   1  name starts with full query
        #   2  brand starts with first token (Nando's, McDonald's, etc.)
        #   3  every token matches the name
        #   4  every token matches name+brand combo
        #   5  brand + name combo (multi-token: brand starts AND name has rest)
        #   6  name contains query phrase
        #   7  brand contains query
        #   8  tags
        if name_n == q_norm:
            tier = 0
        elif name_n.startswith(q_norm):
            tier = 1
        elif brand_n.startswith(q_tokens[0]) and len(q_tokens) == 1:
            # Single-token brand-prefix search — pull up the whole brand
            tier = 2
        elif all(tok in name_n for tok in q_tokens):
            tier = 3
        elif brand_n.startswith(q_tokens[0]) and all(tok in name_n for tok in q_tokens[1:]):
            # "nandos chicken" → brand matches first token, rest in name
            tier = 5
        elif q_norm in name_n:
            tier = 6
        elif q_norm in brand_n:
            tier = 7
        elif all(tok in haystack for tok in q_tokens):
            # All tokens spread across name+brand+tags
            tier = 8
        else:
            tier = 9

        # Region bonus — same tier but region-matched items rank
        # before world-wide items.
        region_bonus = 0
        if region and region in regions_l.split(","):
            region_bonus = -1  # negative pulls earlier in sort

        ranked.append((tier, region_bonus, len(name_n), f))

    ranked.sort(key=lambda t: (t[0], t[1], t[2]))
    top = ranked[:limit]

    results = []
    for _, _, _, f in top:
        results.append({
            "name":            f.name,
            "brand":           f.brand or "",
            "calories":        round(f.kcal_per_100g,    1),
            "protein":         round(f.protein_per_100g, 1),
            "carbs":           round(f.carbs_per_100g,   1),
            "fats":            round(f.fat_per_100g,     1),
            "reference_grams": 100,
            "serving_grams":   f.serving_grams,
            "serving_label":   f.serving_label or "",
            "tags":             f.tags or "",
            "allergens":        f.allergens or "",
            "source":           f.source,
            # FOOD-DB-V2 — portion-unit support. iOS uses these to
            # show "1 egg / 2 / 3" stepper rows instead of grams
            # for unit-portion foods.
            "portion_unit":     f.portion_unit or "grams",
            "unit_grams":       f.unit_grams,
        })

    return Response({"results": results})


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
