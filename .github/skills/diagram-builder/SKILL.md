---
name: diagram-builder
description: >
  Runs the deterministic diagram stage (python -m harness diagrams): generates
  Mermaid diagrams only from verified artifacts (system context, data lineage,
  ERD from DDL keys, one flow per batch job, one sequence diagram per CICS
  transaction, and per program a paragraph-structure graph and a main-flow
  chart), lints every diagram, and writes Markdown galleries. Explains how to
  add checked captions. Used by the 6_diagram agent.
---

# Skill: diagram builder

## 1. Run

```bash
<python> -m harness diagrams --out output/diagram
```

(inputs default to the stage 1 to 4 artifacts under `output/`). Writes
`diagrams/*.mmd`, `diagrams_artifact.json` (index with node/edge counts and
lint results), `diagrams.md` (system diagrams) and `diagrams_programs.md`
(program diagrams). Exit code 1 if any diagram fails the lint or the
annotations file fails its check.

## 2. Diagrams

| Id | Built from | Shows |
|---|---|---|
| `diagram:system_context` | inventory graph | jobs and transactions, the programs they start, CALL / LINK / XCTL between programs; dashed = unresolved target |
| `diagram:data_lineage` | data flows | which programs read and write which datasets, tables, CICS files |
| `diagram:erd` | DB2 DDL, VSAM-keyed records | tables with PK/FK from DDL, foreign keys, keyed files |
| `diagram:job_<JOB>` | JCL + data flows | steps, programs, datasets with DD names and direction |
| `diagram:transaction_<TRAN>` | logic outline | CICS conversation: maps sent/received, LINKs (two levels deep), `opt` for conditions, `loop` for loops |
| `diagram:program_<P>_paragraphs` | parser cfg | PERFORM / GO TO / CICS HANDLE edges; never-run paragraphs grey, undefined targets red |
| `diagram:program_<P>_flow` | statement tree | the entry paragraph as a flowchart (decisions, loops, performs, ends); at most 70 nodes |

The lint checks the diagram type line, balanced quotes, that every edge uses
declared nodes and sequence blocks are closed. It does not run Mermaid itself;
open the galleries in VS Code's Markdown preview or on GitHub to see them
rendered.

## 3. Captions

`output/diagram/diagram_annotations.json`: one `caption` per system-level
diagram (and for any program diagram you discuss), 1 to 3 sentences saying
what the reader should notice. Evidence: the diagram id plus the artifact ids
or source lines behind the point you make. Check with:

```bash
<python> -m harness check-annotations output/diagram/diagram_annotations.json \
    --artifact output/diagram/diagrams_artifact.json
<python> -m harness diagrams       # re-render galleries with captions
```
