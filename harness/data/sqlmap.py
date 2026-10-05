"""Check embedded SQL against DB2 DDL: column counts, column names, host-variable types."""

from __future__ import annotations

import re
from typing import Any

_INSERT = re.compile(r"INSERT INTO ([\w.$#@]+)\s*\(([^)]*)\)\s*VALUES\s*\((.*)\)", re.S)
_SELECT_INTO = re.compile(r"SELECT (.*?) INTO (.*?) FROM ([\w.$#@]+)", re.S)
_UPDATE = re.compile(r"UPDATE ([\w.$#@]+) SET (.*?)(?: WHERE (.*))?$", re.S)
_DECLARE = re.compile(r"DECLARE ([\w-]+) CURSOR(?: WITH HOLD)? FOR SELECT (.*?) FROM ([\w.$#@]+)", re.S)
_FETCH = re.compile(r"FETCH (?:NEXT )?(?:FROM )?([\w-]+) INTO (.*)$", re.S)
_COMPARE = re.compile(r"([\w.]+)\s*(?:=|<>|<=|>=|<|>)\s*:([\w-]+)")
_TYPE = re.compile(r"(CHAR|CHARACTER|VARCHAR|DECIMAL|DEC|NUMERIC|INTEGER|INT|SMALLINT|BIGINT|DATE|TIME|TIMESTAMP)"
                   r"\s*(?:\((\d+)(?:\s*,\s*(\d+))?\))?")


def split_top(text: str) -> list[str]:
    parts, buf, depth, quote = [], [], 0, False
    for ch in text:
        if quote:
            buf.append(ch)
            quote = ch != "'"
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
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _host(expr: str) -> str | None:
    expr = expr.strip()
    if not expr.startswith(":"):
        return None
    name = expr[1:].split(":")[0].split()[0]      # drop :INDICATOR / INDICATOR :IND
    return name.upper()


def column_type(text: str) -> dict[str, Any]:
    m = _TYPE.match(text.upper())
    if not m:
        return {"type": text.upper()}
    kind = {"CHARACTER": "CHAR", "DEC": "DECIMAL", "NUMERIC": "DECIMAL", "INT": "INTEGER"}.get(m.group(1), m.group(1))
    out: dict[str, Any] = {"type": kind}
    if m.group(2):
        out["length" if kind in ("CHAR", "VARCHAR") else "precision"] = int(m.group(2))
    if m.group(3):
        out["scale"] = int(m.group(3))
    elif kind == "DECIMAL":
        out.setdefault("precision", 5)
        out["scale"] = 0
    return out


def compatible(col: dict[str, Any], field: dict[str, Any]) -> str | None:
    """None if the host variable suits the column, else a short reason."""
    t = col["type"]
    size, cat, usage = field.get("size"), field.get("category"), field.get("usage")
    if size is None:
        return None
    if t == "CHAR":
        if cat not in ("alphanumeric", "alphabetic", "group", "alphanumeric-edited"):
            return f"{cat} field for CHAR({col.get('length')})"
        if size != col.get("length"):
            return f"{size} bytes for CHAR({col.get('length')})"
    elif t == "VARCHAR":
        if cat != "group" and size > col.get("length", size):
            return f"{size} bytes for VARCHAR({col.get('length')})"
    elif t == "DECIMAL":
        if cat != "numeric":
            return f"{cat} field for DECIMAL({col.get('precision')},{col.get('scale')})"
        if field.get("digits") != col.get("precision") or (field.get("scale") or 0) != col.get("scale"):
            return (f"{field.get('digits')} digits, {field.get('scale') or 0} decimals for "
                    f"DECIMAL({col.get('precision')},{col.get('scale')})")
        if usage != "COMP-3":
            return f"{usage} usage for DECIMAL (DB2 expects COMP-3)"
    elif t in ("INTEGER", "SMALLINT", "BIGINT"):
        want = {"SMALLINT": 2, "INTEGER": 4, "BIGINT": 8}[t]
        if cat != "numeric" or usage not in ("COMP", "COMP-5") or size != want:
            return (f"{usage} {size}-byte field for {t} (expects {want}-byte binary; "
                    f"values outside the host variable's range fail at run time)")
    elif t in ("DATE", "TIME", "TIMESTAMP"):
        want = {"DATE": 10, "TIME": 8, "TIMESTAMP": 26}[t]
        if size < want:
            return f"{size} bytes for {t} (needs {want})"
    return None


def _column_name(expr: str) -> str | None:
    """Column of a select-list item, or None for expressions (COUNT(*), CASE ..., arithmetic)."""
    text = re.sub(r"\s+AS\s+\w+$", "", expr.strip(), flags=re.I)
    if re.fullmatch(r"[\w.]+", text):
        return text.split(".")[-1]
    return None


