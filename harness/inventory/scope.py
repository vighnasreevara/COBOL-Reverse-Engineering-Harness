"""Restrict an inventory to what is reachable from chosen entry points."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

# Edge types followed when walking outwards from an entry point.
FOLLOW = {
    "JCL_EXEC", "JCL_RUN", "JCL_PROC", "JCL_DD", "IDCAMS_DEFINE", "CSD_TRANSACTION", "CSD_FILE",
    "COPY", "SQL_INCLUDE", "STATIC_CALL", "DYNAMIC_CALL", "CICS_LINK", "CICS_XCTL", "CICS_LOAD",
    "CICS_RETURN_TRANSID", "CICS_START_TRANSID", "CICS_MAP", "CICS_FILE", "CICS_QUEUE",
    "SQL_TABLE", "SQL_CALL", "PROGRAM_DATASET", "DB2_VIEW_BASE", "DB2_FOREIGN_KEY",
}
ENTRY_KINDS = ("job", "transaction", "program", "proc")


def resolve_entry(artifact: dict[str, Any], entry: str) -> str:
    """Accept 'job:NAME', 'transaction:NAME', 'program:NAME' or a bare name."""
    nodes = {n["id"] for n in artifact["graph"]["nodes"]}
    if ":" in entry:
        kind, _, name = entry.partition(":")
        node = f"{kind.lower()}:{name.upper()}"
        if node in nodes:
            return node
        raise ValueError(f"entry point {entry} not found in the inventory")
    matches = [f"{k}:{entry.upper()}" for k in ENTRY_KINDS if f"{k}:{entry.upper()}" in nodes]
    if not matches:
        raise ValueError(f"entry point {entry} not found in the inventory")
    if len(matches) > 1:
        raise ValueError(f"entry point {entry} is ambiguous: {', '.join(matches)}; prefix it with the kind")
    return matches[0]


def reachable(artifact: dict[str, Any], starts: list[str]) -> set[str]:
    out: dict[str, set[str]] = defaultdict(set)
    for e in artifact["graph"]["edges"]:
        if e["type"] in FOLLOW:
            out[e["from"]].add(e["to"])
    seen = set(starts)
    queue = deque(starts)
    while queue:
        cur = queue.popleft()
        for nxt in out.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    # Maps belong to their mapset; keep the mapset when one of its maps is used.
    for nid in list(seen):
        if nid.startswith("map:"):
            seen.add("mapset:" + nid[4:].split(".", 1)[0])
    return seen


def apply_scope(artifact: dict[str, Any], entries: list[str]) -> dict[str, Any]:
    from harness.inventory.builder import compute_stats

    starts = [resolve_entry(artifact, e) for e in entries]
    keep = reachable(artifact, starts)

    def kept(items: list[dict]) -> list[dict]:
        return [i for i in items if i["id"] in keep]

    scoped = dict(artifact)
    scoped["programs"] = [
        {**p, "called_by": [c for c in p["called_by"] if c in keep],
         "executed_by": [c for c in p["executed_by"] if c in keep]}
        for p in kept(artifact["programs"])
    ]
    scoped["copybooks"] = [
        {**c, "used_by": [u for u in c["used_by"] if f"program:{u}" in keep or f"copybook:{u}" in keep]}
        for c in kept(artifact["copybooks"])
    ]
    scoped["jcl"] = {"jobs": kept(artifact["jcl"]["jobs"]), "procs": kept(artifact["jcl"]["procs"])}
    resource_node = {"TRANSACTION": "transaction", "PROGRAM": "program", "FILE": "cics_file", "MAPSET": "mapset"}
    scoped["cics"] = {"resources": [
        r for r in artifact["cics"]["resources"]
        if r["type"] in resource_node and f"{resource_node[r['type']]}:{r['name']}" in keep
    ]}
    scoped["bms"] = {"mapsets": kept(artifact["bms"]["mapsets"])}
    scoped["db2"] = {
        "tables": kept(artifact["db2"]["tables"]),
        "views": kept(artifact["db2"]["views"]),
        "indexes": [i for i in artifact["db2"]["indexes"] if f"table:{i['table']}" in keep],
        "objects": artifact["db2"]["objects"],
    }
    scoped["vsam"] = {"definitions": kept(artifact["vsam"]["definitions"])}
    scoped["references"] = [r for r in artifact["references"] if r["from"] in keep]
    scoped["graph"] = {
        "nodes": [n for n in artifact["graph"]["nodes"] if n["id"] in keep],
        "edges": [e for e in artifact["graph"]["edges"] if e["from"] in keep and e["to"] in keep],
    }
    scoped["copybook_map"] = {c["name"]: c["used_by"] for c in scoped["copybooks"]}
    paths = {i["path"] for group in (scoped["programs"], scoped["copybooks"], scoped["jcl"]["jobs"],
                                     scoped["jcl"]["procs"], scoped["bms"]["mapsets"], scoped["db2"]["tables"],
                                     scoped["db2"]["views"], scoped["cics"]["resources"]) for i in group}
    scoped["files"] = [f for f in artifact["files"] if f["path"] in paths]
    scoped["issues"] = [i for i in artifact["issues"]
                        if i.get("subject") in keep or (i.get("subject") is None and i.get("path") in paths)]
    scoped["meta"] = {**artifact["meta"], "scope": {"mode": "entry_points", "entry_points": starts}}
    scoped["stats"] = compute_stats(scoped)
    return scoped
