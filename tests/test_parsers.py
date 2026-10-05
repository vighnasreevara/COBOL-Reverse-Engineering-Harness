from harness.inventory.bms import parse_bms
from harness.inventory.csd import parse_csd
from harness.inventory.ddl import parse_ddl
from harness.inventory.idcams import parse_idcams
from harness.inventory.jcl import parse_dsn, parse_jcl


def test_jcl_job_steps_dds_and_continuation():
    j = parse_jcl([
        "//PAYJOB   JOB (ACCT),'PAYROLL',",
        "//             CLASS=A,MSGCLASS=X",
        "//* comment",
        "//STEP1    EXEC PGM=PAY001,PARM='A,B'",
        "//INFILE   DD DSN=PROD.PAY.INPUT,DISP=SHR",
        "//         DD DSN=PROD.PAY.EXTRA,DISP=SHR",
        "//OUTFILE  DD DSN=PROD.PAY.OUT(+1),",
        "//            DISP=(NEW,CATLG,DELETE)",
        "//SYSIN    DD *",
        "  CONTROL CARD",
        "/*",
        "//STEP2    EXEC PAYPROC",
    ])
    assert [job.name for job in j.jobs] == ["PAYJOB"]
    s1, s2 = j.jobs[0].steps
    assert (s1.name, s1.program, s1.params["PARM"]) == ("STEP1", "PAY001", "'A,B'")
    assert [(d.name, d.dsn) for d in s1.dds] == [
        ("INFILE", "PROD.PAY.INPUT"), (None, "PROD.PAY.EXTRA"), ("OUTFILE", "PROD.PAY.OUT(+1)"), ("SYSIN", None)]
    assert s1.dds[2].disp == "(NEW,CATLG,DELETE)"
    assert s1.dds[3].instream == ["  CONTROL CARD"]
    assert (s2.proc, s2.order) == ("PAYPROC", 2)
    assert j.notes == []


def test_jcl_instream_cut_short_by_comment_line():
    j = parse_jcl([
        "//DEF JOB 1",
        "//S1  EXEC PGM=IDCAMS",
        "//SYSIN DD *",
        "  DELETE A.B.C",
        "//*",
        "  DEFINE CLUSTER (NAME(A.B.C) INDEXED)",
    ])
    assert j.jobs[0].steps[0].dds[0].instream == ["  DELETE A.B.C"]
    assert [n.code for n in j.notes] == ["stray_lines"]
    assert j.notes[0].details["lines"] == [6]


def test_jcl_tso_batch_run_program_and_procs():
    j = parse_jcl([
        "//MYPROC PROC",
        "//P1 EXEC PGM=IEFBR14",
        "// PEND",
        "//DB2JOB JOB 1",
        "//S1 EXEC PGM=IKJEFT01",
        "//SYSTSIN DD *",
        "  DSN SYSTEM(DB2P)",
        "  RUN PROGRAM(RPT100) PLAN(RPTPLAN)",
        "  END",
        "/*",
    ])
    assert [p.name for p in j.procs] == ["MYPROC"]
    assert j.jobs[0].steps[0].run_program == "RPT100"


def test_dsn_parsing():
    assert parse_dsn("A.B(+1)") == {"name": "A.B(+1)", "base": "A.B", "generation": "+1"}
    assert parse_dsn("A.LIB(MEM)")["member"] == "MEM"
    assert parse_dsn("&&TEMP")["temporary"] is True


def test_csd_resources_with_blank_values():
    res = parse_csd([
        "* comment",
        "DEFINE TRANSACTION(T001) GROUP(G1)",
        "       PROGRAM(PGM001) DESCRIPTION(Main menu (online))",
        "DEFINE FILE(ACCT) DSNAME(PROD.ACCT.KSDS) GROUP(G1)",
    ])
    assert [(r.type, r.name) for r in res] == [("TRANSACTION", "T001"), ("FILE", "ACCT")]
    assert res[0].attributes["PROGRAM"] == "PGM001"
    assert res[0].attributes["DESCRIPTION"] == "Main menu (online)"
    assert res[1].attributes["DSNAME"] == "PROD.ACCT.KSDS"


