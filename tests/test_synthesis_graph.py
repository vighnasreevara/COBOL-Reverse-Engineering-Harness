"""Stages 7 and 8, and the full pipeline command."""

import csv
import json

import pytest

from harness.cli import main
from harness.graph.build import check_queries
from harness.graph.validate import validate_graph
from harness.synthesis.gaps import build_gaps
from harness.synthesis.validate import validate_synthesis


def test_gaps_group_and_rank():
    issues = {"parser": [
        {"severity": "warning", "code": "undefined_procedure", "message": "PERFORM X", "subject": "program:A",
         "path": "a.cbl", "line": 5},
        {"severity": "warning", "code": "undefined_procedure", "message": "PERFORM Y", "subject": "program:A",
         "path": "a.cbl", "line": 6},
        {"severity": "info", "code": "unused_copybook", "message": "unused", "subject": "copybook:C", "path": "c.cpy"},
        {"severity": "info", "code": "not_in_policy", "message": "ignored"},
    ]}
    gaps = build_gaps(issues, [{"program": "program:B", "file": "b.cbl", "line": 9, "id": "rule:B:9"}])
    assert [(g["id"], g["severity"], g["count"]) for g in gaps] == [
        ("gap:unenforced_business_rules:B", "high", 1),      # logic risk ranks before incomplete code
        ("gap:undefined_procedure:A", "high", 2), ("gap:unused_copybook:C", "low", 1),
        ("gap:not_in_policy:system", "low", 1)]                # unlisted info codes are kept, as low
    assert gaps[1]["locations"] == [{"path": "a.cbl", "line": 5}, {"path": "a.cbl", "line": 6}]


def test_query_library_matches_schema():
    assert check_queries() == []


def test_unlisted_issue_codes_are_not_dropped():
    gaps = build_gaps({"inventory": [
        {"severity": "error", "code": "brand_new_code", "message": "x", "subject": "program:A"},
        {"severity": "info", "code": "nonstandard_comment", "message": "style", "subject": "program:A"},
    ]}, [])
    assert [(g["code"], g["severity"]) for g in gaps] == [("brand_new_code", "high")]


def test_run_all_end_to_end(tmp_path):
    from conftest import write_mini_system

    src = write_mini_system(tmp_path / "src")
    out = tmp_path / "output"
    assert main(["run-all", "--root", str(src), "--output-root", str(out), "--system", "Orders",
                 "--no-timestamp"]) == 0
    for rel in ("inventory/inventory_artifact.json", "parser/parser_artifact.json", "data/data_artifact.json",
                "logic/logic_artifact.json", "rules/rules_catalog.md", "diagram/diagrams.md",
                "final_report/brd.md", "final_report/gaps_register.md", "final_report/graph/neo4j/import.cypher"):
        assert (out / rel).is_file(), rel
    synth = json.loads((out / "final_report" / "synthesis_artifact.json").read_text(encoding="utf-8"))
    assert validate_synthesis(synth, out / "final_report") == []
    gdir = out / "final_report" / "graph" / "neo4j"
    graph = json.loads((gdir / "graph_artifact.json").read_text(encoding="utf-8"))
    assert validate_graph(graph, gdir) == []
    with (gdir / "nodes" / "Program.csv").open(encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    assert header[0] == "id:ID" and header[-1] == ":LABEL"
    rules = json.loads((out / "rules" / "rules_artifact.json").read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in rules["rules"]}
    assert by_id["rule:ORDBATCH:29"]["category"] == "validation"
    data = json.loads((out / "data" / "data_artifact.json").read_text(encoding="utf-8"))
    assert {(f["program"], f["entity"], f["direction"]) for f in data["data_flows"]} >= {
        ("program:ORDBATCH", "dataset:PROD.ORDERS", "read"), ("program:ORDINQ", "table:ORDERS_T", "read")}
