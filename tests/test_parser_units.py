"""Unit tests for stage 2: copybook expansion, data entries, statement tree, control flow."""

from pathlib import Path

from conftest import fixed

from harness.cobol.source import normalize
from harness.cobol.tokens import tokenize
from harness.parser.cfg import build_cfg
from harness.parser.data import parse_clauses, parse_data
from harness.parser.expand import Expander
from harness.parser.procedure import iter_statements, parse_procedure


def toks(lines, path="p.cbl"):
    return tokenize(normalize(lines).code, path).tokens


def proc(*code):
    return parse_procedure(toks(fixed("PROCEDURE DIVISION.", *code)), set())


def write(root: Path, rel: str, lines):
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text("\n".join(lines), encoding="utf-8")


# ----------------------------------------------------------------- expansion
def test_copy_expansion_with_replacing_and_nesting(tmp_path):
    write(tmp_path, "c/OUTER.cpy", fixed("01 :PFX:-REC.", "   COPY INNER.", "   05 OLD-NAME PIC X."))
    write(tmp_path, "c/INNER.cpy", fixed("   05 INNER-FIELD PIC 9."))
    ex = Expander(tmp_path, {"OUTER": "c/OUTER.cpy", "INNER": "c/INNER.cpy"})
    out, log = ex.expand(toks(fixed("COPY OUTER REPLACING ==:PFX:== BY ==WS==",
                                    "                     ==OLD-NAME== BY ==NEW-NAME==.")))
    words = [t.value for t in out]
    assert words[:2] == ["01", "WS-REC"]
    assert "INNER-FIELD" in words and "NEW-NAME" in words and "OLD-NAME" not in words
    assert [(e.member, e.status, e.depth) for e in log] == [("OUTER", "expanded", 0), ("INNER", "expanded", 1)]
    inner = next(t for t in out if t.value == "INNER-FIELD")
    assert inner.file == "c/INNER.cpy" and inner.line == 1


def test_recursive_and_missing_copies(tmp_path):
    write(tmp_path, "A.cpy", fixed("COPY B."))
    write(tmp_path, "B.cpy", fixed("COPY A."))
    ex = Expander(tmp_path, {"A": "A.cpy", "B": "B.cpy"})
    _, log = ex.expand(toks(fixed("COPY A.", "COPY NOPE.")))
    assert [(e.member, e.status) for e in log] == [("A", "expanded"), ("B", "expanded"), ("A", "recursive"),
                                                   ("NOPE", "missing")]


def test_leading_replacing(tmp_path):
    write(tmp_path, "R.cpy", fixed("05 XX-ONE PIC X.", "05 XX-TWO PIC X."))
    out, _ = Expander(tmp_path, {"R": "R.cpy"}).expand(toks(fixed("COPY R REPLACING LEADING ==XX== BY ==YY==.")))
    assert [t.value for t in out if t.value.startswith(("XX", "YY"))] == ["YY-ONE", "YY-TWO"]


# ---------------------------------------------------------------------- data
def test_clauses():
    def c(text):
        return parse_clauses(toks(fixed(text))[:-1] if text.endswith(".") else toks(fixed(text)))

    assert c("PIC S9(13)V99 COMP-3 VALUE -1.5") == {"picture": "S9(13)V99", "usage": "COMP-3",
                                                    "values": [{"type": "number", "value": -1.5}]}
    assert c("PIC $$$,$$9.99") == {"picture": "$$$,$$9.99"}
    assert c("OCCURS 1 TO 50 DEPENDING ON WS-N ASCENDING K1 INDEXED BY IX1") == {
        "occurs": {"min": 1, "max": 50, "depending_on": "WS-N", "keys": [{"order": "ASCENDING", "fields": ["K1"]}],
                   "indexed_by": ["IX1"]}}
    assert c("VALUES 'A' 'B' 1 THRU 9") == {"values": [
        {"type": "literal", "value": "A"}, {"type": "literal", "value": "B"},
        {"type": "range", "from": 1, "to": 9}]}
    assert c("PIC S9(4) SIGN LEADING SEPARATE USAGE IS BINARY SYNC") == {
        "picture": "S9(4)", "sign": {"position": "LEADING", "separate": True}, "usage": "COMP", "sync": True}
    assert c("REDEFINES OTHER-FIELD PIC X(4)") == {"redefines": "OTHER-FIELD", "picture": "X(4)"}


