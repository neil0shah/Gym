from datetime import date

from app.models import BARBELL_PLATE_PER_SIDE, DUMBBELL_EACH, TOTAL_WEIGHT, CONFIRMED, ESTIMATED
from app.parser import parse_notes

SAMPLE_PUSH_DAY = """\
Flat bench press: 45: 7,6
Dumbbell Shoulder press: 50: 8,7
Dumbbell Lat raises: 22.5: 9,8
Incline bench press: 35: 7,6
Straight bar tri extension: 65: 9, 6+1
"""

KNOWN_EXERCISES = {
    "flat bench press": ("Flat bench press", BARBELL_PLATE_PER_SIDE),
    "dumbbell shoulder press": ("Dumbbell Shoulder press", DUMBBELL_EACH),
    "dumbbell lat raises": ("Dumbbell Lat raises", DUMBBELL_EACH),
    "incline bench press": ("Incline bench press", BARBELL_PLATE_PER_SIDE),
    "straight bar tri extension": ("Straight bar tri extension", TOTAL_WEIGHT),
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


def test_unparseable_line_produces_warning_not_crash():
    raw = "Not a real line or date\n\n" + SAMPLE_PUSH_DAY
    result = parse_notes(raw, KNOWN_EXERCISES)
    assert len(result.sessions) == 1
    assert any("Not a real line" in w for w in result.warnings)


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
