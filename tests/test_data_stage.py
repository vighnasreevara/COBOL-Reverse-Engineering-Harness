"""Stage 3: layouts, SQL checks, data model, annotations."""

import json
from pathlib import Path

import pytest
from conftest import fixed

from harness.cobol.source import normalize
from harness.cobol.tokens import tokenize
from harness.common.annotations import check_annotations
from harness.data.build import run_data
from harness.data.layout import build_records, flatten
from harness.data.sqlmap import check_sql
from harness.data.validate import validate_data
from harness.inventory.builder import InventoryBuilder, ScanOptions
from harness.parser.build import run_parser
from harness.parser.data import parse_data


def layout(*lines):
    entries = [e.to_dict() for e in parse_data(tokenize(normalize(fixed("WORKING-STORAGE SECTION.", *lines)).code,
                                                        "w.cbl").tokens).entries]
    records, problems = build_records(entries)
    return {f["qualified_name"]: f for rec, _ in records for f in flatten(rec)}, records, problems


def test_sizes_offsets_usage_inheritance():
    f, records, problems = layout(
        "01 REC.",
        "   05 A PIC X(10).",
        "   05 B PIC S9(7)V99 COMP-3.",
        "   05 C PIC S9(4) COMP.",
        "   05 D PIC S9(9) BINARY.",
        "   05 E PIC S9(5)V99 SIGN LEADING SEPARATE.",
        "   05 F COMP-3.",
        "      10 F1 PIC S9(3).",
        "      10 F2 PIC 9(5).",
        "   05 G OCCURS 3 TIMES.",
        "      10 G1 PIC X(2).",
        "      10 G2 PIC 9(3) COMP.",
        "   05 H PIC X(4).",
        "   05 H-R REDEFINES H PIC 9(4).",
        "   05 I USAGE POINTER.",
        "   05 J COMP-2.",
    )
    assert problems == []
    expect = {"REC.A": (0, 10), "REC.B": (10, 5), "REC.C": (15, 2), "REC.D": (17, 4), "REC.E": (21, 8),
              "REC.F": (29, 5), "REC.F.F1": (29, 2), "REC.F.F2": (31, 3), "REC.G": (34, 4), "REC.G.G2": (36, 2),
              "REC.H": (46, 4), "REC.H-R": (46, 4), "REC.I": (50, 4), "REC.J": (54, 8)}
    for name, (off, size) in expect.items():
        assert (f[name]["offset"], f[name]["size"]) == (off, size), name
    assert records[0][0].size == 62
    assert f["REC.F.F1"]["usage"] == "COMP-3"


def test_conditions_and_layout_problems():
    f, records, problems = layout(
        "01 REC.",
        "   05 STATUS-CODE PIC X VALUE 'A'.",
        "      88 ACTIVE VALUE 'A'.",
        "      88 CLOSED VALUES 'C' 'X'.",
        "   05 SHORT-F PIC X(3) VALUE 'TOO LONG'.",
        "   05 SMALL PIC X(2).",
        "   05 BIG REDEFINES SMALL PIC X(5).",
        "01 EMPTY-REC.",
        "01 NEXT-REC PIC X.",
    )
    conds = f["REC.STATUS-CODE"]["conditions"]
    assert [(c["name"], [v["value"] for v in c["values"]]) for c in conds] == [("ACTIVE", ["A"]), ("CLOSED", ["C", "X"])]
    assert sorted(p["code"] for p in problems) == ["empty_record", "redefines_larger_than_target", "value_too_long"]


def test_sql_checks():
    fields = {
        "HV-ID": {"name": "HV-ID", "category": "alphanumeric", "size": 10, "usage": "DISPLAY", "record": "r",
                  "qualified_name": "R.HV-ID"},
        "HV-AMT": {"name": "HV-AMT", "category": "numeric", "size": 8, "usage": "COMP-3", "digits": 15, "scale": 2,
                   "record": "r", "qualified_name": "R.HV-AMT"},
        "HV-CNT": {"name": "HV-CNT", "category": "numeric", "size": 2, "usage": "COMP", "digits": 4, "scale": 0,
                   "record": "r", "qualified_name": "R.HV-CNT"},
    }
    tables = {"T1": {"name": "T1", "columns": [{"name": "ID", "type": "CHAR(8)"}, {"name": "AMT", "type": "DECIMAL(15,2)"},
                                               {"name": "CNT", "type": "INTEGER"}]}}

    def stmt(text):
        return {"text": f"EXEC SQL {text} END-EXEC", "file": "p.cbl", "line": 1}

    pairs, problems = check_sql([
        stmt("INSERT INTO T1 (ID, AMT, CNT) VALUES (:HV-ID, :HV-AMT, :HV-CNT)"),
        stmt("INSERT INTO T1 (ID, AMT) VALUES (:HV-ID)"),
        stmt("SELECT NOPE INTO :HV-ID FROM T1 WHERE AMT = :HV-AMT"),
    ], fields, tables)
    codes = sorted(p["code"] for p in problems)
    assert codes == ["host_variable_type_mismatch", "host_variable_type_mismatch", "host_variable_type_mismatch",
                     "sql_column_count_mismatch", "unknown_column"]
    assert {(p["column"], p.get("mismatch") is None) for p in pairs if p["operation"] == "INSERT"} >= {("AMT", True)}


