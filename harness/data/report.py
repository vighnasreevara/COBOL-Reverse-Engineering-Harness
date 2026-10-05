"""Console summary for the data stage."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any


def summary_text(a: dict[str, Any], out_file: Path) -> str:
    s = a["stats"]
    lines = [
        "=== Data complete ===",
        f"Records         : {s['records']} ({', '.join(f'{k} {v}' for k, v in s['records_by_source'].items())})",
        f"Fields          : {s['fields']} ({s['elementary_fields']} elementary)",
        f"88-level names  : {s['conditions']}",
        f"Entities        : {', '.join(f'{k} {v}' for k, v in s['entities_by_kind'].items())}",
        f"Relationships   : {s['relationships']}",
        f"Data flows      : {s['data_flows']}",
        f"SQL host vars   : {s['host_variable_pairs']} column pairs checked",
        f"Issues          : {s['issues']} ({', '.join(f'{k} {v}' for k, v in s['issues_by_severity'].items()) or 'none'})",
    ]
    top = Counter((i["severity"], i["code"]) for i in a["issues"] if i["severity"] != "info")
    if top:
        lines.append("Top issues      :")
        for (sev, code), n in top.most_common(10):
            lines.append(f"  {sev:<7} {code} x{n}")
    lines += [f"Output          : {out_file}", "====================="]
    return "\n".join(lines)
