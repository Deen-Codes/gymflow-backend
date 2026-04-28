"""URL wiring for the Phase 1 trainer-dashboard JSON endpoints.

Mounted from `config/urls.py` at:
    /api/workouts/dashboard/

Kept separate from `urls.py` (the iOS-facing URLs) so the iOS API
surface is easy to audit.
"""
from django.urls import path

from .dashboard_api_views import (
    catalog_facets,
    catalog_search,
    day_add_exercise,
    day_delete_exercise,
    day_reorder_exercises,
    day_update_exercise,
    library_create_custom,
    library_list,
    library_snapshot_from_catalog,
    sync_wger_catalog,
)


urlpatterns = [
    path("catalog/", catalog_search, name="dashboard-api-catalog-search"),
    path("catalog/facets/", catalog_facets, name="dashboard-api-catalog-facets"),
    path("catalog/sync/", sync_wger_catalog, name="dashboard-api-catalog-sync"),

    path("library/", library_list, name="dashboard-api-library-list"),
    path(
        "library/custom/",
        library_create_custom,
        name="dashboard-api-library-create-custom",
    ),
    path(
        "library/snapshot/",
        library_snapshot_from_catalog,
        name="dashboard-api-library-snapshot",
    ),

    path(
        "day-exercises/",
        day_add_exercise,
        name="dashboard-api-day-add-exercise",
    ),
    path(
        "day-exercises/reorder/",
        day_reorder_exercises,
        name="dashboard-api-day-reorder-exercises",
    ),
    path(
        "day-exercises/<int:exercise_id>/",
        day_update_exercise,
        name="dashboard-api-day-update-exercise",
    ),
    path(
        "day-exercises/<int:exercise_id>/delete/",
        day_delete_exercise,
        name="dashboard-api-day-delete-exercise",
    ),
]
