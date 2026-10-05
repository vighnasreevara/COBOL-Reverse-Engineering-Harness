"""Stage 7: Business Requirements Document assembled from verified artifacts and checked annotations."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness import __version__
from harness.common.annotations import check_annotations
from harness.synthesis.gaps import build_gaps

SCHEMA_VERSION = "1.0"
STAGES = ("inventory", "parser", "data", "logic", "rules", "diagram")
ARTIFACT = {"inventory": "inventory/inventory_artifact.json", "parser": "parser/parser_artifact.json",
            "data": "data/data_artifact.json", "logic": "logic/logic_artifact.json",
            "rules": "rules/rules_artifact.json", "diagram": "diagram/diagrams_artifact.json"}
ANNOTATIONS = {"data": "data/data_annotations.json", "logic": "logic/logic_annotations.json",
               "rules": "rules/rules_annotations.json", "diagram": "diagram/diagram_annotations.json",
               "synthesis": "final_report/synthesis_annotations.json"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cell(text: Any) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def _location(loc: dict) -> str:
    return f"`{loc['path']}:{loc['line']}`" if loc.get("line") else f"`{loc['path']}`"


def _short(i: str) -> str:
    return i.split(":", 1)[1] if ":" in i else i


class Brd:
    def __init__(self, out_root: Path, system_name: str | None, timestamp: bool) -> None:
        self.out = out_root
        self.a = {s: json.loads((out_root / p).read_text(encoding="utf-8")) for s, p in ARTIFACT.items()}
        self.paths = {s: out_root / p for s, p in ARTIFACT.items()}
        self.root = Path(self.a["inventory"]["meta"]["root"])
        self.system = system_name or self.root.name
        self.timestamp = timestamp
        self.problems: list[str] = []
        self.logic_docs = {p["id"]: json.loads((out_root / "logic" / p["output"]).read_text(encoding="utf-8"))
                           for p in self.a["logic"]["programs"]}

    # ------------------------------------------------------------- annotations
    def load_annotations(self, sections: list[dict]) -> dict[str, dict[str, dict]]:
        """All stages' annotations that pass their check: target -> kind -> item."""
        index: dict[str, dict[str, dict]] = defaultdict(dict)
        logic_dir_docs = list(self.logic_docs.values())
        for stage, rel in ANNOTATIONS.items():
            path = self.out / rel
            if not path.is_file():
                continue
            ann = json.loads(path.read_text(encoding="utf-8"))
            if stage == "synthesis":
                doc = {"sections": sections, "gaps": self.gaps, "documents": list(self.a.values()) + logic_dir_docs}
            elif stage == "logic":
                doc = {"documents": logic_dir_docs + [self.a["logic"], self.a["data"]]}
            else:
                doc = self.a[stage]
            problems = check_annotations(ann, doc, self.root)
            if problems:
                self.problems += [f"{rel}: {p}" for p in problems]
                continue
            for item in ann["items"]:
                index[item["target"]][item["kind"]] = item
        return index

    def text(self, target: str, kind: str) -> str | None:
        item = self.ann.get(target, {}).get(kind)
        return f"{item['text']} *(confidence: {item['confidence']})*" if item else None

    # ------------------------------------------------------------------ build
    def build(self) -> tuple[dict[str, Any], str, str, str]:
        rules = self.a["rules"]["rules"]
        issues_by_stage = {s: self.a[s].get("issues", []) for s in ("inventory", "parser", "data", "logic")}
        unreachable = [r for r in rules if r["business"] and not r["reachable"]]
        self.gaps = build_gaps(issues_by_stage, unreachable)
        entry_points = self._entry_points()
        sections = [{"id": "brd:executive_summary", "title": "Executive summary"},
                    {"id": "brd:system_purpose", "title": "System purpose and scope"}]
        sections += [{"id": f"brd:process:{_short(e['id'])}", "title": f"Process {_short(e['id'])}"} for e in entry_points]
        sections += [{"id": "brd:recommendations", "title": "Recommendations"}]
        self.ann = self.load_annotations(sections)
        artifact = {
            "schema_version": SCHEMA_VERSION,
            "meta": {"generator": "harness.synthesis", "generator_version": __version__,
                     "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds") if self.timestamp else None,
                     "system": self.system, "root": str(self.root),
                     "inputs": {s: _sha(p) for s, p in self.paths.items()}},
            "stats": {},
            "sections": sections,
            "entry_points": entry_points,
            "gaps": self.gaps,
            "annotation_problems": self.problems,
        }
        artifact["stats"] = compute_stats(artifact, self.ann)
        return artifact, self.render_brd(artifact), self.render_summary(artifact), self.render_gaps()

    def _entry_points(self) -> list[dict]:
        inv = self.a["inventory"]
        out = []
        for j in inv["jcl"]["jobs"]:
            progs = [s["run_program"] or s["program"] for s in j["steps"]]
            out.append({"id": j["id"], "kind": "batch job", "programs": [p for p in progs if p],
                        "path": j["path"], "line": j["line"]})
        for r in inv["cics"]["resources"]:
            if r["type"] == "TRANSACTION":
                out.append({"id": r["id"], "kind": "CICS transaction", "programs": [r["attributes"].get("PROGRAM")],
                            "path": r["path"], "line": r["line"]})
        return out

    # ----------------------------------------------------------------- render
    def render_brd(self, art: dict) -> str:
        inv, data, logic, rules_a = self.a["inventory"], self.a["data"], self.a["logic"], self.a["rules"]
        s = inv["stats"]
        L: list[str] = []
        add = L.append
        add(f"# Business Requirements Document: {self.system}")
        add("")
        add("Reverse engineered from source by the COBOL reverse engineering harness. Facts come from tested "
            "code and cite the source file and line or the artifact id they come from. Text marked with a "
            "confidence level was written by an agent and checked: every statement it makes points at existing "
            "source lines or artifact ids. Sections without such text show facts only.")
        add("")
        add("| Item | Value |")
        add("|---|---|")
        add(f"| Source analysed | `{self.root}` |")
        add(f"| Generated | {art['meta']['generated_at'] or 'n/a'} |")
        add(f"| Programs / copybooks / jobs | {s['programs']} / {s['copybooks']} / {s['jcl_jobs']} |")
        add(f"| Business rules | {rules_a['stats']['business_rules']} |")
        add(f"| Gaps | {len(self.gaps)} ({', '.join(f'{k} {v}' for k, v in art['stats']['gaps_by_severity'].items())}) |")
        add(f"| Checked annotations used | {art['stats']['annotations_used']} |")
        if self.problems:
            add(f"| Annotations rejected | {len(self.problems)} (see section 9) |")
        add("")

        add("## 1. Executive summary")
        add("")
        add(self.text("brd:executive_summary", "narrative") or "*Not yet written by the synthesis agent.*")
        add("")
        top = [g for g in self.gaps if g["severity"] == "high"][:8]
        if top:
            add("Most important findings:")
            add("")
            for g in top:
                add(f"- **{g['category']}**: {_cell(g['title'])} (`{g['id']}`)")
            add("")

        add("## 2. System overview")
        add("")
        add(self.text("brd:system_purpose", "narrative") or "")
        add("")
        add("| Area | Count | Detail |")
        add("|---|---|---|")
        add(f"| Programs | {s['programs']} | {', '.join(f'{k} {v}' for k, v in s['programs_by_kind'].items())} |")
        add(f"| Entry points | {len(art['entry_points'])} | {sum(1 for e in art['entry_points'] if e['kind'] == 'batch job')} batch jobs, "
            f"{sum(1 for e in art['entry_points'] if e['kind'] != 'batch job')} CICS transaction(s) |")
        add(f"| Data stores | {sum(data['stats']['entities_by_kind'].values())} | {', '.join(f'{k} {v}' for k, v in data['stats']['entities_by_kind'].items())} |")
        add(f"| Record layouts | {data['stats']['records']} | {data['stats']['fields']} fields, {data['stats']['conditions']} coded values (88 levels) |")
        add(f"| Source size | {s['files']} files | {s['lines']} lines |")
        add("")
        add(self._diagram("diagram:system_context", "Figure 2.1 System context"))

        add("## 3. Business processes")
        add("")
        for e in art["entry_points"]:
            add(f"### 3.{art['entry_points'].index(e) + 1} {e['kind'].capitalize()} {_short(e['id'])}")
            add("")
            add(f"Defined at `{e['path']}:{e['line']}`; runs {', '.join(e['programs']) or 'no program'}.")
            add("")
            narrative = self.text(f"brd:process:{_short(e['id'])}", "narrative")
            if narrative:
                add(narrative)
                add("")
            for p in e["programs"]:
                summary = self.text(f"program:{p}", "summary")
                if summary:
                    add(f"**{p}:** {summary}")
                    add("")
            prog_rules = [r for r in self.a["rules"]["rules"] if r["business"] and r["program"] in
                          {f"program:{p}" for p in e["programs"]}]
            if prog_rules:
                add(f"Business rules applied: {len(prog_rules)} (see chapter 4: "
                    f"{', '.join(_short(r['id']) for r in prog_rules[:8])}{' ...' if len(prog_rules) > 8 else ''}).")
                add("")
            did = f"diagram:{'job' if e['kind'] == 'batch job' else 'transaction'}_{_short(e['id'])}"
            add(self._diagram(did, f"Figure 3.{art['entry_points'].index(e) + 1} {_short(e['id'])}"))
        others = [p for p in inv["programs"] if not p["executed_by"] and p["kind"] != "unknown"]
        add(f"### 3.{len(art['entry_points']) + 1} Programs not started by any job or transaction")
        add("")
        add("| Program | Kind | Called by | Summary |")
        add("|---|---|---|---|")
        for p in others:
            add(f"| {p['name']} | {p['kind']} | {', '.join(_short(c) for c in p['called_by']) or 'none'} | "
                f"{_cell(self.text(p['id'], 'summary') or '')} |")
        add("")

        add("## 4. Business rules")
        add("")
        add(f"{rules_a['stats']['business_rules']} business rules were found among {rules_a['stats']['candidates']} "
            "decisions and calculations. The full catalogue with conditions as coded and outcomes is in "
            "`output/rules/rules_catalog.md`. Rules with a business name:")
        add("")
        add("| Rule | Name | Category | Status | Source |")
        add("|---|---|---|---|---|")
        named = [r for r in rules_a["rules"] if r["business"] and self.ann.get(r["id"], {}).get("name")]
        for r in sorted(named, key=lambda x: (x["category"], x["program"], x["line"])):
            status = "enforced" if r["reachable"] else "**never runs**"
            add(f"| `{_short(r['id'])}` | {_cell(self.ann[r['id']]['name']['text'])} | {r['category']} | {status} | "
                f"`{r['file']}:{r['line']}` |")
        add("")
        add(f"Business rules by category: {', '.join(f'{k} {v}' for k, v in rules_a['stats']['business_by_category'].items())}.")
        add("")

        add("## 5. Data")
        add("")
        add("| Store | Kind | Layout / columns | Read by | Written by | Description |")
        add("|---|---|---|---|---|---|")
        readers, writers = defaultdict(set), defaultdict(set)
        for f in data["data_flows"]:
            (readers if f["direction"] == "read" else writers)[f["entity"]].add(_short(f["program"]))
        for e in data["entities"]:
            if e["id"] not in readers and e["id"] not in writers:
                continue
            layout = ", ".join(_short(r) for r in e.get("records", [])) or \
                (f"{len(e['columns'])} columns, key {', '.join(e['primary_key'])}" if e.get("columns") else "")
            add(f"| {e['name']} | {e['kind']} | {_cell(layout)} | {', '.join(sorted(readers[e['id']]))} | "
                f"{', '.join(sorted(writers[e['id']]))} | {_cell(self.text(e['id'], 'description') or '')} |")
        add("")
        described = [r for r in data["records"] if self.ann.get(r["id"], {}).get("description")]
        if described:
            add("Key record layouts:")
            add("")
            for r in described:
                add(f"- **{r['name']}** ({r['size']} bytes, `{r['file']}:{r['line']}`): {self.text(r['id'], 'description')}")
            add("")
        add(self._diagram("diagram:erd", "Figure 5.1 Data model"))
        add(self._diagram("diagram:data_lineage", "Figure 5.2 Data lineage"))

        add("## 6. Program reference")
        add("")
        add("| Program | Kind | Started by | Paragraphs | Complexity | Branches | Source |")
        add("|---|---|---|---|---|---|---|")
        inv_by_id = {p["id"]: p for p in inv["programs"]}
        for p in logic["programs"]:
            ip = inv_by_id.get(p["id"], {})
            add(f"| {p['name']} | {p['kind']} | {', '.join(_short(x) for x in ip.get('executed_by', [])) or '-'} | "
                f"{p['metrics']['paragraphs']} | {p['metrics']['complexity']} | {p['metrics']['branches']} | `{p['path']}` |")
        add("")

        add("## 7. Gaps and risks")
        add("")
        add(f"{len(self.gaps)} gaps, consolidated from every stage. Full register with all locations: "
            "`gaps_register.md`.")
        add("")
        add("| Gap | Severity | Category | Finding | First location | Action |")
        add("|---|---|---|---|---|---|")
        for g in self.gaps:
            if g["severity"] == "low":
                continue
            first = _location(g["locations"][0]) if g["locations"] else ""
            add(f"| `{g['id']}` | {g['severity']} | {g['category']} | {_cell(g['title'])} | {first} | "
                f"{_cell(g['action'])} |")
        add("")

        add("## 8. Recommendations")
        add("")
        add(self.text("brd:recommendations", "narrative") or "*Not yet written by the synthesis agent.*")
        add("")

        add("## 9. About this document")
        add("")
        add("| Stage | Artifact | SHA-256 |")
        add("|---|---|---|")
        for stage, sha in art["meta"]["inputs"].items():
            add(f"| {stage} | `output/{ARTIFACT[stage]}` | `{sha[:16]}` |")
        add("")
        if self.problems:
            add("Annotations rejected by the checker (not used in this document):")
            add("")
            for p in self.problems[:50]:
                add(f"- {p}")
            add("")
        add("Glossary: *copybook* = shared source member copied into programs; *paragraph* = named block of "
            "code; *PERFORM* = call a paragraph; *JCL* = batch job definition; *CICS* = online transaction "
            "monitor; *VSAM KSDS* = keyed file; *88 level* = named coded value of a field.")
        add("")
        return "\n".join(L).replace("\n\n\n", "\n\n")

    def _diagram(self, did: str, caption: str) -> str:
        d = next((x for x in self.a["diagram"]["diagrams"] if x["id"] == did), None)
        if not d:
            return ""
        text = (self.out / "diagram" / d["file"]).read_text(encoding="utf-8").rstrip()
        cap = self.ann.get(did, {}).get("caption")
        out = [f"**{caption}.** " + (f"{cap['text']} *(confidence: {cap['confidence']})*" if cap else d["title"]), "",
               "```mermaid", text, "```", ""]
        return "\n".join(out)

    def render_summary(self, art: dict) -> str:
        L = [f"# {self.system}: summary", ""]
        L.append(self.text("brd:executive_summary", "narrative") or "*Executive summary not yet written.*")
        L += ["", "## Highest-severity gaps", ""]
        for g in [g for g in self.gaps if g["severity"] == "high"][:15]:
            L.append(f"- {g['category']}: {_cell(g['title'])} (`{g['id']}`) — {g['action']}")
        L += ["", "## Recommendations", "", self.text("brd:recommendations", "narrative") or "*Not yet written.*", ""]
        return "\n".join(L)

    def render_gaps(self) -> str:
        L = ["# Gaps and risks register", "", f"{len(self.gaps)} gaps.", ""]
        for g in self.gaps:
            L += [f"## {g['id']} ({g['severity']}, {g['category']})", "", _cell(g["title"]), "",
                  f"Action: {g['action']}", ""]
            note = self.text(g["id"], "note")
            if note:
                L += [f"Review note: {note}", ""]
            if g["count"] > 1:
                L += [f"- {_cell(m)}" for m in g["details"]] + [""]
            if g["locations"]:
                L.append("Locations: " + ", ".join(_location(loc) for loc in g["locations"]))
                L.append("")
        return "\n".join(L)


