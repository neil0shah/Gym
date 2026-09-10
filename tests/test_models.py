from app.models import (
    Exercise, BARBELL_PLATE_PER_SIDE, DUMBBELL_EACH, TOTAL_WEIGHT,
    PLATE_LOADED_PER_SIDE, BODYWEIGHT_FIXED,
)


def test_barbell_adds_default_45lb_bar():
    ex = Exercise(name="Flat bench press", weight_type=BARBELL_PLATE_PER_SIDE)
    assert ex.total_weight_for(45.0) == 135.0  # 45*2 + 45


def test_barbell_uses_custom_bar_weight():
    ex = Exercise(name="EZ curl", weight_type=BARBELL_PLATE_PER_SIDE, bar_weight=20.0)
    assert ex.total_weight_for(30.0) == 80.0  # 30*2 + 20


def test_plate_loaded_machine_has_no_bar():
    ex = Exercise(name="Flat chest press", weight_type=PLATE_LOADED_PER_SIDE)
    assert ex.total_weight_for(60.0) == 120.0  # 60*2, no bar added


def test_dumbbell_tracked_per_hand_not_doubled():
    ex = Exercise(name="Dumbbell curl", weight_type=DUMBBELL_EACH)
    assert ex.total_weight_for(30.0) == 30.0


def test_total_weight_tracked_as_is():
    ex = Exercise(name="Lat pulldown", weight_type=TOTAL_WEIGHT)
    assert ex.total_weight_for(130.0) == 130.0


def test_bodyweight_fixed_tracked_as_is():
    ex = Exercise(name="Pull ups", weight_type=BODYWEIGHT_FIXED, bar_weight=160.0)
    assert ex.total_weight_for(160.0) == 160.0
