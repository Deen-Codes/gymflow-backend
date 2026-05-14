"""T2.7 — ExerciseCatalog search endpoint for the iOS picker.

Powers the catalog-driven exercise picker that replaces the free-
text "Exercise name" TextField in `SoloCustomProgrammeBuilder` and
the same picker used by T2.8's in-place edit affordances on
assigned programmes.

GET /api/workouts/catalog/search/

Query params (all optional):
    ?q=             — partial name match, case-insensitive
    ?muscle=        — primary muscle filter (e.g. "quadriceps")
    ?equipment=     — equipment filter ("barbell", "dumbbell",
                      "body_only", etc.)
    ?level=         — beginner / intermediate / expert
    ?limit=         — page size, default 30, max 100
    ?offset=        — pagination cursor
"""
from django.db.models import Q
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import ExerciseCatalog


@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def exercise_catalog_search(request):
    q          = (request.query_params.get("q") or "").strip()
    muscle     = (request.query_params.get("muscle") or "").strip().lower()
    equipment  = (request.query_params.get("equipment") or "").strip().lower()
    level      = (request.query_params.get("level") or "").strip().lower()
    limit  = max(1, min(int(request.query_params.get("limit", 30)), 100))
    offset = max(0, int(request.query_params.get("offset", 0)))

    qs = ExerciseCatalog.objects.filter(is_published=True)
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(secondary_muscles__icontains=q)
        )
    if muscle:
        qs = qs.filter(muscle_group__iexact=muscle)
    if equipment:
        qs = qs.filter(equipment__iexact=equipment)
    if level:
        qs = qs.filter(level__iexact=level)

    # PICKER-POPULARITY-SORT (#340, May 2026) — surface common lifts
    # first. The picker opens to an unfiltered list; users doing a
    # quick free-session log shouldn't have to scroll past "2-Board
    # Press" and "3/4 Sit-Up" to find Bench Press. Sort by
    # `icon_priority` descending (so the curated staples float to the
    # top), then name ascending as the stable tie-breaker. The
    # client-side relevance ranker (SEARCH-RANKING #319) still wins
    # once a query is typed.
    qs = qs.order_by("-icon_priority", "name")

    total = qs.count()
    rows = list(qs.only(
        "id", "name", "muscle_group", "secondary_muscles", "equipment",
        "level", "mechanic", "force", "image_url", "animation_url",
        "form_description", "icon_priority",
    )[offset:offset + limit])

    payload = [
        {
            "id":               r.id,
            "name":             r.name,
            "primary_muscle":   r.muscle_group,
            "secondary":        [
                m.strip() for m in (r.secondary_muscles or "").split(",") if m.strip()
            ],
            "equipment":        r.equipment,
            "level":            r.level,
            "mechanic":         r.mechanic,
            "force":            r.force,
            "image_url":        r.image_url,
            "animation_url":    r.animation_url,
            "has_form_copy":    bool(r.form_description),
            # Surfaced so the iOS cache can preserve the priority sort
            # after a disk-cache round-trip (the cache stores rows in
            # its own array, not in the order they came off the wire).
            "icon_priority":    r.icon_priority,
        }
        for r in rows
    ]

    return Response({
        "results": payload,
        "total":   total,
        "limit":   limit,
        "offset":  offset,
    })