def _host_count(hosts: list[str], fields: dict[str, dict], all_fields: list[dict]) -> int:
    """Host variables after expanding a single host structure into its elementary fields."""
    if len(hosts) == 1:
        name = _host(hosts[0])
        f = fields.get(name) if name else None
        if f is not None and f["category"] == "group":
            prefix = f["qualified_name"] + "."
            return sum(1 for g in all_fields if g["record"] == f["record"] and
                       g["qualified_name"].startswith(prefix) and g["elementary"])
    return len(hosts)


def check_sql(statements: list[dict], fields: dict[str, dict], tables: dict[str, dict],
              all_fields: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """Returns (column/host-variable pairs, problems) for one program's EXEC SQL statements."""
    all_fields = all_fields or []
    pairs: list[dict] = []
    problems: list[dict] = []
    cursors: dict[str, tuple[str, list[str | None]]] = {}

    def table_for(name: str) -> dict | None:
        t = tables.get(name) or tables.get(name.split(".")[-1])
        return t

    def pair(stmt: dict, table: str, column: str | None, host: str | None, op: str) -> None:
        if host is None or column is None:
            return
        tdef = table_for(table)
        rec = {"table": table, "column": column, "host_variable": host, "operation": op,
               "file": stmt["file"], "line": stmt["line"]}
        pairs.append(rec)
        if tdef is None or not tdef.get("columns"):
            return
        cols = {c["name"]: c for c in tdef["columns"]}
        if column not in cols:
            problems.append({"code": "unknown_column", "file": stmt["file"], "line": stmt["line"],
                             "message": f"{op} uses column {column}, which table {tdef['name']} does not have"})
            return
        field = fields.get(host)
        if field is None:
            return
        why = compatible(column_type(cols[column]["type"]), field)
        if why:
            rec["mismatch"] = why
            problems.append({"code": "host_variable_type_mismatch", "file": stmt["file"], "line": stmt["line"],
                             "message": f"{tdef['name']}.{column} ({cols[column]['type']}) is paired with "
                                        f"{host}: {why}"})

    for stmt in statements:
        text = stmt["text"]
        body = re.sub(r"^EXEC SQL\s+|\s*END-EXEC$", "", text).strip()
        upper = body.upper()
        m = _DECLARE.match(upper)
        if m:
            cursors[m.group(1)] = (m.group(3), [_column_name(c) for c in split_top(m.group(2))])
            continue
        m = _INSERT.match(upper)
        if m:
            table, cols, vals = m.group(1), split_top(m.group(2)), split_top(m.group(3))
            if len(cols) != len(vals):
                problems.append({"code": "sql_column_count_mismatch", "file": stmt["file"], "line": stmt["line"],
                                 "message": f"INSERT INTO {table} lists {len(cols)} columns but "
                                            f"{len(vals)} values"})
            for col, val in zip(cols, vals):
                pair(stmt, table, col.strip(), _host(val), "INSERT")
            continue
        m = _SELECT_INTO.match(upper)
        if m:
            cols, hosts, table = split_top(m.group(1)), split_top(m.group(2)), m.group(3)
            count = _host_count(hosts, fields, all_fields)
            if len(cols) != count and "*" not in m.group(1):
                into = f"host structure {_host(hosts[0])} with {count} fields" if len(hosts) == 1 and count != 1 \
                    else f"{count} host variables"
                problems.append({"code": "sql_column_count_mismatch", "file": stmt["file"], "line": stmt["line"],
                                 "message": f"SELECT from {table} lists {len(cols)} columns but INTO names {into}"})
            if len(hosts) == len(cols):
                for col, host in zip(cols, hosts):
                    pair(stmt, table, _column_name(col), _host(host), "SELECT")
            _compare_pairs(stmt, upper, table, pair)
            continue
        m = _UPDATE.match(upper)
        if m:
            table = m.group(1)
            for assign in split_top(m.group(2)):
                if "=" in assign:
                    col, val = assign.split("=", 1)
                    pair(stmt, table, col.strip(), _host(val), "UPDATE")
            _compare_pairs(stmt, upper, table, pair)
            continue
        m = _FETCH.match(upper)
        if m and m.group(1) in cursors:
            table, cols = cursors[m.group(1)]
            hosts = split_top(m.group(2))
            count = _host_count(hosts, fields, all_fields)
            if len(cols) != count:
                problems.append({"code": "sql_column_count_mismatch", "file": stmt["file"], "line": stmt["line"],
                                 "message": f"FETCH {m.group(1)} returns {len(cols)} columns into "
                                            f"{count} host variables"})
            if len(hosts) == len(cols):
                for col, host in zip(cols, hosts):
                    pair(stmt, table, col, _host(host), "FETCH")
            continue
        if upper.startswith("DELETE FROM "):
            table = upper.split()[2]
            _compare_pairs(stmt, upper, table, pair)
    return pairs, problems


def _compare_pairs(stmt: dict, upper: str, table: str, pair) -> None:
    where = upper.split(" WHERE ", 1)
    if len(where) < 2:
        return
    for col, host in _COMPARE.findall(where[1]):
        pair(stmt, table, col.split(".")[-1], host.upper(), "WHERE")
