"""Decide what kind of artifact each file is."""

from __future__ import annotations

import re
from pathlib import Path

from harness.inventory.idcams import looks_like_idcams

PROGRAM = "program"
COPYBOOK = "copybook"
JCL = "jcl"
CSD = "csd"
BMS = "bms"
DDL = "ddl"
IDCAMS = "idcams"
CONTROL = "control"
LISTING = "listing"
DOC = "doc"
OTHER = "other"

EXTENSIONS = {
    ".cbl": PROGRAM, ".cob": PROGRAM, ".cobol": PROGRAM, ".ccp": PROGRAM,
    ".sqb": PROGRAM, ".pco": PROGRAM,
    ".cpy": COPYBOOK, ".copy": COPYBOOK, ".cbk": COPYBOOK, ".cpb": COPYBOOK,
    ".dclgen": COPYBOOK, ".dcl": COPYBOOK,
    ".jcl": JCL, ".job": JCL, ".prc": JCL, ".proc": JCL,
    ".csd": CSD,
    ".bms": BMS,
    ".sql": DDL, ".ddl": DDL, ".db2": DDL,
    ".idcams": IDCAMS, ".ams": IDCAMS,
    ".ctl": CONTROL, ".lst": LISTING,
    ".md": DOC, ".pdf": DOC, ".doc": DOC, ".docx": DOC, ".rst": DOC,
}

# Extensions whose content decides the kind.
_SNIFF = {".txt", "", ".dat", ".src", ".mbr", ".ctl"}


def classify(path: Path, text: str) -> str:
    ext = path.suffix.lower()
    if ext in EXTENSIONS and ext not in _SNIFF:
        return EXTENSIONS[ext]
    if ext in _SNIFF:
        sniffed = sniff(text)
        if sniffed:
            return sniffed
        return CONTROL if ext == ".ctl" else OTHER
    return OTHER


def sniff(text: str) -> str | None:
    head = text[:20000]
    upper = head.upper()
    if re.search(r"^//\S*\s+(JOB|EXEC|PROC)\b", upper, re.M):
        return JCL
    if re.search(r"\b(IDENTIFICATION|ID)\s+DIVISION\b", upper) or "PROGRAM-ID" in upper:
        return PROGRAM
    if "DFHMSD" in upper or "DFHMDI" in upper:
        return BMS
    if re.search(r"\bDEFINE\s+(TRANSACTION|PROGRAM|MAPSET|FILE)\s*\(", upper):
        return CSD
    if re.search(r"\bCREATE\s+(TABLE|VIEW|INDEX|UNIQUE\s+INDEX)\b", upper):
        return DDL
    if looks_like_idcams(head):
        return IDCAMS
    if re.search(r"^\s{0,12}0?1\s+[A-Z0-9-]+\s*\.?\s*$", upper, re.M) and " PIC" in upper:
        return COPYBOOK
    return None
