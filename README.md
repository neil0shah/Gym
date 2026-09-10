# Gym Progress Tracker

A personal web app for tracking gym/strength progress. Paste shorthand workout
notes (the same format used in the Notes app), review the parsed result, save
it, then explore strength progress over time via charts.

Cardio (runs/bikes/swims) is explicitly out of scope — that's tracked in
Strava.

## Stack

- **Backend**: FastAPI + SQLAlchemy + SQLite (`data/gym.db`, created
  automatically on first run).
- **Frontend**: Server-rendered Jinja2 templates + vanilla JS + Chart.js
  (vendored locally in `static/js/vendor/`, no CDN dependency — the app works
  fully offline).

## Running locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open:
- `http://127.0.0.1:8000/import` — paste and review workout notes
- `http://127.0.0.1:8000/progress` — charts and PR tracker
- `http://127.0.0.1:8000/exercises` — group renamed/varied movements together

The SQLite database is created automatically at `data/gym.db` on first run,
seeded with the exercise weight-type classifications and split (Push/Pull/
Legs/Day4) definitions in `app/seed_data.py`. If you're upgrading an existing
`data/gym.db` from an earlier version of the app, new columns/tables are
added automatically on startup (`app/database.py:run_migrations`) — no need
to delete the database, existing sessions are preserved.

## Running tests

```bash
pytest
```

Parser tests live in `tests/test_parser.py` and cover: partial-rep parsing
(`6+1` → 6 full reps, partial discarded from calculations but kept in
`raw_rep_string`), weight-type lookup vs. heuristic guessing for new
exercises, date-line confirmation vs. day-by-day estimation for undated
blocks in the same week, and split (workout-type) auto-classification.

## How the shorthand parser works

Input looks like:

```
1/5/2026

Flat bench press: 45: 7,6
Dumbbell Shoulder press: 50: 8,7
Dumbbell Lat raises: 22.5: 9,8
Incline bench press: 35: 7,6
Straight bar tri extension: 65: 9, 6+1

Barbell row: 40: 8,8
...
```

- Blank lines separate **blocks**. A block that is a single line and parses
  as a date becomes a **date line**; it applies to the very next block.
- A block of `Exercise: weight: rep1, rep2, ...` lines is a **workout
  block** (one session).
- The workout block right after a date line gets that date with
  `date_confidence = confirmed`. Every subsequent undated block in the same
  run is assumed to be the following day(s) — `date_confidence = estimated`
  — until the next date line resets things. The review screen always shows
  which is which, and editing a session's date in the UI marks it
  confirmed.
- Reps map 1:1 to working sets in order. `6+1` stores `reps_full=6`,
  `reps_partial=1`, and keeps `raw_rep_string="6+1"` — partials never factor
  into 1RM/volume math.
- **Weight ambiguity** is resolved via a per-exercise `weight_type` learned
  once and remembered forever after (`app.models.Exercise.weight_type`):
  - `barbell_plate_per_side`: total load = `weight * 2 + bar_weight` (bar
    defaults to 45 lb, overridable per exercise if you're on an EZ-curl or
    other bar — this was an open question in the original brief and is left
    as a per-exercise override rather than a single global assumption).
  - `dumbbell_each`: tracked as-is, not doubled.
  - `total_weight`: tracked as-is (machine/cable stack weight).

  An exercise seen for the first time is heuristically guessed (name
  contains "dumbbell" → `dumbbell_each`; common barbell-lift keywords →
  `barbell_plate_per_side`; otherwise `total_weight`) and flagged
  `is_unrecognized` so the review screen highlights it for a one-time
  classification. That classification is written back to the `exercises`
  table on save and reused for every future import.
- **Exercise order** within a session is preserved as `order_index` so
  progress views can filter by "1st exercise of the day" vs. "4th," since
  order affects performance (fresh vs. fatigued).
- **Split/workout-type** (Push/Pull/Legs/Day4) is auto-guessed by matching
  a block's exercises against `split_config` (seeded in
  `app/seed_data.py`, date-ranged to reflect the early-2026 shift from a
  4-day Push/Pull/Legs/Day4 split to a 3-day Push/Pull/modified-Legs split).
  This is only ever a suggestion — the review screen lets you override the
  workout type per session, and nothing about auto-classification is
  treated as ground truth.

## Additional raw-note formats supported

Beyond the basic `name: weight: reps` shape, the parser also handles:

- **Per-set weight changes.** `Underhand rows: 175: 6, 160: 8` is a 175lb
  set of 6 followed by a 160lb set of 8 — any set can override the weight
  for itself and every set after it on the same line, not just the first.
- **"Plate" notation.** `RDL: Plate: 8, 7, 5` treats "Plate" as 45lb (one
  45lb plate per side); `Flat chest press: Plate+15: 8, Plate+10: 8` is
  45+15=60lb then 45+10=55lb. This combines with per-set overrides, so
  `Plate+15: 8, Plate+10: 8` works as shown.
