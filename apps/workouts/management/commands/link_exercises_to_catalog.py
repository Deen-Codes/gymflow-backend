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
insensitively, and sets the FK. With `--create-missing` (the
default in build.sh), it ALSO creates a stub ExerciseCatalog row
for any Exercise whose name has no match — guaranteeing 100%
linkage. The YAML loader then populates the form-copy fields on
the newly-created rows.

Idempotent: re-runs only touch rows that are still unlinked.

Wired into `build.sh` between import_exercise_catalog and
seed_exercise_form_copy so every catalog row exists with the right
name BEFORE the YAML pass tries to write copy to it.

Usage:
    python manage.py link_exercises_to_catalog
    python manage.py link_exercises_to_catalog --create-missing
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
        parser.add_argument(
            "--create-missing",
            action="store_true",
            help=(
                "Create a stub ExerciseCatalog row for any Exercise whose "
                "name has no match. Guarantees 100% linkage. The new rows "
                "are tagged with source=gymflow and an `external_id` "
                "derived from the slugified name so re-runs are idempotent."
            ),
        )

    def handle(self, *args, **opts):
        dry_run = opts["dry_run"]
        create_missing = opts["create_missing"]

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
        created_then_linked = 0
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
                if create_missing and not dry_run:
                    # Slug-style external_id keeps re-runs idempotent.
                    ext_id = _slugify_for_external_id(ex.name)
                    cat, _ = ExerciseCatalog.objects.get_or_create(
                        source=ExerciseCatalog.SOURCE_GYMFLOW,
                        external_id=ext_id,
                        defaults={
                            "name":         ex.name,
                            "is_published": True,
                        },
                    )
                    # If the row already existed (re-run after a name
                    # tweak), update the display name to current.
                    if cat.name != ex.name:
                        cat.name = ex.name
                        cat.save(update_fields=["name"])
                    created_then_linked += 1
                else:
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
                f"\nDRY RUN — {linked} would be linked"
                f"{f' (incl. {created_then_linked} via create-missing)' if create_missing else ''}, "
                f"{no_match} have no catalog match."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\nDone. Linked {linked}/{total}"
                f"{f' (created {created_then_linked} new catalog rows)' if created_then_linked else ''}. "
                f"Unmatched: {no_match}."
            ))

        if no_match_names:
            self.stdout.write(self.style.WARNING(
                f"  Sample unmatched names: {no_match_names[:10]}"
            ))
            self.stdout.write(
                "  Unmatched rows stay nil — iOS detail sheet falls back to "
                "name + targets only for these. Pass --create-missing OR add "
                "matching catalog rows to fix."
            )


def _slugify_for_external_id(name: str) -> str:
    """Stable external_id for a stub catalog row. Lowercase, alphanumeric
    + underscores. Matches the shape used elsewhere in the GymFlow
    catalog (e.g. `gymflow_back_squat`)."""
    cleaned = "".join(
        c.lower() if c.isalnum() else "_"
        for c in name.strip()
    )
    # Collapse repeated underscores + trim.
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return f"gymflow_{cleaned.strip('_')}"
