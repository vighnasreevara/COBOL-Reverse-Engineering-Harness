from conftest import fixed

from harness.inventory.cobol_scan import ddname_from_assign, scan_cobol


def refs(facts, type_=None):
    return [(r.type, r.target) for r in facts.references if type_ is None or r.type == type_]


PROGRAM = fixed(
    "IDENTIFICATION DIVISION.",
    "PROGRAM-ID. DEMO01.",
    "ENVIRONMENT DIVISION.",
    "INPUT-OUTPUT SECTION.",
    "FILE-CONTROL.",
    "    SELECT IN-FILE ASSIGN TO UT-S-INFILE",
    "        ORGANIZATION IS INDEXED.",
    "    SELECT OUT-FILE ASSIGN TO OUTFILE.",
    "DATA DIVISION.",
    "WORKING-STORAGE SECTION.",
    "01  WS-PGM            PIC X(8) VALUE 'SUBPGM1'.",
    "01  WS-AREA.",
    "    COPY CPYA.",
    "    COPY CPYB OF MYLIB REPLACING ==:P:== BY ==WS==",
    "                                 LEADING ==X-== BY ==Y-==.",
    "    EXEC SQL INCLUDE SQLCA END-EXEC.",
    "    EXEC SQL INCLUDE DCLTAB1 END-EXEC.",
    "* COPY COMMENTED.",
    "LINKAGE SECTION.",
    "01  LS-PARM PIC X.",
    "PROCEDURE DIVISION USING LS-PARM.",
    "    CALL 'STATIC1' USING WS-AREA",
    "    CALL WS-PGM",
    "    MOVE 'SUBPGM2' TO WS-PGM",
    "    DISPLAY 'CALL NOTREAL'",
    "    EXEC CICS LINK PROGRAM('CICSPGM') COMMAREA(WS-AREA)",
    "              LENGTH(LENGTH OF WS-AREA) END-EXEC",
    "    EXEC CICS XCTL PROGRAM(WS-PGM) END-EXEC",
    "    EXEC CICS SEND MAP('MAP1') MAPSET('SET1') ERASE END-EXEC",
    "    EXEC CICS READ FILE('ACCTF') INTO(WS-AREA) RIDFLD(K) END-EXEC",
    "    EXEC CICS RETURN TRANSID('TRN1') END-EXEC",
    "    EXEC SQL SELECT A INTO :HV FROM SCH.TAB1 T, TAB2",
    "             WHERE T.X = 1 END-EXEC",
    "    EXEC SQL INSERT INTO TAB3 VALUES (:A) END-EXEC",
    "    EXEC SQL UPDATE TAB4 SET C = 1 END-EXEC",
    "    EXEC SQL DECLARE C1 CURSOR FOR SELECT * FROM TAB5 JOIN TAB6",
    "             ON TAB5.K = TAB6.K END-EXEC",
    "    GOBACK.",
)


def test_identity_and_divisions():
    f = scan_cobol(PROGRAM)
    assert f.program_ids[0][0] == "DEMO01"
    assert f.procedure_using == ["LS-PARM"]
    assert set(f.divisions) == {"IDENTIFICATION", "ENVIRONMENT", "DATA", "PROCEDURE"}
    assert f.terminators == ["GOBACK"]
    assert f.has_cics and f.has_sql and f.ends_cleanly


def test_copy_statements_and_replacing():
    f = scan_cobol(PROGRAM)
    copies = [r for r in f.references if r.type == "COPY"]
    assert [r.target for r in copies] == ["CPYA", "CPYB"]
    assert copies[1].details["library"] == "MYLIB"
    assert copies[1].details["replacing"] == [
        {"from": ":P:", "to": "WS"}, {"from": "X-", "to": "Y-", "mode": "LEADING"}]
    assert all(r.division == "DATA" for r in copies)


def test_calls_ignore_literals_and_collect_dynamic_candidates():
    f = scan_cobol(PROGRAM)
    assert refs(f, "STATIC_CALL") == [("STATIC_CALL", "STATIC1")]
    dyn = [r for r in f.references if r.type == "DYNAMIC_CALL"][0]
    assert dyn.details["variable"] == "WS-PGM"
    assert dyn.details["variable_defined"] is True
    assert dyn.details["candidate_targets"] == ["SUBPGM1", "SUBPGM2"]


def test_cics_commands():
    f = scan_cobol(PROGRAM)
    assert refs(f, "CICS_LINK") == [("CICS_LINK", "CICSPGM")]
    xctl = [r for r in f.references if r.type == "CICS_XCTL"][0]
    assert xctl.target is None and xctl.details["variable"] == "WS-PGM"
    cmap = [r for r in f.references if r.type == "CICS_MAP"][0]
    assert (cmap.target, cmap.details["mapset"]) == ("MAP1", "SET1")
    assert refs(f, "CICS_FILE") == [("CICS_FILE", "ACCTF")]
    assert refs(f, "CICS_RETURN_TRANSID") == [("CICS_RETURN_TRANSID", "TRN1")]


def test_sql_includes_and_tables():
    f = scan_cobol(PROGRAM)
    assert refs(f, "SQL_INCLUDE") == [("SQL_INCLUDE", "SQLCA"), ("SQL_INCLUDE", "DCLTAB1")]
    tables = [(r.target, r.details["operation"]) for r in f.references if r.type == "SQL_TABLE"]
    assert tables == [("SCH.TAB1", "SELECT"), ("TAB2", "SELECT"), ("TAB3", "INSERT"),
                      ("TAB4", "UPDATE"), ("TAB5", "SELECT"), ("TAB6", "SELECT")]


def test_file_control():
    f = scan_cobol(PROGRAM)
    assert [(fc.select, fc.ddname, fc.organization) for fc in f.file_controls] == [
        ("IN-FILE", "INFILE", "INDEXED"), ("OUT-FILE", "OUTFILE", None)]


def test_assign_names():
    assert ddname_from_assign("UT-S-MASTER") == "MASTER"
    assert ddname_from_assign("AS-VSAMF") == "VSAMF"
    assert ddname_from_assign("PORTFILE") == "PORTFILE"
    assert ddname_from_assign("/tmp/file.dat") is None


def test_copybook_with_procedure_code_and_calls():
    f = scan_cobol(fixed(
        "01  ERR-AREA.",
        "    05 ERR-CODE PIC 9(4).",
        "ERROR-ROUTINE.",
        "    CALL 'ERRPROC' USING ERR-AREA",
        "    .",
    ))
    assert f.has_data_entries and f.has_procedure_code
    assert refs(f, "STATIC_CALL") == [("STATIC_CALL", "ERRPROC")]


def test_truncated_source_detected():
    f = scan_cobol(fixed("IDENTIFICATION DIVISION.", "PROGRAM-ID. T.", "PROCEDURE DIVISION.",
                         "P100.", "    MOVE 1 TO X.", "P200-EXIT"))
    assert not f.ends_cleanly


def test_select_in_sql_is_not_file_control():
    f = scan_cobol(fixed("PROCEDURE DIVISION.", "    EXEC SQL SELECT A INTO :B FROM T END-EXEC."))
    assert f.file_controls == []
