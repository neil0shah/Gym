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

This module is deliberately dependency-light (dataclasses only) so it can
be unit tested without spinning up the database or web app.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from dateutil import parser as dateutil_parser

from app.models import BARBELL_PLATE_PER_SIDE, DUMBBELL_EACH, TOTAL_WEIGHT, CONFIRMED, ESTIMATED

EXERCISE_LINE_RE = re.compile(
    r"^\s*(?P<name>[^:]+?)\s*:\s*(?P<weight>\d+(?:\.\d+)?)\s*:\s*(?P<reps>.+?)\s*$"
)
REP_TOKEN_RE = re.compile(r"^\s*(?P<full>\d+)\s*(?:\+\s*(?P<partial>\d+))?\s*$")

# Heuristics used only when an exercise hasn't been classified before.
BARBELL_KEYWORDS = [
    "bench press", "squat", "deadlift", "overhead press", "barbell row",
    "bent over row", "skull crusher", "front squat", "hip thrust",
]
DUMBBELL_KEYWORDS = ["dumbbell", "db "]


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
    sets: List[ParsedSet] = field(default_factory=list)


@dataclass
class ParsedSession:
    date: Optional[date]
    date_confidence: str
    workout_type_guess: Optional[str]
    raw_text: str
    exercises: List[ParsedExercise] = field(default_factory=list)


@dataclass
class ParseResult:
    sessions: List[ParsedSession]
    unrecognized_exercise_names: List[str]
    warnings: List[str]


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


def _classify_lines(lines: List[str]) -> Tuple[List[Tuple[str, str, str]], List[str]]:
    exercise_lines = []
    bad_lines = []
    for line in lines:
        m = EXERCISE_LINE_RE.match(line)
        if m:
            exercise_lines.append((m.group("name"), m.group("weight"), m.group("reps")))
        else:
            bad_lines.append(line)
    return exercise_lines, bad_lines


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


def parse_notes(
    raw_text: str,
    known_exercises: Dict[str, Tuple[str, str]],
    split_config: Optional[List[Tuple[str, set, Optional[date], Optional[date]]]] = None,
    today: Optional[date] = None,
) -> ParseResult:
    """Parse raw shorthand text into structured sessions.

    known_exercises: lowercased exercise name -> (canonical_name, weight_type)
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

    for block_lines in blocks:
        block_text = "\n".join(block_lines)
        exercise_tuples, bad_lines = _classify_lines(block_lines)

        if len(block_lines) == 1 and not exercise_tuples:
            parsed_date, fallback_year = _try_parse_date(block_lines[0], fallback_year)
            if parsed_date is not None:
                current_date = parsed_date
                blocks_since_date = 0
            else:
                warnings.append(f"Unrecognized line (not a date or exercise): {block_lines[0]!r}")
            continue

        for bl in bad_lines:
            warnings.append(f"Unrecognized line in workout block: {bl!r}")

        if not exercise_tuples:
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
        for idx, (raw_name, weight_str, reps_str) in enumerate(exercise_tuples):
            name = raw_name.strip()
            key = name.lower()
            exercise_names_lower.add(key)
            known = known_exercises.get(key)
            if known is not None:
                canonical_name, weight_type = known
                is_unrecognized = False
            else:
                canonical_name = name
                weight_type = _guess_weight_type(name)
                is_unrecognized = True
                if key not in unrecognized_seen:
                    unrecognized_seen.add(key)
                    unrecognized_order.append(name)

            weight = float(weight_str)
            sets: List[ParsedSet] = []
            for set_idx, rep_token in enumerate(reps_str.split(",")):
                m = REP_TOKEN_RE.match(rep_token)
                if not m:
                    warnings.append(
                        f"Could not parse rep token {rep_token!r} for {name!r} in block: {block_text!r}"
                    )
                    continue
                full = int(m.group("full"))
                partial = int(m.group("partial")) if m.group("partial") else None
                sets.append(ParsedSet(
                    set_number=set_idx + 1,
                    weight_recorded=weight,
                    reps_full=full,
                    reps_partial=partial,
                    raw_rep_string=rep_token.strip(),
                ))

            exercises.append(ParsedExercise(
                name=canonical_name,
                order_index=idx,
                weight_type_guess=weight_type,
                is_unrecognized=is_unrecognized,
                sets=sets,
            ))

        workout_type_guess = _guess_split(exercise_names_lower, session_date, split_config)

        sessions.append(ParsedSession(
            date=session_date,
            date_confidence=session_confidence,
            workout_type_guess=workout_type_guess,
            raw_text=block_text,
            exercises=exercises,
        ))

    return ParseResult(
        sessions=sessions,
        unrecognized_exercise_names=unrecognized_order,
        warnings=warnings,
    )
