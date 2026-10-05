"""Stage 4: pseudocode, branches, loop termination, outline."""

import json
from pathlib import Path

import pytest
from conftest import fixed

from harness.data.build import run_data
from harness.inventory.builder import InventoryBuilder, ScanOptions
from harness.logic.build import run_logic
from harness.logic.validate import validate_logic
from harness.parser.build import run_parser


def build_all(src: Path, out: Path):
    inv = InventoryBuilder(ScanOptions(root=src, timestamp=False)).build()
    (out / "inventory").mkdir(parents=True, exist_ok=True)
    (out / "inventory" / "inventory_artifact.json").write_text(json.dumps(inv), encoding="utf-8")
    run_parser(out / "inventory" / "inventory_artifact.json", out / "parser", timestamp=False)
    run_data(out / "inventory" / "inventory_artifact.json", out / "parser" / "parser_artifact.json", out / "data",
             timestamp=False)
    return run_logic(out / "inventory" / "inventory_artifact.json", out / "parser" / "parser_artifact.json",
                     out / "data" / "data_artifact.json", out / "logic", timestamp=False)


@pytest.fixture
def program(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "LOOPS.cbl").write_text("\n".join(fixed(
        "IDENTIFICATION DIVISION.", "PROGRAM-ID. LOOPS.", "DATA DIVISION.", "WORKING-STORAGE SECTION.",
        "01 WS-FLAGS.",
        "   05 WS-EOF PIC X VALUE 'N'.",
        "      88 END-OF-FILE VALUE 'Y'.",
        "01 WS-TIME.",
        "   05 WS-HOUR PIC 99.",
        "   05 WS-MIN PIC 99.",
        "01 WS-N PIC 9 VALUE 0.",
        "PROCEDURE DIVISION.",
        "MAIN-PARA.",
        "    PERFORM READ-PARA UNTIL END-OF-FILE",
        "    PERFORM TICK UNTIL WS-HOUR = 23",
        "    PERFORM STUCK UNTIL WS-N > 5",
        "    IF END-OF-FILE",
        "       DISPLAY 'DONE'",
        "    ELSE",
        "       CALL 'OTHER'",
        "    END-IF",
        "    GOBACK.",
        "READ-PARA.", "    SET END-OF-FILE TO TRUE.",
        "TICK.", "    ACCEPT WS-TIME FROM TIME.",
        "STUCK.", "    DISPLAY WS-N.",
    )), encoding="utf-8")
    m = build_all(src, tmp_path / "out")
    doc = json.loads((tmp_path / "out" / "logic" / "program_logic" / "LOOPS.json").read_text(encoding="utf-8"))
    return m, doc, tmp_path / "out" / "logic"


def test_valid(program):
    m, _, out = program
    assert validate_logic(m, out) == []


def test_loop_termination(program):
    m, doc, _ = program
    term = {lp["condition"]: lp["termination"] for lp in doc["loops"]}
    assert term == {"END-OF-FILE": "condition_updated_in_loop",      # 88 set TRUE in performed paragraph
                    "WS-HOUR = 23": "condition_updated_in_loop",     # group WS-TIME written by ACCEPT
                    "WS-N > 5": "potential_infinite"}
    assert [i["code"] for i in m["issues"]] == ["potential_infinite_loop"]


def test_branches_explain_88_levels(program):
    _, doc, _ = program
    b = next(x for x in doc["branches"] if x["kind"] == "IF")
    assert b["conditions_explained"][0]["field"] == "WS-EOF"
    assert b["outcomes"] == [{"when": "true", "actions": ["DISPLAY 'DONE'"]}, {"when": "false", "actions": ["call OTHER"]}]


def test_pseudocode_and_outline(program):
    _, doc, _ = program
    main = doc["paragraphs"][0]
    text = [line["text"] for line in main["pseudocode"]]
    assert text[0] == "WHILE NOT (END-OF-FILE   [END-OF-FILE means WS-EOF = 'Y']): DO READ-PARA"
    assert "RETURN TO CALLER" in text
    kinds = [(n["kind"], n.get("target"), n["when"]) for n in doc["outline"]]
    assert ("call", "OTHER", ["NOT (END-OF-FILE)"]) in kinds
    assert kinds[-1] == ("end", "GOBACK", [])
