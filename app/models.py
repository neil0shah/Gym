from sqlalchemy import (
    Column, Integer, String, Float, Date, ForeignKey, Text, DateTime, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base

# weight_type values
BARBELL_PLATE_PER_SIDE = "barbell_plate_per_side"
DUMBBELL_EACH = "dumbbell_each"
TOTAL_WEIGHT = "total_weight"
PLATE_LOADED_PER_SIDE = "plate_loaded_per_side"
BODYWEIGHT_FIXED = "bodyweight_fixed"
WEIGHT_TYPES = {
    BARBELL_PLATE_PER_SIDE, DUMBBELL_EACH, TOTAL_WEIGHT,
    PLATE_LOADED_PER_SIDE, BODYWEIGHT_FIXED,
}

# date_confidence values
CONFIRMED = "confirmed"
ESTIMATED = "estimated"

DEFAULT_BAR_WEIGHT = 45.0
DEFAULT_BODYWEIGHT_ESTIMATE = 160.0


class ExerciseGroup(Base):
    """A user-defined bucket combining exercise names that are really the same
    movement (e.g. "Flat chest press" and "Barbell bench press"), so progress
    trends can be tracked across renamed/varied movements over time.
    """
    __tablename__ = "exercise_groups"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)

    exercises = relationship("Exercise", back_populates="group")


class Exercise(Base):
    __tablename__ = "exercises"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True, index=True)
    weight_type = Column(String, nullable=False)
    # Dual-purpose fixed-weight override: bar weight for barbell_plate_per_side
    # (default 45), assumed bodyweight for bodyweight_fixed (default 160).
    # Unused for dumbbell_each, total_weight, and plate_loaded_per_side.
    bar_weight = Column(Float, nullable=True)
    category = Column(String, nullable=True)  # e.g. muscle group, optional
    group_id = Column(Integer, ForeignKey("exercise_groups.id"), nullable=True)

    session_exercises = relationship("SessionExercise", back_populates="exercise")
    group = relationship("ExerciseGroup", back_populates="exercises")

    def total_weight_for(self, weight_recorded: float) -> float:
        if self.weight_type == BARBELL_PLATE_PER_SIDE:
            bar = self.bar_weight if self.bar_weight is not None else DEFAULT_BAR_WEIGHT
            return weight_recorded * 2 + bar
        if self.weight_type == PLATE_LOADED_PER_SIDE:
            return weight_recorded * 2
        # dumbbell_each, total_weight, and bodyweight_fixed are all tracked as-is
        return weight_recorded


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False, index=True)
    date_confidence = Column(String, nullable=False, default=ESTIMATED)
    workout_type = Column(String, nullable=True, index=True)  # Push/Pull/Legs/Day4/other
    raw_note_text = Column(Text, nullable=True)
    note = Column(Text, nullable=True)  # free-text context, e.g. "California - No straps"
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    session_exercises = relationship(
        "SessionExercise", back_populates="session",
        order_by="SessionExercise.order_index",
        cascade="all, delete-orphan",
    )


class SessionExercise(Base):
    __tablename__ = "session_exercises"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    exercise_id = Column(Integer, ForeignKey("exercises.id"), nullable=False)
    order_index = Column(Integer, nullable=False)

    session = relationship("Session", back_populates="session_exercises")
    exercise = relationship("Exercise", back_populates="session_exercises")
    sets = relationship(
        "SetRecord", back_populates="session_exercise",
        order_by="SetRecord.set_number",
        cascade="all, delete-orphan",
    )


class SetRecord(Base):
    __tablename__ = "sets"

    id = Column(Integer, primary_key=True)
    session_exercise_id = Column(Integer, ForeignKey("session_exercises.id", ondelete="CASCADE"), nullable=False)
    set_number = Column(Integer, nullable=False)
    weight_recorded = Column(Float, nullable=False)  # as written in notes (plate/side, per-dumbbell, or stack)
    reps_full = Column(Integer, nullable=False)
    reps_partial = Column(Integer, nullable=True)
    raw_rep_string = Column(String, nullable=True)

    session_exercise = relationship("SessionExercise", back_populates="sets")


class SplitConfigEntry(Base):
    """Typical exercises for a given workout type, optionally scoped to a date range.
    Used to auto-tag a parsed session's workout_type by exercise overlap.
    """
    __tablename__ = "split_config"

    id = Column(Integer, primary_key=True)
    workout_type = Column(String, nullable=False)
    exercise_name = Column(String, nullable=False)
    start_date = Column(Date, nullable=True)  # null = open start
    end_date = Column(Date, nullable=True)  # null = open end

    __table_args__ = (
        UniqueConstraint("workout_type", "exercise_name", "start_date", "end_date", name="uq_split_config_entry"),
    )
