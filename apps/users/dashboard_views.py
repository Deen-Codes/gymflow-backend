from .dashboard_auth_views import (
    landing_page,
    trainer_login_page,
    trainer_logout_page,
)

from .dashboard_page_views import (
    trainer_dashboard_home,
    trainer_settings_page,
)

from .dashboard_client_views import (
    trainer_dashboard,
    trainer_client_detail_page,
    dashboard_create_client,
    dashboard_delete_client,
    dashboard_assign_workout_plan,
    dashboard_assign_nutrition_plan,
)

from .dashboard_workout_page_views import (
    trainer_workout_plans_page,
    trainer_workout_plan_detail_page,
)

from .dashboard_workout_action_views import (
    dashboard_create_exercise_library_item,
    dashboard_update_exercise_library_item,
    dashboard_delete_exercise_library_item,
    dashboard_duplicate_exercise_library_item,
    dashboard_create_workout_plan,
    dashboard_duplicate_workout_plan,
    dashboard_update_workout_plan,
    dashboard_delete_workout_plan,
    dashboard_create_workout_day,
    dashboard_update_workout_day,
    dashboard_delete_workout_day,
    dashboard_add_exercise_to_day,
    dashboard_update_exercise,
    dashboard_delete_exercise,
)

from .dashboard_nutrition_page_views import (
    trainer_nutrition_plans_page,
    trainer_nutrition_plan_detail_page,
)

from .dashboard_nutrition_action_views import (
    dashboard_create_nutrition_plan,
    dashboard_update_nutrition_plan,
    dashboard_delete_nutrition_plan,
    dashboard_duplicate_nutrition_plan,
    dashboard_create_food_library_item,
    dashboard_update_food_library_item,
    dashboard_delete_food_library_item,
    dashboard_duplicate_food_library_item,
    dashboard_create_nutrition_meal,
    dashboard_update_nutrition_meal,
    dashboard_delete_nutrition_meal,
    dashboard_add_food_to_nutrition_meal,
    dashboard_delete_nutrition_meal_item,
)

from .dashboard_checkin_page_views import (
    trainer_checkin_forms_page,
    trainer_checkin_form_detail_page,
)

from .dashboard_checkin_action_views import (
    dashboard_create_checkin_form,
    dashboard_update_checkin_form,
    dashboard_delete_checkin_form,
    dashboard_create_checkin_question,
    dashboard_update_checkin_question,
    dashboard_delete_checkin_question,
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
    "trainer_nutrition_plan_detail_page",
    "trainer_checkin_forms_page",
    "trainer_checkin_form_detail_page",
    "trainer_settings_page",
    "dashboard_create_client",
    "dashboard_delete_client",
    "dashboard_assign_workout_plan",
    "dashboard_assign_nutrition_plan",
    "dashboard_create_exercise_library_item",
    "dashboard_update_exercise_library_item",
    "dashboard_delete_exercise_library_item",
    "dashboard_duplicate_exercise_library_item",
    "dashboard_create_workout_plan",
    "dashboard_duplicate_workout_plan",
    "dashboard_update_workout_plan",
    "dashboard_delete_workout_plan",
    "dashboard_create_workout_day",
    "dashboard_update_workout_day",
    "dashboard_delete_workout_day",
    "dashboard_add_exercise_to_day",
    "dashboard_update_exercise",
    "dashboard_delete_exercise",
    "dashboard_create_nutrition_plan",
    "dashboard_update_nutrition_plan",
    "dashboard_delete_nutrition_plan",
    "dashboard_duplicate_nutrition_plan",
    "dashboard_create_food_library_item",
    "dashboard_update_food_library_item",
    "dashboard_delete_food_library_item",
    "dashboard_duplicate_food_library_item",
    "dashboard_create_nutrition_meal",
    "dashboard_update_nutrition_meal",
    "dashboard_delete_nutrition_meal",
    "dashboard_add_food_to_nutrition_meal",
    "dashboard_delete_nutrition_meal_item",
    "dashboard_create_checkin_form",
    "dashboard_update_checkin_form",
    "dashboard_delete_checkin_form",
    "dashboard_create_checkin_question",
    "dashboard_update_checkin_question",
    "dashboard_delete_checkin_question",
]
