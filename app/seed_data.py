"""Initial seed data for exercise_config (weight types) and split_config
(typical exercises per workout type, by date range), based on the sample
Push-day notes provided at kickoff plus reasonable defaults for Pull/Legs/
Day4. These are *starting points*, not ground truth: the review screen
always lets the user override the auto-classified workout_type, and any
newly-seen exercise gets classified once and remembered from then on. Once
more real notes are backfilled (a Pull day, a Legs day, an old Day4 day),
adjust the SPLIT_CONFIG lists below to match reality.
"""
from datetime import date

from sqlalchemy.orm import Session as DBSession

from app.models import (
    Exercise, ExerciseGroup, SplitConfigEntry,
    BARBELL_PLATE_PER_SIDE, DUMBBELL_EACH, TOTAL_WEIGHT, PLATE_LOADED_PER_SIDE,
)

# Exercise name -> weight_type, seeded from the sample Push day + common gym
# vocabulary for Pull/Legs/Day4. Anything not listed here gets surfaced for
# one-time classification during import review.
EXERCISE_CONFIG = {
    # Push (confirmed from sample data)
    "Flat bench press": BARBELL_PLATE_PER_SIDE,
    "Dumbbell Shoulder press": DUMBBELL_EACH,
    "Dumbbell Lat raises": DUMBBELL_EACH,
    "Incline bench press": BARBELL_PLATE_PER_SIDE,
    "Straight bar tri extension": TOTAL_WEIGHT,
    # Pull (best-guess defaults, confirm against real notes)
    "Barbell row": BARBELL_PLATE_PER_SIDE,
    "Lat pulldown": TOTAL_WEIGHT,
    "Seated cable row": TOTAL_WEIGHT,
    "Dumbbell curl": DUMBBELL_EACH,
    "Face pull": TOTAL_WEIGHT,
    "Deadlift": BARBELL_PLATE_PER_SIDE,
    "RDL": BARBELL_PLATE_PER_SIDE,
    "Romanian deadlift": BARBELL_PLATE_PER_SIDE,
    "Underhand rows": BARBELL_PLATE_PER_SIDE,
    # Legs (best-guess defaults)
    "Squat": BARBELL_PLATE_PER_SIDE,
    "Leg press": TOTAL_WEIGHT,
    "Leg curl": TOTAL_WEIGHT,
    "Leg extension": TOTAL_WEIGHT,
    "Calf raise": TOTAL_WEIGHT,
    # Day 4 - Shoulders/Arms (best-guess defaults, dropped in 2026 split)
    "Overhead press": BARBELL_PLATE_PER_SIDE,
    "Dumbbell lateral raise": DUMBBELL_EACH,
    "Bicep curl": DUMBBELL_EACH,
    "Skull crushers": BARBELL_PLATE_PER_SIDE,
    # Alternate names/variations flagged for grouping (see EXERCISE_GROUPS below) —
    # weight types are best guesses, adjust via the Manage Exercises page if wrong.
    "Flat chest press": PLATE_LOADED_PER_SIDE,
    "Barbell bench press": BARBELL_PLATE_PER_SIDE,
    "Flat bench press": BARBELL_PLATE_PER_SIDE,
    "Incline chest press": PLATE_LOADED_PER_SIDE,
    "Shoulder press": TOTAL_WEIGHT,
    "Dumbbell bicep curls": DUMBBELL_EACH,
    "Preacher curl": TOTAL_WEIGHT,
    "Single arm Preacher curl": TOTAL_WEIGHT,
    # Same lift, literal abbreviation of the same name.
    "RDL": BARBELL_PLATE_PER_SIDE,
}

# Groups of exercise names that are really the same movement, so progress
# trends combine them instead of splitting across whatever name was used that
# day. Seeded from the exact pairs given at kickoff, plus "Flat bench press"
# (confirmed as the same lift as "Barbell bench press", just typed
# differently that day) and "RDL"/"Romanian deadlift" (same lift, literal
# abbreviation). Add more via the Manage Exercises page as other historical
# naming variants turn up — a full backfill will surface plenty (e.g. this
# dataset alone has "Rear delt flys" written at least 4 different ways).
EXERCISE_GROUPS = {
    "Chest Press": ["Flat chest press", "Barbell bench press", "Flat bench press"],
    "Incline Chest Press": ["Incline bench press", "Incline chest press"],
    "Shoulder Press": ["Dumbbell Shoulder press", "Shoulder press"],
    "Bicep Curl": [
        "Dumbbell curl", "Dumbbell bicep curls", "Preacher curl", "Single arm Preacher curl",
    ],
    "Romanian Deadlift": ["RDL", "Romanian deadlift"],
}

SPLIT_TRANSITION_DATE = date(2026, 1, 1)

# (workout_type, [exercise names], start_date, end_date)
SPLIT_CONFIG = [
    ("Push", [
        "Flat bench press", "Dumbbell Shoulder press", "Dumbbell Lat raises",
        "Incline bench press", "Straight bar tri extension",
    ], None, None),
    ("Pull", [
        "Barbell row", "Lat pulldown", "Seated cable row", "Dumbbell curl",
        "Face pull", "Deadlift",
    ], None, None),
    # Old (pre-2026) heavier Legs day
    ("Legs", [
        "Squat", "Leg press", "Leg curl", "Leg extension", "Calf raise",
    ], None, SPLIT_TRANSITION_DATE),
    # Modified runner-friendly Legs day starting with the 2026 split change
    ("Legs", [
        "Leg press", "Leg curl", "Leg extension", "Calf raise",
    ], SPLIT_TRANSITION_DATE, None),
    ("Day4", [
        "Overhead press", "Dumbbell lateral raise", "Bicep curl", "Skull crushers",
    ], None, SPLIT_TRANSITION_DATE),
]


def seed_if_empty(db: DBSession) -> None:
    if db.query(Exercise).count() == 0:
        for name, weight_type in EXERCISE_CONFIG.items():
            db.add(Exercise(name=name, weight_type=weight_type))
        db.commit()

    if db.query(SplitConfigEntry).count() == 0:
        for workout_type, exercise_names, start_date, end_date in SPLIT_CONFIG:
            for exercise_name in exercise_names:
                db.add(SplitConfigEntry(
                    workout_type=workout_type,
                    exercise_name=exercise_name,
                    start_date=start_date,
                    end_date=end_date,
                ))
        db.commit()

    if db.query(ExerciseGroup).count() == 0:
        for group_name, exercise_names in EXERCISE_GROUPS.items():
            group = ExerciseGroup(name=group_name)
            db.add(group)
            db.flush()
            db.query(Exercise).filter(Exercise.name.in_(exercise_names)).update(
                {"group_id": group.id}, synchronize_session=False
            )
        db.commit()
