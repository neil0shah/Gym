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

Real notes turned out to use several equivalent shapes for "exercise name"
and "weight" that this parser normalizes:
  - "name: weight: reps" (the common case) and "name weight: reps" (no
    colon between name and weight — just a habitual quirk for some
    exercises) both work; the split point is wherever a weight-shaped token
    starts, not a fixed colon position.
  - Within the comma-separated sets, a set can change the weight for
    itself and every set after it, using either a colon or a " - " dash:
    "175: 6, 160: 8" and "175 - 6, 160 - 8" both mean the same thing.
  - Weight tokens can be a plain number, "Plate" (one 45lb plate per
    side), "Plate+15" (45+15=60), "2 plates" (2*45=90), "2 plates+10"
    (2*45+10=100), or "Plate+2 10s" (45 + 2*10 = 65, two small 10lb
    plates). Bare sums like "35+25" (=60, no "Plate" keyword) work too.
  - A line can omit the weight field entirely for bodyweight movements:
    "Pull ups: 8, 7, 6" is tracked at a fixed assumed bodyweight (default
    160lb, overridable per exercise) since the notation never records a
    real weight for it.
  - A rep token can be "L7R6" (asymmetric single-arm/leg work: left did 7,
    right did 6) — recorded as the lower of the two, the limiting count.

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

# A line is "name" followed by "rest", split at the first point where the
# remainder starts looking like a weight token (a number, or a
# plate(s)/pl word possibly prefixed by a count). name must start with a
# letter or "(" so lines that merely happen to contain a number don't get
# misread as exercises (a running time, "1 mi run: 15:49"; a date-with-note,
# "2/18: First time taking creatine"; a rest-day tally, "2 sets of calf
# raises" — none of these are real exercise lines).
_WEIGHT_START = r"(?:\d|[Pp]lates?\b|\d+\s*[Pp]lates?\b|\d+\s*pl\b)"
# A stray parenthetical ("(no straps)", "(heavy handle)") sometimes sits
# between the name and the actual weight — allow and later strip it.
_OPTIONAL_ANNOTATION = r"(?:\([^)]*\)\s*:?\s*)?"
EXERCISE_LINE_RE = re.compile(
    rf"^\s*(?P<name>[A-Za-z(][^:]*?)\s*:?\s*(?P<rest>{_OPTIONAL_ANNOTATION}{_WEIGHT_START}.*)$"
)
_LEADING_ANNOTATION_RE = re.compile(r"^\([^)]*\)\s*:?\s*")
_TRAILING_ANNOTATION_RE = re.compile(r"\s*\([^)]*\)\s*$")

REP_TOKEN_RE = re.compile(r"^\s*(?P<full>\d+)\s*(?:\+\s*(?P<partial>\d+))?\s*$")
LR_REP_TOKEN_RE = re.compile(r"^\s*[Ll](?P<l>\d+)\s*[Rr](?P<r>\d+)\s*$")
NUMBER_TOKEN_RE = re.compile(r"^\d+(?:\.\d+)?$")
PLATE_WORD_RE = re.compile(r"^(?P<count>\d+)?\s*(?:plates?|pl)$", re.IGNORECASE)
COUNT_DENOM_RE = re.compile(r"^(?P<count>\d+)\s+(?P<denom>\d+(?:\.\d+)?)s?$", re.IGNORECASE)
# A second exercise glued onto the same line as the first, e.g.
# "Hammer: 60: 10 Bicep: 60: 8" or "...9 Cable Lat Raises: 17.5: 7, 15: 11" —
# detected as an uppercase-led name immediately followed by ": " + a weight,
# appearing after the start of the line's own data.
_EMBEDDED_EXERCISE_RE = re.compile(rf"(?P<name>[A-Z][A-Za-z ]{{1,30}}?)\s*:\s*(?={_WEIGHT_START})")

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
    name: str
    rest: str


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


def _looks_like_workout_line(rest: str) -> bool:
    """A structural regex match on "rest" isn't enough on its own — "Day 4:"
    or "Barbell squat practice w 10s on each sode" also match EXERCISE_LINE_RE
    but carry no real rep data. Require at least one token to yield an actual
    rep count before treating the line as a real exercise (vs. a header/note).
    """
    split = _split_embedded_exercise(rest)
    rest = split[0] if split else rest
    for raw_token in rest.split(","):
        token = raw_token.strip()
        if not token:
            continue
        reps_part = token
        if ":" in token:
            _, _, reps_part = token.partition(":")
        elif " - " in token:
            _, _, reps_part = token.partition(" - ")
        reps_part = _TRAILING_ANNOTATION_RE.sub("", reps_part).strip()
        if _parse_rep_token(reps_part) is not None:
            return True
    return False


