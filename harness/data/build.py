"""Stage 3: data layouts, data model and data flows from the inventory and parser outputs."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness import __version__
from harness.common.issues import IssueLog
from harness.data.layout import build_records, flatten, signature
from harness.data.sqlmap import check_sql, column_type
from harness.parser.procedure import iter_statements

SCHEMA_VERSION = "1.0"
_OPEN_MODES = {"INPUT": "read", "OUTPUT": "write", "EXTEND": "write", "I-O": "update"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_data(inventory_path: Path, parser_path: Path, out_dir: Path, timestamp: bool = True) -> dict[str, Any]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    parser = json.loads(parser_path.read_text(encoding="utf-8"))
    parser_dir = parser_path.parent
    log = IssueLog()
    copybook_member = {c["path"]: c["member"] for c in inventory["copybooks"]}
    inv_programs = {p["id"]: p for p in inventory["programs"]}
    tables = {t["name"]: t for t in inventory["db2"]["tables"]}
    vsam = {d["name"]: d for d in inventory["vsam"]["definitions"] if d["kind"] == "CLUSTER"}
    cics_files = {r["name"]: r for r in inventory["cics"]["resources"] if r["type"] == "FILE"}

    records: dict[str, dict] = {}                 # record id -> record
    signatures: dict[str, list[str]] = defaultdict(list)   # base id -> layout variants
    programs_out: list[dict] = []
    flows: list[dict] = []
    host_pairs: list[dict] = []
    seen_problem: set[tuple] = set()
    datasets: dict[str, dict] = {}
    table_use: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    def problem(p: dict, subject: str) -> None:
        key = (p["code"], p["file"], p["line"], p["message"])
        if key in seen_problem:
            return
        seen_problem.add(key)
        severity = "info" if p["code"] in ("empty_record",) else "warning"
        log.add(severity, p["code"], p["message"], path=p["file"], line=p["line"], subject=subject)

    for entry in parser["programs"]:
        if entry["status"] != "parsed":
            continue
        doc = json.loads((parser_dir / entry["output"]).read_text(encoding="utf-8"))
        pid = doc["program"]["id"]
        name = doc["program"]["name"]
        inv = inv_programs.get(pid, {})
        built, problems = build_records(doc["data"]["entries"])
        for p in problems:
            problem(p, pid)

        prog_records: list[dict] = []
        field_index: dict[str, dict] = {}
        record_by_name: dict[str, dict] = {}
        for item, ctx in built:
            member = copybook_member.get(item.file)
            base = f"{member}/{item.name or 'FILLER'}" if member else f"{name}/{item.name or 'FILLER'}"
            sig = signature(item)
            variants = signatures[base]
            if sig not in variants:
                variants.append(sig)
            rid = base if variants.index(sig) == 0 else f"{base}~{variants.index(sig) + 1}"
            fields = flatten(item)
            if rid not in records:
                records[rid] = {
                    "id": f"record:{rid}", "name": item.name, "level": item.level,
                    "source": "copybook" if member else "program", "copybook": member,
                    "file": item.file, "line": item.line, "size": item.size, "fields": fields,
                    "renames": [{"name": r.name, "line": r.line, "file": r.file, **r.clauses.get("renames", {})}
                                for r in item.renames],
                    "used_by": [],
                }
            records[rid]["used_by"].append({"program": pid, "section": ctx["section"], "fd": ctx["fd"]})
            ref = {"record": f"record:{rid}", "name": item.name, "section": ctx["section"], "fd": ctx["fd"],
                   "size": item.size}
            prog_records.append(ref)
            if item.name:
                record_by_name.setdefault(item.name, ref)
            for f in fields:
                if f["name"]:
                    field_index.setdefault(f["name"], {**f, "record": f"record:{rid}"})

        # files: FD -> SELECT -> DD -> dataset
        statements = [s for para in doc["procedure"]["paragraphs"] for s in iter_statements(para["statements"])]
        modes = _open_modes(statements)
        fd_records = defaultdict(list)
        for r in prog_records:
            if r["fd"]:
                fd_records[r["fd"]].append(r)
        files_out = []
        for fc in doc["environment"]["file_control"]:
            recs = fd_records.get(fc["select"], [])
            size = max((r["size"] for r in recs if r["size"] is not None), default=None)
            links = [d for d in inv.get("datasets", []) if d["select"] == fc["select"]]
            file_rec = {"select": fc["select"], "ddname": fc["ddname"], "organization": fc["organization"],
                        "records": [r["record"] for r in recs], "record_size": size,
                        "access": sorted(modes.get(fc["select"], set())), "datasets": []}
            for link in links:
                dd = _dd(inventory, link)
                file_rec["datasets"].append({k: link.get(k) for k in ("job", "step", "ddname", "dsn", "disp")}
                                            | {"lrecl": dd.get("lrecl"), "recfm": dd.get("recfm")})
                if link.get("dsn"):
                    ds = datasets.setdefault(link["dsn"], {"name": link["dsn"], "records": set(), "programs": {},
                                                           "lrecl": set(), "organization": set()})
                    ds["records"].update(r["record"] for r in recs)
                    ds["programs"][pid] = sorted(modes.get(fc["select"], set())) or ["unknown"]
                    if dd.get("lrecl"):
                        ds["lrecl"].add(dd["lrecl"])
                    if fc["organization"]:
                        ds["organization"].add(fc["organization"])
                    for direction in modes.get(fc["select"], set()) or {"unknown"}:
                        flows.append({"program": pid, "entity": f"dataset:{link['dsn']}", "direction": direction,
                                      "via": f"{fc['select']} / DD {fc['ddname']} ({link['job']} {link['step']})",
                                      "file": doc["program"]["path"], "line": fc["line"]})
                    _check_lrecl(log, pid, doc["program"]["path"], fc, size, dd, link)
            if not links and fc["ddname"]:
                # No JCL binds this file: keep the flow against the DD name so lineage is not lost.
                for direction in modes.get(fc["select"], set()) or {"unknown"}:
                    flows.append({"program": pid, "entity": f"file:{fc['ddname']}", "direction": direction,
                                  "via": f"{fc['select']} / DD {fc['ddname']} (no JCL in scope)",
                                  "file": doc["program"]["path"], "line": fc["line"]})
            files_out.append(file_rec)

        # embedded SQL
        sql_statements = [s for s in statements if s["verb"] == "EXEC" and s.get("language") == "SQL"]
        program_fields = [{**f, "record": r["record"]} for item, r in zip([b[0] for b in built], prog_records)
                          for f in flatten(item)]
        pairs, sql_problems = check_sql(sql_statements, field_index, tables, program_fields)
        for p in sql_problems:
            problem(p, pid)
        for pr in pairs:
            host_pairs.append({"program": pid, **pr})
        sql_use: dict[str, set[str]] = defaultdict(set)
        sql_lines: dict[tuple[str, str], tuple[str, int]] = {}     # (table, direction) -> first statement
        for s in sql_statements:
            for t in s.get("tables", []):
                if t["operation"] in ("DECLARE", "DECLARE_TEMPORARY"):
                    continue
                op = {"SELECT": "read", "INSERT": "write", "UPDATE": "update", "DELETE": "delete"}.get(
                    t["operation"], t["operation"].lower())
                sql_use[t["table"]].add(op)
                sql_lines.setdefault((t["table"], op), (s["file"], s["line"]))
        for table, ops in sorted(sql_use.items()):
            entity = f"table:{table}"
            for op in sorted(ops):
                file, line = sql_lines[(table, op)]
                flows.append({"program": pid, "entity": entity, "direction": op, "via": "EXEC SQL",
                              "file": file, "line": line})
                table_use[table][op].add(pid)

        # CICS file access
        cics_out = []
        for s in statements:
            if s["verb"] != "EXEC" or s.get("language") != "CICS":
                continue
            opts = s.get("options", {})
            fname = (opts.get("FILE") or opts.get("DATASET") or "").strip("'\"")
            if not fname or s.get("command") not in ("READ", "WRITE", "REWRITE", "DELETE", "STARTBR", "READNEXT",
                                                      "READPREV"):
                continue
            into = (opts.get("INTO") or opts.get("FROM") or "").split("(")[0].strip() or None
            rec = record_by_name.get(into) if into else None
            size = rec["size"] if rec else (field_index.get(into, {}).get("size") if into else None)
            direction = "read" if s["command"] in ("READ", "STARTBR", "READNEXT", "READPREV") else "write"
            cics_out.append({"file": fname, "command": s["command"], "record": into, "record_size": size,
                             "line": s["line"], "path": s["file"]})
            flows.append({"program": pid, "entity": f"cics_file:{fname}", "direction": direction, "via": "EXEC CICS",
                          "file": s["file"], "line": s["line"]})
            csd = cics_files.get(fname)
            csd_size = int(csd["attributes"]["RECORDSIZE"]) if csd and csd["attributes"].get("RECORDSIZE", "").isdigit() else None
            if csd_size and size is not None and size != csd_size:
                log.add("warning", "cics_record_size_mismatch",
                        f"{into} is {size} bytes but CSD FILE {fname} has RECORDSIZE({csd_size})",
                        path=s["file"], line=s["line"], subject=pid)

        programs_out.append({"id": pid, "name": name, "records": prog_records, "files": files_out,
                             "sql_tables": sorted(sql_use), "cics_files": cics_out})

    # dataset entities with VSAM checks
    entities: list[dict] = []
    for dsn, ds in sorted(datasets.items()):
        definition = vsam.get(dsn)
        rec_ids = sorted(ds["records"])
        sizes = [records[r.split(":", 1)[1]]["size"] for r in rec_ids if records[r.split(":", 1)[1]]["size"] is not None]
        ent = {"id": f"dataset:{dsn}", "kind": "dataset", "name": dsn, "records": rec_ids,
               "record_sizes": sorted(set(sizes)), "lrecl": sorted(ds["lrecl"]),
               "organization": definition["organization"] if definition else (sorted(ds["organization"]) or [None])[0],
               "programs": ds["programs"]}
        if definition:
            ent["vsam"] = {k: definition[k] for k in ("key_length", "key_offset", "record_size_avg",
                                                      "record_size_max", "path", "line")}
            ent["key_fields"] = []
            for rid in rec_ids:
                rec = records[rid.split(":", 1)[1]]
                key = _key_field(rec, definition)
                if key:
                    ent["key_fields"].append({"record": rid, "field": key})
                elif definition["key_length"] is not None:
                    log.add("warning", "vsam_key_mismatch",
                            f"no field of {rec['name']} occupies the VSAM key of {dsn} "
                            f"(offset {definition['key_offset']}, length {definition['key_length']})",
                            path=rec["file"], line=rec["line"], subject=f"dataset:{dsn}",
                            details={"definition": f"{definition['path']}:{definition['line']}"})
                if rec["size"] and definition["record_size_max"] and rec["size"] > definition["record_size_max"]:
                    log.add("warning", "record_length_mismatch",
                            f"{rec['name']} is {rec['size']} bytes but {dsn} allows {definition['record_size_max']}",
                            path=rec["file"], line=rec["line"], subject=f"dataset:{dsn}")
                elif rec["size"] and definition["record_size_max"] and rec["size"] != definition["record_size_max"] \
                        and definition["record_size_avg"] == definition["record_size_max"]:
                    log.add("warning", "record_length_mismatch",
                            f"{rec['name']} is {rec['size']} bytes but fixed-length cluster {dsn} is "
                            f"{definition['record_size_max']} bytes",
                            path=rec["file"], line=rec["line"], subject=f"dataset:{dsn}")
        entities.append(ent)

    for name, t in sorted(tables.items()):
        entities.append({"id": f"table:{name}", "kind": "table", "name": name, "file": t["path"], "line": t["line"],
                         "columns": [{**c, **column_type(c["type"])} for c in t["columns"]],
                         "primary_key": t["primary_key"], "comment": t.get("comment"),
                         "programs": {op: sorted(p) for op, p in sorted(table_use.get(name, {}).items())}})
    for name, r in sorted(cics_files.items()):
        entities.append({"id": f"cics_file:{name}", "kind": "cics_file", "name": name, "file": r["path"],
                         "line": r["line"], "dataset": r["attributes"].get("DSNAME"),
                         "record_size": int(r["attributes"]["RECORDSIZE"]) if r["attributes"].get("RECORDSIZE", "").isdigit() else None})

    relationships = []
    for t in inventory["db2"]["tables"]:
        for fk in t["foreign_keys"]:
            relationships.append({"type": "foreign_key", "from": f"table:{t['name']}", "to": f"table:{fk['references']}",
                                  "columns": fk["columns"], "referenced_columns": fk["referenced_columns"],
                                  "evidence": {"file": t["path"], "line": t["line"]}, "confidence": "declared"})
    for name, r in sorted(cics_files.items()):
        dsn = r["attributes"].get("DSNAME")
        if dsn:
            relationships.append({"type": "cics_file_dataset", "from": f"cics_file:{name}", "to": f"dataset:{dsn}",
                                  "evidence": {"file": r["path"], "line": r["line"]}, "confidence": "declared"})

    # catalogues
    record_list = sorted(records.values(), key=lambda r: r["id"])
    for r in record_list:
        r["used_by"] = sorted({json.dumps(u, sort_keys=True) for u in r["used_by"]})
        r["used_by"] = [json.loads(u) for u in r["used_by"]]
    fields = []
    conditions = []
    for r in record_list:
        for f in r["fields"]:
            fid = f"field:{r['id'][7:]}:{f['qualified_name']}"
            f["id"] = fid
            fields.append({"id": fid, "record": r["id"], **{k: f[k] for k in (
                "name", "qualified_name", "level", "category", "picture", "usage", "size", "offset", "digits",
                "scale", "signed", "file", "line")}})
            for c in f["conditions"]:
                conditions.append({"id": f"condition:{r['id'][7:]}:{f['qualified_name']}:{c['name']}",
                                   "name": c["name"], "field": fid, "record": r["id"], "values": c["values"],
                                   "file": c["file"], "line": c["line"]})

    entity_ids = {e["id"] for e in entities}
    for fl in flows:
        if fl["entity"] not in entity_ids:
            kind, _, nm = fl["entity"].partition(":")
            entities.append({"id": fl["entity"], "kind": kind, "name": nm, "undefined": True})
            entity_ids.add(fl["entity"])

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "generator": "harness.data",
            "generator_version": __version__,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds") if timestamp else None,
            "inventory": str(inventory_path), "inventory_sha256": _sha(inventory_path),
            "parser": str(parser_path), "parser_sha256": _sha(parser_path),
            "root": inventory["meta"]["root"],
        },
        "stats": {},
        "records": record_list,
        "fields": fields,
        "conditions": conditions,
        "programs": sorted(programs_out, key=lambda p: p["name"]),
        "entities": sorted(entities, key=lambda e: e["id"]),
        "relationships": relationships,
        "data_flows": sorted(flows, key=lambda f: (f["program"], f["entity"], f["direction"])),
        "host_variables": host_pairs,
        "issues": [i.to_dict() for i in log.sorted()],
    }
    artifact["stats"] = compute_stats(artifact)
    out_dir.mkdir(parents=True, exist_ok=True)
    layouts = out_dir / "data_layouts"
    layouts.mkdir(exist_ok=True)
    for r in record_list:                       # one browsable file per record layout
        safe = r["id"].split(":", 1)[1].replace("/", "__").replace("~", "-")
        (layouts / f"{safe}.json").write_text(json.dumps(r, indent=1, ensure_ascii=False) + "\n",
                                              encoding="utf-8")
    (out_dir / "data_artifact.json").write_text(json.dumps(artifact, indent=1, ensure_ascii=False) + "\n",
                                                encoding="utf-8")
    return artifact


def _open_modes(statements: list[dict]) -> dict[str, set[str]]:
    modes: dict[str, set[str]] = defaultdict(set)
    for s in statements:
        if s["verb"] != "OPEN":
            continue
        mode = None
        for word in s["text"].split()[1:]:
            if word in _OPEN_MODES:
                mode = _OPEN_MODES[word]
            elif mode and word not in ("WITH", "NO", "REWIND", "LOCK", "REVERSED"):
                modes[word].add(mode)
    return modes


def _dd(inventory: dict, link: dict) -> dict:
    for job in inventory["jcl"]["jobs"] + inventory["jcl"]["procs"]:
        if job["name"] != link["job"]:
            continue
        for step in job["steps"]:
            if step["name"] != link["step"]:
                continue
            for dd in step["dds"]:
                if dd["name"] == link["ddname"]:
                    return dd
    return {}


def _check_lrecl(log: IssueLog, pid: str, path: str, fc: dict, size: int | None, dd: dict, link: dict) -> None:
    lrecl, recfm = dd.get("lrecl"), (dd.get("recfm") or "").upper()
    if not lrecl or not size:
        return
    variable = recfm.startswith("V")
    limit = lrecl - 4 if variable else lrecl
    if (variable and size > limit) or (not variable and size != limit):
        log.add("warning", "record_length_mismatch",
                f"{fc['select']} records are {size} bytes but DD {link['ddname']} in {link['job']} has "
                f"RECFM={recfm or '?'},LRECL={lrecl}", path=path, line=fc["line"], subject=pid,
                details={"job": link["job"], "step": link["step"]})


def _key_field(rec: dict, definition: dict) -> str | None:
    off, length = definition["key_offset"], definition["key_length"]
    if off is None or length is None:
        return None
    for f in rec["fields"]:
        if f["offset"] == off and f["size"] == length:
            return f["qualified_name"]
    return None


def compute_stats(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "records": len(a["records"]),
        "records_by_source": dict(sorted(Counter(r["source"] for r in a["records"]).items())),
        "fields": len(a["fields"]),
        "elementary_fields": sum(1 for f in a["fields"] if f["category"] != "group"),
        "conditions": len(a["conditions"]),
        "programs": len(a["programs"]),
        "entities_by_kind": dict(sorted(Counter(e["kind"] for e in a["entities"]).items())),
        "relationships": len(a["relationships"]),
        "data_flows": len(a["data_flows"]),
        "host_variable_pairs": len(a["host_variables"]),
        "issues": len(a["issues"]),
        "issues_by_severity": dict(sorted(Counter(i["severity"] for i in a["issues"]).items())),
        "issues_by_code": dict(sorted(Counter(i["code"] for i in a["issues"]).items())),
    }

