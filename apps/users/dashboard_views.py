from .dashboard_auth_views import (
    landing_page,
    trainer_login_page,
    trainer_logout_page,
)

from .dashboard_page_views import (
    trainer_dashboard_home,
    trainer_nutrition_plans_page,
    trainer_settings_page,
)

from .dashboard_client_views import (
    trainer_dashboard,
    trainer_client_detail_page,
    dashboard_create_client,
    dashboard_assign_workout_plan,
)

from .dashboard_workout_views import (
    trainer_workout_plans_page,
    trainer_workout_plan_detail_page,
    dashboard_create_exercise_library_item,
    dashboard_create_workout_plan,
    dashboard_create_workout_day,
    dashboard_add_exercise_to_day,
)

__all__ = [
    "landing_page",
    "trainer_login_page",
    "trainer_logout_page",
    "trainer_dashboard_home",
    "trainer_dashboard",
    "trainer_client_detail_page",
    "trainer_workout_plans_page",
    "trainer_workout_plan_detail_page",
    "trainer_nutrition_plans_page",
    "trainer_settings_page",
    "dashboard_create_client",
    "dashboard_assign_workout_plan",
    "dashboard_create_exercise_library_item",
    "dashboard_create_workout_plan",
    "dashboard_create_workout_day",
    "dashboard_add_exercise_to_day",
]
