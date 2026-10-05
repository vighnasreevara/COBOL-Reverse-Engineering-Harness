"""Console summary of an inventory artifact."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any


def summary_text(artifact: dict[str, Any], out_file: Path | None = None) -> str:
    s = artifact["stats"]
    refs = s["references_by_type"]
    res = s["references_by_resolution"]
    scope = artifact["meta"]["scope"]
    kinds = ", ".join(f"{k}: {v}" for k, v in s["programs_by_kind"].items()) or "none"
    lines = [
        "=== Inventory complete ===",
        f"Root            : {artifact['meta']['root']}",
        f"Scope           : {scope['mode']}" + (f" ({', '.join(scope['entry_points'])})" if scope["entry_points"] else ""),
        f"Files           : {s['files']} ({', '.join(f'{k} {v}' for k, v in s['files_by_kind'].items())})",
        f"Programs        : {s['programs']} ({kinds})",
        f"Copybooks       : {s['copybooks']} ({s['copybooks_unused']} unused)",
        f"JCL             : {s['jcl_jobs']} jobs, {s['jcl_steps']} steps, {s['jcl_procs']} procs",
        f"CICS            : {', '.join(f'{k} {v}' for k, v in s['cics_resources_by_type'].items()) or 'none'}",
        f"BMS             : {s['bms_mapsets']} mapsets, {s['bms_maps']} maps",
        f"DB2             : {s['db2_tables']} tables, {s['db2_views']} views",
        f"VSAM            : {s['vsam_definitions']} definitions",
        f"References      : {s['references']} "
        f"(COPY {refs.get('COPY', 0)}, CALL {refs.get('STATIC_CALL', 0) + refs.get('DYNAMIC_CALL', 0)}, "
        f"CICS LINK/XCTL {refs.get('CICS_LINK', 0) + refs.get('CICS_XCTL', 0)}, "
        f"SQL INCLUDE {refs.get('SQL_INCLUDE', 0)}, SQL table {refs.get('SQL_TABLE', 0)})",
        f"Resolution      : {', '.join(f'{k} {v}' for k, v in res.items())}",
        f"Graph           : {s['graph_nodes']} nodes, {s['graph_edges']} unique edges",
        f"Issues          : {s['issues']} ({', '.join(f'{k} {v}' for k, v in s['issues_by_severity'].items()) or 'none'})",
    ]
    top = Counter((i["severity"], i["code"]) for i in artifact["issues"] if i["severity"] != "info")
    if top:
        lines.append("Top issues      :")
        for (sev, code), n in top.most_common(10):
            lines.append(f"  {sev:<7} {code} x{n}")
    if out_file is not None:
        lines.append(f"Output          : {out_file}")
    lines.append("==========================")
    return "\n".join(lines)
