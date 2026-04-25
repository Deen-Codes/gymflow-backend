"""Phase 3 — nutrition dashboard JSON endpoints.

Powers the drag-drop meal builder + Open Food Facts food search.

Auth: trainer with role==TRAINER and a related trainer_profile.
Catalog reads proxy to Open Food Facts; writes are scoped to the
calling trainer's own data.
"""
import json
import logging
import time
import urllib.parse
import urllib.request
import urllib.error

from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404

log = logging.getLogger(__name__)

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import User

from .models import (
    FoodLibraryItem,
    NutritionMeal,
    NutritionMealItem,
)
from .dashboard_serializers import (
    FoodCatalogResultSerializer,
    FoodLibraryItemSerializer,
    MealItemCreateSerializer,
    MealItemReadSerializer,
    MealItemUpdateSerializer,
    MealReorderSerializer,
)


# Search-a-licious is OFF's modern Elasticsearch-backed search engine —
# proper relevance ranking, much less noise than the legacy MongoDB regex
# search. Far better for queries like "rice" / "chicken breast" where the
# old endpoint returns anything containing the word in any field.
OFF_SALC_URL = "https://search.openfoodfacts.org/search"
OFF_V2_URL = "https://world.openfoodfacts.org/api/v2/search"
OFF_LEGACY_URL = "https://world.openfoodfacts.org/cgi/search.pl"
# OFF asks third parties to identify themselves clearly.
OFF_USER_AGENT = "GymFlow/1.0 - Trainer dashboard - https://github.com/Deen-Codes/gymflow-backend"
OFF_TIMEOUT_SEC = 6
OFF_CACHE_SECONDS = 600  # 10 min — same query won't keep hitting OFF
# Bumped to v5 — exact-match boost reorders results, so any cached
# responses from the previous shape would be in the wrong order.
OFF_CACHE_PREFIX = "off:v5:search:"


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


def _pick_name(p):
    """Pick the best human-readable name from an OFF product.
    Prefers explicit English fields, then the generic name, then the
    default. Returns "" if nothing usable exists (caller should drop)."""
    if not isinstance(p, dict):
        return ""
    candidates = [
        p.get("product_name_en"),
        p.get("product_name"),
        p.get("generic_name_en"),
        p.get("generic_name"),
    ]
    for c in candidates:
        if not c:
            continue
        # OFF sometimes returns localized names as a list of dicts
        # like [{"lang": "en", "text": "Brown Rice"}]. Pull the text
        # if that's what we got.
        if isinstance(c, list) and c:
            first = c[0]
            if isinstance(first, dict):
                c = first.get("text") or first.get("value") or ""
            else:
                c = first
        if not isinstance(c, str):
            c = str(c)
        s = c.strip()
        if not s:
            continue
        # Skip names that are just barcodes / numeric IDs — OFF stores
        # these for products with no real name set.
        digits = sum(ch.isdigit() for ch in s)
        if digits >= max(6, len(s) - 2):
            continue
        return s[:255]
    return ""


def _normalize_off_product(p):
    """Reduce one Open Food Facts product blob into our flat shape.

    Handles three shapes:
      • OFF v2 / legacy CGI: flat dict {code, product_name, nutriments}
      • search-a-licious flat: same shape as v2
      • search-a-licious ES-style: {_id, _score, _source: {...flat...}}

    Returns None if the input isn't a dict at all (defensive — we never
    want a single bad row to 500 the whole search).
    """
    if not isinstance(p, dict):
        return None

    # Unwrap Elasticsearch-style hits if needed
    if "_source" in p and isinstance(p.get("_source"), dict):
        p = p["_source"]

    nutr = p.get("nutriments") or {}
    if not isinstance(nutr, dict):
        nutr = {}

    kcal = (
        nutr.get("energy-kcal_100g")
        or nutr.get("energy-kcal")
        or nutr.get("energy_100g")
        or 0
    )
    # OFF returns `brands` as a comma-separated string, but
    # search-a-licious returns it as a list. Handle both shapes
    # so we don't render Python list literals to the trainer.
    brands_raw = p.get("brands")
    if isinstance(brands_raw, list):
        brand_str = (brands_raw[0] if brands_raw else "")
    elif isinstance(brands_raw, str):
        brand_str = brands_raw.split(",")[0]
    else:
        brand_str = ""
    if not isinstance(brand_str, str):
        brand_str = str(brand_str)
    brand = brand_str.strip()[:255]

    return {
        "external_id": str(p.get("code") or "").strip(),
        "name": _pick_name(p),
        "brand": brand,
        "reference_grams": 100.0,
        "calories": _safe_float(kcal),
        "protein": _safe_float(nutr.get("proteins_100g")),
        "carbs": _safe_float(nutr.get("carbohydrates_100g")),
        "fats": _safe_float(nutr.get("fat_100g")),
    }