def test_data_division_sections_fd_and_misplaced_paragraphs():
    d = parse_data(toks(fixed(
        "FILE SECTION.",
        "FD  IN-FILE RECORDING MODE F.",
        "01  IN-REC PIC X(80).",
        "WORKING-STORAGE SECTION.",
        "01  WS-A.",
        "    05 WS-FLAG PIC X VALUE 'N'.",
        "       88 DONE VALUE 'Y'.",
        "    05 FILLER PIC X(3).",
        "EXEC SQL DECLARE T1 TABLE (C1 CHAR(1)) END-EXEC.",
        "STRAY-PARA.",
        "    MOVE 1 TO WS-A.",
    )))
    assert [(f.kind, f.name, f.records) for f in d.files] == [("FD", "IN-FILE", ["IN-REC"])]
    assert [(e.level, e.name, e.section, e.fd) for e in d.entries] == [
        (1, "IN-REC", "FILE", "IN-FILE"), (1, "WS-A", "WORKING-STORAGE", None),
        (5, "WS-FLAG", "WORKING-STORAGE", None), (88, "DONE", "WORKING-STORAGE", None),
        (5, None, "WORKING-STORAGE", None)]
    assert d.sql_declared_tables[0]["table"] == "T1"
    assert [m["name"] for m in d.misplaced_names] == ["STRAY-PARA"]


# ----------------------------------------------------------------- procedure
def test_if_else_nested_and_period_scope():
    p = proc("P1.", "    IF A = 1", "       IF B = 2 MOVE 1 TO C", "       ELSE MOVE 2 TO C",
             "    ELSE", "       MOVE 3 TO C.", "    DISPLAY 'X'.")
    s = p.paragraphs[0].statements
    assert [x["verb"] for x in s] == ["IF", "DISPLAY"]
    outer = s[0]
    assert outer["condition"] == "A = 1"
    inner = outer["then"][0]
    assert inner["condition"] == "B = 2" and inner["else"][0]["text"] == "MOVE 2 TO C"
    assert outer["else"][0]["writes"] == ["C"]


def test_evaluate_with_grouped_whens_and_other():
    p = proc("P1.", "    EVALUATE TRUE ALSO WS-X", "      WHEN A = 1 ALSO 'Y'", "      WHEN B = 2 ALSO ANY",
             "           PERFORM P2", "      WHEN OTHER", "           CONTINUE", "    END-EVALUATE.", "P2.", "    EXIT.")
    ev = p.paragraphs[0].statements[0]
    assert ev["subjects"] == ["TRUE", "WS-X"]
    assert ev["branches"][0]["conditions"] == ["A = 1 ALSO 'Y'", "B = 2 ALSO ANY"]
    assert ev["branches"][0]["statements"][0]["target"] == "P2"
    assert ev["branches"][1]["other"] is True


def test_perform_forms():
    p = proc("P1.",
             "    PERFORM P2 THRU P3",
             "    PERFORM P2 UNTIL WS-EOF = 'Y' OR WS-ERR > 3",
             "    PERFORM P2 VARYING I FROM 1 BY 1 UNTIL I > 10",
             "    PERFORM P2 WS-N TIMES",
             "    PERFORM WITH TEST AFTER UNTIL DONE",
             "        ADD 1 TO I",
             "        IF I > 5 EXIT PERFORM END-IF",
             "    END-PERFORM",
             "    PERFORM WS-N TIMES DISPLAY I END-PERFORM.",
             "P2.", "    EXIT.", "P3.", "    EXIT.")
    s = p.paragraphs[0].statements
    assert (s[0]["target"], s[0]["thru"]) == ("P2", "P3")
    assert s[1]["loop"] == {"type": "until", "condition": "WS-EOF = 'Y' OR WS-ERR > 3"}
    assert s[2]["loop"]["type"] == "varying" and s[2]["loop"]["varying"][0] == {
        "variable": "I", "from": "1", "by": "1", "until": "I > 10"}
    assert s[3]["loop"] == {"type": "times", "count": "WS-N"} and s[3]["target"] == "P2"
    assert s[4]["inline"] and s[4]["loop"]["test"] == "AFTER" and len(s[4]["body"]) == 2
    assert s[4]["body"][1]["then"][0]["verb"] == "EXIT PERFORM"
    assert s[5]["inline"] and s[5]["loop"]["type"] == "times" and s[5]["body"][0]["verb"] == "DISPLAY"
    assert p.problems == []


