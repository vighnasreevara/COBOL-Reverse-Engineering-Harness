"""Agent-written annotations, checked before any later stage may use them.

Deterministic stages produce facts. Agents add meaning (names, descriptions,
summaries) in an annotations file. Every annotation must point at something
that exists in the stage artifact, and every piece of evidence must resolve
to a real source line or artifact id. Nothing else is accepted.

File format (JSON):

    {
      "stage": "data",
      "artifact": "output/data/data_artifact.json",
      "items": [
        {
          "target": "record:CUSTREC/CUSTOMER-RECORD",
          "kind": "description",
          "text": "Customer master record ...",
          "evidence": [
            {"path": "copybook/CUSTREC.cpy", "line": 5},
            {"id": "field:CUSTREC/CUSTOMER-RECORD:CUSTOMER-RECORD.CUST-KEY"}
          ],
          "confidence": "high"
        }
      ]
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

KINDS = {"description", "name", "summary", "narrative", "caption", "note", "verdict"}
CONFIDENCE = {"high", "medium", "low"}


def collect_ids(document: Any) -> set[str]:
    """Every string value stored under an "id" key anywhere in the document."""
    ids: set[str] = set()
    stack = [document]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            value = cur.get("id")
            if isinstance(value, str):
                ids.add(value)
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return ids


def check_annotations(annotations: dict[str, Any], artifact: dict[str, Any], root: Path,
                      extra_ids: set[str] | None = None) -> list[str]:
    problems: list[str] = []
    ids = collect_ids(artifact) | (extra_ids or set())
    line_counts: dict[str, int] = {}

    def lines_in(path: str) -> int:
        if path not in line_counts:
            p = root / path
            line_counts[path] = len(p.read_text(encoding="utf-8", errors="replace").splitlines()) if p.is_file() else -1
        return line_counts[path]

    items = annotations.get("items")
    if not isinstance(items, list):
        return ["annotations file has no 'items' list"]
    for k, item in enumerate(items):
        where = f"items[{k}]"
        target = item.get("target")
        if target not in ids:
            problems.append(f"{where}: target {target!r} does not exist in the artifact")
        if item.get("kind") not in KINDS:
            problems.append(f"{where}: kind must be one of {sorted(KINDS)}")
        if not str(item.get("text", "")).strip():
            problems.append(f"{where}: text is empty")
        if item.get("confidence") not in CONFIDENCE:
            problems.append(f"{where}: confidence must be high, medium or low")
        evidence = item.get("evidence") or []
        if not evidence:
            problems.append(f"{where}: no evidence given")
        for ev in evidence:
            if "id" in ev:
                if ev["id"] not in ids:
                    problems.append(f"{where}: evidence id {ev['id']!r} does not exist")
            elif "path" in ev and "line" in ev:
                count = lines_in(ev["path"])
                if count < 0:
                    problems.append(f"{where}: evidence file {ev['path']} does not exist")
                elif not (isinstance(ev["line"], int) and 1 <= ev["line"] <= count):
                    problems.append(f"{where}: {ev['path']} has no line {ev['line']}")
            else:
                problems.append(f"{where}: evidence must be {{'id': ...}} or {{'path': ..., 'line': ...}}")
    return problems


def load_documents(paths: list[Path]) -> list[dict[str, Any]]:
    """Artifacts to resolve ids against; a directory contributes every *.json below it."""
    docs = []
    for path in paths:
        files = sorted(path.rglob("*.json")) if path.is_dir() else [path]
        for f in files:
            if f.name.endswith("_annotations.json"):
                continue
            docs.append(json.loads(f.read_text(encoding="utf-8")))
    return docs


def load_and_check(annotations_path: Path, artifact_paths: list[Path] | Path, root: Path | None = None) -> list[str]:
    if isinstance(artifact_paths, Path):
        artifact_paths = [artifact_paths]
    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    docs = load_documents(artifact_paths)
    if root is None:
        root = Path(next((d["meta"]["root"] for d in docs if "meta" in d and "root" in d["meta"]), "."))
    return check_annotations(annotations, {"documents": docs}, root)
