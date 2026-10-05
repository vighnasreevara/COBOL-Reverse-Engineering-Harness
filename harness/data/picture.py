"""PICTURE strings and storage sizes (IBM Enterprise COBOL rules)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_REPEAT = re.compile(r"(.)\((\d+)\)")
_NUMERIC = set("9VP")
_NUMERIC_EDIT = set("9VPZ*+-$,.B0/@E")     # @ marks CR / DB
_ALNUM_EDIT = set("XA9B0/")


@dataclass(frozen=True)
class Picture:
    raw: str
    category: str        # numeric, numeric-edited, alphanumeric, alphanumeric-edited, alphabetic, national, dbcs, unknown
    positions: int       # character positions (display length)
    digits: int          # 9s (numeric digits stored)
    scale: int           # digits to the right of the implied decimal point (negative for P scaling)
    signed: bool


def expand(pic: str) -> str:
    return _REPEAT.sub(lambda m: m.group(1) * int(m.group(2)), pic.upper())


def analyze(pic: str) -> Picture:
    text = expand(pic)
    signed = text.startswith("S")
    body = text[1:] if signed else text
    body = body.replace("CR", "@@").replace("DB", "@@")
    chars = set(body)
    positions = sum(1 for c in body if c not in "VP")
    digits = body.count("9")
    if "V" in body:
        left, _, right = body.partition("V")
        scale = right.count("9") + right.count("P")
    elif body.endswith("P"):
        scale = -len(body) + len(body.rstrip("P"))
    elif body.startswith("P"):
        scale = digits + (len(body) - len(body.lstrip("P")))
    else:
        scale = 0
    if not body:
        category = "unknown"
    elif chars <= _NUMERIC:
        category = "numeric"
    elif chars <= {"A"}:
        category = "alphabetic"
    elif chars <= {"N"}:
        category = "national"
    elif chars <= {"G", "B"}:
        category = "dbcs"
    elif "X" in chars and chars <= {"X", "A", "9"}:
        category = "alphanumeric"
    elif chars <= {"A", "9"}:
        category = "alphanumeric"
    elif chars <= _ALNUM_EDIT and chars & {"X", "A"}:
        category = "alphanumeric-edited"
    elif chars <= _NUMERIC_EDIT:
        category = "numeric-edited"
    else:
        category = "unknown"
    return Picture(pic, category, positions, digits, scale, signed)


def binary_bytes(digits: int) -> int:
    if digits <= 4:
        return 2
    if digits <= 9:
        return 4
    return 8


FIXED_SIZE = {"COMP-1": 4, "COMP-2": 8, "INDEX": 4, "POINTER": 4, "PROCEDURE-POINTER": 8, "FUNCTION-POINTER": 4}


def storage_bytes(pic: Picture | None, usage: str, sign_separate: bool = False) -> int | None:
    """Bytes occupied by an elementary item, or None when it cannot be determined."""
    if usage in FIXED_SIZE:
        return FIXED_SIZE[usage]
    if pic is None or pic.category == "unknown":
        return None
    if usage in ("COMP", "COMP-5"):
        return binary_bytes(max(pic.digits, 1))
    if usage == "COMP-3":
        return pic.digits // 2 + 1
    if usage in ("NATIONAL", "DISPLAY-1") or pic.category in ("national", "dbcs"):
        return pic.positions * 2
    return pic.positions + (1 if sign_separate and pic.signed else 0)
