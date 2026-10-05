"""Stage 4: per-program logic (outline, branches, loops, I/O, data flow, pseudocode)."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness import __version__
from harness.common.issues import IssueLog
from harness.logic.analyze import ProgramLogic
from harness.logic.pseudo import pseudocode
from harness.parser.procedure import iter_statements

SCHEMA_VERSION = "1.0"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _condition_index(data: dict) -> dict[str, dict[str, dict]]:
    """program id -> 88 name -> {field_name, values, id}."""
    by_record: dict[str, list[dict]] = defaultdict(list)
    for c in data["conditions"]:
        by_record[c["record"]].append(c)
    field_names = {f["id"]: f["name"] or f["qualified_name"].split(".")[-1] for f in data["fields"]}
    out: dict[str, dict[str, dict]] = {}
    for p in data["programs"]:
        idx: dict[str, dict] = {}
        for ref in p["records"]:
            for c in by_record.get(ref["record"], []):
                idx.setdefault(c["name"], {"field_name": field_names.get(c["field"], c["field"]),
                                           "values": c["values"], "id": c["id"]})
        out[p["id"]] = idx
    return out


def _related_fields(data: dict) -> dict[str, dict[str, set[str]]]:
    """program id -> field name -> names of its enclosing groups and subordinate fields."""
    records = {r["id"]: r for r in data["records"]}
    out: dict[str, dict[str, set[str]]] = {}
    for p in data["programs"]:
        rel: dict[str, set[str]] = defaultdict(set)
        for ref in p["records"]:
            rec = records.get(ref["record"])
            if not rec:
                continue
            paths = [f["qualified_name"].split(".") for f in rec["fields"]]
            for path in paths:
                name = path[-1]
                rel[name].update(p for p in path[:-1] if p != "FILLER")
            for path in paths:
                for k in range(len(path) - 1):
                    rel[path[k]].add(path[-1])
        out[p["id"]] = {k: {x for x in v if x != "FILLER"} for k, v in rel.items()}
    return out


def run_logic(inventory_path: Path, parser_path: Path, data_path: Path, out_dir: Path,
              timestamp: bool = True) -> dict[str, Any]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    parser = json.loads(parser_path.read_text(encoding="utf-8"))
    data = json.loads(data_path.read_text(encoding="utf-8"))
    conds = _condition_index(data)
    related = _related_fields(data)
    inv_programs = {p["id"]: p for p in inventory["programs"]}
    data_programs = {p["id"]: p for p in data["programs"]}
    flows_by_program: dict[str, list[dict]] = defaultdict(list)
    for f in data["data_flows"]:
        flows_by_program[f["program"]].append({k: f[k] for k in ("entity", "direction", "via")})
    log = IssueLog()
    (out_dir / "program_logic").mkdir(parents=True, exist_ok=True)
    entries = []

    for entry in parser["programs"]:
        if entry["status"] != "parsed":
            continue
        doc = json.loads((parser_path.parent / entry["output"]).read_text(encoding="utf-8"))
        pid = doc["program"]["id"]
        logic = ProgramLogic(doc, conds.get(pid, {}), related.get(pid, {}))
        branches = logic.branches()
        loops = logic.loops()
        io = logic.io()
        inv = inv_programs.get(pid, {})
        paragraphs = []
        for para in doc["procedure"]["paragraphs"]:
            node = next((n for n in doc["cfg"]["nodes"] if n["name"] == para["name"]), {})
            paragraphs.append({
                "name": para["name"], "file": para["file"], "line": para["line"], "end_line": para["end_line"],
                "reachable": node.get("reachable", True),
                "complexity": logic.complexity(para),
                "statements": sum(1 for _ in iter_statements(para["statements"])),
                "performed_by": sorted({e["from"] for e in doc["cfg"]["edges"]
                                        if e["type"] == "PERFORM" and para["name"] in (e.get("range") or [e["to"]])}),
                "pseudocode": pseudocode(para["statements"], conds.get(pid, {})),
            })
        for lp in loops:
            if lp["termination"] == "potential_infinite":
                log.add("warning", "potential_infinite_loop",
                        f"loop {lp['condition']!r} in {lp['paragraph']}: nothing executed inside the loop changes "
                        f"{', '.join(lp['condition_fields']) or 'the tested fields'}",
                        path=lp["file"], line=lp["line"], subject=pid, details={"loop": lp["id"]})
        result = {
            "schema_version": SCHEMA_VERSION,
            "program": {"id": pid, "name": doc["program"]["name"], "path": doc["program"]["path"],
                        "kind": doc["program"]["kind"]},
            "context": {
                "executed_by": inv.get("executed_by", []),
                "called_by": inv.get("called_by", []),
                "using": doc["procedure"]["using"],
                "data_flows": flows_by_program.get(pid, []),
                "files": data_programs.get(pid, {}).get("files", []),
            },
            "entry": doc["cfg"]["entry"],
            "outline": logic.outline(),
            "paragraphs": paragraphs,
            "branches": branches,
            "loops": loops,
            "io": io,
            "data_flow": logic.data_flow(),
            "metrics": {
                "paragraphs": len(paragraphs),
                "statements": doc["metrics"]["statements"],
                "branches": len(branches),
                "loops": len(loops),
                "complexity": 1 + sum(p["complexity"] - 1 for p in paragraphs),
                "max_paragraph_complexity": max((p["complexity"] for p in paragraphs), default=0),
                "sql_statements": len(io["sql"]),
                "cics_commands": len(io["cics"]),
                "calls": len(io["calls"]),
                "file_operations": len(io["files"]),
                "pseudocode_lines": sum(len(p["pseudocode"]) for p in paragraphs),
            },
        }
        name = f"{doc['program']['name']}.json"
        (out_dir / "program_logic" / name).write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n",
                                                 encoding="utf-8")
        entries.append({"id": pid, "name": doc["program"]["name"], "path": doc["program"]["path"],
                        "kind": doc["program"]["kind"], "output": f"program_logic/{name}", "metrics": result["metrics"],
                        "branch_ids": [b["id"] for b in branches], "loop_ids": [lp["id"] for lp in loops]})

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "generator": "harness.logic", "generator_version": __version__,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds") if timestamp else None,
            "parser_sha256": _sha(parser_path), "data_sha256": _sha(data_path),
            "root": inventory["meta"]["root"],
        },
        "stats": {},
        "programs": entries,
        "issues": [i.to_dict() for i in log.sorted()],
    }
    manifest["stats"] = compute_stats(manifest)
    (out_dir / "logic_artifact.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                                                 encoding="utf-8")
    return manifest


def compute_stats(m: dict[str, Any]) -> dict[str, Any]:
    def total(key: str) -> int:
        return sum(p["metrics"][key] for p in m["programs"])

    return {
        "programs": len(m["programs"]),
        "programs_by_kind": dict(sorted(Counter(p["kind"] for p in m["programs"]).items())),
        "branches": total("branches"),
        "loops": total("loops"),
        "sql_statements": total("sql_statements"),
        "cics_commands": total("cics_commands"),
        "calls": total("calls"),
        "pseudocode_lines": total("pseudocode_lines"),
        "most_complex": sorted(((p["name"], p["metrics"]["complexity"]) for p in m["programs"]),
                               key=lambda x: (-x[1], x[0]))[:10],
        "issues": len(m["issues"]),
        "issues_by_code": dict(sorted(Counter(i["code"] for i in m["issues"]).items())),
    }
