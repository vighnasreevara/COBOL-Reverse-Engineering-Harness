"""Stage 8: Neo4j export (neo4j-admin CSVs + Cypher script + checked query library)."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness import __version__

SCHEMA_VERSION = "1.0"
BATCH = 200

# label -> (key property, property columns)
NODE_TYPES: dict[str, tuple[str, list[str]]] = {
    "Program": ("id", ["name", "kind", "path", "entry_point", "complexity", "paragraphs", "summary"]),
    "Copybook": ("id", ["name", "path", "content_kind"]),
    "Job": ("id", ["name", "path"]),
    "Transaction": ("id", ["name", "path"]),
    "Paragraph": ("id", ["name", "program", "path", "line", "reachable", "complexity"]),
    "Record": ("id", ["name", "size", "source", "path", "line", "description"]),
    "Field": ("id", ["name", "qualified_name", "category", "picture", "usage", "size", "offset"]),
    "Condition": ("id", ["name", "values"]),
    "DataStore": ("id", ["name", "kind", "description"]),
    "Rule": ("id", ["category", "significance", "business", "reachable", "condition", "name", "path", "line"]),
    "Map": ("id", ["name", "mapset"]),
    "Gap": ("id", ["severity", "category", "title", "action"]),
}
REL_TYPES = {"STARTS", "RUNS", "CALLS", "LINKS", "XCTL", "COPIES", "INCLUDES", "HAS_PARAGRAPH", "PERFORMS",
             "GOES_TO", "DEFINES", "HAS_FIELD", "HAS_CONDITION", "READS", "WRITES", "UPDATES", "DELETES",
             "IN_PARAGRAPH", "TESTS", "REFERENCES", "USES_MAP", "AFFECTS"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.rels: list[dict[str, Any]] = []
        self._seen: set[tuple] = set()

    def node(self, label: str, nid: str, **props: Any) -> str:
        if nid not in self.nodes:
            self.nodes[nid] = {"label": label, "id": nid, **{k: v for k, v in props.items() if v is not None}}
        else:
            for k, v in props.items():
                if v is not None:
                    self.nodes[nid].setdefault(k, v)
        return nid

    def rel(self, a: str, rtype: str, b: str, **props: Any) -> None:
        assert rtype in REL_TYPES, rtype
        key = (a, rtype, b)
        if key in self._seen or a not in self.nodes or b not in self.nodes:
            return
        self._seen.add(key)
        self.rels.append({"from": a, "type": rtype, "to": b, **{k: v for k, v in props.items() if v is not None}})


def build_graph(out_root: Path) -> Graph:
    load = lambda rel: json.loads((out_root / rel).read_text(encoding="utf-8"))  # noqa: E731
    inv = load("inventory/inventory_artifact.json")
    data = load("data/data_artifact.json")
    logic = load("logic/logic_artifact.json")
    rules = load("rules/rules_artifact.json")
    synth = load("final_report/synthesis_artifact.json") if (out_root / "final_report/synthesis_artifact.json").is_file() else {"gaps": []}
    ann: dict[str, dict[str, str]] = defaultdict(dict)
    for rel in ("data/data_annotations.json", "logic/logic_annotations.json", "rules/rules_annotations.json"):
        if (out_root / rel).is_file():
            for item in load(rel)["items"]:
                ann[item["target"]][item["kind"]] = item["text"]
    g = Graph()
    metrics = {p["id"]: p["metrics"] for p in logic["programs"]}

    for p in inv["programs"]:
        m = metrics.get(p["id"], {})
        g.node("Program", p["id"], name=p["name"], kind=p["kind"], path=p["path"], entry_point=p["entry_point"],
               complexity=m.get("complexity"), paragraphs=m.get("paragraphs"), summary=ann[p["id"]].get("summary"))
    for c in inv["copybooks"]:
        g.node("Copybook", c["id"], name=c["name"], path=c["path"], content_kind=c["content_kind"])
    for j in inv["jcl"]["jobs"]:
        g.node("Job", j["id"], name=j["name"], path=j["path"])
    for r in inv["cics"]["resources"]:
        if r["type"] == "TRANSACTION":
            g.node("Transaction", r["id"], name=r["name"], path=r["path"])
    for ms in inv["bms"]["mapsets"]:
        for m in ms["maps"]:
            g.node("Map", m["id"], name=m["name"], mapset=ms["name"])
    for e in data["entities"]:
        g.node("DataStore", e["id"], name=e["name"], kind=e["kind"], description=ann[e["id"]].get("description"))
    rel_of = {"JCL_EXEC": "RUNS", "JCL_RUN": "RUNS", "CSD_TRANSACTION": "STARTS", "STATIC_CALL": "CALLS",
              "DYNAMIC_CALL": "CALLS", "CICS_LINK": "LINKS", "CICS_XCTL": "XCTL", "COPY": "COPIES",
              "SQL_INCLUDE": "INCLUDES", "CICS_MAP": "USES_MAP", "DB2_FOREIGN_KEY": "REFERENCES"}
    for e in inv["graph"]["edges"]:
        rtype = rel_of.get(e["type"])
        if not rtype:
            continue
        if e["to"] not in g.nodes:
            kind = e["to"].split(":", 1)[0]
            label = {"program": "Program", "copybook": "Copybook", "map": "Map", "table": "DataStore"}.get(kind)
            if label is None:
                continue
            g.node(label, e["to"], name=e["to"].split(":", 1)[1], missing=True)
        g.rel(e["from"], rtype, e["to"], count=e["count"], resolution=e["resolution"])
    for f in data["data_flows"]:
        rtype = {"read": "READS", "write": "WRITES", "update": "UPDATES", "delete": "DELETES"}.get(f["direction"])
        if rtype:
            g.rel(f["program"], rtype, f["entity"], via=f["via"], path=f["file"], line=f["line"])
    for r in data["records"]:
        g.node("Record", r["id"], name=r["name"], size=r["size"], source=r["source"], path=r["file"], line=r["line"],
               description=ann[r["id"]].get("description"))
        owner = f"copybook:{r['copybook']}" if r["copybook"] else None
        for use in r["used_by"]:
            g.rel(owner or use["program"], "DEFINES", r["id"])
        for f in r["fields"]:
            if f["level"] == 1:
                continue
            g.node("Field", f["id"], name=f["name"], qualified_name=f["qualified_name"], category=f["category"],
                   picture=f["picture"], usage=f["usage"], size=f["size"], offset=f["offset"])
            g.rel(r["id"], "HAS_FIELD", f["id"])
    for c in data["conditions"]:
        g.node("Condition", c["id"], name=c["name"],
               values=", ".join(str(v.get("value", f"{v.get('from')}-{v.get('to')}")) for v in c["values"]))
        g.rel(c["field"], "HAS_CONDITION", c["id"])
    for entry in logic["programs"]:
        doc = json.loads((out_root / "logic" / entry["output"]).read_text(encoding="utf-8"))
        for p in doc["paragraphs"]:
            pid = f"paragraph:{entry['name']}:{p['name']}"
            g.node("Paragraph", pid, name=p["name"], program=entry["name"], path=p["file"], line=p["line"],
                   reachable=p["reachable"], complexity=p["complexity"])
            g.rel(entry["id"], "HAS_PARAGRAPH", pid)
        for p in doc["paragraphs"]:
            for caller in p["performed_by"]:
                g.rel(f"paragraph:{entry['name']}:{caller}", "PERFORMS", f"paragraph:{entry['name']}:{p['name']}")
    for r in rules["rules"]:
        if not r["business"]:
            continue
        g.node("Rule", r["id"], category=r["category"], significance=r["significance"], business=r["business"],
               reachable=r["reachable"], condition=r["condition"] or r.get("formula"),
               name=ann[r["id"]].get("name"), path=r["file"], line=r["line"])
        program = r["program"].split(":", 1)[1]
        g.rel(r["id"], "IN_PARAGRAPH", f"paragraph:{program}:{r['paragraph']}")
    for gap in synth["gaps"]:
        g.node("Gap", gap["id"], severity=gap["severity"], category=gap["category"], title=gap["title"],
               action=gap["action"])
        if gap["subject"] in g.nodes:
            g.rel(gap["id"], "AFFECTS", gap["subject"])
    return g


# ------------------------------------------------------------------ export
def _value(v: Any) -> Any:
    if isinstance(v, bool):
        return "true" if v else "false"
    return "" if v is None else v


def _type_suffix(values: list[Any]) -> str:
    present = [v for v in values if v is not None]
    if present and all(isinstance(v, bool) for v in present):
        return ":boolean"
    if present and all(isinstance(v, int) and not isinstance(v, bool) for v in present):
        return ":int"
    return ""


def write_csv(g: Graph, out_dir: Path) -> dict[str, list[str]]:
    (out_dir / "nodes").mkdir(parents=True, exist_ok=True)
    (out_dir / "rels").mkdir(parents=True, exist_ok=True)
    files: dict[str, list[str]] = {"nodes": [], "rels": []}
    by_label: dict[str, list[dict]] = defaultdict(list)
    for n in g.nodes.values():
        by_label[n["label"]].append(n)
    for label, nodes in sorted(by_label.items()):
        props = NODE_TYPES[label][1] + (["missing"] if any("missing" in n for n in nodes) else [])
        header = ["id:ID"] + [f"{p}{_type_suffix([n.get(p) for n in nodes])}" for p in props] + [":LABEL"]
        path = out_dir / "nodes" / f"{label}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            for n in sorted(nodes, key=lambda x: x["id"]):
                w.writerow([n["id"]] + [_value(n.get(p)) for p in props] + [label])
        files["nodes"].append(path.name)
    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in g.rels:
        by_type[r["type"]].append(r)
    for rtype, rels in sorted(by_type.items()):
        props = sorted({k for r in rels for k in r if k not in ("from", "to", "type")})
        header = [":START_ID"] + [f"{p}{_type_suffix([r.get(p) for r in rels])}" for p in props] + [":END_ID", ":TYPE"]
        path = out_dir / "rels" / f"{rtype}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            for r in rels:
                w.writerow([r["from"]] + [_value(r.get(p)) for p in props] + [r["to"], rtype])
        files["rels"].append(path.name)
    return files


def _cypher_literal(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ") + "'"


def _map(d: dict) -> str:
    return "{" + ", ".join(f"`{k}`: {_cypher_literal(v)}" for k, v in d.items() if v is not None) + "}"


def write_cypher(g: Graph, out_dir: Path, system: str) -> None:
    lines = ["// COBOL reverse engineering graph: " + system,
             "// Run in Neo4j Browser (enable multi-statement) or with cypher-shell -f import.cypher", ""]
    for label in sorted({n["label"] for n in g.nodes.values()}):
        lines.append(f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE;")
    lines.append("")
    by_label: dict[str, list[dict]] = defaultdict(list)
    for n in g.nodes.values():
        by_label[n["label"]].append({k: v for k, v in n.items() if k != "label"})
    for label, nodes in sorted(by_label.items()):
        for k in range(0, len(nodes), BATCH):
            chunk = nodes[k:k + BATCH]
            lines.append(f"UNWIND [{', '.join(_map(n) for n in chunk)}] AS row")
            lines.append(f"MERGE (n:{label} {{id: row.id}}) SET n += row;")
    lines.append("")
    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in g.rels:
        by_type[r["type"]].append(r)
    labels = {nid: n["label"] for nid, n in g.nodes.items()}
    for rtype, rels in sorted(by_type.items()):
        pairs = defaultdict(list)
        for r in rels:
            pairs[(labels[r["from"]], labels[r["to"]])].append(r)
        for (la, lb), items in sorted(pairs.items()):
            for k in range(0, len(items), BATCH):
                chunk = [{"a": r["from"], "b": r["to"], "p": {x: y for x, y in r.items() if x not in ("from", "to", "type")}}
                         for r in items[k:k + BATCH]]
                rows = ", ".join("{a: %s, b: %s, p: %s}" % (_cypher_literal(c["a"]), _cypher_literal(c["b"]), _map(c["p"]))
                                 for c in chunk)
                lines.append(f"UNWIND [{rows}] AS row")
                lines.append(f"MATCH (a:{la} {{id: row.a}}) MATCH (b:{lb} {{id: row.b}}) "
                             f"MERGE (a)-[r:{rtype}]->(b) SET r += row.p;")
    (out_dir / "import.cypher").write_text("\n".join(lines) + "\n", encoding="utf-8")


QUERIES = [
    ("Entry points and the programs they start",
     "MATCH (e)-[:RUNS|STARTS]->(p:Program) RETURN labels(e)[0] AS kind, e.name AS entry, p.name AS program ORDER BY kind, entry"),
    ("Everything a transaction reaches (programs via LINK/CALL)",
     "MATCH path = (t:Transaction {name: $transaction})-[:STARTS|LINKS|CALLS|XCTL*1..5]->(p:Program) RETURN DISTINCT p.name, length(path) ORDER BY length(path)"),
    ("Programs that call a missing program",
     "MATCH (p:Program)-[:CALLS|LINKS|XCTL]->(m:Program {missing: true}) RETURN p.name, m.name"),
    ("Impact of changing a copybook",
     "MATCH (c:Copybook {name: $copybook})<-[:COPIES]-(p:Program) OPTIONAL MATCH (p)<-[:RUNS|STARTS]-(e) RETURN p.name, collect(DISTINCT e.name) AS entry_points"),
    ("Who reads and writes each data store",
     "MATCH (p:Program)-[r:READS|WRITES|UPDATES|DELETES]->(d:DataStore) RETURN d.name, d.kind, type(r) AS access, collect(p.name) AS programs ORDER BY d.name"),
    ("Data lineage: stores written by one program and read by another",
     "MATCH (w:Program)-[:WRITES|UPDATES]->(d:DataStore)<-[:READS]-(r:Program) WHERE w <> r RETURN w.name AS producer, d.name AS store, r.name AS consumer"),
    ("Business rules of a program",
     "MATCH (r:Rule)-[:IN_PARAGRAPH]->(para:Paragraph {program: $program}) RETURN r.id, r.name, r.category, r.reachable, para.name ORDER BY r.line"),
    ("Business rules that never run",
     "MATCH (r:Rule {reachable: false})-[:IN_PARAGRAPH]->(para:Paragraph) RETURN para.program, para.name, r.id, r.name"),
    ("Unreachable paragraphs",
     "MATCH (p:Program)-[:HAS_PARAGRAPH]->(para:Paragraph {reachable: false}) RETURN p.name, para.name, para.line"),
    ("Most complex programs",
     "MATCH (p:Program) WHERE p.complexity IS NOT NULL RETURN p.name, p.kind, p.complexity ORDER BY p.complexity DESC LIMIT 10"),
    ("Fields of a record with their 88-level values",
     "MATCH (r:Record {name: $record})-[:HAS_FIELD]->(f:Field) OPTIONAL MATCH (f)-[:HAS_CONDITION]->(c:Condition) RETURN f.qualified_name, f.picture, f.size, f.offset, collect(c.name + '=' + c.values) ORDER BY f.offset"),
    ("Where a field's record is used",
     "MATCH (f:Field {name: $field})<-[:HAS_FIELD]-(r:Record)<-[:DEFINES]-(owner) RETURN f.qualified_name, r.name, labels(owner)[0], owner.name"),
    ("High-severity gaps and what they affect",
     "MATCH (g:Gap {severity: 'high'}) OPTIONAL MATCH (g)-[:AFFECTS]->(x) RETURN g.category, g.title, x.name ORDER BY g.category"),
    ("Paragraph call chain inside a program",
     "MATCH path = (a:Paragraph {program: $program})-[:PERFORMS*1..4]->(b:Paragraph) WHERE NOT ()-[:PERFORMS]->(a) RETURN [n IN nodes(path) | n.name] AS chain"),
    ("Screens used by online programs",
     "MATCH (p:Program)-[:USES_MAP]->(m:Map) RETURN p.name, m.mapset, m.name, coalesce(m.missing, false) AS missing"),
    ("Foreign keys between tables",
     "MATCH (a:DataStore)-[:REFERENCES]->(b:DataStore) RETURN a.name, b.name"),
]


def check_queries() -> list[str]:
    """Every label, relationship type and property used in a query is part of the export schema."""
    problems = []
    for title, q in QUERIES:
        for lab in re.findall(r"\(\w*:(\w+)", q):
            if lab not in NODE_TYPES:
                problems.append(f"{title}: label {lab} is not part of the export")
        for group in re.findall(r"\[\w*:([\w|]+)[*\]]", q):
            for rt in group.split("|"):
                if rt not in REL_TYPES:
                    problems.append(f"{title}: relationship {rt} is not part of the export")
        for lab, body in re.findall(r"\(\w*:(\w+)\s*\{([^}]*)\}", q):
            allowed = {NODE_TYPES[lab][0], "missing", *NODE_TYPES[lab][1]} if lab in NODE_TYPES else set()
            for prop in re.findall(r"(\w+)\s*:", body):
                if prop not in allowed:
                    problems.append(f"{title}: {lab} has no property {prop}")
    return problems


def write_library(out_dir: Path, system: str) -> None:
    lines = [f"# Cypher query library: {system}", "",
             "Every label, relationship type and property in these queries is checked against the export "
             "when the graph stage runs. Queries with `$name` parameters need them set first, e.g. "
             "`:param program => 'MYPROG'` in Neo4j Browser.", ""]
    for title, q in QUERIES:
        lines += [f"## {title}", "", "```cypher", q, "```", ""]
    (out_dir / "cypher_library.md").write_text("\n".join(lines), encoding="utf-8")


def write_readme(out_dir: Path, files: dict[str, list[str]], system: str) -> None:
    node_args = " \\\n  ".join(f"--nodes=import/nodes/{f}" for f in files["nodes"])
    rel_args = " \\\n  ".join(f"--relationships=import/rels/{f}" for f in files["rels"])
    text = f"""# Loading the {system} graph into Neo4j

