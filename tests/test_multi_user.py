from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app import models, schemas, seed_data
from app import main as main_module


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def two_users(db):
    alice = models.User(email="alice@example.com", password_hash="x")
    bob = models.User(email="bob@example.com", password_hash="x")
    db.add_all([alice, bob])
    db.commit()
    db.refresh(alice)
    db.refresh(bob)
    return alice, bob


def _log_one_set(db, user, exercise_name, weight, reps, on_date, weight_type=models.TOTAL_WEIGHT):
    req = schemas.SaveRequest(sessions=[
        schemas.SaveSessionIn(
            date=on_date, date_confidence="confirmed", workout_type="Push",
            exercises=[
                schemas.SaveExerciseIn(
                    name=exercise_name, order_index=1, weight_type=weight_type,
                    sets=[schemas.SaveSetIn(set_number=1, weight_recorded=weight, reps_full=reps, raw_rep_string=str(reps))],
                )
            ],
        )
    ])
    return main_module.api_save(req, db=db, current_user=user)


def test_save_scopes_exercises_and_sessions_to_owner(db, two_users):
    alice, bob = two_users
    _log_one_set(db, alice, "Bench press", 135, 8, date(2024, 1, 1))
    _log_one_set(db, bob, "Bench press", 95, 10, date(2024, 1, 2))

    alice_exercise = db.query(models.Exercise).filter(models.Exercise.user_id == alice.id).one()
    bob_exercise = db.query(models.Exercise).filter(models.Exercise.user_id == bob.id).one()
    assert alice_exercise.id != bob_exercise.id
    assert alice_exercise.name == bob_exercise.name == "Bench press"

    alice_session = db.query(models.Session).filter(models.Session.user_id == alice.id).one()
    bob_session = db.query(models.Session).filter(models.Session.user_id == bob.id).one()
    assert alice_session.id != bob_session.id


def test_same_exercise_name_does_not_collide_across_users(db, two_users):
    # Both users logging "Bench press" must not hit the old global
    # UNIQUE(name) constraint — it's UNIQUE(user_id, name) now.
    alice, bob = two_users
    _log_one_set(db, alice, "Bench press", 135, 8, date(2024, 1, 1))
    _log_one_set(db, bob, "Bench press", 95, 10, date(2024, 1, 2))  # must not raise
    assert db.query(models.Exercise).filter(models.Exercise.name == "Bench press").count() == 2


def test_api_exercises_never_lists_another_users_exercises(db, two_users):
    alice, bob = two_users
    _log_one_set(db, alice, "Squat", 225, 5, date(2024, 1, 1))
    _log_one_set(db, bob, "Deadlift", 315, 3, date(2024, 1, 1))

    alice_list = main_module.api_exercises(has_data=True, db=db, current_user=alice)
    bob_list = main_module.api_exercises(has_data=True, db=db, current_user=bob)

    assert [e.name for e in alice_list] == ["Squat"]
    assert [e.name for e in bob_list] == ["Deadlift"]


def test_cannot_update_another_users_exercise(db, two_users):
    alice, bob = two_users
    _log_one_set(db, alice, "Overhead press", 95, 8, date(2024, 1, 1))
    alice_exercise = db.query(models.Exercise).filter(models.Exercise.user_id == alice.id).one()

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        main_module.api_update_exercise(
            alice_exercise.id,
            schemas.ExerciseUpdate(combined_both_sides=True),
            db=db, current_user=bob,
        )
    assert exc_info.value.status_code == 404

    db.refresh(alice_exercise)
    assert alice_exercise.combined_both_sides is False  # untouched


