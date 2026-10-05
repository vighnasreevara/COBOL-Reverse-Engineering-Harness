# COBOL Reverse Engineering Harness

A multi-agent harness for reverse engineering COBOL applications into structured system knowledge, data models, execution logic, business rules, diagrams, and traceable Business Requirements Documents (BRDs).

> **Status:** All eight stages are implemented, tested and run end to end with one command.

## Why

Many critical business systems still run on COBOL, often with little up-to-date documentation and few people who understand them end to end. Before a system can be modernized, migrated, or safely changed, teams need to recover *what it does and why*. That knowledge is buried in programs, copybooks, JCL, and data definitions.

This harness coordinates specialized agents to read a COBOL codebase and produce documentation that both engineers and business stakeholders can use, with every conclusion traceable back to the source that supports it.

## Goals

The harness aims to produce:

| Output | Description |
| --- | --- |
| **System knowledge** | Inventory of programs, copybooks, jobs, and files, and how they depend on each other |
| **Data models** | Record layouts and data structures recovered from `DATA DIVISION` definitions and copybooks |
| **Execution logic** | Control flow, paragraph/section call graphs, and batch job sequencing |
| **Business rules** | Conditions, calculations, and validations extracted from procedural code and stated in plain language |
| **Diagrams** | Visual views of program structure, data flow, and job flow |
| **BRDs** | Business Requirements Documents in which each requirement links back to the code it was derived from |

## Approach

1. **Ingest:** collect COBOL sources, copybooks, and related artifacts such as JCL.
2. **Analyze:** agents with distinct responsibilities (structure, data, logic, rules) each examine the codebase.
3. **Synthesize:** combine agent findings into consistent, cross-referenced knowledge.
4. **Document:** generate models, diagrams, and BRDs with source traceability.

## Traceability

Every generated artifact should answer *"where did this come from?"* Business rules and requirements carry references to the specific programs, paragraphs, and lines they were derived from, so reviewers can verify them against the original code.

## Design principle

Agents orchestrate, verify and explain; code computes. Everything that can be determined exactly
(scanning, parsing, resolving references, counting) is done by tested Python in `harness/`, and
every artifact is validated against a JSON schema and its own data before the next stage reads it.

## Getting started

Requires Python 3.10+.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"     # macOS/Linux: .venv/bin/python
```

Put the COBOL system under `src/` (or point `--root` anywhere), then run every stage:

```bash
.venv/Scripts/python.exe -m harness run-all --root src --system "My System"
```

Each stage writes its artifact under `output/`, validates it against its JSON schema and its own
data, and the run stops at the first stage that fails. Stages can also be run one at a time:
`inventory`, `parse`, `data`, `logic`, `rules`, `diagrams`, `synthesize`, `graph`
(`python -m harness <stage> --help`). Useful inventory options: `--entry job:NAME` /
`--entry transaction:NAME` / `--entry program:NAME` to analyse only what one entry point reaches,
`--exclude "templates/*"` to skip files, and `--no-timestamp` for byte-identical reruns. Run the tests
with `.venv/Scripts/python.exe -m pytest`.

The agents then add what code cannot: checking findings against the source and writing business
meaning (descriptions, program summaries, rule names, captions, BRD narrative). They write it as
*annotations* that must point at existing source lines or artifact ids; `harness check-annotations`
rejects anything else, and only checked annotations reach the BRD. Agent definitions live in
`.claude/agents` (Claude Code) and `.github/agents` (GitHub Copilot, generated with
`python -m harness sync-agents`); start with `0_pipeline`.

## Pipeline stages

| # | Stage | Command | Code computes | Agent adds | Main outputs |
|---|---|---|---|---|---|
| 1 | Inventory | `inventory` | programs, copybooks, JCL, CICS CSD, BMS, DB2 DDL, VSAM; every reference resolved with file:line; program-to-dataset links; defects | verifies findings | `output/inventory/` |
| 2 | Parser | `parse` | copybook expansion with REPLACING, data entries, statement tree, paragraph control flow, dead code, undefined targets, recursion | verifies findings | `output/parser/raw_structure/` |
| 3 | Data | `data` | field sizes and offsets, 88 values, record-file-dataset-VSAM-CICS-DB2 links, SQL host-variable checks, data flows | entity and record descriptions | `output/data/` |
| 4 | Logic | `logic` | execution outline, branches with 88 meanings, loop termination, I/O, data flow, line-traced pseudocode | program summaries | `output/logic/program_logic/` |
| 5 | Rules | `rules` | rule candidates from decisions and calculations, categories with signals, repeats, rules in dead code | business names and descriptions | `output/rules/rules_catalog.md` |
| 6 | Diagrams | `diagrams` | system context, lineage, ERD, job flows, CICS sequence, paragraph graphs, flowcharts (Mermaid, linted) | captions | `output/diagram/diagrams.md` |
| 7 | Synthesis | `synthesize` | gaps and risks register, BRD skeleton, tables and citations | executive summary, processes, recommendations | `output/final_report/brd.md` |
| 8 | Graph | `graph` | Neo4j CSVs (neo4j-admin format), import.cypher, checked query library | export review | `output/final_report/graph/neo4j/` |

Each run writes its artifacts, agent reviews (`*_review.md`) and annotation files to `output/`.

## Contributing

The project is at an early stage. Issues and suggestions are welcome.

## License

Released under the [MIT License](LICENSE).
