"""Parse CICS CSD resource definitions (DFHCSDUP DEFINE commands)."""

from __future__ import annotations

from dataclasses import dataclass, field

from harness.inventory.keywords import read_units

COMMANDS = {"DEFINE", "ADD", "ALTER", "DELETE", "REMOVE", "LIST", "COPY", "APPEND", "INSTALL"}


@dataclass
class CsdResource:
    type: str              # TRANSACTION, PROGRAM, MAPSET, FILE, DB2ENTRY, ...
    name: str
    line: int
    attributes: dict[str, str] = field(default_factory=dict)


def parse_csd(lines: list[str]) -> list[CsdResource]:
    body = []
    for number, raw in enumerate(lines, start=1):
        text = raw.rstrip()
        if not text.strip() or text.lstrip().startswith("*"):
            continue
        if len(text) > 72 and text[72:].strip().isdigit():
            text = text[:72]          # sequence numbers in columns 73-80
        body.append((number, text))

    resources: list[CsdResource] = []
    command = None
    current: CsdResource | None = None
    for unit in read_units(body):
        if unit.value is None and unit.keyword in COMMANDS:
            command = unit.keyword
            current = None
            continue
        if command != "DEFINE":
            continue
        if current is None:
            if unit.value:
                current = CsdResource(unit.keyword, unit.value.upper(), unit.line)
                resources.append(current)
            continue
        current.attributes[unit.keyword] = (unit.value or "").strip()
    return resources
