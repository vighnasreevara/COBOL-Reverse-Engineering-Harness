"""COBOL vocabulary: statement verbs, scope terminators and reserved words.

Paragraph and section names are user-defined words, so a word that is
reserved can never start a paragraph. This is what keeps lines such as
``END-EXEC.`` or ``EXIT.`` from being taken as paragraph names.
"""

# Words that begin a procedural statement.
VERBS = frozenset({
    "ACCEPT", "ADD", "ALLOCATE", "ALTER", "CALL", "CANCEL", "CLOSE", "COMPUTE", "CONTINUE",
    "DELETE", "DISPLAY", "DIVIDE", "ENTRY", "EVALUATE", "EXEC", "EXECUTE", "EXIT", "FREE",
    "GENERATE", "GO", "GOBACK", "IF", "INITIALIZE", "INITIATE", "INSPECT", "INVOKE", "JSON",
    "MERGE", "MOVE", "MULTIPLY", "NEXT", "OPEN", "PERFORM", "READ", "RELEASE", "RETURN",
    "REWRITE", "SEARCH", "SET", "SORT", "START", "STOP", "STRING", "SUBTRACT", "SUPPRESS",
    "TERMINATE", "UNSTRING", "USE", "WRITE", "XML",
})

# Explicit scope terminators.
END_WORDS = frozenset({
    "END-ACCEPT", "END-ADD", "END-CALL", "END-COMPUTE", "END-DELETE", "END-DISPLAY",
    "END-DIVIDE", "END-EVALUATE", "END-IF", "END-INVOKE", "END-JSON", "END-MULTIPLY",
    "END-PERFORM", "END-READ", "END-RECEIVE", "END-RETURN", "END-REWRITE", "END-SEARCH",
    "END-START", "END-STRING", "END-SUBTRACT", "END-UNSTRING", "END-WRITE", "END-XML",
})

# Figurative constants and other reserved words that may appear in procedure text.
RESERVED = frozenset(VERBS | END_WORDS | {
    "ACCESS", "ADDRESS", "ADVANCING", "AFTER", "ALL", "ALPHABET", "ALPHABETIC",
    "ALPHABETIC-LOWER", "ALPHABETIC-UPPER", "ALPHANUMERIC", "ALPHANUMERIC-EDITED", "ALSO",
    "ALTERNATE", "AND", "ANY", "APPLY", "ARE", "AREA", "AREAS", "ASCENDING", "ASSIGN", "AT",
    "AUTHOR", "BASIS", "BEFORE", "BEGINNING", "BINARY", "BLANK", "BLOCK", "BOTTOM", "BY",
    "CBL", "CD", "CF", "CH", "CHARACTER", "CHARACTERS", "CLASS", "CLOCK-UNITS", "COBOL",
    "CODE", "CODE-SET", "COLLATING", "COLUMN", "COMMA", "COMMON", "COMMUNICATION", "COMP",
    "COMP-1", "COMP-2", "COMP-3", "COMP-4", "COMP-5", "COMPUTATIONAL", "COMPUTATIONAL-1",
    "COMPUTATIONAL-2", "COMPUTATIONAL-3", "COMPUTATIONAL-4", "COMPUTATIONAL-5",
    "CONFIGURATION", "CONTAINS", "CONTENT", "CONTROL", "CONTROLS", "CONVERTING", "COPY",
    "CORR", "CORRESPONDING", "COUNT", "CURRENCY", "CYCLE", "DATA", "DATE", "DATE-COMPILED",
    "DATE-WRITTEN", "DAY", "DAY-OF-WEEK", "DBCS", "DE", "DEBUG-CONTENTS", "DEBUG-ITEM",
    "DEBUG-LINE", "DEBUG-NAME", "DEBUG-SUB-1", "DEBUG-SUB-2", "DEBUG-SUB-3", "DEBUGGING",
    "DECIMAL-POINT", "DECLARATIVES", "DELIMITED", "DELIMITER", "DEPENDING", "DESCENDING",
    "DESTINATION", "DETAIL", "DISPLAY-1", "DIVISION", "DOWN", "DUPLICATES", "DYNAMIC", "EGCS",
    "EGI", "EJECT", "ELSE", "EMI", "ENABLE", "END", "END-EXEC", "END-OF-PAGE", "ENDING",
    "ENTER", "ENVIRONMENT", "EOP", "EQUAL", "ERROR", "ESI", "EVERY", "EXCEPTION", "EXTEND",
    "EXTERNAL", "FALSE", "FD", "FILE", "FILE-CONTROL", "FILLER", "FINAL", "FIRST", "FOOTING",
    "FOR", "FROM", "FUNCTION", "FUNCTION-POINTER", "GIVING", "GLOBAL", "GREATER", "GROUP",
    "HEADING", "HIGH-VALUE", "HIGH-VALUES", "I-O", "I-O-CONTROL", "ID", "IDENTIFICATION",
    "IN", "INDEX", "INDEXED", "INDICATE", "INITIAL", "INPUT", "INPUT-OUTPUT", "INSTALLATION",
    "INTO", "INVALID", "IS", "JUST", "JUSTIFIED", "KANJI", "KEY", "LABEL", "LAST", "LEADING",
    "LEFT", "LENGTH", "LESS", "LIMIT", "LIMITS", "LINAGE", "LINAGE-COUNTER", "LINE",
    "LINE-COUNTER", "LINES", "LINKAGE", "LOCAL-STORAGE", "LOCK", "LOW-VALUE", "LOW-VALUES",
    "MEMORY", "MESSAGE", "MODE", "MODULES", "MORE-LABELS", "NATIONAL", "NATIONAL-EDITED",
    "NATIVE", "NEGATIVE", "NO", "NOT", "NULL", "NULLS", "NUMBER", "NUMERIC", "NUMERIC-EDITED",
    "OBJECT-COMPUTER", "OCCURS", "OF", "OFF", "OMITTED", "ON", "OPTIONAL", "OR", "ORDER",
    "ORGANIZATION", "OTHER", "OUTPUT", "OVERFLOW", "PACKED-DECIMAL", "PADDING", "PAGE",
    "PAGE-COUNTER", "PARAGRAPH", "PASSWORD", "PIC", "PICTURE", "PLUS", "POINTER", "POSITION",
    "POSITIVE", "PRINTING", "PROCEDURE", "PROCEDURE-POINTER", "PROCEDURES", "PROCEED",
    "PROCESSING", "PROGRAM", "PROGRAM-ID", "PURGE", "QUEUE", "QUOTE", "QUOTES", "RANDOM", "RD",
    "RECEIVE", "RECORD", "RECORDING", "RECORDS", "RECURSIVE", "REDEFINES", "REEL", "REFERENCE",
    "REFERENCES", "RELATIVE", "RELOAD", "REMAINDER", "REMOVAL", "RENAMES", "REPLACE",
    "REPLACING", "REPORT", "REPORTING", "REPORTS", "RERUN", "RESERVE", "RESET", "RETURN-CODE",
    "REVERSED", "REWIND", "RF", "RH", "RIGHT", "ROUNDED", "RUN", "SAME", "SD", "SECTION",
    "SECURITY", "SEGMENT", "SEGMENT-LIMIT", "SELECT", "SEND", "SENTENCE", "SEPARATE",
    "SEQUENCE", "SEQUENTIAL", "SHIFT-IN", "SHIFT-OUT", "SIGN", "SIZE", "SKIP1", "SKIP2",
    "SKIP3", "SORT-CONTROL", "SORT-CORE-SIZE", "SORT-FILE-SIZE", "SORT-MERGE", "SORT-MESSAGE",
    "SORT-MODE-SIZE", "SORT-RETURN", "SOURCE", "SOURCE-COMPUTER", "SPACE", "SPACES",
    "SPECIAL-NAMES", "STANDARD", "STANDARD-1", "STANDARD-2", "STATUS", "SUB-QUEUE-1",
    "SUB-QUEUE-2", "SUB-QUEUE-3", "SUM", "SYMBOLIC", "SYNC", "SYNCHRONIZED", "TABLE", "TALLY",
    "TALLYING", "TAPE", "TERMINAL", "TEST", "TEXT", "THAN", "THEN", "THROUGH", "THRU", "TIME",
    "TIMES", "TITLE", "TO", "TOP", "TRACE", "TRAILING", "TRUE", "TYPE", "UNIT", "UNTIL", "UP",
    "UPON", "USAGE", "USING", "VALUE", "VALUES", "VARYING", "WHEN", "WHEN-COMPILED", "WITH",
    "WORDS", "WORKING-STORAGE", "ZERO", "ZEROES", "ZEROS", "LOW-VALUE", "OTHERWISE",
    "YYYYMMDD", "YYYYDDD", "CYCLE", "METHOD", "RETURNING", "PROTOTYPE", "RAISING",
})

