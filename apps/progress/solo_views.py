"""
D.2.1 — Solo Progress backend.

Four endpoints, designed for the iOS Progress tab's "Year in
review" / "Wrapped" feel:

  • GET /api/progress/solo/sessions/?from=YYYY-MM-DD&to=YYYY-MM-DD
        Historical workout sessions (lightweight rows). Powers the
        calendar heatmap + the "26-session month" headline.

  • GET /api/progress/solo/weight/?from=YYYY-MM-DD&to=YYYY-MM-DD
        Body-weight history time-series. Pulled from the dedicated
        `SoloBodyweightLog` model added below (Solo users don't have
        the trainer-built check-in weight workflow, so we have a
        first-class store).

  • POST /api/progress/solo/weight/   {kg, logged_on?}
        Append a row.

  • GET /api/progress/solo/prs/
        Personal records — current best set per exercise.

  • GET /api/progress/solo/streak/
        Active streak + week tally + lifetime stats.

For PT-coded clients we already have the existing trophy +
checkin-based weight pipelines. Solo gets its own surface so the
UX is decoupled.
"""
from collections import defaultdict
from datetime import datetime, timedelta

from django.db.models import Max, Sum, Count
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import User
from apps.workouts.models import (
    WorkoutSession, ExerciseSession, SetPerformance,
)


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
def _parse_date(raw, fallback):
    if not raw:
        return fallback
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return fallback


def _solo_only(view_func):
    """Decorator: 403 for non-Solo callers."""
    from functools import wraps
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.role != User.SOLO:
            return Response({"detail": "Solo accounts only."}, status=status.HTTP_403_FORBIDDEN)
        return view_func(request, *args, **kwargs)
    return wrapper


# --------------------------------------------------------------------
# Sessions (historical heatmap + counts)
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
@_solo_only
def solo_progress_sessions(request):
    today = timezone.localdate()
    end = _parse_date(request.query_params.get("to"), today)
    start = _parse_date(request.query_params.get("from"), end - timedelta(days=90))

    qs = (
        WorkoutSession.objects
        .filter(
            user=request.user, is_complete=True,
            completed_at__date__gte=start,
            completed_at__date__lte=end,
        )
        .select_related("workout_day")
        .order_by("-completed_at")
    )

    rows = [{
        "id":           s.id,
        "day_id":       s.workout_day_id,
        "day_title":    s.workout_day.title if s.workout_day_id else "",
        "completed_at": s.completed_at.isoformat(),
        "duration":     s.duration,
    } for s in qs]

    return Response({
        "from":     start.isoformat(),
        "to":       end.isoformat(),
        "count":    len(rows),
        "sessions": rows,
    })


