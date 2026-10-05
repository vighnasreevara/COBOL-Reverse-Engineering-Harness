"""Record layouts: hierarchy, sizes and offsets from parsed data entries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harness.data.picture import analyze, storage_bytes


@dataclass
class Item:
    level: int
    name: str | None
    line: int
    file: str
    clauses: dict[str, Any]
    children: list["Item"] = field(default_factory=list)
    conditions: list["Item"] = field(default_factory=list)   # 88 levels
    renames: list["Item"] = field(default_factory=list)      # 66 levels
    usage: str = "DISPLAY"
    size: int | None = None       # bytes of one occurrence
    offset: int | None = None     # from the start of the record
    category: str = "group"
    digits: int | None = None
    scale: int | None = None
    signed: bool = False

    @property
    def occurs_max(self) -> int:
        occ = self.clauses.get("occurs")
        return occ.get("max", 1) if occ else 1

    @property
    def is_group(self) -> bool:
        return bool(self.children) or ("picture" not in self.clauses and self.clauses.get("usage") not in
                                       ("INDEX", "POINTER", "PROCEDURE-POINTER", "FUNCTION-POINTER",
                                        "COMP-1", "COMP-2"))


def build_records(entries: list[dict]) -> tuple[list[tuple[Item, dict]], list[dict]]:
    """Group a program's entries into records (01/77 trees). Returns (record, context) pairs."""
    problems: list[dict] = []
    records: list[tuple[Item, dict]] = []
    stack: list[Item] = []
    last_data: Item | None = None
    for e in entries:
        item = Item(e["level"], e["name"], e["line"], e["file"], e["clauses"])
        if item.level in (1, 77):
            records.append((item, {"section": e["section"], "fd": e["fd"]}))
            stack = [item]
            last_data = item
            continue
        if not stack:
            problems.append({"code": "orphan_data_entry", "line": item.line, "file": item.file,
                             "message": f"level {item.level} {item.name or 'FILLER'} has no 01-level record"})
            continue
        if item.level == 88:
            (last_data or stack[-1]).conditions.append(item)
            continue
        if item.level == 66:
            stack[0].renames.append(item)
            continue
        while stack and stack[-1].level >= item.level:
            stack.pop()
        if not stack:
            problems.append({"code": "orphan_data_entry", "line": item.line, "file": item.file,
                             "message": f"level {item.level} {item.name or 'FILLER'} has no parent"})
            continue
        stack[-1].children.append(item)
        stack.append(item)
        last_data = item
    for rec, _ in records:
        _layout(rec, 0, "DISPLAY", problems)
    return records, problems


