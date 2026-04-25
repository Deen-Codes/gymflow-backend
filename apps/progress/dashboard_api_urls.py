"""URL routing for the Phase 4 check-ins dashboard JSON endpoints."""
from django.urls import path

from . import dashboard_api_views as v

urlpatterns = [
    path("forms/",                          v.form_list,        name="dashboard-checkin-forms"),
    path("questions/",                      v.question_add,     name="dashboard-checkin-question-add"),
    path("questions/reorder/",              v.question_reorder, name="dashboard-checkin-question-reorder"),
    path("questions/<int:question_id>/",    v.question_update,  name="dashboard-checkin-question-update"),
    path("questions/<int:question_id>/delete/", v.question_delete, name="dashboard-checkin-question-delete"),
    path("submissions/",                    v.submission_list,  name="dashboard-checkin-submissions"),

    # Phase 4.5 — per-client assignments + cadence
    path("client-assignments/",             v.client_assignment_list, name="dashboard-checkin-assignments-list"),
    path("client-assignments/set/",         v.client_assignment_set,  name="dashboard-checkin-assignments-set"),
]
