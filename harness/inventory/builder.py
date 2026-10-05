"""Build the inventory artifact: scan, parse, resolve, link, check, count."""

from __future__ import annotations

import fnmatch
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness import __version__
from harness.common.issues import IssueLog
from harness.common.textio import TextFile, read_text
from harness.inventory import bms as bms_mod
from harness.inventory import classify as kinds
from harness.inventory import csd as csd_mod
from harness.inventory import ddl as ddl_mod
from harness.inventory import idcams as idcams_mod
from harness.inventory import jcl as jcl_mod
from harness.inventory.cobol_scan import CobolFacts, scan_cobol

SCHEMA_VERSION = "1.0"
DEFAULT_EXCLUDE_DIRS = {".git", ".svn", ".hg", "bin", "obj", "node_modules", ".venv", "venv", "__pycache__", ".idea", ".vscode"}

# Routines supplied by the compiler runtime, Language Environment, DB2, CICS, IMS or MQ.
SYSTEM_PROGRAM_PREFIXES = ("CEE", "IGZ", "ILBO", "DSN", "DFH", "CSQ", "EZA", "BPX", "IRX", "IKJ")
SYSTEM_PROGRAMS = {"CBLTDLI", "AIBTDLI", "AERTDLI", "PLITDLI", "MQCONN", "MQCONNX", "MQOPEN", "MQPUT",
                   "MQPUT1", "MQGET", "MQCLOSE", "MQDISC", "MQINQ", "MQSET", "MQCMIT", "MQBACK"}
SQL_SYSTEM_INCLUDES = {"SQLCA", "SQLDA"}

# Notes from the parsers become issues with these severities.
NOTE_SEVERITY = {
    "nonstandard_comment": "info",
    "code_in_indicator_column": "warning",
    "literal_truncated_at_column_72": "warning",
    "unterminated_literal": "warning",
    "malformed_copy": "warning",
    "copy_without_period": "warning",
    "exec_without_end_exec": "warning",
    "stray_lines": "warning",
    "unparseable_jcl_statement": "warning",
    "exec_outside_job": "warning",
    "statements_before_job_card": "info",
    "nonstandard_continuation": "info",
    "missing_continuation_line": "warning",
    "operands_beyond_column_71": "warning",
    "assembler_instruction_in_label_column": "warning",
    "map_outside_mapset": "warning",
    "field_outside_map": "warning",
    "dsn_command_in_sql_file": "info",
    "unrecognised_ddl_statement": "info",
    "alter_unknown_table": "warning",
}
NOTE_MESSAGES = {
    "nonstandard_comment": "comment marked with '*' outside the indicator column",
    "code_in_indicator_column": "program text starts in the indicator column (column 7)",
    "literal_truncated_at_column_72": "literal continues past column 72 and would be truncated by the compiler",
    "unterminated_literal": "literal is not closed before the end of the line",
    "malformed_copy": "COPY statement without a member name",
    "copy_without_period": "COPY statement is not terminated by a period",
    "exec_without_end_exec": "EXEC block has no END-EXEC",
    "stray_lines": "lines that are neither JCL statements nor in-stream data (often in-stream data cut short by a //* line)",
    "unparseable_jcl_statement": "JCL statement could not be parsed",
    "exec_outside_job": "EXEC statement outside any JOB or PROC",
    "statements_before_job_card": "JCL statements appear before the JOB statement",
    "nonstandard_continuation": "BMS continuation without a character in column 72",
    "missing_continuation_line": "BMS statement expects a continuation line that is missing",
    "operands_beyond_column_71": "BMS operands extend past column 71 and would be lost by the assembler",
    "assembler_instruction_in_label_column": "assembler instruction written in the label column (column 1)",
    "map_outside_mapset": "DFHMDI map defined outside a DFHMSD mapset",
    "field_outside_map": "DFHMDF field defined outside a DFHMDI map",
    "dsn_command_in_sql_file": "DSN subcommand (not SQL) in a .sql file",
    "unrecognised_ddl_statement": "SQL statement not recognised by the DDL parser",
    "alter_unknown_table": "ALTER TABLE for a table not defined in this file",
}


@dataclass
class ScanOptions:
    root: Path
    exclude_dirs: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDE_DIRS))
    exclude_globs: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    timestamp: bool = True


class _Ref:
    __slots__ = ("type", "from_id", "target", "to_id", "resolution", "path", "line", "division", "details")

    def __init__(self, type_: str, from_id: str, target: str | None, path: str, line: int,
                 division: str | None = None, details: dict | None = None) -> None:
        self.type = type_
        self.from_id = from_id
        self.target = target
        self.to_id: str | None = None
        self.resolution = "unresolved"
        self.path = path
        self.line = line
        self.division = division
        self.details = details or {}


