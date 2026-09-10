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

Validated against ~2.5 years of real backfill notes (365 parsed sessions),
which turned up a few more shapes now also supported:

- **Dash-separated per-set weight** (an older notation): `Upper back row:
  100 - 7+2, 85 - 10+1` works the same as the colon form.
- **No colon between name and weight** — just a per-exercise typing habit:
  `Shoulder press 55 : 6+2, 50: 8+1` and `Shoulder press: 55: 6+2, 50: 8+1`
  parse identically. The split point is wherever a weight-shaped token
  starts, not a fixed colon position — so a line like `Day 4:` or `2/18:
  First time taking creatine` (name-shaped text that never resolves to real
  set data) correctly falls through to date/note handling instead of
  becoming a broken, empty exercise.
- **More plate-math shapes**: `2 plates` (=90, two 45lb plates), `2
  plates+10` (=100), `Plate+2 10s` (=65, a 45lb plate plus two 10lb
  plates), and bare sums with no "Plate" keyword at all like `35+25` (=60).
- **Asymmetric single-side reps**: `Dumbbell curl: 25: L7R6, 20: 11` (left
  did 7, right did 6) is recorded as 6 — the lower, limiting side.
- **Two exercises glued onto one line** (a fast-typed superset, with or
  without a comma between them): `Hammer: 60: 10 Bicep: 60: 8` splits into
  two exercises, each with its own set.
- **A stray parenthetical between name and weight or after a rep count** —
  `Upper back row: (no straps) 115: 7, 100: 7` and `Lat pulldown: 100 - 8,
  85: 12 (1 partial)` — is stripped rather than breaking the parse.
- Free-text annotations with a bare number but no real date signal (`2 WEEK
  BREAK`, `1 WEEK SICKNESS`) are never misread as a date — fuzzy date
  parsing only kicks in when there's an actual `/`- or `-`-separated digit
  pair or a month name, so these become session notes instead of silently
  corrupting the date chain (dateutil's fuzzy mode will otherwise happily
  read "2 WEEK BREAK" as day 2 of some month).

A handful of genuinely ambiguous or free-text lines still just get skipped
with a warning rather than guessed at — a line with two exercises' worth of
data crammed together with no separator at all, a missing comma, a running
time, a rest-day tally with no numbers ("2 sets of calf raises"). These show
up in the review screen's warnings so you can see exactly what got dropped
and fix it by hand if it matters.

## Using it from your phone (deploying + login)

Running on your laptop only, this app is reachable at `127.0.0.1:8000` —
your own machine only, nothing else on the network can reach it, phone
included. To log workouts from your phone day-to-day, two things have to
change: the app needs to run somewhere both devices can reach, and since
that makes it reachable from the internet generally, it needs a login.

**Login is already built and ready** (`app/auth.py`), but it's off by
default so local laptop-only use needs zero setup — it only turns on once
you set two environment variables:

```bash
AUTH_EMAIL=you@example.com
AUTH_PASSWORD_HASH=<bcrypt hash, see below>
SESSION_SECRET_KEY=<a long random string, keep it secret>
```

Generate the password hash once, locally:

```bash
python3 -c "import bcrypt; print(bcrypt.hashpw(b'your-password-here', bcrypt.gensalt()).decode())"
```

Paste the printed hash as `AUTH_PASSWORD_HASH` — never the raw password
itself. `SESSION_SECRET_KEY` can be anything long and random (e.g. `python3
-c "import secrets; print(secrets.token_hex(32))"`); it just needs to stay
the same across restarts, or you'll get logged out every time the server
restarts. With all three set, every page and API endpoint requires logging
in first, and the session cookie keeps you signed in for 90 days.

This is intentionally a single hardcoded account (one `AUTH_EMAIL` you set
yourself), not a signup system — there's only one person using this app.

### Where to actually run it

You need a host that keeps a Python process running continuously with a
persistent disk (for `data/gym.db`) — not a purely static host. Two
reasonable paths:

1. **A small managed host** (recommended) — Railway, Fly.io, or Render all
   support this directly: point them at this repo, they build and run
   `uvicorn app.main:app`, and you attach a small persistent volume mounted
   at `data/`. Set the three env vars above in the host's dashboard. All
   three have a free or near-free tier for something this small. Railway's
   deploy flow is the simplest (connect the GitHub repo, add a volume, set
   env vars, done) if you want a specific recommendation to start with.