def _is_useful_food(item):
    """Filter rules that drop OFF noise:
       - missing or junk name → drop
       - all-zero macros → drop (means OFF has no nutrition data)
    """
    if not item.get("name"):
        return False
    macro_total = (
        (item.get("calories") or 0)
        + (item.get("protein") or 0)
        + (item.get("carbs") or 0)
        + (item.get("fats") or 0)
    )
    return macro_total > 0


def _snapshot_off_into_library(trainer, payload):
    """Idempotent: if an OFF product is already in this trainer's
    library (matched on source+external_id), return it; otherwise
    create a fresh FoodLibraryItem from the OFF snapshot."""
    external_id = (payload.get("external_id") or "").strip()
    if external_id:
        existing = FoodLibraryItem.objects.filter(
            user=trainer, source=FoodLibraryItem.SOURCE_OFF, external_id=external_id
        ).first()
        if existing:
            return existing

    return FoodLibraryItem.objects.create(
        user=trainer,
        name=payload["name"],
        brand=payload.get("brand", ""),
        reference_grams=payload.get("reference_grams", 100.0) or 100.0,
        calories=payload.get("calories", 0.0) or 0.0,
        protein=payload.get("protein", 0.0) or 0.0,
        carbs=payload.get("carbs", 0.0) or 0.0,
        fats=payload.get("fats", 0.0) or 0.0,
        source=FoodLibraryItem.SOURCE_OFF if external_id else FoodLibraryItem.SOURCE_CUSTOM,
        external_id=external_id,
    )


def _scale_macros(library_item, grams):
    """Scale a library item's per-reference macros to a portion size."""
    ref = library_item.reference_grams or 100.0
    factor = float(grams) / float(ref) if ref else 0
    return {
        "calories": (library_item.calories or 0) * factor,
        "protein": (library_item.protein or 0) * factor,
        "carbs": (library_item.carbs or 0) * factor,
        "fats": (library_item.fats or 0) * factor,
    }


