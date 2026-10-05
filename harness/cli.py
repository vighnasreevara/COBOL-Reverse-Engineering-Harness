"""Command line entry: ``python -m harness <command> ...``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _inventory(args: argparse.Namespace) -> int:
    from harness.inventory.builder import DEFAULT_EXCLUDE_DIRS, InventoryBuilder, ScanOptions
    from harness.inventory.report import summary_text
    from harness.inventory.validate import validate

    root = Path(args.root)
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2
    options = ScanOptions(
        root=root,
        exclude_dirs=set(DEFAULT_EXCLUDE_DIRS) | set(args.exclude_dir or []),
        exclude_globs=args.exclude or [],
        entry_points=args.entry or [],
        timestamp=not args.no_timestamp,
    )
    try:
        artifact = InventoryBuilder(options).build()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    problems = validate(artifact)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "inventory_artifact.json"
    out_file.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(summary_text(artifact, out_file))
    if problems:
        print("\nVALIDATION FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    return 0


def _validate(args: argparse.Namespace) -> int:
    from harness.inventory.validate import validate

    artifact = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
    problems = validate(artifact)
    if problems:
        for p in problems:
            print(f"- {p}")
        return 1
    print("inventory artifact is valid")
    return 0


def _parse(args: argparse.Namespace) -> int:
    from harness.parser.build import run_parser
    from harness.parser.report import summary_text
    from harness.parser.validate import validate_manifest

    inventory = Path(args.inventory)
    if not inventory.is_file():
        print(f"error: inventory artifact {inventory} not found (run the inventory stage first)", file=sys.stderr)
        return 2
    out_dir = Path(args.out)
    manifest = run_parser(inventory, out_dir, programs=args.program, timestamp=not args.no_timestamp)
    print(summary_text(manifest, out_dir))
    problems = validate_manifest(manifest, out_dir)
    return _report_problems(problems)


def _validate_parser(args: argparse.Namespace) -> int:
    from harness.parser.validate import validate_manifest

    path = Path(args.artifact)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    problems = validate_manifest(manifest, path.parent)
    if not problems:
        print("parser artifact is valid")
    return _report_problems(problems)


def _data(args: argparse.Namespace) -> int:
    from harness.data.build import run_data
    from harness.data.report import summary_text
    from harness.data.validate import validate_data

    for p in (args.inventory, args.parser):
        if not Path(p).is_file():
            print(f"error: {p} not found (run the earlier stages first)", file=sys.stderr)
            return 2
    out_dir = Path(args.out)
    artifact = run_data(Path(args.inventory), Path(args.parser), out_dir, timestamp=not args.no_timestamp)
    print(summary_text(artifact, out_dir / "data_artifact.json"))
    return _report_problems(validate_data(artifact))


def _logic(args: argparse.Namespace) -> int:
    from harness.logic.build import run_logic
    from harness.logic.validate import validate_logic

    for p in (args.inventory, args.parser, args.data):
        if not Path(p).is_file():
            print(f"error: {p} not found (run the earlier stages first)", file=sys.stderr)
            return 2
    out_dir = Path(args.out)
    m = run_logic(Path(args.inventory), Path(args.parser), Path(args.data), out_dir, timestamp=not args.no_timestamp)
    s = m["stats"]
    print("=== Logic complete ===")
    print(f"Programs        : {s['programs']} ({', '.join(f'{k} {v}' for k, v in s['programs_by_kind'].items())})")
    print(f"Branches / loops: {s['branches']} / {s['loops']}")
    print(f"SQL / CICS / CALL: {s['sql_statements']} / {s['cics_commands']} / {s['calls']}")
    print(f"Pseudocode lines: {s['pseudocode_lines']}")
    print(f"Most complex    : {', '.join(f'{n} ({c})' for n, c in s['most_complex'][:5])}")
    print(f"Issues          : {s['issues']} {s['issues_by_code'] or ''}")
    print(f"Output          : {out_dir / 'logic_artifact.json'}")
    print("======================")
    return _report_problems(validate_logic(m, out_dir))


def _validate_logic(args: argparse.Namespace) -> int:
    from harness.logic.validate import validate_logic

    path = Path(args.artifact)
    problems = validate_logic(json.loads(path.read_text(encoding="utf-8")), path.parent)
    if not problems:
        print("logic artifact is valid")
    return _report_problems(problems)


def _rules(args: argparse.Namespace) -> int:
    from harness.rules.build import run_rules
    from harness.rules.render import annotation_index, render_catalog
    from harness.rules.validate import validate_rules

    for p in (args.parser, args.logic):
        if not Path(p).is_file():
            print(f"error: {p} not found (run the earlier stages first)", file=sys.stderr)
            return 2
    out_dir = Path(args.out)
    a = run_rules(Path(args.parser), Path(args.logic), out_dir, timestamp=not args.no_timestamp)
    problems = validate_rules(a, Path(args.logic).parent)
    ann_path = out_dir / "rules_annotations.json"
    if ann_path.is_file():
        from harness.common.annotations import check_annotations
        ann_problems = check_annotations(json.loads(ann_path.read_text(encoding="utf-8")), a, Path(a["meta"]["root"]))
        if ann_problems:
            print("rules_annotations.json has problems; the catalogue is rendered without it", file=sys.stderr)
            problems += [f"annotations: {p}" for p in ann_problems]
            ann_path = None
    (out_dir / "rules_catalog.md").write_text(render_catalog(a, annotation_index(ann_path)), encoding="utf-8")
    s = a["stats"]
    print("=== Rules complete ===")
    print(f"Candidates      : {s['candidates']} ({', '.join(f'{k} {v}' for k, v in s['by_category'].items())})")
    print(f"Business rules  : {s['business_rules']} in {s['programs_with_business_rules']} programs "
          f"({s['unreachable_business_rules']} unreachable)")
    print(f"Repeated groups : {s['repeated_groups']}")
    print(f"Output          : {out_dir / 'rules_artifact.json'}, {out_dir / 'rules_catalog.md'}")
    print("======================")
    return _report_problems(problems)


def _diagrams(args: argparse.Namespace) -> int:
    from harness.diagram.build import run_diagrams
    from harness.diagram.render import gallery
    from harness.diagram.validate import validate_diagrams

    inputs = (args.inventory, args.parser, args.data, args.logic)
    for p in inputs:
        if not Path(p).is_file():
            print(f"error: {p} not found (run the earlier stages first)", file=sys.stderr)
            return 2
    out_dir = Path(args.out)
    a = run_diagrams(*(Path(p) for p in inputs), out_dir, timestamp=not args.no_timestamp)
    problems = validate_diagrams(a, out_dir)
    ann_path = out_dir / "diagram_annotations.json"
    if ann_path.is_file():
        from harness.common.annotations import check_annotations
        ann_problems = check_annotations(json.loads(ann_path.read_text(encoding="utf-8")), a, Path(a["meta"]["root"]))
        if ann_problems:
            problems += [f"annotations: {p}" for p in ann_problems]
            ann_path = None
    else:
        ann_path = None
    pages = gallery(a, out_dir, ann_path)
    s = a["stats"]
    print("=== Diagrams complete ===")
    print(f"Diagrams        : {s['diagrams']} ({', '.join(f'{k} {v}' for k, v in s['by_type'].items())})")
    print(f"Lint problems   : {s['with_lint_problems']} | truncated: {s['truncated']}")
    print(f"Galleries       : {', '.join(str(p) for p in pages)}")
    print("=========================")
    return _report_problems(problems)


def _synthesize(args: argparse.Namespace) -> int:
    from harness.synthesis.build import ARTIFACT, run_synthesis
    from harness.synthesis.validate import validate_synthesis

    out = Path(args.output_root)
    missing = [p for p in ARTIFACT.values() if not (out / p).is_file()]
    if missing:
        print(f"error: missing artifacts {missing} (run stages 1-6 first)", file=sys.stderr)
        return 2
    a = run_synthesis(out, args.system, timestamp=not args.no_timestamp)
    s = a["stats"]
    print("=== Synthesis complete ===")
    print(f"Entry points    : {s['entry_points']}")
    print(f"Gaps            : {s['gaps']} ({', '.join(f'{k} {v}' for k, v in s['gaps_by_severity'].items())})")
    print(f"Annotations used: {s['annotations_used']} (rejected: {s['annotation_problems']})")
    print(f"Output          : {out / 'final_report' / 'brd.md'}")
    print("==========================")
    return _report_problems(validate_synthesis(a, out / "final_report"))


def _graph(args: argparse.Namespace) -> int:
    from harness.graph.build import run_graph
    from harness.graph.validate import validate_graph

    out = Path(args.output_root)
    needed = ["inventory/inventory_artifact.json", "data/data_artifact.json", "logic/logic_artifact.json",
              "rules/rules_artifact.json"]
    missing = [p for p in needed if not (out / p).is_file()]
    if missing:
        print(f"error: missing artifacts {missing}", file=sys.stderr)
        return 2
    a = run_graph(out, args.system, timestamp=not args.no_timestamp)
    s = a["stats"]
    print("=== Graph export complete ===")
    print(f"Nodes           : {s['nodes']} ({', '.join(f'{k} {v}' for k, v in s['nodes_by_label'].items())})")
    print(f"Relationships   : {s['relationships']}")
    print(f"Output          : {out / 'final_report' / 'graph' / 'neo4j'}")
    print("=============================")
    return _report_problems(validate_graph(a, out / "final_report" / "graph" / "neo4j"))


def _run_all(args: argparse.Namespace) -> int:
    out = args.output_root
    stages = [
        ["inventory", "--root", args.root, "--out", f"{out}/inventory"]
        + sum((["--entry", e] for e in args.entry or []), []) + sum((["--exclude", e] for e in args.exclude or []), []),
        ["parse", "--inventory", f"{out}/inventory/inventory_artifact.json", "--out", f"{out}/parser"],
        ["data", "--inventory", f"{out}/inventory/inventory_artifact.json", "--parser",
         f"{out}/parser/parser_artifact.json", "--out", f"{out}/data"],
        ["logic", "--inventory", f"{out}/inventory/inventory_artifact.json", "--parser",
         f"{out}/parser/parser_artifact.json", "--data", f"{out}/data/data_artifact.json", "--out", f"{out}/logic"],
        ["rules", "--parser", f"{out}/parser/parser_artifact.json", "--logic", f"{out}/logic/logic_artifact.json",
         "--out", f"{out}/rules"],
        ["diagrams", "--inventory", f"{out}/inventory/inventory_artifact.json", "--parser",
         f"{out}/parser/parser_artifact.json", "--data", f"{out}/data/data_artifact.json", "--logic",
         f"{out}/logic/logic_artifact.json", "--out", f"{out}/diagram"],
        ["synthesize", "--output-root", out] + (["--system", args.system] if args.system else []),
        ["graph", "--output-root", out] + (["--system", args.system] if args.system else []),
    ]
    for argv in stages:
        if args.no_timestamp:
            argv.append("--no-timestamp")
        print(f"\n>>> {argv[0]}")
        code = main(argv)
        if code != 0:
            print(f"\nstopped: stage '{argv[0]}' exited with {code}", file=sys.stderr)
            return code
    print("\nAll 8 stages completed and validated.")
    return 0


def _validate_json(validator_path: str):
    def run(args: argparse.Namespace) -> int:
        module, func = validator_path.rsplit(".", 1)
        validator = getattr(__import__(module, fromlist=[func]), func)
        doc = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
        problems = validator(doc)
        if not problems:
            print("artifact is valid")
        return _report_problems(problems)
    return run


def _check_annotations(args: argparse.Namespace) -> int:
    from harness.common.annotations import load_and_check

    problems = load_and_check(Path(args.annotations), [Path(a) for a in args.artifact],
                              Path(args.root) if args.root else None)
    if not problems:
        print("annotations are valid")
    return _report_problems(problems)


def _sync_agents(args: argparse.Namespace) -> int:
    from harness.sync_agents import sync

    for path in sync():
        print(f"wrote {path}")
    return 0


def _report_problems(problems: list[str]) -> int:
    if problems:
        print("\nVALIDATION FAILED:", file=sys.stderr)
        for p in problems[:100]:
            print(f"  - {p}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness", description="COBOL reverse engineering harness tools")
    sub = parser.add_subparsers(dest="command", required=True)

    inv = sub.add_parser("inventory", help="scan a COBOL codebase and write inventory_artifact.json")
    inv.add_argument("--root", required=True, help="folder containing the COBOL system")
    inv.add_argument("--out", default="output/inventory", help="output folder (default: output/inventory)")
    inv.add_argument("--entry", action="append", metavar="[KIND:]NAME",
                     help="limit to what is reachable from a job, transaction, program or proc (repeatable)")
    inv.add_argument("--exclude", action="append", metavar="GLOB", help="skip files matching a path glob (repeatable)")
    inv.add_argument("--exclude-dir", action="append", metavar="NAME", help="skip directories with this name")
    inv.add_argument("--no-timestamp", action="store_true", help="omit generated_at (reproducible output)")
    inv.set_defaults(func=_inventory)

    val = sub.add_parser("validate-inventory", help="check an existing inventory_artifact.json")
    val.add_argument("artifact")
    val.set_defaults(func=_validate)

    prs = sub.add_parser("parse", help="stage 2: parse every program listed in the inventory")
    prs.add_argument("--inventory", default="output/inventory/inventory_artifact.json")
    prs.add_argument("--out", default="output/parser")
    prs.add_argument("--program", action="append", metavar="NAME", help="parse only this program (repeatable)")
    prs.add_argument("--no-timestamp", action="store_true")
    prs.set_defaults(func=_parse)

    vprs = sub.add_parser("validate-parser", help="check an existing parser_artifact.json and its program files")
    vprs.add_argument("artifact")
    vprs.set_defaults(func=_validate_parser)

    dat = sub.add_parser("data", help="stage 3: record layouts, data model and data flows")
    dat.add_argument("--inventory", default="output/inventory/inventory_artifact.json")
    dat.add_argument("--parser", default="output/parser/parser_artifact.json")
    dat.add_argument("--out", default="output/data")
    dat.add_argument("--no-timestamp", action="store_true")
    dat.set_defaults(func=_data)

    vdat = sub.add_parser("validate-data", help="check an existing data_artifact.json")
    vdat.add_argument("artifact")
    vdat.set_defaults(func=_validate_json("harness.data.validate.validate_data"))

    lg = sub.add_parser("logic", help="stage 4: outline, branches, loops, I/O and pseudocode per program")
    lg.add_argument("--inventory", default="output/inventory/inventory_artifact.json")
    lg.add_argument("--parser", default="output/parser/parser_artifact.json")
    lg.add_argument("--data", default="output/data/data_artifact.json")
    lg.add_argument("--out", default="output/logic")
    lg.add_argument("--no-timestamp", action="store_true")
    lg.set_defaults(func=_logic)

    vlg = sub.add_parser("validate-logic", help="check an existing logic_artifact.json and its program files")
    vlg.add_argument("artifact")
    vlg.set_defaults(func=_validate_logic)

    rl = sub.add_parser("rules", help="stage 5: business-rule candidates and the rules catalogue")
    rl.add_argument("--parser", default="output/parser/parser_artifact.json")
    rl.add_argument("--logic", default="output/logic/logic_artifact.json")
    rl.add_argument("--out", default="output/rules")
    rl.add_argument("--no-timestamp", action="store_true")
    rl.set_defaults(func=_rules)

    vrl = sub.add_parser("validate-rules", help="check an existing rules_artifact.json")
    vrl.add_argument("artifact")
    vrl.set_defaults(func=_validate_json("harness.rules.validate.validate_rules"))

    dg = sub.add_parser("diagrams", help="stage 6: Mermaid diagrams and galleries")
    dg.add_argument("--inventory", default="output/inventory/inventory_artifact.json")
    dg.add_argument("--parser", default="output/parser/parser_artifact.json")
    dg.add_argument("--data", default="output/data/data_artifact.json")
    dg.add_argument("--logic", default="output/logic/logic_artifact.json")
    dg.add_argument("--out", default="output/diagram")
    dg.add_argument("--no-timestamp", action="store_true")
    dg.set_defaults(func=_diagrams)

    sy = sub.add_parser("synthesize", help="stage 7: BRD, summary and gaps register")
    sy.add_argument("--output-root", default="output", help="folder holding the stage outputs")
    sy.add_argument("--system", help="system name for the document title")
    sy.add_argument("--no-timestamp", action="store_true")
    sy.set_defaults(func=_synthesize)

    gr = sub.add_parser("graph", help="stage 8: Neo4j CSVs, import.cypher and query library")
    gr.add_argument("--output-root", default="output")
    gr.add_argument("--system")
    gr.add_argument("--no-timestamp", action="store_true")
    gr.set_defaults(func=_graph)

    ra = sub.add_parser("run-all", help="run stages 1-8 in order, stopping at the first failure")
    ra.add_argument("--root", required=True, help="folder containing the COBOL system")
    ra.add_argument("--output-root", default="output")
    ra.add_argument("--system", help="system name for the BRD and graph")
    ra.add_argument("--entry", action="append", metavar="[KIND:]NAME")
    ra.add_argument("--exclude", action="append", metavar="GLOB")
    ra.add_argument("--no-timestamp", action="store_true")
    ra.set_defaults(func=_run_all)

    ann = sub.add_parser("check-annotations", help="check an agent-written annotations file against an artifact")
    ann.add_argument("annotations")
    ann.add_argument("--artifact", required=True, action="append",
                     help="artifact file or folder of artifacts whose ids may be cited (repeatable)")
    ann.add_argument("--root", help="source root for path:line evidence (default: artifact meta.root)")
    ann.set_defaults(func=_check_annotations)

    syn = sub.add_parser("sync-agents", help="regenerate .github agent/skill copies from .claude")
    syn.set_defaults(func=_sync_agents)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
