---
name: graph-exporter
description: >
  Runs the deterministic Neo4j export (python -m harness graph): nodes for
  programs, copybooks, jobs, transactions, paragraphs, records, fields, 88-level
  conditions, data stores, business rules, maps and gaps, with their
  relationships; CSVs in neo4j-admin import format, a batched import.cypher, a
  query library checked against the export schema, and import instructions.
  Used by the 8_graph agent.
---

# Skill: graph exporter

## 1. Run

```bash
<python> -m harness graph --output-root output --system "<system name>"
```

Writes `output/final_report/graph/neo4j/`: `nodes/<Label>.csv`,
`rels/<TYPE>.csv`, `import.cypher`, `cypher_library.md`, `README.md`,
`graph_artifact.json`. Validation checks CSV headers, unique ids, that every
relationship points at an exported node, the stats, and that every query in
the library only uses labels, relationship types and properties of the export.

## 2. Model

| Label | Key | From |
|---|---|---|
| Program, Copybook, Job, Transaction, Map | `program:X`, `copybook:X`, `job:X`, `transaction:X`, `map:SET.MAP` | inventory |
| Paragraph | `paragraph:PGM:NAME` | logic |
| Record, Field, Condition, DataStore | ids from the data artifact | data |
| Rule | `rule:PGM:LINE` (business rules only) | rules |
| Gap | `gap:CODE:SUBJECT` | synthesis |

Relationships: RUNS, STARTS, CALLS, LINKS, XCTL, COPIES, INCLUDES, USES_MAP,
HAS_PARAGRAPH, PERFORMS, DEFINES, HAS_FIELD, HAS_CONDITION, READS, WRITES,
UPDATES, DELETES, REFERENCES (foreign key), IN_PARAGRAPH (rule), AFFECTS (gap).
Nodes for missing targets carry `missing: true`. Agent-written summaries,
descriptions and rule names are stored as properties when their annotations
passed their checks.

## 3. Loading

See the generated `README.md`: `cypher-shell -f import.cypher` for small and
medium systems, `neo4j-admin database import full` with the CSVs for large ones.
