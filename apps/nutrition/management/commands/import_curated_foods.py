"""
NUTRITION-DB (#105) — multi-region food catalog ingest.

Usage:

    python manage.py import_curated_foods --source=usda --path=/path/to/usda.csv
    python manage.py import_curated_foods --source=fsa_uk --path=/path/to/mccance.csv
    python manage.py import_curated_foods --source=ausnut --path=/path/to/ausnut.csv
    python manage.py import_curated_foods --source=ciqual --path=/path/to/ciqual.csv

Per-source loaders below are stubs. Each source publishes data in a
different shape:

  • USDA FDC      — public domain, foundation foods CSV bundle
                    https://fdc.nal.usda.gov/download-datasets.html
  • UK FSA        — McCance & Widdowson's Composition of Foods,
                    CC BY 4.0, downloadable from food.gov.uk
  • AUSNUT 2011-13 — Food Standards Australia New Zealand,
                    public, .xls bundle
  • CIQUAL        — ANSES (France), open licence, .csv

Implementation note: each loader normalises rows to the same
`CuratedFood`-shaped dict, then we batch `update_or_create` against
`(source, source_id)` so re-runs are idempotent.

Cooked-vs-raw normalisation: USDA bundles both forms; we prefer
"as eaten" (cooked rice, grilled chicken, etc.) for staples. Loaders
filter to the cooked form via the `data_type=foundation_food` flag
where applicable.

Licensing: each loader ALSO writes the source license to the
`tags` field (e.g. `usda,public_domain`) so we can audit the
catalog provenance later.

This command is the entry point — actual loaders live below as
stubs to be filled in per source. The schema, idempotency, and
batching are all wired so dropping in a real loader is the only
remaining work.
"""
from __future__ import annotations

import csv
from typing import Iterable

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.nutrition.models import CuratedFood


SUPPORTED_SOURCES = {
    CuratedFood.SOURCE_USDA,
    CuratedFood.SOURCE_FSA_UK,
    CuratedFood.SOURCE_AUSNUT,
    CuratedFood.SOURCE_CIQUAL,
    CuratedFood.SOURCE_MARROW,
}

# Default region distribution per source. Ingest can override per-row
# (e.g. if a CIQUAL row is also commonly eaten in the UK we'd add
# `gb` to its region_codes manually). The defaults reflect the
# canonical user base of each source.
DEFAULT_REGIONS = {
    CuratedFood.SOURCE_USDA:   "us",
    CuratedFood.SOURCE_FSA_UK: "gb",
    CuratedFood.SOURCE_AUSNUT: "au,nz",
    CuratedFood.SOURCE_CIQUAL: "fr,eu",
    CuratedFood.SOURCE_MARROW: "us,gb,au,nz,fr,eu",
}


class Command(BaseCommand):
    help = "Ingest curated foods from a public source (USDA / FSA / AUSNUT / CIQUAL)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            required=True,
            choices=sorted(SUPPORTED_SOURCES),
        )
        parser.add_argument(
            "--path",
            required=True,
            help="Path to the source CSV/JSON file.",
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
            help="Only ingest the first N rows (smoke test).",
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

        self.stdout.write(f"Parsed {len(rows)} rows from {source} at {path}.")

        if dry_run:
            self.stdout.write("Dry run — no DB writes.")
            return

        created = updated = 0
        with transaction.atomic():
            for row in rows:
                obj, was_created = CuratedFood.objects.update_or_create(
                    source=row["source"],
                    source_id=row["source_id"],
                    defaults={k: v for k, v in row.items() if k not in ("source", "source_id")},
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created {created}, updated {updated} rows in CuratedFood."
            )
        )


# --------------------------------------------------------------------
# Per-source loaders. Each yields dicts matching CuratedFood fields
# (source, source_id, name, brand, barcode, region_codes,
#  kcal/protein/carbs/fat per 100g, serving_grams, serving_label,
#  tags). All currently STUBS — fill in when wiring real data.
# --------------------------------------------------------------------


def load_usda(path: str) -> Iterable[dict]:
    """USDA FoodData Central — Foundation Foods CSV.

    Schema: fdc_id, description, food_category, ... + nutrient rows
    in a separate `food_nutrient.csv` you join on fdc_id.

    TODO: implement. The shape published is two CSVs (foods +
    nutrients) — typical loader streams the small one into memory
    keyed by id, then joins the large one on the fly.
    """
    raise NotImplementedError(
        "USDA loader stub. See https://fdc.nal.usda.gov/download-datasets.html "
        "for the bundle shape."
    )
    # pragma: no cover — generator placeholder so caller's
    # `list(loader(...))` succeeds in dry-run testing.
    yield  # type: ignore[unreachable]


def load_fsa_uk(path: str) -> Iterable[dict]:
    """UK FSA — McCance & Widdowson's Composition of Foods.

    CC BY 4.0. Distributed as a .xlsx with one row per food. We
    tag each row with `region_codes="gb"` and `tags="fsa_uk,cc_by"`.
    """
    raise NotImplementedError(
        "FSA loader stub. Source: https://www.food.gov.uk/research/food-composition-data"
    )
    yield  # type: ignore[unreachable]


def load_ausnut(path: str) -> Iterable[dict]:
    """AUSNUT 2011-13 — Food Standards Australia NZ."""
    raise NotImplementedError(
        "AUSNUT loader stub. Source: https://www.foodstandards.gov.au/science-data/monitoringnutrients/ausnut"
    )
    yield  # type: ignore[unreachable]


def load_ciqual(path: str) -> Iterable[dict]:
    """CIQUAL — ANSES (France), open licence."""
    raise NotImplementedError(
        "CIQUAL loader stub. Source: https://ciqual.anses.fr"
    )
    yield  # type: ignore[unreachable]


def load_marrow(path: str) -> Iterable[dict]:
    """Marrow-curated — our own additions. Generic CSV ingest with
    columns: source_id,name,brand,barcode,region_codes,kcal_per_100g,
    protein_per_100g,carbs_per_100g,fat_per_100g,serving_grams,
    serving_label,tags. This one is real — we use it for hand-curated
    branded items (Tesco / Whole Foods) we want in the catalog.
    """
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield {
                "source":            CuratedFood.SOURCE_MARROW,
                "source_id":         row["source_id"].strip(),
                "name":              row["name"].strip(),
                "brand":             (row.get("brand") or "").strip(),
                "barcode":           (row.get("barcode") or "").strip(),
                "region_codes":      (row.get("region_codes") or DEFAULT_REGIONS[CuratedFood.SOURCE_MARROW]).strip(),
                "kcal_per_100g":     float(row["kcal_per_100g"]),
                "protein_per_100g":  float(row["protein_per_100g"]),
                "carbs_per_100g":    float(row["carbs_per_100g"]),
                "fat_per_100g":      float(row["fat_per_100g"]),
                "serving_grams":     float(row["serving_grams"]) if row.get("serving_grams") else None,
                "serving_label":     (row.get("serving_label") or "").strip(),
                "tags":              (row.get("tags") or "marrow").strip(),
            }


LOADERS = {
    CuratedFood.SOURCE_USDA:   load_usda,
    CuratedFood.SOURCE_FSA_UK: load_fsa_uk,
    CuratedFood.SOURCE_AUSNUT: load_ausnut,
    CuratedFood.SOURCE_CIQUAL: load_ciqual,
    CuratedFood.SOURCE_MARROW: load_marrow,
}
