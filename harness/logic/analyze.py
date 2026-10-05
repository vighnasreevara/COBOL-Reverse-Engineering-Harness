"""Program logic facts from the statement tree: outline, branches, loops, I/O, data flow."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from harness.cobol.words import RESERVED
from harness.parser.procedure import iter_statements

_WORD = re.compile(r"[A-Z][A-Z0-9-]*")
_FILE_VERBS = {"OPEN", "CLOSE", "READ", "WRITE", "REWRITE", "DELETE", "START", "RETURN", "RELEASE"}
MAX_OUTLINE_DEPTH = 8


def words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text or "") if w not in RESERVED]


class ProgramLogic:
    def __init__(self, doc: dict[str, Any], conditions: dict[str, dict],
                 related: dict[str, set[str]] | None = None) -> None:
        self.doc = doc
        self.name = doc["program"]["name"]
        self.conditions = conditions                       # 88 name -> {field_name, values, id}
        self.related = related or {}                       # field -> its groups and subfields
        self.paragraphs = doc["procedure"]["paragraphs"]
        self.order = [p["name"] for p in self.paragraphs]
        self.by_name = {p["name"]: p for p in self.paragraphs}
        self.performs: dict[str, list[list[str]]] = defaultdict(list)
        for e in doc["cfg"]["edges"]:
            if e["type"] == "PERFORM" or e["type"].endswith("PROCEDURE"):
                self.performs[e["from"]].append(e.get("range") or [e["to"]])
        self._closure: dict[str, set[str]] = {}

    # ------------------------------------------------------------ data flow
    def field_of(self, name: str) -> str:
        cond = self.conditions.get(name)
        return cond["field_name"] if cond else name

    def written(self, name: str) -> set[str]:
        """Names whose value changes when `name` is written (the item, its groups and subfields)."""
        base = {name, self.field_of(name)}
        out = set(base)
        for b in base:
            out |= self.related.get(b, set())
        return out

    def direct_writes(self, para: dict) -> set[str]:
        out: set[str] = set()
        for s in iter_statements(para["statements"]):
            for w in s.get("writes", []):
                out |= self.written(w)
        return out

    def writes_closure(self, name: str, stack: frozenset = frozenset()) -> set[str]:
        if name in self._closure:
            return self._closure[name]
        if name in stack or name not in self.by_name:
            return set()
        out = set(self.direct_writes(self.by_name[name]))
        for rng in self.performs.get(name, []):
            for target in rng:
                out |= self.writes_closure(target, stack | {name})
        self._closure[name] = out
        return out

    def statements_writes(self, stmts: list[dict]) -> set[str]:
        out: set[str] = set()
        for s in iter_statements(stmts):
            for w in s.get("writes", []):
                out |= self.written(w)
            if s["verb"] == "PERFORM" and not s.get("inline") and s.get("target"):
                for rng in self._ranges(s):
                    for p in rng:
                        out |= self.writes_closure(p)
        return out

    def _ranges(self, s: dict) -> list[list[str]]:
        target, thru = s["target"], s.get("thru")
        if target not in self.by_name:
            sec = [p["name"] for p in self.paragraphs if p["section"] == target]
            return [sec] if sec else []
        if thru and thru in self.by_name:
            lo, hi = self.order.index(target), self.order.index(thru)
            return [self.order[lo:hi + 1]] if hi >= lo else [[target]]
        return [[target]]

    # ------------------------------------------------------------- branches
    def actions(self, stmts: list[dict], limit: int = 10) -> list[str]:
        """What a branch does: its top-level statements, nested decisions shown as 'if ...'."""
        out: list[str] = []
        for s in stmts:
            v = s["verb"]
            if v == "PERFORM" and s.get("target"):
                text = f"perform {s['target']}" + (f" thru {s['thru']}" if s.get("thru") else "")
                if s.get("loop", {}).get("type", "none") != "none":
                    text += f" ({s['loop']['type']} {s['loop'].get('condition') or s['loop'].get('count') or ''})".rstrip()
            elif v == "PERFORM":
                text = f"loop {s.get('loop', {}).get('type', '')} {s.get('loop', {}).get('condition') or ''}".strip()
            elif v == "IF":
                text = f"if {s['condition']} ..."
            elif v == "EVALUATE":
                text = f"evaluate {' / '.join(s['subjects'])} ..."
            elif v == "CALL":
                text = f"call {s.get('program') or s.get('program_variable')}"
            elif v == "EXEC" and s.get("language") == "CICS":
                prog = s.get("options", {}).get("PROGRAM")
                text = f"CICS {s.get('command')}" + (f" {prog}" if prog else "")
            elif v == "EXEC" and s.get("language") == "SQL":
                tables = ",".join(sorted({t["table"] for t in s.get("tables", [])}))
                text = f"SQL {s.get('command')}" + (f" {tables}" if tables else "")
            elif v in ("GOBACK", "STOP RUN", "EXIT PROGRAM"):
                text = v.lower()
            elif v == "GO TO":
                text = f"go to {'/'.join(s.get('targets', []))}"
            elif v == "CONTINUE":
                text = "continue (no action)"
            elif v in ("EXIT", "NEXT SENTENCE"):
                continue
            else:
                text = s["text"][:70]
            out.append(text)
            if len(out) >= limit:
                out.append("...")
                break
        return out

    def explain(self, text: str) -> list[dict]:
        out = []
        for w in sorted(set(words(text))):
            c = self.conditions.get(w)
            if c:
                out.append({"condition": w, "field": c["field_name"], "values": c["values"], "id": c["id"]})
        return out

    def branches(self) -> list[dict]:
        found: list[dict] = []

        def add(kind: str, para: str, s: dict, condition: str, outcomes: list[dict], line=None, file=None) -> None:
            found.append({"id": f"branch:{self.name}:B{len(found) + 1}", "kind": kind, "paragraph": para,
                          "file": file or s["file"], "line": line or s["line"], "condition": condition,
                          "fields": sorted({self.field_of(w) for w in words(condition)}),
                          "conditions_explained": self.explain(condition), "outcomes": outcomes})

        for para in self.paragraphs:
            for s in iter_statements(para["statements"]):
                v = s["verb"]
                if v == "IF":
                    outcomes = [{"when": "true", "actions": self.actions(s.get("then", []))}]
                    outcomes.append({"when": "false", "actions": self.actions(s.get("else", []))})
                    add("IF", para["name"], s, s["condition"], outcomes)
                elif v == "EVALUATE":
                    subject = " / ".join(s["subjects"])
                    for b in s["branches"]:
                        label = "OTHER" if b["other"] and not b["conditions"] else " OR ".join(b["conditions"])
                        cond = label if subject == "TRUE" else f"{subject} = {label}"
                        add("WHEN", para["name"], s, cond, [{"when": "match", "actions": self.actions(b["statements"])}],
                            b["line"], b["file"])
                elif v == "SEARCH":
                    for b in s.get("branches", []):
                        add("SEARCH WHEN", para["name"], s, b["conditions"][0],
                            [{"when": "found", "actions": self.actions(b["statements"])}], b["line"], b["file"])
                for ph in s.get("phrases", []):
                    add("PHRASE", para["name"], s, f"{ph['phrase']} ({s['text'][:50]})",
                        [{"when": "phrase", "actions": self.actions(ph["statements"])}])
        return found

    # ---------------------------------------------------------------- loops
    def loops(self) -> list[dict]:
        found: list[dict] = []
        for para in self.paragraphs:
            for s in iter_statements(para["statements"]):
                if s["verb"] != "PERFORM":
                    continue
                loop = s.get("loop", {})
                kind = loop.get("type", "none")
                if kind == "none":
                    continue
                if s.get("inline"):
                    body_writes = self.statements_writes(s.get("body", []))
                    performed = None
                else:
                    body_writes = set()
                    for rng in self._ranges(s) if s.get("target") else []:
                        for p in rng:
                            body_writes |= self.writes_closure(p)
                    performed = s.get("target")
                condition = loop.get("condition") or ""
                fields = sorted({self.field_of(w) for w in words(condition)} | {w for w in words(condition)
                                                                                 if w in self.conditions})
                updated = sorted(set(fields) & body_writes)
                if kind in ("times", "varying"):
                    termination = "counted"
                elif not condition:
                    termination = "unknown"
                elif updated:
                    termination = "condition_updated_in_loop"
                else:
                    termination = "potential_infinite"
                found.append({"id": f"loop:{self.name}:L{len(found) + 1}", "paragraph": para["name"],
                              "file": s["file"], "line": s["line"], "type": kind, "inline": bool(s.get("inline")),
                              "performs": performed, "condition": condition, "test": loop.get("test", "BEFORE"),
                              "condition_fields": fields, "fields_updated_in_loop": updated,
                              "termination": termination})
        return found

    # ------------------------------------------------------------------- I/O
    def io(self) -> dict[str, list[dict]]:
        files, sql, cics, calls = [], [], [], []
        for para in self.paragraphs:
            for s in iter_statements(para["statements"]):
                v = s["verb"]
                base = {"paragraph": para["name"], "file": s["file"], "line": s["line"]}
                if v in _FILE_VERBS:
                    parts = s["text"].split()
                    files.append({**base, "verb": v, "operands": parts[1:6]})
                elif v == "EXEC" and s.get("language") == "SQL":
                    sql.append({**base, "command": s.get("command"), "tables": s.get("tables", []),
                                "host_reads": s.get("reads", []), "host_writes": s.get("writes", [])})
                elif v == "EXEC" and s.get("language") == "CICS":
                    cics.append({**base, "command": s.get("command"), "options": s.get("options", {})})
                elif v == "CALL":
                    calls.append({**base, "program": s.get("program"), "program_variable": s.get("program_variable"),
                                  "using": s.get("using", [])})
        return {"files": files, "sql": sql, "cics": cics, "calls": calls}

    # --------------------------------------------------------------- outline
    def outline(self) -> list[dict]:
        if not self.paragraphs:
            return []
        expanded: set[str] = set()
        return self._outline_paragraph(self.order[0], [], expanded, 0)

    def _outline_paragraph(self, name: str, guards: list[str], expanded: set[str], depth: int) -> list[dict]:
        para = self.by_name.get(name)
        if para is None:
            return []
        expanded.add(name)
        nodes: list[dict] = []
        self._outline_block(para["statements"], guards, expanded, depth, nodes)
        return nodes

    def _outline_block(self, stmts: list[dict], guards: list[str], expanded: set[str], depth: int,
                       nodes: list[dict]) -> None:
        for s in stmts:
            v = s["verb"]
            loc = {"file": s["file"], "line": s["line"]}
            if v == "IF":
                self._outline_block(s.get("then", []), guards + [s["condition"]], expanded, depth, nodes)
                if s.get("else"):
                    self._outline_block(s["else"], guards + [f"NOT ({s['condition']})"], expanded, depth, nodes)
            elif v == "EVALUATE":
                subject = " / ".join(s["subjects"])
                for b in s["branches"]:
                    label = "OTHER" if b["other"] and not b["conditions"] else " OR ".join(b["conditions"])
                    g = label if subject == "TRUE" else f"{subject} = {label}"
                    self._outline_block(b["statements"], guards + [g], expanded, depth, nodes)
            elif v == "PERFORM" and not s.get("inline") and s.get("target"):
                node = {"kind": "perform", "target": s["target"], **loc, "when": guards}
                if s.get("thru"):
                    node["thru"] = s["thru"]
                if s.get("loop", {}).get("type", "none") != "none":
                    node["loop"] = {k: v for k, v in s["loop"].items() if k in ("type", "condition", "count", "test")}
                ranges = self._ranges(s)
                if not ranges:
                    node["undefined"] = True
                elif s["target"] in expanded or depth >= MAX_OUTLINE_DEPTH:
                    node["expanded_elsewhere"] = True
                else:
                    children: list[dict] = []
                    for p in ranges[0]:
                        children += self._outline_paragraph(p, [], expanded, depth + 1)
                    node["children"] = children
                nodes.append(node)
            elif v == "PERFORM" and s.get("inline"):
                inner: list[dict] = []
                self._outline_block(s.get("body", []), [], expanded, depth, inner)
                if inner:
                    nodes.append({"kind": "loop", **loc, "when": guards,
                                  "loop": {k: v for k, v in s["loop"].items() if k in ("type", "condition", "count", "test")},
                                  "children": inner})
            elif v == "CALL":
                nodes.append({"kind": "call", "target": s.get("program") or f"[{s.get('program_variable')}]",
                              **loc, "when": guards})
            elif v == "EXEC" and s.get("language") == "CICS" and s.get("command") in ("LINK", "XCTL", "RETURN", "SEND",
                                                                                         "RECEIVE", "START", "ABEND"):
                opts = s.get("options", {})
                target = opts.get("PROGRAM") or opts.get("MAP") or opts.get("TRANSID")
                nodes.append({"kind": f"cics {s['command'].lower()}", "target": target, **loc, "when": guards})
            elif v in ("GOBACK", "STOP RUN", "EXIT PROGRAM"):
                nodes.append({"kind": "end", "target": v, **loc, "when": guards})
            elif v == "GO TO":
                nodes.append({"kind": "goto", "target": "/".join(s.get("targets", [])), **loc, "when": guards})
            for ph in s.get("phrases", []):
                self._outline_block(ph["statements"], guards + [f"{ph['phrase']} on {s['verb']}"], expanded, depth, nodes)

    # --------------------------------------------------------------- metrics
    def complexity(self, para: dict) -> int:
        n = 1
        for s in iter_statements(para["statements"]):
            if s["verb"] == "IF":
                n += 1
            elif s["verb"] == "EVALUATE":
                n += sum(1 for b in s["branches"] if not b["other"])
            elif s["verb"] == "SEARCH":
                n += len(s.get("branches", []))
            elif s["verb"] == "PERFORM" and s.get("loop", {}).get("type") in ("until", "varying"):
                n += 1
            n += len(s.get("phrases", []))
        return n

    def data_flow(self) -> list[dict]:
        out = []
        for para in self.paragraphs:
            reads, writes = set(), set()
            for s in iter_statements(para["statements"]):
                reads.update(s.get("reads", []))
                writes.update(s.get("writes", []))
            out.append({"paragraph": para["name"], "reads": sorted(reads), "writes": sorted(writes)})
        return out
