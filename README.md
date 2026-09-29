# COBOL Reverse Engineering Harness

A multi-agent harness for reverse engineering COBOL applications into structured system knowledge, data models, execution logic, business rules, diagrams, and traceable Business Requirements Documents (BRDs).

> **Status:** Early development. The architecture and outputs described below are the project's goals; implementation is in progress.

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

## Contributing

The project is at an early stage. Issues and suggestions are welcome.

## License

Released under the [MIT License](LICENSE).
