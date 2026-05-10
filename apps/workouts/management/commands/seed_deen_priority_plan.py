"""DEEN-PLAN — ensure every exercise + food in Deen's PT-built plan
exists in the catalog and is flagged as icon-priority.

The 30 lifts come from the Deen Ali Training Plan (5 days: Upper /
Lower / Push / Pull / Legs). The 18 foods come from Fat Loss Phase 1
(Pre Workout, Intra Workout, Post Workout, Meal 1-3).

Idempotent. Uses fuzzy name match (lower + strip + collapse spaces)
to avoid duplicating rows that already exist under a slightly
different display name. When a row exists, only `icon_priority`
flips to 10 — name / muscle / equipment / form copy stay untouched
because the catalog curation pass owns those.

Usage:
    python manage.py seed_deen_priority_plan
    python manage.py seed_deen_priority_plan --dry-run
"""
from __future__ import annotations

import re

from django.core.management.base import BaseCommand
from django.db import transaction


# (display_name, primary_muscle, equipment, level, mechanic, force, category)
PRIORITY_LIFTS = [
    # ------ Day 1 — Upper ------
    ("Pec Deck Chest Fly",                  "chest",     "machine",  "beginner",     "isolation", "push", "strength"),
    ("Incline Machine Chest Press",         "chest",     "machine",  "beginner",     "compound",  "push", "strength"),
    ("Chest Supported T-Bar Row",           "lats",      "machine",  "intermediate", "compound",  "pull", "strength"),
    ("Neutral Grip Lat Pulldown",           "lats",      "cable",    "beginner",     "compound",  "pull", "strength"),
    ("Torso Supported Lateral Raise",       "shoulders", "machine",  "beginner",     "isolation", "push", "strength"),
    ("Shoulder Press Machine",              "shoulders", "machine",  "beginner",     "compound",  "push", "strength"),
    ("Cross Body Tricep Extension",         "triceps",   "cable",    "intermediate", "isolation", "push", "strength"),
    ("Dual Arm Cable Bicep Curl",           "biceps",    "cable",    "beginner",     "isolation", "pull", "strength"),
    ("Dumbbell Skull Crusher",              "triceps",   "dumbbell", "intermediate", "isolation", "push", "strength"),
    ("Single Arm Dumbbell Preacher Curl",   "biceps",    "dumbbell", "intermediate", "isolation", "pull", "strength"),

    # ------ Day 2 — Lower ------
    ("Seated Calf Raise",                   "calves",    "machine",  "beginner",     "isolation", "push", "strength"),
    ("Lying Hamstring Curl",                "hamstrings","machine",  "beginner",     "isolation", "pull", "strength"),
    ("Leg Extension",                       "quads",     "machine",  "beginner",     "isolation", "push", "strength"),
    ("Leg Press",                           "quads",     "machine",  "beginner",     "compound",  "push", "strength"),
    ("Hack Squat",                          "quads",     "machine",  "intermediate", "compound",  "push", "strength"),
    ("Dumbbell Bulgarian Split Squat",      "quads",     "dumbbell", "intermediate", "compound",  "push", "strength"),
    ("Adductor Machine",                    "adductors", "machine",  "beginner",     "isolation", "push", "strength"),

    # ------ Day 3 — Push (Incline Machine Chest Press already above) ------
    ("Dumbbell Shoulder Press",             "shoulders", "dumbbell", "intermediate", "compound",  "push", "strength"),
    ("Seated Machine Chest Press",          "chest",     "machine",  "beginner",     "compound",  "push", "strength"),
    ("Lying Cuffed Lateral Raise",          "shoulders", "cable",    "intermediate", "isolation", "push", "strength"),
    ("Cable Crossover",                     "chest",     "cable",    "intermediate", "isolation", "push", "strength"),
    ("Machine Dip",                         "triceps",   "machine",  "beginner",     "compound",  "push", "strength"),
    ("Single Arm Tricep Pushdown",          "triceps",   "cable",    "beginner",     "isolation", "push", "strength"),

    # ------ Day 4 — Pull ------
    ("Reverse Bench Single Arm Pulldown",   "lats",      "cable",    "intermediate", "isolation", "pull", "strength"),
    ("Hammer Strength Low to High Row",     "back",      "machine",  "intermediate", "compound",  "pull", "strength"),
    ("Single Arm D-Handle Low Row",         "back",      "cable",    "intermediate", "compound",  "pull", "strength"),
    ("EZ Bar Cable Bicep Curl",             "biceps",    "cable",    "beginner",     "isolation", "pull", "strength"),
    ("Alternating Hammer Curl",             "biceps",    "dumbbell", "beginner",     "isolation", "pull", "strength"),

    # ------ Day 5 — Legs ------
    ("Seated Hamstring Curl",               "hamstrings","machine",  "beginner",     "isolation", "pull", "strength"),
    ("Glute Drive Machine",                 "glutes",    "machine",  "beginner",     "isolation", "push", "strength"),
]