FIGURATIVE = frozenset({
    "ZERO", "ZEROS", "ZEROES", "SPACE", "SPACES", "HIGH-VALUE", "HIGH-VALUES", "LOW-VALUE",
    "LOW-VALUES", "QUOTE", "QUOTES", "NULL", "NULLS",
})

USAGE_WORDS = {
    "COMP": "COMP", "COMPUTATIONAL": "COMP", "COMP-4": "COMP", "COMPUTATIONAL-4": "COMP",
    "BINARY": "COMP", "COMP-5": "COMP-5", "COMPUTATIONAL-5": "COMP-5",
    "COMP-1": "COMP-1", "COMPUTATIONAL-1": "COMP-1", "COMP-2": "COMP-2", "COMPUTATIONAL-2": "COMP-2",
    "COMP-3": "COMP-3", "COMPUTATIONAL-3": "COMP-3", "PACKED-DECIMAL": "COMP-3",
    "DISPLAY": "DISPLAY", "DISPLAY-1": "DISPLAY-1", "NATIONAL": "NATIONAL", "INDEX": "INDEX",
    "POINTER": "POINTER", "PROCEDURE-POINTER": "PROCEDURE-POINTER",
    "FUNCTION-POINTER": "FUNCTION-POINTER", "FLOAT-SHORT": "COMP-1", "FLOAT-LONG": "COMP-2",
}


def is_user_word(value: str) -> bool:
    """True for a word that can name a paragraph, section or data item."""
    return bool(value) and value not in RESERVED and any(c.isalpha() for c in value) \
        and not value.startswith(":")


def starts_statement(toks, i: int) -> bool:
    """Is toks[i] the first word of a statement?"""
    tok = toks[i]
    if tok.kind != "WORD" or tok.value not in VERBS:
        return False
    nxt = toks[i + 1] if i + 1 < len(toks) else None
    if tok.value == "NEXT":
        return nxt is not None and nxt.is_word("SENTENCE")
    if tok.value in ("DISPLAY", "INDEX", "POINTER"):
        # DISPLAY is also a USAGE word; as a verb it is never followed by "."
        return nxt is not None and not nxt.is_punct(".")
    return True