- **Plate-loaded machines with no separate bar** (`plate_loaded_per_side`
  weight type) — total load is `weight * 2`, unlike `barbell_plate_per_side`
  which adds a 45lb bar. Classify a plate-loaded machine as this type
  (rather than barbell) during review or on the Manage Exercises page.
- **Bodyweight movements with no weight field at all**
  (`bodyweight_fixed` weight type) — `Pull ups: 8, 7, 6` has no weight
  number in the notation, so it's tracked at a fixed assumed bodyweight
  (160lb by default, shown in the "Bar/BW wt." column during review and
  editable there, remembered per-exercise from then on).
- **Free-text session notes.** A line that's neither a date nor an exercise
  — whether on its own (like a date line) or glued directly above the
  workout with no blank line — is attached to the session that follows as a
  note, e.g. a `California - No straps:` header before that day's
  exercises. Edit or add a note for any session in the review screen; it's
  saved with the session (`sessions.note`) as context for later, and
  doesn't otherwise affect parsing or calculations.

## Grouping exercises for trend continuity

Different names or machines for the same movement (e.g. "Flat chest press"
vs. "Barbell bench press", or "Dumbbell curl" vs. "Preacher curl") split
progress trends across names by default, since each is stored as its own
`exercises` row. The **Manage Exercises** page (`/exercises`) lists every
exercise name the parser has ever seen and lets you group any of them
together; the Progress page's exercise dropdown then shows each group as one
combined entry, aggregating the weight/1RM trend, reps-at-weight, and volume
charts across every exercise in the group. Grouping only changes how charts
aggregate — it never edits or merges the underlying saved sets. Four groups
are seeded from the exact pairs given at kickoff (Chest Press, Incline Chest
Press, Shoulder Press, Bicep Curl) in `app/seed_data.py`; add more as you
find other historical naming variants during backfill — the Manage Exercises
page is meant to be a preliminary pass you can run before (or during) a big
backfill, not a one-time setup step.

## What's implemented vs. deferred

Implemented (phases 1–3, 5, and part of 6 from the original build plan):
- Data model (`exercises`, `exercise_groups`, `sessions`, `session_exercises`,
  `sets`, `split_config`)
- Shorthand parser with unit tests, including per-set weight overrides,
  "Plate"/"Plate+N" notation, plate-loaded machines, bodyweight movements
  with no weight field, and free-text session notes
- Paste → parse → **editable review table** → save flow, including
  one-time weight-type classification for new exercises, workout-type
  override, and an editable per-session note
- Manage Exercises page to group renamed/varied movements for trend
  continuity
- Progress page: per-exercise weight/est.-1RM trend, reps-at-weight trend,
  volume-over-time (bar per week/month), workout frequency, and a PR
  tracker (best estimated 1RM ever per exercise, via the Epley formula),
  all filterable by exercise-or-group, order-in-session, workout type, and
  date range

Deliberately deferred (flagged as secondary/optional/nice-to-have in the
original brief, to keep the first pass focused):
- A structured manual-entry form as an alternative to pasting shorthand
  (secondary in the brief — paste-and-review covers single-day entry fine
  in the meantime).
- A dedicated UI for editing `split_config` after the fact — it's seeded
  with reasonable defaults and stored in the DB, but today you'd edit
  `app/seed_data.py` and re-seed, or add rows directly, rather than through
  a settings page. Since the review screen always lets you override the
  guessed workout type per session, this doesn't block correctness, just
  editing convenience.

## Open items carried over from the brief

The parser and seed data were built from the one sample Push day provided
at kickoff, plus reasonable defaults for Pull/Legs/Day4 (see
`app/seed_data.py`) and a flexible date-line parser (tries several common
date formats since the exact Notes-app format wasn't available yet). Two
things worth confirming once the real 2–3 years of notes are on hand:

1. **Date line format** — the parser accepts most common formats
   (`1/5/2026`, `2026-01-05`, `Jan 5`, weekday-prefixed, etc.) via
   `dateutil`, but hasn't been validated against your actual Notes export.
   If real dates fail to parse, they'll show up as `warnings` in the parse
   response (surfaced in the import UI) rather than silently misfiring.
2. **Pull/Legs/Day4 exercise lists** in `split_config` are best-guess
   placeholders (only a Push day was available at kickoff) — worth a pass
   once real data shows what those days actually look like. Low risk either
   way since workout-type is always user-overridable per session.
3. **"Flat bench press" (from the original sample) probably belongs in the
   "Chest Press" group** alongside "Barbell bench press" — they read as the
   same lift under two different names, but that wasn't explicitly
   confirmed, so it was left out of the seeded groups. Add it via the
   Manage Exercises page if so.
