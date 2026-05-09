"""T1.8 — NutritionTemplate recommend endpoint.

Free-tier "Tier 2" path per DISPATCH_BRIEF.md. Returns the top-N
NutritionTemplate rows ranked against the user's profile so the
iOS empty-state carousel (T2.6) can surface a personalised first
read without burning AI budget.

Wire shape (GET /api/nutrition/templates/recommend/):

    {
      "templates": [
        {
          "slug": "lean_cutting",
          "name": "Lean cutting",
          "tagline": "Drop fat, keep the muscle.",
          "summary": "...",
          "pace_label": "~0.5 kg/week down",
          "scaled_macros": {"calories": 2000, "protein": 150, ...},
          "rank_score": 0.92,
          "match_reasons": ["matches goal: lose_fat"]
        },
        ...
      ]
    }

Ranking (no AI call):
  • Hard filter — drop any template whose `dietary_compatibility`
    is non-empty AND doesn't contain the user's dietary_pattern.
  • Soft score — +1 per goal_alignment overlap with user goals,
    +0.5 if the user's dietary_pattern matches a non-empty tag,
    +0.1 baseline so all rows have a score. Higher wins.
  • Tiebreak by NutritionTemplate.sort_order (lower is earlier).
"""
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import SoloProfile, User

from .models import NutritionTemplate


def _rank(template: NutritionTemplate, profile: SoloProfile | None) -> tuple[float, list[str]]:
    """Return (score, reasons[]) for a template against this profile."""
    reasons: list[str] = []
    score = 0.1   # baseline so unranked templates still show

    user_goals = set(getattr(profile, "goals", None) or [])
    template_goals = set(template.goal_tags())
    overlap = user_goals & template_goals
    if overlap:
        score += float(len(overlap))
        reasons.append(f"matches goal: {', '.join(sorted(overlap))}")

    user_diet = (getattr(profile, "dietary_pattern", "") or "").strip()
    template_diets = set(template.dietary_tags())
    if user_diet and user_diet in template_diets:
        score += 0.5
        reasons.append(f"diet match: {user_diet}")
    elif user_diet and template_diets and user_diet not in template_diets:
        # Hard filter — caller drops this row.
        return -1.0, []

    return score, reasons


@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def recommend_templates(request):
    """Top 3 templates ranked for the current user.

    Query params (all optional):
      • `top`   — number of templates to return (default 3, max 8)
      • `goal` — override the user's profile goal for ranking. Useful
                 for iOS preview when the user is browsing alternatives
                 to their committed goal.
    """
    user = request.user
    top_n = max(1, min(int(request.query_params.get("top", 3)), 8))
    goal_override = request.query_params.get("goal", "").strip()

    profile = None
    if user.role == User.SOLO:
        profile, _ = SoloProfile.objects.get_or_create(user=user)
        if goal_override:
            # Per-request override; never mutates the saved profile.
            profile_clone = SoloProfile(
                user=user,
                goals=[goal_override],
                dietary_pattern=profile.dietary_pattern,
                bodyweight_kg=profile.bodyweight_kg,
            )
            profile_for_rank = profile_clone
        else:
            profile_for_rank = profile
    else:
        profile_for_rank = None

    bw   = getattr(profile, "bodyweight_kg", None)
    tdee = int(bw * 30.0) if bw else None

    rows = list(NutritionTemplate.objects.filter(is_published=True))

    ranked = []
    for tpl in rows:
        score, reasons = _rank(tpl, profile_for_rank)
        if score < 0:
            continue   # hard-filtered (incompatible diet)
        ranked.append((score, tpl, reasons))

    # Sort: score desc, then sort_order asc as tiebreak.
    ranked.sort(key=lambda x: (-x[0], x[1].sort_order, x[1].name))

    payload = []
    for score, tpl, reasons in ranked[:top_n]:
        payload.append({
            "slug":           tpl.slug,
            "name":           tpl.name,
            "tagline":        tpl.tagline,
            "summary":        tpl.summary,
            "pace_label":     tpl.pace_label,
            "goal_alignment":  tpl.goal_tags(),
            "dietary_compatibility": tpl.dietary_tags(),
            "scaled_macros": tpl.scaled_macros(bw, tdee),
            "rank_score":    round(score, 3),
            "match_reasons": reasons,
        })

    return Response({"templates": payload}, status=status.HTTP_200_OK)
