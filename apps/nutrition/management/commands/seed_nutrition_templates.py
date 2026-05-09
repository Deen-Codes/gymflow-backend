"""T1.7 — Seed the curated NutritionTemplate rows from YAML.

Idempotent. Re-running the command upserts each row by `slug`. Run on
Render shell after the migration:

    python manage.py migrate nutrition
    python manage.py seed_nutrition_templates

Source data lives at `apps/nutrition/seed/templates/*.yaml`.
"""
import glob
import os

import yaml
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.nutrition.models import NutritionTemplate


class Command(BaseCommand):
    help = "Upsert NutritionTemplate rows from apps/nutrition/seed/templates/*.yaml"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse + validate YAML, log what would change, write nothing.",
        )

    def handle(self, *args, **opts):
        seed_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "seed", "templates",
        )
        seed_dir = os.path.abspath(seed_dir)

        files = sorted(glob.glob(os.path.join(seed_dir, "*.yaml")))
        if not files:
            self.stderr.write(self.style.WARNING(
                f"No YAML files found in {seed_dir}",
            ))
            return

        all_rows: list[dict] = []
        for path in files:
            with open(path, encoding="utf-8") as fh:
                rows = yaml.safe_load(fh) or []
            if not isinstance(rows, list):
                self.stderr.write(self.style.ERROR(
                    f"{path}: top-level must be a list, got {type(rows).__name__}",
                ))
                continue
            self.stdout.write(f"  {os.path.basename(path)}: {len(rows)} rows")
            all_rows.extend(rows)

        if opts["dry_run"]:
            self.stdout.write(self.style.NOTICE(
                f"[dry-run] would upsert {len(all_rows)} templates"
            ))
            for r in all_rows:
                self.stdout.write(f"    {r.get('slug')}: {r.get('name')}")
            return

        created = 0
        updated = 0
        with transaction.atomic():
            for r in all_rows:
                slug = r.get("slug")
                if not slug:
                    self.stderr.write(self.style.ERROR(
                        f"Missing slug in row: {r}",
                    ))
                    continue
                defaults = {
                    "name":                 r.get("name", slug.replace("_", " ").title()),
                    "tagline":              r.get("tagline", ""),
                    "summary":              (r.get("summary") or "").strip(),
                    "protein_g_per_kg":     float(r.get("protein_g_per_kg", 1.8)),
                    "fat_g_per_kg":         float(r.get("fat_g_per_kg", 0.8)),
                    "kcal_delta_vs_tdee":   int(r.get("kcal_delta_vs_tdee", 0)),
                    "goal_alignment":       r.get("goal_alignment", ""),
                    "dietary_compatibility": r.get("dietary_compatibility", ""),
                    "pace_label":           r.get("pace_label", ""),
                    "sort_order":           int(r.get("sort_order", 100)),
                    "is_published":         bool(r.get("is_published", True)),
                }
                _, was_created = NutritionTemplate.objects.update_or_create(
                    slug=slug, defaults=defaults,
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"Templates upserted — {created} created, {updated} updated.",
        ))
