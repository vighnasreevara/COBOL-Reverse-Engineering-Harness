"""Reading source files exported from a mainframe into a workstation repo."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

# Mainframe exports are usually ASCII/UTF-8 after transfer, but cp1252 and
# latin-1 show up for files with national characters. latin-1 never fails,
# so it is the last resort.
_ENCODINGS = ("utf-8", "cp1252", "latin-1")


@dataclass(frozen=True)
class TextFile:
    path: Path
    text: str
    lines: list[str]
    encoding: str
    line_ending: str          # "crlf", "lf", "cr", "mixed" or "none"
    final_newline: bool
    size_bytes: int
    sha256: str

    @property
    def is_blank(self) -> bool:
        return not self.text.strip()


def read_text(path: Path) -> TextFile:
    raw = path.read_bytes()
    text = None
    encoding = _ENCODINGS[-1]
    for enc in _ENCODINGS:
        try:
            text = raw.decode(enc)
            encoding = enc
            break
        except UnicodeDecodeError:
            continue
    assert text is not None
    if text.startswith("﻿"):
        text = text[1:]

    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    kinds = [k for k, n in (("crlf", crlf), ("lf", lf), ("cr", cr)) if n]
    line_ending = kinds[0] if len(kinds) == 1 else ("mixed" if kinds else "none")

    return TextFile(
        path=path,
        text=text,
        lines=text.splitlines(),
        encoding=encoding,
        line_ending=line_ending,
        final_newline=text.endswith(("\n", "\r")),
        size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
    )
