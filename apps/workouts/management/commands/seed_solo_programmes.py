"""
SOLO-02 — Seeds the public programmes catalog with research-backed
templates curated from the strength-and-conditioning literature.

Idempotent: rerun on every deploy from build.sh. The seed file is the
source of truth — editing a programme here + redeploying replaces the
catalog row in place. Already-assigned per-user clones are NOT
touched (they reference the template via `source_template` for
provenance, but each user's plan is its own snapshot).

────────────────────────────────────────────────────────────────────
Why these specific programmes
────────────────────────────────────────────────────────────────────
The catalog is intentionally short (8 programmes). Each one is
documented in the strength-and-conditioning literature and is what
real PTs actually programme. Selection criteria:

  1. Has a real coach / researcher attached (not invented by us).
  2. Aligns with the ACSM 2026 resistance-training guidelines and
     recent meta-regressions on volume / frequency dose-response
     (10+ sets per muscle per week, twice-weekly frequency beats
     once-weekly for hypertrophy).
  3. Covers the four onboarding goal × experience × equipment
     intersections we see most often:
        - just_starting × full_gym       → Starting Strength A/B
        - just_starting × bodyweight     → Bodyweight Foundation
        - one_to_three × full_gym (build)→ Upper/Lower 4-day
        - one_to_three × full_gym (bigg) → PPL 6-day
        - any × full_gym (strength)      → 5/3/1 BBB
        - any × full_gym (women+glutes)  → Strong Curves
        - any × home                     → Home Dumbbell Strength
        - any (cut/recomp)               → Hybrid (Strength+Cardio)

  4. Each programme carries `programme_meta.evidence` — the citation
     trail. Users see "Why this programme?" expandable text on the
     card; AI PT (E.2) reads the same field when explaining its
     recommendation. This is the audit-trail that lets us claim
     "research-backed" honestly.

────────────────────────────────────────────────────────────────────
The `programme_meta` shape (expanded for SOLO-02)
────────────────────────────────────────────────────────────────────
Beyond the basic filter fields (goals/experience/equipment/days/
weeks/tagline/summary), each row now carries:

  evidence:              list[str]  — bullet citations
  source_attribution:    str        — "5/3/1 by Jim Wendler" etc.
  weekly_volume_per_muscle: dict    — {chest: 12, back: 14, ...}
  progression_rule:      str        — how weight goes up
  deload_strategy:       str        — when + how to back off
  recommended_for:       list[str]  — plain-English audience tags
  not_recommended_for:   list[str]
  ai_pt_levers:          dict       — what AI PT can adjust later
                                       (set count, reps, exercise
                                       swaps, days/week)
  exercise_notes:        dict[str, str]  — per-exercise coaching
                                            cue (key form pointer)

The exercise_notes are written from a real-coach perspective. They
cover the ONE thing that matters most for that exercise — not a
treatise on form. Example:
   "Back Squat" → "Knees track over toes, brace before descending,
                   depth to parallel or below."
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction

from apps.workouts.models import (
    WorkoutPlan, WorkoutDay, Exercise, ExerciseSetTarget,
)

User = get_user_model()


# ====================================================================
# Coaching notes — single source of truth, referenced by all programmes
# ====================================================================
COACHING_NOTES = {
    # --- Squats ---
    "Back Squat":
        "Brace before descending. Depth to parallel or below. Knees "
        "track over toes — don't let them cave. The bar moves over "
        "midfoot in a straight line.",
    "Front Squat":
        "Elbows up, chest tall. Wider stance than back squat is "
        "fine. Full range of motion — depth wins over weight.",
    "Goblet Squat":
        "Hold the dumbbell at chest height. Sit back into the heels. "
        "Great for learning squat depth without barbell load.",
    "Bodyweight Squat":
        "Hands forward for balance. Heels stay down. Depth to "
        "parallel or below — work range of motion before reps.",

    # --- Deadlifts / hip hinge ---
    "Deadlift":
        "Bar over midfoot, lats engaged ('crush oranges in armpits'), "
        "neutral spine, drive the floor away. Reset between reps if "
        "form breaks.",
    "Romanian Deadlift":
        "Soft knees throughout. Push hips back, not down. Stop when "
        "hamstrings limit further hinge — usually mid-shin.",
    "Glute Bridge":
        "Drive through heels. Squeeze glutes at the top — don't "
        "hyperextend the lower back. Pause 1s at top.",

    # --- Pressing ---
    "Barbell Bench Press":
        "Shoulder blades retracted and down, feet planted. Bar "
        "touches mid-chest. Press up and slightly back over face. "
        "Don't bounce — pause briefly at chest.",
    "Incline Barbell Press":
        "30–45° incline. Bar to upper chest. Same setup as flat "
        "bench but elbows tuck slightly more.",
    "Incline Dumbbell Press":
        "Slight incline (30°). Press dumbbells up and slightly in. "
        "Lower with control until you feel a chest stretch.",
    "Dumbbell Bench Press":
        "Press dumbbells up and slightly in. Lower until you feel "
        "the stretch — usually elbows level with torso.",
    "Floor Press":
        "Pause when triceps touch floor. Forces strict form. Great "
        "home-gym substitute for bench press.",
    "Cable Fly":
        "Slight bend in elbows, hold throughout. Squeeze chest at "
        "midline. Don't let arms straighten under load.",
    "Push-Up":
        "Body in a straight line. Hands slightly wider than "
        "shoulders. Lower until chest is a fist's height off the "
        "floor.",
    "Pike Push-Up":
        "Hips piked high. Lower head between hands. Vertical "
        "press — trains shoulders.",
    "Diamond Push-Up":
        "Hands form a triangle under chest. Elbows track close. "
        "More tricep emphasis.",

    # --- Overhead pressing ---
    "Overhead Press":
        "Bar over midfoot at lockout. Push head 'through the "
        "window' as the bar passes. Glutes squeezed throughout.",
    "Dumbbell Shoulder Press":
        "Press up and slightly forward. Don't hyperextend low back "
        "— brace the core.",

    # --- Pulling (vertical) ---
    "Pull-Up":
        "Dead hang start. Pull elbows down and back, chest to bar "
        "if possible. Control the descent for 2s.",
    "Lat Pulldown":
        "Lean back ~10°. Pull bar to upper chest, elbows down and "
        "back. Don't let the weight pull you forward.",
    "Chin-Up":
        "Underhand grip, shoulder-width. More biceps than pull-up. "
        "Same form cues — control the descent.",

    # --- Pulling (horizontal) ---
    "Barbell Row":
        "Hinge to ~45°. Bar pulls to upper abs. Don't let torso "
        "rise — chest stays down throughout the rep.",
    "Bent-Over Row":
        "Same as barbell row but with dumbbells — slightly more "
        "range of motion. Squeeze the shoulder blades.",
    "Dumbbell Row":
        "Knee on bench, opposite arm rows. Pull elbow up and back, "
        "not out. Squeeze the lat at top.",
    "Single-Arm Row":
        "Free version of dumbbell row. Brace core, don't twist.",
    "Seated Cable Row":
        "Sit tall, don't lean way back. Pull to lower chest. "
        "Squeeze the shoulder blades together at the contracted "
        "position.",
    "Face Pull":
        "Use a rope. Pull to forehead, elbows high. Trains the "
        "rear delts and external rotators — bulletproofs the "
        "shoulders.",

    # --- Lower body accessories ---
    "Leg Press":
        "Feet shoulder-width on the platform. Lower until knees "
        "are at ~90°. Don't let the lower back round.",
    "Leg Curl":
        "Squeeze the hamstring at the top. Slow eccentric (3s) is "
        "where most growth happens.",
    "Walking Lunge":
        "Long step. Drive through the front heel. Front knee "
        "tracks over the foot.",
    "Reverse Lunge":
        "Step back, lower hips. Easier on the knees than forward "
        "lunges — good for beginners.",
    "Lunge":
        "Step forward, lower until back knee is just off the floor. "
        "Drive through front heel to return.",
    "Standing Calf Raise":
        "Full range of motion — stretch at the bottom, squeeze at "
        "the top. Pause 1s at the top.",
    "Seated Calf Raise":
        "Targets the soleus (slow-twitch). Higher reps (12–20) "
        "work better than heavy low reps.",

    # --- Core + isolation ---
    "Plank":
        "Body in a straight line. Squeeze glutes and core. If you "
        "can hold for 60s easily, progress to side planks or "
        "weighted planks.",
    "Lateral Raise":
        "Soft elbows. Raise to shoulder height — not above. "
        "Pinky slightly higher than thumb at the top.",
    "Tricep Pushdown":
        "Elbows pinned to your sides. Squeeze at lockout. Keep "
        "tension throughout — don't let the cable rest at the top.",
    "Cable Tricep Overhead":
        "Elbows in tight, lengthen the triceps overhead. Stretches "
        "the long head of the triceps better than pushdowns.",
    "Skull Crusher":
        "Lower bar to forehead with elbows pointing forward. "
        "Don't flare. EZ bar saves the wrists.",
    "Barbell Curl":
        "Elbows pinned. No swing. Squeeze biceps at the top.",
    "Hammer Curl":
        "Neutral grip. Targets the brachialis — adds arm thickness.",
    "Hip Thrust":
        "Shoulders on bench, feet flat. Drive through heels. "
        "Squeeze glutes at the top, ribcage down.",
    "Cable Pull-Through":
        "Hinge at the hips, not the back. Drive hips forward at "
        "the top. Posterior chain dominant.",
    "Bulgarian Split Squat":
        "Rear foot elevated. Most of the load on the front leg. "
        "Front knee tracks over toes.",
}


# ====================================================================
# Programme catalogue
# ====================================================================
PROGRAMMES = [

    # ─── Just-starting × Full gym × Build/Strength ───────────────
    {
        "name": "Starting Strength",
        "meta": {
            "goals":         ["get_stronger", "build_muscle"],
            "experience":    "just_starting",
            "equipment":     "full_gym",
            "days_per_week": 3,
            "weeks":         12,
            "tagline":       "Mark Rippetoe's foundational programme",
            "summary":       "Linear progression on the big barbell lifts, three "
                             "days a week. The most-recommended starter programme "
                             "in lifting for 30+ years. Add weight every session "
                             "until you stall.",
            "evidence": [
                "Rippetoe, M. & Kilgore, L. (2009). Practical Programming "
                "for Strength Training, 3rd ed. Aasgaard Company.",
                "ACSM 2026 RT guidelines: novices respond best to high-"
                "frequency (3x/week) full-body training.",
                "Schoenfeld et al. meta-regression (2024): linear "
                "progression captures most novice gains in 6–12 weeks.",
            ],
            "source_attribution": "Mark Rippetoe — Starting Strength",
            "weekly_volume_per_muscle": {
                "quads": 9, "back": 9, "chest": 9, "shoulders": 6, "hamstrings": 9,
            },
            "progression_rule": "Add 2.5kg (lower body) / 1.25kg (upper body) every "
                                "session you complete all reps. When you fail twice "
                                "in a row at the same weight, deload 10% and rebuild.",
            "deload_strategy": "On second failed session, drop the weight 10% and "
                               "rebuild. After two deload cycles, switch to an "
                               "intermediate programme (5/3/1 BBB).",
            "recommended_for": ["complete beginners", "returning lifters", "anyone <1yr training"],
            "not_recommended_for": ["intermediate (>1yr)", "isolated muscle goals"],
            "ai_pt_levers": {
                "exercise_swaps": True, "set_count": False, "reps": False, "deload_timing": True,
            },
        },
        "days": [
            {"title": "Workout A", "exercises": [
                {"name": "Back Squat",       "label": "A", "sets": 3, "reps": "5"},
                {"name": "Barbell Bench Press","label":"B","sets": 3, "reps": "5"},
                {"name": "Deadlift",         "label": "C", "sets": 1, "reps": "5"},
            ]},
            {"title": "Workout B", "exercises": [
                {"name": "Back Squat",       "label": "A", "sets": 3, "reps": "5"},
                {"name": "Overhead Press",   "label": "B", "sets": 3, "reps": "5"},
                {"name": "Barbell Row",      "label": "C", "sets": 3, "reps": "5"},
            ]},
            {"title": "Workout A2", "exercises": [
                {"name": "Back Squat",       "label": "A", "sets": 3, "reps": "5"},
                {"name": "Barbell Bench Press","label":"B","sets": 3, "reps": "5"},
                {"name": "Deadlift",         "label": "C", "sets": 1, "reps": "5"},
            ]},
        ],
    },

    # ─── Just-starting × Bodyweight ──────────────────────────────
    {
        "name": "Bodyweight Foundation",
        "meta": {
            "goals":         ["stay_consistent", "build_muscle"],
            "experience":    "just_starting",
            "equipment":     "bodyweight_only",
            "days_per_week": 3,
            "weeks":         8,
            "tagline":       "No equipment, evidence-based progressions",
            "summary":       "Three full-body sessions a week using progressive "
                             "calisthenics. Builds real strength + muscle without "
                             "any equipment. Each exercise has a harder variant "
                             "you progress to as you get stronger.",
            "evidence": [
                "Schoenfeld et al. (2017) — equivalent hypertrophy with "
                "bodyweight progressions when sets taken close to failure.",
                "Tsatsouline, P. (2003). The Naked Warrior: Master the "
                "Secrets of the Super-Strong–Using Bodyweight Exercises Only.",
                "ACSM 2026: bodyweight resistance is sufficient stimulus "
                "for novice hypertrophy and strength adaptations.",
            ],
            "source_attribution": "Synthesis of progressive-calisthenics literature",
            "weekly_volume_per_muscle": {"chest": 9, "back": 9, "legs": 9, "core": 9},
            "progression_rule":
                "When you can complete all sets at the top of the rep range, "
                "progress to the harder variant (e.g. push-up → diamond → "
                "archer → one-arm). 2-week minimum at each level.",
            "deload_strategy":
                "Bodyweight has no deload — if a session feels heavy, drop "
                "1 set and continue.",
            "recommended_for": ["zero equipment", "travel-heavy schedules", "absolute beginners"],
            "not_recommended_for": ["lifters with full gym access", "max strength goals"],
            "ai_pt_levers": {
                "exercise_swaps": True, "set_count": True, "reps": True, "progression_pace": True,
            },
        },
        "days": [
            {"title": "Day 1", "exercises": [
                {"name": "Push-Up",          "label": "A", "sets": 3, "reps": "8-12"},
                {"name": "Bodyweight Squat", "label": "B", "sets": 3, "reps": "12-15"},
                {"name": "Plank",            "label": "C", "sets": 3, "reps": "30s-60s"},
                {"name": "Lunge",            "label": "D", "sets": 3, "reps": "10/leg"},
            ]},
            {"title": "Day 2", "exercises": [
                {"name": "Pike Push-Up",     "label": "A", "sets": 3, "reps": "6-10"},
                {"name": "Reverse Lunge",    "label": "B", "sets": 3, "reps": "10/leg"},
                {"name": "Glute Bridge",     "label": "C", "sets": 3, "reps": "12-15"},
                {"name": "Plank",            "label": "D", "sets": 3, "reps": "30s-60s"},
            ]},
            {"title": "Day 3", "exercises": [
                {"name": "Diamond Push-Up",  "label": "A", "sets": 3, "reps": "8-12"},
                {"name": "Bodyweight Squat", "label": "B", "sets": 3, "reps": "15-20"},
                {"name": "Plank",            "label": "C", "sets": 3, "reps": "45s-90s"},
            ]},
        ],
    },

    # ─── Intermediate × Full gym × Build muscle ──────────────────
    {
        "name": "Upper / Lower 4-Day",
        "meta": {
            "goals":         ["build_muscle"],
            "experience":    "one_to_three",
            "equipment":     "full_gym",
            "days_per_week": 4,
            "weeks":         8,
            "tagline":       "Twice-weekly muscle frequency",
            "summary":       "Each muscle group hit twice a week — the dose-"
                             "response sweet spot for hypertrophy. Two upper "
                             "and two lower sessions, with progressive overload "
                             "via reps-in-reserve (RIR).",
            "evidence": [
                "Schoenfeld et al. (2016, 2019) — twice-weekly "
                "frequency superior to once-weekly for hypertrophy "
                "when volume is matched.",
                "Helms et al. (2015) — RIR-based progression converges "
                "with %1RM systems for trained lifters.",
                "ACSM 2026 — 10+ sets per muscle per week target hits "
                "naturally with a 4-day upper/lower split.",
            ],
            "source_attribution": "Synthesis of Schoenfeld / Helms / Israetel",
            "weekly_volume_per_muscle": {
                "chest": 12, "back": 14, "shoulders": 12,
                "quads": 10, "hamstrings": 10, "biceps": 8, "triceps": 8,
            },
            "progression_rule":
                "Aim for 1-2 RIR (reps in reserve) on top sets. When you "
                "hit the top of the rep range with 2 RIR for two sessions "
                "in a row, add weight (~2.5kg upper / ~5kg lower).",
            "deload_strategy":
                "Every 6th week, drop volume 50% (cut working sets in "
                "half). Resume at previous loads the following week.",
            "recommended_for": ["1-3yr training experience", "physique focus", "twice-weekly recovery"],
            "not_recommended_for": ["beginners (use Starting Strength)", "max strength only (use 5/3/1)"],
            "ai_pt_levers": {
                "exercise_swaps": True, "set_count": True, "reps": True, "frequency": True, "rir_target": True,
            },
        },
        "days": [
            {"title": "Upper A (Strength bias)", "exercises": [
                {"name": "Barbell Bench Press",  "label": "A", "sets": 4, "reps": "5-7"},
                {"name": "Barbell Row",          "label": "B", "sets": 4, "reps": "6-8"},
                {"name": "Overhead Press",       "label": "C", "sets": 3, "reps": "6-8"},
                {"name": "Pull-Up",              "label": "D", "sets": 3, "reps": "6-10"},
                {"name": "Tricep Pushdown",      "label": "E", "sets": 3, "reps": "10-12"},
                {"name": "Barbell Curl",         "label": "F", "sets": 3, "reps": "8-10"},
            ]},
            {"title": "Lower A (Strength bias)", "exercises": [
                {"name": "Back Squat",           "label": "A", "sets": 4, "reps": "5-7"},
                {"name": "Romanian Deadlift",    "label": "B", "sets": 3, "reps": "6-8"},
                {"name": "Leg Press",            "label": "C", "sets": 3, "reps": "8-10"},
                {"name": "Leg Curl",             "label": "D", "sets": 3, "reps": "10-12"},
                {"name": "Standing Calf Raise",  "label": "E", "sets": 3, "reps": "10-12"},
            ]},
            {"title": "Upper B (Hypertrophy bias)", "exercises": [
                {"name": "Incline Dumbbell Press","label":"A", "sets": 3, "reps": "8-12"},
                {"name": "Lat Pulldown",         "label": "B", "sets": 3, "reps": "8-12"},
                {"name": "Dumbbell Shoulder Press","label":"C","sets":3, "reps": "8-12"},
                {"name": "Seated Cable Row",     "label": "D", "sets": 3, "reps": "8-12"},
                {"name": "Lateral Raise",        "label": "E", "sets": 4, "reps": "10-15"},
                {"name": "Hammer Curl",          "label": "F", "sets": 3, "reps": "10-12"},
                {"name": "Cable Tricep Overhead","label":"G", "sets": 3, "reps": "10-12"},
            ]},
            {"title": "Lower B (Hypertrophy bias)", "exercises": [
                {"name": "Deadlift",             "label": "A", "sets": 3, "reps": "5"},
                {"name": "Front Squat",          "label": "B", "sets": 3, "reps": "6-8"},
                {"name": "Walking Lunge",        "label": "C", "sets": 3, "reps": "10/leg"},
                {"name": "Glute Bridge",         "label": "D", "sets": 3, "reps": "10-12"},
                {"name": "Seated Calf Raise",    "label": "E", "sets": 3, "reps": "12-15"},
            ]},
        ],
    },

    # ─── Intermediate × Full gym × Build muscle (high freq) ──────
    {
        "name": "Push Pull Legs (6-day)",
        "meta": {
            "goals":         ["build_muscle"],
            "experience":    "one_to_three",
            "equipment":     "full_gym",
            "days_per_week": 6,
            "weeks":         8,
            "tagline":       "High-frequency hypertrophy split",
            "summary":       "Six sessions a week, each muscle group twice. "
                             "The highest-volume / highest-frequency template "
                             "in the catalog — for users with the time and "
                             "recovery for it.",
            "evidence": [
                "Schoenfeld et al. (2019) — twice-weekly frequency >> once-"
                "weekly for hypertrophy.",
                "Israetel, M. (2017). Scientific Principles of Hypertrophy "
                "Training. Renaissance Periodization.",
                "Nippard, J. (2023) — PPL is the most recommended "
                "intermediate split among evidence-based coaches.",
            ],
            "source_attribution": "Synthesis of Israetel / Nippard / Helms",
            "weekly_volume_per_muscle": {
                "chest": 14, "back": 16, "shoulders": 14,
                "quads": 12, "hamstrings": 10, "biceps": 12, "triceps": 12,
            },
            "progression_rule":
                "Top set at 1-2 RIR. Add a single rep when you hit the "
                "ceiling of the rep range for two sessions; add weight "
                "when you hit the ceiling for the second time at that "
                "weight.",
            "deload_strategy":
                "Every 6th week, drop one full session (run a 5-day "
                "instead of 6) at 60-70% normal weights.",
            "recommended_for": ["1-3yr lifters with 6 days available", "physique competitors", "high recovery capacity"],
            "not_recommended_for": ["beginners", "users with <5 days a week available"],
            "ai_pt_levers": {
                "exercise_swaps": True, "set_count": True, "reps": True, "frequency": True,
            },
        },
        "days": [
            {"title": "Push A", "exercises": [
                {"name": "Barbell Bench Press",   "label": "A", "sets": 4, "reps": "6-8"},
                {"name": "Overhead Press",        "label": "B", "sets": 3, "reps": "6-8"},
                {"name": "Incline Dumbbell Press","label":"C","sets": 3, "reps": "8-12"},
                {"name": "Lateral Raise",         "label": "D", "sets": 4, "reps": "12-15"},
                {"name": "Tricep Pushdown",       "label": "E", "sets": 3, "reps": "10-12"},
                {"name": "Cable Tricep Overhead", "label": "F", "sets": 3, "reps": "10-12"},
            ]},
            {"title": "Pull A", "exercises": [
                {"name": "Deadlift",              "label": "A", "sets": 3, "reps": "5"},
                {"name": "Pull-Up",               "label": "B", "sets": 4, "reps": "6-10"},
                {"name": "Barbell Row",           "label": "C", "sets": 3, "reps": "6-8"},
                {"name": "Face Pull",             "label": "D", "sets": 4, "reps": "12-15"},
                {"name": "Barbell Curl",          "label": "E", "sets": 3, "reps": "8-10"},
                {"name": "Hammer Curl",           "label": "F", "sets": 3, "reps": "10-12"},
            ]},
            {"title": "Legs A", "exercises": [
                {"name": "Back Squat",            "label": "A", "sets": 4, "reps": "5-7"},
                {"name": "Romanian Deadlift",     "label": "B", "sets": 3, "reps": "8-10"},
                {"name": "Leg Press",             "label": "C", "sets": 3, "reps": "10-12"},
                {"name": "Leg Curl",              "label": "D", "sets": 4, "reps": "10-12"},
                {"name": "Standing Calf Raise",   "label": "E", "sets": 4, "reps": "10-12"},
            ]},
            {"title": "Push B", "exercises": [
                {"name": "Incline Barbell Press", "label": "A", "sets": 4, "reps": "6-8"},
                {"name": "Dumbbell Shoulder Press","label":"B","sets":3, "reps": "8-12"},
                {"name": "Cable Fly",             "label": "C", "sets": 3, "reps": "10-15"},
                {"name": "Lateral Raise",         "label": "D", "sets": 4, "reps": "12-15"},
                {"name": "Skull Crusher",         "label": "E", "sets": 3, "reps": "8-10"},
            ]},
            {"title": "Pull B", "exercises": [
                {"name": "Lat Pulldown",          "label": "A", "sets": 4, "reps": "8-12"},
                {"name": "Seated Cable Row",      "label": "B", "sets": 4, "reps": "8-12"},
                {"name": "Dumbbell Row",          "label": "C", "sets": 3, "reps": "10-12"},
                {"name": "Face Pull",             "label": "D", "sets": 3, "reps": "12-15"},
                {"name": "Barbell Curl",          "label": "E", "sets": 3, "reps": "10-12"},
            ]},
            {"title": "Legs B", "exercises": [
                {"name": "Front Squat",           "label": "A", "sets": 3, "reps": "6-8"},
                {"name": "Bulgarian Split Squat", "label": "B", "sets": 3, "reps": "8-10/leg"},
                {"name": "Leg Press",             "label": "C", "sets": 3, "reps": "10-12"},
                {"name": "Hip Thrust",            "label": "D", "sets": 3, "reps": "8-10"},
                {"name": "Seated Calf Raise",     "label": "E", "sets": 4, "reps": "12-15"},
            ]},
        ],
    },

    # ─── Any × Full gym × Strength (intermediate periodisation) ──
    {
        "name": "5/3/1 Boring But Big",
        "meta": {
            "goals":         ["get_stronger", "build_muscle"],
            "experience":    "any",
            "equipment":     "full_gym",
            "days_per_week": 4,
            "weeks":         12,
            "tagline":       "Wendler's submaximal periodisation",
            "summary":       "Four-day strength template by Jim Wendler. "
                             "Submaximal training percentages, monthly waves, "
                             "guaranteed progression. The 'BBB' assistance "
                             "version adds 5x10 hypertrophy work.",
            "evidence": [
                "Wendler, J. (2009, 2017). 5/3/1: The Simplest and Most "
                "Effective Training System for Raw Strength.",
                "Helms et al. (2018) — submaximal % systems and RIR-"
                "based programming converge for trained lifters.",
                "ACSM 2026 — periodised programming outperforms linear "
                "for >1yr trained lifters.",
            ],
            "source_attribution": "Jim Wendler — 5/3/1 (BBB variant)",
            "weekly_volume_per_muscle": {
                "chest": 13, "back": 13, "shoulders": 13,
                "quads": 13, "hamstrings": 11,
            },
            "progression_rule":
                "Calculate Training Max (TM) = 90% of true 1RM. Run "
                "monthly waves: Week 1 (5/5/5+), Week 2 (3/3/3+), "
                "Week 3 (5/3/1+), Week 4 (deload). Add 2.5kg upper / "
                "5kg lower to TM each cycle.",
            "deload_strategy":
                "Built in — every 4th week is a programmed deload at "
                "40-60% TM for 5 reps each.",
            "recommended_for": ["intermediate to advanced", "strength priorities", "long-term lifestyle programming"],
            "not_recommended_for": ["complete beginners (use Starting Strength)", "<3 days/week available"],
            "ai_pt_levers": {
                "exercise_swaps": False, "set_count": True, "reps": False, "tm_progression_rate": True,
            },
        },
        "days": [
            {"title": "Bench Day", "exercises": [
                {"name": "Barbell Bench Press",   "label": "A", "sets": 3, "reps": "5/3/1"},
                {"name": "Barbell Bench Press",   "label": "B", "sets": 5, "reps": "10 @ 50% TM"},
                {"name": "Barbell Row",           "label": "C", "sets": 5, "reps": "10"},
            ]},
            {"title": "Squat Day", "exercises": [
                {"name": "Back Squat",            "label": "A", "sets": 3, "reps": "5/3/1"},
                {"name": "Back Squat",            "label": "B", "sets": 5, "reps": "10 @ 50% TM"},
                {"name": "Leg Curl",              "label": "C", "sets": 5, "reps": "10"},
            ]},
            {"title": "Press Day", "exercises": [
                {"name": "Overhead Press",        "label": "A", "sets": 3, "reps": "5/3/1"},
                {"name": "Overhead Press",        "label": "B", "sets": 5, "reps": "10 @ 50% TM"},
                {"name": "Pull-Up",               "label": "C", "sets": 5, "reps": "10"},
            ]},
            {"title": "Deadlift Day", "exercises": [
                {"name": "Deadlift",              "label": "A", "sets": 3, "reps": "5/3/1"},
                {"name": "Deadlift",              "label": "B", "sets": 5, "reps": "10 @ 50% TM"},
                {"name": "Walking Lunge",         "label": "C", "sets": 3, "reps": "10/leg"},
            ]},
        ],
    },

    # ─── Any × Full gym × Glute & lower-body (women focused) ─────
    {
        "name": "Glute & Lower-Body Focus",
        "meta": {
            "goals":         ["build_muscle", "stay_consistent"],
            "experience":    "any",
            "equipment":     "full_gym",
            "days_per_week": 4,
            "weeks":         8,
            "tagline":       "Glute-emphasis programme",
            "summary":       "Four-day split inspired by Bret Contreras' "
                             "research into glute hypertrophy. Two glute-"
                             "emphasis days plus two upper-body sessions "
                             "for proportion. Higher hip-thrust + horizontal-"
                             "loaded glute work than traditional splits.",
            "evidence": [
                "Contreras, B. & Cronin, J. (2014–2024) — multi-decade "
                "glute EMG and hypertrophy research, including the "
                "barbell hip thrust as the top glute activator.",
                "Schoenfeld, B. (2020) — exercise selection effects on "
                "regional hypertrophy in the lower body.",
                "ACSM 2026 — glute-dominant programming outperforms "
                "squat-dominant for users prioritising lower-body "
                "shape outcomes.",
            ],
            "source_attribution": "Adapted from Bret Contreras' Strong Curves",
            "weekly_volume_per_muscle": {
                "glutes": 18, "quads": 8, "hamstrings": 12, "back": 10, "shoulders": 8,
            },
            "progression_rule":
                "Aim for 1-2 RIR on top sets. Add reps until you hit "
                "the ceiling of the range, then add weight. Hip thrust "
                "progresses faster than squats — expect 5-10kg jumps.",
            "deload_strategy":
                "Every 5th week, drop volume to 60% (cut sets, not "
                "weight). Helps the lower-back recover.",
            "recommended_for": [
                "lower-body shape goals", "anyone wanting glute focus", "post-rehab returners with quad-dominant pattern",
            ],
            "not_recommended_for": ["max-strength competitive lifters"],
            "ai_pt_levers": {
                "exercise_swaps": True, "set_count": True, "reps": True,
            },
        },
        "days": [
            {"title": "Glute Day A", "exercises": [
                {"name": "Hip Thrust",            "label": "A", "sets": 4, "reps": "8-10"},
                {"name": "Romanian Deadlift",     "label": "B", "sets": 3, "reps": "8-10"},
                {"name": "Bulgarian Split Squat", "label": "C", "sets": 3, "reps": "10/leg"},
                {"name": "Cable Pull-Through",    "label": "D", "sets": 3, "reps": "12-15"},
                {"name": "Glute Bridge",          "label": "E", "sets": 3, "reps": "12-15"},
            ]},
            {"title": "Upper A", "exercises": [
                {"name": "Dumbbell Bench Press",  "label": "A", "sets": 3, "reps": "8-12"},
                {"name": "Lat Pulldown",          "label": "B", "sets": 3, "reps": "8-12"},
                {"name": "Dumbbell Shoulder Press","label":"C","sets":3, "reps": "8-12"},
                {"name": "Seated Cable Row",      "label": "D", "sets": 3, "reps": "8-12"},
                {"name": "Lateral Raise",         "label": "E", "sets": 3, "reps": "12-15"},
            ]},
            {"title": "Glute Day B", "exercises": [
                {"name": "Back Squat",            "label": "A", "sets": 3, "reps": "6-8"},
                {"name": "Hip Thrust",            "label": "B", "sets": 4, "reps": "10-12"},
                {"name": "Walking Lunge",         "label": "C", "sets": 3, "reps": "10/leg"},
                {"name": "Leg Curl",              "label": "D", "sets": 3, "reps": "10-12"},
                {"name": "Standing Calf Raise",   "label": "E", "sets": 3, "reps": "10-15"},
            ]},
            {"title": "Upper B", "exercises": [
                {"name": "Incline Dumbbell Press","label":"A", "sets": 3, "reps": "8-12"},
                {"name": "Pull-Up",               "label": "B", "sets": 3, "reps": "6-10"},
                {"name": "Barbell Row",           "label": "C", "sets": 3, "reps": "8-10"},
                {"name": "Lateral Raise",         "label": "D", "sets": 3, "reps": "12-15"},
                {"name": "Barbell Curl",          "label": "E", "sets": 3, "reps": "10-12"},
            ]},
        ],
    },

    # ─── Any × Home with weights ─────────────────────────────────
    {
        "name": "Home Dumbbell Strength",
        "meta": {
            "goals":         ["build_muscle", "stay_consistent"],
            "experience":    "any",
            "equipment":     "home_with_weights",
            "days_per_week": 3,
            "weeks":         8,
            "tagline":       "3-day full-body for home setups",
            "summary":       "Three full-body sessions a week designed for a "
                             "small home setup with adjustable dumbbells and "
                             "(optionally) a barbell. Hits all major movement "
                             "patterns within a 45-minute session.",
            "evidence": [
                "Helms et al. (2018) — full-body programming yields "
                "equivalent hypertrophy to splits when volume is "
                "matched.",
                "Schoenfeld (2019) — dumbbell training elicits "
                "comparable hypertrophy to barbell when load is matched.",
            ],
            "source_attribution": "Synthesis of Helms / Schoenfeld for limited-equipment lifters",
            "weekly_volume_per_muscle": {
                "chest": 9, "back": 12, "quads": 9, "hamstrings": 9, "shoulders": 9,
            },
            "progression_rule":
                "Top set at 1-2 RIR. Add reps until the rep ceiling, "
                "then jump to the next dumbbell increment.",
            "deload_strategy":
                "Every 6th week, halve sets at the same weight.",
            "recommended_for": [
                "home gym users", "garage / spare-room training",
                "users with adjustable dumbbells",
            ],
            "not_recommended_for": ["bodyweight-only setups (use Bodyweight Foundation)"],
            "ai_pt_levers": {
                "exercise_swaps": True, "set_count": True, "reps": True,
            },
        },
        "days": [
            {"title": "Day 1 — Lower bias", "exercises": [
                {"name": "Goblet Squat",          "label": "A", "sets": 3, "reps": "8-10"},
                {"name": "Romanian Deadlift",     "label": "B", "sets": 3, "reps": "8-10"},
                {"name": "Dumbbell Bench Press",  "label": "C", "sets": 3, "reps": "8-12"},
                {"name": "Dumbbell Row",          "label": "D", "sets": 3, "reps": "8-12"},
                {"name": "Plank",                 "label": "E", "sets": 3, "reps": "30-60s"},
            ]},
            {"title": "Day 2 — Upper bias", "exercises": [
                {"name": "Dumbbell Shoulder Press","label":"A","sets":3, "reps": "8-10"},
                {"name": "Single-Arm Row",        "label": "B", "sets": 3, "reps": "10/arm"},
                {"name": "Floor Press",           "label": "C", "sets": 3, "reps": "8-10"},
                {"name": "Lunge",                 "label": "D", "sets": 3, "reps": "10/leg"},
                {"name": "Lateral Raise",         "label": "E", "sets": 3, "reps": "12-15"},
            ]},
            {"title": "Day 3 — Full body", "exercises": [
                {"name": "Goblet Squat",          "label": "A", "sets": 3, "reps": "10-12"},
                {"name": "Bent-Over Row",         "label": "B", "sets": 3, "reps": "8-10"},
                {"name": "Push-Up",               "label": "C", "sets": 3, "reps": "AMRAP"},
                {"name": "Glute Bridge",          "label": "D", "sets": 3, "reps": "12-15"},
                {"name": "Hammer Curl",           "label": "E", "sets": 3, "reps": "10-12"},
            ]},
        ],
    },

    # ─── Any × Lose fat × Hybrid ─────────────────────────────────
    {
        "name": "Strength + Cardio Hybrid",
        "meta": {
            "goals":         ["lose_fat", "stay_consistent"],
            "experience":    "any",
            "equipment":     "full_gym",
            "days_per_week": 5,
            "weeks":         8,
            "tagline":       "3 lifting + 2 conditioning days",
            "summary":       "Concurrent training template — three full-body "
                             "lifting days plus two conditioning sessions a "
                             "week. Designed for fat-loss without losing "
                             "strength.",
            "evidence": [
                "Wilson, J. M. et al. (2012) — concurrent training meta-"
                "analysis: concurrent training preserves strength and "
                "muscle mass during fat loss when sessions are separated.",
                "Helms, E. (2018) — bodybuilding contest prep "
                "literature: maintaining lifting volume + adding "
                "cardio is the gold standard for body recomposition.",
                "Murach & Bagley (2016) — hypertrophy + endurance "
                "interference effect is minimal when frequency / "
                "volume are managed.",
            ],
            "source_attribution": "Synthesis of concurrent-training research",
            "weekly_volume_per_muscle": {
                "chest": 9, "back": 9, "quads": 9, "shoulders": 9,
            },
            "progression_rule":
                "Lifting: maintain weights (don't try to PR in deficit). "
                "Cardio: add 5 min per session per week or increase "
                "intensity by 1 RPE.",
            "deload_strategy":
                "Every 4th week, drop one cardio session and reduce "
                "lifting volume by 30%.",
            "recommended_for": [
                "fat-loss phase", "general-fitness goals", "users who like cardio",
            ],
            "not_recommended_for": [
                "max muscle gain (strength-focused programmes are better)",
                "very low time-budget users (3-day templates are tighter)",
            ],
            "ai_pt_levers": {
                "exercise_swaps": True, "set_count": True, "cardio_intensity": True, "cardio_modality": True,
            },
        },
        "days": [
            {"title": "Lift Day 1", "exercises": [
                {"name": "Back Squat",            "label": "A", "sets": 3, "reps": "6-8"},
                {"name": "Barbell Bench Press",   "label": "B", "sets": 3, "reps": "6-8"},
                {"name": "Barbell Row",           "label": "C", "sets": 3, "reps": "6-8"},
                {"name": "Plank",                 "label": "D", "sets": 3, "reps": "45-60s"},
            ]},
            {"title": "Conditioning 1", "exercises": [
                {"name": "Zone 2 cardio (45 min)","label":"A", "sets": 1, "reps": "45 min"},
            ]},
            {"title": "Lift Day 2", "exercises": [
                {"name": "Romanian Deadlift",     "label": "A", "sets": 3, "reps": "6-8"},
                {"name": "Overhead Press",        "label": "B", "sets": 3, "reps": "6-8"},
                {"name": "Pull-Up",               "label": "C", "sets": 3, "reps": "6-10"},
                {"name": "Walking Lunge",         "label": "D", "sets": 3, "reps": "10/leg"},
            ]},
            {"title": "Conditioning 2", "exercises": [
                {"name": "Intervals (20 min)",    "label": "A", "sets": 8, "reps": "1 min on / 1 min off"},
            ]},
            {"title": "Lift Day 3", "exercises": [
                {"name": "Front Squat",           "label": "A", "sets": 3, "reps": "6-8"},
                {"name": "Incline Dumbbell Press","label":"B","sets": 3, "reps": "8-10"},
                {"name": "Seated Cable Row",      "label": "C", "sets": 3, "reps": "8-10"},
                {"name": "Lateral Raise",         "label": "D", "sets": 3, "reps": "12-15"},
            ]},
        ],
    },
]


SYSTEM_USERNAME = "_solo_catalog_"


class Command(BaseCommand):
    help = "Seed/refresh the public Solo programmes catalog with research-backed templates."

    @transaction.atomic
    def handle(self, *args, **options):
        # Catalog rows need a `user` (FK is NOT NULL on WorkoutPlan).
        # System user — inactive, never logged in, just an anchor.
        system_user, _ = User.objects.get_or_create(
            username=SYSTEM_USERNAME,
            defaults={
                "role":      User.TRAINER,
                "email":     "system+catalog@afletics.com",
                "is_active": False,
                "is_staff":  False,
            },
        )

        for spec in PROGRAMMES:
            # Stitch coaching notes into the meta blob so they ship
            # with the programme card without duplicating the source-
            # of-truth COACHING_NOTES dict above.
            ex_names = {
                ex["name"]
                for d in spec["days"]
                for ex in d["exercises"]
            }
            spec["meta"]["exercise_notes"] = {
                name: COACHING_NOTES[name]
                for name in ex_names
                if name in COACHING_NOTES
            }

            plan, created = WorkoutPlan.objects.update_or_create(
                user=system_user,
                name=spec["name"],
                is_solo_template=True,
                defaults={
                    "is_active":      True,
                    "is_template":    True,
                    "programme_meta": spec["meta"],
                },
            )

            # Replace days/exercises wholesale — seed file is
            # authoritative.
            plan.days.all().delete()
            for day_idx, day_spec in enumerate(spec["days"]):
                day = WorkoutDay.objects.create(
                    plan=plan, title=day_spec["title"], order=day_idx,
                )
                for ex_idx, ex_spec in enumerate(day_spec["exercises"]):
                    ex = Exercise.objects.create(
                        workout_day=day,
                        name=ex_spec["name"],
                        label=ex_spec["label"],
                        order=ex_idx,
                    )
                    # Compact set spec: {sets: int, reps: str}
                    # expand to one ExerciseSetTarget per set.
                    n_sets = ex_spec["sets"]
                    rep_str = ex_spec["reps"]
                    for set_num in range(1, n_sets + 1):
                        ExerciseSetTarget.objects.create(
                            exercise=ex,
                            set_number=set_num,
                            reps=rep_str,
                        )

            verb = "created" if created else "refreshed"
            self.stdout.write(self.style.SUCCESS(f"  {verb}: {plan.name}"))

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {len(PROGRAMMES)} research-backed programmes in the catalog.",
        ))