def compute_stats(art: dict, ann: dict | None = None) -> dict:
    gaps = art["gaps"]
    out = {
        "entry_points": len(art["entry_points"]),
        "sections": len(art["sections"]),
        "gaps": len(gaps),
        "gaps_by_severity": dict(sorted(Counter(g["severity"] for g in gaps).items(),
                                        key=lambda kv: ["high", "medium", "low"].index(kv[0]))),
        "gaps_by_category": dict(sorted(Counter(g["category"] for g in gaps).items())),
        "annotation_problems": len(art["annotation_problems"]),
    }
    if ann is not None:
        out["annotations_used"] = sum(len(v) for v in ann.values())
    else:
        out["annotations_used"] = art["stats"].get("annotations_used", 0)
    return out


def run_synthesis(out_root: Path, system_name: str | None = None, timestamp: bool = True) -> dict[str, Any]:
    brd = Brd(out_root, system_name, timestamp)
    artifact, brd_md, summary_md, gaps_md = brd.build()
    final = out_root / "final_report"
    final.mkdir(parents=True, exist_ok=True)
    (final / "synthesis_artifact.json").write_text(json.dumps(artifact, indent=1, ensure_ascii=False) + "\n",
                                                   encoding="utf-8")
    (final / "brd.md").write_text(brd_md, encoding="utf-8")
    (final / "brd_summary.md").write_text(summary_md, encoding="utf-8")
    (final / "gaps_register.md").write_text(gaps_md, encoding="utf-8")
    return artifact
