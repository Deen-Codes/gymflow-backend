"""T2.9 — MealTemplate CRUD + one-tap log endpoint.

Endpoints:
    GET    /api/nutrition/meal-templates/
    POST   /api/nutrition/meal-templates/
    PATCH  /api/nutrition/meal-templates/<id>/
    DELETE /api/nutrition/meal-templates/<id>/
    POST   /api/nutrition/meal-templates/<id>/log/

POST body:
    {
      "title": "My usual breakfast",
      "slot":  "breakfast",
      "notes": "Quick oats + Greek yoghurt",
      "items": [
        {"food_id": 4521, "portion_g": 80, "order": 0},
        {"food_id": 9132, "portion_g": 200, "order": 1},
      ]
    }

Solo-only — trainer/client meal plans live in the trainer-track
endpoints (apps/nutrition/views.py). Each edit writes a
RecentEditLog row (T2.10) so the AI PT context surfaces the
change next time the user opens chat.
"""
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import User

from .models import (
    CuratedFood, MealTemplate, MealTemplateItem, SoloFoodLogEntry,
)


def _log_edit(user, kind: str, summary: str, payload: dict) -> None:
    try:
        from apps.users.models import RecentEditLog
        RecentEditLog.record(user=user, kind=kind, summary=summary, payload=payload)
    except Exception:
        pass


def _serialize_template(t: MealTemplate) -> dict:
    items = []
    for it in t.items.all().select_related("food"):
        f = it.food
        scale = (it.portion_g or 0.0) / 100.0
        items.append({
            "id":         it.id,
            "food_id":    f.id,
            "name":       f.name,
            "brand":      f.brand or "",
            "portion_g":  round(it.portion_g or 0.0, 1),
            "calories":   round(f.kcal_per_100g    * scale, 1),
            "protein":    round(f.protein_per_100g * scale, 1),
            "carbs":      round(f.carbs_per_100g   * scale, 1),
            "fats":       round(f.fat_per_100g     * scale, 1),
            "order":      it.order,
        })
    return {
        "id":               t.id,
        "title":            t.title,
        "slot":             t.slot,
        "notes":            t.notes,
        "source":           t.source,
        "is_favourite":     t.is_favourite,
        "is_in_daily_plan": t.is_in_daily_plan,
        "items":            items,
        "totals":           t.totals(),
        "created_at":       t.created_at.isoformat(),
        "updated_at":       t.updated_at.isoformat(),
    }


