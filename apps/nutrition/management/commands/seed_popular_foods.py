"""
NUTRITION-DB seed loader — popular foods bootstrap.

Loads one or many YAML files under `apps/nutrition/seed/` into the
`CuratedFood` table. Idempotent: re-running updates existing rows
matched by `(source, source_id)` without creating duplicates.

Usage:
    python manage.py seed_popular_foods
        # loads `apps/nutrition/seed/popular_foods.yaml` plus every
        # other `.yaml` in `apps/nutrition/seed/` (FOOD-DB-V2 batches
        # — `whole_foods.yaml`, `tesco.yaml`, `mcdonalds.yaml`, …).

    python manage.py seed_popular_foods --path /custom/path.yaml
        # one specific file.

    python manage.py seed_popular_foods --dry-run
        # validate everything, write nothing.

To extend: drop a new `.yaml` into `apps/nutrition/seed/` and re-run.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.nutrition.models import CuratedFood


REQUIRED_FIELDS = {
    "source", "source_id", "name",
    "kcal_per_100g", "protein_per_100g",
    "carbs_per_100g", "fat_per_100g",
}

OPTIONAL_FIELDS = {
    "brand", "barcode", "region_codes",
    "serving_grams", "serving_label",
    "tags", "dietary_compat", "allergens",
    "portion_unit", "unit_grams",
}

VALID_PORTION_UNITS = {
    "grams", "ml", "piece", "slice", "wrap", "scoop",
    "tbsp", "tsp", "cup", "oz", "egg", "bar",
    "can", "bottle", "pack", "pint", "shot", "meal",
}

DEFAULT_SEED_DIR = Path(__file__).resolve().parents[2] / "seed"
DEFAULT_SEED_PATH = DEFAULT_SEED_DIR / "popular_foods.yaml"


class Command(BaseCommand):
    help = "Seed CuratedFood from the popular_foods.yaml hand-curated bundle."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default=None,
            help=(
                "Path to a specific seed YAML. "
                "Default behaviour is to load every .yaml in apps/nutrition/seed/."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate the YAML without writing to the DB.",
        )

    def handle(self, *args, **opts):
        path = opts["path"]
        dry_run = opts["dry_run"]

        # Build the file list. --path overrides; otherwise glob the
        # whole seed directory so newly-added YAMLs (tesco.yaml,
        # mcdonalds.yaml, …) get picked up automatically.
        if path:
            if not os.path.exists(path):
                raise CommandError(f"Seed file not found: {path}")
            seed_files = [Path(path)]
        else:
            seed_files = sorted(DEFAULT_SEED_DIR.glob("*.yaml"))
            if not seed_files:
                raise CommandError(f"No YAML files in {DEFAULT_SEED_DIR}")

        # Load + concatenate every YAML into a single entries list.
        entries = []
        for f_path in seed_files:
            self.stdout.write(f"Loading {f_path.name}…")
            with open(f_path, "r") as f:
                chunk = yaml.safe_load(f)
            if chunk is None:
                continue
            if not isinstance(chunk, list):
                raise CommandError(
                    f"{f_path.name}: expected a YAML list at root, "
                    f"got {type(chunk).__name__}",
                )
            entries.extend(chunk)

        # Validate all entries upfront — we want a hard fail BEFORE
        # we start writing rows so partial-write states don't happen.
        validation_errors = []
        seen_ids = set()
        for i, e in enumerate(entries):
            if not isinstance(e, dict):
                validation_errors.append(
                    f"Entry {i}: expected dict, got {type(e).__name__}",
                )
                continue
            missing = REQUIRED_FIELDS - set(e.keys())
            if missing:
                validation_errors.append(
                    f"Entry {i} ({e.get('source_id', '?')}): missing {sorted(missing)}",
                )
            sid = e.get("source_id")
            key = (e.get("source"), sid)
            if key in seen_ids:
                validation_errors.append(
                    f"Entry {i}: duplicate (source, source_id)={key}",
                )
            seen_ids.add(key)

            # Macro sanity — kcal vs (4P + 4C + 9F) within tolerance.
            try:
                kcal = float(e.get("kcal_per_100g", 0))
                p = float(e.get("protein_per_100g", 0))
                c = float(e.get("carbs_per_100g", 0))
                f_g = float(e.get("fat_per_100g", 0))
                if kcal > 5:  # skip near-zero items (water, etc.)
                    calc = (p * 4) + (c * 4) + (f_g * 9)
                    if calc > 0:
                        delta = abs(calc - kcal) / kcal
                        if delta > 0.40:
                            self.stdout.write(self.style.WARNING(
                                f"  Macro check warning: {e.get('source_id')} — "
                                f"kcal={kcal:.0f} vs calculated={calc:.0f} "
                                f"(delta {delta:.0%})"
                            ))
            except (TypeError, ValueError):
                validation_errors.append(
                    f"Entry {i}: numeric macro fields invalid",
                )

            # FOOD-DB-V2 — portion-unit validation. If a portion_unit
            # other than "grams" is specified, unit_grams MUST also
            # be present (otherwise the iOS picker can't compute
            # per-N-units macros).
            portion_unit = e.get("portion_unit") or "grams"
            if portion_unit not in VALID_PORTION_UNITS:
                validation_errors.append(
                    f"Entry {i} ({e.get('source_id', '?')}): "
                    f"portion_unit={portion_unit!r} not in {sorted(VALID_PORTION_UNITS)}"
                )
            if portion_unit != "grams" and e.get("unit_grams") is None:
                validation_errors.append(
                    f"Entry {i} ({e.get('source_id', '?')}): "
                    f"portion_unit={portion_unit!r} requires unit_grams (gram-equivalent of 1 unit)"
                )

        if validation_errors:
            self.stdout.write(self.style.ERROR(
                f"Validation failed — {len(validation_errors)} errors:"
            ))
            for err in validation_errors[:20]:
                self.stdout.write(self.style.ERROR(f"  • {err}"))
            if len(validation_errors) > 20:
                self.stdout.write(self.style.ERROR(
                    f"  …and {len(validation_errors) - 20} more"
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
                    "name":             str(e["name"]),
                    "brand":            str(e.get("brand", "") or ""),
                    "barcode":          str(e.get("barcode", "") or ""),
                    "region_codes":     str(e.get("region_codes", "") or ""),
                    "kcal_per_100g":    float(e["kcal_per_100g"]),
                    "protein_per_100g": float(e["protein_per_100g"]),
                    "carbs_per_100g":   float(e["carbs_per_100g"]),
                    "fat_per_100g":     float(e["fat_per_100g"]),
                    "serving_grams":    e.get("serving_grams"),
                    "serving_label":    str(e.get("serving_label", "") or ""),
                    "tags":             str(e.get("tags", "") or ""),
                    "dietary_compat":   str(e.get("dietary_compat", "") or ""),
                    "allergens":        str(e.get("allergens", "") or ""),
                    # FOOD-DB-V2 — portion units
                    "portion_unit":     str(e.get("portion_unit", "grams") or "grams"),
                    "unit_grams":       e.get("unit_grams"),
                }
                _, was_created = CuratedFood.objects.update_or_create(
                    source=e["source"],
                    source_id=e["source_id"],
                    defaults=defaults,
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done. Created {created}, updated {updated}, total {created + updated}."
        ))
