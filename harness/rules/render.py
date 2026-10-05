"""Business rules catalogue (Markdown) from the rules artifact and checked annotations."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

CATEGORY_TITLES = {
    "validation": "Validation rules", "limit": "Limits and thresholds", "calculation": "Calculations",
    "routing": "Routing and dispatch", "state_change": "State changes", "decision": "Other decisions",
}


def annotation_index(path: Path | None) -> dict[str, dict[str, dict]]:
    """target id -> kind -> annotation item."""
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    if path and path.is_file():
        for item in json.loads(path.read_text(encoding="utf-8")).get("items", []):
            out[item["target"]][item["kind"]] = item
    return out


def _cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def render_catalog(artifact: dict[str, Any], annotations: dict[str, dict[str, dict]]) -> str:
    rules = [r for r in artifact["rules"] if r["business"]]
    s = artifact["stats"]
    lines = [
        "# Business rules catalogue",
        "",
        f"{s['business_rules']} business rules among {s['candidates']} decision and calculation points "
        f"in {s['programs_with_business_rules']} programs. Technical checks (file status, SQLCODE, end of file) "
        "are excluded. Every rule cites the source line it comes from. Names and descriptions marked "
        "with a confidence level were written by the rules agent and checked against the source; rules "
        "without one show the condition as written in the code.",
        "",
        f"Rules in code that can never run: {s['unreachable_business_rules']} (marked *unreachable*).",
        "",
    ]
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in rules:
        by_cat[r["category"]].append(r)
    for cat, title in CATEGORY_TITLES.items():
        items = by_cat.get(cat, [])
        if not items:
            continue
        lines += [f"## {title} ({len(items)})", "", "| Rule | Program | Name | Rule as coded | Outcome | Source |",
                  "|---|---|---|---|---|---|"]
        for r in sorted(items, key=lambda x: (-x["significance"], x["program"], x["line"])):
            ann = annotations.get(r["id"], {})
            name = ann.get("name", {}).get("text") or ""
            desc = ann.get("description", {}).get("text")
            conf = ann.get("name", ann.get("description", {})).get("confidence")
            coded = r["condition"] if r["condition"] is not None else r.get("formula", "")
            explained = "; ".join(f"{c['condition']} = {c['field']} {', '.join(str(v.get('value', v.get('from'))) for v in c['values'])}"
                                  for c in r["conditions_explained"])
            if explained:
                coded = f"{coded} ({explained})"
            outcome = "; ".join(f"{o['when']}: {', '.join(o['actions']) or 'nothing'}" for o in r["outcomes"])
            label = name + (f" ({conf})" if name and conf else "")
            if desc:
                label = f"{label}<br>{desc}" if label else desc
            if not r["reachable"]:
                label = f"*unreachable* {label}"
            lines.append(f"| `{r['id'].split(':', 1)[1]}` | {r['program'].split(':', 1)[1]} | {_cell(label)} | "
                         f"`{_cell(coded)}` | {_cell(outcome)} | `{r['file']}:{r['line']}` |")
        lines.append("")
    if artifact["groups"]:
        lines += ["## Rules repeated in several places", "", "| Group | Category | Condition | Where |", "|---|---|---|---|"]
        for g in artifact["groups"]:
            lines.append(f"| {g['id']} | {g['category']} | `{_cell(g['condition'])}` | "
                         f"{', '.join(r.split(':', 1)[1] for r in g['rules'])} |")
        lines.append("")
    return "\n".join(lines)
