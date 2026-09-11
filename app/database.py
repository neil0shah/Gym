import os
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker, declarative_base

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "gym.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columns added after the initial release. Base.metadata.create_all() creates
# brand-new tables fine but never alters existing ones, so a database created
# by an earlier version of the app needs these added by hand.
_ADDED_COLUMNS = {
    "sessions": [("note", "TEXT")],
    "exercises": [
        ("group_id", "INTEGER"),
        ("combined_both_sides", "BOOLEAN NOT NULL DEFAULT 0"),
    ],
}


def run_migrations():
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if table not in existing_tables:
                continue  # create_all will create it fresh, already has the column
            existing_columns = {col["name"] for col in inspector.get_columns(table)}
            for column_name, column_type in columns:
                if column_name not in existing_columns:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_type}"))

        _migrate_to_multi_user(conn, inspector, existing_tables)


def _migrate_to_multi_user(conn, inspector, existing_tables):
    """Add per-user data isolation to a database created before multi-user
    support existed. A brand-new database never hits this path — create_all()
    already builds exercises/exercise_groups/sessions with user_id in place.

    Every pre-existing exercise/group/session is assigned to one "bootstrap"
    account (the owner, from AUTH_EMAIL/AUTH_PASSWORD_HASH, or the fixed local
    account when login is disabled) so no existing data is orphaned or lost.
    Idempotent: a no-op once exercises.user_id already exists.
    """
    if "exercises" not in existing_tables:
        return
    existing_columns = {col["name"] for col in inspector.get_columns("exercises")}
    if "user_id" in existing_columns:
        return  # already migrated

    auth_email = os.environ.get("AUTH_EMAIL")
    auth_password_hash = os.environ.get("AUTH_PASSWORD_HASH")
    if auth_email and auth_password_hash:
        bootstrap_email = auth_email.strip().lower()
        bootstrap_hash = auth_password_hash
    else:
        bootstrap_email = "local@localhost"
        bootstrap_hash = ""

    existing_user = conn.execute(
        text("SELECT id FROM users WHERE email = :email"), {"email": bootstrap_email}
    ).first()
    if existing_user is not None:
        bootstrap_user_id = existing_user[0]
    else:
        result = conn.execute(
            text("INSERT INTO users (email, password_hash) VALUES (:email, :hash)"),
            {"email": bootstrap_email, "hash": bootstrap_hash},
        )
        bootstrap_user_id = result.lastrowid

    # exercises and exercise_groups both had a *global* UNIQUE(name) before
    # multi-user support — SQLite can't alter a constraint in place, so
    # rebuild each table with UNIQUE(user_id, name) instead, preserving ids
    # (everything else references these by id, not by name).
    conn.execute(text("""
        CREATE TABLE exercises_new (
            id INTEGER PRIMARY KEY,
            name VARCHAR NOT NULL,
            weight_type VARCHAR NOT NULL,
            bar_weight FLOAT,
            category VARCHAR,
            group_id INTEGER,
            combined_both_sides BOOLEAN NOT NULL DEFAULT 0,
            user_id INTEGER NOT NULL,
            UNIQUE (user_id, name)
        )
    """))
    conn.execute(text(
        "INSERT INTO exercises_new "
        "(id, name, weight_type, bar_weight, category, group_id, combined_both_sides, user_id) "
        "SELECT id, name, weight_type, bar_weight, category, group_id, combined_both_sides, :uid "
        "FROM exercises"
    ), {"uid": bootstrap_user_id})
    conn.execute(text("DROP TABLE exercises"))
    conn.execute(text("ALTER TABLE exercises_new RENAME TO exercises"))
    conn.execute(text("CREATE INDEX ix_exercises_name ON exercises (name)"))

    conn.execute(text("""
        CREATE TABLE exercise_groups_new (
            id INTEGER PRIMARY KEY,
            name VARCHAR NOT NULL,
            user_id INTEGER NOT NULL,
            UNIQUE (user_id, name)
        )
    """))
    conn.execute(text(
        "INSERT INTO exercise_groups_new (id, name, user_id) "
        "SELECT id, name, :uid FROM exercise_groups"
    ), {"uid": bootstrap_user_id})
    conn.execute(text("DROP TABLE exercise_groups"))
    conn.execute(text("ALTER TABLE exercise_groups_new RENAME TO exercise_groups"))

    # sessions had no unique constraint on anything user-specific, so a plain
    # additive column + backfill is enough — no table rebuild needed.
    conn.execute(text("ALTER TABLE sessions ADD COLUMN user_id INTEGER"))
    conn.execute(
        text("UPDATE sessions SET user_id = :uid WHERE user_id IS NULL"),
        {"uid": bootstrap_user_id},
    )
