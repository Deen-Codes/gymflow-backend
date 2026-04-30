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
    solo_nutrition_barcode_lookup,
    solo_nutrition_food_create,
    solo_macro_targets_update,
)
from .ai_describe_views import solo_ai_describe_food

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
    path("solo/barcode/<str:code>/",     solo_nutrition_barcode_lookup,  name="solo-nutrition-barcode"),
    path("solo/foods/",                  solo_nutrition_food_create,     name="solo-nutrition-food-create"),

    # R5-2 — first-time macro target setup ("Set them myself"
    # path on iOS).
    path("solo/macro-targets/",          solo_macro_targets_update,      name="solo-macro-targets"),

    # N.1.2 — AI describe (Pro AI gated)
    path("solo/ai-describe/",            solo_ai_describe_food,          name="solo-ai-describe"),
]
