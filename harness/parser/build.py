"""Stage 2: parse every program of an inventory into structure + control flow."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness import __version__
from harness.cobol.source import normalize
from harness.cobol.tokens import WORD, Token, tokenize
from harness.common.issues import IssueLog
from harness.common.textio import read_text
from harness.parser.cfg import build_cfg, statement_count
from harness.parser.data import parse_data
from harness.parser.expand import Expander
from harness.parser.procedure import iter_statements, parse_procedure

SCHEMA_VERSION = "1.0"

SPECIAL_REGISTERS = {
    "RETURN-CODE", "SORT-RETURN", "SORT-CONTROL", "SORT-CORE-SIZE", "SORT-FILE-SIZE", "SORT-MESSAGE",
    "SORT-MODE-SIZE", "TALLY", "WHEN-COMPILED", "LENGTH", "ADDRESS", "DEBUG-ITEM", "SHIFT-IN", "SHIFT-OUT",
    "LINAGE-COUNTER", "XML-CODE", "XML-EVENT", "XML-TEXT", "XML-NTEXT", "XML-NAMESPACE", "JSON-CODE",
    "JSON-STATUS", "JNIENVPTR", "SQLCODE", "SQLSTATE", "SQLCA", "SQLERRM", "SQLERRD", "SQLWARN",
    "SQLERRMC", "SQLERRML", "DFHCOMMAREA", "DFHEIBLK",
}
# Names provided by the CICS translator, the DB2 precompiler and IBM macros.
SYSTEM_PREFIXES = ("EIB", "DFH", "SQL")

INTRINSIC_FUNCTIONS = {
    "ABS", "ACOS", "ANNUITY", "ASIN", "ATAN", "BIT-OF", "BIT-TO-CHAR", "BYTE-LENGTH", "CHAR", "COMBINED-DATETIME",
    "CONTENT-OF", "COS", "CURRENT-DATE", "DATE-OF-INTEGER", "DATE-TO-YYYYMMDD", "DAY-OF-INTEGER",
    "DAY-TO-YYYYDDD", "DISPLAY-OF", "E", "EXP", "EXP10", "FACTORIAL", "FORMATTED-CURRENT-DATE",
    "FORMATTED-DATE", "FORMATTED-DATETIME", "FORMATTED-TIME", "HEX-OF", "HEX-TO-CHAR", "INTEGER",
    "INTEGER-OF-DATE", "INTEGER-OF-DAY", "INTEGER-OF-FORMATTED-DATE", "INTEGER-PART", "LENGTH", "LOG",
    "LOG10", "LOWER-CASE", "MAX", "MEAN", "MEDIAN", "MIDRANGE", "MIN", "MOD", "NATIONAL-OF", "NUMVAL",
    "NUMVAL-C", "NUMVAL-F", "ORD", "ORD-MAX", "ORD-MIN", "PI", "PRESENT-VALUE", "RANDOM", "RANGE", "REM",
    "REVERSE", "SECONDS-FROM-FORMATTED-TIME", "SECONDS-PAST-MIDNIGHT", "SIGN", "SIN", "SQRT",
    "STANDARD-DEVIATION", "SUM", "TAN", "TEST-DATE-YYYYMMDD", "TEST-DAY-YYYYDDD", "TEST-FORMATTED-DATETIME",
    "TEST-NUMVAL", "TEST-NUMVAL-C", "TEST-NUMVAL-F", "TRIM", "ULENGTH", "UPOS", "UPPER-CASE", "USUBSTR",
    "USUPPLEMENTARY", "UUID4", "UVALID", "UWIDTH", "VARIANCE", "WHEN-COMPILED", "YEAR-TO-YYYY",
}


def _division_index(tokens: list[Token], name: str) -> int | None:
    for k in range(len(tokens) - 1):
        if tokens[k].is_word(name) and tokens[k + 1].is_word("DIVISION"):
            return k
    return None


def parse_program(root: Path, program: dict, expander: Expander, file_controls: list[dict]) -> dict[str, Any]:
    rel = program["path"]
    tf = read_text(root / rel)
    raw_tokens = tokenize(normalize(tf.lines).code, rel).tokens
    tokens, expansions = expander.expand(raw_tokens)
    issues: list[dict] = []

    def issue(severity: str, code: str, message: str, file: str | None = None, line: int | None = None,
              **details: Any) -> None:
        item: dict[str, Any] = {"severity": severity, "code": code, "message": message}
        if file:
            item["path"] = file
        if line:
            item["line"] = line
        if details:
            item["details"] = details
        issues.append(item)

    for exp in expansions:
        if exp.status in ("recursive", "too_deep"):
            issue("warning", f"copy_{exp.status}", f"COPY {exp.member} was not expanded ({exp.status})",
                  exp.at_file, exp.at_line)

    env_i = _division_index(tokens, "ENVIRONMENT")
    data_i = _division_index(tokens, "DATA")
    proc_i = _division_index(tokens, "PROCEDURE")
    end_data = proc_i if proc_i is not None else len(tokens)
    data = parse_data(tokens[data_i + 2:end_data] if data_i is not None else [])
    env_words = {t.value for t in tokens[(env_i or 0):(data_i or end_data)] if t.kind == WORD} if env_i is not None else set()

    defined: set[str] = set(SPECIAL_REGISTERS) | env_words
    for e in data.entries:
        if e.name:
            defined.add(e.name)
        occ = e.clauses.get("occurs") or {}
        defined.update(occ.get("indexed_by", []))
    defined.update(f.name for f in data.files)
    defined.update(fc["select"] for fc in file_controls)

    proc = parse_procedure(tokens[proc_i:], defined) if proc_i is not None else None
    cfg, cfg_problems = build_cfg(proc) if proc else ({"entry": None, "nodes": [], "edges": [],
                                                       "dead_paragraphs": [], "recursive_cycles": [],
                                                       "undefined_targets": []}, [])

    # data division findings
    misplaced_by_file: dict[str, list[dict]] = defaultdict(list)
    for m in data.misplaced_names:
        misplaced_by_file[m["file"]].append(m)
    for file, names in misplaced_by_file.items():
        issue("warning", "procedure_code_in_data_division",
              f"{len(names)} paragraph(s) from {file} sit in the DATA DIVISION and are not executable "
              f"({', '.join(n['name'] for n in names)})", file, names[0]["line"],
              paragraphs=[n["name"] for n in names])
    for prob in data.problems:
        if prob["file"] in misplaced_by_file:
            continue      # statements belonging to the misplaced paragraphs reported above
        issue("warning", prob["code"], f"unexpected text in DATA DIVISION: {prob['text']}", prob["file"], prob["line"])

    # procedure findings
    misplaced = {m["name"]: m for m in data.misplaced_names}
    if proc:
        for prob in proc.problems:
            msg = {"unrecognised_statement": f"statement not recognised: {prob.get('text', '')}",
                   "inline_perform_without_end_perform": "inline PERFORM is not closed by END-PERFORM",
                   "unexpected_token": f"unexpected {prob.get('text', '')} at statement level",
                   "paragraph_name_without_period": "paragraph name is not followed by a period"}[prob["code"]]
            issue("warning", prob["code"], msg, prob["file"], prob["line"])
    for prob in cfg_problems:
        code = prob["code"]
        if code == "unreachable_statement":
            issue("info", code, f"statement after {prob['after']} in {prob['paragraph']} can never run",
                  prob["file"], prob["line"])
        elif code == "duplicate_paragraph":
            issue("warning", code, f"paragraph {prob['name']} is defined more than once", prob["file"], prob["line"])
        elif code == "alter_statement":
            issue("warning", code, "ALTER changes GO TO targets at run time", prob["file"], prob["line"])
        else:
            issue("warning", code, f"PERFORM {prob['target']} THRU {prob['thru']} runs backwards",
                  prob["file"], prob["line"])
    for und in cfg["undefined_targets"]:
        where = misplaced.get(und["name"])
        msg = f"{und['type']} target {und['name']} is not a paragraph or section of this program"
        if where:
            msg += f" (it is defined in {where['file']}, which is copied into the DATA DIVISION)"
        issue("warning", "undefined_procedure", msg, und["file"], und["line"], target=und["name"])
    nodes = {n["name"]: n for n in cfg["nodes"]}
    for name in cfg["dead_paragraphs"]:
        n = nodes[name]
        issue("info", "dead_paragraph", f"paragraph {name} is never reached", n["file"], n["line"])
    for cycle in cfg["recursive_cycles"]:
        n = nodes[cycle[0]]
        issue("warning", "recursive_perform", f"PERFORM recursion among {', '.join(cycle)}", n["file"], n["line"],
              paragraphs=cycle)

    # undefined data names and unknown intrinsic functions
    undefined_names: dict[str, list[tuple[str, int]]] = defaultdict(list)
    procedure_names = ({p.name for p in proc.paragraphs} | {s.name for s in proc.sections}) if proc else set()
    statements = [s for p in proc.paragraphs for s in iter_statements(p.statements)] if proc else []
    for s in statements:
        for name in s.get("reads", []) + s.get("writes", []):
            if name in defined or name in procedure_names or name.startswith(SYSTEM_PREFIXES):
                continue
            undefined_names[name].append((s["file"], s["line"]))
    for name, locs in sorted(undefined_names.items()):
        issue("warning", "undefined_data_name",
              f"{name} is used but not defined in the program or its copybooks"
              + (f" ({len(locs)} uses)" if len(locs) > 1 else ""), locs[0][0], locs[0][1], name=name,
              uses=len(locs))
    for k in range(len(tokens) - 1):
        if tokens[k].is_word("FUNCTION") and tokens[k + 1].kind == WORD \
                and tokens[k + 1].value not in INTRINSIC_FUNCTIONS and proc_i is not None and k > proc_i:
            issue("warning", "unknown_intrinsic_function", f"FUNCTION {tokens[k + 1].value} is not an intrinsic function",
                  tokens[k].file, tokens[k].line, function=tokens[k + 1].value)

    files_used = sorted({t.file for t in tokens})
    paragraphs = [{
        "name": p.name, "section": p.section, "line": p.line, "file": p.file, "end_line": p.end_line,
        "statements": p.statements,
    } for p in proc.paragraphs] if proc else []
    perform_edges = sum(1 for e in cfg["edges"] if e["type"] == "PERFORM")
    goto_edges = sum(1 for e in cfg["edges"] if e["type"] == "GO TO")
    return {
        "schema_version": SCHEMA_VERSION,
        "program": {"id": program["id"], "name": program["name"], "path": rel, "kind": program["kind"]},
        "sources": files_used,
        "copy_expansions": [{
            "member": e.member, "kind": e.kind, "status": e.status, "path": e.path, "at_file": e.at_file,
            "at_line": e.at_line, "depth": e.depth, "division": e.division, "replacing": e.replacing,
        } for e in expansions],
        "environment": {"file_control": file_controls},
        "data": {
            "files": [{"kind": f.kind, "name": f.name, "line": f.line, "file": f.file, "clauses": f.clauses,
                       "records": f.records} for f in data.files],
            "entries": [e.to_dict() for e in data.entries],
            "sql_declared_tables": data.sql_declared_tables,
            "misplaced_procedure_names": data.misplaced_names,
        },
        "procedure": {
            "line": proc.line if proc else None,
            "using": proc.using if proc else [],
            "returning": proc.returning if proc else None,
            "sections": [{"name": s.name, "line": s.line, "file": s.file, "paragraphs": s.paragraphs}
                         for s in proc.sections] if proc else [],
            "paragraphs": paragraphs,
        },
        "cfg": cfg,
        "metrics": {
            "source_lines": len(tf.lines),
            "expanded_tokens": len(tokens),
            "copybooks_expanded": sum(1 for e in expansions if e.status == "expanded"),
            "data_entries": len(data.entries),
            "paragraphs": len(paragraphs),
            "sections": len(proc.sections) if proc else 0,
            "statements": statement_count(proc) if proc else 0,
            "perform_edges": perform_edges,
            "goto_edges": goto_edges,
            "dead_paragraphs": len(cfg["dead_paragraphs"]),
            "max_nesting": max((_depth(p["statements"]) for p in paragraphs), default=0),
            "verbs": dict(sorted(Counter(s["verb"] for s in statements).items())),
        },
        "issues": issues,
    }


def _depth(stmts: list[dict]) -> int:
    best = 0
    for s in stmts:
        inner = [s.get(k, []) for k in ("then", "else", "body", "at_end")]
        inner += [b["statements"] for b in s.get("branches", [])]
        inner += [ph["statements"] for ph in s.get("phrases", [])]
        best = max(best, 1 + max((_depth(x) for x in inner), default=0))
    return best


def run_parser(inventory_path: Path, out_dir: Path, programs: list[str] | None = None,
               timestamp: bool = True) -> dict[str, Any]:
    inv_bytes = inventory_path.read_bytes()
    inventory = json.loads(inv_bytes)
    root = Path(inventory["meta"]["root"])
    members = {}
    for c in inventory["copybooks"]:
        members.setdefault(c["member"], c["path"])
    expander = Expander(root, members)
    log = IssueLog()
    out_programs = out_dir / "raw_structure"
    out_programs.mkdir(parents=True, exist_ok=True)

    entries = []
    for prog in inventory["programs"]:
        if programs and prog["name"] not in programs:
            continue
        record = {"id": prog["id"], "name": prog["name"], "path": prog["path"], "kind": prog["kind"]}
        if prog["kind"] == "unknown":
            record.update(status="skipped", reason="no PROGRAM-ID (empty or unparseable file)")
            entries.append(record)
            continue
        try:
            result = parse_program(root, prog, expander, prog.get("file_controls", []))
        except Exception as exc:     # one bad program must not stop the run
            record.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
            log.add("error", "parse_failed", f"{prog['name']} could not be parsed: {exc}", path=prog["path"],
                    subject=prog["id"])
            entries.append(record)
            continue
        name = _output_name(prog)
        (out_programs / name).write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        for item in result["issues"]:
            log.add(item["severity"], item["code"], item["message"], path=item.get("path"),
                    line=item.get("line"), subject=prog["id"], details=item.get("details", {}))
        record.update(status="parsed", output=f"raw_structure/{name}", metrics=result["metrics"],
                      issues=len(result["issues"]))
        entries.append(record)

    issues = [i.to_dict() for i in log.sorted()]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "generator": "harness.parser",
            "generator_version": __version__,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds") if timestamp else None,
            "inventory": str(inventory_path),
            "inventory_sha256": hashlib.sha256(inv_bytes).hexdigest(),
            "root": str(root),
        },
        "stats": {},
        "programs": entries,
        "issues": issues,
    }
    manifest["stats"] = compute_stats(manifest)
    (out_dir / "parser_artifact.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                                                  encoding="utf-8")
    return manifest


def _output_name(prog: dict) -> str:
    return f"{prog['name']}.json"


def compute_stats(manifest: dict[str, Any]) -> dict[str, Any]:
    parsed = [p for p in manifest["programs"] if p["status"] == "parsed"]

    def total(key: str) -> int:
        return sum(p["metrics"][key] for p in parsed)

    return {
        "programs": len(manifest["programs"]),
        "programs_by_status": dict(sorted(Counter(p["status"] for p in manifest["programs"]).items())),
        "paragraphs": total("paragraphs"),
        "sections": total("sections"),
        "statements": total("statements"),
        "data_entries": total("data_entries"),
        "perform_edges": total("perform_edges"),
        "goto_edges": total("goto_edges"),
        "dead_paragraphs": total("dead_paragraphs"),
        "copybooks_expanded": total("copybooks_expanded"),
        "issues": len(manifest["issues"]),
        "issues_by_severity": dict(sorted(Counter(i["severity"] for i in manifest["issues"]).items())),
        "issues_by_code": dict(sorted(Counter(i["code"] for i in manifest["issues"]).items())),
    }
