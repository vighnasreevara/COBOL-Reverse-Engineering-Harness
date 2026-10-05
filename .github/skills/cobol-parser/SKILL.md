---
name: cobol-parser
description: >
  Runs the deterministic COBOL parser (python -m harness parse) over every
  program in inventory_artifact.json. Expands copybooks (with REPLACING),
  parses DATA DIVISION entries and clauses, builds a statement tree for the
  PROCEDURE DIVISION and a paragraph control-flow graph, and reports dead
  paragraphs, undefined PERFORM/GO TO targets, PERFORM recursion and undefined
  data names. Writes output/parser/parser_artifact.json and one JSON file per
  program. Used by the 2_parser agent.
---

# Skill: COBOL parser

Parsing is done by tested code in `harness/parser/`. Run it, check it
succeeded, and never edit its output.

## 1. Run

Python: `.venv\Scripts\python.exe` (Windows) or `.venv/bin/python`.

```bash
<python> -m harness parse --inventory output/inventory/inventory_artifact.json --out output/parser
```

| Option | Meaning |
|---|---|
| `--inventory` | Stage 1 artifact (default `output/inventory/inventory_artifact.json`) |
| `--out` | Output folder (default `output/parser`) |
| `--program NAME` | Parse only this program; repeatable |
| `--no-timestamp` | Reproducible output |

Exit codes: 0 valid, 1 written but failed validation (stop and report), 2
missing inventory (run stage 1 first). Re-check later with
`<python> -m harness validate-parser output/parser/parser_artifact.json`.

## 2. Outputs

`parser_artifact.json` (schema `schemas/parser_artifact.schema.json`):
per-program status (`parsed`, `skipped`, `failed`), metrics, all issues
with `subject` = program id. Stats are recomputed and checked on every run.

`raw_structure/<NAME>.json` (schema `schemas/parser_program.schema.json`):

| Section | Content |
|---|---|
| `sources` | Every file whose text is in the program (program + expanded copybooks) |
| `copy_expansions` | Each COPY / SQL INCLUDE with status `expanded`, `missing`, `system`, `recursive`, `too_deep`, depth, REPLACING pairs |
| `data.files` | FD/SD entries and their 01 records |
| `data.entries` | Every data entry: level, name (null = FILLER), section, owning FD, parsed clauses (`picture`, `usage`, `values`, `occurs`, `redefines`, `sign`, `sync`, `justified`, `renames` ...), file and line |
| `data.misplaced_procedure_names` | Paragraph names found in the DATA DIVISION (procedure copybooks copied into storage) |
| `procedure.paragraphs` | Paragraphs in source order; `(mainline)` holds statements before the first paragraph. Each has a statement tree |
| `cfg` | Paragraph nodes with `reachable`/`terminal`, edges (`PERFORM`, `GO TO`, `FALL THROUGH`, `CICS HANDLE`, `ALTER`, SORT procedures) with `range` for THRU, `dead_paragraphs`, `recursive_cycles`, `undefined_targets` |

Statement nodes: `verb`, `file`, `line`, `end_line`, `text`, `reads`,
`writes`, and by type: IF `condition`/`then`/`else`; EVALUATE `subjects`/
`branches` (grouped WHENs, `other`); PERFORM `target`/`thru`/`inline`/`body`/
`loop` (`none`, `times`, `until`, `varying`, `test`); GO TO `targets`/
`depending_on`; CALL `program` or `program_variable`, `using`; EXEC
`language`, `command`, `tables` (SQL), `options` and `handlers` (CICS);
conditional `phrases` (AT END, INVALID KEY, ON SIZE ERROR, ON EXCEPTION ...).

## 3. Issue codes

| Code | Meaning |
|---|---|
| `undefined_procedure` | PERFORM / GO TO / HANDLE target is not a paragraph or section (message says when it exists in a copybook copied into the DATA DIVISION) |
| `undefined_data_name` | A name read or written by a statement is not defined in the program or its expanded copybooks |
| `unknown_intrinsic_function` | `FUNCTION x` where x is not an IBM intrinsic function |
| `dead_paragraph` | Paragraph not reachable from the entry point (PERFORM THRU ranges and fall-through are honoured) |
| `recursive_perform` | Paragraphs that PERFORM themselves directly or indirectly |
| `procedure_code_in_data_division` | Procedure text from a copybook copied into storage; it never executes |
| `unreachable_statement` | Statement after GOBACK / STOP RUN / GO TO / CICS RETURN in the same paragraph |
| `unrecognised_statement`, `unexpected_token`, `inline_perform_without_end_perform`, `paragraph_name_without_period` | Syntax the parser could not place; usually a source defect, possibly a parser gap |

## 4. Limits

- One program per file (nested programs are read as part of the outer one).
- Undefined-name checks ignore names starting with EIB, DFH or SQL (supplied by
  the CICS translator and DB2 precompiler).
- Qualified references (`A OF B`) are reduced to the leading name.
- Copybooks are expanded from the scanned folder only; missing members are
  reported, not guessed.
