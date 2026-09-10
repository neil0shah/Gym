from datetime import date

from app.models import (
    BARBELL_PLATE_PER_SIDE, DUMBBELL_EACH, TOTAL_WEIGHT,
    PLATE_LOADED_PER_SIDE, BODYWEIGHT_FIXED, CONFIRMED, ESTIMATED,
)
from app.parser import parse_notes

SAMPLE_PUSH_DAY = """\
Flat bench press: 45: 7,6
Dumbbell Shoulder press: 50: 8,7
Dumbbell Lat raises: 22.5: 9,8
Incline bench press: 35: 7,6
Straight bar tri extension: 65: 9, 6+1
"""

# lowercased name -> (canonical name, weight_type, bar_weight)
KNOWN_EXERCISES = {
    "flat bench press": ("Flat bench press", BARBELL_PLATE_PER_SIDE, None),
    "dumbbell shoulder press": ("Dumbbell Shoulder press", DUMBBELL_EACH, None),
    "dumbbell lat raises": ("Dumbbell Lat raises", DUMBBELL_EACH, None),
    "incline bench press": ("Incline bench press", BARBELL_PLATE_PER_SIDE, None),
    "straight bar tri extension": ("Straight bar tri extension", TOTAL_WEIGHT, None),
    "underhand rows": ("Underhand rows", BARBELL_PLATE_PER_SIDE, None),
    "rdl": ("RDL", BARBELL_PLATE_PER_SIDE, None),
    "flat chest press": ("Flat chest press", PLATE_LOADED_PER_SIDE, None),
    "pull ups": ("Pull ups", BODYWEIGHT_FIXED, 160.0),
}

PUSH_SPLIT_CONFIG = [
    ("Push", {
        "flat bench press", "dumbbell shoulder press", "dumbbell lat raises",
        "incline bench press", "straight bar tri extension",
    }, None, None),
    ("Pull", {"barbell row", "lat pulldown", "dumbbell curl"}, None, None),
]


def test_single_push_day_no_date_line():
    result = parse_notes(SAMPLE_PUSH_DAY, KNOWN_EXERCISES)
    assert len(result.sessions) == 1
    session = result.sessions[0]
    assert session.date is None
    assert session.date_confidence == ESTIMATED
    assert session.note is None
    assert len(session.exercises) == 5
    assert [e.name for e in session.exercises] == [
        "Flat bench press", "Dumbbell Shoulder press", "Dumbbell Lat raises",
        "Incline bench press", "Straight bar tri extension",
    ]
    assert [e.order_index for e in session.exercises] == [0, 1, 2, 3, 4]
    assert result.unrecognized_exercise_names == []


def test_weight_types_looked_up_from_known_exercises():
    result = parse_notes(SAMPLE_PUSH_DAY, KNOWN_EXERCISES)
    exercises = {e.name: e for e in result.sessions[0].exercises}
    assert exercises["Flat bench press"].weight_type_guess == BARBELL_PLATE_PER_SIDE
    assert exercises["Dumbbell Shoulder press"].weight_type_guess == DUMBBELL_EACH
    assert exercises["Straight bar tri extension"].weight_type_guess == TOTAL_WEIGHT


def test_sets_parsed_in_order_with_correct_weight_and_reps():
    result = parse_notes(SAMPLE_PUSH_DAY, KNOWN_EXERCISES)
    bench = next(e for e in result.sessions[0].exercises if e.name == "Flat bench press")
    assert len(bench.sets) == 2
    assert bench.sets[0].set_number == 1
    assert bench.sets[0].weight_recorded == 45.0
    assert bench.sets[0].reps_full == 7
    assert bench.sets[0].reps_partial is None
    assert bench.sets[1].reps_full == 6


def test_partial_reps_store_full_and_partial_separately():
    result = parse_notes(SAMPLE_PUSH_DAY, KNOWN_EXERCISES)
    tri = next(e for e in result.sessions[0].exercises if e.name == "Straight bar tri extension")
    assert len(tri.sets) == 2
    first, second = tri.sets
    assert first.reps_full == 9
    assert first.reps_partial is None
    assert first.raw_rep_string == "9"
    assert second.reps_full == 6
    assert second.reps_partial == 1
    assert second.raw_rep_string == "6+1"


