"""Parse DB2 DDL: tables, columns, keys, indexes, views and other objects."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from harness.cobol.source import Note


@dataclass
class Column:
    name: str
    type: str
    nullable: bool


@dataclass
class ForeignKey:
    columns: list[str]
    references: str
    referenced_columns: list[str]
    name: str | None = None


@dataclass
class Table:
    name: str
    line: int
    columns: list[Column] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    unique_keys: list[list[str]] = field(default_factory=list)
    comment: str | None = None
    column_comments: dict[str, str] = field(default_factory=dict)


@dataclass
class Index:
    name: str
    table: str
    columns: list[str]
    unique: bool
    line: int


@dataclass
class View:
    name: str
    line: int
    base_tables: list[str]


@dataclass
class DbObject:
    kind: str        # DATABASE, TABLESPACE, PROCEDURE, BIND_PLAN, ...
    name: str
    line: int


@dataclass
class DdlFile:
    tables: list[Table] = field(default_factory=list)
    indexes: list[Index] = field(default_factory=list)
    views: list[View] = field(default_factory=list)
    objects: list[DbObject] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)


_NAME = r"([A-Z0-9_#@$\"]+(?:\.[A-Z0-9_#@$\"]+)?)"


def _strip_comments(lines: list[str]) -> list[str]:
    out = []
    in_block = False
    for raw in lines:
        text = raw
        result = []
        i = 0
        quote = False
        while i < len(text):
            if in_block:
                end = text.find("*/", i)
                if end == -1:
                    i = len(text)
                    continue
                in_block = False
                i = end + 2
                continue
            ch = text[i]
            if quote:
                result.append(ch)
                if ch == "'":
                    quote = False
            elif ch == "'":
                quote = True
                result.append(ch)
            elif text.startswith("--", i):
                break
            elif text.startswith("/*", i):
                in_block = True
                i += 2
                continue
            else:
                result.append(ch)
            i += 1
        out.append("".join(result))
    return out


_TOKEN = re.compile(r"'|;|[A-Za-z_][A-Za-z0-9_]*")
# END IF / END WHILE close blocks that never incremented the depth.
_CONTROL_END = re.compile(r"\s+(IF|WHILE|LOOP|REPEAT|FOR)\b", re.I)


def _statements(lines: list[str]) -> list[tuple[int, str]]:
    """Split on ';' outside quotes and outside BEGIN ... END bodies."""
    stmts: list[tuple[int, str]] = []
    buf: list[str] = []
    start_line: int | None = None
    depth = 0
    quote = False

    def flush() -> None:
        nonlocal buf, start_line
        stmt = " ".join(" ".join(buf).split())
        if stmt:
            stmts.append((start_line or 1, stmt))
        buf = []
        start_line = None

    for number, text in enumerate(_strip_comments(lines), start=1):
        pos = 0
        for m in _TOKEN.finditer(text):
            tok = m.group(0)
            if quote:
                if tok == "'":
                    quote = False
                continue
            if tok == "'":
                quote = True
                continue
            if tok == ";":
                if depth == 0:
                    segment = text[pos:m.start()]
                    if segment.strip() and start_line is None:
                        start_line = number
                    buf.append(segment)
                    flush()
                    pos = m.end()
                continue
            if start_line is None:
                start_line = number
            word = tok.upper()
            if word in ("BEGIN", "CASE"):
                depth += 1
            elif word == "END" and not _CONTROL_END.match(text, m.end()):
                depth = max(0, depth - 1)
        rest = text[pos:]
        if rest.strip() and start_line is None:
            start_line = number
        buf.append(rest)
    flush()
    return stmts


def _split_top(text: str) -> list[str]:
    parts, buf, depth, quote = [], [], 0, False
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == "'":
                quote = False
            continue
        if ch == "'":
            quote = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    if "".join(buf).strip():
        parts.append("".join(buf).strip())
    return parts


def _paren_body(text: str, start: int) -> tuple[str, int]:
    """Text inside the parenthesis group starting at text[start] == '('."""
    depth = 0
    quote = False
    for k in range(start, len(text)):
        ch = text[k]
        if quote:
            if ch == "'":
                quote = False
            continue
        if ch == "'":
            quote = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:k], k + 1
    return text[start + 1:], len(text)


def _names(text: str) -> list[str]:
    return [p.split()[0].strip('"').upper() for p in _split_top(text) if p.split()]


def _bare(name: str) -> str:
    return name.replace('"', "").upper()


def parse_ddl(lines: list[str]) -> DdlFile:
    result = DdlFile()
    tables: dict[str, Table] = {}
    from harness.cobol.source import CodeLine
    from harness.cobol.tokens import tokenize
    from harness.inventory.cobol_scan import sql_tables

    for line, stmt in _statements(lines):
        upper = stmt.upper()
        m = re.match(r"CREATE\s+(?:GLOBAL\s+TEMPORARY\s+)?TABLE\s+" + _NAME + r"\s*\(", upper)
        if m:
            name = _bare(m.group(1))
            body, _ = _paren_body(stmt, m.end() - 1)
            table = Table(name, line)
            for item in _split_top(body):
                _table_item(table, item)
            tables[name] = table
            result.tables.append(table)
            continue
        m = re.match(r"ALTER\s+TABLE\s+" + _NAME + r"\s+ADD\s+(?:CONSTRAINT\s+\S+\s+)?(PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE)\s*\(", upper)
        if m:
            table = tables.get(_bare(m.group(1)))
            if table is None:
                result.notes.append(Note("alter_unknown_table", line, {"table": _bare(m.group(1))}))
                continue
            _table_item(table, stmt[m.start(2):])
            continue
        m = re.match(r"CREATE\s+(UNIQUE\s+)?INDEX\s+" + _NAME + r"\s+ON\s+" + _NAME + r"\s*\(", upper)
        if m:
            body, _ = _paren_body(stmt, m.end() - 1)
            result.indexes.append(Index(_bare(m.group(2)), _bare(m.group(3)), _names(body), bool(m.group(1)), line))
            continue
        m = re.match(r"CREATE\s+VIEW\s+" + _NAME, upper)
        if m:
            as_pos = re.search(r"\bAS\s+(SELECT|WITH)\b", upper)
            base = []
            if as_pos:
                code = [CodeLine(line, stmt[as_pos.start(1):], False, False)]
                base = [t for t, _ in sql_tables(tokenize(code).tokens)]
            result.views.append(View(_bare(m.group(1)), line, base))
            continue
        m = re.match(r"COMMENT\s+ON\s+(TABLE|COLUMN)\s+" + _NAME + r"\s+IS\s+'(.*)'$", stmt, re.I | re.S)
        if m:
            target = _bare(m.group(2))
            text = m.group(3).replace("''", "'")
            if m.group(1).upper() == "TABLE" and target in tables:
                tables[target].comment = text
            elif m.group(1).upper() == "COLUMN" and "." in target:
                tname, col = target.rsplit(".", 1)
                if tname in tables:
                    tables[tname].column_comments[col] = text
            continue
        m = re.match(r"CREATE\s+(DATABASE|TABLESPACE|STOGROUP|PROCEDURE|FUNCTION|TRIGGER|ALIAS|SYNONYM|SEQUENCE|AUXILIARY\s+TABLE)\s+" + _NAME, upper)
        if m:
            result.objects.append(DbObject(" ".join(m.group(1).split()), _bare(m.group(2)), line))
            continue
        m = re.match(r"BIND\s+(PLAN|PACKAGE)\s*\(?\s*" + _NAME, upper)
        if m:
            result.objects.append(DbObject(f"BIND_{m.group(1)}", _bare(m.group(2)), line))
            result.notes.append(Note("dsn_command_in_sql_file", line, {"command": f"BIND {m.group(1)}"}))
            continue
        if re.match(r"(GRANT|REVOKE|SET|COMMIT|DROP|LABEL|RUNSTATS|INSERT|UPDATE|DELETE)\b", upper):
            continue
        result.notes.append(Note("unrecognised_ddl_statement", line, {"start": stmt[:40]}))
    return result


def _table_item(table: Table, item: str) -> None:
    upper = item.upper().strip()
    m = re.match(r"(?:CONSTRAINT\s+(\S+)\s+)?PRIMARY\s+KEY\s*\(", upper)
    if m:
        body, _ = _paren_body(item, upper.index("(", m.start()))
        table.primary_key = _names(body)
        return
    m = re.match(r"(?:CONSTRAINT\s+(\S+)\s+)?FOREIGN\s+KEY\s*\(", upper)
    if m:
        cols, after = _paren_body(item, upper.index("(", m.start()))
        ref = re.match(r"\s*REFERENCES\s+" + _NAME + r"\s*(\()?", upper[after:])
        if ref:
            ref_cols: list[str] = []
            if ref.group(2):
                body, _ = _paren_body(item, after + ref.start(2))
                ref_cols = _names(body)
            table.foreign_keys.append(ForeignKey(_names(cols), _bare(ref.group(1)), ref_cols, m.group(1)))
        return
    m = re.match(r"(?:CONSTRAINT\s+(\S+)\s+)?UNIQUE\s*\(", upper)
    if m:
        body, _ = _paren_body(item, upper.index("(", m.start()))
        table.unique_keys.append(_names(body))
        return
    if re.match(r"(CONSTRAINT|CHECK|PERIOD|LIKE)\b", upper):
        return
    parts = item.split()
    if len(parts) < 2:
        return
    name = parts[0].strip('"').upper()
    type_match = re.match(r"\S+\s+([A-Z ]+?(?:\s*\([^)]*\))?)(?=\s|$)", upper)
    col_type = " ".join(type_match.group(1).split()) if type_match else parts[1].upper()
    nullable = "NOT NULL" not in upper
    table.columns.append(Column(name, col_type, nullable))
    if re.search(r"\bPRIMARY\s+KEY\b", upper):
        table.primary_key = [name]
