"""Parse BMS map source (DFHMSD / DFHMDI / DFHMDF assembler macros)."""

from __future__ import annotations

from dataclasses import dataclass, field

from harness.cobol.source import Note
from harness.inventory.jcl import split_operands

ASSEMBLER_INSTRUCTIONS = {"PRINT", "TITLE", "SPACE", "EJECT", "END", "COPY", "PUNCH"}


@dataclass
class BmsField:
    name: str | None
    line: int
    pos: str | None
    length: int | None
    attrb: str | None
    initial: str | None


@dataclass
class BmsMap:
    name: str
    line: int
    size: str | None
    fields: list[BmsField] = field(default_factory=list)


@dataclass
class BmsMapset:
    name: str
    line: int
    params: dict
    maps: list[BmsMap] = field(default_factory=list)


@dataclass
class BmsFile:
    mapsets: list[BmsMapset] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)


@dataclass
class _Stmt:
    line: int
    label: str | None
    op: str
    operands: str


def _statements(lines: list[str], notes: list[Note]) -> list[_Stmt]:
    out: list[_Stmt] = []
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i].rstrip("\r\n")
        number = i + 1
        i += 1
        if not raw.strip() or raw.startswith("*") or raw.startswith(".*"):
            continue
        label, op, operands, continued = _split_line(raw, number, notes)
        if op is None:
            continue
        while (continued or operands.endswith(",")) and i < n:
            nxt = lines[i].rstrip("\r\n")
            if not nxt.strip() or nxt.startswith("*") or not nxt.startswith(" "):
                notes.append(Note("missing_continuation_line", number))
                break
            if not continued:
                notes.append(Note("nonstandard_continuation", number))
            i += 1
            more, continued = _continuation_text(nxt, i, notes)
            operands += more
        if label and label.upper() in ASSEMBLER_INSTRUCTIONS:
            notes.append(Note("assembler_instruction_in_label_column", number, {"instruction": label.upper()}))
            continue
        out.append(_Stmt(number, label.upper() if label else None, op, operands))
    return out


def _balanced(text: str) -> bool:
    depth = 0
    quote = False
    for ch in text:
        if quote:
            quote = ch != "'"
        elif ch == "'":
            quote = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
    return depth == 0 and not quote


def _statement_text(raw: str, number: int, notes: list[Note]) -> tuple[str, bool]:
    """Columns 1-71 plus the column-72 continuation flag.

    Operands that spill past column 71 are kept (and reported) when cutting
    them would leave unbalanced parentheses or quotes.
    """
    cut = raw[:71]
    full = raw.rstrip()
    if len(full) > 71 and not _balanced(_operand_part(cut.strip())) and _balanced(_operand_part(full.strip())):
        notes.append(Note("operands_beyond_column_71", number))
        return full, False
    return cut, len(raw) > 71 and raw[71] != " "


def _split_line(raw: str, number: int, notes: list[Note]) -> tuple[str | None, str | None, str, bool]:
    text, continued = _statement_text(raw, number, notes)
    label = None
    if not text.startswith(" "):
        label, _, text = text.partition(" ")
    parts = text.strip().split(None, 1)
    if not parts:
        return label, None, "", False
    operands = _operand_part(parts[1]) if len(parts) > 1 else ""
    return label, parts[0].upper(), operands, continued


def _continuation_text(raw: str, number: int, notes: list[Note]) -> tuple[str, bool]:
    text, continued = _statement_text(raw, number, notes)
    return _operand_part(text.strip()), continued


def _operand_part(text: str) -> str:
    """Drop the trailing comment and any continuation marker past the operands."""
    depth = 0
    quote = False
    for k, ch in enumerate(text):
        if quote:
            if ch == "'":
                quote = False
            continue
        if ch == "'":
            quote = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == " " and depth == 0:
            return text[:k]
    return text


def parse_bms(lines: list[str]) -> BmsFile:
    result = BmsFile()
    mapset: BmsMapset | None = None
    current: BmsMap | None = None
    for st in _statements(lines, result.notes):
        positional, keywords = split_operands(st.operands)
        if st.op == "DFHMSD":
            if keywords.get("TYPE", "").upper() == "FINAL" or (positional and positional[0].upper() == "TYPE=FINAL"):
                mapset = None
                current = None
                continue
            mapset = BmsMapset(st.label or "", st.line, keywords)
            result.mapsets.append(mapset)
        elif st.op == "DFHMDI":
            if mapset is None:
                result.notes.append(Note("map_outside_mapset", st.line))
                mapset = BmsMapset("", st.line, {})
                result.mapsets.append(mapset)
            current = BmsMap(st.label or "", st.line, keywords.get("SIZE"))
            mapset.maps.append(current)
        elif st.op == "DFHMDF":
            if current is None:
                result.notes.append(Note("field_outside_map", st.line))
                continue
            length = keywords.get("LENGTH")
            initial = keywords.get("INITIAL")
            current.fields.append(BmsField(
                st.label, st.line, keywords.get("POS"),
                int(length) if length and length.isdigit() else None,
                keywords.get("ATTRB"),
                initial.strip("'") if initial else None,
            ))
    return result