# --------------------------------------------------------------------
# Body-weight history
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["GET", "POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
@_solo_only
def solo_progress_weight(request):
    from .models import SoloBodyweightLog

    if request.method == "POST":
        try:
            kg = float(request.data.get("kg") or 0)
        except (TypeError, ValueError):
            return Response({"detail": "kg must be a number."}, status=400)
        if kg < 25 or kg > 400:
            return Response({"detail": "kg out of plausible range."}, status=400)
        on = _parse_date(request.data.get("logged_on"), timezone.localdate())
        # update_or_create on (user, date) so consecutive same-day
        # entries replace rather than duplicate.
        row, created = SoloBodyweightLog.objects.update_or_create(
            user=request.user, logged_on=on, defaults={"kg": kg},
        )
        # Mirror onto SoloProfile.bodyweight_kg when it's the latest
        # row, so macro target re-computation pulls the fresh weight.
        latest = (
            SoloBodyweightLog.objects
            .filter(user=request.user)
            .order_by("-logged_on")
            .first()
        )
        if latest and latest.id == row.id:
            from apps.users.models import SoloProfile
            profile, _ = SoloProfile.objects.get_or_create(user=request.user)
            profile.bodyweight_kg = kg
            profile.save(update_fields=["bodyweight_kg"])
        return Response({"id": row.id, "kg": row.kg, "logged_on": row.logged_on.isoformat()})

    # GET
    today = timezone.localdate()
    end = _parse_date(request.query_params.get("to"), today)
    start = _parse_date(request.query_params.get("from"), end - timedelta(days=180))

    rows = list(SoloBodyweightLog.objects.filter(
        user=request.user, logged_on__gte=start, logged_on__lte=end,
    ).order_by("logged_on"))
    return Response({
        "from":   start.isoformat(),
        "to":     end.isoformat(),
        "points": [{"date": r.logged_on.isoformat(), "kg": r.kg} for r in rows],
    })


# --------------------------------------------------------------------
# PRs (current best per exercise)
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
@_solo_only
def solo_progress_prs(request):
    """For each exercise the user has logged, return the heaviest set
    (with reps + date). PR detection is "highest weight × reps
    product" — a 5×100 beats a 3×100 because the working volume is
    higher. Easy to swap for est-1RM later."""

    # weight + reps are CharField (legacy). Coerce defensively.
    def _f(s):
        try: return float(str(s).strip())
        except (TypeError, ValueError): return 0.0

    sets = (
        SetPerformance.objects
        .filter(exercise_session__workout_session__user=request.user)
        .select_related("exercise_session__exercise")
        .values(
            "exercise_session__exercise__name",
            "weight", "reps",
            "exercise_session__workout_session__completed_at",
        )
    )

    best_by_name: dict[str, dict] = {}
    for s in sets:
        name = s["exercise_session__exercise__name"]
        if not name:
            continue
        w = _f(s["weight"])
        r = _f(s["reps"])
        score = w * r
        if score <= 0:
            continue
        prior = best_by_name.get(name)
        if prior is None or score > prior["score"]:
            best_by_name[name] = {
                "exercise":     name,
                "weight":       w,
                "reps":         r,
                "score":        score,
                "completed_at": s["exercise_session__workout_session__completed_at"].isoformat()
                                 if s["exercise_session__workout_session__completed_at"] else None,
            }

    prs = sorted(best_by_name.values(), key=lambda p: -p["score"])
    return Response({"prs": prs})


# --------------------------------------------------------------------
# Streak + lifetime stats
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
@_solo_only
def solo_progress_streak(request):
    """Solo streak: consecutive weeks where the user trained ≥
    SoloProfile.days_per_week. Falls back to 3 if not set.

    Also returns lifetime totals — workouts, total minutes — for the
    Profile lifetime card.
    """
    from apps.users.models import SoloProfile
    profile, _ = SoloProfile.objects.get_or_create(user=request.user)
    target = profile.days_per_week or 3

    # Group sessions by ISO week.
    sessions = (
        WorkoutSession.objects
        .filter(user=request.user, is_complete=True)
        .order_by("-completed_at")
    )
    weeks: dict[tuple[int, int], int] = defaultdict(int)
    lifetime_count = 0
    lifetime_minutes = 0
    for s in sessions:
        if s.completed_at is None:
            continue
        iso = s.completed_at.isocalendar()
        weeks[(iso.year, iso.week)] += 1
        lifetime_count += 1
        lifetime_minutes += (s.duration or 0)

    # Walk back from current week counting consecutive `>= target`.
    streak = 0
    today = timezone.now().date()
    cur_iso = today.isocalendar()
    cur = (cur_iso.year, cur_iso.week)
    while True:
        if weeks.get(cur, 0) >= target:
            streak += 1
            # previous ISO week
            ref = datetime.fromisocalendar(cur[0], cur[1], 1) - timedelta(days=7)
            riso = ref.isocalendar()
            cur = (riso.year, riso.week)
        else:
            break

    # This week count (for the home/progress UI).
    this_week_count = weeks.get((cur_iso.year, cur_iso.week), 0)

    return Response({
        "streak":           streak,
        "weekly_target":    target,
        "this_week_count":  this_week_count,
        "lifetime_workouts": lifetime_count,
        "lifetime_minutes":  lifetime_minutes,
    })


# --------------------------------------------------------------------
# D.2.2 — Progress photos
# --------------------------------------------------------------------
import base64
from .models import ProgressPhoto


MAX_PHOTO_BYTES = 4 * 1024 * 1024   # ~4MB after b64
FREE_TIER_PHOTOS_PER_MONTH = 1


def _photo_payload(p: ProgressPhoto, *, include_image: bool = False) -> dict:
    out = {
        "id":             p.id,
        "category":       p.category,
        "bodyweight_kg":  p.bodyweight_kg,
        "note":           p.note,
        "taken_on":       p.taken_on.isoformat(),
        "created_at":     p.created_at.isoformat(),
        # PHOTO-COACHING (#106) — expose AI commentary so iOS can
        # surface it on the photo card. Both nullable; iOS gracefully
        # hides if empty.
        "ai_commentary":  p.ai_commentary or "",
        "ai_analyzed_at": p.ai_analyzed_at.isoformat() if p.ai_analyzed_at else None,
    }
    if include_image:
        out["image_base64"] = p.image_base64
    return out


@csrf_exempt
@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
@_solo_only
def solo_progress_photos_list(request):
    """List photos (lightweight — no image bytes by default to keep
    the gallery snappy). Pass ?include_image=1 to fetch the bytes
    inline, or use the per-id endpoint."""
    include_image = request.query_params.get("include_image") == "1"
    rows = list(ProgressPhoto.objects.filter(user=request.user))
    return Response({
        "count":  len(rows),
        "photos": [_photo_payload(p, include_image=include_image) for p in rows],
    })


@csrf_exempt
@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
@_solo_only
def solo_progress_photo_detail(request, photo_id: int):
    """Single photo with the b64 bytes, for the lightbox / compare
    UI."""
    p = ProgressPhoto.objects.filter(user=request.user, id=photo_id).first()
    if p is None:
        return Response({"detail": "Not found."}, status=404)
    return Response(_photo_payload(p, include_image=True))


@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
@_solo_only
def solo_progress_photo_create(request):
    """Upload a new progress photo. Body:
        { image_base64, category?, bodyweight_kg?, note?, taken_on? }
    Free tier: capped at 1 photo per calendar month."""
    from apps.users.models import SoloProfile

    profile, _ = SoloProfile.objects.get_or_create(user=request.user)

    image_b64 = (request.data.get("image_base64") or "").strip()
    if not image_b64:
        return Response({"detail": "image_base64 is required."}, status=400)
    try:
        decoded = base64.b64decode(image_b64, validate=True)
    except Exception:
        return Response({"detail": "image_base64 is not valid base64."}, status=400)
    if len(decoded) > MAX_PHOTO_BYTES:
        return Response({"detail": "Image too large (4MB max)."}, status=413)

    # Free tier: enforce monthly cap.
    if not profile.has_pro_access:
        today = timezone.localdate()
        month_start = today.replace(day=1)
        used = ProgressPhoto.objects.filter(
            user=request.user, taken_on__gte=month_start,
        ).count()
        if used >= FREE_TIER_PHOTOS_PER_MONTH:
            return Response(
                {"detail": "Free tier allows 1 photo per month. Upgrade for unlimited.",
                 "upgrade_to": "pro"},
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )

    category = (request.data.get("category") or "front").lower()
    if category not in {c for c, _ in ProgressPhoto.CATEGORY_CHOICES}:
        category = "front"
    note = (request.data.get("note") or "").strip()[:255]
    try:
        bw = float(request.data.get("bodyweight_kg")) if request.data.get("bodyweight_kg") else None
    except (TypeError, ValueError):
        bw = None
    taken_on = _parse_date(request.data.get("taken_on"), timezone.localdate())

    photo = ProgressPhoto.objects.create(
        user=request.user, category=category,
        image_base64=image_b64, bodyweight_kg=bw, note=note,
        taken_on=taken_on,
    )
    return Response(_photo_payload(photo), status=status.HTTP_201_CREATED)


@csrf_exempt
@api_view(["DELETE"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
@_solo_only
def solo_progress_photo_delete(request, photo_id: int):
    p = ProgressPhoto.objects.filter(user=request.user, id=photo_id).first()
    if p is None:
        return Response({"detail": "Not found."}, status=404)
    p.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)
