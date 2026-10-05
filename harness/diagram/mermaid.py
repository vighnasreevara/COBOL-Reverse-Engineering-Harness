"""Small Mermaid writers with safe ids/labels, and a structural linter."""

from __future__ import annotations

import re

_SAFE = re.compile(r"[^A-Za-z0-9_]")


def node_id(*parts: str) -> str:
    raw = "_".join(p for p in parts if p)
    nid = _SAFE.sub("_", raw)
    return nid if nid[:1].isalpha() else f"n_{nid}"


def label(text: str, limit: int = 60) -> str:
    text = " ".join(str(text).split())
    if len(text) > limit:
        text = text[:limit - 3] + "..."
    return '"' + text.replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;") + '"'


class Flowchart:
    def __init__(self, direction: str = "TD") -> None:
        self.direction = direction
        self.nodes: dict[str, str] = {}
        self.order: list[str] = []
        self.edges: list[str] = []
        self.classes: dict[str, str] = {}
        self.assign: dict[str, str] = {}
        self.groups: dict[str, tuple[str, list[str]]] = {}

    def node(self, nid: str, text: str, shape: str = "box", cls: str | None = None, group: str | None = None) -> str:
        if nid not in self.nodes:
            open_, close = {"box": ("[", "]"), "round": ("(", ")"), "stadium": ("([", "])"),
                            "db": ("[(", ")]"), "diamond": ("{", "}"), "hex": ("{{", "}}"),
                            "sub": ("[[", "]]"), "para": ("[/", "/]")}[shape]
            self.nodes[nid] = f"{nid}{open_}{label(text)}{close}"
            self.order.append(nid)
            if group:
                self.groups.setdefault(group, (group, []))[1].append(nid)
        if cls:
            self.assign[nid] = cls
        return nid

    def edge(self, a: str, b: str, text: str | None = None, style: str = "-->") -> None:
        mid = f"|{label(text, 40)}|" if text else ""
        self.edges.append(f"{a} {style}{mid} {b}")

    def style(self, cls: str, css: str) -> None:
        self.classes[cls] = css

    def subgraph(self, gid: str, title: str) -> str:
        self.groups.setdefault(gid, (title, []))
        self.groups[gid] = (title, self.groups[gid][1])
        return gid

    def render(self, title: str) -> str:
        out = ["---", f"title: {title}", "---", f"flowchart {self.direction}"]
        grouped = {n for _, (_, members) in self.groups.items() for n in members}
        for gid, (title_, members) in self.groups.items():
            if not members:
                continue
            out.append(f"  subgraph {node_id('g', gid)}[{label(title_)}]")
            out += [f"    {self.nodes[m]}" for m in members]
            out.append("  end")
        out += [f"  {self.nodes[n]}" for n in self.order if n not in grouped]
        out += [f"  {e}" for e in self.edges]
        for cls, css in self.classes.items():
            out.append(f"  classDef {cls} {css}")
        for nid, cls in self.assign.items():
            out.append(f"  class {nid} {cls}")
        return "\n".join(out) + "\n"


_DECL = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*(\[\[|\[\(|\[/|\(\[|\{\{|\[|\(|\{)")
_EDGE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s+(-->|-\.->|==>|---|-\.-)(\|[^|]*\|)?\s+([A-Za-z][A-Za-z0-9_]*)\s*$")
TYPES = ("flowchart", "graph", "sequenceDiagram", "erDiagram", "classDiagram", "stateDiagram")


def lint(text: str) -> list[str]:
    """Structural checks that catch the usual generation mistakes."""
    problems: list[str] = []
    lines = [l for l in text.splitlines() if l.strip() and not l.strip().startswith("%%")]
    body = lines
    if lines and lines[0].strip() == "---":
        end = next((k for k in range(1, len(lines)) if lines[k].strip() == "---"), None)
        if end is None:
            return ["front matter is not closed"]
        body = lines[end + 1:]
    if not body or not body[0].strip().startswith(TYPES):
        return [f"first line must declare the diagram type, got {body[0].strip() if body else 'nothing'!r}"]
    kind = body[0].split()[0]
    for line in body:
        if line.count('"') % 2:
            problems.append(f"unbalanced quotes: {line.strip()[:80]}")
    if kind in ("flowchart", "graph"):
        declared: set[str] = set()
        edges: list[tuple[str, str, str]] = []
        for line in body[1:]:
            s = line.strip()
            if s.startswith(("subgraph", "end", "classDef", "class ", "style ", "linkStyle", "direction")):
                if s.startswith("subgraph"):
                    m = re.match(r"subgraph\s+([A-Za-z][A-Za-z0-9_]*)", s)
                    if m:
                        declared.add(m.group(1))
                continue
            m = _EDGE.match(s)
            if m:
                edges.append((m.group(1), m.group(4), s))
                continue
            m = _DECL.match(s)
            if m:
                declared.add(m.group(1))
                continue
            problems.append(f"unrecognised line: {s[:80]}")
        for a, b, s in edges:
            for end_ in (a, b):
                if end_ not in declared:
                    problems.append(f"edge uses undeclared node {end_}: {s[:80]}")
    elif kind == "sequenceDiagram":
        participants = set(re.findall(r"^\s*(?:participant|actor)\s+([A-Za-z][A-Za-z0-9_]*)", text, re.M))
        for a, b in re.findall(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*-[-x>)]+[+-]?\s*([A-Za-z][A-Za-z0-9_]*)\s*:", text, re.M):
            for p in (a, b):
                if p not in participants:
                    problems.append(f"message uses undeclared participant {p}")
        opens = len(re.findall(r"^\s*(alt|opt|loop|par|critical|rect)\b", text, re.M))
        ends = len(re.findall(r"^\s*end\s*$", text, re.M))
        if opens != ends:
            problems.append(f"{opens} blocks opened but {ends} closed")
    return problems
