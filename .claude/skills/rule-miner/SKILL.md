---
name: rule-miner
description: >
  Runs the deterministic rules stage (python -m harness rules): turns every
  decision (IF, EVALUATE WHEN, SEARCH WHEN, AT END / INVALID KEY / SIZE ERROR
  phrases) and every non-counter calculation into a rule candidate anchored to
  one source line, classifies it (validation, limit, calculation, routing,
  state_change, decision, error_handling, control_flow) with the signals that
  decided it, groups repeated rules, and renders rules_catalog.md. Explains how
  to name and describe rules with checked annotations. Used by the 5_rules agent.
---

# Skill: rule miner

## 1. Run

```bash
<python> -m harness rules --parser output/parser/parser_artifact.json \
    --logic output/logic/logic_artifact.json --out output/rules
```

Writes `rules_artifact.json` and `rules_catalog.md`. If
`output/rules/rules_annotations.json` exists it is checked and merged into the
catalogue; a failing annotations file makes the command exit 1 and the
catalogue is rendered without it. Re-check with `validate-rules`.

## 2. Rule candidates

| Field | Meaning |
|---|---|
| `id` | `rule:<PROGRAM>:<line>` (`.2`, `.3` when several decisions share a line) |
| `source` | Logic-stage branch id, or `statement:<PROGRAM>:<line>` for a calculation |
| `category` | validation, limit, calculation, routing, state_change, decision (business); error_handling, control_flow (technical) |
| `significance` | 1 to 5; `business` is true for significance >= 3 outside the technical categories |
| `signals` | Why the category was chosen (e.g. "sets an error message", "tests status field WS-FILE-STATUS") |
| `condition` / `formula` | The rule exactly as coded; `conditions_explained` gives 88-level meanings |
| `outcomes` | Top-level actions per outcome |
| `reachable` | False when the rule sits in a paragraph that can never run |
| `group` | Set when the same rule appears in several places (`groups`) |

Classification is heuristic. Your job is to correct its meaning, not its
facts: if a candidate is not really a business rule, say so in a `verdict`
annotation.

## 3. Annotations

`output/rules/rules_annotations.json`, one or more items per rule:

| kind | text |
|---|---|
| `name` | Short business name, e.g. "Sale quantity cannot exceed units held" |
| `description` | 1 to 3 sentences in business terms: when it applies, what happens, what it protects |
| `verdict` | "not a business rule: ..." or "misclassified: should be ..." |

Evidence: the rule's source line plus the rule id. Confidence `high` only when
the code and field meanings make the purpose unambiguous.

```bash
<python> -m harness check-annotations output/rules/rules_annotations.json --artifact output/rules/rules_artifact.json
<python> -m harness rules     # re-render the catalogue with the names
```
