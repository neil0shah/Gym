from typing import Dict, List, Optional, Tuple
from datetime import date

from sqlalchemy.orm import Session as DBSession

from app.models import Exercise, SplitConfigEntry


def get_known_exercises(db: DBSession) -> Dict[str, Tuple[str, str, Optional[float], bool]]:
    """lowercased exercise name -> (canonical name, weight_type, bar_weight, combined_both_sides)"""
    return {
        e.name.lower(): (e.name, e.weight_type, e.bar_weight, e.combined_both_sides)
        for e in db.query(Exercise).all()
    }


def get_split_config(db: DBSession) -> List[Tuple[str, set, date, date]]:
    """List of (workout_type, {lowercased exercise names}, start_date, end_date),
    one entry per (workout_type, date range) combination in split_config.
    """
    entries = db.query(SplitConfigEntry).all()
    grouped: Dict[Tuple[str, object, object], set] = {}
    for entry in entries:
        key = (entry.workout_type, entry.start_date, entry.end_date)
        grouped.setdefault(key, set()).add(entry.exercise_name.lower())
    return [
        (workout_type, names, start_date, end_date)
        for (workout_type, start_date, end_date), names in grouped.items()
    ]


def get_workout_types(db: DBSession) -> List[str]:
    types = sorted({e.workout_type for e in db.query(SplitConfigEntry.workout_type).distinct()})
    if "Other" not in types:
        types.append("Other")
    return types
