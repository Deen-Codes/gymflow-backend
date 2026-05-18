"""EXERCISE-LIB-1500 (#210) — load curated YAML batches into ExerciseCatalog.

Mirrors the `seed_popular_foods` pattern in nutrition: drop YAML
files into `apps/workouts/seed/` and re-run. Idempotent — uses
`(source=afletics, external_id)` as the unique key.

Each YAML file is a list of exercise dicts. Required fields:
    external_id          (string, snake_case, unique within source)
    name                 (string, UK-friendly display name)
    muscle_group         (string, primary muscle)
    equipment            (string, e.g. barbell / dumbbell / cable)
    level                (beginner / intermediate / expert)
    category             (strength / stretching / plyometrics / etc.)

Optional fields:
    secondary_muscles    (comma-separated, e.g. "triceps,shoulders")
    mechanic             (compound / isolation)
    force                (push / pull / static)
    instructions         (numbered steps, newline-delimited)
    form_description     (paragraph)
    common_mistakes      (newline-delimited)
    breathing_cues       (string)
    image_url            (start-frame icon URL)
    animation_url        (motion URL)

Usage:
    python manage.py seed_exercise_curated
    python manage.py seed_exercise_curated --dry-run
    python manage.py seed_exercise_curated --path apps/workouts/seed/glutes.yaml
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.workouts.models import ExerciseCatalog


REQUIRED_FIELDS = {
    "external_id", "name", "muscle_group", "equipment", "level", "category",
}

VALID_LEVELS = {"beginner", "intermediate", "expert"}
VALID_MECHANICS = {"compound", "isolation", ""}
VALID_FORCES = {"push", "pull", "static", ""}
VALID_CATEGORIES = {
    "strength", "stretching", "plyometrics", "powerlifting",
    "cardio", "olympic_weightlifting", "strongman",
}

DEFAULT_SEED_DIR = Path(__file__).resolve().parents[2] / "seed"


class Command(BaseCommand):
    help = "Seed ExerciseCatalog from curated YAML batches under apps/workouts/seed/."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default=None,
            help="Single YAML to load. Default: glob apps/workouts/seed/*.yaml.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate only, don't write to the DB.",
        )

    def handle(self, *args, **opts):
        path = opts["path"]
        dry_run = opts["dry_run"]

        if path:
            if not os.path.exists(path):
                raise CommandError(f"Seed file not found: {path}")
            seed_files = [Path(path)]
        else:
            if not DEFAULT_SEED_DIR.exists():
                raise CommandError(f"Seed dir does not exist: {DEFAULT_SEED_DIR}")
            seed_files = sorted(DEFAULT_SEED_DIR.glob("*.yaml"))
            if not seed_files:
                self.stdout.write(self.style.WARNING(
                    f"No YAML files in {DEFAULT_SEED_DIR}. Nothing to do."
                ))
                return

        # Load every YAML
        entries = []
        for f_path in seed_files:
            self.stdout.write(f"Loading {f_path.name}…")
            with open(f_path, "r") as fh:
                chunk = yaml.safe_load(fh)
            if chunk is None:
                continue
            if not isinstance(chunk, list):
                raise CommandError(
                    f"{f_path.name}: expected a YAML list at root, "
                    f"got {type(chunk).__name__}"
                )
            entries.extend(chunk)

        # Validation pass
        errors = []
        seen_ids = set()
        for i, e in enumerate(entries):
            if not isinstance(e, dict):
                errors.append(f"Entry {i}: expected dict, got {type(e).__name__}")
                continue
            missing = REQUIRED_FIELDS - set(e.keys())
            if missing:
                errors.append(
                    f"Entry {i} ({e.get('external_id', '?')}): missing {sorted(missing)}"
                )
            eid = e.get("external_id")
            if eid in seen_ids:
                errors.append(f"Entry {i}: duplicate external_id={eid!r}")
            seen_ids.add(eid)
            if e.get("level") not in VALID_LEVELS:
                errors.append(
                    f"Entry {i} ({eid}): invalid level {e.get('level')!r}"
                )
            mech = e.get("mechanic", "")
            if mech not in VALID_MECHANICS:
                errors.append(
                    f"Entry {i} ({eid}): invalid mechanic {mech!r}"
                )
            force = e.get("force", "")
            if force not in VALID_FORCES:
                errors.append(
                    f"Entry {i} ({eid}): invalid force {force!r}"
                )
            cat = e.get("category", "")
            if cat not in VALID_CATEGORIES:
                errors.append(
                    f"Entry {i} ({eid}): invalid category {cat!r}"
                )

        if errors:
            self.stdout.write(self.style.ERROR(
                f"Validation failed — {len(errors)} errors:"
            ))
            for err in errors[:20]:
                self.stdout.write(self.style.ERROR(f"  • {err}"))
            if len(errors) > 20:
                self.stdout.write(self.style.ERROR(
                    f"  …and {len(errors) - 20} more"
                ))
            raise CommandError("Fix the seed YAML and re-run.")

        self.stdout.write(self.style.SUCCESS(
            f"Validated {len(entries)} entries — no errors"
        ))

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry-run — no DB writes."))
            return

        # Write — upsert each row idempotently.
        created = 0
        updated = 0
        with transaction.atomic():
            for e in entries:
                defaults = {
                    "name":               str(e["name"]),
                    "muscle_group":       str(e["muscle_group"]),
                    "secondary_muscles":  str(e.get("secondary_muscles", "")),
                    "equipment":          str(e["equipment"]),
                    "level":              str(e["level"]),
                    "mechanic":           str(e.get("mechanic", "")),
                    "force":              str(e.get("force", "")),
                    "category":           str(e["category"]),
                    "instructions":       str(e.get("instructions", "")),
                    "form_description":   str(e.get("form_description", "")),
                    "common_mistakes":    str(e.get("common_mistakes", "")),
                    "breathing_cues":     str(e.get("breathing_cues", "")),
                    "image_url":          str(e.get("image_url", "")),
                    "video_url":          str(e.get("video_url", "")),
                    "animation_url":      str(e.get("animation_url", "")),
                    "is_published":       True,
                }
                _, was_created = ExerciseCatalog.objects.update_or_create(
                    source=ExerciseCatalog.SOURCE_AFLETICS,
                    external_id=str(e["external_id"]),
                    defaults=defaults,
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done. Created {created}, updated {updated}, total {created + updated}."
        ))
