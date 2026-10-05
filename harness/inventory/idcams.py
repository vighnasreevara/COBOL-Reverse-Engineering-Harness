"""Parse IDCAMS control statements (DEFINE CLUSTER / AIX / GDG, DELETE)."""

from __future__ import annotations

from dataclasses import dataclass, field

from harness.inventory.keywords import Unit, read_units

_ORGANIZATIONS = {"INDEXED": "KSDS", "NONINDEXED": "ESDS", "NUMBERED": "RRDS", "LINEAR": "LDS"}


@dataclass
class VsamDefinition:
    kind: str                  # CLUSTER, ALTERNATEINDEX, PATH, GDG
    name: str
    line: int
    organization: str | None = None
    key_length: int | None = None
    key_offset: int | None = None
    record_size_avg: int | None = None
    record_size_max: int | None = None
    related_to: str | None = None
    attributes: dict = field(default_factory=dict)


@dataclass
class IdcamsResult:
    definitions: list[VsamDefinition] = field(default_factory=list)
    deletes: list[tuple[str, int]] = field(default_factory=list)


def _clean(lines: list[str], first_line: int) -> list[tuple[int, str]]:
    out = []
    in_block_comment = False
    for offset, raw in enumerate(lines):
        number = first_line + offset
        text = raw.rstrip()
        if in_block_comment:
            end = text.find("*/")
            if end == -1:
                continue
            text = text[end + 2:]
            in_block_comment = False
        while "/*" in text:
            start = text.find("/*")
            end = text.find("*/", start + 2)
            if end == -1:
                text = text[:start]
                in_block_comment = True
                break
            text = text[:start] + text[end + 2:]
        if text.lstrip().startswith("*"):
            continue
        stripped = text.rstrip()
        if stripped.endswith(("-", "+")):
            stripped = stripped[:-1]
        out.append((number, stripped))
    return out


def _numbers(value: str | None) -> list[int]:
    if not value:
        return []
    return [int(p) for p in value.replace(",", " ").split() if p.isdigit()]


def parse_idcams(lines: list[str], first_line: int = 1) -> IdcamsResult:
    result = IdcamsResult()
    units = read_units(_clean(lines, first_line))
    k = 0
    while k < len(units):
        unit = units[k]
        if unit.keyword == "DEFINE" and unit.value is None and k + 1 < len(units):
            kind_unit = units[k + 1]
            kind = {"AIX": "ALTERNATEINDEX", "CL": "CLUSTER"}.get(kind_unit.keyword, kind_unit.keyword)
            if kind_unit.value is not None:
                definition = _definition(kind, kind_unit)
                if definition:
                    result.definitions.append(definition)
            k += 2
            continue
        if unit.keyword == "DELETE" and k + 1 < len(units):
            target = units[k + 1]
            if target.value is None:
                result.deletes.append((target.keyword, target.line))
            k += 2
            continue
        k += 1
    return result


def _definition(kind: str, unit: Unit) -> VsamDefinition | None:
    inner = read_units([(unit.line, unit.value or "")])
    attrs: dict[str, str | None] = {}
    for u in inner:
        attrs.setdefault(u.keyword, u.value)
    name = attrs.get("NAME")
    if not name:
        return None
    d = VsamDefinition(kind, name.upper(), unit.line)
    for keyword, org in _ORGANIZATIONS.items():
        if keyword in attrs:
            d.organization = org
    if kind == "CLUSTER" and d.organization is None:
        d.organization = "KSDS"       # IDCAMS default is INDEXED
    keys = _numbers(attrs.get("KEYS"))
    if keys:
        d.key_length = keys[0]
        d.key_offset = keys[1] if len(keys) > 1 else 0
    sizes = _numbers(attrs.get("RECORDSIZE") or attrs.get("RECSZ"))
    if sizes:
        d.record_size_avg = sizes[0]
        d.record_size_max = sizes[1] if len(sizes) > 1 else sizes[0]
    if attrs.get("RELATE"):
        d.related_to = attrs["RELATE"].upper()
    for key in ("LIMIT", "SHAREOPTIONS", "FREESPACE", "CONTROLINTERVALSIZE", "CISZ"):
        if attrs.get(key):
            d.attributes[key] = attrs[key]
    return d


def looks_like_idcams(text: str) -> bool:
    upper = text.upper()
    return "DEFINE" in upper and ("CLUSTER" in upper or "ALTERNATEINDEX" in upper or " GDG" in upper)