def write(root: Path, rel: str, lines):
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture
def system(tmp_path):
    src = tmp_path / "src"
    write(src, "REC.cpy", fixed("01 MASTER-REC.", "   05 M-KEY PIC X(8).", "   05 M-DATA PIC X(42)."))
    write(src, "PGM.cbl", fixed(
        "IDENTIFICATION DIVISION.", "PROGRAM-ID. PGM.", "ENVIRONMENT DIVISION.", "INPUT-OUTPUT SECTION.",
        "FILE-CONTROL.", "    SELECT MASTER ASSIGN TO MASTDD ORGANIZATION IS INDEXED.",
        "    SELECT REPORT-OUT ASSIGN TO RPTDD.", "DATA DIVISION.", "FILE SECTION.", "FD MASTER.", "    COPY REC.",
        "FD REPORT-OUT.", "01 RPT-LINE PIC X(120).", "PROCEDURE DIVISION.", "    OPEN INPUT MASTER OUTPUT REPORT-OUT",
        "    READ MASTER", "    CLOSE MASTER REPORT-OUT", "    GOBACK."))
    write(src, "RUN.jcl", ["//RUN JOB 1", "//S1 EXEC PGM=PGM", "//MASTDD DD DSN=PROD.MASTER,DISP=SHR",
                           "//RPTDD DD DSN=PROD.RPT,DISP=(NEW,CATLG),DCB=(RECFM=FB,LRECL=132)"])
    write(src, "def.idcams", ["  DEFINE CLUSTER (NAME(PROD.MASTER) INDEXED KEYS(10 0) RECORDSIZE(50 50))"])
    out = tmp_path / "out"
    inv = InventoryBuilder(ScanOptions(root=src, timestamp=False)).build()
    (out / "inventory").mkdir(parents=True)
    (out / "inventory" / "inventory_artifact.json").write_text(json.dumps(inv), encoding="utf-8")
    run_parser(out / "inventory" / "inventory_artifact.json", out / "parser", timestamp=False)
    return src, out


def test_small_system_model(system):
    src, out = system
    a = run_data(out / "inventory" / "inventory_artifact.json", out / "parser" / "parser_artifact.json",
                 out / "data", timestamp=False)
    assert validate_data(a) == []
    codes = sorted(i["code"] for i in a["issues"])
    assert codes == ["record_length_mismatch", "vsam_key_mismatch"]
    flows = {(f["entity"], f["direction"]) for f in a["data_flows"]}
    assert flows == {("dataset:PROD.MASTER", "read"), ("dataset:PROD.RPT", "write")}
    master = next(e for e in a["entities"] if e["id"] == "dataset:PROD.MASTER")
    assert master["records"] == ["record:REC/MASTER-REC"] and master["organization"] == "KSDS"


def test_annotations_are_checked(system):
    src, out = system
    a = run_data(out / "inventory" / "inventory_artifact.json", out / "parser" / "parser_artifact.json",
                 out / "data", timestamp=False)
    good = {"items": [{"target": "record:REC/MASTER-REC", "kind": "description", "text": "Master record",
                       "confidence": "high", "evidence": [{"path": "REC.cpy", "line": 1},
                                                          {"id": "field:REC/MASTER-REC:MASTER-REC.M-KEY"}]}]}
    assert check_annotations(good, a, src) == []
    bad = {"items": [{"target": "record:NOPE", "kind": "story", "text": "", "confidence": "sure",
                      "evidence": [{"path": "REC.cpy", "line": 99}, {"id": "field:none"}]}]}
    problems = check_annotations(bad, a, src)
    assert len(problems) == 6