class InventoryBuilder:
    def __init__(self, options: ScanOptions) -> None:
        self.opt = options
        self.root = options.root.resolve()
        self.issues = IssueLog()
        self.files: list[dict] = []
        self.programs: list[dict] = []
        self.copybooks: list[dict] = []
        self.jobs: list[dict] = []
        self.procs: list[dict] = []
        self.csd: list[dict] = []
        self.mapsets: list[dict] = []
        self.tables: list[dict] = []
        self.views: list[dict] = []
        self.indexes: list[dict] = []
        self.db_objects: list[dict] = []
        self.vsam: list[dict] = []
        self.refs: list[_Ref] = []
        self._facts: dict[str, CobolFacts] = {}      # node id -> facts

    # ------------------------------------------------------------------ scan
    def build(self) -> dict[str, Any]:
        for path in self._walk():
            self._scan_file(path)
        self._index()
        self._resolve()
        self._link_datasets()
        self._classify_programs()
        self._checks()
        artifact = self._assemble()
        if self.opt.entry_points:
            from harness.inventory.scope import apply_scope
            artifact = apply_scope(artifact, self.opt.entry_points)
        return artifact

    def _walk(self) -> list[Path]:
        found = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in self.opt.exclude_dirs)
            for name in sorted(filenames):
                path = Path(dirpath) / name
                rel = self._rel(path)
                if any(fnmatch.fnmatch(rel, g) for g in self.opt.exclude_globs):
                    continue
                found.append(path)
        return found

    def _rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def _scan_file(self, path: Path) -> None:
        rel = self._rel(path)
        try:
            tf = read_text(path)
        except OSError as exc:
            self.issues.add("error", "unreadable_file", f"cannot read file: {exc}", path=rel)
            return
        kind = kinds.classify(path, tf.text)
        self.files.append({
            "path": rel, "kind": kind, "size_bytes": tf.size_bytes, "lines": len(tf.lines),
            "sha256": tf.sha256, "encoding": tf.encoding, "line_ending": tf.line_ending,
            "final_newline": tf.final_newline,
        })
        if tf.is_blank and kind not in (kinds.DOC, kinds.OTHER):
            self.issues.add("warning", "empty_file", f"{kind} file is empty or blank", path=rel)
            if kind == kinds.PROGRAM:
                self.programs.append(self._program_record(rel, path, None))
            return
        handler = {
            kinds.PROGRAM: self._scan_program,
            kinds.COPYBOOK: self._scan_copybook,
            kinds.JCL: self._scan_jcl,
            kinds.CSD: self._scan_csd,
            kinds.BMS: self._scan_bms,
            kinds.DDL: self._scan_ddl,
            kinds.IDCAMS: self._scan_idcams,
        }.get(kind)
        if handler:
            handler(rel, path, tf)

    def _notes(self, rel: str, notes: list, subject: str | None = None) -> None:
        truncated = {n.line for n in notes if n.code == "literal_truncated_at_column_72"}
        grouped: dict[str, list] = defaultdict(list)
        for note in notes:
            if note.code == "unterminated_literal" and note.line in truncated:
                continue          # same defect, already reported as truncated at column 72
            grouped[note.code].append(note)
        for code, items in grouped.items():
            severity = NOTE_SEVERITY.get(code, "info")
            first = items[0]
            details = dict(first.details)
            lines = sorted({n.line for n in items})
            message = NOTE_MESSAGES.get(code, code.replace("_", " "))
            if code == "stray_lines":
                lines = first.details.get("lines", lines)
                text = details.pop("text", [])
                details.pop("lines", None)
                message = self._stray_message(message, text, lines[0])
            if len(lines) > 1:
                details["lines"] = lines
                details["count"] = len(lines)
                message = f"{message} ({len(lines)} occurrences)"
            self.issues.add(severity, code, message, path=rel, line=lines[0], subject=subject, details=details)

    @staticmethod
    def _stray_message(message: str, text: list[str], first_line: int) -> str:
        joined = "\n".join(text)
        if idcams_mod.looks_like_idcams(joined):
            defs = idcams_mod.parse_idcams(text, first_line).definitions
            if defs:
                names = ", ".join(f"DEFINE {d.kind} {d.name}" for d in defs)
                return (f"IDCAMS control statements ({names}) sit outside any in-stream data set, "
                        f"so IDCAMS never receives them; a //* line probably ended the SYSIN data early")
        return message

    # ------------------------------------------------------------- programs
    def _program_record(self, rel: str, path: Path, facts: CobolFacts | None) -> dict:
        member = path.stem.upper()
        pid = facts.program_ids[0][0] if facts and facts.program_ids else None
        rec = {
            "id": f"program:{pid or member}",
            "name": pid or member,
            "program_id": pid,
            "member": member,
            "path": rel,
            "area": Path(rel).parent.name or None,
            "source_format": facts.fmt if facts else None,
            "kind": None,
            "entry_point": False,
            "procedure_using": facts.procedure_using if facts else [],
            "has_sql": bool(facts and facts.has_sql),
            "has_cics": bool(facts and facts.has_cics),
            "has_dli": bool(facts and facts.has_dli),
            "terminators": sorted(set(facts.terminators)) if facts else [],
            "file_controls": [
                {"select": fc.select, "assign": fc.assign, "ddname": fc.ddname,
                 "organization": fc.organization, "line": fc.line}
                for fc in (facts.file_controls if facts else [])
            ],
            "nested_program_ids": [p for p, _ in facts.program_ids[1:]] if facts else [],
            "executed_by": [],
            "called_by": [],
            "datasets": [],
        }
        return rec

    def _scan_program(self, rel: str, path: Path, tf: TextFile) -> None:
        facts = scan_cobol(tf.lines)
        rec = self._program_record(rel, path, facts)
        self.programs.append(rec)
        node = rec["id"]
        self._facts[node] = facts
        self._notes(rel, facts.notes, node)
        if not facts.program_ids:
            self.issues.add("warning", "program_without_program_id",
                            f"no PROGRAM-ID found; using member name {rec['member']}", path=rel, subject=node)
        elif rec["program_id"] != rec["member"]:
            self.issues.add("info", "program_id_differs_from_member",
                            f"PROGRAM-ID {rec['program_id']} differs from member name {rec['member']}",
                            path=rel, line=facts.program_ids[0][1], subject=node)
        if facts.fmt == "free":
            self.issues.add("info", "free_format_source", "source is in free format (no column layout)",
                            path=rel, subject=node)
        if not facts.ends_cleanly:
            self.issues.add("warning", "possibly_truncated_source",
                            "source does not end with a period; the file may be truncated",
                            path=rel, line=len(tf.lines), subject=node)
        self._add_cobol_refs(node, rel, facts)

    def _scan_copybook(self, rel: str, path: Path, tf: TextFile) -> None:
        facts = scan_cobol(tf.lines)
        member = path.stem.upper()
        node = f"copybook:{member}"
        if facts.has_procedure_code and facts.has_data_entries:
            content = "mixed"
        elif facts.has_procedure_code:
            content = "procedure"
        elif facts.has_data_entries:
            content = "data"
        else:
            content = "other"
        self.copybooks.append({
            "id": node, "name": member, "member": member, "path": rel,
            "area": Path(rel).parent.name or None,
            "source_format": facts.fmt, "content_kind": content,
            "has_sql": facts.has_sql, "has_cics": facts.has_cics, "used_by": [],
        })
        self._facts[node] = facts
        self._notes(rel, facts.notes, node)
        self._add_cobol_refs(node, rel, facts)

    def _add_cobol_refs(self, node: str, rel: str, facts: CobolFacts) -> None:
        for r in facts.references:
            self.refs.append(_Ref(r.type, node, r.target, rel, r.line, r.division, dict(r.details)))

    # ------------------------------------------------------------------ jcl
    def _scan_jcl(self, rel: str, path: Path, tf: TextFile) -> None:
        parsed = jcl_mod.parse_jcl(tf.lines)
        for job in parsed.jobs:
            node = f"job:{job.name}"
            self.jobs.append({"id": node, "name": job.name, "path": rel, "line": job.line,
                              "steps": [self._step_record(s) for s in job.steps]})
            self._jcl_refs(node, rel, job.steps)
            self._notes(rel, parsed.notes, node)
        for proc in parsed.procs:
            name = proc.name or path.stem.upper()
            node = f"proc:{name}"
            self.procs.append({"id": node, "name": name, "path": rel, "line": proc.line,
                               "instream": proc.instream, "steps": [self._step_record(s) for s in proc.steps]})
            self._jcl_refs(node, rel, proc.steps)
        if not parsed.jobs:
            self._notes(rel, parsed.notes)
        for member, line in parsed.includes:
            self.refs.append(_Ref("JCL_INCLUDE", f"job:{parsed.jobs[0].name}" if parsed.jobs else f"proc:{path.stem.upper()}",
                                  member, rel, line))

    @staticmethod
    def _step_record(step: jcl_mod.Step) -> dict:
        return {
            "name": step.name, "order": step.order, "line": step.line,
            "program": step.program, "proc": step.proc, "run_program": step.run_program,
            "utility": bool(step.program and step.program in jcl_mod.UTILITIES),
            "params": {k: v for k, v in step.params.items() if k in ("PARM", "COND", "REGION", "TIME")},
            "dds": [
                {"name": dd.name, "line": dd.line, "dsn": dd.dsn, "disp": dd.disp, "sysout": dd.sysout,
                 "dummy": dd.dummy, "instream_lines": len(dd.instream) if dd.instream is not None else None,
                 **_dcb(dd.params)}
                for dd in step.dds
            ],
        }

    def _jcl_refs(self, node: str, rel: str, steps: list[jcl_mod.Step]) -> None:
        for step in steps:
            info = {"step": step.name, "order": step.order}
            if step.program:
                self.refs.append(_Ref("JCL_EXEC", node, step.program, rel, step.line, details=dict(info)))
            if step.proc:
                self.refs.append(_Ref("JCL_PROC", node, step.proc, rel, step.line, details=dict(info)))
            if step.run_program:
                self.refs.append(_Ref("JCL_RUN", node, step.run_program, rel, step.line,
                                      details={**info, "via": step.program}))
            ddname = None
            for dd in step.dds:
                ddname = dd.name or ddname
                if dd.dsn:
                    dsn_info = jcl_mod.parse_dsn(dd.dsn)
                    self.refs.append(_Ref("JCL_DD", node, dsn_info.get("base", dd.dsn), rel, dd.line,
                                          details={**info, "ddname": ddname, "disp": dd.disp,
                                                   **{k: v for k, v in dsn_info.items() if k != "name"}}))
                if dd.instream and step.program == "IDCAMS" and ddname == "SYSIN":
                    result = idcams_mod.parse_idcams(dd.instream, dd.line + 1)
                    for d in result.definitions:
                        self._add_vsam(d, rel, source=f"{node} step {step.name}")
                        self.refs.append(_Ref("IDCAMS_DEFINE", node, d.name, rel, d.line, details=dict(info)))

    # ------------------------------------------------------- other sources
    def _scan_csd(self, rel: str, path: Path, tf: TextFile) -> None:
        for res in csd_mod.parse_csd(tf.lines):
            node = f"csd_{res.type.lower()}:{res.name}"
            if res.type == "TRANSACTION":
                node = f"transaction:{res.name}"
            self.csd.append({"id": node, "type": res.type, "name": res.name, "path": rel,
                             "line": res.line, "attributes": res.attributes})
            attrs = res.attributes
            if res.type == "TRANSACTION" and attrs.get("PROGRAM"):
                self.refs.append(_Ref("CSD_TRANSACTION", node, attrs["PROGRAM"].upper(), rel, res.line))
            elif res.type == "FILE" and attrs.get("DSNAME"):
                self.refs.append(_Ref("CSD_FILE", f"cics_file:{res.name}", attrs["DSNAME"].upper(), rel, res.line))

    def _scan_bms(self, rel: str, path: Path, tf: TextFile) -> None:
        parsed = bms_mod.parse_bms(tf.lines)
        for ms in parsed.mapsets:
            name = ms.name or path.stem.upper()
            self.mapsets.append({
                "id": f"mapset:{name}", "name": name, "path": rel, "line": ms.line,
                "params": ms.params,
                "maps": [
                    {"id": f"map:{name}.{m.name}", "name": m.name, "line": m.line, "size": m.size,
                     "field_count": len(m.fields),
                     "fields": [{"name": f.name, "line": f.line, "pos": f.pos, "length": f.length,
                                 "attrb": f.attrb} for f in m.fields if f.name]}
                    for m in ms.maps
                ],
            })
        self._notes(rel, parsed.notes, f"mapset:{parsed.mapsets[0].name}" if parsed.mapsets else None)

    def _scan_ddl(self, rel: str, path: Path, tf: TextFile) -> None:
        parsed = ddl_mod.parse_ddl(tf.lines)
        for t in parsed.tables:
            self.tables.append({
                "id": f"table:{t.name}", "name": t.name, "path": rel, "line": t.line,
                "columns": [{"name": c.name, "type": c.type, "nullable": c.nullable,
                             **({"comment": t.column_comments[c.name]} if c.name in t.column_comments else {})}
                            for c in t.columns],
                "primary_key": t.primary_key,
                "foreign_keys": [{"columns": fk.columns, "references": fk.references,
                                  "referenced_columns": fk.referenced_columns} for fk in t.foreign_keys],
                "unique_keys": t.unique_keys,
                "comment": t.comment,
            })
            for fk in t.foreign_keys:
                self.refs.append(_Ref("DB2_FOREIGN_KEY", f"table:{t.name}", fk.references, rel, t.line,
                                      details={"columns": fk.columns}))
        for v in parsed.views:
            self.views.append({"id": f"view:{v.name}", "name": v.name, "path": rel, "line": v.line,
                               "base_tables": v.base_tables})
            for base in v.base_tables:
                self.refs.append(_Ref("DB2_VIEW_BASE", f"view:{v.name}", base, rel, v.line))
        for ix in parsed.indexes:
            self.indexes.append({"name": ix.name, "table": ix.table, "columns": ix.columns,
                                 "unique": ix.unique, "path": rel, "line": ix.line})
        for o in parsed.objects:
            self.db_objects.append({"kind": o.kind, "name": o.name, "path": rel, "line": o.line})
        self._notes(rel, parsed.notes)

    def _scan_idcams(self, rel: str, path: Path, tf: TextFile) -> None:
        for d in idcams_mod.parse_idcams(tf.lines).definitions:
            self._add_vsam(d, rel, source=rel)

    def _add_vsam(self, d: idcams_mod.VsamDefinition, rel: str, source: str) -> None:
        self.vsam.append({
            "id": f"dataset:{d.name}", "name": d.name, "kind": d.kind, "organization": d.organization,
            "key_length": d.key_length, "key_offset": d.key_offset,
            "record_size_avg": d.record_size_avg, "record_size_max": d.record_size_max,
            "related_to": d.related_to, "attributes": d.attributes,
            "path": rel, "line": d.line, "source": source,
        })

    # -------------------------------------------------------------- resolve
    def _index(self) -> None:
        self.prog_by_id: dict[str, list[dict]] = defaultdict(list)
        self.prog_by_member: dict[str, list[dict]] = defaultdict(list)
        for p in self.programs:
            if p["program_id"]:
                self.prog_by_id[p["program_id"]].append(p)
            self.prog_by_member[p["member"]].append(p)
        self.copy_by_name: dict[str, list[dict]] = defaultdict(list)
        for c in self.copybooks:
            self.copy_by_name[c["member"]].append(c)
        self.csd_by_type: dict[str, dict[str, dict]] = defaultdict(dict)
        for r in self.csd:
            self.csd_by_type[r["type"]][r["name"]] = r
        self.maps: dict[tuple[str, str], dict] = {}
        self.mapset_names = {m["name"] for m in self.mapsets}
        for ms in self.mapsets:
            for m in ms["maps"]:
                self.maps[(ms["name"], m["name"])] = m
        self.table_names = {t["name"]: t for t in self.tables}
        self.table_names.update({v["name"]: v for v in self.views})
        self.table_short = defaultdict(list)
        for name, rec in self.table_names.items():
            self.table_short[name.split(".")[-1]].append(rec)
        self.job_names = {j["name"] for j in self.jobs}
        self.proc_names = {p["name"] for p in self.procs}

    def _find_program(self, name: str) -> list[dict]:
        return self.prog_by_id.get(name) or self.prog_by_member.get(name) or []

    def _resolve(self) -> None:
        for ref in self.refs:
            handler = getattr(self, f"_resolve_{ref.type.lower()}", None)
            if handler:
                handler(ref)
            else:
                ref.resolution = "resolved"
                ref.to_id = f"{_target_kind(ref.type)}:{ref.target}"

    def _program_target(self, ref: _Ref, missing_code: str = "missing_program") -> None:
        name = ref.target
        if name is None:
            ref.resolution = "dynamic"
            var = ref.details.get("variable")
            candidates = [c for c in ref.details.get("candidate_targets", []) if self._find_program(c)]
            if candidates:
                ref.details["resolved_candidates"] = candidates
            self.issues.add("info", "dynamic_call",
                            f"{ref.type} target held in variable {var}; resolved statically only through "
                            f"literal assignments ({', '.join(ref.details.get('candidate_targets', [])) or 'none found'})",
                            path=ref.path, line=ref.line, subject=ref.from_id, details={"variable": var})
            return
        found = self._find_program(name)
        if found:
            ref.resolution = "resolved"
            ref.to_id = found[0]["id"]
            if len(found) > 1:
                ref.details["candidates"] = [p["path"] for p in found]
            return
        ref.to_id = f"program:{name}"
        if name.startswith(SYSTEM_PROGRAM_PREFIXES) or name in SYSTEM_PROGRAMS:
            ref.resolution = "system"
            self.issues.add("info", "system_program", f"{ref.type} to runtime/system routine {name}",
                            path=ref.path, line=ref.line, subject=ref.from_id, details={"target": name})
            return
        ref.resolution = "unresolved"
        self.issues.add("warning", missing_code, f"{ref.type} target {name} is not in the scanned source",
                        path=ref.path, line=ref.line, subject=ref.from_id, details={"target": name})

    _resolve_static_call = _program_target
    _resolve_dynamic_call = _program_target
    _resolve_cics_link = _program_target
    _resolve_cics_xctl = _program_target
    _resolve_cics_load = _program_target

    def _resolve_csd_transaction(self, ref: _Ref) -> None:
        self._program_target(ref, "csd_program_missing")

    def _resolve_jcl_run(self, ref: _Ref) -> None:
        self._program_target(ref)

    def _resolve_jcl_exec(self, ref: _Ref) -> None:
        name = ref.target or ""
        if name in jcl_mod.UTILITIES:
            ref.resolution = "system"
            ref.to_id = f"utility:{name}"
            return
        self._program_target(ref, "missing_jcl_program")

    def _resolve_jcl_proc(self, ref: _Ref) -> None:
        ref.to_id = f"proc:{ref.target}"
        if ref.target in self.proc_names:
            ref.resolution = "resolved"
        else:
            ref.resolution = "external"
            self.issues.add("info", "external_proc", f"PROC {ref.target} is not in the scanned source "
                            "(likely a catalogued procedure)", path=ref.path, line=ref.line,
                            subject=ref.from_id, details={"target": ref.target})

    def _resolve_jcl_include(self, ref: _Ref) -> None:
        ref.to_id = f"jcl_include:{ref.target}"
        ref.resolution = "external"

    def _resolve_copy(self, ref: _Ref) -> None:
        name = ref.target or ""
        found = self.copy_by_name.get(name, [])
        ref.to_id = f"copybook:{name}"
        if found:
            ref.resolution = "resolved"
            if len(found) > 1:
                ref.resolution = "ambiguous"
                ref.details["candidates"] = [c["path"] for c in found]
                self.issues.add("warning", "ambiguous_member",
                                f"COPY {name} matches {len(found)} copybooks", path=ref.path, line=ref.line,
                                subject=ref.from_id, details={"candidates": ref.details["candidates"]})
            return
        ref.resolution = "unresolved"
        if self._find_program(name):
            self.issues.add("warning", "copy_target_is_program",
                            f"COPY {name}: no copybook with that name, but a program {name} exists",
                            path=ref.path, line=ref.line, subject=ref.from_id, details={"target": name})
        else:
            self.issues.add("warning", "missing_copybook", f"copybook {name} is not in the scanned source",
                            path=ref.path, line=ref.line, subject=ref.from_id, details={"target": name})

    def _resolve_sql_include(self, ref: _Ref) -> None:
        name = ref.target or ""
        if name in SQL_SYSTEM_INCLUDES:
            # The DB2 precompiler supplies SQLCA/SQLDA for EXEC SQL INCLUDE.
            ref.resolution = "system"
            ref.to_id = f"system_include:{name}"
            return
        found = self.copy_by_name.get(name, [])
        ref.to_id = f"copybook:{name}"
        if found:
            ref.resolution = "resolved"
            return
        ref.resolution = "unresolved"
        self.issues.add("warning", "missing_include", f"SQL INCLUDE member {name} is not in the scanned source",
                        path=ref.path, line=ref.line, subject=ref.from_id, details={"target": name})

    def _resolve_cics_map(self, ref: _Ref) -> None:
        mapset = ref.details.get("mapset")
        name = ref.target
        if name is None:
            ref.resolution = "dynamic"
            return
        if mapset is None:
            matches = [k for k in self.maps if k[1] == name]
            mapset = matches[0][0] if len(matches) == 1 else None
        ref.to_id = f"map:{mapset or '?'}.{name}"
        if mapset and (mapset, name) in self.maps:
            ref.resolution = "resolved"
            return
        ref.resolution = "unresolved"
        if mapset and mapset not in self.mapset_names:
            self.issues.add("warning", "missing_mapset", f"mapset {mapset} (map {name}) is not in the scanned source",
                            path=ref.path, line=ref.line, subject=ref.from_id, details={"mapset": mapset, "map": name})
        else:
            available = sorted(m for (ms, m) in self.maps if ms == mapset) if mapset else []
            self.issues.add("warning", "missing_map",
                            f"map {name} is not defined in mapset {mapset or '(unknown)'}",
                            path=ref.path, line=ref.line, subject=ref.from_id,
                            details={"mapset": mapset, "map": name, "maps_in_mapset": available})

    def _resolve_cics_file(self, ref: _Ref) -> None:
        if ref.target is None:
            ref.resolution = "dynamic"
            return
        ref.to_id = f"cics_file:{ref.target}"
        if ref.target in self.csd_by_type.get("FILE", {}):
            ref.resolution = "resolved"
        else:
            ref.resolution = "unresolved"
            if self.csd:
                self.issues.add("warning", "missing_cics_file",
                                f"CICS file {ref.target} has no CSD FILE definition",
                                path=ref.path, line=ref.line, subject=ref.from_id, details={"target": ref.target})

    def _transaction_target(self, ref: _Ref) -> None:
        if ref.target is None:
            ref.resolution = "dynamic"
            return
        ref.to_id = f"transaction:{ref.target}"
        if ref.target in self.csd_by_type.get("TRANSACTION", {}):
            ref.resolution = "resolved"
        else:
            ref.resolution = "unresolved"
            if self.csd:
                self.issues.add("warning", "missing_transaction",
                                f"transaction {ref.target} has no CSD definition", path=ref.path,
                                line=ref.line, subject=ref.from_id, details={"target": ref.target})

    _resolve_cics_return_transid = _transaction_target
    _resolve_cics_start_transid = _transaction_target

    def _resolve_cics_queue(self, ref: _Ref) -> None:
        ref.resolution = "resolved" if ref.target else "dynamic"
        if ref.target:
            ref.to_id = f"queue:{ref.target}"

    def _table_target(self, ref: _Ref) -> None:
        name = ref.target or ""
        op = ref.details.get("operation")
        if op == "DECLARE_TEMPORARY" or name.startswith("SESSION."):
            ref.resolution = "temporary"
            ref.to_id = f"table:{name}"
            return
        if name.startswith(("SYSIBM.", "SYSCAT.", "SYSSTAT.", "SYSIBMADM.")):
            ref.resolution = "system"
            ref.to_id = f"table:{name}"
            return
        rec = self.table_names.get(name)
        if rec is None:
            short = self.table_short.get(name.split(".")[-1], [])
            rec = short[0] if len(short) == 1 else None
        if rec is not None:
            ref.resolution = "resolved"
            ref.to_id = rec["id"]
            return
        ref.resolution = "undefined"
        ref.to_id = f"table:{name}"
        if self.tables or self.views:
            self.issues.add("info", "undefined_table", f"table {name} has no DDL in the scanned source",
                            path=ref.path, line=ref.line, subject=ref.from_id, details={"target": name})

    _resolve_sql_table = _table_target
    _resolve_db2_foreign_key = _table_target
    _resolve_db2_view_base = _table_target

    def _resolve_sql_call(self, ref: _Ref) -> None:
        ref.to_id = f"procedure:{ref.target}" if ref.target else None
        known = {o["name"] for o in self.db_objects if o["kind"] == "PROCEDURE"}
        ref.resolution = "dynamic" if ref.target is None else ("resolved" if ref.target in known else "undefined")

    def _resolve_jcl_dd(self, ref: _Ref) -> None:
        ref.to_id = f"dataset:{ref.target}"
        ref.resolution = "temporary" if ref.details.get("temporary") else "resolved"

    def _resolve_csd_file(self, ref: _Ref) -> None:
        ref.to_id = f"dataset:{ref.target}"
        ref.resolution = "resolved"

    def _resolve_idcams_define(self, ref: _Ref) -> None:
        ref.to_id = f"dataset:{ref.target}"
        ref.resolution = "resolved"

    # ------------------------------------------------------------- linking
    def _link_datasets(self) -> None:
        """Program file (SELECT ... ASSIGN ddname) -> JCL DD -> dataset, per job step."""
        by_id = {p["id"]: p for p in self.programs}
        for owner in self.jobs + self.procs:
            for step in owner["steps"]:
                target = step["run_program"] or step["program"]
                if not target:
                    continue
                progs = self._find_program(target)
                if not progs:
                    continue
                prog = by_id[progs[0]["id"]]
                dds: dict[str, dict] = {}
                current = None
                for dd in step["dds"]:
                    current = dd["name"] or current
                    if current and current not in dds:
                        dds[current] = dd
                for fc in prog["file_controls"]:
                    ddname = fc["ddname"]
                    if not ddname:
                        continue
                    dd = dds.get(ddname)
                    if dd is None:
                        self.issues.add("warning", "missing_dd_for_file",
                                        f"{prog['name']} file {fc['select']} needs DD {ddname}, "
                                        f"which step {step['name']} of {owner['name']} does not define",
                                        path=owner["path"], line=step["line"], subject=prog["id"],
                                        details={"job": owner["name"], "step": step["name"], "ddname": ddname})
                        continue
                    link = {"job": owner["name"], "step": step["name"], "ddname": ddname, "select": fc["select"],
                            "dsn": dd["dsn"], "disp": dd["disp"], "sysout": dd["sysout"], "dummy": dd["dummy"]}
                    prog["datasets"].append(link)
                    if dd["dsn"]:
                        base = jcl_mod.parse_dsn(dd["dsn"]).get("base", dd["dsn"])
                        self.refs.append(_Ref("PROGRAM_DATASET", prog["id"], base, owner["path"], dd["line"],
                                              details={k: v for k, v in link.items() if k != "dsn"}))
                        self.refs[-1].to_id = f"dataset:{base}"
                        self.refs[-1].resolution = "resolved"

    def _classify_programs(self) -> None:
        inbound: dict[str, set[str]] = defaultdict(set)
        executed: dict[str, set[str]] = defaultdict(set)
        transactions: dict[str, set[str]] = defaultdict(set)
        for ref in self.refs:
            if ref.to_id is None or ref.resolution not in ("resolved", "ambiguous"):
                continue
            if ref.type in ("STATIC_CALL", "CICS_LINK", "CICS_XCTL", "CICS_LOAD"):
                inbound[ref.to_id].add(ref.from_id)
            elif ref.type in ("JCL_EXEC", "JCL_RUN"):
                executed[ref.to_id].add(ref.from_id)
            elif ref.type == "CSD_TRANSACTION":
                transactions[ref.to_id].add(ref.from_id)
        # A CALL inside a copybook is made by every member that copies it.
        copied_by: dict[str, set[str]] = defaultdict(set)
        for ref in self.refs:
            if ref.type == "COPY" and ref.to_id and ref.resolution in ("resolved", "ambiguous"):
                copied_by[ref.to_id].add(ref.from_id)

        def expand(member: str, seen: set[str]) -> set[str]:
            if not member.startswith("copybook:") or member in seen:
                return {member} if member.startswith("program:") else set()
            seen.add(member)
            out: set[str] = set()
            for user in copied_by.get(member, ()):
                out |= expand(user, seen)
            return out

        for target, callers in inbound.items():
            for caller in list(callers):
                if caller.startswith("copybook:"):
                    callers |= expand(caller, set())

        csd_programs = self.csd_by_type.get("PROGRAM", {})
        for p in self.programs:
            pid = p["id"]
            p["called_by"] = sorted(inbound.get(pid, set()))
            p["executed_by"] = sorted(executed.get(pid, set()) | transactions.get(pid, set()))
            p["entry_point"] = bool(p["executed_by"])
            if p["program_id"] is None and not self._facts.get(pid):
                p["kind"] = "unknown"
            elif p["has_cics"] or p["name"] in csd_programs:
                p["kind"] = "online"
            elif executed.get(pid):
                p["kind"] = "batch"
            elif p["procedure_using"] or inbound.get(pid):
                p["kind"] = "subroutine"
            else:
                p["kind"] = "batch"

    # --------------------------------------------------------------- checks
    def _checks(self) -> None:
        # duplicate program ids
        for pid, recs in self.prog_by_id.items():
            if len(recs) > 1:
                self.issues.add("error", "duplicate_program_id",
                                f"PROGRAM-ID {pid} is defined in {len(recs)} files",
                                path=recs[0]["path"], subject=f"program:{pid}",
                                details={"paths": [r["path"] for r in recs]})
        # names used for both a program and a copybook
        for name in sorted(set(self.copy_by_name) & ({p["name"] for p in self.programs})):
            self.issues.add("info", "name_collision", f"{name} is both a program and a copybook",
                            subject=f"copybook:{name}",
                            details={"paths": [p["path"] for p in self._find_program(name)]
                                     + [c["path"] for c in self.copy_by_name[name]]})
        # copybook usage
        used: dict[str, set[str]] = defaultdict(set)
        copy_division: dict[str, set[str]] = defaultdict(set)
        for ref in self.refs:
            if ref.type in ("COPY", "SQL_INCLUDE") and ref.to_id and ref.resolution in ("resolved", "ambiguous"):
                used[ref.to_id].add(ref.from_id)
                if ref.division:
                    copy_division[ref.to_id].add(ref.division)
        for c in self.copybooks:
            c["used_by"] = sorted(u.split(":", 1)[1] for u in used.get(c["id"], set()))
            if not c["used_by"]:
                self.issues.add("info", "unused_copybook", f"copybook {c['name']} is not copied by any scanned member",
                                path=c["path"], subject=c["id"])
            if c["content_kind"] in ("procedure", "mixed") and "DATA" in copy_division.get(c["id"], set()):
                users = sorted(r.from_id for r in self.refs if r.to_id == c["id"] and r.division == "DATA")
                self.issues.add("warning", "procedure_copybook_in_data_division",
                                f"copybook {c['name']} contains procedure code but is copied into the DATA DIVISION",
                                path=c["path"], subject=c["id"], details={"copied_by": users})
        # programs nobody reaches
        has_jcl = bool(self.jobs)
        csd_programs = self.csd_by_type.get("PROGRAM", {})
        for p in self.programs:
            if p["kind"] == "unknown":
                continue
            if p["kind"] == "subroutine" and not p["called_by"]:
                self.issues.add("info", "subroutine_without_callers",
                                f"subroutine {p['name']} is not called by any scanned program",
                                path=p["path"], subject=p["id"])
            elif p["kind"] == "batch" and not p["executed_by"] and not p["called_by"] and has_jcl:
                self.issues.add("info", "program_not_executed",
                                f"batch program {p['name']} is not executed by any scanned JCL",
                                path=p["path"], subject=p["id"])
            if p["kind"] == "online" and self.csd and p["name"] not in csd_programs:
                self.issues.add("warning", "online_program_not_in_csd",
                                f"CICS program {p['name']} has no CSD PROGRAM definition",
                                path=p["path"], subject=p["id"])
        # DB2 batch programs need an attach: DSN RUN under IKJEFT01, or CAF/RRSAF calls.
        direct_exec = defaultdict(list)
        for ref in self.refs:
            if ref.type == "JCL_EXEC" and ref.resolution == "resolved" and ref.to_id:
                direct_exec[ref.to_id].append(ref)
        attach_calls = {"DSNALI", "DSNRLI", "DSNHLI", "DSNHLI2", "DSNELI"}
        for p in self.programs:
            if not p["has_sql"] or p["has_cics"] or p["id"] not in direct_exec:
                continue
            calls = {r.target for r in self.refs if r.from_id == p["id"] and r.type == "STATIC_CALL"}
            if calls & attach_calls:
                continue
            for ref in direct_exec[p["id"]]:
                self.issues.add("warning", "db2_program_without_attach",
                                f"{p['name']} contains SQL but {ref.from_id.split(':', 1)[1]} runs it with "
                                "EXEC PGM= instead of IKJEFT01/DSN RUN, and it makes no CAF/RRSAF call",
                                path=ref.path, line=ref.line, subject=p["id"],
                                details={"job": ref.from_id, "step": ref.details.get("step")})
        for name, r in sorted(csd_programs.items()):
            if not self._find_program(name):
                self.issues.add("warning", "csd_program_without_source",
                                f"CSD defines PROGRAM {name} but its source is not in the scanned source",
                                path=r["path"], line=r["line"], subject=r["id"])
        # conflicting VSAM definitions for the same cluster
        by_name: dict[str, list[dict]] = defaultdict(list)
        for d in self.vsam:
            if d["kind"] == "CLUSTER":
                by_name[d["name"]].append(d)
        for name, defs in sorted(by_name.items()):
            shapes = {(d["organization"], d["key_length"], d["key_offset"], d["record_size_max"]) for d in defs}
            if len(shapes) > 1:
                self.issues.add("warning", "conflicting_vsam_definitions",
                                f"{len(defs)} definitions of {name} disagree on keys or record size",
                                path=defs[0]["path"], line=defs[0]["line"], subject=f"dataset:{name}",
                                details={"definitions": [{k: d[k] for k in ("path", "line", "organization",
                                         "key_length", "key_offset", "record_size_max")} for d in defs]})

    # ------------------------------------------------------------- assemble
    def _assemble(self) -> dict[str, Any]:
        refs_out = []
        for k, ref in enumerate(sorted(self.refs, key=lambda r: (r.path, r.line, r.type, r.target or "")), start=1):
            item = {"id": f"R{k:05d}", "type": ref.type, "from": ref.from_id, "to": ref.to_id,
                    "target": ref.target, "resolution": ref.resolution, "path": ref.path, "line": ref.line}
            if ref.division:
                item["division"] = ref.division
            if ref.details:
                item["details"] = ref.details
            refs_out.append(item)

        graph = self._graph(refs_out)
        copybook_map = {c["name"]: c["used_by"] for c in sorted(self.copybooks, key=lambda c: c["name"])}
        issues = [i.to_dict() for i in self.issues.sorted()]
        artifact: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "meta": {
                "generator": "harness.inventory",
                "generator_version": __version__,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds") if self.opt.timestamp else None,
                "root": str(self.root),
                "scope": {"mode": "full", "entry_points": []},
                "exclude_dirs": sorted(self.opt.exclude_dirs),
                "exclude_globs": self.opt.exclude_globs,
            },
            "stats": {},
            "files": sorted(self.files, key=lambda f: f["path"]),
            "programs": sorted(self.programs, key=lambda p: (p["name"], p["path"])),
            "copybooks": sorted(self.copybooks, key=lambda c: (c["name"], c["path"])),
            "jcl": {"jobs": sorted(self.jobs, key=lambda j: (j["name"], j["path"])),
                    "procs": sorted(self.procs, key=lambda p: (p["name"], p["path"]))},
            "cics": {"resources": sorted(self.csd, key=lambda r: (r["type"], r["name"]))},
            "bms": {"mapsets": sorted(self.mapsets, key=lambda m: m["name"])},
            "db2": {"tables": sorted(self.tables, key=lambda t: t["name"]),
                    "views": sorted(self.views, key=lambda v: v["name"]),
                    "indexes": sorted(self.indexes, key=lambda i: (i["table"], i["name"])),
                    "objects": sorted(self.db_objects, key=lambda o: (o["kind"], o["name"]))},
            "vsam": {"definitions": sorted(self.vsam, key=lambda d: (d["name"], d["path"], d["line"]))},
            "references": refs_out,
            "graph": graph,
            "copybook_map": copybook_map,
            "issues": issues,
        }
        artifact["stats"] = compute_stats(artifact)
        return artifact

    def _graph(self, refs: list[dict]) -> dict:
        nodes: dict[str, dict] = {}

        def node(nid: str, kind: str, name: str, status: str = "defined", path: str | None = None) -> None:
            if nid not in nodes:
                nodes[nid] = {"id": nid, "kind": kind, "name": name, "status": status}
                if path:
                    nodes[nid]["path"] = path

        for p in self.programs:
            node(p["id"], "program", p["name"], path=p["path"])
        for c in self.copybooks:
            node(c["id"], "copybook", c["name"], path=c["path"])
        for j in self.jobs:
            node(j["id"], "job", j["name"], path=j["path"])
        for p in self.procs:
            node(p["id"], "proc", p["name"], path=p["path"])
        for r in self.csd:
            if r["type"] == "TRANSACTION":
                node(r["id"], "transaction", r["name"], path=r["path"])
            elif r["type"] == "FILE":
                node(f"cics_file:{r['name']}", "cics_file", r["name"], path=r["path"])
        for ms in self.mapsets:
            node(ms["id"], "mapset", ms["name"], path=ms["path"])
            for m in ms["maps"]:
                node(m["id"], "map", f"{ms['name']}.{m['name']}", path=ms["path"])
        for t in self.tables:
            node(t["id"], "table", t["name"], path=t["path"])
        for v in self.views:
            node(v["id"], "view", v["name"], path=v["path"])
        for d in self.vsam:
            node(d["id"], "dataset", d["name"], path=d["path"])

        edges: dict[tuple[str, str, str], dict] = {}
        for r in refs:
            if not r["to"]:
                continue
            kind, _, name = r["to"].partition(":")
            status = {"resolved": "defined", "ambiguous": "defined"}.get(r["resolution"], r["resolution"])
            if r["to"] not in nodes:
                node(r["to"], kind, name, "missing" if status == "unresolved" else status)
            if r["from"] not in nodes:
                fkind, _, fname = r["from"].partition(":")
                node(r["from"], fkind, fname)
            key = (r["from"], r["to"], r["type"])
            edge = edges.get(key)
            if edge is None:
                edge = edges[key] = {"from": r["from"], "to": r["to"], "type": r["type"],
                                     "resolution": r["resolution"], "count": 0, "references": []}
            edge["count"] += 1
            edge["references"].append(r["id"])
        return {
            "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
            "edges": sorted(edges.values(), key=lambda e: (e["from"], e["type"], e["to"])),
        }


