"""Parser for the Notes-app shorthand workout format.

Format recap (see project brief):
    <date line>
    <blank line>
    <exercise>: <weight>: <rep1>, <rep2>, ...
    <exercise>: <weight>: <rep1>, <rep2>, ...
    <blank line>
    <exercise>: <weight>: <rep1>, <rep2>, ...
    ...

Only the workout block immediately following a date line has a confirmed
date. Later undated blocks in the same run are assumed to be on subsequent
days and are marked as "estimated" so the review UI can surface them for
correction.

A block's first line can also be a free-text note ("California - No straps")
rather than a date, whether it sits in its own blank-line-separated block
(like a date line) or is glued directly above the workout lines with no
blank line at all. Either way it's attached to the session built from the
lines that follow it.

Within a workout line, a set can override the weight for itself and every
set after it in the same line: "Underhand rows: 175: 6, 160: 8" is a 175lb
set of 6 followed by a 160lb set of 8. Weight tokens can also be written as
"Plate" (a 45lb plate per side) or "Plate+15" (45+15=60 per side).

A line can also omit the weight field entirely for bodyweight movements:
"Pull ups: 8, 7, 6" is tracked at a fixed assumed bodyweight (default 160lb,
overridable per exercise) since the notation never records real weight.

This module is deliberately dependency-light (dataclasses only) so it can
be unit tested without spinning up the database or web app.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from dateutil import parser as dateutil_parser

from app.models import (
    BARBELL_PLATE_PER_SIDE, DUMBBELL_EACH, TOTAL_WEIGHT,
    PLATE_LOADED_PER_SIDE, BODYWEIGHT_FIXED,
    DEFAULT_BAR_WEIGHT, DEFAULT_BODYWEIGHT_ESTIMATE,
    CONFIRMED, ESTIMATED,
)

# name : weight : sets  (sets may itself contain further "weight: reps" overrides)
EXERCISE_LINE_RE = re.compile(
    r"^\s*(?P<name>[^:]+?)\s*:\s*(?P<weight>[^:]+?)\s*:\s*(?P<sets>.+?)\s*$"
)
# name : reps  (bodyweight movements with no weight field at all)
BODYWEIGHT_LINE_RE = re.compile(
    r"^\s*(?P<name>[^:]+?)\s*:\s*(?P<reps>[^:]+?)\s*$"
)
REP_TOKEN_RE = re.compile(r"^\s*(?P<full>\d+)\s*(?:\+\s*(?P<partial>\d+))?\s*$")
PLATE_TOKEN_RE = re.compile(r"^plate\s*(?:\+\s*(?P<extra>\d+(?:\.\d+)?))?$", re.IGNORECASE)
NUMBER_TOKEN_RE = re.compile(r"^\d+(?:\.\d+)?$")

# Heuristics used only when an exercise hasn't been classified before.
BARBELL_KEYWORDS = [
    "bench press", "squat", "deadlift", "overhead press", "barbell row",
    "bent over row", "skull crusher", "front squat", "hip thrust", "rdl",
]
DUMBBELL_KEYWORDS = ["dumbbell", "db "]

# lowercased exercise name -> (canonical_name, weight_type, bar_weight)
KnownExercises = Dict[str, Tuple[str, str, Optional[float]]]


@dataclass
class ParsedSet:
    set_number: int
    weight_recorded: float
    reps_full: int
    reps_partial: Optional[int]
    raw_rep_string: str


@dataclass
class ParsedExercise:
    name: str
    order_index: int
    weight_type_guess: str
    is_unrecognized: bool
    bar_weight_guess: Optional[float] = None
    sets: List[ParsedSet] = field(default_factory=list)


@dataclass
class ParsedSession:
    date: Optional[date]
    date_confidence: str
    workout_type_guess: Optional[str]
    raw_text: str
    note: Optional[str] = None
    exercises: List[ParsedExercise] = field(default_factory=list)


@dataclass
class ParseResult:
    sessions: List[ParsedSession]
    unrecognized_exercise_names: List[str]
    warnings: List[str]


@dataclass
class _RawLine:
    kind: str  # "weighted" or "bodyweight"
    name: str
    weight_token: Optional[str]  # None for bodyweight
    sets_str: str


def _split_blocks(raw_text: str) -> List[List[str]]:
    blocks: List[List[str]] = []
    current: List[str] = []
    for line in raw_text.splitlines():
        if line.strip() == "":
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _classify_line(line: str) -> Optional[_RawLine]:
    m = EXERCISE_LINE_RE.match(line)
    if m:
        return _RawLine("weighted", m.group("name"), m.group("weight"), m.group("sets"))
    m = BODYWEIGHT_LINE_RE.match(line)
    if m:
        return _RawLine("bodyweight", m.group("name"), None, m.group("reps"))
    return None


_YEAR_TOKEN_RE = re.compile(r"\b(19|20)\d{2}\b")


def _try_parse_date(line: str, fallback_year: int) -> Tuple[Optional[date], int]:
    stripped = line.strip().rstrip(":").strip()
    if not any(ch.isdigit() for ch in stripped):
        return None, fallback_year
    has_year = bool(_YEAR_TOKEN_RE.search(stripped))
    default_dt = datetime(fallback_year, 1, 1)
    for fuzzy in (False, True):
        try:
            parsed = dateutil_parser.parse(stripped, fuzzy=fuzzy, default=default_dt)
            new_year = parsed.year if has_year else fallback_year
            return parsed.date(), new_year
        except (ValueError, OverflowError):
            continue
    return None, fallback_year


def parse_weight_token(token: str) -> Optional[float]:
    """Parse a weight token: a plain number, "Plate" (=45), or "Plate+15" (=45+15)."""
    token = token.strip()
    if NUMBER_TOKEN_RE.match(token):
        return float(token)
    m = PLATE_TOKEN_RE.match(token)
    if m:
        extra = float(m.group("extra")) if m.group("extra") else 0.0
        return DEFAULT_BAR_WEIGHT + extra
    return None


def _guess_weight_type(name: str) -> str:
    lname = name.lower()
    if any(k in lname for k in DUMBBELL_KEYWORDS):
        return DUMBBELL_EACH
    if any(k in lname for k in BARBELL_KEYWORDS):
        return BARBELL_PLATE_PER_SIDE
    return TOTAL_WEIGHT


def _guess_split(
    exercise_names_lower: set,
    session_date: Optional[date],
    split_config: List[Tuple[str, set, Optional[date], Optional[date]]],
) -> Optional[str]:
    best_type = None
    best_overlap = 0
    for workout_type, config_names_lower, start_date, end_date in split_config:
        if session_date is not None:
            if start_date is not None and session_date < start_date:
                continue
            if end_date is not None and session_date >= end_date:
                continue
        overlap = len(exercise_names_lower & config_names_lower)
        if overlap > best_overlap:
            best_overlap = overlap
            best_type = workout_type
    return best_type if best_overlap > 0 else None


def _track_unrecognized(key: str, name: str, unrecognized_seen: set, unrecognized_order: List[str]) -> None:
    if key not in unrecognized_seen:
        unrecognized_seen.add(key)
        unrecognized_order.append(name)


def _build_weighted_exercise(
    raw: _RawLine,
    idx: int,
    known_exercises: KnownExercises,
    unrecognized_seen: set,
    unrecognized_order: List[str],
    warnings: List[str],
    block_text: str,
) -> Optional[ParsedExercise]:
    name = raw.name.strip()
    key = name.lower()
    known = known_exercises.get(key)
    is_unrecognized = known is None
    if known is not None:
        canonical_name, weight_type, _known_bar_weight = known
    else:
        canonical_name = name
        weight_type = _guess_weight_type(name)
        _track_unrecognized(key, name, unrecognized_seen, unrecognized_order)

    current_weight = parse_weight_token(raw.weight_token)
    if current_weight is None:
        warnings.append(
            f"Could not parse weight {raw.weight_token!r} for {name!r} in block: {block_text!r}"
        )
        return None

    bar_weight_guess = None
    if weight_type == BARBELL_PLATE_PER_SIDE:
        bar_weight_guess = _known_bar_weight if known is not None and _known_bar_weight is not None else DEFAULT_BAR_WEIGHT

    sets: List[ParsedSet] = []
    for set_idx, raw_token in enumerate(raw.sets_str.split(",")):
        token = raw_token.strip()
        if ":" in token:
            weight_part, _, reps_part = token.partition(":")
            new_weight = parse_weight_token(weight_part)
            if new_weight is None:
                warnings.append(
                    f"Could not parse weight override {weight_part!r} for {name!r} in block: {block_text!r}"
                )
                continue
            current_weight = new_weight
            reps_part = reps_part.strip()
        else:
            reps_part = token

        m = REP_TOKEN_RE.match(reps_part)
        if not m:
            warnings.append(
                f"Could not parse rep token {reps_part!r} for {name!r} in block: {block_text!r}"
            )
            continue
        full = int(m.group("full"))
        partial = int(m.group("partial")) if m.group("partial") else None
        sets.append(ParsedSet(
            set_number=set_idx + 1,
            weight_recorded=current_weight,
            reps_full=full,
            reps_partial=partial,
            raw_rep_string=token,
        ))

    if not sets:
        return None

    return ParsedExercise(
        name=canonical_name,
        order_index=idx,
        weight_type_guess=weight_type,
        is_unrecognized=is_unrecognized,
        bar_weight_guess=bar_weight_guess,
        sets=sets,
    )


def _build_bodyweight_exercise(
    raw: _RawLine,
    idx: int,
    known_exercises: KnownExercises,
    unrecognized_seen: set,
    unrecognized_order: List[str],
    warnings: List[str],
    block_text: str,
) -> Optional[ParsedExercise]:
    name = raw.name.strip()
    key = name.lower()
    known = known_exercises.get(key)
    is_unrecognized = known is None
    if known is not None:
        canonical_name, weight_type, known_bar_weight = known
        assumed_weight = known_bar_weight if known_bar_weight is not None else DEFAULT_BODYWEIGHT_ESTIMATE
    else:
        canonical_name = name
        weight_type = BODYWEIGHT_FIXED
        assumed_weight = DEFAULT_BODYWEIGHT_ESTIMATE
        _track_unrecognized(key, name, unrecognized_seen, unrecognized_order)

    sets: List[ParsedSet] = []
    for set_idx, raw_token in enumerate(raw.sets_str.split(",")):
        token = raw_token.strip()
        m = REP_TOKEN_RE.match(token)
        if not m:
            warnings.append(
                f"Could not parse rep token {token!r} for {name!r} in block: {block_text!r}"
            )
            continue
        full = int(m.group("full"))
        partial = int(m.group("partial")) if m.group("partial") else None
        sets.append(ParsedSet(
            set_number=set_idx + 1,
            weight_recorded=assumed_weight,
            reps_full=full,
            reps_partial=partial,
            raw_rep_string=token,
        ))

    if not sets:
        return None

    return ParsedExercise(
        name=canonical_name,
        order_index=idx,
        weight_type_guess=weight_type,
        is_unrecognized=is_unrecognized,
        bar_weight_guess=assumed_weight,
        sets=sets,
    )


def parse_notes(
    raw_text: str,
    known_exercises: KnownExercises,
    split_config: Optional[List[Tuple[str, set, Optional[date], Optional[date]]]] = None,
    today: Optional[date] = None,
) -> ParseResult:
    """Parse raw shorthand text into structured sessions.

    known_exercises: lowercased exercise name -> (canonical_name, weight_type, bar_weight)
    split_config: list of (workout_type, {lowercased exercise names}, start_date, end_date)
    """
    split_config = split_config or []
    blocks = _split_blocks(raw_text)

    sessions: List[ParsedSession] = []
    warnings: List[str] = []
    unrecognized_seen = set()
    unrecognized_order: List[str] = []

    fallback_year = (today or date.today()).year
    current_date: Optional[date] = None
    blocks_since_date = 0
    pending_note: Optional[str] = None

    for block_lines in blocks:
        block_text = "\n".join(block_lines)
        line_results = [_classify_line(line) for line in block_lines]

        # A block's first line that doesn't parse as an exercise, followed only
        # by lines that do, is a header (date or free-text note) for the
        # workout that follows it — whether that workout is in this same block
        # (glued, no blank line) or, when the header is the whole block, in the
        # next block (the classic blank-line-separated date-line convention).
        if line_results and line_results[0] is None and all(r is not None for r in line_results[1:]):
            header_line = block_lines[0]
            raw_lines = line_results[1:]
            parsed_date, fallback_year = _try_parse_date(header_line, fallback_year)
            if parsed_date is not None:
                current_date = parsed_date
                blocks_since_date = 0
            else:
                pending_note = header_line.strip().rstrip(":").strip()
            if not raw_lines:
                continue
        else:
            raw_lines = line_results
            for line, result in zip(block_lines, line_results):
                if result is None:
                    warnings.append(f"Unrecognized line in workout block: {line!r}")

        raw_lines = [r for r in raw_lines if r is not None]
        if not raw_lines:
            continue

        if current_date is None:
            session_date = None
            session_confidence = ESTIMATED
        elif blocks_since_date == 0:
            session_date = current_date
            session_confidence = CONFIRMED
        else:
            session_date = current_date + timedelta(days=blocks_since_date)
            session_confidence = ESTIMATED
        blocks_since_date += 1

        exercises: List[ParsedExercise] = []
        exercise_names_lower = set()
        for idx, raw in enumerate(raw_lines):
            exercise_names_lower.add(raw.name.strip().lower())
            if raw.kind == "weighted":
                built = _build_weighted_exercise(
                    raw, idx, known_exercises, unrecognized_seen, unrecognized_order, warnings, block_text,
                )
            else:
                built = _build_bodyweight_exercise(
                    raw, idx, known_exercises, unrecognized_seen, unrecognized_order, warnings, block_text,
                )
            if built is not None:
                exercises.append(built)

        workout_type_guess = _guess_split(exercise_names_lower, session_date, split_config)

        sessions.append(ParsedSession(
            date=session_date,
            date_confidence=session_confidence,
            workout_type_guess=workout_type_guess,
            raw_text=block_text,
            note=pending_note,
            exercises=exercises,
        ))
        pending_note = None

    return ParseResult(
        sessions=sessions,
        unrecognized_exercise_names=unrecognized_order,
        warnings=warnings,
    )
