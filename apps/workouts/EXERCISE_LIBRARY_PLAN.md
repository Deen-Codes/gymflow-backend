# Exercise Library Plan — to 1500

## Goal
Build to 1500 exercises (industry sweet spot: Fitbod ~1500, Jefit ~1300, Hevy ~800),
each with:
- Icon (start-frame static image — used on cinematic setup-workout background drop)
- Animation (plays on enlarge)
- Form description (proper setup + cues — shown on enlarge)
- Common mistakes (newline-delimited "don't…" list)
- Breathing cues (inhale/exhale timing)

## Phase status

| Phase | What | Status |
|---|---|---|
| 1 | Schema migration (level/mechanic/force/category/secondary_muscles + form_description/common_mistakes/breathing_cues) | ✅ written, awaiting deploy |
| 2 | Free Exercise DB import (~873 with full metadata + start-frame images) | ✅ JSON fetched, importer updated, dry-run passed |
| 3 | Curated YAML batches (+627 to reach 1500) | ⏳ planned, scoped below |
| 4 | Form descriptions (AI-drafted + curated review) | ⏳ after Phase 3 |
| 5 | Icon generation (replace yuhonas stock with branded start-frames) | ⏳ after Phase 4 |
| 6 | Animation commissioning (animation_url field already exists) | ⏳ separate workstream |
| 7 | iOS UI: enlarged exercise view shows form_description + animation | ⏳ after Phase 5 |

## Deploy commands (Phase 1 + 2)

After pushing the schema + importer changes:

```bash
cd ~/Documents/gymflow-backend
git add apps/workouts/ apps/nutrition/
git commit -m "EXERCISE-LIB-1500 + SEARCH-FIX: schema migration, FreeExerciseDB import, apostrophe search fix"
git push
```

Then on Render shell (after auto-deploy completes):

```bash
python manage.py migrate workouts
python manage.py import_exercise_catalog \
    --source=free_exercise_db \
    --path=apps/workouts/management/commands/data/free_exercise_db.json
```

Expected output: `Done. Created 873, updated 0 rows in ExerciseCatalog.`

Re-running the import is safe (idempotent on `(source, external_id)`).

## Phase 3 — Curated 627 plan

Free Exercise DB has gaps. Plan a phased curated push, mirroring the food-DB
batch pattern (YAML batches loaded via a `seed_exercises` command, dedup by
`(source=gymflow, external_id)`).

Coverage gaps after the 873 import:

| Gap | FreeDB count | Curated target |
|---|---|---|
| Glutes (primary) | 22 | +60 — hip thrust variants, RDLs, frog pumps, kickbacks, sumo, B-stance, single-leg |
| Cardio | 14 | +40 — sprint variants, intervals, rower, ski erg, assault bike, jump rope variants |
| TRX / suspension | ~5 (in "other") | +50 — full TRX library |
| Bands | 20 | +60 — banded variants of every basic |
| Functional / CrossFit | 0 specific | +80 — thrusters, wall balls, KB swings full, snatches, complexes, devil press, burpee variants |
| HIIT / plyo | 61 | +40 — tuck jumps, depth jumps, broad jumps, lateral bounds, sprint mechanics |
| Mobility / prehab | 123 stretching | +80 — CARs, dynamic warm-ups, foam rolling per muscle, sport-specific mobility |
| Lengthened-bias hypertrophy | minimal | +40 — modern stretch-emphasis variants per muscle |
| Powerlifting accessories | 38 | +50 — paused squats, deficit deadlifts, board press, pin press, deadlift variations |
| Olympic derivatives | 35 | +30 — high pulls, hang variations, push press, jerks, complexes |
| Core / stability | mixed | +50 — Pallof press, Copenhagen, dead bug, bird dog, hollow holds, anti-rotation |
| UK-specific naming | n/a | +30 — UK gym vocabulary aliases |
| Machine variants (modern) | 67 | +40 — Hammer Strength, Prime, plate-loaded variants, V-grip rows, Smith bench |
| Misc gaps | n/a | +27 |
| **Total Phase 3** | | **+627** |

Each curated entry needs:
- name, muscle_group, secondary_muscles, equipment, level, mechanic, force, category
- instructions (basic numbered steps)
- form_description (richer paragraph) — Phase 4 if not done in batch
- image_url (Phase 5 — generated/commissioned)

## Phase 4 — Form descriptions

For all 1500 (873 imported + 627 curated):
- AI-draft form_description (paragraph form, 2–4 sentences on setup/execution/key cues)
- AI-draft common_mistakes (3–5 bullet "Don't…" items)
- AI-draft breathing_cues (1–2 sentences on when to inhale/exhale)
- Quality review pass on the top 100 most-popular exercises (Big 6 lifts + their direct variants)

## Phase 5 — Icon generation

Free Exercise DB ships stock photos as the start-frame. They work as v1 icons but
look nothing like our brand. Replacement options (decision pending):

1. **AI-generated, consistent style** — Midjourney/SD with a tightly-defined
   style spec (silhouette/line art, brand palette). Fastest. Quality variance
   is the risk; needs a human review pass per icon.
2. **Custom illustration commission** — slowest, most expensive, highest
   quality and brand consistency.
3. **Layered placeholder approach** — keep yuhonas images on a tinted/
   desaturated overlay so they fit the cinematic look until proper icons
   land. Lowest effort to ship.

Default if no decision: **option 3 for v1, option 1 for v2** as a curated
bulk run after the 1500 entries are categorised.

## Phase 6 — Animation commissioning

`animation_url` field already exists in the schema. Animation strategy is
documented in `EXERCISE_ANIMATION_LIBRARY.md` (iOS repo). Lottie/.lottie
preferred, .mp4 fallback. Out of scope for this task — links into the
existing animation workstream.

## Phase 7 — iOS UI

Enlarged exercise view (tap an exercise card from the catalog or workout list):
- Hero animation (animation_url) playing on loop
- Section: **Form** — form_description paragraph
- Section: **Watch out for** — common_mistakes bulleted
- Section: **Breathing** — breathing_cues
- Section: **Equipment / Muscles / Level** — chips

Cinematic setup-workout background drop already uses the start-frame image —
once Phase 5 lands branded icons, the drop visual upgrades automatically.
