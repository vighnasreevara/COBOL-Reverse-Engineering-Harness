"""Stage 5: business-rule candidates from decisions and calculations.

Every candidate is anchored to one source location and one logic-stage
branch (or a calculation statement). Classification is deterministic and
explainable: each candidate records the signals that produced its category.
Names and plain-English descriptions are added later by the agent as checked
annotations; this stage never invents meaning.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness import __version__
from harness.parser.procedure import iter_statements

SCHEMA_VERSION = "1.0"

_STATUS = re.compile(r"(STATUS|STAT|-FS|FILE-STAT|RESP|SQLCODE|SQLSTATE|RETURN-CODE|-RC\b|EIBRESP|DFHRESP)")
_ERROR_TEXT = re.compile(r"(ERR|MSG|MESSAGE|TEXT|REASON|DESC)")
_LIMIT_NAME = re.compile(r"(MAX|MIN|LIMIT|THRESH|CAP|CEILING|FLOOR|TOLERANCE)")
_COUNTER = re.compile(r"(COUNT|CTR|CNT|TOTAL-RECS|-NUM-READ|RECORDS|-SUB\b|-IDX\b|INDEX)")
_NUMBER = re.compile(r"(?<![\w-])[+-]?\d+(\.\d+)?(?![\w-])")
_ERROR_ACTION = re.compile(r"(ERROR|ABEND|9\d{3}-|FAIL|REJECT)", re.I)
_CALC_VERBS = {"COMPUTE", "ADD", "SUBTRACT", "MULTIPLY", "DIVIDE"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normal(text: str) -> str:
    return " ".join(text.split())


def classify_branch(b: dict) -> tuple[str, int, list[str]]:
    """(category, significance 1-5, signals) for one logic-stage branch."""
    cond = b["condition"]
    actions = [a for o in b["outcomes"] for a in o["actions"]]
    action_text = " ".join(actions)
    signals: list[str] = []
    if b["kind"] == "PHRASE":
        phrase = cond.split(" (")[0]
        if "AT END" in phrase and "NOT" not in phrase:
            return "control_flow", 1, ["end-of-file phrase"]
        if "INVALID KEY" in phrase and "NOT" not in phrase:
            signals.append("invalid-key phrase")
            sig = 3 if re.search(r"'[^']*(NOT FOUND|INVALID|DOES NOT|MISSING)[^']*'", action_text, re.I) else 2
            return "error_handling", sig, signals
        if "SIZE ERROR" in phrase:
            return "calculation", 3, ["size-error phrase"]
        return "error_handling", 1, [f"{phrase} phrase"]
    # CICS translator constants such as DFHRESP(NORMAL) are values, not fields.
    plain = re.sub(r"DFH(RESP|VALUE)\s*\(\s*[\w-]+\s*\)", "0", cond)
    names = [w for w in re.findall(r"[A-Z][A-Z0-9-]*", plain)
             if w not in ("NOT", "AND", "OR", "ZERO", "ZEROS", "ZEROES", "SPACE", "SPACES", "EQUAL", "TO",
                          "GREATER", "LESS", "THAN", "OTHER", "TRUE", "FALSE")]
    # 88-level names stand for the field they test.
    parents = {c["condition"]: c["field"] for c in b["conditions_explained"]}
    names = [parents.get(w, w) for w in names]
    status_fields = [f for f in names if _STATUS.search(f)]
    if names and _STATUS.search(names[0]):
        # the subject being tested is a file status, SQLCODE, CICS response or return code
        signals.append(f"tests status field {names[0]}")
        return "error_handling", 1, signals
    if status_fields and len(status_fields) == len(names):
        signals.append(f"tests status field(s) {', '.join(sorted(set(status_fields)))}")
        return "error_handling", 1, signals
    if re.search(r"(ERROR|ERR-|ABEND|EXCEPTION|^9\d{3})", b["paragraph"]):
        return "error_handling", 2, [f"inside error-handling paragraph {b['paragraph']}"]
    fields = b["fields"]
    if b["kind"] == "WHEN":
        targets = [a for a in actions if a.startswith(("perform", "call", "CICS LINK", "CICS XCTL"))]
        if targets and len(targets) >= len([a for a in actions if a != "continue (no action)"]) / 2:
            signals.append("EVALUATE branch dispatching to " + ", ".join(targets[:2]))
            return "routing", 3 if b["conditions_explained"] or "'" in cond else 2, signals
    sets_error = [a for a in actions if a.startswith("MOVE '") and _ERROR_TEXT.search(a.split(" TO ")[-1])]
    goes_error = [a for a in actions if _ERROR_ACTION.search(a)]
    numbers = [n for n in _NUMBER.findall(cond)]
    comparisons = re.findall(r"(<|>|<=|>=|NOT =|=)", cond)
    if any(_LIMIT_NAME.search(f) for f in fields) or (numbers and re.search(r"[<>]", cond)
                                                       and not all(_COUNTER.search(f) for f in fields)):
        signals.append("comparison against a threshold")
        if sets_error or goes_error:
            signals.append("rejects when exceeded")
        return "limit", 4 if sets_error else 3, signals
    if sets_error or goes_error:
        signals.append("sets an error message" if sets_error else "routes to error handling")
        if b["conditions_explained"]:
            signals.append("uses 88-level names")
        return "validation", 4 if sets_error else 3, signals
    if b["conditions_explained"]:
        signals.append("uses 88-level names")
        state = [a for a in actions if a.upper().startswith("SET ") and a.upper().endswith("TO TRUE")]
        return ("state_change" if state else "decision"), 3, signals
    if any(_COUNTER.search(f) for f in fields) and not comparisons[1:]:
        return "control_flow", 1, ["tests a counter"]
    if comparisons and ("'" in cond or numbers):
        signals.append("compares a field with a literal")
        return "decision", 2, signals
    return "decision", 1, ["no business signal"]


def calculations(doc: dict) -> list[dict]:
    """Arithmetic statements that compute values other than counters."""
    out = []
    for para in doc["procedure"]["paragraphs"]:
        for s in iter_statements(para["statements"]):
            if s["verb"] not in _CALC_VERBS:
                continue
            writes = s.get("writes", [])
            reads = s.get("reads", [])
            is_counter = s["verb"] in ("ADD", "SUBTRACT") and re.match(r"^(ADD|SUBTRACT) 1 (TO|FROM) ", s["text"])
            if is_counter or (writes and all(_COUNTER.search(w) for w in writes)):
                continue
            out.append({"paragraph": para["name"], "file": s["file"], "line": s["line"], "text": s["text"],
                        "writes": writes, "reads": reads, "rounded": "ROUNDED" in s["text"].split(),
                        "size_error": bool(s.get("phrases"))})
    return out


def run_rules(parser_path: Path, logic_path: Path, out_dir: Path, timestamp: bool = True) -> dict[str, Any]:
    parser = json.loads(parser_path.read_text(encoding="utf-8"))
    logic = json.loads(logic_path.read_text(encoding="utf-8"))
    parser_docs = {p["id"]: parser_path.parent / p["output"] for p in parser["programs"] if p["status"] == "parsed"}
    rules: list[dict] = []
    for entry in logic["programs"]:
        doc = json.loads((logic_path.parent / entry["output"]).read_text(encoding="utf-8"))
        pid = entry["id"]
        name = entry["name"]
        reachable = {p["name"]: p["reachable"] for p in doc["paragraphs"]}
        seen: Counter = Counter()

        def rule_id(line: int) -> str:
            seen[line] += 1
            return f"rule:{name}:{line}" + (f".{seen[line]}" if seen[line] > 1 else "")

        for b in doc["branches"]:
            category, significance, signals = classify_branch(b)
            rules.append({
                "id": rule_id(b["line"]), "program": pid, "paragraph": b["paragraph"], "file": b["file"],
                "line": b["line"], "source": b["id"], "kind": b["kind"], "category": category,
                "significance": significance, "business": significance >= 3 and category not in
                ("error_handling", "control_flow"), "signals": signals, "condition": b["condition"],
                "conditions_explained": b["conditions_explained"], "fields": b["fields"],
                "outcomes": b["outcomes"], "reachable": reachable.get(b["paragraph"], True),
            })
        pdoc = json.loads(parser_docs[pid].read_text(encoding="utf-8")) if pid in parser_docs else None
        for calc in calculations(pdoc) if pdoc else []:
            rules.append({
                "id": rule_id(calc["line"]), "program": pid, "paragraph": calc["paragraph"], "file": calc["file"],
                "line": calc["line"], "source": f"statement:{name}:{calc['line']}", "kind": "CALCULATION",
                "category": "calculation", "significance": 4 if calc["rounded"] or "COMPUTE" in calc["text"] else 3,
                "business": True, "signals": ["arithmetic on non-counter fields"]
                + (["ROUNDED"] if calc["rounded"] else []) + (["ON SIZE ERROR"] if calc["size_error"] else []),
                "condition": None, "formula": calc["text"], "conditions_explained": [],
                "fields": sorted(set(calc["writes"]) | set(calc["reads"])),
                "outcomes": [{"when": "always", "actions": [calc["text"]]}],
                "reachable": reachable.get(calc["paragraph"], True),
            })

    # repeated rules: same condition (or formula) and same category in more than one place
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rules:
        key_text = _normal(r["condition"] or r.get("formula", ""))
        if r["business"] and not key_text.endswith("OTHER"):     # WHEN OTHER means nothing on its own
            groups[(r["category"], key_text)].append(r)
    group_list = []
    for k, (key, members) in enumerate(sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])), start=1):
        if len(members) < 2:
            continue
        gid = f"rulegroup:{k}"
        for m in members:
            m["group"] = gid
        group_list.append({"id": gid, "category": key[0], "condition": key[1],
                           "rules": [m["id"] for m in members],
                           "programs": sorted({m["program"] for m in members})})

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "meta": {"generator": "harness.rules", "generator_version": __version__,
                 "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds") if timestamp else None,
                 "logic_sha256": _sha(logic_path), "parser_sha256": _sha(parser_path),
                 "root": logic["meta"]["root"]},
        "stats": {},
        "rules": sorted(rules, key=lambda r: (r["program"], r["file"], r["line"], r["id"])),
        "groups": group_list,
    }
    artifact["stats"] = compute_stats(artifact)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rules_artifact.json").write_text(json.dumps(artifact, indent=1, ensure_ascii=False) + "\n",
                                                 encoding="utf-8")
    return artifact


def compute_stats(a: dict[str, Any]) -> dict[str, Any]:
    rules = a["rules"]
    return {
        "candidates": len(rules),
        "business_rules": sum(1 for r in rules if r["business"]),
        "by_category": dict(sorted(Counter(r["category"] for r in rules).items())),
        "business_by_category": dict(sorted(Counter(r["category"] for r in rules if r["business"]).items())),
        "by_significance": dict(sorted(Counter(str(r["significance"]) for r in rules).items())),
        "unreachable_business_rules": sum(1 for r in rules if r["business"] and not r["reachable"]),
        "repeated_groups": len(a["groups"]),
        "programs_with_business_rules": len({r["program"] for r in rules if r["business"]}),
    }
