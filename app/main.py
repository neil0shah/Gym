import os
import uuid
from collections import defaultdict
from datetime import date, timedelta
from typing import List, Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.database import Base, engine, get_db, run_migrations
from app import models, schemas
from app.auth import (
    AUTH_ENABLED, NotAuthenticated, SESSION_MAX_AGE_SECONDS, SignupError,
    bootstrap_owner_and_seed, create_account, get_current_user,
    not_authenticated_handler, require_login, verify_credentials,
)
from app.lookup import get_known_exercises, get_split_config, get_workout_types
from app.parser import parse_notes
from app.stats import epley_1rm

Base.metadata.create_all(bind=engine)
run_migrations()

if AUTH_ENABLED and not os.environ.get("SESSION_SECRET_KEY"):
    raise RuntimeError(
        "AUTH_EMAIL/AUTH_PASSWORD_HASH are set but SESSION_SECRET_KEY is not — "
        "set a stable random secret (see README) so logins survive a restart."
    )
session_secret = os.environ.get("SESSION_SECRET_KEY") or os.urandom(32).hex()

app = FastAPI(title="Gym Progress Tracker", dependencies=[Depends(require_login)])
app.add_exception_handler(NotAuthenticated, not_authenticated_handler)
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    max_age=SESSION_MAX_AGE_SECONDS,
    same_site="lax",
    https_only=os.environ.get("SESSION_HTTPS_ONLY", "true" if AUTH_ENABLED else "false").lower() == "true",
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
templates.env.globals["auth_enabled"] = AUTH_ENABLED
# Appended to our own static asset URLs (?v=...) so a new deploy always gets
# a fresh URL for changed CSS/JS — otherwise browsers can keep serving a
# cached copy of an old file indefinitely after a redeploy, since neither
# the filename nor its request path ever changes on its own.
templates.env.globals["static_version"] = uuid.uuid4().hex[:10]


@app.on_event("startup")
def startup_seed():
    db = next(get_db())
    try:
        bootstrap_owner_and_seed(db)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Login (no-op unless AUTH_EMAIL/AUTH_PASSWORD_HASH are configured — see app/auth.py)
# ---------------------------------------------------------------------------

@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login_submit(
    request: Request, email: str = Form(...), password: str = Form(...),
    db: DBSession = Depends(get_db),
):
    user = verify_credentials(db, email, password)
    if user is None:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Incorrect email or password."}, status_code=401,
        )
    request.session["user_id"] = user.id
    return RedirectResponse(url="/import", status_code=302)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


@app.get("/signup")
def signup_page(request: Request):
    if not AUTH_ENABLED:
        return RedirectResponse(url="/import", status_code=302)
    return templates.TemplateResponse("signup.html", {"request": request, "error": None})


@app.post("/signup")
def signup_submit(
    request: Request, email: str = Form(...), password: str = Form(...),
    confirm_password: str = Form(...), db: DBSession = Depends(get_db),
):
    if not AUTH_ENABLED:
        return RedirectResponse(url="/import", status_code=302)
    if password != confirm_password:
        return templates.TemplateResponse(
            "signup.html", {"request": request, "error": "Passwords don't match."}, status_code=400,
        )
    try:
        user = create_account(db, email, password)
    except SignupError as e:
        return templates.TemplateResponse(
            "signup.html", {"request": request, "error": str(e)}, status_code=400,
        )
    request.session["user_id"] = user.id
    return RedirectResponse(url="/import", status_code=302)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return RedirectResponse(url="/import")


@app.get("/import")
def import_page(request: Request, current_user: models.User = Depends(get_current_user)):
    return templates.TemplateResponse(
        "import.html", {"request": request, "current_user_email": current_user.email}
    )


@app.get("/progress")
def progress_page(request: Request, current_user: models.User = Depends(get_current_user)):
    return templates.TemplateResponse(
        "progress.html", {"request": request, "current_user_email": current_user.email}
    )


@app.get("/exercises")
def exercises_page(request: Request, current_user: models.User = Depends(get_current_user)):
    return templates.TemplateResponse(
        "exercises.html", {"request": request, "current_user_email": current_user.email}
    )