def test_group_names_dont_collide_across_users_and_stay_scoped(db, two_users):
    alice, bob = two_users
    _log_one_set(db, alice, "Preacher curl", 100, 8, date(2024, 1, 1))
    _log_one_set(db, bob, "Preacher curl", 60, 8, date(2024, 1, 1))
    alice_ex = db.query(models.Exercise).filter(models.Exercise.user_id == alice.id).one()
    bob_ex = db.query(models.Exercise).filter(models.Exercise.user_id == bob.id).one()

    alice_group = main_module.api_create_exercise_group(
        schemas.ExerciseGroupCreate(name="Bicep Curl", exercise_ids=[alice_ex.id, bob_ex.id]),
        db=db, current_user=alice,
    )
    # Bob's exercise id must NOT have been swept into Alice's group.
    alice_ex_after = db.query(models.Exercise).filter(models.Exercise.id == alice_ex.id).one()
    bob_ex_after = db.query(models.Exercise).filter(models.Exercise.id == bob_ex.id).one()
    assert alice_ex_after.group_id == alice_group.id
    assert bob_ex_after.group_id is None

    # Bob can use the identical group name — no cross-user unique collision.
    bob_group = main_module.api_create_exercise_group(
        schemas.ExerciseGroupCreate(name="Bicep Curl", exercise_ids=[bob_ex.id]),
        db=db, current_user=bob,
    )
    assert bob_group.id != alice_group.id

    alice_groups = main_module.api_list_exercise_groups(db=db, current_user=alice)
    bob_groups = main_module.api_list_exercise_groups(db=db, current_user=bob)
    assert [g.id for g in alice_groups] == [alice_group.id]
    assert [g.id for g in bob_groups] == [bob_group.id]


def test_progress_exercise_ignores_foreign_exercise_id(db, two_users):
    alice, bob = two_users
    _log_one_set(db, alice, "Leg press", 400, 10, date(2024, 1, 1))
    _log_one_set(db, bob, "Leg press", 300, 10, date(2024, 1, 1))
    bob_ex = db.query(models.Exercise).filter(models.Exercise.user_id == bob.id).one()

    # Alice querying Bob's exercise_id must get nothing back, never Bob's data.
    points = main_module.api_progress_exercise(
        bob_ex.id, start_date=None, end_date=None, order_index=None, workout_type=None,
        db=db, current_user=alice,
    )
    assert points == []


def test_progress_group_ignores_foreign_group_id(db, two_users):
    alice, bob = two_users
    _log_one_set(db, alice, "Lat pulldown", 130, 10, date(2024, 1, 1))
    _log_one_set(db, bob, "Lat pulldown", 100, 10, date(2024, 1, 1))
    bob_ex = db.query(models.Exercise).filter(models.Exercise.user_id == bob.id).one()
    bob_group = main_module.api_create_exercise_group(
        schemas.ExerciseGroupCreate(name="Back", exercise_ids=[bob_ex.id]),
        db=db, current_user=bob,
    )

    points = main_module.api_progress_group(
        bob_group.id, start_date=None, end_date=None, order_index=None, workout_type=None,
        db=db, current_user=alice,
    )
    assert points == []


def test_progress_frequency_counts_only_own_sessions(db, two_users):
    alice, bob = two_users
    _log_one_set(db, alice, "Squat", 225, 5, date(2024, 1, 1))
    _log_one_set(db, alice, "Squat", 230, 5, date(2024, 1, 8))
    _log_one_set(db, bob, "Squat", 185, 5, date(2024, 1, 1))

    alice_freq = main_module.api_progress_frequency(
        workout_type=None, start_date=None, end_date=None, period="week", db=db, current_user=alice,
    )
    bob_freq = main_module.api_progress_frequency(
        workout_type=None, start_date=None, end_date=None, period="week", db=db, current_user=bob,
    )
    assert sum(p.session_count for p in alice_freq) == 2
    assert sum(p.session_count for p in bob_freq) == 1


def test_seed_if_empty_seeds_each_user_independently(db, two_users):
    alice, bob = two_users
    seed_data.seed_if_empty(db, user_id=alice.id)

    alice_count = db.query(models.Exercise).filter(models.Exercise.user_id == alice.id).count()
    bob_count = db.query(models.Exercise).filter(models.Exercise.user_id == bob.id).count()
    assert alice_count > 0
    assert bob_count == 0  # seeding Alice must not touch Bob

    seed_data.seed_if_empty(db, user_id=bob.id)
    bob_count_after = db.query(models.Exercise).filter(models.Exercise.user_id == bob.id).count()
    assert bob_count_after == alice_count


