"""EXERCISE-LIB-FORM-DESCRIPTIONS (#212) — load form copy onto ExerciseCatalog.

Populates `form_description`, `common_mistakes`, `breathing_cues` (and
optionally re-writes `instructions`) for exercises in the catalog.

Two match modes:

    1. By external_id — exact match. Used for hand-curated entries
       (the staple top-100 most-tapped lifts).

    2. By name (case-insensitive) — fallback for FreeDB/curated rows
       that share the same canonical name across sources.

YAML files live at `apps/workouts/seed/form_copy/*.yaml`. Each entry:

    - external_id: gymflow_back_squat       # OR
      name: Back Squat                       # match by name (any source)
      form_description: |
        Multi-paragraph or single paragraph copy on setup, execution,
        and key cues. UK gym vocabulary.
      common_mistakes: |
        - Knees caving inward
        - Heels lifting off the floor
        - Looking up at the ceiling
      breathing_cues: |
        Inhale at the top, brace through the descent, exhale through
        the sticking point on the way up.
      instructions: |              # optional override
        1. Set the bar across …

Usage:
    python manage.py seed_exercise_form_copy
    python manage.py seed_exercise_form_copy --dry-run
    python manage.py seed_exercise_form_copy --path apps/workouts/seed/form_copy/staples.yaml
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.workouts.models import ExerciseCatalog


DEFAULT_SEED_DIR = Path(__file__).resolve().parents[2] / "seed" / "form_copy"


class Command(BaseCommand):
    help = "Apply hand-curated form copy to ExerciseCatalog rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default=None,
            help="Single YAML to load. Default: glob apps/workouts/seed/form_copy/*.yaml.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate + show match counts, don't write to the DB.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Overwrite existing form copy (default skips rows that already have it).",
        )

    def handle(self, *args, **opts):
        path = opts["path"]
        dry_run = opts["dry_run"]
        overwrite = opts["overwrite"]

        if path:
            if not os.path.exists(path):
                raise CommandError(f"Form copy file not found: {path}")
            files = [Path(path)]
        else:
            if not DEFAULT_SEED_DIR.exists():
                self.stdout.write(self.style.WARNING(
                    f"Form-copy dir does not exist: {DEFAULT_SEED_DIR}. Nothing to do."
                ))
                return
            files = sorted(DEFAULT_SEED_DIR.glob("*.yaml"))
            if not files:
                self.stdout.write(self.style.WARNING(
                    f"No YAML files in {DEFAULT_SEED_DIR}. Nothing to do."
                ))
                return

        # Load every YAML
        entries = []
        for fp in files:
            self.stdout.write(f"Loading {fp.name}…")
            data = yaml.safe_load(open(fp)) or []
            if not isinstance(data, list):
                raise CommandError(
                    f"{fp.name}: expected a YAML list, got {type(data).__name__}"
                )
            entries.extend(data)

        self.stdout.write(f"Parsed {len(entries)} form-copy entries.")

        # Walk entries, find catalog matches
        match_eid = 0; match_name = 0; no_match = 0; would_skip = 0
        skipped_eids = []
        plan = []  # list of (catalog_obj, fields_dict)
        for e in entries:
            eid = e.get("external_id")
            name = e.get("name")
            if not eid and not name:
                self.stdout.write(self.style.WARNING(
                    f"  ⚠ entry missing both external_id and name — skipping"
                ))
                continue

            obj = None
            if eid:
                obj = ExerciseCatalog.objects.filter(external_id=eid).first()
                if obj:
                    match_eid += 1
            if obj is None and name:
                obj = ExerciseCatalog.objects.filter(name__iexact=name).first()
                if obj:
                    match_name += 1
            if obj is None:
                no_match += 1
                skipped_eids.append(eid or name)
                continue

            # Skip if already populated and not overwriting
            if not overwrite and (obj.form_description or obj.common_mistakes or obj.breathing_cues):
                would_skip += 1
                continue

            fields = {}
            if "form_description" in e:
                fields["form_description"] = str(e["form_description"]).strip()
            if "common_mistakes" in e:
                fields["common_mistakes"] = str(e["common_mistakes"]).strip()
            if "breathing_cues" in e:
                fields["breathing_cues"] = str(e["breathing_cues"]).strip()
            if "instructions" in e:
                fields["instructions"] = str(e["instructions"]).strip()
            plan.append((obj, fields))

        self.stdout.write(
            f"\nMatched: {match_eid} by external_id, {match_name} by name. "
            f"No match: {no_match}. Already populated (skipped): {would_skip}. "
            f"To write: {len(plan)}."
        )

        if no_match and skipped_eids:
            self.stdout.write(self.style.WARNING(
                f"  Sample no-match: {skipped_eids[:5]}"
            ))

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry-run — no DB writes."))
            return

        with transaction.atomic():
            for obj, fields in plan:
                for k, v in fields.items():
                    setattr(obj, k, v)
                obj.save(update_fields=list(fields.keys()))

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Wrote form copy to {len(plan)} ExerciseCatalog rows."
        ))
