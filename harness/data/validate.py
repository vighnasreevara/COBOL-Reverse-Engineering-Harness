"""Self-checks for data_artifact.json."""

from __future__ import annotations

from typing import Any

from harness.common.schema import schema_problems


def validate_data(a: dict[str, Any]) -> list[str]:
    problems = schema_problems(a, "data_artifact.schema.json")
    if problems:
        return problems
    from harness.data.build import compute_stats

    for key, value in compute_stats(a).items():
        if a["stats"].get(key) != value:
            problems.append(f"stats.{key} is {a['stats'].get(key)!r} but the arrays give {value!r}")
    record_ids = {r["id"] for r in a["records"]}
    field_ids = {f["id"] for f in a["fields"]}
    program_ids = {p["id"] for p in a["programs"]}
    entity_ids = {e["id"] for e in a["entities"]}
    for r in a["records"]:
        for f in r["fields"]:
            if r["size"] is None or f["size"] is None or f["offset"] is None:
                continue
            span = f["size"] * ((f.get("occurs") or {}).get("max", 1))
            if f["offset"] < 0 or f["offset"] + span > r["size"]:
                problems.append(f"{f['id']} at offset {f['offset']} ({span} bytes) lies outside {r['id']} "
                                f"({r['size']} bytes)")
        for use in r["used_by"]:
            if use["program"] not in program_ids:
                problems.append(f"{r['id']} is used by unknown program {use['program']}")
    for f in a["fields"]:
        if f["record"] not in record_ids:
            problems.append(f"{f['id']} belongs to unknown record {f['record']}")
    for c in a["conditions"]:
        if c["field"] not in field_ids:
            problems.append(f"{c['id']} refers to unknown field {c['field']}")
    for p in a["programs"]:
        for ref in p["records"]:
            if ref["record"] not in record_ids:
                problems.append(f"{p['id']} lists unknown record {ref['record']}")
    for fl in a["data_flows"]:
        if fl["entity"] not in entity_ids:
            problems.append(f"data flow to unknown entity {fl['entity']}")
        if fl["program"] not in program_ids:
            problems.append(f"data flow from unknown program {fl['program']}")
    for rel in a["relationships"]:
        for end in (rel["from"], rel["to"]):
            if end not in entity_ids and not end.startswith("dataset:"):
                problems.append(f"relationship end {end} is not an entity")
    return problems
