"""Extract cross-references and identity facts from one COBOL member.

Works for programs and copybooks alike. Only cross-reference level facts
are extracted here; paragraph structure and data layouts belong to the
parser and data stages.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from harness.cobol.source import CobolSource, normalize
from harness.cobol.tokens import LIT, PSEUDO, PUNCT, WORD, Token, tokenize

DIVISIONS = ("IDENTIFICATION", "ID", "ENVIRONMENT", "DATA", "PROCEDURE")

# Words that start a COBOL procedural statement. Used to tell copybooks that
# carry procedure code apart from pure data copybooks.
PROCEDURE_VERBS = {
    "ACCEPT", "ADD", "CALL", "CLOSE", "COMPUTE", "CONTINUE", "DELETE", "DISPLAY",
    "DIVIDE", "EVALUATE", "GOBACK", "IF", "INITIALIZE", "INSPECT", "MOVE",
    "MULTIPLY", "OPEN", "PERFORM", "READ", "RETURN", "REWRITE", "SEARCH", "SET",
    "START", "STOP", "STRING", "SUBTRACT", "UNSTRING", "WRITE",
}

CICS_PROGRAM_COMMANDS = {"LINK": "CICS_LINK", "XCTL": "CICS_XCTL", "LOAD": "CICS_LOAD"}
CICS_FILE_COMMANDS = {
    "READ", "WRITE", "REWRITE", "DELETE", "STARTBR", "READNEXT", "READPREV",
    "ENDBR", "RESETBR", "UNLOCK",
}
SQL_CLAUSE_WORDS = {
    "WHERE", "ORDER", "GROUP", "HAVING", "FETCH", "FOR", "WITH", "UNION", "EXCEPT",
    "INTERSECT", "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS", "ON",
    "SET", "VALUES", "SELECT", "OPTIMIZE", "QUERYNO", "SKIP", "LIMIT", "OFFSET",
    "AS", "USING", "WHEN", "END-EXEC", "INTO",
}


@dataclass
class Reference:
    type: str                 # COPY, SQL_INCLUDE, STATIC_CALL, DYNAMIC_CALL, CICS_*, SQL_TABLE, SQL_CALL
    target: str | None        # upper-cased name; None for unresolvable dynamic targets
    line: int
    division: str | None = None
    details: dict = field(default_factory=dict)


@dataclass
class FileControl:
    select: str
    assign: str
    ddname: str | None
    organization: str | None
    line: int


@dataclass
class CobolFacts:
    fmt: str
    program_ids: list[tuple[str, int]] = field(default_factory=list)
    divisions: dict[str, int] = field(default_factory=dict)
    procedure_using: list[str] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    file_controls: list[FileControl] = field(default_factory=list)
    data_names: set[str] = field(default_factory=set)
    has_cics: bool = False
    has_sql: bool = False
    has_dli: bool = False
    has_data_entries: bool = False
    has_procedure_code: bool = False
    terminators: list[str] = field(default_factory=list)   # GOBACK, STOP RUN, EXIT PROGRAM
    ends_cleanly: bool = True
    token_count: int = 0
    notes: list = field(default_factory=list)


def scan_cobol(lines: list[str]) -> CobolFacts:
    source: CobolSource = normalize(lines)
    stream = tokenize(source.code)
    toks = stream.tokens
    facts = CobolFacts(fmt=source.fmt, token_count=len(toks))
    facts.notes.extend(source.notes)
    facts.notes.extend(stream.notes)

    division: str | None = None
    literal_moves: dict[str, set[str]] = {}
    i = 0
    n = len(toks)
    statement_start = True   # previous token was a period (or file start)

    while i < n:
        tok = toks[i]

        if tok.kind != WORD:
            statement_start = tok.is_punct(".")
            i += 1
            continue

        v = tok.value
        nxt = toks[i + 1] if i + 1 < n else None

        if v in DIVISIONS and nxt is not None and nxt.is_word("DIVISION"):
            division = "IDENTIFICATION" if v == "ID" else v
            facts.divisions.setdefault(division, tok.line)
            if division == "PROCEDURE":
                j = i + 2
                if j < n and toks[j].is_word("USING"):
                    j += 1
                    while j < n and not toks[j].is_punct("."):
                        t = toks[j]
                        if t.kind == WORD and t.value not in ("BY", "REFERENCE", "VALUE", "CONTENT"):
                            facts.procedure_using.append(t.value)
                        j += 1
                i = j
            else:
                i += 2
            statement_start = False
            continue

        if v == "PROGRAM-ID":
            j = i + 1
            if j < n and toks[j].is_punct("."):
                j += 1
            if j < n and toks[j].kind in (WORD, LIT):
                facts.program_ids.append((toks[j].value.upper(), toks[j].line))
            i = j + 1
            continue

        if v == "COPY":
            i = _scan_copy(toks, i, division, facts)
            statement_start = True
            continue

        if v == "EXEC" and nxt is not None and nxt.kind == WORD:
            i = _scan_exec(toks, i, division, facts)
            continue

        # division None means a copybook fragment, which may hold procedure code
        if v == "CALL" and division in (None, "PROCEDURE"):
            facts.has_procedure_code = True
            _scan_call(toks, i, division, facts)
            i += 1
            continue

        if v == "SELECT" and division == "ENVIRONMENT":
            i = _scan_select(toks, i, facts)
            continue

        if v == "MOVE" and nxt is not None and nxt.kind == LIT:
            # MOVE 'PGMNAME' TO WS-PGM: candidate targets for dynamic CALL/LINK
            if i + 3 < n and toks[i + 2].is_word("TO") and toks[i + 3].kind == WORD:
                literal_moves.setdefault(toks[i + 3].value, set()).add(nxt.value.strip().upper())

        if (statement_start and division in (None, "DATA") and _is_level_number(v)
                and nxt is not None and nxt.kind == WORD):
            facts.has_data_entries = True
            facts.data_names.add(nxt.value)
            value = _value_literal(toks, i + 2)
            if value:
                literal_moves.setdefault(nxt.value, set()).add(value.upper())
        elif v in PROCEDURE_VERBS and division in (None, "PROCEDURE"):
            facts.has_procedure_code = True

        if v == "GOBACK":
            facts.terminators.append("GOBACK")
        elif v == "STOP" and nxt is not None and nxt.is_word("RUN"):
            facts.terminators.append("STOP RUN")
        elif v == "EXIT" and nxt is not None and nxt.is_word("PROGRAM"):
            facts.terminators.append("EXIT PROGRAM")

        statement_start = False
        i += 1

    _attach_dynamic_candidates(facts, literal_moves)
    if facts.divisions.get("PROCEDURE") and toks:
        last = toks[-1]
        facts.ends_cleanly = last.is_punct(".") or last.is_word("PROGRAM", "END-EXEC")
    return facts


def _is_level_number(value: str) -> bool:
    if not value.isdigit() or len(value) > 2:
        return False
    level = int(value)
    return 1 <= level <= 49 or level in (66, 77, 88)


def _value_literal(toks: list[Token], start: int) -> str | None:
    j = start
    while j < len(toks) and not toks[j].is_punct("."):
        if toks[j].is_word("VALUE", "VALUES"):
            k = j + 1
            if k < len(toks) and toks[k].is_word("IS", "ARE"):
                k += 1
            if k < len(toks) and toks[k].kind == LIT:
                return toks[k].value.strip()
            return None
        j += 1
    return None


def _operand(tok: Token) -> str | None:
    if tok.kind in (WORD, LIT):
        return tok.value.strip().upper()
    return None


def _scan_copy(toks: list[Token], i: int, division: str | None, facts: CobolFacts) -> int:
    start = toks[i]
    n = len(toks)
    j = i + 1
    if j >= n or toks[j].kind not in (WORD, LIT):
        facts.notes.append(_note("malformed_copy", start.line))
        return j
    name = _operand(toks[j])
    j += 1
    details: dict = {}
    if j < n and toks[j].is_word("OF", "IN") and j + 1 < n:
        details["library"] = _operand(toks[j + 1])
        j += 2
    if j < n and toks[j].is_word("SUPPRESS"):
        details["suppress"] = True
        j += 1
    if j < n and toks[j].is_word("REPLACING"):
        j += 1
        pairs = []
        while j < n and not toks[j].is_punct("."):
            mode = None
            if toks[j].is_word("LEADING", "TRAILING"):
                mode = toks[j].value
                j += 1
            left = toks[j] if j < n else None
            if j + 2 < n and toks[j + 1].is_word("BY"):
                right = toks[j + 2]
                pair = {"from": _pseudo_text(left), "to": _pseudo_text(right)}
                if mode:
                    pair["mode"] = mode
                pairs.append(pair)
                j += 3
            else:
                j += 1
        details["replacing"] = pairs
    if j < n and toks[j].is_punct("."):
        j += 1
    else:
        facts.notes.append(_note("copy_without_period", start.line, {"copybook": name}))
    facts.references.append(Reference("COPY", name, start.line, division, details))
    return j


def _pseudo_text(tok: Token) -> str:
    if tok.kind == PSEUDO:
        return tok.value
    if tok.kind == LIT:
        return f"'{tok.value}'"
    return tok.value


def _scan_exec(toks: list[Token], i: int, division: str | None, facts: CobolFacts) -> int:
    lang = toks[i + 1].value
    line = toks[i].line
    end = i + 2
    while end < len(toks) and not toks[end].is_word("END-EXEC"):
        end += 1
    body = toks[i + 2:end]
    after = end + 1
    if end == len(toks):
        facts.notes.append(_note("exec_without_end_exec", line, {"language": lang}))
    if lang == "SQL":
        facts.has_sql = True
        _scan_sql(body, line, division, facts)
    elif lang == "CICS":
        facts.has_cics = True
        if division in (None, "PROCEDURE"):
            facts.has_procedure_code = True
        _scan_cics(body, line, division, facts)
    elif lang in ("DLI", "SQLIMS"):
        facts.has_dli = True
    return after


def _scan_sql(body: list[Token], line: int, division: str | None, facts: CobolFacts) -> None:
    if not body:
        return
    verb = body[0].value if body[0].kind == WORD else ""
    if verb == "INCLUDE" and len(body) > 1:
        facts.references.append(Reference("SQL_INCLUDE", body[1].value.upper(), line, division))
        return
    if verb == "CALL" and len(body) > 1 and body[1].kind == WORD:
        target = body[1].value
        if target.startswith(":"):
            facts.references.append(Reference("SQL_CALL", None, line, division, {"variable": target[1:]}))
        else:
            facts.references.append(Reference("SQL_CALL", target, line, division))
        return
    if verb in ("BEGIN", "END", "WHENEVER", "COMMIT", "ROLLBACK", "CONNECT", "SAVEPOINT",
                "RELEASE", "PREPARE", "EXECUTE", "OPEN", "CLOSE", "FETCH", "SET", "GET"):
        return
    if verb == "DECLARE":
        # DECLARE t TABLE (...)            -> DCLGEN table declaration
        # DECLARE GLOBAL TEMPORARY TABLE t -> temporary table
        # DECLARE c CURSOR FOR SELECT ...  -> tables in the SELECT
        if len(body) > 2 and body[2].is_word("TABLE"):
            facts.references.append(Reference("SQL_TABLE", body[1].value, line, division, {"operation": "DECLARE"}))
            return
        if len(body) > 4 and body[1].is_word("GLOBAL") and body[3].is_word("TABLE"):
            facts.references.append(Reference("SQL_TABLE", body[4].value, line, division,
                                              {"operation": "DECLARE_TEMPORARY"}))
            return
    for table, op in sql_tables(body):
        facts.references.append(Reference("SQL_TABLE", table, line, division, {"operation": op}))


def sql_tables(body: list[Token]) -> list[tuple[str, str]]:
    """Table names referenced by one SQL statement, with the statement verb."""
    if not body or body[0].kind != WORD:
        return []
    verb = body[0].value
    if verb == "DECLARE":
        verb = "SELECT"
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(tok: Token | None) -> None:
        if tok is None or tok.kind != WORD:
            return
        name = tok.value
        if name.startswith(":") or name in SQL_CLAUSE_WORDS or name == "(" or name.isdigit():
            return
        if name not in seen:
            seen.add(name)
            found.append((name, verb))

    n = len(body)
    for k, tok in enumerate(body):
        if tok.kind != WORD:
            continue
        nxt = body[k + 1] if k + 1 < n else None
        if tok.value in ("FROM", "JOIN"):
            if nxt is not None and nxt.is_word("FINAL", "NEW", "OLD"):
                continue
            add(nxt)
            # comma separated FROM list: FROM A X, B Y
            m = k + 2
            while m < n:
                t = body[m]
                if t.is_punct(","):
                    add(body[m + 1] if m + 1 < n else None)
                    m += 2
                    continue
                if t.kind == WORD and t.value not in SQL_CLAUSE_WORDS and not t.value.startswith(":"):
                    m += 1          # alias
                    continue
                break
        elif tok.value == "INTO" and verb == "INSERT":
            add(nxt)
        elif tok.value == "UPDATE" and k == 0:
            add(nxt)
        elif tok.value == "TABLE" and k > 0 and body[k - 1].is_word("LOCK"):
            add(nxt)
    return found


def _cics_options(body: list[Token]) -> dict[str, list[Token]]:
    """KEYWORD(arg ...) pairs at top level of an EXEC CICS command."""
    opts: dict[str, list[Token]] = {}
    k = 0
    n = len(body)
    while k < n:
        tok = body[k]
        if tok.kind == WORD and k + 1 < n and body[k + 1].is_punct("("):
            depth = 0
            m = k + 1
            arg: list[Token] = []
            while m < n:
                t = body[m]
                if t.is_punct("("):
                    depth += 1
                    if depth > 1:
                        arg.append(t)
                elif t.is_punct(")"):
                    depth -= 1
                    if depth == 0:
                        break
                    arg.append(t)
                else:
                    arg.append(t)
                m += 1
            opts.setdefault(tok.value, arg)
            k = m + 1
            continue
        if tok.kind == WORD:
            opts.setdefault(tok.value, [])
        k += 1
    return opts


def _cics_name(arg: list[Token]) -> tuple[str | None, str | None]:
    """(literal name, variable name) for a CICS option argument."""
    if len(arg) == 1:
        if arg[0].kind == LIT:
            return arg[0].value.strip().upper(), None
        if arg[0].kind == WORD:
            return None, arg[0].value
    return None, None


def _scan_cics(body: list[Token], line: int, division: str | None, facts: CobolFacts) -> None:
    if not body or body[0].kind != WORD:
        return
    command = body[0].value
    opts = _cics_options(body)

    def emit(ref_type: str, option: str, extra: dict | None = None) -> None:
        if option not in opts:
            return
        name, var = _cics_name(opts[option])
        details = {"command": command}
        if extra:
            details.update(extra)
        if var is not None:
            details["variable"] = var
        facts.references.append(Reference(ref_type, name, line, division, details))

    if command in CICS_PROGRAM_COMMANDS:
        emit(CICS_PROGRAM_COMMANDS[command], "PROGRAM")
    elif command in ("RETURN", "START"):
        emit(f"CICS_{command}_TRANSID", "TRANSID")
    elif command in ("SEND", "RECEIVE") and "MAP" in opts:
        mapset, mapset_var = _cics_name(opts.get("MAPSET", []))
        extra = {"mapset": mapset} if mapset else {}
        if mapset_var:
            extra["mapset_variable"] = mapset_var
        emit("CICS_MAP", "MAP", extra)
    elif command in CICS_FILE_COMMANDS:
        emit("CICS_FILE", "FILE" if "FILE" in opts else "DATASET")
    elif command in ("WRITEQ", "READQ", "DELETEQ") and len(body) > 1:
        emit("CICS_QUEUE", "QUEUE" if "QUEUE" in opts else "QNAME", {"queue_type": body[1].value})


def _scan_call(toks: list[Token], i: int, division: str | None, facts: CobolFacts) -> None:
    if i + 1 >= len(toks):
        return
    target = toks[i + 1]
    line = toks[i].line
    if target.kind == LIT:
        facts.references.append(Reference("STATIC_CALL", target.value.strip().upper(), line, division))
    elif target.kind == WORD and target.value not in ("USING", "PROCEDURE-POINTER"):
        facts.references.append(Reference("DYNAMIC_CALL", None, line, division, {"variable": target.value}))


def _scan_select(toks: list[Token], i: int, facts: CobolFacts) -> int:
    n = len(toks)
    j = i + 1
    if j < n and toks[j].is_word("OPTIONAL"):
        j += 1
    if j >= n or toks[j].kind != WORD:
        return j
    select = toks[j].value
    line = toks[i].line
    assign = None
    organization = None
    while j < n and not toks[j].is_punct("."):
        t = toks[j]
        if t.is_word("ASSIGN"):
            k = j + 1
            if k < n and toks[k].is_word("TO", "USING"):
                k += 1
            if k < n and toks[k].kind in (WORD, LIT):
                assign = toks[k].value.strip()
        elif t.is_word("ORGANIZATION"):
            k = j + 1
            if k < n and toks[k].is_word("IS"):
                k += 1
            if k < n and toks[k].kind == WORD:
                organization = toks[k].value
                if organization == "LINE" and k + 1 < n:
                    organization = "LINE SEQUENTIAL"
        j += 1
    if assign is not None:
        facts.file_controls.append(FileControl(select, assign, ddname_from_assign(assign), organization, line))
    return j + 1


def ddname_from_assign(assign: str) -> str | None:
    """IBM assignment-name: [label-][S-|AS-|DA-...]name. Literals are paths/DSNs, not DD names."""
    value = assign.upper()
    if "/" in value or "\\" in value or "." in value:
        return None
    parts = value.split("-")
    if len(parts) > 1 and all(p in ("UT", "UR", "DA", "S", "AS", "D", "I", "R", "V") for p in parts[:-1]):
        return parts[-1]
    return value


def _attach_dynamic_candidates(facts: CobolFacts, literal_moves: dict[str, set[str]]) -> None:
    for ref in facts.references:
        var = ref.details.get("variable")
        if ref.target is None and var:
            ref.details["variable_defined"] = var in facts.data_names
            candidates = sorted(literal_moves.get(var, set()))
            if candidates:
                ref.details["candidate_targets"] = candidates


def _note(code: str, line: int, details: dict | None = None):
    from harness.cobol.source import Note
    return Note(code, line, details or {})
