"""Self-checks for the parser manifest and per-program outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.common.schema import schema_problems
from harness.parser.procedure import iter_statements


def validate_program(doc: dict[str, Any], root: Path | None = None) -> list[str]:
    problems = schema_problems(doc, "parser_program.schema.json")
    if problems:
        return problems
    name = doc["program"]["name"]
    sources = set(doc["sources"])
    paragraphs = doc["procedure"]["paragraphs"]
    names = [p["name"] for p in paragraphs]
    nodes = [n["name"] for n in doc["cfg"]["nodes"]]
    if names != nodes:
        problems.append(f"{name}: cfg nodes do not match paragraphs")
    known = set(names) | {s["name"] for s in doc["procedure"]["sections"]}
    for e in doc["cfg"]["edges"]:
        if e["from"] not in known and e["type"] != "ALTER":
            problems.append(f"{name}: edge from unknown paragraph {e['from']}")
        if e["to"] not in known:
            problems.append(f"{name}: edge to unknown paragraph {e['to']}")
    dead = {n["name"] for n in doc["cfg"]["nodes"] if not n["reachable"]}
    if dead != set(doc["cfg"]["dead_paragraphs"]):
        problems.append(f"{name}: dead_paragraphs disagrees with node reachability")
    count = 0
    line_cache: dict[str, int] = {}
    for p in paragraphs:
        for s in iter_statements(p["statements"]):
            count += 1
            if s["file"] not in sources:
                problems.append(f"{name}: statement at {s['file']}:{s['line']} cites a file not in sources")
            if root is not None:
                if s["file"] not in line_cache:
                    path = root / s["file"]
                    line_cache[s["file"]] = len(path.read_text(encoding="utf-8", errors="replace").splitlines()) \
                        if path.exists() else 0
                if s["line"] > line_cache[s["file"]]:
                    problems.append(f"{name}: statement line {s['line']} is beyond the end of {s['file']}")
    if count != doc["metrics"]["statements"]:
        problems.append(f"{name}: metrics.statements {doc['metrics']['statements']} != {count} statements")
    if len(paragraphs) != doc["metrics"]["paragraphs"]:
        problems.append(f"{name}: metrics.paragraphs disagrees with paragraphs")
    if len(doc["data"]["entries"]) != doc["metrics"]["data_entries"]:
        problems.append(f"{name}: metrics.data_entries disagrees with entries")
    return problems


def validate_manifest(manifest: dict[str, Any], out_dir: Path, deep: bool = True) -> list[str]:
    from harness.parser.build import compute_stats

    problems = schema_problems(manifest, "parser_artifact.schema.json")
    if problems:
        return problems
    expected = compute_stats(manifest)
    for key, value in expected.items():
        if manifest["stats"].get(key) != value:
            problems.append(f"stats.{key} is {manifest['stats'].get(key)!r} but the program list gives {value!r}")
    root = Path(manifest["meta"]["root"])
    for p in manifest["programs"]:
        if p["status"] != "parsed":
            continue
        path = out_dir / p["output"]
        if not path.exists():
            problems.append(f"{p['name']}: output {p['output']} is missing")
            continue
        if deep:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if doc["metrics"] != p["metrics"]:
                problems.append(f"{p['name']}: manifest metrics differ from the program file")
            problems += validate_program(doc, root if root.exists() else None)
    return problems
