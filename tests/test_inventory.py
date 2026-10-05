"""End-to-end inventory tests on a small hand-built system."""

import copy
import json
from pathlib import Path

import pytest
from conftest import fixed

from harness.cli import main
from harness.inventory.builder import InventoryBuilder, ScanOptions
from harness.inventory.validate import validate


def write(root: Path, rel: str, lines: list[str]) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\r\n".join(lines), encoding="utf-8")


@pytest.fixture
def system(tmp_path: Path) -> Path:
    write(tmp_path, "cbl/MAINPGM.cbl", fixed(
        "IDENTIFICATION DIVISION.",
        "PROGRAM-ID. MAINPGM.",
        "ENVIRONMENT DIVISION.",
        "INPUT-OUTPUT SECTION.",
        "FILE-CONTROL.",
        "    SELECT MASTER ASSIGN TO MASTERDD.",
        "    SELECT REPORT-OUT ASSIGN TO RPTDD.",
        "DATA DIVISION.",
        "WORKING-STORAGE SECTION.",
        "    COPY RECA.",
        "    COPY NOSUCH.",
        "    COPY UTILPGM.",
        "    COPY PROCCPY.",
        "PROCEDURE DIVISION.",
        "    CALL 'UTILPGM'",
        "    CALL 'CEEDAYS'",
        "    CALL 'GHOST'",
        "    GOBACK.",
    ))
    write(tmp_path, "cbl/UTILPGM.cbl", fixed(
        "IDENTIFICATION DIVISION.", "PROGRAM-ID. UTILPGM.", "DATA DIVISION.", "LINKAGE SECTION.",
        "01 LS-A PIC X.", "PROCEDURE DIVISION USING LS-A.", "    GOBACK.",
    ))
    write(tmp_path, "cbl/ORPHAN.cbl", fixed(
        "IDENTIFICATION DIVISION.", "PROGRAM-ID. ORPHAN.", "PROCEDURE DIVISION USING X.", "    GOBACK.",
    ))
    write(tmp_path, "cbl/ONLINE1.cbl", fixed(
        "IDENTIFICATION DIVISION.", "PROGRAM-ID. ONLINE1.", "PROCEDURE DIVISION.",
        "    EXEC CICS SEND MAP('M1') MAPSET('MS1') END-EXEC",
        "    EXEC CICS SEND MAP('M9') MAPSET('MS1') END-EXEC",
        "    EXEC CICS LINK PROGRAM('UTILPGM') END-EXEC",
        "    EXEC SQL SELECT A INTO :B FROM CUSTOMER END-EXEC",
        "    EXEC CICS RETURN TRANSID('T001') END-EXEC.",
    ))
    write(tmp_path, "cpy/RECA.cpy", fixed("01 REC-A.", "   05 A-ID PIC X(8)."))
    write(tmp_path, "cpy/UNUSED.cpy", fixed("01 REC-U PIC X."))
    write(tmp_path, "cpy/PROCCPY.cpy", fixed("01 P-AREA PIC X.", "P-ROUTINE.", "    CALL 'UTILPGM'", "    ."))
    write(tmp_path, "jcl/RUNMAIN.jcl", [
        "//RUNMAIN JOB 1",
        "//STEP1   EXEC PGM=MAINPGM",
        "//MASTERDD DD DSN=PROD.MASTER,DISP=SHR",
        "//SYSOUT  DD SYSOUT=*",
    ])
    write(tmp_path, "cics/system.csd", [
        "DEFINE TRANSACTION(T001) PROGRAM(ONLINE1) GROUP(G)",
        "DEFINE PROGRAM(ONLINE1) GROUP(G)",
        "DEFINE PROGRAM(LOSTPGM) GROUP(G)",
    ])
    write(tmp_path, "maps/MS1.bms", [
        "MS1      DFHMSD TYPE=MAP,LANG=COBOL",
        "M1       DFHMDI SIZE=(24,80)",
        "F1       DFHMDF POS=(1,1),LENGTH=5",
        "         DFHMSD TYPE=FINAL",
    ])
    write(tmp_path, "ddl/customer.sql", ["CREATE TABLE CUSTOMER (ID CHAR(8) NOT NULL, PRIMARY KEY (ID));"])
    write(tmp_path, "README.md", ["# docs"])
    return tmp_path


def build(root: Path, **kw) -> dict:
    return InventoryBuilder(ScanOptions(root=root, timestamp=False, **kw)).build()


def by(items, key="name"):
    return {i[key]: i for i in items}


def codes(artifact):
    return {(i["code"], i.get("subject")) for i in artifact["issues"]}


