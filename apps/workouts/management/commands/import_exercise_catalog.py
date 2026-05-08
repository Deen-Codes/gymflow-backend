"""
EXERCISE-DB — owned multi-source exercise catalog ingest.

Mirrors the approach in `import_curated_foods` (NUTRITION-DB / #105):
re-derive a clean owned catalog from public sources, with the
GymFlow source for our own additions + branded animated pose stills.

Usage:

    python manage.py import_exercise_catalog --source=free_exercise_db --path=/path/to/exercises.json
    python manage.py import_exercise_catalog --source=gymflow --path=/path/to/gymflow_exercises.csv

Sources:
  • Free Exercise DB (yuhonas) — public domain, ~800 exercises with
    instructions + images. https://github.com/yuhonas/free-exercise-db
    Re-derive: we ingest the metadata + replace images with our own
    animated pose stills (sourced separately per
    `EXERCISE_ANIMATION_LIBRARY.md`).
  • GymFlow                    — our own additions / curated overrides.

Intentionally NOT ingested:
  • wger (AGPL/viral copyleft — would taint the catalog DB)
  • ExRx (proprietary)

Idempotency: `update_or_create` against `(source, external_id)` so
duplicates are impossible by design (mirrors NUTRITION-DB).

Animated pose stills: the exercise's `image_url` points to a high-
quality static "good posture" frame. iOS `ExerciseAnimationView`
treats this as the start frame of an animation if `animation_url`
is also set (Lottie/.lottie/.mp4). The static image alone is enough
for v1 — the animation is layered on top once commissioned per
`EXERCISE_ANIMATION_LIBRARY.md`.
"""
from __future__ import annotations

import csv
import json
from typing import Iterable

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.workouts.models import ExerciseCatalog


SUPPORTED_SOURCES = {
    ExerciseCatalog.SOURCE_FREE_EXERCISE_DB,
    ExerciseCatalog.SOURCE_GYMFLOW,
}


class Command(BaseCommand):
    help = "Ingest exercises from a public source (Free Exercise DB) or the GymFlow-curated list."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            required=True,
            choices=sorted(SUPPORTED_SOURCES),
        )
        parser.add_argument(
            "--path",
            required=True,
            help="Path to the source JSON/CSV file.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse + validate but don't write to the DB.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
        )

    def handle(self, *args, **opts):
        source = opts["source"]
        path = opts["path"]
        dry_run = opts["dry_run"]
        limit = opts["limit"]

        loader = LOADERS.get(source)
        if loader is None:
            raise CommandError(f"No loader for source: {source}")

        rows = list(loader(path))
        if limit is not None:
            rows = rows[:limit]

        self.stdout.write(f"Parsed {len(rows)} exercises from {source} at {path}.")
        if dry_run:
            self.stdout.write("Dry run — no DB writes.")
            return

        created = updated = 0
        with transaction.atomic():
            for row in rows:
                obj, was_created = ExerciseCatalog.objects.update_or_create(
                    source=row["source"],
                    external_id=row["external_id"],
                    defaults={k: v for k, v in row.items() if k not in ("source", "external_id")},
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created {created}, updated {updated} rows in ExerciseCatalog."
            )
        )


# --------------------------------------------------------------------
# Per-source loaders — each yields dicts matching ExerciseCatalog
# fields. The Free Exercise DB loader is REAL (the JSON file is
# small and well-shaped); the GymFlow loader expects our own CSV.
# --------------------------------------------------------------------


def load_free_exercise_db(path: str) -> Iterable[dict]:
    """yuhonas/free-exercise-db — exercises.json.

    Each row has shape:
      {
        "id": "Push_Up",
        "name": "Push-Up",
        "primaryMuscles": ["chest"],
        "secondaryMuscles": ["triceps", "shoulders"],
        "level": "beginner",
        "mechanic": "compound",
        "equipment": "body only",
        "instructions": ["Lie face down...", "Push up..."],
        "images": ["Push_Up/0.jpg", "Push_Up/1.jpg"],
        "category": "strength"
      }

    We map → ExerciseCatalog. Image URL is the FIRST image (the
    "start frame" — good-posture static). When an animation is
    later commissioned for this exercise, set `animation_url` via
    a separate management command or admin UI; it doesn't replace
    the image, it layers on top.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # The repo publishes exercises.json as either a list or a dict
    # keyed by id. Handle both.
    rows = data.values() if isinstance(data, dict) else data

    for row in rows:
        # Default to body only when missing.
        equipment = (row.get("equipment") or "body only").strip()
        primary_muscle = ""
        if row.get("primaryMuscles"):
            primary_muscle = row["primaryMuscles"][0]

        instructions = "\n".join(row.get("instructions") or [])

        # Image URL — the repo serves images from a known prefix.
        # Caller can override the prefix at runtime if mirroring
        # to S3 / CDN. Default to the GitHub raw URL.
        image_relpath = (row.get("images") or [""])[0]
        image_url = (
            f"https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/exercises/{image_relpath}"
            if image_relpath else ""
        )

        yield {
            "source":        ExerciseCatalog.SOURCE_FREE_EXERCISE_DB,
            "external_id":   row["id"],
            "name":          row["name"],
            "muscle_group":  primary_muscle,
            "equipment":     equipment,
            "instructions":  instructions,
            "image_url":     image_url,
            "video_url":     "",
            "animation_url": "",       # populate later when commissioned
            "is_published":  True,
        }


def load_gymflow(path: str) -> Iterable[dict]:
    """GymFlow-curated CSV. Columns: external_id,name,muscle_group,
    equipment,instructions,image_url,video_url,animation_url.
    Used for branded / overridden / newly-commissioned exercises.
    """
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield {
                "source":        ExerciseCatalog.SOURCE_GYMFLOW,
                "external_id":   row["external_id"].strip(),
                "name":          row["name"].strip(),
                "muscle_group":  (row.get("muscle_group") or "").strip(),
                "equipment":     (row.get("equipment") or "").strip(),
                "instructions":  (row.get("instructions") or "").strip(),
                "image_url":     (row.get("image_url") or "").strip(),
                "video_url":     (row.get("video_url") or "").strip(),
                "animation_url": (row.get("animation_url") or "").strip(),
                "is_published":  True,
            }


LOADERS = {
    ExerciseCatalog.SOURCE_FREE_EXERCISE_DB: load_free_exercise_db,
    ExerciseCatalog.SOURCE_GYMFLOW:           load_gymflow,
}