@app.get("/data")
def data_page(request: Request, current_user: models.User = Depends(get_current_user)):
    return templates.TemplateResponse(
        "data.html", {"request": request, "current_user_email": current_user.email}
    )


# ---------------------------------------------------------------------------
# Import / parse / save
# ---------------------------------------------------------------------------

@app.post("/api/parse", response_model=schemas.ParseResponse)
def api_parse(
    req: schemas.ParseRequest, db: DBSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    known_exercises = get_known_exercises(db, current_user.id)
    split_config = get_split_config(db)
    result = parse_notes(req.raw_text, known_exercises, split_config)
    return schemas.ParseResponse(
        sessions=[
            schemas.SessionOut(
                date=s.date,
                date_confidence=s.date_confidence,
                workout_type_guess=s.workout_type_guess,
                raw_text=s.raw_text,
                note=s.note,
                exercises=[
                    schemas.ExerciseOut(
                        name=ex.name,
                        order_index=ex.order_index,
                        weight_type_guess=ex.weight_type_guess,
                        is_unrecognized=ex.is_unrecognized,
                        bar_weight_guess=ex.bar_weight_guess,
                        combined_both_sides=ex.combined_both_sides,
                        sets=[
                            schemas.SetOut(
                                set_number=st.set_number,
                                weight_recorded=st.weight_recorded,
                                reps_full=st.reps_full,
                                reps_partial=st.reps_partial,
                                raw_rep_string=st.raw_rep_string,
                            )
                            for st in ex.sets
                        ],
                    )
                    for ex in s.exercises
                ],
            )
            for s in result.sessions
        ],
        unrecognized_exercise_names=result.unrecognized_exercise_names,
        warnings=result.warnings,
        workout_types=get_workout_types(db),
    )


@app.post("/api/save", response_model=schemas.SaveResponse)
def api_save(
    req: schemas.SaveRequest, db: DBSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    saved_session_ids = []
    new_exercise_count = 0

    for session_in in req.sessions:
        session_row = models.Session(
            date=session_in.date,
            date_confidence=session_in.date_confidence,
            workout_type=session_in.workout_type,
            raw_note_text=session_in.raw_text,
            note=session_in.note,
            user_id=current_user.id,
        )
        db.add(session_row)
        db.flush()  # get session_row.id

        for exercise_in in session_in.exercises:
            exercise_row = (
                db.query(models.Exercise)
                .filter(
                    func.lower(models.Exercise.name) == exercise_in.name.lower(),
                    models.Exercise.user_id == current_user.id,
                )
                .first()
            )
            if exercise_row is None:
                exercise_row = models.Exercise(
                    name=exercise_in.name,
                    weight_type=exercise_in.weight_type,
                    bar_weight=exercise_in.bar_weight,
                    combined_both_sides=exercise_in.combined_both_sides,
                    user_id=current_user.id,
                )
                db.add(exercise_row)
                db.flush()
                new_exercise_count += 1
            else:
                if exercise_row.weight_type != exercise_in.weight_type:
                    exercise_row.weight_type = exercise_in.weight_type
                if exercise_in.bar_weight is not None:
                    exercise_row.bar_weight = exercise_in.bar_weight
                exercise_row.combined_both_sides = exercise_in.combined_both_sides

            session_exercise_row = models.SessionExercise(
                session_id=session_row.id,
                exercise_id=exercise_row.id,
                order_index=exercise_in.order_index,
            )
            db.add(session_exercise_row)
            db.flush()

            for set_in in exercise_in.sets:
                db.add(models.SetRecord(
                    session_exercise_id=session_exercise_row.id,
                    set_number=set_in.set_number,
                    weight_recorded=set_in.weight_recorded,
                    reps_full=set_in.reps_full,
                    reps_partial=set_in.reps_partial,
                    raw_rep_string=set_in.raw_rep_string,
                ))

        saved_session_ids.append(session_row.id)

    db.commit()
    return schemas.SaveResponse(
        saved_session_ids=saved_session_ids,
        session_count=len(saved_session_ids),
        new_exercise_count=new_exercise_count,
    )


# ---------------------------------------------------------------------------
# Exercises
# ---------------------------------------------------------------------------

@app.get("/api/exercises", response_model=List[schemas.ExerciseListItem])
def api_exercises(
    has_data: bool = False, db: DBSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.Exercise).filter(models.Exercise.user_id == current_user.id)
    if has_data:
        query = query.filter(
            db.query(models.SessionExercise)
            .filter(models.SessionExercise.exercise_id == models.Exercise.id)
            .exists()
        )
    exercises = query.order_by(models.Exercise.name).all()
    return [
        schemas.ExerciseListItem(
            id=e.id,
            name=e.name,
            weight_type=e.weight_type,
            bar_weight=e.bar_weight,
            combined_both_sides=e.combined_both_sides,
            category=e.category,
            group_id=e.group_id,
            group_name=e.group.name if e.group is not None else None,
        )
        for e in exercises
    ]


@app.put("/api/exercises/{exercise_id}", response_model=schemas.ExerciseListItem)
def api_update_exercise(
    exercise_id: int, req: schemas.ExerciseUpdate, db: DBSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    exercise = db.query(models.Exercise).filter(
        models.Exercise.id == exercise_id, models.Exercise.user_id == current_user.id,
    ).first()
    if exercise is None:
        raise HTTPException(status_code=404, detail="Exercise not found")
    if req.weight_type is not None:
        exercise.weight_type = req.weight_type
    if req.bar_weight is not None:
        exercise.bar_weight = req.bar_weight
    if req.combined_both_sides is not None:
        exercise.combined_both_sides = req.combined_both_sides
    if "category" in req.model_fields_set:
        exercise.category = req.category.strip() if req.category and req.category.strip() else None
    if "group_id" in req.model_fields_set:
        if req.group_id is not None:
            group = db.query(models.ExerciseGroup).filter(
                models.ExerciseGroup.id == req.group_id, models.ExerciseGroup.user_id == current_user.id,
            ).first()
            if group is None:
                raise HTTPException(status_code=404, detail="Group not found")
        exercise.group_id = req.group_id
    db.commit()
    db.refresh(exercise)
    return schemas.ExerciseListItem(
        id=exercise.id,
        name=exercise.name,
        weight_type=exercise.weight_type,
        bar_weight=exercise.bar_weight,
        combined_both_sides=exercise.combined_both_sides,
        category=exercise.category,
        group_id=exercise.group_id,
        group_name=exercise.group.name if exercise.group is not None else None,
    )


@app.get("/api/workout_types", response_model=List[str])
def api_workout_types(db: DBSession = Depends(get_db)):
    return get_workout_types(db)


@app.get("/api/exercises/{exercise_id}/history", response_model=List[schemas.ExerciseHistorySession])
def api_exercise_history(
    exercise_id: int, db: DBSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Every session this exercise was logged in, most recent first, with
    the raw per-set weight/reps as recorded (not the normalized total_weight
    the Progress charts use) — for drilling into the underlying data behind
    a chart from the Manage Exercises page.
    """
    exercise = db.query(models.Exercise).filter(
        models.Exercise.id == exercise_id, models.Exercise.user_id == current_user.id,
    ).first()
    if exercise is None:
        raise HTTPException(status_code=404, detail="Exercise not found")

    rows = (
        db.query(models.SetRecord, models.SessionExercise, models.Session)
        .join(models.SessionExercise, models.SetRecord.session_exercise_id == models.SessionExercise.id)
        .join(models.Session, models.SessionExercise.session_id == models.Session.id)
        .filter(models.SessionExercise.exercise_id == exercise_id)
        .order_by(models.Session.date.desc(), models.SessionExercise.order_index.asc(), models.SetRecord.set_number.asc())
        .all()
    )

    by_session_exercise: dict = {}
    order: List[int] = []
    for set_row, session_exercise, session in rows:
        key = session_exercise.id
        if key not in by_session_exercise:
            by_session_exercise[key] = schemas.ExerciseHistorySession(
                session_id=session.id,
                date=session.date,
                date_confidence=session.date_confidence,
                workout_type=session.workout_type,
                note=session.note,
                sets=[],
            )
            order.append(key)
        by_session_exercise[key].sets.append(schemas.ExerciseHistorySet(
            set_number=set_row.set_number,
            weight_recorded=set_row.weight_recorded,
            reps_full=set_row.reps_full,
            reps_partial=set_row.reps_partial,
            raw_rep_string=set_row.raw_rep_string,
        ))
    return [by_session_exercise[key] for key in order]


# ---------------------------------------------------------------------------
# Exercise groups (combine renamed/varied movements for trend continuity)
# ---------------------------------------------------------------------------

def _exercise_group_item(group: models.ExerciseGroup) -> schemas.ExerciseGroupItem:
    return schemas.ExerciseGroupItem(
        id=group.id,
        name=group.name,
        exercise_ids=[e.id for e in group.exercises],
    )


@app.get("/api/exercise_groups", response_model=List[schemas.ExerciseGroupItem])
def api_list_exercise_groups(
    db: DBSession = Depends(get_db), current_user: models.User = Depends(get_current_user),
):
    groups = (
        db.query(models.ExerciseGroup)
        .filter(models.ExerciseGroup.user_id == current_user.id)
        .order_by(models.ExerciseGroup.name)
        .all()
    )
    return [_exercise_group_item(g) for g in groups]


@app.post("/api/exercise_groups", response_model=schemas.ExerciseGroupItem)
def api_create_exercise_group(
    req: schemas.ExerciseGroupCreate, db: DBSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    existing = db.query(models.ExerciseGroup).filter(
        func.lower(models.ExerciseGroup.name) == req.name.lower(),
        models.ExerciseGroup.user_id == current_user.id,
    ).first()
    if existing is not None:
        raise HTTPException(status_code=400, detail=f"A group named {req.name!r} already exists")
    group = models.ExerciseGroup(name=req.name, user_id=current_user.id)
    db.add(group)
    db.flush()
    if req.exercise_ids:
        db.query(models.Exercise).filter(
            models.Exercise.id.in_(req.exercise_ids), models.Exercise.user_id == current_user.id,
        ).update({"group_id": group.id}, synchronize_session=False)
    db.commit()
    db.refresh(group)
    return _exercise_group_item(group)


@app.put("/api/exercise_groups/{group_id}", response_model=schemas.ExerciseGroupItem)
def api_update_exercise_group(
    group_id: int, req: schemas.ExerciseGroupUpdate, db: DBSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    group = db.query(models.ExerciseGroup).filter(
        models.ExerciseGroup.id == group_id, models.ExerciseGroup.user_id == current_user.id,
    ).first()
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    if req.name is not None:
        group.name = req.name
    if req.add_exercise_ids:
        db.query(models.Exercise).filter(
            models.Exercise.id.in_(req.add_exercise_ids), models.Exercise.user_id == current_user.id,
        ).update({"group_id": group.id}, synchronize_session=False)
    if req.remove_exercise_ids:
        db.query(models.Exercise).filter(
            models.Exercise.id.in_(req.remove_exercise_ids),
            models.Exercise.group_id == group.id,
            models.Exercise.user_id == current_user.id,
        ).update({"group_id": None}, synchronize_session=False)
    db.commit()
    db.refresh(group)
    return _exercise_group_item(group)


@app.delete("/api/exercise_groups/{group_id}")
def api_delete_exercise_group(
    group_id: int, db: DBSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    group = db.query(models.ExerciseGroup).filter(
        models.ExerciseGroup.id == group_id, models.ExerciseGroup.user_id == current_user.id,
    ).first()
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    db.query(models.Exercise).filter(
        models.Exercise.group_id == group_id, models.Exercise.user_id == current_user.id,
    ).update({"group_id": None}, synchronize_session=False)
    db.delete(group)
    db.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------

def _base_set_query(db: DBSession, user_id: int):
    # The Exercise.user_id filter is the load-bearing part of this query: it's
    # what stops one account's exercise_id/group_id from ever pulling another
    # account's workout data, even if a foreign id is passed in.
    return (
        db.query(models.SetRecord, models.SessionExercise, models.Session, models.Exercise)
        .join(models.SessionExercise, models.SetRecord.session_exercise_id == models.SessionExercise.id)
        .join(models.Session, models.SessionExercise.session_id == models.Session.id)
        .join(models.Exercise, models.SessionExercise.exercise_id == models.Exercise.id)
        .filter(models.Exercise.user_id == user_id)
    )


def _group_exercise_ids(db: DBSession, group_id: int, user_id: int) -> List[int]:
    return [
        row.id for row in
        db.query(models.Exercise.id)
        .filter(models.Exercise.group_id == group_id, models.Exercise.user_id == user_id)
        .all()
    ]


def _progress_points_for_query(query) -> List[schemas.ProgressPoint]:
    points = []
    for set_row, session_exercise, session, exercise in query.all():
        total_weight = exercise.total_weight_for(set_row.weight_recorded)
        points.append(schemas.ProgressPoint(
            session_id=session.id,
            date=session.date,
            date_confidence=session.date_confidence,
            order_index=session_exercise.order_index,
            set_number=set_row.set_number,
            weight_recorded=set_row.weight_recorded,
            total_weight=total_weight,
            reps_full=set_row.reps_full,
            reps_partial=set_row.reps_partial,
            est_1rm=epley_1rm(total_weight, set_row.reps_full),
            workout_type=session.workout_type,
        ))
    return points


@app.get("/api/progress/exercise/{exercise_id}", response_model=List[schemas.ProgressPoint])
def api_progress_exercise(
    exercise_id: int,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    order_index: Optional[int] = None,
    workout_type: Optional[str] = None,
    db: DBSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = _base_set_query(db, current_user.id).filter(models.Exercise.id == exercise_id)
    if start_date is not None:
        query = query.filter(models.Session.date >= start_date)
    if end_date is not None:
        query = query.filter(models.Session.date <= end_date)
    if order_index is not None:
        query = query.filter(models.SessionExercise.order_index == order_index)
    if workout_type is not None:
        query = query.filter(models.Session.workout_type == workout_type)
    query = query.order_by(models.Session.date, models.SetRecord.set_number)
    return _progress_points_for_query(query)


@app.get("/api/progress/group/{group_id}", response_model=List[schemas.ProgressPoint])
def api_progress_group(
    group_id: int,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    order_index: Optional[int] = None,
    workout_type: Optional[str] = None,
    db: DBSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Same as /api/progress/exercise/{id} but aggregated across every exercise
    in the group, so renamed/varied movements (e.g. "Flat chest press" and
    "Barbell bench press") show up as one continuous trend.
    """
    member_ids = _group_exercise_ids(db, group_id, current_user.id)
    if not member_ids:
        return []
    query = _base_set_query(db, current_user.id).filter(models.Exercise.id.in_(member_ids))
    if start_date is not None:
        query = query.filter(models.Session.date >= start_date)
    if end_date is not None:
        query = query.filter(models.Session.date <= end_date)
    if order_index is not None:
        query = query.filter(models.SessionExercise.order_index == order_index)
    if workout_type is not None:
        query = query.filter(models.Session.workout_type == workout_type)
    query = query.order_by(models.Session.date, models.SetRecord.set_number)
    return _progress_points_for_query(query)


def _period_start(d: date, period: str) -> date:
    if period == "day":
        return d
    if period == "month":
        return d.replace(day=1)
    # default: week, Monday-start
    return d - timedelta(days=d.weekday())


@app.get("/api/progress/volume", response_model=List[schemas.VolumePoint])
def api_progress_volume(
    exercise_id: Optional[int] = None,
    group_id: Optional[int] = None,
    workout_type: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    period: str = Query("week", pattern="^(day|week|month)$"),
    db: DBSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = _base_set_query(db, current_user.id)
    if group_id is not None:
        member_ids = _group_exercise_ids(db, group_id, current_user.id)
        if not member_ids:
            return []
        query = query.filter(models.Exercise.id.in_(member_ids))
    elif exercise_id is not None:
        query = query.filter(models.Exercise.id == exercise_id)
    if workout_type is not None:
        query = query.filter(models.Session.workout_type == workout_type)
    if start_date is not None:
        query = query.filter(models.Session.date >= start_date)
    if end_date is not None:
        query = query.filter(models.Session.date <= end_date)

    totals = defaultdict(float)
    for set_row, session_exercise, session, exercise in query.all():
        total_weight = exercise.total_weight_for(set_row.weight_recorded)
        volume = total_weight * set_row.reps_full
        key = _period_start(session.date, period)
        totals[key] += volume

    return [
        schemas.VolumePoint(period_start=k, total_volume=v)
        for k, v in sorted(totals.items())
    ]


@app.get("/api/progress/frequency", response_model=List[schemas.FrequencyPoint])
def api_progress_frequency(
    workout_type: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    period: str = Query("week", pattern="^(day|week|month)$"),
    db: DBSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.Session).filter(models.Session.user_id == current_user.id)
    if workout_type is not None:
        query = query.filter(models.Session.workout_type == workout_type)
    if start_date is not None:
        query = query.filter(models.Session.date >= start_date)
    if end_date is not None:
        query = query.filter(models.Session.date <= end_date)

    counts = defaultdict(int)
    for session in query.all():
        key = _period_start(session.date, period)
        counts[key] += 1

    return [
        schemas.FrequencyPoint(period_start=k, session_count=v)
        for k, v in sorted(counts.items())
    ]


@app.get("/api/progress/prs", response_model=List[schemas.PRItem])
def api_progress_prs(
    exercise_id: Optional[int] = None,
    db: DBSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = _base_set_query(db, current_user.id)
    if exercise_id is not None:
        query = query.filter(models.Exercise.id == exercise_id)

    # Keyed by exercise variation (the group, if it's in one) rather than by
    # literal exercise — a dumbbell exercise and a machine exercise grouped
    # as the same variation should show one PR, but two exercises that are
    # deliberately kept as separate variations (different equipment, not
    # comparable numbers) must never be merged into one row.
    best: dict = {}
    for set_row, session_exercise, session, exercise in query.all():
        total_weight = exercise.total_weight_for(set_row.weight_recorded)
        est = epley_1rm(total_weight, set_row.reps_full)
        variation_key = exercise.group_id if exercise.group_id is not None else f"exercise:{exercise.id}"
        variation_name = exercise.group.name if exercise.group_id is not None else exercise.name
        current = best.get(variation_key)
        if current is None or est > current[0] or (est == current[0] and session.date < current[1].date):
            best[variation_key] = (est, schemas.PRItem(
                exercise_id=exercise.id,
                exercise_name=exercise.name,
                variation_name=variation_name,
                group_id=exercise.group_id,
                date=session.date,
                weight_recorded=set_row.weight_recorded,
                total_weight=total_weight,
                reps_full=set_row.reps_full,
                est_1rm=est,
            ))

    items = [v[1] for v in best.values()]
    items.sort(key=lambda p: p.variation_name)
    return items


# ---------------------------------------------------------------------------
# Data Lookup: view and correct the raw saved data behind a specific date
# ---------------------------------------------------------------------------

@app.get("/api/logged_dates", response_model=List[date])
def api_logged_dates(db: DBSession = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    rows = (
        db.query(models.Session.date)
        .filter(models.Session.user_id == current_user.id)
        .distinct()
        .order_by(models.Session.date.desc())
        .all()
    )
    return [r[0] for r in rows]


def _day_session_item(session: models.Session) -> schemas.DaySessionItem:
    return schemas.DaySessionItem(
        id=session.id,
        date=session.date,
        date_confidence=session.date_confidence,
        workout_type=session.workout_type,
        note=session.note,
        raw_note_text=session.raw_note_text,
        exercises=[
            schemas.DaySessionExerciseItem(
                id=se.id,
                exercise_id=se.exercise_id,
                exercise_name=se.exercise.name,
                order_index=se.order_index,
                sets=[
                    schemas.DaySetItem(
                        id=s.id, set_number=s.set_number, weight_recorded=s.weight_recorded,
                        reps_full=s.reps_full, reps_partial=s.reps_partial, raw_rep_string=s.raw_rep_string,
                    )
                    for s in se.sets
                ],
            )
            for se in session.session_exercises
        ],
    )


@app.get("/api/days/{on_date}", response_model=List[schemas.DaySessionItem])
def api_day_sessions(
    on_date: date, db: DBSession = Depends(get_db), current_user: models.User = Depends(get_current_user),
):
    sessions = (
        db.query(models.Session)
        .filter(models.Session.user_id == current_user.id, models.Session.date == on_date)
        .order_by(models.Session.id)
        .all()
    )
    return [_day_session_item(s) for s in sessions]


def _get_owned_session(db: DBSession, session_id: int, user_id: int) -> models.Session:
    session = db.query(models.Session).filter(
        models.Session.id == session_id, models.Session.user_id == user_id,
    ).first()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def _get_owned_session_exercise(db: DBSession, session_exercise_id: int, user_id: int) -> models.SessionExercise:
    session_exercise = (
        db.query(models.SessionExercise)
        .join(models.Session, models.SessionExercise.session_id == models.Session.id)
        .filter(models.SessionExercise.id == session_exercise_id, models.Session.user_id == user_id)
        .first()
    )
    if session_exercise is None:
        raise HTTPException(status_code=404, detail="Exercise entry not found")
    return session_exercise


def _get_owned_set(db: DBSession, set_id: int, user_id: int) -> models.SetRecord:
    set_row = (
        db.query(models.SetRecord)
        .join(models.SessionExercise, models.SetRecord.session_exercise_id == models.SessionExercise.id)
        .join(models.Session, models.SessionExercise.session_id == models.Session.id)
        .filter(models.SetRecord.id == set_id, models.Session.user_id == user_id)
        .first()
    )
    if set_row is None:
        raise HTTPException(status_code=404, detail="Set not found")
    return set_row


def _day_session_exercise_item(session_exercise: models.SessionExercise) -> schemas.DaySessionExerciseItem:
    return schemas.DaySessionExerciseItem(
        id=session_exercise.id,
        exercise_id=session_exercise.exercise_id,
        exercise_name=session_exercise.exercise.name,
        order_index=session_exercise.order_index,
        sets=[
            schemas.DaySetItem(
                id=s.id, set_number=s.set_number, weight_recorded=s.weight_recorded,
                reps_full=s.reps_full, reps_partial=s.reps_partial, raw_rep_string=s.raw_rep_string,
            )
            for s in session_exercise.sets
        ],
    )


@app.put("/api/day-sessions/{session_id}", response_model=schemas.DaySessionItem)
def api_update_day_session(
    session_id: int, req: schemas.DaySessionUpdate, db: DBSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    session = _get_owned_session(db, session_id, current_user.id)
    if req.date is not None:
        session.date = req.date
    if "date_confidence" in req.model_fields_set and req.date_confidence is not None:
        if req.date_confidence not in (models.CONFIRMED, models.ESTIMATED):
            raise HTTPException(status_code=422, detail="date_confidence must be 'confirmed' or 'estimated'")
        session.date_confidence = req.date_confidence
    if "workout_type" in req.model_fields_set:
        session.workout_type = req.workout_type.strip() if req.workout_type and req.workout_type.strip() else None
    if "note" in req.model_fields_set:
        session.note = req.note.strip() if req.note and req.note.strip() else None
    db.commit()
    db.refresh(session)
    return _day_session_item(session)


@app.delete("/api/day-sessions/{session_id}")
def api_delete_day_session(
    session_id: int, db: DBSession = Depends(get_db), current_user: models.User = Depends(get_current_user),
):
    session = _get_owned_session(db, session_id, current_user.id)
    db.delete(session)
    db.commit()
    return {"deleted": True}


@app.post("/api/day-sessions/{session_id}/exercises", response_model=schemas.DaySessionExerciseItem)
def api_add_day_session_exercise(
    session_id: int, req: schemas.SessionExerciseCreate, db: DBSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    session = _get_owned_session(db, session_id, current_user.id)
    exercise = db.query(models.Exercise).filter(
        models.Exercise.id == req.exercise_id, models.Exercise.user_id == current_user.id,
    ).first()
    if exercise is None:
        raise HTTPException(status_code=404, detail="Exercise not found")
    next_order = max([se.order_index for se in session.session_exercises], default=-1) + 1
    session_exercise = models.SessionExercise(session_id=session.id, exercise_id=exercise.id, order_index=next_order)
    db.add(session_exercise)
    db.commit()
    db.refresh(session_exercise)
    return _day_session_exercise_item(session_exercise)


@app.put("/api/day-session-exercises/{session_exercise_id}", response_model=schemas.DaySessionExerciseItem)
def api_update_day_session_exercise(
    session_exercise_id: int, req: schemas.SessionExerciseUpdate, db: DBSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    session_exercise = _get_owned_session_exercise(db, session_exercise_id, current_user.id)
    if req.exercise_id is not None:
        exercise = db.query(models.Exercise).filter(
            models.Exercise.id == req.exercise_id, models.Exercise.user_id == current_user.id,
        ).first()
        if exercise is None:
            raise HTTPException(status_code=404, detail="Exercise not found")
        session_exercise.exercise_id = exercise.id
    if req.order_index is not None:
        session_exercise.order_index = req.order_index
    db.commit()
    db.refresh(session_exercise)
    return _day_session_exercise_item(session_exercise)


@app.delete("/api/day-session-exercises/{session_exercise_id}")
def api_delete_day_session_exercise(
    session_exercise_id: int, db: DBSession = Depends(get_db), current_user: models.User = Depends(get_current_user),
):
    session_exercise = _get_owned_session_exercise(db, session_exercise_id, current_user.id)
    db.delete(session_exercise)
    db.commit()
    return {"deleted": True}


@app.post("/api/day-session-exercises/{session_exercise_id}/sets", response_model=schemas.DaySetItem)
def api_add_set(
    session_exercise_id: int, req: schemas.SetCreate, db: DBSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    session_exercise = _get_owned_session_exercise(db, session_exercise_id, current_user.id)
    next_set_number = max([s.set_number for s in session_exercise.sets], default=0) + 1
    set_row = models.SetRecord(
        session_exercise_id=session_exercise.id,
        set_number=next_set_number,
        weight_recorded=req.weight_recorded,
        reps_full=req.reps_full,
        reps_partial=req.reps_partial,
        raw_rep_string=None,  # manually added, not parsed from anything typed
    )
    db.add(set_row)
    db.commit()
    db.refresh(set_row)
    return schemas.DaySetItem(
        id=set_row.id, set_number=set_row.set_number, weight_recorded=set_row.weight_recorded,
        reps_full=set_row.reps_full, reps_partial=set_row.reps_partial, raw_rep_string=set_row.raw_rep_string,
    )


@app.put("/api/sets/{set_id}", response_model=schemas.DaySetItem)
def api_update_set(
    set_id: int, req: schemas.SetUpdate, db: DBSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    set_row = _get_owned_set(db, set_id, current_user.id)
    if req.weight_recorded is not None:
        set_row.weight_recorded = req.weight_recorded
    if req.reps_full is not None:
        set_row.reps_full = req.reps_full
    if "reps_partial" in req.model_fields_set:
        set_row.reps_partial = req.reps_partial
    if req.set_number is not None:
        set_row.set_number = req.set_number
    db.commit()
    db.refresh(set_row)
    return schemas.DaySetItem(
        id=set_row.id, set_number=set_row.set_number, weight_recorded=set_row.weight_recorded,
        reps_full=set_row.reps_full, reps_partial=set_row.reps_partial, raw_rep_string=set_row.raw_rep_string,
    )


@app.delete("/api/sets/{set_id}")
def api_delete_set(
    set_id: int, db: DBSession = Depends(get_db), current_user: models.User = Depends(get_current_user),
):
    set_row = _get_owned_set(db, set_id, current_user.id)
    db.delete(set_row)
    db.commit()
    return {"deleted": True}
