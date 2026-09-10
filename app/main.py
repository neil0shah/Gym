from collections import defaultdict
from datetime import date, timedelta
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.database import Base, engine, get_db, run_migrations
from app import models, schemas, seed_data
from app.lookup import get_known_exercises, get_split_config, get_workout_types
from app.parser import parse_notes
from app.stats import epley_1rm

Base.metadata.create_all(bind=engine)
run_migrations()

app = FastAPI(title="Gym Progress Tracker")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
def startup_seed():
    db = next(get_db())
    try:
        seed_data.seed_if_empty(db)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return RedirectResponse(url="/import")


@app.get("/import")
def import_page(request: Request):
    return templates.TemplateResponse("import.html", {"request": request})


@app.get("/progress")
def progress_page(request: Request):
    return templates.TemplateResponse("progress.html", {"request": request})


@app.get("/exercises")
def exercises_page(request: Request):
    return templates.TemplateResponse("exercises.html", {"request": request})


# ---------------------------------------------------------------------------
# Import / parse / save
# ---------------------------------------------------------------------------

@app.post("/api/parse", response_model=schemas.ParseResponse)
def api_parse(req: schemas.ParseRequest, db: DBSession = Depends(get_db)):
    known_exercises = get_known_exercises(db)
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
def api_save(req: schemas.SaveRequest, db: DBSession = Depends(get_db)):
    saved_session_ids = []
    new_exercise_count = 0

    for session_in in req.sessions:
        session_row = models.Session(
            date=session_in.date,
            date_confidence=session_in.date_confidence,
            workout_type=session_in.workout_type,
            raw_note_text=session_in.raw_text,
            note=session_in.note,
        )
        db.add(session_row)
        db.flush()  # get session_row.id

        for exercise_in in session_in.exercises:
            exercise_row = (
                db.query(models.Exercise)
                .filter(func.lower(models.Exercise.name) == exercise_in.name.lower())
                .first()
            )
            if exercise_row is None:
                exercise_row = models.Exercise(
                    name=exercise_in.name,
                    weight_type=exercise_in.weight_type,
                    bar_weight=exercise_in.bar_weight,
                )
                db.add(exercise_row)
                db.flush()
                new_exercise_count += 1
            else:
                if exercise_row.weight_type != exercise_in.weight_type:
                    exercise_row.weight_type = exercise_in.weight_type
                if exercise_in.bar_weight is not None:
                    exercise_row.bar_weight = exercise_in.bar_weight

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
def api_exercises(db: DBSession = Depends(get_db)):
    exercises = db.query(models.Exercise).order_by(models.Exercise.name).all()
    return [
        schemas.ExerciseListItem(
            id=e.id,
            name=e.name,
            weight_type=e.weight_type,
            bar_weight=e.bar_weight,
            category=e.category,
            group_id=e.group_id,
            group_name=e.group.name if e.group is not None else None,
        )
        for e in exercises
    ]


@app.get("/api/workout_types", response_model=List[str])
def api_workout_types(db: DBSession = Depends(get_db)):
    return get_workout_types(db)


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
def api_list_exercise_groups(db: DBSession = Depends(get_db)):
    groups = db.query(models.ExerciseGroup).order_by(models.ExerciseGroup.name).all()
    return [_exercise_group_item(g) for g in groups]


@app.post("/api/exercise_groups", response_model=schemas.ExerciseGroupItem)
def api_create_exercise_group(req: schemas.ExerciseGroupCreate, db: DBSession = Depends(get_db)):
    existing = db.query(models.ExerciseGroup).filter(func.lower(models.ExerciseGroup.name) == req.name.lower()).first()
    if existing is not None:
        raise HTTPException(status_code=400, detail=f"A group named {req.name!r} already exists")
    group = models.ExerciseGroup(name=req.name)
    db.add(group)
    db.flush()
    if req.exercise_ids:
        db.query(models.Exercise).filter(models.Exercise.id.in_(req.exercise_ids)).update(
            {"group_id": group.id}, synchronize_session=False
        )
    db.commit()
    db.refresh(group)
    return _exercise_group_item(group)