def test_unrecognized_exercise_is_flagged_and_heuristically_guessed():
    result = parse_notes(SAMPLE_PUSH_DAY, known_exercises={})
    exercises = {e.name: e for e in result.sessions[0].exercises}
    assert all(e.is_unrecognized for e in exercises.values())
    assert set(result.unrecognized_exercise_names) == set(exercises.keys())
    # heuristic: "Dumbbell ..." -> dumbbell_each, "... bench press" -> barbell_plate_per_side
    assert exercises["Dumbbell Shoulder press"].weight_type_guess == DUMBBELL_EACH
    assert exercises["Flat bench press"].weight_type_guess == BARBELL_PLATE_PER_SIDE
    # no keyword match -> falls back to total_weight
    assert exercises["Straight bar tri extension"].weight_type_guess == TOTAL_WEIGHT


def test_split_classification_guesses_push():
    result = parse_notes(SAMPLE_PUSH_DAY, KNOWN_EXERCISES, split_config=PUSH_SPLIT_CONFIG)
    assert result.sessions[0].workout_type_guess == "Push"


def test_date_line_confirms_first_block_and_estimates_following_blocks():
    raw = (
        "1/5/2026\n"
        "\n"
        + SAMPLE_PUSH_DAY + "\n"
        + SAMPLE_PUSH_DAY + "\n"
        + SAMPLE_PUSH_DAY
    )
    result = parse_notes(raw, KNOWN_EXERCISES)
    assert len(result.sessions) == 3
    s1, s2, s3 = result.sessions
    assert s1.date == date(2026, 1, 5)
    assert s1.date_confidence == CONFIRMED
    assert s2.date == date(2026, 1, 6)
    assert s2.date_confidence == ESTIMATED
    assert s3.date == date(2026, 1, 7)
    assert s3.date_confidence == ESTIMATED


def test_new_date_line_resets_confirmation():
    raw = (
        "1/5/2026\n\n" + SAMPLE_PUSH_DAY + "\n"
        "1/8/2026\n\n" + SAMPLE_PUSH_DAY
    )
    result = parse_notes(raw, KNOWN_EXERCISES)
    assert len(result.sessions) == 2
    assert result.sessions[0].date == date(2026, 1, 5)
    assert result.sessions[0].date_confidence == CONFIRMED
    assert result.sessions[1].date == date(2026, 1, 8)
    assert result.sessions[1].date_confidence == CONFIRMED


def test_unparseable_header_line_becomes_session_note():
    raw = "Not a real line or date\n\n" + SAMPLE_PUSH_DAY
    result = parse_notes(raw, KNOWN_EXERCISES)
    assert len(result.sessions) == 1
    assert result.sessions[0].note == "Not a real line or date"
    assert result.warnings == []


def test_multiple_weeks_full_backfill_shape():
    raw = (
        "1/5/2026\n\n" + SAMPLE_PUSH_DAY + "\n"
        + SAMPLE_PUSH_DAY + "\n\n"
        "1/12/2026\n\n" + SAMPLE_PUSH_DAY
    )
    result = parse_notes(raw, KNOWN_EXERCISES)
    assert len(result.sessions) == 3
    assert [s.date for s in result.sessions] == [
        date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 12),
    ]
    assert [s.date_confidence for s in result.sessions] == [CONFIRMED, ESTIMATED, CONFIRMED]


# --- New raw-format edge cases -------------------------------------------

def test_per_set_weight_override():
    raw = "Underhand rows: 175: 6, 160: 8"
    result = parse_notes(raw, KNOWN_EXERCISES)
    ex = result.sessions[0].exercises[0]
    assert ex.name == "Underhand rows"
    assert len(ex.sets) == 2
    assert ex.sets[0].weight_recorded == 175.0
    assert ex.sets[0].reps_full == 6
    assert ex.sets[1].weight_recorded == 160.0
    assert ex.sets[1].reps_full == 8


