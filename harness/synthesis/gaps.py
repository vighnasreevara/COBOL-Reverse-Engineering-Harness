"""Gap and risk register consolidated from every stage's issues."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

# code -> (severity, category, recommended action)
POLICY: dict[str, tuple[str, str, str]] = {
    "missing_program": ("high", "Missing source", "Locate the program in another load library or vendor package and add it to the scan."),
    "missing_copybook": ("high", "Missing source", "Find the copybook (another library, renamed member) and rescan; layouts using it are incomplete."),
    "copy_target_is_program": ("high", "Missing source", "Confirm which member was meant; a COPY names a program, not a copybook."),
    "missing_include": ("high", "Missing source", "Find the SQL INCLUDE member (DCLGEN) and rescan."),
    "missing_map": ("high", "Missing source", "Find or define the BMS map; the screen cannot be shown as coded."),
    "missing_mapset": ("high", "Missing source", "Find the BMS mapset source."),
    "csd_program_missing": ("high", "Configuration", "Fix the CSD transaction definition or add the program."),
    "online_program_not_in_csd": ("high", "Configuration", "Add a CSD PROGRAM definition, otherwise LINK/XCTL to it fails (PGMIDERR)."),
    "csd_program_without_source": ("medium", "Missing source", "Find the source of the defined CICS program or remove the definition."),
    "db2_program_without_attach": ("high", "Configuration", "Run the program under IKJEFT01 with DSN RUN, or add a CAF/RRSAF attach."),
    "stray_lines": ("high", "Configuration", "Fix the JCL so the control statements are part of the in-stream data."),
    "undefined_procedure": ("high", "Incomplete code", "The program performs paragraphs it does not contain; obtain the complete source before modernising."),
    "undefined_data_name": ("high", "Incomplete code", "Data names are used but never defined; obtain the complete source or correct copybook."),
    "unknown_intrinsic_function": ("medium", "Incomplete code", "Replace with a valid intrinsic function or a site routine."),
    "possibly_truncated_source": ("high", "Incomplete code", "The file ends mid-program; obtain the full member."),
    "empty_file": ("medium", "Incomplete code", "Obtain the real member or remove it from the inventory."),
    "procedure_code_in_data_division": ("high", "Incomplete code", "Copy the procedure copybook into the PROCEDURE DIVISION."),
    "procedure_copybook_in_data_division": ("high", "Incomplete code", "Copy the procedure copybook into the PROCEDURE DIVISION."),
    "recursive_perform": ("high", "Logic risk", "Remove the recursion; behaviour is undefined in non-recursive COBOL."),
    "potential_infinite_loop": ("high", "Logic risk", "Confirm how the loop ends; nothing in the loop changes the tested fields."),
    "dead_paragraph": ("medium", "Logic risk", "Confirm whether the unreachable code is retired or should be called; business rules in it are not enforced."),
    "unreachable_statement": ("low", "Logic risk", "Remove or relocate code after the program end."),
    "record_length_mismatch": ("high", "Data integrity", "Align the record layout with the dataset definition (LRECL / RECORDSIZE)."),
    "vsam_key_mismatch": ("high", "Data integrity", "Align the record key with the cluster KEYS definition."),
    "cics_record_size_mismatch": ("high", "Data integrity", "Align the record with the CSD FILE RECORDSIZE."),
    "sql_column_count_mismatch": ("high", "Data integrity", "Match the SQL column list with the host variables."),
    "unknown_column": ("high", "Data integrity", "Correct the column name or the DDL."),
    "host_variable_type_mismatch": ("medium", "Data integrity", "Use host variables that match the column types."),
    "value_too_long": ("low", "Data integrity", "Shorten the VALUE literal or enlarge the field."),
    "value_too_large": ("low", "Data integrity", "Adjust the VALUE or the PICTURE."),
    "literal_truncated_at_column_72": ("medium", "Code quality", "Split the literal with a continuation line."),
    "free_format_source": ("low", "Code quality", "Confirm the compiler option for free-format source."),
    "unused_copybook": ("low", "Housekeeping", "Remove or document the unused copybook."),
    "subroutine_without_callers": ("low", "Housekeeping", "Confirm whether the subroutine is called from outside the scanned source."),
    "program_not_executed": ("medium", "Configuration", "Find the JCL or scheduler entry that runs the program, or confirm it is retired."),
}
POLICY.update({
    "duplicate_program_id": ("high", "Configuration", "Two members declare the same PROGRAM-ID; decide which one is deployed."),
    "parse_failed": ("high", "Incomplete code", "The parser could not process this program; inspect it manually."),
    "missing_dd_for_file": ("high", "Configuration", "Add the DD statement the program's file needs to the job step."),
    "missing_transaction": ("medium", "Configuration", "Define the transaction in the CSD or correct the TRANSID."),
    "missing_cics_file": ("medium", "Configuration", "Define the CICS FILE in the CSD."),
    "conflicting_vsam_definitions": ("high", "Data integrity", "Decide which cluster definition is authoritative."),
    "ambiguous_member": ("medium", "Missing source", "Several members share the name; confirm which library is used."),
    "alter_statement": ("high", "Logic risk", "ALTER changes control flow at run time; document every target."),
    "redefines_larger_than_target": ("medium", "Data integrity", "Check the REDEFINES layout; it is longer than the item it redefines."),
    "redefines_target_missing": ("medium", "Data integrity", "Correct the REDEFINES target."),
    "unrecognised_picture": ("medium", "Data integrity", "Check the PICTURE clause."),
    "group_with_picture": ("medium", "Data integrity", "Remove the PICTURE from the group item."),
    "perform_thru_backwards": ("medium", "Logic risk", "Correct the PERFORM THRU range."),
    "duplicate_paragraph": ("medium", "Logic risk", "Rename or qualify the duplicate paragraph."),
    "unreadable_file": ("high", "Missing source", "Fix the file encoding or permissions and rescan."),
})
# Findings that describe style or expected system use, not risks; kept in the stage artifacts only.
IGNORED = {"nonstandard_comment", "nonstandard_continuation", "system_program", "dynamic_call", "external_proc",
           "program_id_differs_from_member", "empty_record", "undefined_table", "dsn_command_in_sql_file",
           "statements_before_job_card", "name_collision"}
DEFAULT = ("low", "Other", "Review the finding.")
_BY_LEVEL = {"error": ("high", "Other", "Review the finding."), "warning": ("medium", "Other", "Review the finding."),
             "info": ("low", "Other", "Review the finding.")}
_RANK = {"high": 0, "medium": 1, "low": 2}
# Business impact order within a severity.
_CATEGORY_ORDER = ["Logic risk", "Missing source", "Data integrity", "Configuration", "Incomplete code",
                   "Code quality", "Housekeeping", "Other"]


def build_gaps(issues_by_stage: dict[str, list[dict]], unreachable_rules: list[dict]) -> list[dict]:
    """One gap per (code, subject); every location kept."""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for stage, issues in issues_by_stage.items():
        for i in issues:
            code = i["code"]
            if code in IGNORED:
                continue
            subject = i.get("subject") or i.get("path") or "system"
            key = (code, subject)
            g = grouped.setdefault(key, {"code": code, "subject": subject, "stage": stage, "messages": [], "locations": [],
                                         "level": i["severity"]})
            g["messages"].append(i["message"])
            if i.get("path"):
                loc = {"path": i["path"], "line": i.get("line")}
                if loc not in g["locations"]:
                    g["locations"].append(loc)
    if unreachable_rules:
        by_program = defaultdict(list)
        for r in unreachable_rules:
            by_program[r["program"]].append(r)
        for program, rules in by_program.items():
            grouped[("unenforced_business_rules", program)] = {
                "code": "unenforced_business_rules", "subject": program, "stage": "rules",
                "messages": [f"{len(rules)} business rule(s) sit in code that never runs"],
                "locations": [{"path": r["file"], "line": r["line"]} for r in rules],
                "rules": [r["id"] for r in rules]}
    POLICY.setdefault("unenforced_business_rules", ("high", "Logic risk",
                                                    "Decide whether these rules must be enforced; today they are not."))
    gaps = []
    for (code, subject), g in grouped.items():
        severity, category, action = POLICY.get(code) or _BY_LEVEL.get(g.get("level"), DEFAULT)
        count = len(g["messages"])
        title = g["messages"][0] if count == 1 else f"{subject.split(':', 1)[-1]}: {count} x {code.replace('_', ' ')}"
        gaps.append({"severity": severity, "category": category, "code": code, "subject": subject,
                     "stage": g["stage"], "title": title, "count": count,
                     "details": g["messages"][:20], "locations": g["locations"][:50], "action": action,
                     **({"rules": g["rules"]} if "rules" in g else {})})
    gaps.sort(key=lambda g: (_RANK[g["severity"]], _CATEGORY_ORDER.index(g["category"]), -g["count"], g["subject"], g["code"]))
    for g in gaps:
        # stable across runs, so annotations can cite it
        g["id"] = f"gap:{g['code']}:{g['subject'].split(':', 1)[-1]}"
    return gaps
