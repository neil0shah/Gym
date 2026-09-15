import datetime
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
    bar_weight_guess: Optional[float] = None
    combined_both_sides: bool = False
    sets: List[SetOut]


class SessionOut(BaseModel):
    date: Optional[date]
    date_confidence: str
    workout_type_guess: Optional[str]
    raw_text: str
    note: Optional[str] = None
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
    combined_both_sides: bool = False
    sets: List[SaveSetIn]


class SaveSessionIn(BaseModel):
    date: date
    date_confidence: str
    workout_type: Optional[str] = None
    raw_text: Optional[str] = None
    note: Optional[str] = None
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
    bar_weight: Optional[float] = None
    combined_both_sides: bool = False
    category: Optional[str] = None
    group_id: Optional[int] = None
    group_name: Optional[str] = None

    class Config:
        from_attributes = True


class ExerciseUpdate(BaseModel):
    weight_type: Optional[str] = None
    bar_weight: Optional[float] = None
    combined_both_sides: Optional[bool] = None
    # Optional[int]/[str] = None is ambiguous between "leave alone" and
    # "clear the field" — the route distinguishes the two via
    # `model_fields_set`, so only send these keys at all when you mean to
    # change them (a JSON null clears the field; omitting the key entirely
    # leaves it untouched).
    group_id: Optional[int] = None
    category: Optional[str] = None  # informal "Muscle Group" tag, e.g. "Biceps"


class ExerciseHistorySet(BaseModel):
    set_number: int
    weight_recorded: float
    reps_full: int
    reps_partial: Optional[int] = None
    raw_rep_string: Optional[str] = None


class ExerciseHistorySession(BaseModel):
    session_id: int
    date: date
    date_confidence: str
    workout_type: Optional[str] = None
    note: Optional[str] = None
    sets: List[ExerciseHistorySet]


class ExerciseGroupItem(BaseModel):
    id: int
    name: str
    exercise_ids: List[int]


class ExerciseGroupCreate(BaseModel):
    name: str
    exercise_ids: List[int] = []


class ExerciseGroupUpdate(BaseModel):
    name: Optional[str] = None
    add_exercise_ids: List[int] = []
    remove_exercise_ids: List[int] = []


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


class DaySetItem(BaseModel):
    id: int
    set_number: int
    weight_recorded: float
    reps_full: int
    reps_partial: Optional[int] = None
    raw_rep_string: Optional[str] = None  # what was originally typed, for comparison; not editable


class DaySessionExerciseItem(BaseModel):
    id: int
    exercise_id: int
    exercise_name: str
    order_index: int
    sets: List[DaySetItem]


class DaySessionItem(BaseModel):
    id: int
    date: date
    date_confidence: str
    workout_type: Optional[str] = None
    note: Optional[str] = None
    raw_note_text: Optional[str] = None  # the original pasted text, for comparison; not editable
    exercises: List[DaySessionExerciseItem]


class DaySessionUpdate(BaseModel):
    # Qualified as datetime.date (not the bare `date` import) because a field
    # literally named "date" with an Optional[date] = None default shadows
    # the type itself under Pydantic v2's annotation resolution, silently
    # turning the field into NoneType — see the field's own name for why.
    date: Optional[datetime.date] = None
    # workout_type/note: like ExerciseUpdate.category above, omit the key to
    # leave alone, send an explicit null to clear (model_fields_set).
    date_confidence: Optional[str] = None
    workout_type: Optional[str] = None
    note: Optional[str] = None


class SessionExerciseCreate(BaseModel):
    exercise_id: int


class SessionExerciseUpdate(BaseModel):
    exercise_id: Optional[int] = None
    order_index: Optional[int] = None


class SetCreate(BaseModel):
    weight_recorded: float = 0
    reps_full: int = 0
    reps_partial: Optional[int] = None


class SetUpdate(BaseModel):
    weight_recorded: Optional[float] = None
    reps_full: Optional[int] = None
    reps_partial: Optional[int] = None  # always sent explicitly by the UI, so no leave-alone ambiguity here
    set_number: Optional[int] = None


class PRItem(BaseModel):
    exercise_id: int
    exercise_name: str
    variation_name: str  # exercise's group name if it's in one, else its own name
    group_id: Optional[int] = None
    date: date
    weight_recorded: float
    total_weight: float
    reps_full: int
    est_1rm: float
