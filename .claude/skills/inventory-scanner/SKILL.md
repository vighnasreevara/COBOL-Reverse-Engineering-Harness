---
name: inventory-scanner
description: >
  Runs the deterministic inventory scanner (python -m harness inventory) over a
  COBOL system and validates its output. The scanner catalogues programs,
  copybooks, JCL, CICS CSD, BMS maps, DB2 DDL and IDCAMS/VSAM definitions,
  resolves every COPY / CALL / CICS / SQL / JCL reference with file:line
  provenance, links program files to JCL datasets, and writes
  output/inventory/inventory_artifact.json. Used by the 1_inventory agent.
---

# Skill: inventory scanner

All counting, parsing and resolving is done by tested code in `harness/`.
Your job is to run it, check it succeeded, and never alter its output.

## 1. Find Python

Use the project's virtual environment if it exists:

| OS | Command |
|---|---|
| Windows | `.venv\Scripts\python.exe` |
| macOS / Linux | `.venv/bin/python` |

If there is no `.venv`, create it once:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"        # Windows: .venv\Scripts\python.exe
```

The scanner itself has no third-party dependencies; `[dev]` adds pytest and
jsonschema (schema validation is stricter when jsonschema is installed).

## 2. Run the scanner

```bash
<python> -m harness inventory --root <ROOT> --out <OUT_DIR> [options]
```

| Option | Meaning |
|---|---|
| `--root` | Folder holding the COBOL system (required) |
| `--out` | Output folder, default `output/inventory` |
| `--entry [KIND:]NAME` | Keep only what is reachable from a job, transaction, program or proc. Repeatable. Kinds: `job`, `transaction`, `program`, `proc` |
| `--exclude GLOB` | Skip files whose path (relative to root) matches, e.g. `templates/*`. Repeatable |
| `--exclude-dir NAME` | Skip directories with this name |
| `--no-timestamp` | Omit `generated_at` so reruns are byte-identical |

Exit codes:

| Code | Meaning | What to do |
|---|---|---|
| 0 | Artifact written and valid | Continue |
| 1 | Artifact written but failed validation | Stop. Report the listed problems. Do not edit the JSON |
| 2 | Bad arguments (root missing, unknown entry point) | Fix the arguments or ask the user |

Validation is built in. To re-check an existing artifact:

```bash
<python> -m harness validate-inventory <OUT_DIR>/inventory_artifact.json
```

## 3. What the artifact contains

Schema: `schemas/inventory_artifact.schema.json`. Top-level sections:

| Section | Content |
|---|---|
| `stats` | Every count, computed from the arrays below (never estimate a number yourself) |
| `files` | Every file: kind, size, lines, sha256, encoding, line ending |
| `programs` | PROGRAM-ID, kind (`batch`, `subroutine`, `online`, `unknown`), entry point flag, `executed_by` (jobs, transactions), `called_by`, FILE-CONTROL entries, `datasets` (DD name to DSN per job step) |
| `copybooks` | content kind (`data`, `procedure`, `mixed`), `used_by` |
| `jcl` | jobs and procs with ordered steps and DD statements |
| `cics` | CSD resources (transactions, programs, mapsets, files, DB2 entries) |
| `bms` | mapsets, maps, named fields |
| `db2` | tables (columns, primary and foreign keys, comments), views, indexes, other objects |
| `vsam` | IDCAMS cluster definitions |
| `references` | Every reference occurrence with `type`, `from`, `to`, `resolution`, `path`, `line` |
| `graph` | Unique nodes and edges (edge `references` list the occurrence ids) |
| `copybook_map` | copybook to the members that copy it |
| `issues` | Findings with severity, code, message, path, line, subject |

`resolution` values: `resolved`, `unresolved` (target not in the scanned
source), `ambiguous`, `system` (runtime or utility, e.g. CEE*, IDCAMS, SQLCA),
`external` (catalogued PROC), `dynamic` (target held in a variable),
`temporary` (DB2 session table, && dataset), `undefined` (table with no DDL).

## 4. Known limits of the scanner

Report these honestly when they matter for the system at hand:

- Dynamic CALL / LINK targets are resolved only through literal `VALUE` or
  `MOVE 'NAME' TO var` assignments in the same member.
- SQL built at run time (dynamic SQL in literals) is not parsed for tables.
- JCL symbolic parameters (`&VAR`) are not substituted; catalogued PROCs not in
  the scanned folder are recorded as `external`.
- Copybook nesting is resolved one hop per reference; COPY REPLACING is
  recorded but not applied (the Data stage applies it).