def _classify_line(line: str) -> Optional[_RawLine]:
    m = EXERCISE_LINE_RE.match(line)
    if not m:
        return None
    rest = _LEADING_ANNOTATION_RE.sub("", m.group("rest"))
    if not _looks_like_workout_line(rest):
        return None
    return _RawLine(m.group("name").strip(), rest)


def _split_embedded_exercise(rest: str) -> Optional[Tuple[str, str, str]]:
    """A second exercise sometimes gets glued onto the same line as the
    first (a fast-typed superset): "Hammer: 60: 10 Bicep: 60: 8" or
    "...20: 9 Cable Lat Raises: 17.5: 7, 15: 11". Detect an embedded
    "Name: <weight>" starting after the line's own data and split there.
    """
    for m in _EMBEDDED_EXERCISE_RE.finditer(rest):
        if m.start() == 0:
            continue
        name = m.group("name").strip()
        if PLATE_WORD_RE.match(name):
            continue  # "2 Plates: 6" — "Plates" is a weight unit, not a name
        before = rest[:m.start()]
        return before.rstrip(", "), name, rest[m.end():]
    return None


_DATE_LIKE_RE = re.compile(r"\d+\s*[/-]\s*\d+")
_MONTH_NAME_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.IGNORECASE
)


def _try_parse_date(line: str, fallback_year: int) -> Tuple[Optional[date], int]:
    stripped = line.strip().rstrip(":").strip()
    if not any(ch.isdigit() for ch in stripped):
        return None, fallback_year
    default_dt = datetime(fallback_year, 1, 1)
    # parsed.year already equals fallback_year whenever the string itself
    # doesn't specify one (that's what `default` is for), and reflects the
    # real year (2-digit or 4-digit) whenever it does — so it's always safe
    # to carry forward as the new fallback, keeping later year-less dates
    # (e.g. "1/26" after "1/16/24") rolling forward correctly.
    try:
        parsed = dateutil_parser.parse(stripped, fuzzy=False, default=default_dt)
        return parsed.date(), parsed.year
    except (ValueError, OverflowError):
        pass
    # Fuzzy parsing can misread a bare number in free-text ("2 WEEK BREAK")
    # as a day-of-month, so only attempt it when there's an actual date-like
    # signal: digits separated by "/" or "-", or a month name.
    if _DATE_LIKE_RE.search(stripped) or _MONTH_NAME_RE.search(stripped):
        try:
            parsed = dateutil_parser.parse(stripped, fuzzy=True, default=default_dt)
            return parsed.date(), parsed.year
        except (ValueError, OverflowError):
            pass
    return None, fallback_year


def parse_weight_token(token: str) -> Optional[float]:
    """Parse a weight token: a plain number ("175"), "Plate" (=45), "Plate+15"
    (=60), "2 plates" (=90), "2 plates+10" (=100), "Plate+2 10s" (=65, two
    10lb plates), or a bare sum like "35+25" (=60, no "Plate" keyword needed).
    """
    token = token.strip()
    if not token:
        return None
    total = 0.0
    for part in token.split("+"):
        part = part.strip()
        if not part:
            return None
        if NUMBER_TOKEN_RE.match(part):
            total += float(part)
            continue
        m = PLATE_WORD_RE.match(part)
        if m:
            count = float(m.group("count")) if m.group("count") else 1.0
            total += count * DEFAULT_BAR_WEIGHT
            continue
        m = COUNT_DENOM_RE.match(part)
        if m:
            total += float(m.group("count")) * float(m.group("denom"))
            continue
        return None
    return total


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


def _parse_rep_token(token: str) -> Optional[Tuple[int, Optional[int]]]:
    """Returns (reps_full, reps_partial) for a plain "8", partial "6+1", or
    asymmetric "L7R6" (recorded as the lower/limiting side) rep token.
    """
    m = REP_TOKEN_RE.match(token)
    if m:
        full = int(m.group("full"))
        partial = int(m.group("partial")) if m.group("partial") else None
        return full, partial
    m = LR_REP_TOKEN_RE.match(token)
    if m:
        return min(int(m.group("l")), int(m.group("r"))), None
    return None