def test_plate_weight_token_alone():
    raw = "RDL: Plate: 8, 7, 5"
    result = parse_notes(raw, KNOWN_EXERCISES)
    ex = result.sessions[0].exercises[0]
    assert ex.name == "RDL"
    assert [s.weight_recorded for s in ex.sets] == [45.0, 45.0, 45.0]
    assert [s.reps_full for s in ex.sets] == [8, 7, 5]


def test_plate_plus_n_weight_token_with_per_set_override():
    raw = "Flat chest press: Plate+15: 8, Plate+10: 8"
    result = parse_notes(raw, KNOWN_EXERCISES)
    ex = result.sessions[0].exercises[0]
    assert ex.name == "Flat chest press"
    assert ex.weight_type_guess == PLATE_LOADED_PER_SIDE
    assert len(ex.sets) == 2
    assert ex.sets[0].weight_recorded == 60.0  # 45 + 15
    assert ex.sets[0].reps_full == 8
    assert ex.sets[1].weight_recorded == 55.0  # 45 + 10
    assert ex.sets[1].reps_full == 8


def test_plate_token_unrecognized_exercise_defaults_to_total_weight():
    # An exercise not seen before still parses "Plate" correctly even though
    # the heuristic weight-type guess (no barbell/dumbbell keyword match)
    # falls back to total_weight, pending the user's classification.
    raw = "Cable row machine: Plate+20: 10"
    result = parse_notes(raw, known_exercises={})
    ex = result.sessions[0].exercises[0]
    assert ex.is_unrecognized is True
    assert ex.weight_type_guess == TOTAL_WEIGHT
    assert ex.sets[0].weight_recorded == 65.0  # 45 + 20


def test_bodyweight_exercise_with_no_weight_field():
    raw = "Pull ups: 8, 7, 6"
    result = parse_notes(raw, KNOWN_EXERCISES)
    ex = result.sessions[0].exercises[0]
    assert ex.name == "Pull ups"
    assert ex.weight_type_guess == BODYWEIGHT_FIXED
    assert ex.is_unrecognized is False
    assert [s.weight_recorded for s in ex.sets] == [160.0, 160.0, 160.0]
    assert [s.reps_full for s in ex.sets] == [8, 7, 6]


def test_bodyweight_exercise_unrecognized_defaults_to_160():
    raw = "Chin ups: 5, 4"
    result = parse_notes(raw, known_exercises={})
    ex = result.sessions[0].exercises[0]
    assert ex.is_unrecognized is True
    assert ex.weight_type_guess == BODYWEIGHT_FIXED
    assert ex.bar_weight_guess == 160.0
    assert [s.weight_recorded for s in ex.sets] == [160.0, 160.0]


def test_note_header_glued_directly_above_workout_with_no_blank_line():
    raw = (
        "California - No straps:\n"
        "Easy machine Upper back row: 145: 9, 7\n"
        "Easy machine Underhand rows: 175: 6, 160: 8\n"
        "Lat pulldown: 130: 10, 7\n"
        "Rear delt flys: 120: 10, 9\n"
        "Single arm Preacher curl: 55: 7, 50: 8\n"
    )
    result = parse_notes(raw, known_exercises={})
    assert len(result.sessions) == 1
    session = result.sessions[0]
    assert session.note == "California - No straps"
    assert len(session.exercises) == 5
    assert session.exercises[0].name == "Easy machine Upper back row"
    assert session.exercises[1].sets[1].weight_recorded == 160.0


def test_note_attaches_only_to_the_next_session():
    raw = (
        "California - No straps:\n"
        + SAMPLE_PUSH_DAY + "\n"
        + SAMPLE_PUSH_DAY
    )
    result = parse_notes(raw, KNOWN_EXERCISES)
    assert len(result.sessions) == 2
    assert result.sessions[0].note == "California - No straps"
    assert result.sessions[1].note is None
