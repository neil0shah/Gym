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

The SQLite database is created automatically at `data/gym.db` on first run,
seeded with the exercise weight-type classifications and split (Push/Pull/
Legs/Day4) definitions in `app/seed_data.py`.

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

## What's implemented vs. deferred

Implemented (phases 1–3, 5, and part of 6 from the original build plan):
- Data model (`exercises`, `sessions`, `session_exercises`, `sets`,
  `split_config`)
- Shorthand parser with unit tests
- Paste → parse → **editable review table** → save flow, including
  one-time weight-type classification for new exercises and workout-type
  override
- Progress page: per-exercise weight/est.-1RM trend, reps-at-weight trend,
  volume-over-time (bar per week/month), workout frequency, and a PR
  tracker (best estimated 1RM ever per exercise, via the Epley formula),
  all filterable by exercise, order-in-session, workout type, and date
  range

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
