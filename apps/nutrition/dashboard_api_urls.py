"""URL routing for the Phase 3 nutrition dashboard JSON endpoints."""
from django.urls import path

from . import dashboard_api_views as v

urlpatterns = [
    path("catalog/",                   v.food_search,        name="dashboard-nutrition-catalog"),
    path("library/",                   v.library_list,       name="dashboard-nutrition-library"),
    path("meal-items/",                v.meal_item_add,      name="dashboard-nutrition-meal-item-add"),
    path("meal-items/reorder/",        v.meal_item_reorder,  name="dashboard-nutrition-meal-item-reorder"),
    path("meal-items/<int:item_id>/",  v.meal_item_update,   name="dashboard-nutrition-meal-item-update"),
    path("meal-items/<int:item_id>/delete/", v.meal_item_delete, name="dashboard-nutrition-meal-item-delete"),
]
