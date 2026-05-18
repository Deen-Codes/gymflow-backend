# FOOD_DB_INGEST.md — how we populate the food catalog

**Status:** shipped. Last updated 2026-05-09.
**Predecessor:** the original multi-source ingest plan (USDA + OFF + AUSNUT + CIQUAL bulk loaders) is archived at `../Afletics/_archive/docs_2026-05-09/FOOD_DB_INGEST_v1.md`. The decision to drop external sources is logged in `../Afletics/DECISIONS.md`.

## Single source: hand-curated YAML

```
apps/nutrition/seed/popular_foods.yaml
```

Every entry has the same shape:

```yaml
- source_id: tesco_greek_yogurt_0
  name: Greek Yogurt 0% Fat
  brand: Tesco
  region_codes: gb
  kcal_per_100g: 57
  protein_per_100g: 10.0
  carbs_per_100g: 4.0
  fat_per_100g: 0.0
  serving_grams: 170
  serving_label: 1 pot (170 g)
  tags: staple,branded,dairy,high_protein
  dietary_compat: vegetarian,kosher,halal,gluten_free
  allergens: milk
```

The `source` field on every entry is `afletics`. We ingest no other DB.

## Loader command

```bash
python manage.py seed_popular_foods
```

- Idempotent: `CuratedFood.objects.update_or_create(source="afletics", source_id=X, defaults=...)`
- Validates required fields before any write
- Runs the macro-math sanity check `(p × 4) + (c × 4) + (f × 9)`; warns if the kcal value is off by more than 10% (alcohol items expected to warn)

Output on a clean run:

```
Created 200, updated 0, total 200
```

Output on a re-run after edits:

```
Created 0, updated 12, total 200
```

## Where to source numbers from

In priority order:

1. **Restaurant chains.** UK Calorie Labelling Regulations 2022 require chains > 250 employees to publish kcal in-store and online. Every UK chain we curate has a published nutrition + allergen page; we use those.
2. **UK supermarket own-brand.** Each supermarket publishes a per-product nutrition table on the product page. Tesco, Sainsbury's, M&S, Aldi, Waitrose all have this online.
3. **Whole foods.** USDA FoodData Central is public domain and stable. We don't ingest it via API — we manually copy the macros for the food we want into the YAML.

No scraping. No user-generated edits. No community contributions. The DB only changes when we ship a new YAML through git.

## Adding a batch

1. Append to `popular_foods.yaml`
2. Bump the file version comment if you want
3. `git commit && git push`
4. Render auto-deploys
5. SSH the Render shell or wait for the post-deploy hook → `python manage.py seed_popular_foods`
6. iOS sees new entries within 5 minutes (in-memory search cache TTL)

## What's intentionally NOT here

- No `import_curated_foods.py` ingest from public-domain CSVs. The stub for that exists; it isn't wired. If we want USDA whole foods later we'd flesh it out, but every entry we've ingested so far is faster and more accurate hand-typed than scripted.
- No barcode field population. The `CuratedFood.barcode` column exists for legacy decode compat with old log entries; no current entries set it and the iOS app no longer scans.
- No image URLs. Adding food photos is a v2 — for now the post-search confirm sheet uses a generic fork-knife glyph.

## Path forward

See `../Afletics/NUTRITION_DB_DESIGN.md` §"Path forward — expanding to 1000+".