# -------------------------------------------------------------------
# Open Food Facts catalog (right-rail search)
# -------------------------------------------------------------------
def _http_get_json(url, timeout=OFF_TIMEOUT_SEC):
    """Fetch a JSON response. Returns (body_dict, error_str). On HTTP
    or network failure returns (None, "<reason>"). Caller decides what
    to do — we don't raise here so the view can fall back cleanly."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": OFF_USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code} {exc.reason}"
    except (urllib.error.URLError, TimeoutError) as exc:
        return None, f"network: {exc}"
    except (ValueError, json.JSONDecodeError) as exc:
        return None, f"bad JSON: {exc}"


FIELDS = "code,product_name,product_name_en,generic_name,generic_name_en,brands,nutriments"


def _fetch_off(query):
    """Try OFF endpoints in order of result quality:
       1. search-a-licious  — Elasticsearch-backed, proper relevance ranking
       2. v2 + categories   — restricts matches to the curated category
                              taxonomy (drops dairy when searching "rice")
       3. v2 plain text     — last-resort full-text fallback

    First non-empty response wins.
    """
    last_err = "no attempts"
    for fetcher in (_fetch_off_salc, _fetch_off_v2_categories, _fetch_off_v2_text):
        try:
            products, err = fetcher(query)
        except Exception as exc:
            log.warning("OFF fetcher %s raised: %s", fetcher.__name__, exc)
            products, err = None, f"{fetcher.__name__}: {exc}"
        if products:
            return products, None
        if err:
            last_err = err
        time.sleep(0.3)
    return None, last_err


def _fetch_off_salc(query):
    """OFF search-a-licious — modern Elasticsearch search engine."""
    params = {
        "q": query,
        "page_size": "40",
        "fields": FIELDS,
        "langs": "en",
    }
    url = f"{OFF_SALC_URL}?{urllib.parse.urlencode(params)}"
    body, err = _http_get_json(url)
    if body is None:
        return None, f"salc: {err}"
    hits = body.get("hits") or body.get("products") or []
    if isinstance(hits, list):
        return hits, None
    return None, "salc: no hits field"


def _fetch_off_v2_categories(query):
    """OFF v2 with category-tag filter."""
    params = {
        "tagtype_0": "categories",
        "tag_contains_0": "contains",
        "tag_0": query,
        "lc": "en",
        "sort_by": "unique_scans_n",
        "fields": FIELDS,
        "page_size": "40",
        "json": "1",
    }
    url = f"{OFF_V2_URL}?{urllib.parse.urlencode(params)}"
    body, err = _http_get_json(url)
    if body is None:
        return None, f"v2-categories: {err}"
    products = body.get("products") or []
    if isinstance(products, list):
        return products, None
    return None, "v2-categories: no products field"


def _fetch_off_v2_text(query):
    """OFF v2 free-text fallback."""
    params = {
        "search_terms": query,
        "lc": "en",
        "sort_by": "unique_scans_n",
        "fields": FIELDS,
        "page_size": "40",
        "json": "1",
    }
    url = f"{OFF_V2_URL}?{urllib.parse.urlencode(params)}"
    body, err = _http_get_json(url)
    if body is None:
        return None, f"v2-text: {err}"
    products = body.get("products") or []
    if isinstance(products, list):
        return products, None
    return None, "v2-text: no products field"


def _dedupe_by_name(items):
    """Collapse multiple branded variants of the same food into one row.

    Trainers don't care that "Tilda Brown Rice" and "Sainsbury's Brown
    Rice" are different products — they care about brown rice. After
    debranding, we keep the first occurrence per lowercased name so the
    list stays clean.
    """
    seen = set()
    out = []
    for item in items:
        key = (item.get("name") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _exact_match_boost(items, query):
    """Re-rank results so exact / near-exact name matches float to the top.

    The default OFF order surfaces popular compound products like
    "Almond milk" before the literal "Almonds". Trainers searching
    "almond" almost always want the literal first. We bucket results
    into match-quality tiers and concatenate, preserving relative
    order inside each tier.
    """
    q = (query or "").strip().lower()
    if not q:
        return items

    exact, plural, starts, contains, rest = [], [], [], [], []
    for item in items:
        n = (item.get("name") or "").strip().lower()
        if n == q:
            exact.append(item)
        elif n in (q + "s", q + "es") or q in (n + "s", n + "es"):
            plural.append(item)
        elif n.startswith(q + " ") or n.startswith(q + ","):
            starts.append(item)
        elif f" {q} " in f" {n} " or n.endswith(" " + q):
            contains.append(item)
        else:
            rest.append(item)

    return exact + plural + starts + contains + rest


def _library_fallback(trainer, query):
    """When OFF is fully down, search the trainer's already-snapshotted
    library so they can still build meals. Returns rows shaped like the
    catalog response."""
    qs = FoodLibraryItem.objects.filter(user=trainer)
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(brand__icontains=query))
    qs = qs.order_by("name")[:20]
    rows = []
    for f in qs:
        # `id` lets the JS post the library_item_id path (no re-snapshot
        # needed). external_id stays for the in-library badge.
        rows.append({
            "id": f.id,
            "external_id": f.external_id or f"lib-{f.id}",
            "name": f.name,
            "brand": f.brand,
            "reference_grams": f.reference_grams or 100,
            "calories": f.calories or 0,
            "protein": f.protein or 0,
            "carbs": f.carbs or 0,
            "fats": f.fats or 0,
            "in_library": True,
        })
    return rows


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def food_search(request):
    """GET /api/nutrition/dashboard/catalog/?q=apple

    Live-proxy to Open Food Facts with retry, legacy-endpoint fallback,
    a 10-minute in-memory cache, and a library fallback when OFF is
    fully down — so the trainer can still build meals during outages.

    Response shape: {results: [...], source: "off"|"cache"|"library", message?}
    """
    trainer, err = _require_trainer(request)
    if err:
        return err

    q = (request.query_params.get("q") or "").strip()
    if not q:
        return Response({"results": [], "source": "off"})

    cache_key = f"{OFF_CACHE_PREFIX}{q.lower()}"
    try:
        cached = cache.get(cache_key)
    except Exception as exc:  # broken cache backend shouldn't 500
        log.warning("food cache read failed: %s", exc)
        cached = None
    if cached is not None:
        results = _annotate_in_library(trainer, cached)
        return Response({"results": results, "source": "cache"})

    # Fetch + normalize is wrapped end-to-end so any unexpected exception
    # (network, weird response shape, etc.) falls back to the library
    # rather than 500-ing the entire search.
    try:
        products, off_err = _fetch_off(q)
    except Exception as exc:
        log.exception("food_search: fetch raised: %s", exc)
        products, off_err = None, str(exc)

    if not products:
        log.info("food_search: OFF returned no usable results for %r (%s)", q, off_err)
        rows = _library_fallback(trainer, q)
        return Response({
            "results": rows,
            "source": "library",
            "message": "Food search is offline — showing matches from your library.",
        })

    normalized = []
    for p in products:
        try:
            n = _normalize_off_product(p)
        except Exception as exc:
            log.warning("food_search: normalize failed for %r: %s", p, exc)
            continue
        if not n:
            continue
        if not n.get("external_id"):
            continue
        if not _is_useful_food(n):
            continue
        normalized.append(n)

    # Collapse branded variants of the same food into one row
    # ("Tilda Brown Rice", "Sainsbury's Brown Rice" → just "Brown Rice").
    normalized = _dedupe_by_name(normalized)

    # Bubble exact / near-exact matches to the top so "almond" surfaces
    # "Almonds" before "Almond milk".
    normalized = _exact_match_boost(normalized, q)

    # Trim back down to ~20 after filtering — page_size=40 was headroom.
    normalized = normalized[:20]

    if not normalized:
        log.info("food_search: all %d OFF results filtered out for %r", len(products), q)
        rows = _library_fallback(trainer, q)
        return Response({
            "results": rows,
            "source": "library",
            "message": "No matches with nutrition data — showing your library instead.",
        })

    try:
        cache.set(cache_key, normalized, OFF_CACHE_SECONDS)
    except Exception as exc:
        log.warning("food cache write failed: %s", exc)

    results = _annotate_in_library(trainer, normalized)
    return Response({"results": results, "source": "off"})


def _annotate_in_library(trainer, items):
    """Tag each item with `in_library=True` if this trainer already
    snapshotted that OFF code. Mutates a shallow copy."""
    if not items:
        return []
    external_ids = [it["external_id"] for it in items if it.get("external_id")]
    in_lib = set()
    if external_ids:
        in_lib = set(
            FoodLibraryItem.objects.filter(
                user=trainer,
                source=FoodLibraryItem.SOURCE_OFF,
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
    The OFF path implicitly snapshots the food into the trainer's
    library before creating the meal item.
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
        library_item = _snapshot_off_into_library(trainer, {
            "external_id": payload.get("external_id", ""),
            "name": payload["name"],
            "brand": payload.get("brand", ""),
            "reference_grams": payload.get("reference_grams", 100.0) or 100.0,
            "calories": payload.get("calories", 0.0) or 0.0,
            "protein": payload.get("protein", 0.0) or 0.0,
            "carbs": payload.get("carbs", 0.0) or 0.0,
            "fats": payload.get("fats", 0.0) or 0.0,
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
        item.protein = (src.protein or 0) * factor * (src.reference_grams or 100.0) / ref
        item.carbs = (src.carbs or 0) * factor * (src.reference_grams or 100.0) / ref
        item.fats = (src.fats or 0) * factor * (src.reference_grams or 100.0) / ref
    else:
        # Proportional rescale: new_macro = old_macro * (new_grams / old_grams)
        old_grams = item.grams or ref
        scale = grams / old_grams if old_grams else 0
        item.calories = (item.calories or 0) * scale
        item.protein = (item.protein or 0) * scale
        item.carbs = (item.carbs or 0) * scale
        item.fats = (item.fats or 0) * scale

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
