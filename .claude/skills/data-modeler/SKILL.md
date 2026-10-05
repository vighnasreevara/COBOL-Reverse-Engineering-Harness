---
name: data-modeler
description: >
  Runs the deterministic data stage (python -m harness data): computes every
  record layout (field sizes, offsets, usage, OCCURS, REDEFINES, 88-level
  values), links records to files, JCL datasets, VSAM clusters, CICS files and
  DB2 tables, checks embedded SQL host variables against the DDL, and builds
  the data model and data flows. Writes output/data/data_artifact.json. Also
  explains how to write and check data annotations. Used by the 3_data agent.
---

# Skill: data modeler

## 1. Run

Python: `.venv\Scripts\python.exe` (Windows) or `.venv/bin/python`.

```bash
<python> -m harness data --inventory output/inventory/inventory_artifact.json \
                        --parser output/parser/parser_artifact.json --out output/data
```

Exit codes: 0 valid, 1 written but failed validation (stop and report),
2 an input artifact is missing (run stages 1 and 2 first). Re-check with
`<python> -m harness validate-data output/data/data_artifact.json`.

## 2. What is computed

| Section | Content |
|---|---|
| `records` | One entry per distinct 01/77 layout. Id `record:<COPYBOOK or PROGRAM>/<NAME>` (`~2` suffix for REPLACING variants). `size` in bytes, `fields` with `offset`, `size`, `category`, `usage`, `digits`, `scale`, `occurs`, `redefines`, `conditions` (88s), `used_by` (program, section, FD) |
| `fields`, `conditions` | Flat catalogues with ids `field:...` and `condition:...` |
| `programs` | Per program: records used, files (SELECT, DD, organization, OPEN modes, record size, bound datasets with LRECL/RECFM), SQL tables, CICS file accesses |
| `entities` | `dataset:<DSN>` (records, VSAM key/size, programs and access), `table:<NAME>` (columns with types, primary key, DDL comment, programs per operation), `cics_file:<NAME>`, `file:<DD>` (file with no JCL in scope) |
| `relationships` | Declared only: DB2 foreign keys, CICS file to dataset |
| `data_flows` | program to entity with direction (read, write, update, delete) and the source line |
| `host_variables` | Every SQL column paired with its host variable, with any type mismatch |

Size rules (IBM Enterprise COBOL): DISPLAY = character positions (+1 for SIGN
SEPARATE); COMP/BINARY/COMP-5 = 2, 4 or 8 bytes for up to 4, 9, 18 digits;
COMP-3 = digits / 2 + 1; COMP-1 4; COMP-2 8; POINTER/INDEX 4; NATIONAL 2 per
position. Group USAGE is inherited. SYNC slack bytes are not added.

## 3. Issue codes

| Code | Meaning |
|---|---|
| `record_length_mismatch` | Record size disagrees with DD LRECL/RECFM or a fixed-length VSAM cluster |
| `vsam_key_mismatch` | No field sits exactly on the cluster's KEYS(length offset) |
| `cics_record_size_mismatch` | CICS READ/WRITE record size differs from the CSD FILE RECORDSIZE |
| `sql_column_count_mismatch` | INSERT/SELECT/FETCH column count differs from host variables (host structures are expanded) |
| `unknown_column` | SQL names a column the DDL does not define |
| `host_variable_type_mismatch` | Host variable PICTURE/USAGE does not suit the column type |
| `value_too_long`, `value_too_large`, `nonnumeric_value_for_numeric` | VALUE clause does not fit the item |
| `redefines_larger_than_target`, `redefines_target_missing`, `group_with_picture`, `orphan_data_entry` | Layout defects |
| `empty_record` (info) | 01 with no subordinates, usually because the COPY under it starts its own 01 |

## 4. Annotations (descriptions written by the agent)

Write `output/data/data_annotations.json`:

```json
{
  "stage": "data",
  "items": [
    {
      "target": "record:CUSTREC/CUSTOMER-RECORD",
      "kind": "description",
      "text": "Customer master record keyed by customer number ...",
      "evidence": [{"path": "copybook/CUSTREC.cpy", "line": 5},
                   {"id": "field:CUSTREC/CUSTOMER-RECORD:CUSTOMER-RECORD.CUST-KEY"}],
      "confidence": "high"
    }
  ]
}
```

- `target` and every evidence `id` must exist in `data_artifact.json`;
  `path` is relative to the scanned root and `line` must exist.
- `kind`: `description` for entities/records, `name` for a business name of a
  field, `note` for anything else. `confidence`: high, medium or low.
- Base descriptions on field names, 88-level values, DDL comments, VALUE
  literals and how programs use the data (data_flows). Say "appears to" and use
  `medium`/`low` when inferring.

Check before finishing:

```bash
<python> -m harness check-annotations output/data/data_annotations.json --artifact output/data/data_artifact.json
```
