"""LINK-EXERCISE-CATALOG — back-link every Exercise row to its
ExerciseCatalog entry by case-insensitive name match.

Why this command — SOLO programmes (Starting Strength, StrongLifts,
PPL, etc.) seed Exercise rows with just a name and set targets,
without setting `catalog_item_id`. iOS then renders the workout
fine but the form-detail bottom sheet (`/api/workouts/catalog/<id>/`)
short-circuits because there's no catalog_id to fetch with — the
user sees an empty sheet.

This command walks every Exercise row where `catalog_item_id IS
NULL`, looks up an ExerciseCatalog row whose `name` matches case-
insensitively, and sets the FK. Idempotent: re-runs only touch
rows that are still unlinked.

Wired into `build.sh` after the YAML form-copy seed so any newly-
imported catalog rows can pick up the missing back-links on every
deploy.

Usage:
    python manage.py link_exercises_to_catalog
    python manage.py link_exercises_to_catalog --dry-run
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import F, Q

from apps.workouts.models import Exercise, ExerciseCatalog


class Command(BaseCommand):
    help = "Back-link Exercise rows to ExerciseCatalog rows by name."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be linked, don't write.",
        )

    def handle(self, *args, **opts):
        dry_run = opts["dry_run"]

        # Pull every Exercise with no catalog link. Working set is
        # small (a few hundred at most for the SOLO programme library)
        # so we can afford to load names into memory and do the match
        # per row.
        unlinked = Exercise.objects.filter(catalog_item__isnull=True)
        total = unlinked.count()
        if not total:
            self.stdout.write("Nothing to link — every Exercise already has a catalog FK.")
            return

        self.stdout.write(f"Scanning {total} unlinked Exercise rows…")

        linked = 0
        no_match = 0
        no_match_names: list[str] = []

        for ex in unlinked.iterator(chunk_size=200):
            if not ex.name:
                no_match += 1
                continue
            cat = (
                ExerciseCatalog.objects
                .filter(name__iexact=ex.name)
                .first()
            )
            if cat is None:
                no_match += 1
                if len(no_match_names) < 20:
                    no_match_names.append(ex.name)
                continue
            if not dry_run:
                ex.catalog_item = cat
                ex.save(update_fields=["catalog_item"])
            linked += 1

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"\nDRY RUN — {linked} would be linked, {no_match} have no catalog match."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\nDone. Linked {linked}/{total}. Unmatched: {no_match}."
            ))

        if no_match_names:
            self.stdout.write(self.style.WARNING(
                f"  Sample unmatched names: {no_match_names[:10]}"
            ))
            self.stdout.write(
                "  Unmatched rows stay nil — iOS detail sheet falls back to "
                "name + targets only for these. Add matching catalog rows to "
                "fix."
            )
