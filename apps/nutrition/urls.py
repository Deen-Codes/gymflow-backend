"""Mobile-facing nutrition endpoints (iOS client)."""
from django.urls import path
from .mobile_views import (
    nutrition_today_for_me,
    consumption_for_me,
)
from .solo_views import (
    solo_nutrition_today,
    solo_nutrition_log_create,
    solo_nutrition_log_delete,
    solo_nutrition_food_search,
    solo_nutrition_food_create,
    solo_macro_targets_update,
)
from .ai_describe_views import solo_ai_describe_food
from .ai_build_views import solo_ai_nutrition_build
from .ai_meals_views import solo_ai_meals_suggest
from .template_views import recommend_templates
from .meal_template_views import (
    meal_templates_collection,
    meal_template_detail,
    meal_template_log,
)

urlpatterns = [
    path("me/today/",        nutrition_today_for_me, name="me-nutrition-today"),

    # Phase C.2 — server-of-record meal consumption.
    # Single URL handles GET (list ticks for date) / POST (tick) /
    # DELETE (untick). Method dispatch lives in the view itself.
    path("me/consumption/",  consumption_for_me,     name="me-consumption"),

    # N.1.1 — Solo nutrition. Separate from the trainer-meal-plan
    # endpoints so a user can be SOLO and use these without any
    # role-detection branching on iOS.
    path("solo/today/",                  solo_nutrition_today,           name="solo-nutrition-today"),
    path("solo/log/",                    solo_nutrition_log_create,      name="solo-nutrition-log-create"),
    path("solo/log/<int:entry_id>/",     solo_nutrition_log_delete,      name="solo-nutrition-log-delete"),

    # NUTRITION-DB (#105) — text search across CuratedFood.
    # Query: ?q=chicken&limit=25&region=gb
    # Internal-only — no external DB fallback.
    path("solo/foods/search/",           solo_nutrition_food_search,     name="solo-nutrition-food-search"),

    path("solo/foods/",                  solo_nutrition_food_create,     name="solo-nutrition-food-create"),

    # R5-2 — first-time macro target setup ("Set them myself"
    # path on iOS).
    path("solo/macro-targets/",          solo_macro_targets_update,      name="solo-macro-targets"),

    # N.1.2 — AI describe (Pro AI gated)
    path("solo/ai-describe/",            solo_ai_describe_food,          name="solo-ai-describe"),

    # NUTRITION-3-OPTIONS — three-variant macro plan generator.
    # First call free per user (AI-FREE-FIRST-GEN); subsequent
    # require Pro AI. Returns cut/maintain/bulk variants Claude
    # produces from the user's onboarding context.
    path("solo/ai-build/",               solo_ai_nutrition_build,        name="solo-ai-nutrition-build"),

    # T1.8 — free-tier nutrition template recommender. No AI cost.
    # Ranks the 8 NutritionTemplate rows against the user's goals
    # + dietary pattern + bodyweight and returns the top 3 (or
    # whatever ?top= asks for, capped at 8).
    path("templates/recommend/",         recommend_templates,            name="nutrition-templates-recommend"),

    # T3.2 — catalog-grounded AI meal suggestions. Pro-AI gated.
    # Pulls a slot-aware slice of CuratedFood, asks Claude to
    # assemble meals that hit the user's saved macro target using
    # ONLY food_ids from that slice. Validates IDs + retries once
    # on hallucination.
    path("solo/ai-meals/",               solo_ai_meals_suggest,          name="solo-ai-meals-suggest"),

    # T2.9 — user-saved meal templates from the food catalog.
    # CRUD + one-tap log to the food diary. Idempotent on retry.
    path("meal-templates/",                          meal_templates_collection, name="meal-templates-collection"),
    path("meal-templates/<int:template_id>/",        meal_template_detail,      name="meal-template-detail"),
    path("meal-templates/<int:template_id>/log/",    meal_template_log,         name="meal-template-log"),
]
