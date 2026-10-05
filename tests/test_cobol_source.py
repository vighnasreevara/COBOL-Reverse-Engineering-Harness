from conftest import fixed

from harness.cobol.source import FIXED, FREE, detect_format, normalize, strip_floating_comment
from harness.cobol.tokens import LIT, PSEUDO, WORD, tokenize


def texts(lines):
    return [c.text.strip() for c in normalize(lines).code]


def test_fixed_format_drops_comments_and_sequence_area():
    lines = [
        "000100 IDENTIFICATION DIVISION.",
        "000200*THIS IS A COMMENT",
        "000300/PAGE EJECT COMMENT",
        "000400 PROGRAM-ID. DEMO.                                                CHG001",
    ]
    assert detect_format(lines) == FIXED
    assert texts(lines) == ["IDENTIFICATION DIVISION.", "PROGRAM-ID. DEMO."]


def test_free_format_detected_when_code_starts_in_column_1():
    lines = ["IDENTIFICATION DIVISION.", "PROGRAM-ID. FREE1.", "*> comment", "* old style comment",
             "PROCEDURE DIVISION.", "    GOBACK."]
    src = normalize(lines)
    assert src.fmt == FREE
    assert [c.text.strip() for c in src.code] == ["IDENTIFICATION DIVISION.", "PROGRAM-ID. FREE1.",
                                                   "PROCEDURE DIVISION.", "GOBACK."]
    assert [n.code for n in src.notes] == ["nonstandard_comment"]


def test_asterisk_in_area_a_is_treated_as_comment_and_reported():
    lines = fixed("* real comment", "MOVE A TO B.")
    lines.insert(1, "       *==== asterisk in column 8, indicator blank")
    src = normalize(lines)
    assert [c.text.strip() for c in src.code] == ["MOVE A TO B."]
    assert [n.code for n in src.notes] == ["nonstandard_comment"]


def test_floating_comment_respects_literals():
    assert strip_floating_comment("MOVE '*>' TO X *> note") == "MOVE '*>' TO X "


def test_literal_truncated_at_column_72_is_reported():
    line = "       DISPLAY 'THIS LITERAL IS FAR TOO LONG AND RUNS PAST COLUMN SEVENTY-TWO FOR SURE'."
    src = normalize([line])
    assert [n.code for n in src.notes] == ["literal_truncated_at_column_72"]


def test_tokenizer_keeps_literals_whole_and_uppercases_words():
    toks = tokenize(normalize(fixed("display 'call me' x'00' FROM SESSION.DBSTATS.")).code).tokens
    assert [(t.kind, t.value) for t in toks] == [
        (WORD, "DISPLAY"), (LIT, "call me"), (LIT, "00"), (WORD, "FROM"), (WORD, "SESSION.DBSTATS"),
        ("PUNCT", "."),
    ]


def test_tokenizer_joins_continued_literal():
    lines = fixed(
        "MOVE 'FIRST PART OF A LONG LITERAL THAT KEEPS GOING UNTIL COLUMN 72",
        "- 'SECOND PART' TO X.",
    )
    toks = tokenize(normalize(lines).code).tokens
    lit = [t for t in toks if t.kind == LIT]
    assert len(lit) == 1
    assert lit[0].value.startswith("FIRST PART") and lit[0].value.endswith("SECOND PART")


def test_tokenizer_pseudo_text():
    toks = tokenize(normalize(fixed("COPY X REPLACING ==:PFX:== BY ==WS==.")).code).tokens
    assert [t.value for t in toks if t.kind == PSEUDO] == [":PFX:", "WS"]
