"""Turn physical COBOL lines into program text, the way the compiler sees it.

Fixed reference format: columns 1-6 sequence area, column 7 indicator,
columns 8-72 program text, 73-80 ignored. Free format has no columns.
Real exports are messy, so the rules here are lenient and every leniency
is reported as a note the caller turns into an issue.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FIXED = "fixed"
FREE = "free"


@dataclass(frozen=True)
class CodeLine:
    number: int          # 1-based physical line number
    text: str            # program text with comments removed
    continuation: bool   # fixed-format "-" indicator
    debug: bool          # fixed-format "D" indicator
    start_col: int = 1   # physical column of text[0]


@dataclass
class Note:
    code: str
    line: int
    details: dict = field(default_factory=dict)


@dataclass
class CobolSource:
    fmt: str
    code: list[CodeLine]
    notes: list[Note]


def detect_format(lines: list[str]) -> str:
    for line in lines[:200]:
        upper = line.upper()
        if ">>SOURCE" in upper or "SOURCE FORMAT" in upper:
            if "FREE" in upper:
                return FREE
            if "FIX" in upper:
                return FIXED

    free_votes = 0
    fixed_votes = 0
    for line in lines:
        if not line.strip():
            continue
        first = len(line) - len(line.lstrip(" "))
        if first >= 6:
            fixed_votes += 1
            continue
        seq = line[:6]
        if len(line) > 6 and line[6] in " *-/dD" and (not seq.strip() or seq.strip().isdigit()):
            fixed_votes += 1
            continue
        if line[first] == "*":
            free_votes += 1
        elif len(line) > 6 and " " not in line[first:7]:
            # a word starts in the sequence area and runs into column 7
            free_votes += 1
    if free_votes > max(2, fixed_votes // 4):
        return FREE
    return FIXED


def strip_floating_comment(text: str) -> str:
    """Remove a ``*>`` comment that is not inside a literal."""
    quote = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "*" and text.startswith("*>", i):
            return text[:i]
        i += 1
    return text


def _open_quote(text: str) -> str | None:
    quote = None
    for ch in text:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
    return quote


def normalize(lines: list[str]) -> CobolSource:
    fmt = detect_format(lines)
    code: list[CodeLine] = []
    notes: list[Note] = []

    for number, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        if fmt == FREE:
            stripped = raw.lstrip()
            if stripped.startswith("*>") or stripped.startswith(">>"):
                continue
            if stripped.startswith("*"):
                notes.append(Note("nonstandard_comment", number, {"column": len(raw) - len(stripped) + 1}))
                continue
            code.append(CodeLine(number, strip_floating_comment(raw), False, False))
            continue

        indicator = raw[6] if len(raw) > 6 else " "
        area = raw[7:72]
        start_col = 8
        if indicator in "*/":
            continue
        first = len(raw) - len(raw.lstrip())
        if first < 6 and raw[first] == "*":
            # comment marker typed in the sequence area
            notes.append(Note("nonstandard_comment", number, {"column": first + 1}))
            continue
        if indicator not in " -dD":
            # Code that starts in column 7 is a misalignment, not a comment.
            notes.append(Note("code_in_indicator_column", number, {"character": indicator}))
            area = raw[6:72]
            indicator = " "
            start_col = 7
        stripped = area.lstrip()
        if stripped.startswith(">>"):
            continue
        if indicator == " " and stripped.startswith("*") and not stripped.startswith("*>"):
            notes.append(Note("nonstandard_comment", number, {"column": 8 + len(area) - len(stripped)}))
            continue
        if len(raw) > 72 and raw[72:].strip():
            quote = _open_quote(area)
            if quote and quote in raw[72:]:
                notes.append(Note("literal_truncated_at_column_72", number))
        code.append(CodeLine(number, strip_floating_comment(area), indicator == "-", indicator in "dD", start_col))

    return CobolSource(fmt, code, notes)
