"""Console summary for the parser stage."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any


def summary_text(manifest: dict[str, Any], out_dir: Path) -> str:
    s = manifest["stats"]
    status = ", ".join(f"{k} {v}" for k, v in s["programs_by_status"].items())
    lines = [
        "=== Parser complete ===",
        f"Programs        : {s['programs']} ({status})",
        f"Paragraphs      : {s['paragraphs']} in {s['sections']} sections",
        f"Statements      : {s['statements']}",
        f"Data entries    : {s['data_entries']}",
        f"Copybooks       : {s['copybooks_expanded']} expansions",
        f"PERFORM / GO TO : {s['perform_edges']} / {s['goto_edges']}",
        f"Dead paragraphs : {s['dead_paragraphs']}",
        f"Issues          : {s['issues']} ({', '.join(f'{k} {v}' for k, v in s['issues_by_severity'].items()) or 'none'})",
    ]
    top = Counter((i["severity"], i["code"]) for i in manifest["issues"] if i["severity"] != "info")
    if top:
        lines.append("Top issues      :")
        for (sev, code), n in top.most_common(10):
            lines.append(f"  {sev:<7} {code} x{n}")
    for p in manifest["programs"]:
        if p["status"] != "parsed":
            lines.append(f"Not parsed      : {p['name']} ({p['status']}: {p.get('reason', '')})")
    lines.append(f"Output          : {out_dir / 'parser_artifact.json'}")
    lines.append("=======================")
    return "\n".join(lines)
