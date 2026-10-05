"""Self-checks for the diagram stage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.common.schema import schema_problems
from harness.diagram.mermaid import lint


def validate_diagrams(a: dict[str, Any], out_dir: Path | None = None) -> list[str]:
    problems = schema_problems(a, "diagrams_artifact.schema.json")
    if problems:
        return problems
    from harness.diagram.build import compute_stats

    for key, value in compute_stats(a).items():
        if a["stats"].get(key) != value:
            problems.append(f"stats.{key} is {a['stats'].get(key)!r} but the list gives {value!r}")
    for d in a["diagrams"]:
        if d["lint"]:
            problems.append(f"{d['id']}: {d['lint'][0]}")
        if out_dir is not None:
            path = out_dir / d["file"]
            if not path.exists():
                problems.append(f"{d['id']}: {d['file']} is missing")
            elif lint(path.read_text(encoding="utf-8")) != d["lint"]:
                problems.append(f"{d['id']}: file changed since it was generated")
    return problems