def test_update_exercise_can_set_and_clear_group(db, two_users):
    alice, bob = two_users
    _log_one_set(db, alice, "Preacher curl", 100, 8, date(2024, 1, 1))
    ex = db.query(models.Exercise).filter(models.Exercise.user_id == alice.id).one()
    group = main_module.api_create_exercise_group(
        schemas.ExerciseGroupCreate(name="Bicep Curl", exercise_ids=[]), db=db, current_user=alice,
    )

    updated = main_module.api_update_exercise(
        ex.id, schemas.ExerciseUpdate(group_id=group.id), db=db, current_user=alice,
    )
    assert updated.group_id == group.id

    cleared = main_module.api_update_exercise(
        ex.id, schemas.ExerciseUpdate(group_id=None), db=db, current_user=alice,
    )
    assert cleared.group_id is None


def test_update_exercise_omitting_group_id_leaves_it_untouched(db, two_users):
    alice, bob = two_users
    _log_one_set(db, alice, "Preacher curl", 100, 8, date(2024, 1, 1))
    ex = db.query(models.Exercise).filter(models.Exercise.user_id == alice.id).one()
    group = main_module.api_create_exercise_group(
        schemas.ExerciseGroupCreate(name="Bicep Curl", exercise_ids=[ex.id]), db=db, current_user=alice,
    )

    # Only touching combined_both_sides — group_id key is absent from the
    # request entirely, so it must not be cleared as a side effect.
    updated = main_module.api_update_exercise(
        ex.id, schemas.ExerciseUpdate(combined_both_sides=True), db=db, current_user=alice,
    )
    assert updated.group_id == group.id


def test_update_exercise_cannot_assign_another_users_group(db, two_users):
    alice, bob = two_users
    _log_one_set(db, alice, "Preacher curl", 100, 8, date(2024, 1, 1))
    ex = db.query(models.Exercise).filter(models.Exercise.user_id == alice.id).one()
    bob_group = main_module.api_create_exercise_group(
        schemas.ExerciseGroupCreate(name="Bob's Group", exercise_ids=[]), db=db, current_user=bob,
    )

    with pytest.raises(HTTPException) as exc_info:
        main_module.api_update_exercise(
            ex.id, schemas.ExerciseUpdate(group_id=bob_group.id), db=db, current_user=alice,
        )
    assert exc_info.value.status_code == 404
    db.refresh(ex)
    assert ex.group_id is None


def test_exercise_history_orders_newest_first_with_raw_sets(db, two_users):
    alice, bob = two_users
    _log_one_set(db, alice, "Bench press", 135, 8, date(2024, 1, 1))
    _log_one_set(db, alice, "Bench press", 140, 6, date(2024, 1, 8))
    ex = db.query(models.Exercise).filter(models.Exercise.user_id == alice.id).one()

    history = main_module.api_exercise_history(ex.id, db=db, current_user=alice)
    assert [h.date for h in history] == [date(2024, 1, 8), date(2024, 1, 1)]
    assert history[0].sets[0].weight_recorded == 140
    assert history[1].sets[0].weight_recorded == 135


def test_exercise_history_scoped_to_owner(db, two_users):
    alice, bob = two_users
    _log_one_set(db, alice, "Squat", 225, 5, date(2024, 1, 1))
    _log_one_set(db, bob, "Squat", 185, 5, date(2024, 1, 1))
    bob_ex = db.query(models.Exercise).filter(models.Exercise.user_id == bob.id).one()

    with pytest.raises(HTTPException) as exc_info:
        main_module.api_exercise_history(bob_ex.id, db=db, current_user=alice)
    assert exc_info.value.status_code == 404
