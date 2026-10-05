"""Reader for the KEYWORD(value) command syntax shared by DFHCSDUP and IDCAMS."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Unit:
    keyword: str
    value: str | None     # text inside the parentheses, None for a bare keyword
    line: int


def read_units(lines: list[tuple[int, str]]) -> list[Unit]:
    """Split text into top-level KEYWORD / KEYWORD(value) units.

    ``lines`` is (line number, text) with comments already removed. Values
    keep nested parentheses and blanks, e.g. DESCRIPTION(Order inquiry).
    """
    units: list[Unit] = []
    word: list[str] = []
    word_line = 0
    value: list[str] = []
    depth = 0
    quote = False

    def flush_word() -> None:
        nonlocal word
        if word:
            units.append(Unit("".join(word).upper(), None, word_line))
            word = []

    for number, text in lines:
        for ch in text:
            if depth > 0:
                if quote:
                    value.append(ch)
                    if ch == "'":
                        quote = False
                    continue
                if ch == "'":
                    quote = True
                    value.append(ch)
                elif ch == "(":
                    depth += 1
                    value.append(ch)
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        units[-1].value = "".join(value).strip()
                        value = []
                    else:
                        value.append(ch)
                else:
                    value.append(ch)
                continue
            if ch == "(":
                if word:
                    units.append(Unit("".join(word).upper(), "", word_line))
                    word = []
                elif units and units[-1].value is None:
                    units[-1].value = ""     # KEYWORD (value) with a blank before "("
                else:
                    units.append(Unit("", "", number))
                depth = 1
                value = []
            elif ch.isspace() or ch in ",":
                flush_word()
            else:
                if not word:
                    word_line = number
                word.append(ch)
        if depth > 0:
            value.append(" ")
        else:
            flush_word()
    flush_word()
    return units
