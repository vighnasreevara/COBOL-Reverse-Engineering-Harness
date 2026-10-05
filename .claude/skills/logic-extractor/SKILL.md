---
name: logic-extractor
description: >
  Runs the deterministic logic stage (python -m harness logic): for every
  parsed program builds an execution outline from the entry point, a catalogue
  of branches with 88-level meanings and outcomes, loop termination analysis,
  file / SQL / CICS / CALL operations, per-paragraph data flow, complexity and
  line-traceable pseudocode. Writes output/logic/logic_artifact.json and
  output/logic/program_logic/<NAME>.json. Also explains how to write checked program
  summaries. Used by the 4_logic agent.
---

# Skill: logic extractor

## 1. Run

Python: `.venv\Scripts\python.exe` (Windows) or `.venv/bin/python`.

```bash
<python> -m harness logic --inventory output/inventory/inventory_artifact.json \
    --parser output/parser/parser_artifact.json --data output/data/data_artifact.json --out output/logic
```

Exit codes: 0 valid, 1 failed validation (stop and report), 2 missing input.
Re-check with `<python> -m harness validate-logic output/logic/logic_artifact.json`.

## 2. Program file contents (`program_logic/<NAME>.json`)

| Section | Content |
|---|---|
| `context` | Jobs/transactions that start it, callers, USING parameters, data flows and files (from stage 3) |
| `outline` | Execution tree from the entry paragraph: `perform` (with `loop`, `thru`, `children` or `expanded_elsewhere`), `loop` (inline), `call`, `cics link/xctl/return/send/receive`, `goto`, `end`; each with `when` = the conditions under which it happens |
| `paragraphs` | Name, location, reachable, complexity, `performed_by`, and `pseudocode` lines (`file`, `line`, `depth`, `text`) |
| `branches` | `branch:<PGM>:B<n>`: kind (IF, WHEN, SEARCH WHEN, PHRASE such as AT END / INVALID KEY), condition, fields tested, 88-level meanings, outcomes with key actions |
| `loops` | `loop:<PGM>:L<n>`: type (until, varying, times), condition, fields it tests, fields changed inside the loop, `termination` (counted, condition_updated_in_loop, potential_infinite, unknown) |
| `io` | File verbs, SQL statements (tables, host variables), CICS commands, CALLs |
| `data_flow` | Fields read and written per paragraph |

Pseudocode conventions: `DO P [THRU Q]` = PERFORM; `WHILE NOT (c): DO P` =
PERFORM UNTIL (test before); `REPEAT UNTIL c` = WITH TEST AFTER; `SELECT CASE`
= EVALUATE; `SET x = y` = MOVE/COMPUTE; `RETURN TO CALLER` = GOBACK; 88-level
names are explained in square brackets.

Loop analysis counts a field as changed when the loop body, or any paragraph it
performs (transitively), writes the field, its group, a subfield, or sets one of
its 88-level names; every EXEC SQL changes SQLCODE.

## 3. Program summaries (written by the agent)

Write `output/logic/logic_annotations.json`:

```json
{
  "stage": "logic",
  "items": [
    {
      "target": "program:ORDLOAD",
      "kind": "summary",
      "text": "Loads the day's orders from the ORDERS file into DB2 table ORDERS_T ...",
      "evidence": [{"path": "programs/ORDLOAD.cbl", "line": 120},
                   {"id": "loop:ORDLOAD:L1"}, {"id": "branch:ORDLOAD:B3"}],
      "confidence": "high"
    }
  ]
}
```

A good summary (4 to 8 sentences) says: how the program is started; its main
loop or dispatch; what it reads and writes; the decisions that matter
(validation, routing, error handling) citing branch ids; how it ends; and
anything surprising the facts show (dead paragraphs, undefined targets,
loops that may not end, order-of-operations problems). Write only what the
outline, branches, pseudocode and source support.

Check:

```bash
<python> -m harness check-annotations output/logic/logic_annotations.json \
    --artifact output/logic --artifact output/data/data_artifact.json
```