2. **Self-hosted + Tailscale** — run the app on a machine that stays on
   (a home server, a Raspberry Pi, or a laptop you don't fully shut down),
   install [Tailscale](https://tailscale.com) on it and on your phone, and
   reach the app via its private Tailscale address. Never touches the
   public internet, so you could skip the login env vars entirely and rely
   on Tailscale's device list as the access control instead. No hosting
   cost, but the host machine has to actually be running and reachable
   whenever you want to log a set.

Whichever you pick, make sure the app is served over **HTTPS** — most
managed hosts do this automatically at their edge. The session cookie
defaults to HTTPS-only whenever login is enabled (`SESSION_HTTPS_ONLY`,
default `true` when `AUTH_EMAIL` is set); only set it to `false` if you're
deliberately testing over plain HTTP.

### Bringing your data with you

`data/gym.db` is just a SQLite file. To move your existing local import to
a new deployment: copy that file into the deployed environment's
persistent volume at the same path before the app's first request there
(or stop the deployed app, replace its `data/gym.db`, restart it). There's
no export/import tool for this today — it's a file copy.

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
backfill, not a one-time setup step. Group names are editable inline right
in that table (click and type), and the exercise list only shows exercises
you've actually logged data for — nothing from the seed defaults you haven't
used — with ungrouped ones sorted first and highlighted amber, since those
are the ones worth triaging.

### Normalizing bilateral vs. unilateral variants within a group

Some machines can be worked with both limbs sharing one stack (e.g. a
preacher curl bar pulled with both arms) or with one limb alone moving the
whole stack (e.g. "Single arm Preacher curl"). Grouping both under one name
without adjustment would make the trend look like it cratered the day you
switched from "both arms, 100lb combined" to "one arm, 50lb" — even though
50lb per arm is *more* than the ~50lb per arm the 100lb combined lift
actually represented.

Check **Combined?** for an exercise on the Manage Exercises page (or in the
import review screen) when its recorded weight is a combined load shared
across both sides rather than already being per-side — `Exercise.
combined_both_sides` then halves it in `total_weight_for()` for every
chart, so it's comparable to a unilateral variant grouped alongside it.
This has no effect on `dumbbell_each` exercises, which are already
per-hand regardless of how the set was done. It's off by default for every
exercise except the one confirmed case from testing (`Preacher curl`).

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

The parser and seed data were originally built from one sample Push day,
then validated against the full ~2.5-year backfill (365 sessions parsed,
28 lines skipped with warnings — all genuinely ambiguous or out-of-scope
source text, see above). What's resolved and what's still worth a look:

- **Date line format** — confirmed against real data: every real date line
  in the full history uses `M/D`, `M/D/YY`, or `M/D/YYYY` (optionally with
  a trailing colon). The parser also tolerates month names and
  weekday-prefixes via `dateutil` for future flexibility, but only ever
  attempts that fuzzier matching when there's a real date-like signal
  (a `/`-or-`-`-separated digit pair, or a month name) — see "Additional
  raw-note formats supported" above for why that guard matters.
- **Pull/Legs/Day4 exercise lists** in `split_config` are still the
  original best-guess placeholders from kickoff (the backfill data is Push
  and Pull only) — low risk since workout-type is always user-overridable
  per session, but worth a pass once Legs/Day4 notes get backfilled.
- **"Flat bench press" and "RDL"/"Romanian deadlift"** are now grouped
  (confirmed as the same lifts under different names). The backfill
  surfaced many more naming clusters worth a look on the Manage Exercises
  page before or during a full import — e.g. "Rear delt flys" alone is
  written at least 4 different ways, and there's a large tricep-extension
  and lat-raise family of near-duplicate names. Grouping only affects how
  charts aggregate, so it's safe to do incrementally rather than all at
  once.
- **Assisted pull-ups** (`Assisted pull up: 45 - 5, 55 - 8`) record an
  *assistance* weight, where a higher number means an *easier* rep, not a
  harder one — the opposite of every other weight_type. The app has no
  "inverse" weight type, so these currently get tracked as a plain number
  like any other; a 1RM/trend chart on them will read backwards (higher
  assistance showing as "progress"). Low priority given how few sessions
  use assisted reps, but worth knowing before trusting that particular
  chart.
