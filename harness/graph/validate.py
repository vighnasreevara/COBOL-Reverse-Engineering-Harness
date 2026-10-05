"""Self-checks for the Neo4j export: headers, ids, dangling relationships, queries."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


def validate_graph(a: dict[str, Any], out_dir: Path) -> list[str]:
    problems = [f"query: {p}" for p in a.get("query_problems", [])]
    ids: set[str] = set()
    node_count = 0
    for name in a["files"]["nodes"]:
        with (out_dir / "nodes" / name).open(encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh))
        header = rows[0]
        if header[0] != "id:ID" or header[-1] != ":LABEL":
            problems.append(f"nodes/{name}: header must start with id:ID and end with :LABEL")
        for row in rows[1:]:
            if len(row) != len(header):
                problems.append(f"nodes/{name}: row with {len(row)} columns, header has {len(header)}")
                break
            if row[0] in ids:
                problems.append(f"nodes/{name}: duplicate id {row[0]}")
            ids.add(row[0])
            node_count += 1
    rel_count = 0
    for name in a["files"]["rels"]:
        with (out_dir / "rels" / name).open(encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh))
        header = rows[0]
        if header[0] != ":START_ID" or header[-2:] != [":END_ID", ":TYPE"]:
            problems.append(f"rels/{name}: header must be :START_ID ... :END_ID,:TYPE")
        for row in rows[1:]:
            rel_count += 1
            if row[0] not in ids or row[-2] not in ids:
                problems.append(f"rels/{name}: relationship {row[0]} -> {row[-2]} points at a missing node")
                break
    if node_count != a["stats"]["nodes"] or rel_count != a["stats"]["relationships"]:
        problems.append(f"stats say {a['stats']['nodes']} nodes / {a['stats']['relationships']} relationships, "
                        f"files hold {node_count} / {rel_count}")
    if not (out_dir / "import.cypher").is_file():
        problems.append("import.cypher is missing")
    return problems
