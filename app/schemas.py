from datetime import date
from typing import List, Optional

from pydantic import BaseModel


class ParseRequest(BaseModel):
    raw_text: str


class SetOut(BaseModel):
    set_number: int
    weight_recorded: float
    reps_full: int
    reps_partial: Optional[int] = None
    raw_rep_string: str


class ExerciseOut(BaseModel):
    name: str
    order_index: int
    weight_type_guess: str
    is_unrecognized: bool
    sets: List[SetOut]


class SessionOut(BaseModel):
    date: Optional[date]
    date_confidence: str
    workout_type_guess: Optional[str]
    raw_text: str
    exercises: List[ExerciseOut]


class ParseResponse(BaseModel):
    sessions: List[SessionOut]
    unrecognized_exercise_names: List[str]
    warnings: List[str]
    workout_types: List[str]


class SaveSetIn(BaseModel):
    set_number: int
    weight_recorded: float
    reps_full: int
    reps_partial: Optional[int] = None
    raw_rep_string: str


class SaveExerciseIn(BaseModel):
    name: str
    order_index: int
    weight_type: str
    bar_weight: Optional[float] = None
    sets: List[SaveSetIn]


class SaveSessionIn(BaseModel):
    date: date
    date_confidence: str
    workout_type: Optional[str] = None
    raw_text: Optional[str] = None
    exercises: List[SaveExerciseIn]


class SaveRequest(BaseModel):
    sessions: List[SaveSessionIn]


class SaveResponse(BaseModel):
    saved_session_ids: List[int]
    session_count: int
    new_exercise_count: int


class ExerciseListItem(BaseModel):
    id: int
    name: str
    weight_type: str
    category: Optional[str] = None

    class Config:
        from_attributes = True


class ProgressPoint(BaseModel):
    session_id: int
    date: date
    date_confidence: str
    order_index: int
    set_number: int
    weight_recorded: float
    total_weight: float
    reps_full: int
    reps_partial: Optional[int]
    est_1rm: float
    workout_type: Optional[str]


class VolumePoint(BaseModel):
    period_start: date
    total_volume: float
    workout_type: Optional[str] = None


class FrequencyPoint(BaseModel):
    period_start: date
    session_count: int
    workout_type: Optional[str] = None


class PRItem(BaseModel):
    exercise_id: int
    exercise_name: str
    date: date
    weight_recorded: float
    total_weight: float
    reps_full: int
    est_1rm: float