def _layout(item: Item, offset: int, inherited_usage: str, problems: list[dict]) -> int:
    """Assign sizes/offsets below item; returns bytes of one occurrence."""
    item.usage = item.clauses.get("usage", inherited_usage)
    item.offset = offset
    if item.children:
        if "picture" in item.clauses:
            problems.append({"code": "group_with_picture", "line": item.line, "file": item.file,
                             "message": f"group item {item.name} has a PICTURE clause"})
        cursor = offset
        by_name: dict[str, Item] = {}
        end = offset
        for child in item.children:
            target_name = child.clauses.get("redefines")
            if target_name:
                target = by_name.get(target_name)
                if target is None:
                    problems.append({"code": "redefines_target_missing", "line": child.line, "file": child.file,
                                     "message": f"{child.name} redefines {target_name}, which is not an "
                                                f"earlier item at the same level"})
                    start = cursor
                else:
                    start = target.offset
                size = _layout(child, start, item.usage, problems)
                total = size * child.occurs_max if size is not None else None
                if target is not None and total is not None and target.size is not None:
                    target_total = target.size * target.occurs_max
                    if total > target_total and target.level != 1:
                        problems.append({"code": "redefines_larger_than_target", "line": child.line,
                                         "file": child.file,
                                         "message": f"{child.name} ({total} bytes) redefines {target_name} "
                                                    f"({target_total} bytes)"})
                if total is not None:
                    end = max(end, start + total)
            else:
                size = _layout(child, cursor, item.usage, problems)
                if size is None:
                    item.size = None     # size unknown from here on
                    break
                cursor += size * child.occurs_max
                end = max(end, cursor)
            if child.name:
                by_name[child.name] = child
        else:
            item.size = end - offset
        item.category = "group"
        return item.size
    if item.is_group and "picture" not in item.clauses:
        item.category = "group"
        item.size = 0
        if item.level == 1:
            problems.append({"code": "empty_record", "line": item.line, "file": item.file,
                             "message": f"01 {item.name} has no subordinate items; a COPY under it probably "
                                        f"starts its own 01 level, so the copied fields are a separate record"})
        else:
            problems.append({"code": "elementary_item_without_picture", "line": item.line, "file": item.file,
                             "message": f"{item.name or 'FILLER'} has neither a PICTURE nor subordinate items"})
        return 0
    pic = analyze(item.clauses["picture"]) if "picture" in item.clauses else None
    sign = item.clauses.get("sign") or {}
    item.size = storage_bytes(pic, item.usage, sign.get("separate", False))
    if pic is not None:
        item.category = pic.category
        item.digits = pic.digits
        item.scale = pic.scale
        item.signed = pic.signed
        if pic.category == "unknown":
            problems.append({"code": "unrecognised_picture", "line": item.line, "file": item.file,
                             "message": f"PICTURE {pic.raw} of {item.name} is not recognised"})
    else:
        item.category = item.usage.lower()
    _check_value(item, problems)
    return item.size


def _check_value(item: Item, problems: list[dict]) -> None:
    values = item.clauses.get("values") or []
    if not values or item.size is None:
        return
    v = values[0]
    if v["type"] == "literal" and item.category in ("alphanumeric", "alphabetic") and len(v["value"]) > item.size:
        problems.append({"code": "value_too_long", "line": item.line, "file": item.file,
                         "message": f"VALUE '{v['value']}' ({len(v['value'])} characters) does not fit "
                                    f"{item.name} ({item.size} bytes)"})
    if v["type"] == "literal" and item.category == "numeric":
        problems.append({"code": "nonnumeric_value_for_numeric", "line": item.line, "file": item.file,
                         "message": f"numeric item {item.name} has an alphanumeric VALUE '{v['value']}'"})
    if v["type"] == "number" and item.category in ("numeric",) and item.digits is not None:
        integer_digits = len(str(int(abs(v["value"])))) if v["value"] else 0
        if integer_digits > (item.digits - (item.scale or 0)) and item.digits:
            problems.append({"code": "value_too_large", "line": item.line, "file": item.file,
                             "message": f"VALUE {v['value']} has more integer digits than {item.name} allows"})


def flatten(item: Item, parent_path: str = "") -> list[dict[str, Any]]:
    """Depth-first list of fields with qualified names."""
    name = item.name or "FILLER"
    path = f"{parent_path}.{name}" if parent_path else name
    occ = item.clauses.get("occurs")
    out = [{
        "name": item.name,
        "qualified_name": path,
        "level": item.level,
        "category": item.category,
        "picture": item.clauses.get("picture"),
        "usage": item.usage,
        "size": item.size,
        "offset": item.offset,
        "digits": item.digits,
        "scale": item.scale,
        "signed": item.signed,
        "occurs": occ,
        "redefines": item.clauses.get("redefines"),
        "values": item.clauses.get("values"),
        "conditions": [{"name": c.name, "values": c.clauses.get("values", []), "line": c.line, "file": c.file}
                       for c in item.conditions],
        "line": item.line,
        "file": item.file,
        "elementary": not item.children,
    }]
    for child in item.children:
        out.extend(flatten(child, path))
    return out


def signature(item: Item) -> str:
    parts = []
    for f in flatten(item):
        parts.append(f"{f['level']}|{f['name']}|{f['picture']}|{f['usage']}|{f['occurs']}|{f['redefines']}")
    return "\n".join(parts)
