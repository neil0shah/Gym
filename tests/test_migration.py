"""Verifies the pre-multi-user -> multi-user migration in app/database.py
never loses or orphans data from an existing (already-deployed) database.
This is the highest-stakes migration in the app: it runs automatically
against real, already-deployed histories.
"""
import sqlite3

import pytest
from sqlalchemy import create_engine, text

from app import database as database_module

_LEGACY_SCHEMA = """
CREATE TABLE exercise_groups (
    id INTEGER PRIMARY KEY,
    name VARCHAR NOT NULL UNIQUE
);
CREATE TABLE exercises (
    id INTEGER PRIMARY KEY,
    name VARCHAR NOT NULL UNIQUE,
    weight_type VARCHAR NOT NULL,
    bar_weight FLOAT,
    category VARCHAR,
    group_id INTEGER,
    combined_both_sides BOOLEAN NOT NULL DEFAULT 0
);
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY,
    date DATE NOT NULL,
    date_confidence VARCHAR NOT NULL DEFAULT 'estimated',
    workout_type VARCHAR,
    raw_note_text TEXT,
    note TEXT,
    created_at DATETIME
);
CREATE TABLE session_exercises (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL,
    exercise_id INTEGER NOT NULL,
    order_index INTEGER NOT NULL
);
CREATE TABLE sets (
    id INTEGER PRIMARY KEY,
    session_exercise_id INTEGER NOT NULL,
    set_number INTEGER NOT NULL,
    weight_recorded FLOAT NOT NULL,
    reps_full INTEGER NOT NULL,
    reps_partial INTEGER,
    raw_rep_string VARCHAR
);
CREATE TABLE split_config (
    id INTEGER PRIMARY KEY,
    workout_type VARCHAR NOT NULL,
    exercise_name VARCHAR NOT NULL,
    start_date DATE,
    end_date DATE
);
"""


@pytest.fixture
def old_schema_db(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_LEGACY_SCHEMA)

    group_id = conn.execute("INSERT INTO exercise_groups (name) VALUES ('Bicep Curl')").lastrowid
    preacher_id = conn.execute(
        "INSERT INTO exercises (name, weight_type, group_id, combined_both_sides) VALUES (?, ?, ?, 1)",
        ("Preacher curl", "total_weight", group_id),
    ).lastrowid
    bench_id = conn.execute(
        "INSERT INTO exercises (name, weight_type) VALUES (?, ?)",
        ("Bench press", "barbell_plate_per_side"),
    ).lastrowid
    session_id = conn.execute(
        "INSERT INTO sessions (date, date_confidence, workout_type) VALUES ('2024-01-01', 'confirmed', 'Push')"
    ).lastrowid
    se_id = conn.execute(
        "INSERT INTO session_exercises (session_id, exercise_id, order_index) VALUES (?, ?, 1)",
        (session_id, bench_id),
    ).lastrowid
    conn.execute(
        "INSERT INTO sets (session_exercise_id, set_number, weight_recorded, reps_full) VALUES (?, 1, 135, 8)",
        (se_id,),
    )
    conn.commit()
    conn.close()

    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(database_module, "engine", test_engine)
    # Mirrors app/main.py's real startup order: create_all() runs first and
    # only ever adds brand-new tables (here, just `users`) — it never
    # touches the pre-existing legacy tables above.
    database_module.Base.metadata.create_all(bind=test_engine)
    return {"group_id": group_id, "preacher_curl_id": preacher_id, "bench_press_id": bench_id, "session_id": session_id}


def test_migration_backfills_existing_data_to_bootstrap_owner(old_schema_db, monkeypatch):
    monkeypatch.setenv("AUTH_EMAIL", "owner@example.com")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", "some-bcrypt-hash")

    database_module.run_migrations()

    with database_module.engine.connect() as conn:
        owner = conn.execute(
            text("SELECT id, password_hash FROM users WHERE email = 'owner@example.com'")
        ).first()
        assert owner is not None
        assert owner[1] == "some-bcrypt-hash"
        owner_id = owner[0]

        exercises = conn.execute(
            text("SELECT id, name, user_id, group_id, combined_both_sides FROM exercises ORDER BY id")
        ).all()
        assert len(exercises) == 2
        assert all(row[2] == owner_id for row in exercises)

        preacher = next(r for r in exercises if r[0] == old_schema_db["preacher_curl_id"])
        assert preacher[1] == "Preacher curl"
        assert preacher[3] == old_schema_db["group_id"]
        assert preacher[4] == 1

        groups = conn.execute(text("SELECT id, name, user_id FROM exercise_groups")).all()
        assert len(groups) == 1
        assert groups[0][2] == owner_id

        sessions = conn.execute(text("SELECT id, user_id FROM sessions")).all()
        assert len(sessions) == 1
        assert sessions[0][1] == owner_id

        # Nothing lost in the exercises/exercise_groups table rebuild.
        sets = conn.execute(text("SELECT weight_recorded, reps_full FROM sets")).all()
        assert sets == [(135.0, 8)]


def test_migration_allows_second_user_with_colliding_exercise_name(old_schema_db, monkeypatch):
    monkeypatch.setenv("AUTH_EMAIL", "owner@example.com")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", "some-bcrypt-hash")
    database_module.run_migrations()

    with database_module.engine.begin() as conn:
        friend_id = conn.execute(
            text("INSERT INTO users (email, password_hash) VALUES ('friend@example.com', 'x')")
        ).lastrowid
        # Must not raise: this name already exists for the owner, but the
        # constraint is UNIQUE(user_id, name) now, not a global UNIQUE(name).
        conn.execute(
            text(
                "INSERT INTO exercises (name, weight_type, user_id) "
                "VALUES ('Bench press', 'barbell_plate_per_side', :uid)"
            ),
            {"uid": friend_id},
        )


def test_migration_is_idempotent(old_schema_db, monkeypatch):
    monkeypatch.setenv("AUTH_EMAIL", "owner@example.com")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", "some-bcrypt-hash")
    database_module.run_migrations()
    database_module.run_migrations()  # must be a no-op, not raise or duplicate

    with database_module.engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM users WHERE email = 'owner@example.com'")
        ).scalar()
        assert count == 1
        assert conn.execute(text("SELECT COUNT(*) FROM exercises")).scalar() == 2


def test_migration_without_auth_env_vars_uses_local_account(old_schema_db):
    # No AUTH_EMAIL/AUTH_PASSWORD_HASH set: local/laptop usage, existing data
    # must still land somewhere sane rather than being orphaned.
    database_module.run_migrations()

    with database_module.engine.connect() as conn:
        local_user = conn.execute(
            text("SELECT id FROM users WHERE email = 'local@localhost'")
        ).first()
        assert local_user is not None
        exercise_user_ids = {
            row[0] for row in conn.execute(text("SELECT DISTINCT user_id FROM exercises"))
        }
        assert exercise_user_ids == {local_user[0]}
