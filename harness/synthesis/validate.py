"""Self-checks for the synthesis stage."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from harness.common.schema import schema_problems

_CITE = re.compile(r"`([\w./-]+\.(?:cbl|cob|cpy|jcl|csd|bms|sql|txt|ctl)):(\d+)`", re.I)


def validate_synthesis(a: dict[str, Any], final_dir: Path | None = None) -> list[str]:
    problems = schema_problems(a, "synthesis_artifact.schema.json")
    if problems:
        return problems
    from harness.synthesis.build import compute_stats

    expected = compute_stats(a)
    for key, value in expected.items():
        if a["stats"].get(key) != value:
            problems.append(f"stats.{key} is {a['stats'].get(key)!r} but the lists give {value!r}")
    ids = [g["id"] for g in a["gaps"]]
    if len(ids) != len(set(ids)):
        problems.append("gap ids are not unique")
    if final_dir is not None:
        root = Path(a["meta"]["root"])
        brd = (final_dir / "brd.md").read_text(encoding="utf-8")
        counts: dict[str, int] = {}
        for path, line in _CITE.findall(brd):
            if path not in counts:
                p = root / path
                counts[path] = len(p.read_text(encoding="utf-8", errors="replace").splitlines()) if p.is_file() else -1
            if counts[path] < 0:
                problems.append(f"brd.md cites missing file {path}")
            elif not 1 <= int(line) <= counts[path]:
                problems.append(f"brd.md cites {path}:{line}, which does not exist")
        if a["annotation_problems"]:
            problems += [f"rejected annotation: {p}" for p in a["annotation_problems"][:20]]
    return problems
