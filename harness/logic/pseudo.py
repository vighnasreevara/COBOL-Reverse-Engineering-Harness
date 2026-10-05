"""Deterministic pseudocode from the parser's statement tree.

Every output line keeps the file and line of the statement it came from, so
any sentence a reader quotes can be traced back to the source.
"""

from __future__ import annotations

import re
from typing import Any

_CICS_KEYS = ("PROGRAM", "TRANSID", "MAP", "MAPSET", "FILE", "DATASET", "INTO", "FROM", "RIDFLD", "COMMAREA",
              "QUEUE", "CONDITION")


class Pseudo:
    def __init__(self, conditions: dict[str, dict]) -> None:
        self.conditions = conditions       # 88-level name -> {field, values}
        self.lines: list[dict[str, Any]] = []

    def emit(self, stmt: dict, depth: int, text: str) -> None:
        self.lines.append({"file": stmt["file"], "line": stmt["line"], "depth": depth, "text": text})

    def explain(self, text: str) -> str:
        """Append the meaning of 88-level names used in a condition."""
        notes = []
        for word in sorted(set(re.findall(r"[A-Z0-9][A-Z0-9-]*", text))):
            cond = self.conditions.get(word)
            if cond:
                notes.append(f"{word} means {cond['field_name']} = {_values(cond['values'])}")
        return f"{text}   [{'; '.join(notes)}]" if notes else text

    def block(self, stmts: list[dict], depth: int) -> None:
        for s in stmts:
            self.statement(s, depth)

    def statement(self, s: dict, d: int) -> None:
        verb = s["verb"]
        if verb == "IF":
            self.emit(s, d, f"IF {self.explain(s['condition'])}")
            self.block(s.get("then", []), d + 1)
            if s.get("else"):
                self.emit(s, d, "ELSE")
                self.block(s["else"], d + 1)
            self.emit(s, d, "END-IF")
        elif verb == "EVALUATE":
            self.emit(s, d, f"SELECT CASE {' / '.join(s['subjects'])}")
            for b in s["branches"]:
                label = "OTHERWISE" if b["other"] and not b["conditions"] else \
                    "CASE " + " OR ".join(self.explain(c) for c in b["conditions"]) + (" OR OTHERWISE" if b["other"] else "")
                self.lines.append({"file": b["file"], "line": b["line"], "depth": d + 1, "text": label})
                self.block(b["statements"], d + 2)
            self.emit(s, d, "END-SELECT")
        elif verb == "PERFORM":
            self.perform(s, d)
        elif verb == "SEARCH":
            self.emit(s, d, f"SEARCH {s['text'].split(' ', 1)[1] if ' ' in s['text'] else ''}")
            if s.get("at_end"):
                self.emit(s, d + 1, "WHEN NOT FOUND")
                self.block(s["at_end"], d + 2)
            for b in s.get("branches", []):
                self.lines.append({"file": b["file"], "line": b["line"], "depth": d + 1,
                                   "text": f"WHEN {self.explain(b['conditions'][0])}"})
                self.block(b["statements"], d + 2)
            self.emit(s, d, "END-SEARCH")
        else:
            self.emit(s, d, simple_text(s))
            for ph in s.get("phrases", []):
                self.emit(s, d + 1, f"ON {ph['phrase']}:" if not ph["phrase"].startswith("ON ") else f"{ph['phrase']}:")
                self.block(ph["statements"], d + 2)

    def perform(self, s: dict, d: int) -> None:
        loop = s.get("loop", {})
        kind = loop.get("type", "none")
        target = s.get("target")
        call = f"DO {target}" + (f" THRU {s['thru']}" if s.get("thru") else "") if target else None
        head = None
        if kind == "until":
            cond = self.explain(loop.get("condition") or "")
            head = f"REPEAT UNTIL {cond}" if loop.get("test") == "AFTER" else f"WHILE NOT ({cond})"
        elif kind == "varying":
            v = loop["varying"][0]
            head = f"FOR {v.get('variable')} FROM {v.get('from')} BY {v.get('by')} UNTIL {self.explain(v.get('until') or '')}"
        elif kind == "times":
            head = f"REPEAT {loop.get('count')} TIMES"
        if s.get("inline"):
            if head:
                self.emit(s, d, head)
                self.block(s.get("body", []), d + 1)
                self.emit(s, d, "END-LOOP")
            else:
                self.block(s.get("body", []), d)
        elif head:
            self.emit(s, d, f"{head}: {call}")
        else:
            self.emit(s, d, call or s["text"])


def _values(values: list[dict]) -> str:
    out = []
    for v in values:
        if v["type"] == "range":
            out.append(f"{v['from']} to {v['to']}")
        elif v["type"] == "literal":
            out.append(f"'{v['value']}'")
        else:
            out.append(str(v["value"]))
    return out[0] if len(out) == 1 else "one of " + ", ".join(out)


def simple_text(s: dict) -> str:
    verb = s["verb"]
    text = s["text"]
    rest = text.split(" ", 1)[1] if " " in text else ""
    if verb == "MOVE" and " TO " in text:
        src, dst = rest.rsplit(" TO ", 1)
        return f"SET {', '.join(dst.split())} = {src}"
    if verb == "COMPUTE" and "=" in rest:
        return f"SET {rest}"
    if verb == "CALL":
        args = f" WITH {', '.join(s.get('using', []))}" if s.get("using") else ""
        target = s.get("program") or f"[program named in {s.get('program_variable')}]"
        return f"CALL {target}{args}"
    if verb == "EXEC":
        if s.get("language") == "SQL":
            tables = ", ".join(sorted({t["table"] for t in s.get("tables", [])}))
            return f"SQL {s.get('command')}" + (f" on {tables}" if tables else "")
        if s.get("language") == "CICS":
            opts = s.get("options", {})
            keys = [f"{k.lower()}={opts[k]}" for k in _CICS_KEYS if k in opts and opts[k]]
            return f"CICS {s.get('command')}" + (f" ({', '.join(keys)})" if keys else "")
        return f"{s.get('language')} {s.get('command')}"
    if verb == "GOBACK":
        return "RETURN TO CALLER"
    if verb == "STOP RUN":
        return "END PROGRAM"
    if verb == "EXIT PROGRAM":
        return "RETURN TO CALLER"
    if verb == "EXIT":
        return "(paragraph exit)"
    if verb == "GO TO":
        dep = f" DEPENDING ON {s['depending_on']}" if s.get("depending_on") else ""
        return f"GO TO {' / '.join(s.get('targets', []))}{dep}"
    if verb == "SET" and text.endswith("TO TRUE"):
        return f"SET {rest[:-len(' TO TRUE')]} TO TRUE"
    return text


def pseudocode(statements: list[dict], conditions: dict[str, dict]) -> list[dict[str, Any]]:
    p = Pseudo(conditions)
    p.block(statements, 0)
    return p.lines
