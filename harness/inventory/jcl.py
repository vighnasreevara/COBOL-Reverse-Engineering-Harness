"""Parse z/OS JCL: jobs, steps, DD statements, in-stream data and PROCs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from harness.cobol.source import Note

# Programs that are system utilities rather than application code.
UTILITIES = {
    "IDCAMS", "IEFBR14", "IEBGENER", "IEBCOPY", "IEBUPDTE", "IEBPTPCH", "IEHLIST",
    "IEHPROGM", "SORT", "ICEMAN", "DFSORT", "SYNCSORT", "ICETOOL", "ADRDSSU",
    "IKJEFT01", "IKJEFT1A", "IKJEFT1B", "DFSRRC00", "DSNUTILB", "DSNTIAUL",
    "DSNTEP2", "DSNTEP4", "IRXJCL", "BPXBATCH", "FTP", "IEBDG", "IGYCRCTL",
    "IEWL", "HEWL", "IEWBLINK", "DSNHPC", "DFHECP1$", "AMASPZAP",
}
TSO_BATCH = {"IKJEFT01", "IKJEFT1A", "IKJEFT1B"}

_NAME_RE = re.compile(r"^//([A-Z0-9#@$][A-Z0-9#@$]{0,7}(?:\.[A-Z0-9#@$]{1,8})?)?\s+(\S+)\s*(.*)$", re.I)


@dataclass
class DD:
    name: str | None          # None for concatenated DDs
    line: int
    dsn: str | None = None
    disp: str | None = None
    sysout: str | None = None
    dummy: bool = False
    instream: list[str] | None = None
    params: dict = field(default_factory=dict)


@dataclass
class Step:
    name: str | None
    order: int
    line: int
    program: str | None = None       # PGM=
    proc: str | None = None          # EXEC PROC or EXEC procname
    params: dict = field(default_factory=dict)
    dds: list[DD] = field(default_factory=list)
    run_program: str | None = None   # program started through IKJEFT01 / DFSRRC00


@dataclass
class Job:
    name: str
    line: int
    params: dict = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)


@dataclass
class ProcDef:
    name: str
    line: int
    steps: list[Step] = field(default_factory=list)
    instream: bool = False


@dataclass
class JclFile:
    jobs: list[Job] = field(default_factory=list)
    procs: list[ProcDef] = field(default_factory=list)
    includes: list[tuple[str, int]] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)


@dataclass
class _Stmt:
    line: int
    name: str | None
    op: str
    operands: str


def split_operands(text: str) -> tuple[list[str], dict[str, str]]:
    """Split a JCL operand field into positional and keyword parameters.

    Commas inside parentheses or quotes do not split; the field ends at the
    first blank outside quotes/parentheses (the rest is a comment).
    """
    parts: list[str] = []
    buf = []
    depth = 0
    quote = False
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == "'":
                quote = False
            continue
        if ch == "'":
            quote = True
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        elif ch == " " and depth == 0:
            break
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))

    positional: list[str] = []
    keywords: dict[str, str] = {}
    for part in parts:
        if not part:
            continue
        m = re.match(r"^([A-Z][A-Z0-9.]*)=(.*)$", part, re.I)
        if m and not part.startswith("'"):
            keywords[m.group(1).upper()] = m.group(2)
        else:
            positional.append(part)
    return positional, keywords


def _strip_parens(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if value.startswith("(") and value.endswith(")"):
        value = value[1:-1]
    return value


def parse_jcl(lines: list[str]) -> JclFile:
    result = JclFile()
    statements: list[_Stmt] = []
    instream_for: dict[int, list[str]] = {}   # statement index -> in-stream lines
    stray: list[int] = []

    i = 0
    n = len(lines)
    pending_instream: tuple[int, str] | None = None   # (stmt index, delimiter)
    while i < n:
        raw = lines[i].rstrip()
        number = i + 1

        if pending_instream is not None:
            idx, dlm = pending_instream
            terminator = raw.startswith(dlm) if dlm != "/*" else raw.startswith("/*")
            ends_on_jcl = dlm == "/*" and raw.startswith("//")
            if terminator or ends_on_jcl:
                pending_instream = None
                if terminator:
                    i += 1
                    continue
            else:
                instream_for[idx].append(raw)
                i += 1
                continue

        if raw.startswith("//*") or raw == "//" or raw.startswith("/*"):
            i += 1
            continue
        if not raw.startswith("//"):
            if raw.strip():
                stray.append(number)
            i += 1
            continue

        m = _NAME_RE.match(raw[:72] if len(raw) > 72 else raw)
        if not m:
            result.notes.append(Note("unparseable_jcl_statement", number))
            i += 1
            continue
        name, op, rest = m.group(1), m.group(2).upper(), m.group(3)
        operands = rest
        # continuation: operand field ends with a comma
        while _continues(operands) and i + 1 < n and lines[i + 1].startswith("//") \
                and not lines[i + 1].startswith("//*"):
            i += 1
            cont = lines[i].rstrip()
            cont = cont[:72] if len(cont) > 72 else cont
            operands = _operand_field(operands) + cont[2:].strip()
        statements.append(_Stmt(number, name.upper() if name else None, op, operands))

        if op == "DD":
            positional, keywords = split_operands(operands)
            if positional and positional[0] in ("*", "DATA"):
                dlm = keywords.get("DLM", "").strip("'") or "/*"
                instream_for[len(statements) - 1] = []
                pending_instream = (len(statements) - 1, dlm)
        i += 1

    if stray:
        text = [lines[k - 1].rstrip() for k in stray]
        result.notes.append(Note("stray_lines", stray[0], {"lines": stray, "text": text}))

    _build(statements, instream_for, result)
    return result


def _operand_field(operands: str) -> str:
    """Operand text without the trailing comment."""
    positional_end = 0
    depth = 0
    quote = False
    for k, ch in enumerate(operands):
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
        elif ch == " " and depth == 0:
            return operands[:k]
        positional_end = k + 1
    return operands[:positional_end]


def _continues(operands: str) -> bool:
    return _operand_field(operands).endswith(",")


def _build(statements: list[_Stmt], instream_for: dict[int, list[str]], result: JclFile) -> None:
    job: Job | None = None
    proc: ProcDef | None = None
    step: Step | None = None
    order = 0
    first_job_line = None

    for idx, st in enumerate(statements):
        positional, keywords = split_operands(st.operands)
        if st.op == "JOB":
            job = Job(st.name or "", st.line, keywords)
            result.jobs.append(job)
            proc = None
            step = None
            order = 0
            if first_job_line is None:
                first_job_line = st.line
        elif st.op == "PROC":
            proc = ProcDef(st.name or "", st.line, instream=job is not None)
            result.procs.append(proc)
            step = None
            order = 0
        elif st.op == "PEND":
            proc = None
            step = None
        elif st.op == "EXEC":
            order += 1
            step = Step(st.name, order, st.line, params=keywords)
            if "PGM" in keywords:
                step.program = keywords["PGM"].upper()
            elif "PROC" in keywords:
                step.proc = keywords["PROC"].upper()
            elif positional:
                step.proc = positional[0].upper()
            if step.program == "DFSRRC00":
                parm = _strip_parens(keywords.get("PARM", "")) or ""
                fields = [p.strip("'") for p in parm.strip("'").split(",")]
                if len(fields) > 1 and fields[1]:
                    step.run_program = fields[1].upper()
            (proc.steps if proc is not None else job.steps if job is not None else _orphan(result, st)).append(step)
        elif st.op == "DD":
            if step is None:
                continue
            dd = DD(st.name, st.line, params=keywords)
            dsn = keywords.get("DSN") or keywords.get("DSNAME")
            if dsn:
                dd.dsn = dsn.upper()
            dd.disp = keywords.get("DISP")
            dd.sysout = keywords.get("SYSOUT")
            dd.dummy = bool(positional) and positional[0].upper() == "DUMMY"
            if idx in instream_for:
                dd.instream = instream_for[idx]
            step.dds.append(dd)
            if step.program in TSO_BATCH and st.name == "SYSTSIN" and dd.instream:
                run = re.search(r"\bRUN\s+PROGRAM\s*\(\s*([A-Z0-9#@$]+)\s*\)", " ".join(dd.instream), re.I)
                if run:
                    step.run_program = run.group(1).upper()
        elif st.op == "INCLUDE":
            member = keywords.get("MEMBER")
            if member:
                result.includes.append((member.upper(), st.line))

    if first_job_line is not None:
        before = [s for s in statements if s.line < first_job_line]
        if before:
            result.notes.append(Note("statements_before_job_card", before[0].line))


def _orphan(result: JclFile, st: _Stmt) -> list[Step]:
    """EXEC outside any JOB or PROC: keep it under an anonymous PROC so nothing is lost."""
    result.notes.append(Note("exec_outside_job", st.line))
    proc = ProcDef("", st.line)
    result.procs.append(proc)
    return proc.steps


def parse_dsn(dsn: str) -> dict:
    """Describe a DSN: temporary (&&), GDG generation, member, symbolics."""
    info: dict = {"name": dsn}
    if dsn.startswith("&&"):
        info["temporary"] = True
    m = re.match(r"^(.*)\(([+-]?\d+)\)$", dsn)
    if m:
        info["base"] = m.group(1)
        info["generation"] = m.group(2)
    else:
        m = re.match(r"^(.*)\(([A-Z0-9#@$]{1,8})\)$", dsn)
        if m:
            info["base"] = m.group(1)
            info["member"] = m.group(2)
    if "&" in dsn.lstrip("&"):
        info["symbolic"] = True
    return info
