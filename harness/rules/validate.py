"""Self-checks for rules_artifact.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.common.schema import schema_problems


def validate_rules(a: dict[str, Any], logic_dir: Path | None = None) -> list[str]:
    problems = schema_problems(a, "rules_artifact.schema.json")
    if problems:
        return problems
    from harness.rules.build import compute_stats

    for key, value in compute_stats(a).items():
        if a["stats"].get(key) != value:
            problems.append(f"stats.{key} is {a['stats'].get(key)!r} but the rules give {value!r}")
    ids = [r["id"] for r in a["rules"]]
    if len(ids) != len(set(ids)):
        problems.append("rule ids are not unique")
    known = set(ids)
    for g in a["groups"]:
        missing = [r for r in g["rules"] if r not in known]
        if missing:
            problems.append(f"{g['id']} lists unknown rules {missing[:3]}")
    for r in a["rules"]:
        if r["business"] and r["category"] in ("error_handling", "control_flow"):
            problems.append(f"{r['id']} is marked business but categorised {r['category']}")
    if logic_dir is not None and (logic_dir / "logic_artifact.json").exists():
        manifest = json.loads((logic_dir / "logic_artifact.json").read_text(encoding="utf-8"))
        branch_ids = {b for p in manifest["programs"] for b in p["branch_ids"]}
        for r in a["rules"]:
            if r["source"].startswith("branch:") and r["source"] not in branch_ids:
                problems.append(f"{r['id']} cites unknown branch {r['source']}")
    return problems