# ----------------------------------------------------------------
# List + create
# ----------------------------------------------------------------
@api_view(["GET", "POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def meal_templates_collection(request):
    user = request.user
    if user.role != User.SOLO:
        return Response({"detail": "Solo accounts only."}, status=403)

    if request.method == "GET":
        slot = request.query_params.get("slot")
        qs = MealTemplate.objects.filter(user=user)
        if slot:
            qs = qs.filter(slot=slot)
        rows = list(qs.prefetch_related("items__food"))
        return Response({"templates": [_serialize_template(t) for t in rows]})

    # POST — create
    body = request.data or {}
    title = (body.get("title") or "").strip()[:120]
    slot  = (body.get("slot")  or "breakfast").strip()
    notes = (body.get("notes") or "").strip()[:240]
    items = body.get("items") or []

    if not title:
        return Response({"detail": "title is required."}, status=400)
    if slot not in dict(MealTemplate.SLOT_CHOICES):
        return Response({"detail": f"Invalid slot: {slot}"}, status=400)
    if not isinstance(items, list) or not items:
        return Response({"detail": "items must be a non-empty list."}, status=400)

    food_ids = []
    for it in items:
        try:
            food_ids.append(int(it["food_id"]))
        except (KeyError, TypeError, ValueError):
            return Response({"detail": "Each item needs food_id + portion_g."}, status=400)
    food_rows = {f.id: f for f in CuratedFood.objects.filter(id__in=food_ids)}
    missing = [fid for fid in food_ids if fid not in food_rows]
    if missing:
        return Response({"detail": f"Unknown food_id(s): {missing[:5]}"}, status=400)

    # Optional flags — defaults preserve existing behaviour for older
    # callers, but the AI-meal "Add to plan" path needs to set both
    # `source=ai_generated` and `is_in_daily_plan=true` upfront
    # rather than POST-then-PATCH (saves a roundtrip + avoids the
    # template briefly existing in the wrong state).
    source_in = (body.get("source") or "").strip()
    valid_sources = {MealTemplate.SOURCE_USER, MealTemplate.SOURCE_AI}
    source = source_in if source_in in valid_sources else MealTemplate.SOURCE_USER
    in_plan = bool(body.get("is_in_daily_plan", False))

    with transaction.atomic():
        tpl = MealTemplate.objects.create(
            user=user, title=title, slot=slot, notes=notes,
            source=source,
            is_in_daily_plan=in_plan,
        )
        for idx, it in enumerate(items):
            try:
                grams = float(it["portion_g"])
            except (KeyError, TypeError, ValueError):
                continue
            MealTemplateItem.objects.create(
                template=tpl,
                food=food_rows[int(it["food_id"])],
                portion_g=max(1.0, grams),
                order=int(it.get("order") or idx),
            )

    _log_edit(user,
              kind="nutrition_meal",
              summary=f"Saved meal: {title}",
              payload={"template_id": tpl.id, "slot": slot,
                       "item_count": len(items)})

    return Response(_serialize_template(tpl), status=status.HTTP_201_CREATED)


# ----------------------------------------------------------------
# Detail — patch + delete
# ----------------------------------------------------------------
@api_view(["PATCH", "DELETE"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def meal_template_detail(request, template_id: int):
    user = request.user
    tpl = get_object_or_404(MealTemplate, pk=template_id, user=user)

    if request.method == "DELETE":
        title = tpl.title
        tpl.delete()
        _log_edit(user, kind="nutrition_meal",
                  summary=f"Deleted meal: {title}",
                  payload={"template_id": template_id})
        return Response(status=status.HTTP_204_NO_CONTENT)

    body = request.data or {}
    if "title" in body:
        tpl.title = (body["title"] or "").strip()[:120]
    if "notes" in body:
        tpl.notes = (body["notes"] or "").strip()[:240]
    if "slot" in body and body["slot"] in dict(MealTemplate.SLOT_CHOICES):
        tpl.slot = body["slot"]
    if "is_favourite" in body:
        tpl.is_favourite = bool(body["is_favourite"])
    # DAILY-MEAL-PLAN — toggle inclusion in the user's daily plan.
    # Side-effect-free elsewhere; the Nutrition tab reads this flag
    # to decide which meals to surface as "today's plan" when
    # SoloProfile.nutrition_mode = "meal_plan".
    if "is_in_daily_plan" in body:
        tpl.is_in_daily_plan = bool(body["is_in_daily_plan"])

    items_payload = body.get("items")
    if isinstance(items_payload, list):
        # Full replace — simpler than a partial-merge protocol.
        food_ids = []
        for it in items_payload:
            try:
                food_ids.append(int(it["food_id"]))
            except (KeyError, TypeError, ValueError):
                return Response({"detail": "Each item needs food_id + portion_g."}, status=400)
        food_rows = {f.id: f for f in CuratedFood.objects.filter(id__in=food_ids)}
        missing = [fid for fid in food_ids if fid not in food_rows]
        if missing:
            return Response({"detail": f"Unknown food_id(s): {missing[:5]}"}, status=400)

        with transaction.atomic():
            tpl.items.all().delete()
            for idx, it in enumerate(items_payload):
                try:
                    grams = float(it["portion_g"])
                except (KeyError, TypeError, ValueError):
                    continue
                MealTemplateItem.objects.create(
                    template=tpl,
                    food=food_rows[int(it["food_id"])],
                    portion_g=max(1.0, grams),
                    order=int(it.get("order") or idx),
                )

    tpl.save()
    _log_edit(user, kind="nutrition_meal",
              summary=f"Edited meal: {tpl.title}",
              payload={"template_id": tpl.id})
    return Response(_serialize_template(tpl))


# ----------------------------------------------------------------
# One-tap log → SoloFoodLogEntry
# ----------------------------------------------------------------
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def meal_template_log(request, template_id: int):
    """Log every item in this template as a SoloFoodLogEntry on
    today's diary (or `?date=YYYY-MM-DD`). Idempotent on retry —
    safe to call again from iOS if the network drops mid-write."""
    user = request.user
    tpl = get_object_or_404(MealTemplate, pk=template_id, user=user)

    date_str = (request.data or {}).get("date") or request.query_params.get("date")
    today = timezone.localdate()
    try:
        if date_str:
            from datetime import date
            target = date.fromisoformat(date_str)
        else:
            target = today
    except Exception:
        target = today

    created_ids: list[int] = []
    with transaction.atomic():
        for it in tpl.items.all().select_related("food"):
            f = it.food
            if f is None or (it.portion_g or 0) <= 0:
                continue
            scale = it.portion_g / 100.0
            entry = SoloFoodLogEntry.objects.create(
                user=user,
                food=f,
                name=f.name,
                portion=it.portion_g,
                calories=round(f.kcal_per_100g    * scale, 1),
                protein= round(f.protein_per_100g * scale, 1),
                carbs=   round(f.carbs_per_100g   * scale, 1),
                fats=    round(f.fat_per_100g     * scale, 1),
                consumed_on=target,
            )
            created_ids.append(entry.id)

    return Response({
        "ok":          True,
        "template_id": tpl.id,
        "entry_ids":   created_ids,
        "date":        target.isoformat(),
    })
