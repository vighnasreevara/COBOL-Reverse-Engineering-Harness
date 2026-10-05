---
name: brd-writer
description: >
  Runs the deterministic synthesis stage (python -m harness synthesize): builds
  the gaps and risks register from every stage's issues, and assembles the
  Business Requirements Document (brd.md), a summary and the gaps register from
  the verified artifacts plus every stage's checked annotations. Explains the
  narrative sections the agent writes (executive summary, purpose, processes,
  recommendations) and how they are checked. Used by the 7_synthesis agent.
---

# Skill: BRD writer

## 1. Run

```bash
<python> -m harness synthesize --output-root output --system "<system name>"
```

Needs the stage 1 to 6 artifacts. Writes to `output/final_report/`:
`synthesis_artifact.json` (sections, entry points, gaps), `brd.md`,
`brd_summary.md`, `gaps_register.md`. Exit code 1 when an annotations file
from any stage fails its check (it is then left out of the BRD and listed in
chapter 9), or when the BRD cites a source line that does not exist.

## 2. What the BRD contains

| Chapter | Built from |
|---|---|
| 1 Executive summary | your `brd:executive_summary` narrative + highest-severity gaps |
| 2 System overview | your `brd:system_purpose` narrative + inventory/data counts + system context diagram |
| 3 Business processes | one section per job / transaction: your `brd:process:<NAME>` narrative, logic summaries, rules applied, job or transaction diagram; then programs nobody starts |
| 4 Business rules | named rules from the rules annotations (enforced / never runs) |
| 5 Data | data stores with readers, writers and descriptions; key record layouts; ERD and lineage |
| 6 Program reference | every program with kind, entry point, size and complexity |
| 7 Gaps and risks | consolidated register (`gap:<code>:<subject>`), high and medium |
| 8 Recommendations | your `brd:recommendations` narrative |
| 9 About | input hashes, rejected annotations, glossary |

Gap severity and recommended actions come from a fixed policy
(`harness/synthesis/gaps.py`); business rules that never run become
`unenforced_business_rules` gaps.

## 3. Narratives (written by the agent)

`output/final_report/synthesis_annotations.json`, `kind: narrative`, targets from
`synthesis_artifact.json` `sections`: `brd:executive_summary`,
`brd:system_purpose`, `brd:process:<JOB or TRANSACTION>`, `brd:recommendations`.
You may also add `kind: note` on a gap id to explain it.

Evidence may be source lines or any id from any stage (program, record,
entity, rule, branch, loop, diagram, gap). Write for business readers: no
COBOL jargon without explanation, every claim traceable, inferences marked
with medium/low confidence.

Then rerun `harness synthesize` (the narratives are checked on every run).
