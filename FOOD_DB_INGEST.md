# FOOD-DB-INGEST — owned multi-source food catalogue

Build a curated, multi-source, comprehensively-tagged food
database that powers AI meal planning, dietary filtering, and
cross-region search. Replaces runtime third-party calls in the
steady state.

---

## Sources + licensing

| Source | Items | Region | Licence | Status |
|---|---|---|---|---|
| **USDA SR Legacy** | ~7,700 | US | Public domain (17 U.S.C. § 105) | **Live** (loader real, deploy script ready) |
| **USDA Foundation Foods** | ~400 | US | Public domain | **Live** (loader real, same JSON shape) |
| **UK FSA McCance & Widdowson** | ~3,000 | UK | CC BY 4.0 | Phase 2 (loader stubbed) |
| **AUSNUT 2011-13** | ~5,700 | AU/NZ | Public | Phase 3 (loader stubbed) |
| **CIQUAL** | ~3,200 | EU/FR | Open licence | Phase 4 (loader stubbed) |
| **Marrow-curated** | manual | global | Owned | **Live** (CSV loader real) |

**Open Food Facts intentionally excluded** from ingest — its
CC BY-SA copyleft would force the whole derivative DB under CC
BY-SA, blocking commercial bake-in. OFF stays as a runtime
barcode lookup on iOS via the existing `FoodLookupService` path.

---

## What's tagged

Each `CuratedFood` row carries:

### `dietary_compat` (CharField, comma-separated)
Drawn from this fixed vocabulary, ONLY present when the food
is provably compatible:
- `halal` — no pork, no alcohol, no gelatin
- `kosher` — no pork, no shellfish, no rabbit/eel/catfish, no gelatin
- `vegan` — no animal products
- `vegetarian` — no meat, no fish, no gelatin
- `pescatarian` — no meat, no gelatin
- `gluten_free` — no wheat / rye / barley / oats / spelt / etc.
- `dairy_free` — no milk / cheese / butter / etc.

**Conservative semantics.** A `halal` tag means "compatible with
a halal diet" — NOT "this specific item was slaughtered halal".
Real provenance requires certified labelling on the consumed
item; we surface compatibility based on absence of haram
ingredients only. Same for `kosher`.

### `allergens` (CharField, comma-separated)
EU 1169/2011 Annex II "top 14":
- `milk` · `eggs` · `gluten` · `peanuts` · `tree_nuts` · `sesame`
- `soy` · `fish` · `crustaceans` · `molluscs` · `celery`
- `mustard` · `lupin` · `sulphites`

Auto-tagged via name-pattern matching on import (see
`apps/nutrition/food_tagging.py`). Aggressive — over-tags rather
than miss. Empty string means "no major allergens detected
**from the name alone**" — branded items always need real label
data.

---

## Initial deployment (USDA only — Phase 1)

```bash
# On Render shell:
bash scripts/seed_food_db.sh
```

What that script does:
1. Migrates nutrition (0009 + 0010).
2. Downloads USDA SR Legacy JSON (~25MB).
3. Downloads USDA Foundation JSON (~5MB).
4. Imports both — yields ~8,100 rows with auto-tagged
   `dietary_compat` and `allergens`.
5. Runs `dedup_curated_foods` — currently a no-op (single
   source) but ready for FSA on top.
6. Reports the final catalogue size.

Expected output:
```
  total rows: ~8,100
  active (non-duplicate): ~8,100
  by source:
    usda      8100
```

After this runs, AI nutrition build endpoints can query
`CuratedFood` for foods matching dietary preferences:
```python
CuratedFood.objects.filter(
    dietary_compat__contains="halal",
).exclude(allergens__contains="peanuts")
```

---

## Adding sources later

### Phase 2 — UK FSA McCance & Widdowson (CC BY 4.0)
1. Download the workbook from the Food Standards Agency:
   https://www.gov.uk/government/publications/composition-of-foods-integrated-dataset-cofid
2. Extract the .xlsx. The "Foods" sheet holds one row per food
   with macro columns (`Energy (kcal/100g)`, `Protein g`, etc.).
3. Implement `load_fsa_uk()` in `apps/nutrition/management/
   commands/import_curated_foods.py` — yield CuratedFood-shaped
   dicts with `source=fsa_uk`, `region_codes="gb"`,
   `tags="fsa_uk,cc_by_4"`.
4. Run the import:
   `python manage.py import_curated_foods --source=fsa_uk --path=/tmp/fsa.xlsx`
5. Run dedup: `python manage.py dedup_curated_foods` — flags
   "Chicken, breast, raw" duplicates between USDA + FSA, keeps
   USDA (higher priority).

### Phase 3 — AUSNUT 2011-13
Same shape, different schema. AUSNUT's macro columns use
different names; the loader needs a column-mapping dict.

### Phase 4 — CIQUAL
French dataset, columns in French. Translation layer in the
loader, but the CuratedFood schema doesn't change.

---

## Re-running

The whole ingest is idempotent. `update_or_create` against
`(source, source_id)` means re-running the same loader on the
same data produces zero new rows. Re-running with a newer
USDA dataset updates the macros + tags but preserves the row
identity (so AI proposals referencing those rows stay valid).

---

## Maintenance

- **Re-tag existing rows when the tagging engine improves** —
  the auto-tagger is in `apps/nutrition/food_tagging.py`. Run a
  one-off shell command:
  ```python
  python manage.py shell -c "
  from apps.nutrition.models import CuratedFood
  from apps.nutrition.food_tagging import auto_tag
  for f in CuratedFood.objects.iterator():
      d, a = auto_tag(f.name)
      if d != f.dietary_compat or a != f.allergens:
          f.dietary_compat = d
          f.allergens = a
          f.save(update_fields=['dietary_compat', 'allergens'])
  "
  ```
- **Add a Marrow override** (e.g. for a branded item where you
  know the actual halal certification) — write a CSV with
  explicit `dietary_compat` / `allergens` columns and import
  via `--source=marrow`. Marrow has the highest dedup priority
  so it overrides USDA's auto-tagged values.
