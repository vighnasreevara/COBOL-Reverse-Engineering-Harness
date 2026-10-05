"""Self-checks for the logic stage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.common.schema import schema_problems


def validate_logic(manifest: dict[str, Any], out_dir: Path) -> list[str]:
    problems = schema_problems(manifest, "logic_artifact.schema.json")
    if problems:
        return problems
    from harness.logic.build import compute_stats

    for key, value in compute_stats(manifest).items():
        if manifest["stats"].get(key) != value:
            problems.append(f"stats.{key} is {manifest['stats'].get(key)!r} but the program list gives {value!r}")
    root = Path(manifest["meta"]["root"])
    line_counts: dict[str, int] = {}
    seen_ids: set[str] = set()
    for entry in manifest["programs"]:
        path = out_dir / entry["output"]
        if not path.exists():
            problems.append(f"{entry['name']}: {entry['output']} is missing")
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        problems += [f"{entry['name']}: {p}" for p in schema_problems(doc, "logic_program.schema.json", 10)]
        m = doc["metrics"]
        if m != entry["metrics"]:
            problems.append(f"{entry['name']}: manifest metrics differ from the program file")
        if m["branches"] != len(doc["branches"]) or m["loops"] != len(doc["loops"]):
            problems.append(f"{entry['name']}: branch/loop counts disagree with the lists")
        if m["pseudocode_lines"] != sum(len(p["pseudocode"]) for p in doc["paragraphs"]):
            problems.append(f"{entry['name']}: pseudocode_lines disagrees with the paragraphs")
        ids = [b["id"] for b in doc["branches"]] + [lp["id"] for lp in doc["loops"]]
        if ids != entry["branch_ids"] + entry["loop_ids"]:
            problems.append(f"{entry['name']}: manifest ids differ from the program file")
        for i in ids:
            if i in seen_ids:
                problems.append(f"duplicate id {i}")
            seen_ids.add(i)
        names = {p["name"] for p in doc["paragraphs"]}
        for b in doc["branches"]:
            if b["paragraph"] not in names:
                problems.append(f"{b['id']} sits in unknown paragraph {b['paragraph']}")
        if root.exists():
            for p in doc["paragraphs"]:
                for line in p["pseudocode"]:
                    f = line["file"]
                    if f not in line_counts:
                        fp = root / f
                        line_counts[f] = len(fp.read_text(encoding="utf-8", errors="replace").splitlines()) \
                            if fp.is_file() else 0
                    if not 1 <= line["line"] <= line_counts[f]:
                        problems.append(f"{entry['name']}: pseudocode cites {f}:{line['line']}, which does not exist")
                        break
    return problems
