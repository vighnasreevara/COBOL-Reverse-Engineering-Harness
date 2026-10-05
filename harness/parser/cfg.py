"""Paragraph-level control flow: PERFORM / GO TO / fall-through, reachability, recursion."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from harness.parser.procedure import ProcedureDivision, iter_statements

TERMINAL = {"GOBACK", "STOP RUN", "EXIT PROGRAM"}
CICS_TERMINAL = {"RETURN", "XCTL", "ABEND"}
_SORT_PROC = re.compile(r"\b(INPUT|OUTPUT) PROCEDURE (?:IS )?([A-Z0-9-]+)(?: (?:THRU|THROUGH) ([A-Z0-9-]+))?")


def is_terminal(stmt: dict) -> bool:
    verb = stmt["verb"]
    if verb in TERMINAL:
        return True
    if verb == "GO TO" and stmt.get("targets") and not stmt.get("depending_on"):
        return True
    return verb == "EXEC" and stmt.get("language") == "CICS" and stmt.get("command") in CICS_TERMINAL


def build_cfg(proc: ProcedureDivision) -> tuple[dict[str, Any], list[dict]]:
    problems: list[dict] = []
    paras = proc.paragraphs
    index: dict[str, int] = {}
    for k, p in enumerate(paras):
        key = p.name
        if key in index:
            problems.append({"code": "duplicate_paragraph", "line": p.line, "file": p.file, "name": p.name})
            key = f"{p.name} OF {p.section}" if p.section else key
        index.setdefault(key, k)
        if p.section:
            index.setdefault(f"{p.name} OF {p.section}", k)
    sections = {s.name: [index[f"{n} OF {s.name}"] if f"{n} OF {s.name}" in index else index[n]
                         for n in s.paragraphs] for s in proc.sections}

    def resolve(name: str) -> list[int] | None:
        if name in index:
            return [index[name]]
        if name in sections:
            return sections[name] or None
        return None

    edges: list[dict] = []
    undefined: list[dict] = []
    perform_targets: dict[int, list[list[int]]] = defaultdict(list)   # paragraph -> performed ranges
    goto_targets: dict[int, list[int]] = defaultdict(list)

    def add_range(src: int, stmt: dict, target: str, thru: str | None, kind: str) -> None:
        start = resolve(target)
        end = resolve(thru) if thru else start
        if start is None or end is None:
            missing = target if start is None else thru
            undefined.append({"name": missing, "line": stmt["line"], "file": stmt["file"], "type": kind,
                              "paragraph": paras[src].name})
            return
        lo, hi = start[0], end[-1]
        if hi < lo:
            problems.append({"code": "perform_thru_backwards", "line": stmt["line"], "file": stmt["file"],
                             "target": target, "thru": thru})
            hi = lo
        rng = list(range(lo, hi + 1))
        perform_targets[src].append(rng)
        edge = {"from": paras[src].name, "to": paras[lo].name, "type": kind, "line": stmt["line"],
                "file": stmt["file"], "target": target}
        if thru:
            edge["thru"] = thru
        if len(rng) > 1:
            edge["range"] = [paras[r].name for r in rng]
        loop = stmt.get("loop", {})
        if loop.get("type", "none") != "none":
            edge["loop"] = loop.get("type")
        edges.append(edge)

    for k, para in enumerate(paras):
        for depth_stmt in _with_depth(para.statements):
            stmt, nested = depth_stmt
            if stmt["verb"] == "PERFORM" and not stmt.get("inline"):
                add_range(k, stmt, stmt["target"], stmt.get("thru"), "PERFORM")
            elif stmt["verb"] == "GO TO":
                for target in stmt.get("targets", []):
                    found = resolve(target)
                    if found is None:
                        undefined.append({"name": target, "line": stmt["line"], "file": stmt["file"],
                                          "type": "GO TO", "paragraph": para.name})
                        continue
                    goto_targets[k].append(found[0])
                    edges.append({"from": para.name, "to": paras[found[0]].name, "type": "GO TO",
                                  "line": stmt["line"], "file": stmt["file"],
                                  "conditional": nested or bool(stmt.get("depending_on"))})
            elif stmt["verb"] == "ALTER":
                problems.append({"code": "alter_statement", "line": stmt["line"], "file": stmt["file"]})
                for pair in stmt.get("alterations", []):
                    found = resolve(pair["proceed_to"])
                    if found is not None:
                        goto_targets[k].append(found[0])
                        edges.append({"from": pair["paragraph"], "to": paras[found[0]].name, "type": "ALTER",
                                      "line": stmt["line"], "file": stmt["file"], "conditional": True})
            elif stmt["verb"] == "EXEC" and stmt.get("handlers"):
                for target in stmt["handlers"]:
                    found = resolve(target)
                    if found is None:
                        undefined.append({"name": target, "line": stmt["line"], "file": stmt["file"],
                                          "type": "CICS HANDLE", "paragraph": para.name})
                        continue
                    goto_targets[k].append(found[0])
                    edges.append({"from": para.name, "to": paras[found[0]].name, "type": "CICS HANDLE",
                                  "line": stmt["line"], "file": stmt["file"], "conditional": True})
            elif stmt["verb"] in ("SORT", "MERGE"):
                for m in _SORT_PROC.finditer(stmt["text"]):
                    add_range(k, stmt, m.group(2), m.group(3), f"{stmt['verb']} {m.group(1)} PROCEDURE")

    # A paragraph ends the flow if any top-level statement does (code after it never runs).
    terminal = [any(is_terminal(s) for s in p.statements) for p in paras]
    for k, para in enumerate(paras):
        for pos, stmt in enumerate(para.statements[:-1]):
            if is_terminal(stmt):
                nxt = para.statements[pos + 1]
                problems.append({"code": "unreachable_statement", "line": nxt["line"], "file": nxt["file"],
                                 "after": stmt["verb"], "paragraph": para.name})
                break
    for k in range(len(paras) - 1):
        if not terminal[k]:
            edges.append({"from": paras[k].name, "to": paras[k + 1].name, "type": "FALL THROUGH",
                          "line": paras[k].end_line or paras[k].line, "file": paras[k].end_file or paras[k].file})

    # Reachability. Fall-through applies to code entered by flow (entry, GO TO);
    # a performed range returns to its caller at the end of the range.
    reached: set[int] = set()
    flow_seen: set[int] = set()
    work: list[tuple[str, int, int]] = []
    if paras:
        work.append(("flow", 0, 0))
    while work:
        mode, a, b = work.pop()
        if mode == "flow":
            k = a
            while k < len(paras) and k not in flow_seen:
                flow_seen.add(k)
                _reach(k, reached, work, perform_targets, goto_targets)
                if terminal[k]:
                    break
                k += 1
        else:
            for k in range(a, b + 1):
                _reach(k, reached, work, perform_targets, goto_targets)

    # PERFORM recursion: cycles in the paragraph perform graph.
    graph: dict[int, set[int]] = defaultdict(set)
    for src, ranges in perform_targets.items():
        for rng in ranges:
            graph[src].update(rng)
    cycles = [[paras[k].name for k in comp] for comp in _cycles(graph, len(paras))]

    nodes = [{"name": p.name, "section": p.section, "line": p.line, "file": p.file,
              "end_line": p.end_line, "reachable": k in reached, "terminal": terminal[k],
              "statements": len(p.statements)} for k, p in enumerate(paras)]
    cfg = {
        "entry": paras[0].name if paras else None,
        "nodes": nodes,
        "edges": edges,
        "dead_paragraphs": [p.name for k, p in enumerate(paras) if k not in reached],
        "recursive_cycles": cycles,
        "undefined_targets": undefined,
    }
    return cfg, problems


def _reach(k: int, reached: set[int], work: list, performs: dict, gotos: dict) -> None:
    if k in reached:
        return
    reached.add(k)
    for rng in performs.get(k, []):
        work.append(("range", rng[0], rng[-1]))
    for target in gotos.get(k, []):
        work.append(("flow", target, target))


def _with_depth(stmts: list[dict], nested: bool = False):
    for s in stmts:
        yield s, nested
        for key in ("then", "else", "body", "at_end"):
            yield from _with_depth(s.get(key, []), True)
        for b in s.get("branches", []):
            yield from _with_depth(b["statements"], True)
        for ph in s.get("phrases", []):
            yield from _with_depth(ph["statements"], True)


def _cycles(graph: dict[int, set[int]], n: int) -> list[list[int]]:
    """Strongly connected components with a cycle (Tarjan, iterative)."""
    index_of: dict[int, int] = {}
    low: dict[int, int] = {}
    on_stack: set[int] = set()
    stack: list[int] = []
    result: list[list[int]] = []
    counter = 0
    for root in range(n):
        if root in index_of:
            continue
        work = [(root, iter(sorted(graph.get(root, ()))))]
        index_of[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index_of:
                    index_of[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(sorted(graph.get(nxt, ())))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index_of[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index_of[node]:
                comp = []
                while True:
                    m = stack.pop()
                    on_stack.discard(m)
                    comp.append(m)
                    if m == node:
                        break
                if len(comp) > 1 or node in graph.get(node, ()):
                    result.append(sorted(comp))
    return result


def statement_count(proc: ProcedureDivision) -> int:
    return sum(sum(1 for _ in iter_statements(p.statements)) for p in proc.paragraphs)