def _dcb(params: dict) -> dict:
    """RECFM / LRECL from DCB=(...) or the stand-alone keywords."""
    out: dict = {}
    dcb = params.get("DCB", "")
    _, inner = jcl_mod.split_operands(dcb[1:-1] if dcb.startswith("(") else dcb)
    for key in ("RECFM", "LRECL"):
        value = params.get(key) or inner.get(key)
        if value:
            out[key.lower()] = int(value) if key == "LRECL" and value.isdigit() else value
    return out


def _target_kind(ref_type: str) -> str:
    return {"CSD_FILE": "dataset", "JCL_DD": "dataset"}.get(ref_type, "unknown")


def compute_stats(artifact: dict[str, Any]) -> dict[str, Any]:
    """All counts derive from the arrays in the artifact, never from the scan itself."""
    programs = artifact["programs"]
    refs = artifact["references"]
    edges = artifact["graph"]["edges"]
    steps = [s for j in artifact["jcl"]["jobs"] for s in j["steps"]]
    return {
        "files": len(artifact["files"]),
        "files_by_kind": dict(sorted(Counter(f["kind"] for f in artifact["files"]).items())),
        "bytes": sum(f["size_bytes"] for f in artifact["files"]),
        "lines": sum(f["lines"] for f in artifact["files"]),
        "programs": len(programs),
        "programs_by_kind": dict(sorted(Counter(p["kind"] for p in programs).items())),
        "programs_with_sql": sum(1 for p in programs if p["has_sql"]),
        "programs_with_cics": sum(1 for p in programs if p["has_cics"]),
        "entry_point_programs": sum(1 for p in programs if p["entry_point"]),
        "copybooks": len(artifact["copybooks"]),
        "copybooks_unused": sum(1 for c in artifact["copybooks"] if not c["used_by"]),
        "jcl_jobs": len(artifact["jcl"]["jobs"]),
        "jcl_steps": len(steps),
        "jcl_utility_steps": sum(1 for s in steps if s["utility"]),
        "jcl_procs": len(artifact["jcl"]["procs"]),
        "cics_resources_by_type": dict(sorted(Counter(r["type"] for r in artifact["cics"]["resources"]).items())),
        "bms_mapsets": len(artifact["bms"]["mapsets"]),
        "bms_maps": sum(len(m["maps"]) for m in artifact["bms"]["mapsets"]),
        "db2_tables": len(artifact["db2"]["tables"]),
        "db2_views": len(artifact["db2"]["views"]),
        "vsam_definitions": len(artifact["vsam"]["definitions"]),
        "references": len(refs),
        "references_by_type": dict(sorted(Counter(r["type"] for r in refs).items())),
        "references_by_resolution": dict(sorted(Counter(r["resolution"] for r in refs).items())),
        "graph_nodes": len(artifact["graph"]["nodes"]),
        "graph_edges": len(edges),
        "graph_edges_by_type": dict(sorted(Counter(e["type"] for e in edges).items())),
        "issues": len(artifact["issues"]),
        "issues_by_severity": dict(sorted(Counter(i["severity"] for i in artifact["issues"]).items())),
    }