@app.put("/api/exercise_groups/{group_id}", response_model=schemas.ExerciseGroupItem)
def api_update_exercise_group(group_id: int, req: schemas.ExerciseGroupUpdate, db: DBSession = Depends(get_db)):
    group = db.query(models.ExerciseGroup).filter(models.ExerciseGroup.id == group_id).first()
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    if req.name is not None:
        group.name = req.name
    if req.add_exercise_ids:
        db.query(models.Exercise).filter(models.Exercise.id.in_(req.add_exercise_ids)).update(
            {"group_id": group.id}, synchronize_session=False
        )
    if req.remove_exercise_ids:
        db.query(models.Exercise).filter(
            models.Exercise.id.in_(req.remove_exercise_ids), models.Exercise.group_id == group.id
        ).update({"group_id": None}, synchronize_session=False)
    db.commit()
    db.refresh(group)
    return _exercise_group_item(group)


@app.delete("/api/exercise_groups/{group_id}")
def api_delete_exercise_group(group_id: int, db: DBSession = Depends(get_db)):
    group = db.query(models.ExerciseGroup).filter(models.ExerciseGroup.id == group_id).first()
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    db.query(models.Exercise).filter(models.Exercise.group_id == group_id).update(
        {"group_id": None}, synchronize_session=False
    )
    db.delete(group)
    db.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------

def _base_set_query(db: DBSession):
    return (
        db.query(models.SetRecord, models.SessionExercise, models.Session, models.Exercise)
        .join(models.SessionExercise, models.SetRecord.session_exercise_id == models.SessionExercise.id)
        .join(models.Session, models.SessionExercise.session_id == models.Session.id)
        .join(models.Exercise, models.SessionExercise.exercise_id == models.Exercise.id)
    )


def _group_exercise_ids(db: DBSession, group_id: int) -> List[int]:
    return [
        row.id for row in
        db.query(models.Exercise.id).filter(models.Exercise.group_id == group_id).all()
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
):
    query = _base_set_query(db).filter(models.Exercise.id == exercise_id)
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
):
    """Same as /api/progress/exercise/{id} but aggregated across every exercise
    in the group, so renamed/varied movements (e.g. "Flat chest press" and
    "Barbell bench press") show up as one continuous trend.
    """
    member_ids = _group_exercise_ids(db, group_id)
    if not member_ids:
        return []
    query = _base_set_query(db).filter(models.Exercise.id.in_(member_ids))
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
    period: str = Query("week", pattern="^(week|month)$"),
    db: DBSession = Depends(get_db),
):
    query = _base_set_query(db)
    if group_id is not None:
        member_ids = _group_exercise_ids(db, group_id)
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
    period: str = Query("week", pattern="^(week|month)$"),
    db: DBSession = Depends(get_db),
):
    query = db.query(models.Session)
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
):
    query = _base_set_query(db)
    if exercise_id is not None:
        query = query.filter(models.Exercise.id == exercise_id)

    best: dict = {}
    for set_row, session_exercise, session, exercise in query.all():
        total_weight = exercise.total_weight_for(set_row.weight_recorded)
        est = epley_1rm(total_weight, set_row.reps_full)
        current = best.get(exercise.id)
        if current is None or est > current[0] or (est == current[0] and session.date < current[1].date):
            best[exercise.id] = (est, schemas.PRItem(
                exercise_id=exercise.id,
                exercise_name=exercise.name,
                date=session.date,
                weight_recorded=set_row.weight_recorded,
                total_weight=total_weight,
                reps_full=set_row.reps_full,
                est_1rm=est,
            ))

    items = [v[1] for v in best.values()]
    items.sort(key=lambda p: p.exercise_name)
    return items