# (display_name, brand_or_blank, kcal_100g, protein_100g, carbs_100g, fat_100g)
# Per-100g macros sourced from standard UK food composition references.
PRIORITY_FOODS = [
    ("Oats (rolled)",                  "",            372, 12.0, 60.0, 8.0),
    ("Whey Protein",                   "",            390, 73.0, 8.0,  5.0),
    ("Banana",                         "",            89,  1.1,  23.0, 0.3),
    ("Dark Chocolate 85%",             "",            580, 9.0,  19.0, 46.0),
    ("EAAs (Essential Amino Acids)",   "",            10,  2.0,  0.0,  0.0),
    ("Creatine Monohydrate",           "",            240, 0.0,  60.0, 0.0),
    ("Coco Pops",                      "Kellogg's",   382, 5.0,  87.0, 2.5),
    ("Pineapple",                      "",            50,  0.5,  13.0, 0.1),
    ("Whole Egg",                      "",            143, 12.6, 0.7,  9.5),
    ("Egg White",                      "",            52,  10.9, 0.7,  0.2),
    ("Turkey Rashers",                 "",            120, 22.0, 1.0,  3.0),
    ("Tortilla Wrap",                  "",            315, 8.0,  53.0, 7.0),
    ("Chicken Breast (raw)",           "",            106, 23.0, 0.0,  2.0),
    ("Mixed Salad Leaves",             "",            17,  1.5,  3.0,  0.3),
    ("Apple",                          "",            52,  0.3,  14.0, 0.2),
    ("Pasta (dry)",                    "",            371, 13.0, 75.0, 1.5),
    ("Beef Mince 5% Fat (raw)",        "",            137, 21.0, 0.0,  6.0),
    ("Extra Virgin Olive Oil",         "",            884, 0.0,  0.0,  100.0),
]


def normalise(s: str) -> str:
    """Lowercase + strip + collapse internal whitespace + drop punctuation."""
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s


class Command(BaseCommand):
    help = "Seed Deen's priority plan (30 lifts + 18 foods) and stamp icon_priority."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Report would-do without writing.")

    def handle(self, *args, **opts):
        from apps.workouts.models import ExerciseCatalog
        from apps.nutrition.models import CuratedFood

        dry = opts["dry_run"]
        ICON_PRIORITY = 10

        # ---- Exercises ----
        existing = list(
            ExerciseCatalog.objects.filter(is_published=True)
            .values("id", "name", "muscle_group", "equipment")
        )
        by_norm = {normalise(r["name"]): r for r in existing}

        flipped, created = 0, 0
        for (name, muscle, equipment, level, mechanic, force, category) in PRIORITY_LIFTS:
            row = by_norm.get(normalise(name))
            if row:
                if dry:
                    self.stdout.write(f"  flip-priority: {row['name']} (id={row['id']})")
                else:
                    ExerciseCatalog.objects.filter(pk=row["id"]).update(
                        icon_priority=max(ICON_PRIORITY, 0),
                    )
                flipped += 1
            else:
                if dry:
                    self.stdout.write(self.style.WARNING(
                        f"  would-create: {name} ({muscle}, {equipment})"
                    ))
                else:
                    ExerciseCatalog.objects.create(
                        name=name,
                        muscle_group=muscle,
                        equipment=equipment,
                        level=level,
                        mechanic=mechanic,
                        force=force,
                        category=category,
                        source=ExerciseCatalog.SOURCE_GYMFLOW,
                        external_id="deen_plan_" + normalise(name).replace(" ", "_")[:48],
                        icon_priority=ICON_PRIORITY,
                        is_published=True,
                    )
                created += 1

        # ---- Foods ----
        existing_foods = list(
            CuratedFood.objects.filter(is_published=True)
            .values("id", "name", "brand")
        )
        food_by_norm = {normalise(r["name"]): r for r in existing_foods}

        food_flipped, food_created = 0, 0
        for (name, brand, kcal, p, c, f) in PRIORITY_FOODS:
            row = food_by_norm.get(normalise(name))
            if row:
                if dry:
                    self.stdout.write(f"  food-exists: {row['name']} (id={row['id']})")
                food_flipped += 1
            else:
                if dry:
                    self.stdout.write(self.style.WARNING(
                        f"  would-create-food: {name} ({brand or 'unbranded'})"
                    ))
                else:
                    self._create_food(CuratedFood, name, brand, kcal, p, c, f)
                food_created += 1

        msg = (f"Exercises: {flipped} flipped, {created} created. "
               f"Foods: {food_flipped} matched, {food_created} created.")
        if dry:
            self.stdout.write(self.style.NOTICE("DRY RUN — " + msg))
        else:
            self.stdout.write(self.style.SUCCESS(msg))

    def _create_food(self, CuratedFood, name, brand, kcal, p, c, f):
        """Insert a CuratedFood with sensible defaults. Field names
        match the existing schema — falls back gracefully if any
        optional field doesn't exist on this branch."""
        kwargs = dict(
            name=name,
            brand=brand,
            kcal_per_100g=kcal,
            protein_per_100g=p,
            carbs_per_100g=c,
            fat_per_100g=f,
            source=CuratedFood.SOURCE_GYMFLOW,
            source_id="deen_plan_" + normalise(name).replace(" ", "_")[:48],
            is_published=True,
        )
        # Drop unknown kwargs so this command stays robust across
        # schema drifts on any old branch.
        valid = {fld.name for fld in CuratedFood._meta.get_fields()}
        kwargs = {k: v for k, v in kwargs.items() if k in valid}
        with transaction.atomic():
            CuratedFood.objects.create(**kwargs)