def test_bms_maps_fields_and_continuation():
    lines = [
        "MSET1    DFHMSD TYPE=MAP,MODE=INOUT,LANG=COBOL,".ljust(71) + "X",
        "               STORAGE=AUTO",
        "MAP1     DFHMDI SIZE=(24,80)",
        "         DFHMDF POS=(1,1),LENGTH=10,ATTRB=PROT,INITIAL='TITLE TEXT'",
        "FLD1     DFHMDF POS=(3,1),LENGTH=8,ATTRB=(UNPROT,IC)",
        "         DFHMSD TYPE=FINAL",
    ]
    b = parse_bms(lines)
    assert [ms.name for ms in b.mapsets] == ["MSET1"]
    assert b.mapsets[0].params["STORAGE"] == "AUTO"
    m = b.mapsets[0].maps[0]
    assert (m.name, len(m.fields)) == ("MAP1", 2)
    assert (m.fields[1].name, m.fields[1].length, m.fields[1].attrb) == ("FLD1", 8, "(UNPROT,IC)")
    assert m.fields[0].initial == "TITLE TEXT"
    assert b.notes == []


def test_ddl_tables_keys_views_and_procedure_bodies():
    d = parse_ddl([
        "-- comment; with semicolon",
        "CREATE TABLE PARENT (ID CHAR(8) NOT NULL, NAME VARCHAR(30),",
        "  PRIMARY KEY (ID));",
        "CREATE TABLE CHILD (",
        "  ID CHAR(8) NOT NULL, SEQ INTEGER NOT NULL,",
        "  AMT DECIMAL(15,2) NOT NULL WITH DEFAULT 0,",
        "  FOREIGN KEY (ID) REFERENCES PARENT(ID));",
        "ALTER TABLE CHILD ADD CONSTRAINT CHILD_PK PRIMARY KEY (ID, SEQ);",
        "CREATE UNIQUE INDEX CHILD_IX ON CHILD (SEQ ASC);",
        "CREATE VIEW V1 AS SELECT P.ID FROM PARENT P JOIN CHILD C ON P.ID = C.ID;",
        "COMMENT ON TABLE PARENT IS 'Parent table';",
        "CREATE PROCEDURE CLEANUP (IN D INTEGER) LANGUAGE SQL",
        "BEGIN",
        "  DELETE FROM CHILD WHERE SEQ < D;",
        "  IF D > 1 THEN SET D = 1; END IF;",
        "END;",
        "GRANT SELECT ON PARENT TO PUBLIC;",
    ])
    parent, child = d.tables
    assert parent.primary_key == ["ID"] and parent.comment == "Parent table"
    assert [(c.name, c.type, c.nullable) for c in child.columns] == [
        ("ID", "CHAR(8)", False), ("SEQ", "INTEGER", False), ("AMT", "DECIMAL(15,2)", False)]
    assert child.primary_key == ["ID", "SEQ"]
    assert (child.foreign_keys[0].columns, child.foreign_keys[0].references) == (["ID"], "PARENT")
    assert [(i.name, i.unique) for i in d.indexes] == [("CHILD_IX", True)]
    assert d.views[0].base_tables == ["PARENT", "CHILD"]
    assert [(o.kind, o.name) for o in d.objects] == [("PROCEDURE", "CLEANUP")]
    assert d.notes == []


def test_idcams_define_cluster_with_continuations():
    r = parse_idcams([
        "  DELETE OLD.CLUSTER CLUSTER",
        "  DEFINE CLUSTER (NAME(PROD.ACCT.KSDS) -",
        "         INDEXED KEYS(12 0)       -",
        "         RECORDSIZE(200 400))     -",
        "     DATA (NAME(PROD.ACCT.KSDS.DATA))",
        "  /* trailing comment */",
    ])
    d = r.definitions[0]
    assert (d.name, d.organization, d.key_length, d.key_offset) == ("PROD.ACCT.KSDS", "KSDS", 12, 0)
    assert (d.record_size_avg, d.record_size_max) == (200, 400)
    assert r.deletes[0][0] == "OLD.CLUSTER"
