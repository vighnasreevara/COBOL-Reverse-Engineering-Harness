"""Parse DATA DIVISION entries (syntax only; sizes and offsets are the Data stage's job)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harness.cobol.tokens import LIT, PIC, PUNCT, WORD, Token, render
from harness.cobol.words import FIGURATIVE, USAGE_WORDS, is_user_word

SECTIONS = {"FILE", "WORKING-STORAGE", "LOCAL-STORAGE", "LINKAGE", "COMMUNICATION", "REPORT", "SCREEN"}
CLAUSE_WORDS = {
    "PIC", "PICTURE", "USAGE", "VALUE", "VALUES", "OCCURS", "REDEFINES", "SIGN", "SYNC",
    "SYNCHRONIZED", "JUST", "JUSTIFIED", "BLANK", "EXTERNAL", "GLOBAL", "RENAMES", "IS",
    "GROUP-USAGE", "VOLATILE", "LEADING", "TRAILING", "DATE", "BASED", "DYNAMIC",
} | set(USAGE_WORDS)


@dataclass
class DataEntry:
    level: int
    name: str | None          # None for FILLER / unnamed
    line: int
    file: str
    section: str | None
    fd: str | None            # FD/SD the record belongs to
    clauses: dict[str, Any] = field(default_factory=dict)
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "name": self.name, "line": self.line, "file": self.file,
                "section": self.section, "fd": self.fd, "clauses": self.clauses, "text": self.text}


@dataclass
class FileDescription:
    kind: str                 # FD or SD
    name: str
    line: int
    file: str
    clauses: str
    records: list[str] = field(default_factory=list)


@dataclass
class DataDivision:
    entries: list[DataEntry] = field(default_factory=list)
    files: list[FileDescription] = field(default_factory=list)
    misplaced_names: list[dict] = field(default_factory=list)   # paragraph-like names in DATA DIVISION
    sql_declared_tables: list[dict] = field(default_factory=list)
    problems: list[dict] = field(default_factory=list)


def parse_data(tokens: list[Token]) -> DataDivision:
    result = DataDivision()
    section: str | None = None
    fd: FileDescription | None = None
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok.is_punct("."):
            i += 1
            continue
        if tok.kind == WORD and tok.value in SECTIONS and i + 1 < n and tokens[i + 1].is_word("SECTION"):
            section = tok.value
            fd = None
            i += 2
            continue
        if tok.is_word("FD", "SD") and i + 1 < n and tokens[i + 1].kind == WORD:
            end = _sentence_end(tokens, i)
            fd = FileDescription(tok.value, tokens[i + 1].value, tok.line, tok.file, render(tokens[i + 2:end]))
            result.files.append(fd)
            i = end + 1
            continue
        if tok.is_word("EXEC"):
            end = i + 1
            while end < n and not tokens[end].is_word("END-EXEC"):
                end += 1
            body = tokens[i + 2:end]
            if len(body) > 2 and body[0].is_word("DECLARE") and body[2].is_word("TABLE"):
                result.sql_declared_tables.append({"table": body[1].value, "line": tok.line, "file": tok.file})
            i = end + 1
            continue
        if tok.kind == WORD and _level(tok.value) is not None:
            end = _sentence_end(tokens, i)
            entry = _entry(tokens[i:end], section, fd.name if fd else None)
            result.entries.append(entry)
            if fd is not None and entry.level == 1 and entry.name:
                fd.records.append(entry.name)
            i = end + 1
            continue
        # Anything else: procedure text that ended up here (usually from a copybook).
        end = _sentence_end(tokens, i)
        if tok.kind == WORD and is_user_word(tok.value) and end == i + 1:
            result.misplaced_names.append({"name": tok.value, "line": tok.line, "file": tok.file})
        elif tok.kind == WORD and tok.value not in ("SKIP1", "SKIP2", "SKIP3", "EJECT"):
            result.problems.append({"code": "unexpected_data_division_text", "line": tok.line,
                                    "file": tok.file, "text": render(tokens[i:min(end, i + 12)])})
        i = end + 1
    return result


def _sentence_end(tokens: list[Token], i: int) -> int:
    n = len(tokens)
    j = i
    while j < n and not tokens[j].is_punct("."):
        if tokens[j].is_word("EXEC"):
            while j < n and not tokens[j].is_word("END-EXEC"):
                j += 1
        j += 1
    return min(j, n)


def _level(value: str) -> int | None:
    if not value.isdigit() or len(value) > 2:
        return None
    level = int(value)
    return level if 1 <= level <= 49 or level in (66, 77, 88) else None


def _entry(toks: list[Token], section: str | None, fd: str | None) -> DataEntry:
    level = int(toks[0].value)
    name = None
    k = 1
    if k < len(toks) and toks[k].kind == WORD and toks[k].value not in CLAUSE_WORDS:
        name = None if toks[k].value == "FILLER" else toks[k].value
        k += 1
    entry = DataEntry(level, name, toks[0].line, toks[0].file, section, fd, text=render(toks))
    entry.clauses = parse_clauses(toks[k:])
    return entry


def parse_clauses(toks: list[Token]) -> dict[str, Any]:
    c: dict[str, Any] = {}
    unparsed: list[str] = []
    i = 0
    n = len(toks)

    def word(j: int, *values: str) -> bool:
        return j < n and toks[j].is_word(*values)

    while i < n:
        t = toks[i]
        v = t.value if t.kind == WORD else None
        if v in ("PIC", "PICTURE"):
            i += 1
            if word(i, "IS"):
                i += 1
            if i < n and toks[i].kind in (PIC, WORD):
                c["picture"] = toks[i].value
                i += 1
            continue
        if v == "USAGE":
            i += 1
            if word(i, "IS"):
                i += 1
            if i < n and toks[i].kind == WORD:
                c["usage"] = USAGE_WORDS.get(toks[i].value, toks[i].value)
                i += 1
            continue
        if v in USAGE_WORDS:
            c["usage"] = USAGE_WORDS[v]
            i += 1
            continue
        if v in ("VALUE", "VALUES"):
            i += 1
            if word(i, "IS", "ARE"):
                i += 1
            values, i = _values(toks, i)
            c["values"] = values
            continue
        if v == "OCCURS":
            occ, i = _occurs(toks, i + 1)
            c["occurs"] = occ
            continue
        if v == "REDEFINES" and i + 1 < n:
            c["redefines"] = toks[i + 1].value
            i += 2
            continue
        if v == "RENAMES" and i + 1 < n:
            ren = {"from": toks[i + 1].value}
            i += 2
            if word(i, "THRU", "THROUGH") and i + 1 < n:
                ren["thru"] = toks[i + 1].value
                i += 2
            c["renames"] = ren
            continue
        if v in ("SIGN", "LEADING", "TRAILING"):
            if v == "SIGN":
                i += 1
                if word(i, "IS"):
                    i += 1
            if word(i, "LEADING", "TRAILING"):
                sign = {"position": toks[i].value, "separate": False}
                i += 1
                if word(i, "SEPARATE"):
                    sign["separate"] = True
                    i += 1
                    if word(i, "CHARACTER"):
                        i += 1
                c["sign"] = sign
            continue
        if v in ("SYNC", "SYNCHRONIZED"):
            c["sync"] = True
            i += 1
            if word(i, "LEFT", "RIGHT"):
                i += 1
            continue
        if v in ("JUST", "JUSTIFIED"):
            c["justified"] = True
            i += 1
            if word(i, "RIGHT"):
                i += 1
            continue
        if v == "BLANK":
            c["blank_when_zero"] = True
            i += 1
            while word(i, "WHEN", "ZERO", "ZEROS", "ZEROES"):
                i += 1
            continue
        if v in ("EXTERNAL", "GLOBAL", "VOLATILE"):
            c[v.lower()] = True
            i += 1
            continue
        if v == "IS":
            i += 1
            continue
        unparsed.append(t.text())
        i += 1
    if unparsed:
        c["unparsed"] = " ".join(unparsed)
    return c


def _number(tok: Token) -> float | int | None:
    if tok.kind != WORD:
        return None
    text = tok.value
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return None


def _value_item(toks: list[Token], i: int) -> tuple[dict | None, int]:
    n = len(toks)
    if i >= n:
        return None, i
    t = toks[i]
    if t.is_word("ALL") and i + 1 < n and toks[i + 1].kind == LIT:
        return {"type": "all", "value": toks[i + 1].value}, i + 2
    if t.kind == LIT:
        return {"type": "literal", "value": t.value}, i + 1
    if t.kind == PUNCT and t.value in "+-" and i + 1 < n and _number(toks[i + 1]) is not None:
        num = _number(toks[i + 1])
        return {"type": "number", "value": -num if t.value == "-" else num}, i + 2
    num = _number(t)
    if num is not None:
        return {"type": "number", "value": num}, i + 1
    if t.kind == WORD and t.value in FIGURATIVE:
        return {"type": "figurative", "value": t.value}, i + 1
    return None, i


def _values(toks: list[Token], i: int) -> tuple[list[dict], int]:
    values: list[dict] = []
    n = len(toks)
    while i < n:
        item, j = _value_item(toks, i)
        if item is None:
            break
        i = j
        if i < n and toks[i].is_word("THRU", "THROUGH"):
            hi, j = _value_item(toks, i + 1)
            if hi is not None:
                item = {"type": "range", "from": item["value"], "to": hi["value"]}
                i = j
        values.append(item)
    return values, i


def _occurs(toks: list[Token], i: int) -> tuple[dict, int]:
    n = len(toks)
    occ: dict[str, Any] = {}
    first = _number(toks[i]) if i < n else None
    if first is not None:
        occ["min"] = occ["max"] = int(first)
        i += 1
        if i < n and toks[i].is_word("TO") and i + 1 < n and _number(toks[i + 1]) is not None:
            occ["max"] = int(_number(toks[i + 1]))
            i += 2
    while i < n:
        t = toks[i]
        if t.is_word("TIMES"):
            i += 1
        elif t.is_word("DEPENDING"):
            i += 1
            if i < n and toks[i].is_word("ON"):
                i += 1
            if i < n:
                occ["depending_on"] = toks[i].value
                i += 1
        elif t.is_word("ASCENDING", "DESCENDING"):
            order = t.value
            i += 1
            while i < n and toks[i].is_word("KEY", "IS"):
                i += 1
            keys = []
            while i < n and toks[i].kind == WORD and is_user_word(toks[i].value):
                keys.append(toks[i].value)
                i += 1
            occ.setdefault("keys", []).append({"order": order, "fields": keys})
        elif t.is_word("INDEXED"):
            i += 1
            if i < n and toks[i].is_word("BY"):
                i += 1
            names = []
            while i < n and toks[i].kind == WORD and is_user_word(toks[i].value):
                names.append(toks[i].value)
                i += 1
            occ["indexed_by"] = names
        else:
            break
    return occ, i
