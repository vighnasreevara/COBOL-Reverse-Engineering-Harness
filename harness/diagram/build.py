"""Stage 6: Mermaid diagrams generated only from verified artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness import __version__
from harness.diagram.mermaid import Flowchart, label, lint, node_id

SCHEMA_VERSION = "1.0"
MAX_FLOW_NODES = 70


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _short(entity: str) -> str:
    return entity.split(":", 1)[1]


STYLES = {
    "job": "fill:#ede9fe,stroke:#7c3aed,color:#2e1065",
    "tran": "fill:#fce7f3,stroke:#db2777,color:#500724",
    "batch": "fill:#dbeafe,stroke:#2563eb,color:#172554",
    "online": "fill:#d1fae5,stroke:#059669,color:#022c22",
    "sub": "fill:#f1f5f9,stroke:#475569,color:#0f172a",
    "store": "fill:#fef3c7,stroke:#d97706,color:#451a03",
    "missing": "fill:#fee2e2,stroke:#dc2626,color:#450a0a,stroke-dasharray:4 3",
    "dead": "fill:#f3f4f6,stroke:#9ca3af,color:#6b7280,stroke-dasharray:4 3",
}


def _styled(fc: Flowchart, *names: str) -> None:
    for n in names:
        fc.style(n, STYLES[n])


# --------------------------------------------------------------------------- system level
def system_context(inv: dict) -> tuple[str, dict]:
    fc = Flowchart("LR")
    _styled(fc, "job", "tran", "batch", "online", "sub", "missing")
    kinds = {p["id"]: p["kind"] for p in inv["programs"]}
    used = set()
    for e in inv["graph"]["edges"]:
        if e["type"] in ("JCL_EXEC", "JCL_RUN", "CSD_TRANSACTION", "STATIC_CALL", "CICS_LINK", "CICS_XCTL"):
            used.update((e["from"], e["to"]))
    for nid in sorted(used):
        kind, name = nid.split(":", 1)
        if kind == "job":
            fc.node(node_id(nid), f"job {name}", "para", "job", "Batch jobs")
        elif kind == "transaction":
            fc.node(node_id(nid), f"transaction {name}", "hex", "tran", "CICS transactions")
        elif kind == "program":
            pk = kinds.get(nid)
            cls = {"online": "online", "batch": "batch", "subroutine": "sub"}.get(pk, "missing")
            group = {"online": "Online programs", "batch": "Batch programs", "subroutine": "Subroutines"}.get(pk, "Not in source")
            fc.node(node_id(nid), name, "box" if pk else "hex", cls, group)
        elif kind == "utility":
            fc.node(node_id(nid), name, "sub", "sub", "System utilities")
        else:
            fc.node(node_id(nid), f"{kind} {name}", "box", "sub", "Copybooks with procedure code")
    for e in inv["graph"]["edges"]:
        if e["from"] in used and e["to"] in used and e["type"] in ("JCL_EXEC", "JCL_RUN", "CSD_TRANSACTION",
                                                                      "STATIC_CALL", "CICS_LINK", "CICS_XCTL"):
            text = {"JCL_EXEC": "runs", "JCL_RUN": "runs (DSN)", "CSD_TRANSACTION": "starts",
                    "STATIC_CALL": "CALL", "CICS_LINK": "LINK", "CICS_XCTL": "XCTL"}[e["type"]]
            style = "-.->" if e["resolution"] not in ("resolved", "ambiguous") else "-->"
            fc.edge(node_id(e["from"]), node_id(e["to"]), text + ("" if e["count"] == 1 else f" x{e['count']}"), style)
    return fc.render("System context: jobs, transactions and program calls"), \
        {"nodes": len(fc.nodes), "edges": len(fc.edges)}


def data_lineage(data: dict) -> tuple[str, dict]:
    fc = Flowchart("LR")
    _styled(fc, "batch", "online", "sub", "store", "missing")
    kinds = {}
    for e in data["entities"]:
        kinds[e["id"]] = e
    for f in data["data_flows"]:
        store = f["entity"]
        ent = kinds.get(store, {})
        text = f"{ent.get('kind', store.split(':')[0])} {_short(store)}"
        fc.node(node_id(store), text, "db", "missing" if ent.get("undefined") else "store", "Data stores")
        fc.node(node_id(f["program"]), _short(f["program"]), "box", "batch", "Programs")
    seen = set()
    for f in data["data_flows"]:
        key = (f["program"], f["entity"], f["direction"])
        if key in seen:
            continue
        seen.add(key)
        p, s = node_id(f["program"]), node_id(f["entity"])
        if f["direction"] == "read":
            fc.edge(s, p, "read")
        elif f["direction"] in ("write", "delete"):
            fc.edge(p, s, f["direction"])
        else:
            fc.edge(p, s, f["direction"], "<-->" if False else "-->")
    return fc.render("Data lineage: which programs read and write which data stores"), \
        {"nodes": len(fc.nodes), "edges": len(fc.edges)}


def job_flow(job: dict, data: dict, inv: dict) -> tuple[str, dict]:
    fc = Flowchart("LR")
    _styled(fc, "job", "batch", "sub", "store")
    jid = node_id("job", job["name"])
    fc.node(jid, f"job {job['name']}", "para", "job")
    flows = defaultdict(list)
    for f in data["data_flows"]:
        flows[f["program"]].append(f)
    for step in job["steps"]:
        target = step["run_program"] or step["program"] or step["proc"] or "?"
        sid = node_id("step", job["name"], step["name"] or str(step["order"]))
        cls = "sub" if step["utility"] and not step["run_program"] else "batch"
        fc.node(sid, f"step {step['name']}: {target}", "box", cls)
        fc.edge(jid, sid, f"step {step['order']}")
        prog_flows = [f for f in flows.get(f"program:{target}", []) if f"({job['name']} {step['name']})" in f["via"]]
        if prog_flows:
            for f in prog_flows:
                dsid = node_id(f["entity"])
                fc.node(dsid, _short(f["entity"]), "db", "store")
                dd = f["via"].split("DD ")[1].split(" ")[0] if "DD " in f["via"] else ""
                if f["direction"] == "read":
                    fc.edge(dsid, sid, f"{dd} read")
                else:
                    fc.edge(sid, dsid, f"{dd} {f['direction']}")
        else:
            for dd in step["dds"]:
                if dd["dsn"]:
                    dsid = node_id("dataset", dd["dsn"])
                    fc.node(dsid, dd["dsn"], "db", "store")
                    fc.edge(sid, dsid, f"{dd['name'] or ''} {dd['disp'] or ''}".strip())
    return fc.render(f"Batch job {job['name']}"), {"nodes": len(fc.nodes), "edges": len(fc.edges)}


def erd(inv: dict, data: dict) -> tuple[str, dict]:
    out = ["---", "title: Data model (DB2 tables from DDL, keyed files from record layouts)", "---", "erDiagram"]
    ents = 0
    rels = 0
    fk_cols = defaultdict(set)
    for t in inv["db2"]["tables"]:
        for fk in t["foreign_keys"]:
            fk_cols[t["name"]].update(fk["columns"])
    for t in inv["db2"]["tables"]:
        ents += 1
        out.append(f"  {node_id(t['name'])} {{")
        for c in t["columns"]:
            ctype = re.sub(r"[^A-Za-z0-9]", "_", c["type"]).strip("_") or "UNKNOWN"
            keys = []
            if c["name"] in t["primary_key"]:
                keys.append("PK")
            if c["name"] in fk_cols[t["name"]]:
                keys.append("FK")
            out.append(f"    {ctype} {node_id(c['name'])}{' ' + ','.join(keys) if keys else ''}")
        out.append("  }")
    for t in inv["db2"]["tables"]:
        for fk in t["foreign_keys"]:
            rels += 1
            out.append(f"  {node_id(fk['references'])} ||--o{{ {node_id(t['name'])} : {label(', '.join(fk['columns']))}")
    records = {r["id"]: r for r in data["records"]}
    for e in data["entities"]:
        if e["kind"] != "dataset" or not e.get("key_fields") and not e.get("vsam"):
            continue
        rec = records.get(e["records"][0]) if e.get("records") else None
        if not rec:
            continue
        ents += 1
        keyset = {k["field"] for k in e.get("key_fields", [])}
        out.append(f"  {node_id(e['name'])} {{")
        for f in [f for f in rec["fields"] if f["level"] != 1 and f["qualified_name"].count(".") == 1][:14]:
            ftype = (f["category"] or "group").replace("-", "_")
            mark = " PK" if f["qualified_name"] in keyset else ""
            out.append(f"    {ftype} {node_id(f['name'] or 'FILLER')}{mark}")
        out.append("  }")
    return "\n".join(out) + "\n", {"nodes": ents, "edges": rels}


# --------------------------------------------------------------------------- program level
def paragraph_graph(doc: dict) -> tuple[str, dict]:
    fc = Flowchart("TD")
    _styled(fc, "dead", "missing")
    fc.style("entry", "fill:#1e3a8a,stroke:#1e3a8a,color:#ffffff")
    entry = doc["cfg"]["entry"]
    for n in doc["cfg"]["nodes"]:
        cls = "entry" if n["name"] == entry else ("dead" if not n["reachable"] else None)
        fc.node(node_id("p", n["name"]), n["name"] + (" (never runs)" if not n["reachable"] else ""), "box", cls)
    for u in doc["cfg"]["undefined_targets"]:
        fc.node(node_id("p", u["name"]), f"{u['name']} (not defined)", "hex", "missing")
    seen = set()
    for e in doc["cfg"]["edges"]:
        if e["type"] == "FALL THROUGH":
            continue
        key = (e["from"], e["to"], e["type"], e.get("thru"))
        if key in seen:
            continue
        seen.add(key)
        text = {"PERFORM": "perform", "GO TO": "go to", "CICS HANDLE": "on CICS condition", "ALTER": "altered"}.get(
            e["type"], e["type"].lower())
        if e.get("thru"):
            text += f" thru {e['thru']}"
        if e.get("loop"):
            text += f" ({e['loop']})"
        style = "-->" if e["type"] == "PERFORM" else "-.->"
        fc.edge(node_id("p", e["from"]), node_id("p", e["to"]), text, style)
    for u in doc["cfg"]["undefined_targets"]:
        key = (u["paragraph"], u["name"])
        if key not in seen:
            seen.add(key)
            fc.edge(node_id("p", u["paragraph"]), node_id("p", u["name"]), u["type"].lower(), "-.->")
    return fc.render(f"{doc['program']['name']}: paragraph structure"), {"nodes": len(fc.nodes), "edges": len(fc.edges)}


class _FlowBuilder:
    def __init__(self, fc: Flowchart, prefix: str) -> None:
        self.fc = fc
        self.prefix = prefix
        self.count = 0
        self.truncated = False

    def new(self, text: str, shape: str = "box", cls: str | None = None) -> str:
        self.count += 1
        return self.fc.node(f"{self.prefix}{self.count}", text, shape, cls)

    def block(self, stmts: list[dict], tails: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
        for s in stmts:
            if self.count >= MAX_FLOW_NODES:
                if not self.truncated:
                    n = self.new("... more steps (see pseudocode)", "box", "dead")
                    self._link(tails, n)
                    tails = [(n, None)]
                    self.truncated = True
                return tails
            tails = self.statement(s, tails)
        return tails

    def _link(self, tails: list[tuple[str, str | None]], target: str) -> None:
        for t, text in tails:
            self.fc.edge(t, target, text)

    def statement(self, s: dict, tails: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
        v = s["verb"]
        if v == "IF":
            d = self.new(f"{s['condition']}?", "diamond")
            self._link(tails, d)
            yes = self.block(s.get("then", []), [(d, "yes")])
            no = self.block(s.get("else", []), [(d, "no")]) if s.get("else") else [(d, "no")]
            return yes + no
        if v == "EVALUATE":
            d = self.new(f"{' / '.join(s['subjects'])}?", "diamond")
            self._link(tails, d)
            out: list[tuple[str, str | None]] = []
            has_other = False
            for b in s["branches"]:
                text = "other" if b["other"] and not b["conditions"] else " or ".join(b["conditions"])
                has_other |= b["other"]
                out += self.block(b["statements"], [(d, text)])
            if not has_other:
                out.append((d, "no match"))
            return out
        if v == "PERFORM" and s.get("inline"):
            loop = s.get("loop", {})
            head = self.new(f"loop {loop.get('type')}: {loop.get('condition') or loop.get('count') or ''}", "hex")
            self._link(tails, head)
            body_tails = self.block(s.get("body", []), [(head, "repeat")])
            for t, text in body_tails:
                self.fc.edge(t, head, text, "-.->")
            return [(head, "done")]
        if v == "PERFORM" and s.get("target"):
            loop = s.get("loop", {})
            suffix = "" if loop.get("type", "none") == "none" else f" ({loop['type']} {loop.get('condition') or loop.get('count') or ''})"
            n = self.new(f"{s['target']}{' thru ' + s['thru'] if s.get('thru') else ''}{suffix}", "sub")
        elif v in ("GOBACK", "STOP RUN", "EXIT PROGRAM"):
            n = self.new(v, "stadium")
            self._link(tails, n)
            return []
        elif v == "EXEC" and s.get("language") == "CICS" and s.get("command") in ("RETURN", "XCTL", "ABEND"):
            n = self.new(f"CICS {s['command']}", "stadium")
            self._link(tails, n)
            return []
        elif v == "GO TO":
            n = self.new(f"go to {' / '.join(s.get('targets', []))}", "stadium")
            self._link(tails, n)
            return []
        elif v == "EXEC":
            opts = s.get("options", {})
            n = self.new(f"{s.get('language')} {s.get('command')} {opts.get('PROGRAM') or opts.get('MAP') or ''}".strip(), "para")
        elif v == "CALL":
            n = self.new(f"CALL {s.get('program') or s.get('program_variable')}", "sub")
        elif v in ("READ", "WRITE", "REWRITE", "DELETE", "OPEN", "CLOSE", "START"):
            n = self.new(s["text"], "para")
        else:
            n = self.new(s["text"])
        self._link(tails, n)
        for ph in s.get("phrases", []):
            pass
        return [(n, None)]


def program_flow(doc: dict) -> tuple[str, dict]:
    fc = Flowchart("TD")
    _styled(fc, "dead")
    paras = doc["procedure"]["paragraphs"]
    if not paras:
        fc.node("empty", "no procedure", "stadium")
        return fc.render(f"{doc['program']['name']}: main flow"), {"nodes": 1, "edges": 0, "truncated": False}
    entry = paras[0]
    fb = _FlowBuilder(fc, "s")
    start = fc.node("start", f"start {doc['program']['name']} at {entry['name']}", "stadium")
    tails = fb.block(entry["statements"], [(start, None)])
    if tails:
        end = fc.node("fallthrough", "falls through to the next paragraph", "stadium")
        for t, text in tails:
            fc.edge(t, end, text)
    return fc.render(f"{doc['program']['name']}: main flow ({entry['name']})"), \
        {"nodes": len(fc.nodes), "edges": len(fc.edges), "truncated": fb.truncated}


def online_sequence(tran: str, entry_program: str, logic_dir: Path, logic_index: dict[str, str]) -> tuple[str, dict]:
    lines = ["---", f"title: CICS transaction {tran}", "---", "sequenceDiagram"]
    participants: dict[str, str] = {}

    def part(name: str, text: str | None = None) -> str:
        pid = node_id(name)
        if pid not in participants:
            participants[pid] = f"  participant {pid} as {text or name}"
        return pid

    user = part("Terminal", "Terminal user")
    cics = part("CICS", "CICS")
    body: list[str] = []
    entry = part(entry_program)
    body.append(f"  {user}->>{entry}: transaction {tran}")
    messages = 0

    def walk(program: str, nodes: list[dict], depth: int, indent: str) -> None:
        nonlocal messages
        me = part(program)
        for n in nodes:
            guard = " and ".join(n["when"])
            pad = indent
            if guard:
                body.append(f"{indent}opt {guard[:80]}")
                pad = indent + "  "
            kind = n["kind"]
            target = (n.get("target") or "").strip("'\"")
            if kind == "cics link" and target:
                callee = part(target)
                body.append(f"{pad}{me}->>{callee}: LINK")
                messages += 1
                if depth < 2 and target in logic_index:
                    child = json.loads((logic_dir / logic_index[target]).read_text(encoding="utf-8"))
                    walk(target, child["outline"], depth + 1, pad)
                body.append(f"{pad}{callee}-->>{me}: return")
            elif kind == "cics xctl" and target:
                callee = part(target)
                body.append(f"{pad}{me}->>{callee}: XCTL (no return)")
                messages += 1
            elif kind in ("cics send", "cics receive"):
                arrow = f"{me}->>{user}: SEND map {target}" if kind == "cics send" else f"{user}->>{me}: RECEIVE map {target}"
                body.append(f"{pad}{arrow}")
                messages += 1
            elif kind == "cics return" and depth == 0:
                # in a LINKed program RETURN just goes back to the caller (drawn as the reply)
                body.append(f"{pad}{me}->>{cics}: RETURN{' ' + target if target else ''}")
                messages += 1
            elif kind == "call" and target:
                callee = part(target)
                body.append(f"{pad}{me}->>{callee}: CALL")
                body.append(f"{pad}{callee}-->>{me}: return")
                messages += 1
            elif kind in ("perform", "loop") and n.get("children"):
                if n.get("loop"):
                    body.append(f"{pad}loop {n['loop'].get('type')} {(n['loop'].get('condition') or n['loop'].get('count') or '')[:60]}")
                    walk(program, n["children"], depth, pad + "  ")
                    body.append(f"{pad}end")
                else:
                    walk(program, n["children"], depth, pad)
            if guard:
                body.append(f"{indent}end")
            if messages > 60:
                return

    doc = json.loads((logic_dir / logic_index[entry_program]).read_text(encoding="utf-8"))
    walk(entry_program, doc["outline"], 0, "  ")
    # drop opt blocks that ended up empty
    cleaned: list[str] = []
    for line in body:
        if line.strip() == "end" and cleaned and cleaned[-1].strip().startswith(("opt ", "loop ")):
            cleaned.pop()
            continue
        cleaned.append(line)
    text = "\n".join(lines + list(participants.values()) + cleaned) + "\n"
    return text, {"nodes": len(participants), "edges": messages}


# --------------------------------------------------------------------------- driver
def run_diagrams(inventory_path: Path, parser_path: Path, data_path: Path, logic_path: Path, out_dir: Path,
                 timestamp: bool = True) -> dict[str, Any]:
    inv = json.loads(inventory_path.read_text(encoding="utf-8"))
    parser = json.loads(parser_path.read_text(encoding="utf-8"))
    data = json.loads(data_path.read_text(encoding="utf-8"))
    logic = json.loads(logic_path.read_text(encoding="utf-8"))
    ddir = out_dir / "diagrams"
    ddir.mkdir(parents=True, exist_ok=True)
    index: list[dict] = []

    def save(did: str, kind: str, title: str, text: str, info: dict, subject: str | None, sources: list[str]) -> None:
        fname = f"{did}.mmd"
        (ddir / fname).write_text(text, encoding="utf-8")
        index.append({"id": f"diagram:{did}", "file": f"diagrams/{fname}", "type": kind, "title": title,
                      "subject": subject, "nodes": info["nodes"], "edges": info["edges"],
                      "truncated": info.get("truncated", False), "sources": sources, "lint": lint(text)})

    text, info = system_context(inv)
    save("system_context", "flowchart", "System context", text, info, None, ["inventory"])
    text, info = data_lineage(data)
    save("data_lineage", "flowchart", "Data lineage", text, info, None, ["data"])
    text, info = erd(inv, data)
    save("erd", "erDiagram", "Data model", text, info, None, ["inventory", "data"])
    for job in inv["jcl"]["jobs"]:
        text, info = job_flow(job, data, inv)
        save(f"job_{node_id(job['name'])}", "flowchart", f"Batch job {job['name']}", text, info, job["id"],
             ["inventory", "data"])
    logic_index = {p["name"]: p["output"] for p in logic["programs"]}
    for r in inv["cics"]["resources"]:
        prog = r["attributes"].get("PROGRAM")
        if r["type"] == "TRANSACTION" and prog in logic_index:
            text, info = online_sequence(r["name"], prog, logic_path.parent, logic_index)
            save(f"transaction_{node_id(r['name'])}", "sequenceDiagram", f"Transaction {r['name']}", text, info,
                 r["id"], ["inventory", "logic"])
    for entry in parser["programs"]:
        if entry["status"] != "parsed":
            continue
        doc = json.loads((parser_path.parent / entry["output"]).read_text(encoding="utf-8"))
        name = doc["program"]["name"]
        text, info = paragraph_graph(doc)
        save(f"program_{node_id(name)}_paragraphs", "flowchart", f"{name} paragraph structure", text, info,
             doc["program"]["id"], ["parser"])
        text, info = program_flow(doc)
        save(f"program_{node_id(name)}_flow", "flowchart", f"{name} main flow", text, info, doc["program"]["id"],
             ["parser"])

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "meta": {"generator": "harness.diagram", "generator_version": __version__,
                 "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds") if timestamp else None,
                 "inventory_sha256": _sha(inventory_path), "data_sha256": _sha(data_path),
                 "logic_sha256": _sha(logic_path), "root": inv["meta"]["root"]},
        "stats": {},
        "diagrams": index,
    }
    artifact["stats"] = compute_stats(artifact)
    (out_dir / "diagrams_artifact.json").write_text(json.dumps(artifact, indent=1) + "\n", encoding="utf-8")
    return artifact


def compute_stats(a: dict) -> dict:
    from collections import Counter
    ds = a["diagrams"]
    return {
        "diagrams": len(ds),
        "by_type": dict(sorted(Counter(d["type"] for d in ds).items())),
        "with_lint_problems": sum(1 for d in ds if d["lint"]),
        "truncated": sum(1 for d in ds if d["truncated"]),
    }