def _build_exercises(
    raw: _RawLine,
    start_idx: int,
    known_exercises: KnownExercises,
    unrecognized_seen: set,
    unrecognized_order: List[str],
    warnings: List[str],
    block_text: str,
) -> List[ParsedExercise]:
    """Build one or more ParsedExercise from a single source line — usually
    one, but a fast-typed superset can glue a second exercise onto the same
    line (see _split_embedded_exercise), producing two.
    """
    results: List[ParsedExercise] = []
    name, rest = raw.name, raw.rest
    idx = start_idx
    while True:
        split = _split_embedded_exercise(rest)
        this_rest = split[0] if split else rest
        built = _build_single_exercise(
            name, this_rest, idx, known_exercises, unrecognized_seen, unrecognized_order, warnings, block_text,
        )
        if built is not None:
            results.append(built)
            idx += 1
        if split is None:
            break
        _, name, rest = split
    return results


def _build_single_exercise(
    name: str,
    rest: str,
    idx: int,
    known_exercises: KnownExercises,
    unrecognized_seen: set,
    unrecognized_order: List[str],
    warnings: List[str],
    block_text: str,
) -> Optional[ParsedExercise]:
    key = name.lower()
    known = known_exercises.get(key)

    # Pass 1: split each comma-separated token into (weight_or_None, reps).
    raw_sets: List[Tuple[Optional[float], int, Optional[int], str]] = []
    for raw_token in rest.split(","):
        token = raw_token.strip()
        if not token:
            continue

        weight_part, reps_part = None, token
        if ":" in token:
            weight_part, _, reps_part = token.partition(":")
        elif " - " in token:
            weight_part, _, reps_part = token.partition(" - ")

        weight_val = None
        if weight_part is not None:
            weight_part = _TRAILING_ANNOTATION_RE.sub("", weight_part).strip()
            weight_val = parse_weight_token(weight_part)
            if weight_val is None:
                warnings.append(
                    f"Could not parse weight {weight_part!r} for {name!r} in block: {block_text!r}"
                )
                continue

        reps_part = _TRAILING_ANNOTATION_RE.sub("", reps_part).strip()
        parsed_reps = _parse_rep_token(reps_part)
        if parsed_reps is None:
            warnings.append(
                f"Could not parse rep token {reps_part!r} for {name!r} in block: {block_text!r}"
            )
            continue
        full, partial = parsed_reps
        raw_sets.append((weight_val, full, partial, reps_part))

    if not raw_sets:
        return None

    is_bodyweight = all(w is None for w, *_ in raw_sets)

    if is_bodyweight:
        is_unrecognized = known is None
        if known is not None:
            canonical_name, weight_type, known_bar_weight = known
            assumed = known_bar_weight if known_bar_weight is not None else DEFAULT_BODYWEIGHT_ESTIMATE
        else:
            canonical_name = name
            assumed = DEFAULT_BODYWEIGHT_ESTIMATE
            _track_unrecognized(key, name, unrecognized_seen, unrecognized_order)
        weight_type = BODYWEIGHT_FIXED
        sets = [
            ParsedSet(set_number=i + 1, weight_recorded=assumed, reps_full=f, reps_partial=p, raw_rep_string=raw_str)
            for i, (_, f, p, raw_str) in enumerate(raw_sets)
        ]
        bar_weight_guess = assumed
    else:
        is_unrecognized = known is None
        if known is not None:
            canonical_name, weight_type, known_bar_weight = known
        else:
            canonical_name = name
            weight_type = _guess_weight_type(name)
            known_bar_weight = None
            _track_unrecognized(key, name, unrecognized_seen, unrecognized_order)

        # Backfill any leading sets that came before the first weight-bearing
        # token (rare, but possible if the first token in the line was a
        # bare rep count) with that first established weight.
        first_weight = next((w for w, *_ in raw_sets if w is not None), None)
        sets = []
        current_weight = first_weight
        for w, f, p, raw_str in raw_sets:
            if w is not None:
                current_weight = w
            sets.append(ParsedSet(
                set_number=len(sets) + 1, weight_recorded=current_weight,
                reps_full=f, reps_partial=p, raw_rep_string=raw_str,
            ))

        bar_weight_guess = None
        if weight_type == BARBELL_PLATE_PER_SIDE:
            bar_weight_guess = known_bar_weight if known_bar_weight is not None else DEFAULT_BAR_WEIGHT

    return ParsedExercise(
        name=canonical_name,
        order_index=idx,
        weight_type_guess=weight_type,
        is_unrecognized=is_unrecognized,
        bar_weight_guess=bar_weight_guess,
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
        for raw in raw_lines:
            built_list = _build_exercises(
                raw, len(exercises), known_exercises, unrecognized_seen, unrecognized_order, warnings, block_text,
            )
            for built in built_list:
                exercise_names_lower.add(built.name.lower())
            exercises.extend(built_list)

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
