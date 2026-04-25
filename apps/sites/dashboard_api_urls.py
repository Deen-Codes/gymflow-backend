"""URL routing for the Phase 7 site editor JSON API."""
from django.urls import path

from . import views as v

urlpatterns = [
    path("site/",                                  v.site_update,            name="dashboard-site-update"),
    path("sections/",                              v.site_section_create,    name="dashboard-site-section-create"),
    path("sections/reorder/",                      v.site_sections_reorder,  name="dashboard-site-sections-reorder"),
    path("sections/<int:section_id>/",             v.site_section_update,    name="dashboard-site-section-update"),
    path("sections/<int:section_id>/delete/",      v.site_section_delete,    name="dashboard-site-section-delete"),
]
