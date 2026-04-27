"""The 100-trophy catalogue.

This is the source of truth for which trophies exist. Used both by:
  * The data migration that seeds them on first deploy.
  * `apps.trophies.evaluators.EVALUATORS` which maps each `code` to
    a function that decides whether the trophy is earned.

Adding a new trophy = add a row here + add the matching code → evaluator
mapping in evaluators.py + ship a small data migration to upsert the
new row. Renaming copy or icons can be done from /admin without code.

Rarity distribution is intentional:
    Common ~30, Uncommon ~25, Rare ~25, Epic ~15, Legendary ~5
which produces a satisfying pyramid — most users earn the bottom tier
quickly, and only a handful ever unlock the legendary band.

Format: each tuple is (code, name, description, category, rarity, icon, sort_order).
SF Symbols icon names — chosen to be recognisable on iOS without
needing custom artwork upfront.
"""

# Rarity shorthands so the table stays readable.
_C, _U, _R, _E, _L = "common", "uncommon", "rare", "epic", "legendary"

TROPHY_CATALOGUE = [
    # ----- Workout Volume (15) -----
    ("first_workout",          "First Workout",         "You logged your first session. Welcome aboard.",        "workout_volume", _C, "figure.run",                 10),
    ("five_workouts",          "5 Workouts",            "Five sessions in the books.",                            "workout_volume", _C, "5.circle.fill",              20),
    ("ten_workouts",           "10 Workouts",           "Double digits.",                                         "workout_volume", _C, "10.circle.fill",             30),
    ("twentyfive_workouts",    "25 Workouts",           "A serious habit forming.",                               "workout_volume", _U, "25.circle.fill",             40),
    ("fifty_workouts",         "50 Workouts",           "Half a hundred.",                                        "workout_volume", _U, "50.circle.fill",             50),
    ("hundred_workouts",       "100 Workouts",          "Triple digits — you're a regular now.",                  "workout_volume", _R, "100.circle.fill",            60),
    ("twofifty_workouts",      "250 Workouts",          "Quarter of a thousand sessions.",                        "workout_volume", _R, "trophy.fill",                70),
    ("fivehundred_workouts",   "500 Workouts",          "Half-K club.",                                            "workout_volume", _E, "trophy.fill",                80),
    ("thousand_workouts",      "1,000 Workouts",        "One thousand sessions completed. Iron veteran.",         "workout_volume", _L, "crown.fill",                 90),
    ("first_thousand_kg",      "First 1,000 kg",        "You've moved a tonne of total weight.",                  "workout_volume", _C, "scalemass",                 100),
    ("ten_thousand_kg",        "10,000 kg Lifted",      "Ten tonnes of total volume.",                            "workout_volume", _U, "scalemass",                 110),
    ("fifty_thousand_kg",      "50,000 kg Lifted",      "Fifty tonnes. That's a small lorry.",                    "workout_volume", _R, "scalemass.fill",            120),
    ("hundred_thousand_kg",    "100,000 kg Lifted",     "One hundred tonnes lifted. Beast.",                       "workout_volume", _E, "scalemass.fill",            130),
    ("five_hundred_thousand_kg", "500,000 kg Lifted",  "Half a million kilos moved. Surreal.",                   "workout_volume", _L, "flame.fill",                140),
    ("million_kg_club",        "Million-Kilo Club",     "One million kilograms lifted across your career.",        "workout_volume", _L, "crown.fill",                150),

    # ----- Streaks (12) -----
    ("streak_3",               "3-Day Streak",          "Three days on-target.",                                   "streaks", _C, "flame",                            10),
    ("streak_7",               "7-Day Streak",          "A full week on-target.",                                  "streaks", _C, "flame",                            20),
    ("streak_14",              "14-Day Streak",         "Two weeks on-target. Rolling.",                           "streaks", _U, "flame.fill",                       30),
    ("streak_30",              "30-Day Streak",         "A whole month on-target.",                                "streaks", _R, "flame.fill",                       40),
    ("streak_60",              "60-Day Streak",         "Two months on-target.",                                   "streaks", _R, "flame.fill",                       50),
    ("streak_100",             "100-Day Streak",        "A hundred days on-target. Incredible discipline.",        "streaks", _E, "flame.fill",                       60),
    ("streak_200",             "200-Day Streak",        "200 days on-target.",                                     "streaks", _E, "flame.fill",                       70),
    ("streak_365",             "Year of Iron",          "365 days on-target. A full year.",                        "streaks", _L, "crown.fill",                       80),
    ("comeback",               "Comeback",              "Returned to training after a 7+ day break.",              "streaks", _U, "arrow.uturn.right",                90),
    ("phoenix",                "Phoenix",               "Lost a 30+ day streak and rebuilt it.",                   "streaks", _R, "sparkles",                        100),
    ("weekend_warrior",        "Weekend Warrior",       "Trained every weekend for a month.",                      "streaks", _R, "calendar.badge.checkmark",        110),
    ("iron_discipline",        "Iron Discipline",       "Hit your weekly target every week of a calendar month.", "streaks", _E, "shield.checkered",                120),

    # ----- Frequency (10) -----
    ("three_in_week",          "3 in a Week",           "Three sessions in a single week.",                        "frequency", _C, "3.square.fill",                  10),
    ("five_in_week",           "5 in a Week",           "Five sessions in a single week.",                         "frequency", _U, "5.square.fill",                  20),
    ("full_week",              "Full Week",             "Trained seven days in a row.",                            "frequency", _R, "checkmark.seal.fill",            30),
    ("twelve_in_month",        "12 in a Month",         "Twelve sessions in a calendar month.",                    "frequency", _U, "12.square.fill",                 40),
    ("twenty_in_month",        "20 in a Month",         "Twenty sessions in a calendar month.",                    "frequency", _R, "20.square.fill",                 50),
    ("thirty_in_month",        "30 in a Month",         "Thirty sessions in a calendar month.",                    "frequency", _E, "30.square.fill",                 60),
    ("two_a_day",              "Two-a-Day",             "Two sessions in a single day.",                           "frequency", _R, "2.square.fill",                  70),
    ("triple_threat",          "Triple Threat",         "Three sessions in a single day.",                         "frequency", _E, "bolt.fill",                      80),
    ("perfect_week",           "Perfect Week",          "Hit every workout your plan called for in a week.",       "frequency", _U, "checkmark.circle.fill",          90),
    ("perfect_month",          "Perfect Month",         "Hit every workout your plan called for, four weeks running.", "frequency", _E, "checkmark.seal.fill",       100),

    # ----- Personal Records (12) -----
    ("first_pr",               "First PR",              "First personal record.",                                  "personal_record", _C, "star",                      10),
    ("five_prs",               "5 PRs",                 "Five personal records under your belt.",                  "personal_record", _U, "star.fill",                 20),
    ("twentyfive_prs",         "25 PRs",                "Twenty-five PRs — you keep finding new ceilings.",         "personal_record", _R, "star.fill",                 30),
    ("hundred_prs",            "100 PRs",               "A hundred personal records.",                             "personal_record", _E, "star.circle.fill",          40),
    ("bench_bodyweight",       "Bench Bodyweight",      "Bench-pressed your own bodyweight for reps.",             "personal_record", _R, "figure.strengthtraining.traditional", 50),
    ("squat_1_5x",             "Squat 1.5x BW",         "Squatted 1.5x your bodyweight.",                          "personal_record", _E, "figure.strengthtraining.traditional", 60),
    ("deadlift_2x",            "Deadlift 2x BW",        "Deadlifted twice your bodyweight.",                       "personal_record", _L, "figure.strengthtraining.traditional", 70),
    ("ohp_bodyweight",         "OHP Bodyweight",        "Overhead-pressed your bodyweight.",                       "personal_record", _E, "figure.strengthtraining.traditional", 80),
    ("three_prs_session",      "Three PRs in a Session", "Three personal records in one workout.",                 "personal_record", _R, "star.leadinghalf.filled",    90),
    ("pr_three_weeks",         "PR Three Weeks Running", "New PR in three consecutive weeks.",                     "personal_record", _R, "calendar.circle.fill",      100),
    ("triple_digit",           "Triple-Digit Lift",     "First lift hitting 100 kg.",                              "personal_record", _R, "100.circle",                110),
    ("double_triple",          "Double Triple",         "First lift hitting 200 kg.",                              "personal_record", _E, "200.circle",                120),

    # ----- Reps & Sets (8) -----
    ("hundred_sets",           "100 Sets",              "One hundred sets completed across all sessions.",          "reps_sets", _C, "rectangle.stack",                 10),
    ("thousand_sets",          "1,000 Sets",            "One thousand sets logged.",                                "reps_sets", _U, "rectangle.stack.fill",            20),
    ("ten_thousand_sets",      "10,000 Sets",           "Ten thousand sets. That's a lot of bracing.",              "reps_sets", _E, "rectangle.stack.fill",            30),
    ("thousand_reps",          "1,000 Reps",            "One thousand reps total.",                                 "reps_sets", _C, "repeat",                          40),
    ("ten_thousand_reps",      "10,000 Reps",           "Ten thousand reps total.",                                 "reps_sets", _U, "repeat",                          50),
    ("centurion",              "Centurion",             "100,000 total reps. Body of work.",                        "reps_sets", _L, "repeat.circle.fill",              60),
    ("hundred_reps_exercise",  "Hundred-Rep Hero",      "100 reps of a single exercise in one session.",            "reps_sets", _R, "100.circle.fill",                 70),
    ("five_thousand_session",  "5,000 kg in a Session", "Moved 5,000 kg of total volume in one workout.",          "reps_sets", _R, "scalemass.fill",                  80),

    # ----- Time-of-Day & Special Days (12) -----
    ("early_bird",             "Early Bird",            "Workout finished before 6am.",                             "time_special", _U, "sunrise.fill",                10),
    ("night_owl",              "Night Owl",             "Workout finished after 10pm.",                             "time_special", _U, "moon.stars.fill",             20),
    ("midnight_iron",          "Midnight Iron",         "Logged a session at exactly 00:00.",                       "time_special", _R, "moon.fill",                   30),
    ("lunch_hero",             "Lunch Hour Hero",       "Squeezed a session in between 12:00 and 13:00.",           "time_special", _C, "fork.knife",                  40),
    ("sunday_soldier",         "Sunday Soldier",        "Trained on a Sunday.",                                     "time_special", _C, "calendar",                    50),
    ("monday_motivated",       "Monday Motivated",      "Trained on Monday, four weeks running.",                   "time_special", _U, "calendar.badge.exclamationmark", 60),
    ("christmas_day",          "Christmas Iron",        "Trained on Christmas Day.",                                "time_special", _E, "gift.fill",                   70),
    ("new_years_day",          "New Year, New Reps",    "Trained on January 1st.",                                  "time_special", _R, "sparkles",                    80),
    ("birthday_workout",       "Birthday Workout",      "Trained on your birthday.",                                "time_special", _R, "birthday.cake.fill",          90),
    ("quick_finisher",         "Quick Finisher",        "Workout completed in under 30 minutes.",                   "time_special", _C, "timer",                      100),
    ("endurance_test",         "Endurance Test",        "90+ minute workout.",                                      "time_special", _U, "stopwatch.fill",             110),
    ("two_hour_beast",         "Two-Hour Beast",        "120+ minute workout.",                                     "time_special", _R, "hourglass.tophalf.filled",   120),

    # ----- Check-ins & Progress (12) -----
    ("first_checkin",          "First Check-in",        "Submitted your first check-in.",                           "check_ins", _C, "checklist",                    10),
    ("ten_checkins",           "10 Check-ins",          "Ten check-ins submitted.",                                 "check_ins", _C, "checklist",                    20),
    ("twentyfive_checkins",    "25 Check-ins",          "Twenty-five check-ins.",                                   "check_ins", _U, "checklist",                    30),
    ("fifty_checkins",         "50 Check-ins",          "Fifty check-ins.",                                         "check_ins", _R, "checklist.checked",            40),
    ("hundred_checkins",       "100 Check-ins",         "One hundred check-ins.",                                   "check_ins", _E, "checklist.checked",            50),
    ("first_photo",            "First Progress Photo",  "First photo submitted with a check-in.",                   "check_ins", _C, "camera",                       60),
    ("photo_comparison",       "4-Week Comparison",     "Submitted progress photos in two check-ins 4 weeks apart.", "check_ins", _U, "camera.fill",                70),
    ("onboarding_complete",    "Onboarded",             "Submitted your onboarding form.",                           "check_ins", _C, "person.badge.plus",            80),
    ("four_weekly_streak",     "4 Weekly Check-ins in a Row", "Four weekly check-ins, no gaps.",                    "check_ins", _U, "calendar.badge.checkmark",     90),
    ("thirty_daily_streak",    "30 Daily Check-ins in a Row", "Thirty daily check-ins, no gaps.",                   "check_ins", _E, "calendar.badge.checkmark",    100),
    ("spotless_month",         "Spotless Month",        "Every check-in submitted on time for 30 days straight.",   "check_ins", _E, "checkmark.seal.fill",         110),
    ("one_year_client",        "Client of the Year",    "One year of consistent check-ins with your trainer.",      "check_ins", _L, "crown.fill",                  120),

    # ----- Nutrition & Hydration (12) -----
    ("first_meal_logged",      "First Meal Logged",     "Logged your first meal.",                                  "nutrition", _C, "fork.knife",                   10),
    ("full_day_logged",        "Full Day Logged",       "Logged every meal in a single day.",                       "nutrition", _C, "calendar.circle",              20),
    ("seven_days_logged",      "7 Days of Logging",     "Logged every meal for seven days running.",                "nutrition", _U, "calendar.circle.fill",         30),
    ("thirty_days_logged",     "30 Days of Logging",    "Logged every meal for thirty days running.",               "nutrition", _R, "calendar.circle.fill",         40),
    ("hundred_meals",          "100 Meals Logged",      "One hundred meals logged.",                                "nutrition", _U, "list.bullet.clipboard",        50),
    ("thousand_meals",         "1,000 Meals Logged",    "One thousand meals logged.",                               "nutrition", _E, "list.bullet.clipboard.fill",   60),
    ("macro_hit_day",          "Macro Hit",             "Hit calorie + protein targets in a single day.",           "nutrition", _C, "target",                       70),
    ("macro_week",             "Macro Week",            "Hit macros every day for a week.",                         "nutrition", _R, "target",                       80),
    ("macro_month",            "Macro Month",           "Hit macros every day for thirty days.",                    "nutrition", _E, "target",                       90),
    ("eight_cups_day",         "8 Cups in a Day",       "Hit your hydration goal in a day.",                        "nutrition", _C, "drop.fill",                   100),
    ("seven_day_hydration",    "7-Day Hydration",       "Hit hydration goal seven days running.",                   "nutrition", _U, "drop.fill",                   110),
    ("hundred_days_hydrated",  "100 Days Hydrated",     "Hit hydration goal one hundred days.",                     "nutrition", _R, "drop.circle.fill",            120),

    # ----- Body Composition (7) -----
    ("first_weight_logged",    "First Weight Logged",   "Logged your weight for the first time.",                    "body", _C, "scalemass",                          10),
    ("lost_2_5",               "Lost 2.5 kg",           "Down 2.5 kg from your starting weight.",                    "body", _U, "arrow.down.circle.fill",             20),
    ("lost_5",                 "Lost 5 kg",             "Down 5 kg.",                                                "body", _R, "arrow.down.circle.fill",             30),
    ("lost_10",                "Lost 10 kg",            "Down 10 kg.",                                               "body", _E, "arrow.down.circle.fill",             40),
    ("lost_20",                "Lost 20 kg",            "Down 20 kg. Major transformation.",                         "body", _L, "arrow.down.circle.fill",             50),
    ("reached_goal_weight",    "Goal Weight Reached",   "Hit your trainer-set goal weight.",                         "body", _L, "flag.checkered",                     60),
    ("six_month_transform",    "6-Month Transformation", "Six months of consistent weight tracking.",               "body", _E, "calendar",                           70),
]


def assert_codes_unique():
    """Sanity check used by the migration — fails loudly if anyone
    accidentally duplicates a code while editing the catalogue."""
    seen = set()
    for entry in TROPHY_CATALOGUE:
        code = entry[0]
        if code in seen:
            raise ValueError(f"Duplicate trophy code in seed: {code}")
        seen.add(code)
    return True
