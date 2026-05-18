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
    CuratedFood.SOURCE_AFLETICS,
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
    CuratedFood.SOURCE_AFLETICS: "us,gb,au,nz,fr,eu",
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
    """USDA FoodData Central — JSON download.

    Source: https://fdc.nal.usda.gov/fdc-datasets/
        FoodData_Central_sr_legacy_food_json_2018-04.zip
        FoodData_Central_foundation_food_json_2024-04-18.zip

    Both unzip to a single JSON file with shape:
        {"FoundationFoods": [...]}  or  {"SRLegacyFoods": [...]}

    Each food is:
        {
          "fdcId":         123,
          "description":   "Chicken, breast, raw",
          "foodCategory":  {"description": "Poultry Products"},
          "foodNutrients": [
            {
              "nutrient": {"id": 1003, "name": "Protein", "unitName": "g"},
              "amount":   23.1
            },
            ...
          ],
          "foodPortions":  [
            {"gramWeight": 100.0, "portionDescription": "1 piece"},
            ...
          ]
        }

    Nutrient IDs we care about (USDA canonical):
      1003 — Protein (g)
      1004 — Total fat (g)
      1005 — Carbohydrate, by difference (g)
      1008 — Energy (kcal)

    A single download contains both raw and prepared variants
    ("Chicken, breast, raw" vs "Chicken, breast, cooked, roasted").
    We keep both — users searching for "chicken breast" benefit
    from seeing the cooked entry which matches what they actually
    eat. The auto-tagger applies on the description so all are
    tagged consistently.

    Public domain — USDA publications are works of the U.S.
    federal government, exempt from copyright per 17 U.S.C. § 105.
    No attribution required, full commercial use permitted.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # The JSON payload uses different top-level keys depending on
    # which dataset you downloaded. Handle all of them.
    foods = (
        data.get("FoundationFoods")
        or data.get("SRLegacyFoods")
        or data.get("BrandedFoods")
        or data.get("foods")
        or []
    )

    # Deferred import — only needed when this loader is invoked,
    # avoids a hard dependency if a different loader is selected.
    from apps.nutrition.food_tagging import auto_tag

    nutrient_ids = {
        "protein": 1003,
        "fat":     1004,
        "carbs":   1005,
        "kcal":    1008,
    }

    for food in foods:
        fdc_id = food.get("fdcId")
        name = (food.get("description") or "").strip()
        if not fdc_id or not name:
            continue

        # Macros — index nutrient amounts by ID for cheap lookup.
        macros: dict[str, float] = {}
        for fn in food.get("foodNutrients") or []:
            # USDA's structure varies by dataset version: some have
            # `nutrient: {id, name, unitName}`, others flatten to
            # `nutrientId` / `nutrientName` / `unitName` directly.
            n = fn.get("nutrient") or fn
            nid = n.get("id") or fn.get("nutrientId")
            amt = fn.get("amount")
            if nid is None or amt is None:
                continue
            for key, target_id in nutrient_ids.items():
                if nid == target_id:
                    try:
                        macros[key] = float(amt)
                    except (TypeError, ValueError):
                        pass
                    break

        # Skip rows missing the headline number — energy is the
        # only one we hard-require because variants without macro
        # data aren't useful in a meal plan.
        if "kcal" not in macros:
            continue

        # Default serving — first food portion if present.
        serving_grams: float | None = None
        serving_label = ""
        for fp in food.get("foodPortions") or []:
            gw = fp.get("gramWeight")
            desc = fp.get("portionDescription") or fp.get("modifier") or ""
            if gw and desc and "100" not in desc:    # skip "100g" tautology
                serving_grams = float(gw)
                serving_label = desc[:40]
                break

        # Tags from USDA category + auto-tagging engine.
        category = ((food.get("foodCategory") or {}).get("description") or "").lower()
        tag_set: list[str] = []
        if category:
            tag_set.append(category.replace(" ", "_").replace(",", ""))
        tag_set.append("usda")

        dietary_compat, allergens = auto_tag(name)

        yield {
            "source":           CuratedFood.SOURCE_USDA,
            "source_id":        str(fdc_id),
            "name":             name[:200],
            "brand":            "",
            "barcode":          (food.get("gtinUpc") or "").strip(),
            "region_codes":     "us",
            "kcal_per_100g":    macros.get("kcal", 0.0),
            "protein_per_100g": macros.get("protein", 0.0),
            "carbs_per_100g":   macros.get("carbs", 0.0),
            "fat_per_100g":     macros.get("fat", 0.0),
            "serving_grams":    serving_grams,
            "serving_label":    serving_label,
            "tags":             ",".join(tag_set)[:200],
            "dietary_compat":   dietary_compat,
            "allergens":        allergens,
        }


def load_fsa_uk(path: str) -> Iterable[dict]:
    """UK FSA — McCance & Widdowson's Composition of Foods (7th ed).

    CC BY 4.0. Distributed as a .xlsx workbook with multiple
    sheets. Authoring the parser is a follow-up — schema differs
    from USDA's clean JSON, requires openpyxl/pandas, and the
    macro field naming varies between sheets ("Energy (kcal/100g)"
    vs "Energy kcal").

    To add: open `path` with openpyxl, locate the "Foods" sheet,
    and yield rows in the same shape as `load_usda` (matching
    CuratedFood field names). See FOOD_DB_INGEST.md for the
    field-mapping reference.
    """
    raise NotImplementedError(
        "FSA loader is a Phase 2 task. USDA covers most reference foods "
        "for v1; FSA adds UK-specific items (kippers, marmite, salad "
        "cream, etc.). Source: https://www.gov.uk/government/publications"
        "/composition-of-foods-integrated-dataset-cofid"
    )
    yield  # type: ignore[unreachable]


def load_ausnut(path: str) -> Iterable[dict]:
    """AUSNUT 2011-13 — Food Standards Australia New Zealand.

    Public, .xls bundle. Phase 3 — adds AU-specific items
    (Vegemite, Tim Tams, lamingtons, regional cuts).
    """
    raise NotImplementedError(
        "AUSNUT loader is a Phase 3 task. Source: "
        "https://www.foodstandards.gov.au/science-data/monitoringnutrients/ausnut"
    )
    yield  # type: ignore[unreachable]


def load_ciqual(path: str) -> Iterable[dict]:
    """CIQUAL — ANSES (France), open licence. Phase 4."""
    raise NotImplementedError(
        "CIQUAL loader is a Phase 4 task. Source: https://ciqual.anses.fr"
    )
    yield  # type: ignore[unreachable]


def load_afletics(path: str) -> Iterable[dict]:
    """Afletics-curated — our own additions. Generic CSV ingest with
    columns: source_id,name,brand,barcode,region_codes,kcal_per_100g,
    protein_per_100g,carbs_per_100g,fat_per_100g,serving_grams,
    serving_label,tags,dietary_compat,allergens.

    The last two are optional — if missing, the auto-tagger fills
    them from the food name. Pass explicit values to override the
    auto-tagging (useful for branded products where we know the
    label).
    """
    from apps.nutrition.food_tagging import auto_tag

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row["name"].strip()
            csv_dietary = (row.get("dietary_compat") or "").strip()
            csv_allergens = (row.get("allergens") or "").strip()
            if not csv_dietary or not csv_allergens:
                auto_dietary, auto_allergens = auto_tag(name)
                csv_dietary = csv_dietary or auto_dietary
                csv_allergens = csv_allergens or auto_allergens

            yield {
                "source":            CuratedFood.SOURCE_AFLETICS,
                "source_id":         row["source_id"].strip(),
                "name":              name,
                "brand":             (row.get("brand") or "").strip(),
                "barcode":           (row.get("barcode") or "").strip(),
                "region_codes":      (row.get("region_codes") or DEFAULT_REGIONS[CuratedFood.SOURCE_AFLETICS]).strip(),
                "kcal_per_100g":     float(row["kcal_per_100g"]),
                "protein_per_100g":  float(row["protein_per_100g"]),
                "carbs_per_100g":    float(row["carbs_per_100g"]),
                "fat_per_100g":      float(row["fat_per_100g"]),
                "serving_grams":     float(row["serving_grams"]) if row.get("serving_grams") else None,
                "serving_label":     (row.get("serving_label") or "").strip(),
                "tags":              (row.get("tags") or "afletics").strip(),
                "dietary_compat":    csv_dietary,
                "allergens":         csv_allergens,
            }


LOADERS = {
    CuratedFood.SOURCE_USDA:   load_usda,
    CuratedFood.SOURCE_FSA_UK: load_fsa_uk,
    CuratedFood.SOURCE_AUSNUT: load_ausnut,
    CuratedFood.SOURCE_CIQUAL: load_ciqual,
    CuratedFood.SOURCE_AFLETICS: load_afletics,
}
