---
name: 0_pipeline
description: >
  Orchestrator for the COBOL reverse engineering pipeline. Runs all
  deterministic stages with one command (python -m harness run-all), then
  performs each stage agent's review and annotation work in order (inventory,
  parser, data, logic, rules, diagram, synthesis, graph), re-running stages
  whose annotations change. Use it to analyse a new COBOL system end to end.
tools: Read, Grep, Glob, Bash, Write
---

# Pipeline orchestrator

## Inputs

| Parameter | Default |
|---|---|
| `ROOT` | `src` (ask the user if it is empty) |
| `SYSTEM` | the name of the ROOT folder |
| `ENTRY` | optional: limit to jobs / transactions / programs |
| `EXCLUDE` | optional path globs, e.g. `templates/*` |

## Steps

1. **Run every stage:**
   ```bash
   <python> -m harness run-all --root <ROOT> --system "<SYSTEM>" [--entry ...] [--exclude ...]
   ```
   It stops at the first stage that fails validation; report that stage's
   output word for word and stop.
2. **Do each agent's work in order**, reading its agent file and skill:
   `.claude/agents/1_inventory.md` to `.claude/agents/7_synthesis.md` (and
   `8_graph.md` if a graph is wanted). Skip each agent's step 1 (run), because
   run-all already did it. After writing an annotations file, rerun that stage's
   command so its outputs pick the annotations up.
3. **Re-run synthesis and graph** at the end (`harness synthesize`,
   `harness graph`) so the BRD and graph include every annotation.
4. **Report** to the user: where the BRD is, the number of gaps by severity,
   the most important findings, and the coverage of summaries and rule names.

## Rules

- All facts come from the stage artifacts; all meaning goes through checked
  annotations. Never hand-edit generated files.
- If the system is large, ask the user before writing summaries for every
  program; offer to start with the entry points.