def test_conditional_phrases_and_exec():
    p = proc("P1.",
             "    READ IN-FILE INTO WS-REC",
             "        AT END MOVE 'Y' TO WS-EOF",
             "        NOT AT END ADD 1 TO WS-COUNT",
             "    END-READ",
             "    COMPUTE X ROUNDED = Y * 2",
             "        ON SIZE ERROR PERFORM P2",
             "    END-COMPUTE",
             "    EXEC SQL SELECT A INTO :HV FROM T1",
             "        WHERE K = :KEY END-EXEC",
             "    EXEC CICS HANDLE CONDITION NOTFND(P2) END-EXEC",
             "    GO TO P2 P3 DEPENDING ON WS-IX.",
             "P2.", "    GOBACK.", "P3.", "    GOBACK.")
    s = p.paragraphs[0].statements
    read = s[0]
    assert [ph["phrase"] for ph in read["phrases"]] == ["AT END", "NOT AT END"]
    assert read["writes"] == ["WS-REC"]
    assert s[1]["phrases"][0]["phrase"] == "ON SIZE ERROR" and s[1]["writes"] == ["X"] and s[1]["reads"] == ["Y"]
    assert s[2]["tables"] == [{"table": "T1", "operation": "SELECT"}]
    assert (s[2]["reads"], s[2]["writes"]) == (["KEY"], ["HV", "SQLCODE", "SQLSTATE"])
    assert s[3]["handlers"] == ["P2"]
    assert s[4]["targets"] == ["P2", "P3"] and s[4]["depending_on"] == "WS-IX"


def test_mainline_before_first_paragraph_and_reserved_words_are_not_paragraphs():
    p = proc("    PERFORM P1", "    EXEC CICS RETURN", "    END-EXEC.", "P1.", "    EXIT.")
    assert [x.name for x in p.paragraphs] == ["(mainline)", "P1"]
    assert p.paragraphs[0].statements[0]["target"] == "P1"


def test_data_flow():
    p = proc("P1.",
             "    MOVE A TO B C",
             "    ADD X Y TO Z",
             "    DIVIDE A INTO B GIVING C REMAINDER D",
             "    STRING A DELIMITED BY SIZE B INTO C",
             "    MOVE FUNCTION UPPER-CASE(N) TO M OF G.")
    rw = [(s["reads"], s["writes"]) for s in p.paragraphs[0].statements]
    assert rw == [(["A"], ["B", "C"]), (["X", "Y", "Z"], ["Z"]), (["A", "B"], ["C", "D"]),
                  (["A", "B"], ["C"]), (["N"], ["M"])]


# ----------------------------------------------------------------------- cfg
def test_cfg_reachability_thru_goto_and_dead_code():
    p = proc("MAIN-PARA.",
             "    PERFORM A1 THRU A9",
             "    PERFORM R1",
             "    GO TO FIN.",
             "A1.", "    MOVE 1 TO X.",
             "A5.", "    MOVE 2 TO X.",
             "A9.", "    EXIT.",
             "DEAD1.", "    MOVE 3 TO X.",
             "R1.", "    PERFORM R1.",
             "FIN.", "    GOBACK.", "    DISPLAY 'NEVER'.",
             "AFTER-FIN.", "    PERFORM NOWHERE.")
    cfg, problems = build_cfg(p)
    assert cfg["dead_paragraphs"] == ["DEAD1", "AFTER-FIN"]
    assert cfg["recursive_cycles"] == [["R1"]]
    assert cfg["undefined_targets"][0]["name"] == "NOWHERE"
    thru = next(e for e in cfg["edges"] if e["type"] == "PERFORM" and e["to"] == "A1")
    assert thru["range"] == ["A1", "A5", "A9"]
    assert [x["code"] for x in problems] == ["unreachable_statement"]


def test_cics_handle_makes_paragraph_reachable():
    p = proc("    EXEC CICS HANDLE CONDITION ERROR(ERR-PARA) END-EXEC",
             "    EXEC CICS RETURN END-EXEC.",
             "ERR-PARA.", "    EXEC CICS RETURN END-EXEC.")
    cfg, _ = build_cfg(p)
    assert cfg["dead_paragraphs"] == []


def test_statement_iteration_counts_nested():
    p = proc("P1.", "    IF A = 1 MOVE 1 TO B ELSE PERFORM UNTIL C = 1 ADD 1 TO C END-PERFORM END-IF.")
    assert [s["verb"] for s in iter_statements(p.paragraphs[0].statements)] == ["IF", "MOVE", "PERFORM", "ADD"]