## Option A: Cypher script (small and medium systems)

1. Start a Neo4j 5 database.
2. `cypher-shell -u neo4j -p <password> -f import.cypher`
   (or open Neo4j Browser, enable multi-statement queries in the settings, and run the file).
3. Check: `MATCH (n) RETURN labels(n)[0] AS label, count(*) ORDER BY label`.

## Option B: bulk import (large systems, empty database)

Copy `nodes/` and `rels/` into the database's `import/` folder, stop the database, then:

```bash
neo4j-admin database import full cobol \\
  {node_args} \\
  {rel_args}
```

Every node CSV has an `id:ID` column and a `:LABEL` column; every relationship CSV has
`:START_ID`, `:END_ID` and `:TYPE`, so the files need no extra options.

Queries to start with: `cypher_library.md`.
"""
    (out_dir / "README.md").write_text(text, encoding="utf-8")


def run_graph(out_root: Path, system: str | None = None, timestamp: bool = True) -> dict[str, Any]:
    g = build_graph(out_root)
    out_dir = out_root / "final_report" / "graph" / "neo4j"
    out_dir.mkdir(parents=True, exist_ok=True)
    system = system or Path(json.loads((out_root / "inventory/inventory_artifact.json").read_text(encoding="utf-8"))["meta"]["root"]).name
    files = write_csv(g, out_dir)
    write_cypher(g, out_dir, system)
    write_library(out_dir, system)
    write_readme(out_dir, files, system)
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "meta": {"generator": "harness.graph", "generator_version": __version__,
                 "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds") if timestamp else None,
                 "system": system},
        "stats": {"nodes": len(g.nodes), "relationships": len(g.rels),
                  "nodes_by_label": dict(sorted(Counter(n["label"] for n in g.nodes.values()).items())),
                  "relationships_by_type": dict(sorted(Counter(r["type"] for r in g.rels).items()))},
        "files": files,
        "query_problems": check_queries(),
    }
    (out_dir / "graph_artifact.json").write_text(json.dumps(artifact, indent=1) + "\n", encoding="utf-8")
    return artifact
