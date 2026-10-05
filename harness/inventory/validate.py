"""Self-checks for inventory_artifact.json.

Run on every artifact before it is handed to the next stage. Catches the
usual failure modes of generated inventories: stats that disagree with the
arrays, edges pointing at nothing, references without a source location.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "inventory_artifact.schema.json"

RESOLUTIONS = {"resolved", "unresolved", "ambiguous", "system", "external", "dynamic", "temporary", "undefined"}
SEVERITIES = {"error", "warning", "info"}


def validate(artifact: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    problems += _schema_problems(artifact)
    if problems:
        return problems

    from harness.inventory.builder import compute_stats

    expected = compute_stats(artifact)
    for key, value in expected.items():
        if artifact["stats"].get(key) != value:
            problems.append(f"stats.{key} is {artifact['stats'].get(key)!r} but the arrays give {value!r}")

    files = {f["path"] for f in artifact["files"]}
    for group in ("programs", "copybooks"):
        ids = [i["id"] for i in artifact[group]]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes and group == "copybooks":
            problems.append(f"duplicate {group} ids: {sorted(dupes)}")
        for item in artifact[group]:
            if item["path"] not in files:
                problems.append(f"{item['id']} points at {item['path']}, which is not in files")

    nodes = {n["id"] for n in artifact["graph"]["nodes"]}
    ref_ids = set()
    for r in artifact["references"]:
        if r["id"] in ref_ids:
            problems.append(f"duplicate reference id {r['id']}")
        ref_ids.add(r["id"])
        if r["resolution"] not in RESOLUTIONS:
            problems.append(f"{r['id']} has unknown resolution {r['resolution']!r}")
        if r["path"] not in files:
            problems.append(f"{r['id']} cites {r['path']}, which is not in files")
        if not isinstance(r["line"], int) or r["line"] < 1:
            problems.append(f"{r['id']} has no valid line number")
        if r["from"] not in nodes:
            problems.append(f"{r['id']} comes from {r['from']}, which is not a graph node")
        if r["to"] is not None and r["to"] not in nodes:
            problems.append(f"{r['id']} points at {r['to']}, which is not a graph node")
        if r["resolution"] in ("resolved", "ambiguous") and r["to"] is None:
            problems.append(f"{r['id']} is {r['resolution']} but has no target node")

    for e in artifact["graph"]["edges"]:
        if e["from"] not in nodes or e["to"] not in nodes:
            problems.append(f"edge {e['from']} -> {e['to']} ({e['type']}) uses an unknown node")
        if e["count"] != len(e["references"]):
            problems.append(f"edge {e['from']} -> {e['to']} count {e['count']} != {len(e['references'])} references")
        missing = [x for x in e["references"] if x not in ref_ids]
        if missing:
            problems.append(f"edge {e['from']} -> {e['to']} cites unknown references {missing[:3]}")

    for c in artifact["copybooks"]:
        if artifact["copybook_map"].get(c["name"]) != c["used_by"]:
            problems.append(f"copybook_map[{c['name']}] disagrees with copybooks[].used_by")

    for i in artifact["issues"]:
        if i["severity"] not in SEVERITIES:
            problems.append(f"issue {i['code']} has unknown severity {i['severity']!r}")
        if i.get("path") is not None and i["path"] not in files:
            problems.append(f"issue {i['code']} cites {i['path']}, which is not in files")
    return problems


def _schema_problems(artifact: dict[str, Any]) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        required = ("schema_version", "meta", "stats", "files", "programs", "copybooks", "jcl", "cics",
                    "bms", "db2", "vsam", "references", "graph", "copybook_map", "issues")
        return [f"missing top-level key {k}" for k in required if k not in artifact]
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"schema: {'/'.join(str(p) for p in err.absolute_path) or '(root)'}: {err.message}"
        for err in sorted(validator.iter_errors(artifact), key=lambda e: list(e.absolute_path))[:50]
    ]