def test_artifact_is_valid(system):
    a = build(system)
    assert validate(a) == []
    assert a["stats"]["files_by_kind"] == {"bms": 1, "copybook": 3, "csd": 1, "ddl": 1, "doc": 1,
                                            "jcl": 1, "program": 4}


def test_program_kinds_and_entry_points(system):
    progs = by(build(system)["programs"])
    assert progs["MAINPGM"]["kind"] == "batch" and progs["MAINPGM"]["entry_point"]
    assert progs["MAINPGM"]["executed_by"] == ["job:RUNMAIN"]
    assert progs["UTILPGM"]["kind"] == "subroutine"
    assert progs["UTILPGM"]["called_by"] == ["copybook:PROCCPY", "program:MAINPGM", "program:ONLINE1"]
    assert progs["ONLINE1"]["kind"] == "online" and progs["ONLINE1"]["executed_by"] == ["transaction:T001"]
    assert progs["ORPHAN"]["kind"] == "subroutine"


def test_resolution_outcomes(system):
    a = build(system)
    res = {(r["type"], r["target"]): r["resolution"] for r in a["references"]}
    assert res[("COPY", "RECA")] == "resolved"
    assert res[("COPY", "NOSUCH")] == "unresolved"
    assert res[("STATIC_CALL", "CEEDAYS")] == "system"
    assert res[("STATIC_CALL", "GHOST")] == "unresolved"
    assert res[("CICS_MAP", "M1")] == "resolved"
    assert res[("CICS_MAP", "M9")] == "unresolved"
    assert res[("SQL_TABLE", "CUSTOMER")] == "resolved"
    assert res[("CICS_RETURN_TRANSID", "T001")] == "resolved"


def test_issues(system):
    found = codes(build(system))
    expected = {
        ("missing_copybook", "program:MAINPGM"),
        ("copy_target_is_program", "program:MAINPGM"),
        ("missing_program", "program:MAINPGM"),
        ("missing_map", "program:ONLINE1"),
        ("unused_copybook", "copybook:UNUSED"),
        ("subroutine_without_callers", "program:ORPHAN"),
        ("missing_dd_for_file", "program:MAINPGM"),
        ("csd_program_without_source", "csd_program:LOSTPGM"),
        ("procedure_copybook_in_data_division", "copybook:PROCCPY"),
        ("system_program", "program:MAINPGM"),
    }
    assert expected <= found


def test_dataset_lineage(system):
    main_pgm = by(build(system)["programs"])["MAINPGM"]
    assert [(d["ddname"], d["dsn"], d["disp"]) for d in main_pgm["datasets"]] == [
        ("MASTERDD", "PROD.MASTER", "SHR")]


def test_copybook_map_and_used_by(system):
    a = build(system)
    assert a["copybook_map"]["RECA"] == ["MAINPGM"]
    assert a["copybook_map"]["UNUSED"] == []


def test_entry_point_scope(system):
    a = build(system, entry_points=["T001"])
    assert validate(a) == []
    assert {p["name"] for p in a["programs"]} == {"ONLINE1", "UTILPGM"}
    assert a["meta"]["scope"] == {"mode": "entry_points", "entry_points": ["transaction:T001"]}
    assert {t["name"] for t in a["db2"]["tables"]} == {"CUSTOMER"}
    assert a["jcl"]["jobs"] == []


def test_unknown_entry_point_is_rejected(system):
    with pytest.raises(ValueError):
        build(system, entry_points=["NOPE"])


def test_validator_catches_tampering(system):
    a = build(system)
    bad = copy.deepcopy(a)
    bad["stats"]["programs"] = 99
    assert any("stats.programs" in p for p in validate(bad))
    bad = copy.deepcopy(a)
    bad["references"][0]["path"] = "made/up.cbl"
    assert any("made/up.cbl" in p for p in validate(bad))
    bad = copy.deepcopy(a)
    bad["graph"]["edges"][0]["to"] = "program:NOWHERE"
    assert any("NOWHERE" in p for p in validate(bad))


def test_output_is_deterministic(system):
    assert json.dumps(build(system)) == json.dumps(build(system))


def test_cli_writes_artifact(system, tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["inventory", "--root", str(system), "--out", str(out), "--no-timestamp"]) == 0
    assert "Inventory complete" in capsys.readouterr().out
    artifact = json.loads((out / "inventory_artifact.json").read_text(encoding="utf-8"))
    assert main(["validate-inventory", str(out / "inventory_artifact.json")]) == 0
    assert artifact["stats"]["programs"] == 4
