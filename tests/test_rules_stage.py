"""Stage 5: rule classification and catalogue rendering."""

import pytest

from harness.rules.build import classify_branch
from harness.rules.render import render_catalog


def branch(kind, condition, actions, explained=(), paragraph="2100-VALIDATE", fields=None):
    return {"id": "branch:P:B1", "kind": kind, "paragraph": paragraph, "condition": condition,
            "fields": fields if fields is not None else [w for w in condition.split() if "-" in w],
            "conditions_explained": list(explained),
            "outcomes": [{"when": "true", "actions": list(actions)}, {"when": "false", "actions": []}]}


@pytest.mark.parametrize("b,category", [
    (branch("IF", "ORD-QTY < = ZERO", ["MOVE 'Quantity must be positive' TO ERR-TEXT"]), "validation"),
    (branch("IF", "WS-FILE-STATUS NOT = '00'", ["MOVE 'Open failed' TO ERR-TEXT"]), "error_handling"),
    (branch("IF", "NOT FILE-OK", ["perform 9000-ERROR"],
            [{"condition": "FILE-OK", "field": "WS-CUST-STATUS", "values": [], "id": "c"}]), "error_handling"),
    (branch("IF", "WS-RESP = DFHRESP (NORMAL)", ["MOVE 0 TO X"]), "error_handling"),
    (branch("IF", "WS-RETRY-COUNT > = WS-MAX-RETRIES", ["perform 9000-ERROR-ROUTINE"]), "limit"),
    (branch("WHEN", "ORD-TYPE = 'NW'", ["perform 2210-NEW-ORDER"]), "routing"),
    (branch("PHRASE", "AT END (READ ORDER-FILE)", ["SET END-OF-FILE TO TRUE"]), "control_flow"),
    (branch("IF", "ERR-FATAL", ["CICS ABEND"], paragraph="P900-ERROR-ROUTINE"), "error_handling"),
])
def test_classification(b, category):
    assert classify_branch(b)[0] == category


def test_catalog_uses_checked_annotations():
    rule = {"id": "rule:ORDBATCH:28", "program": "program:ORDBATCH", "paragraph": "PROCESS-ORDER",
            "file": "cbl/ORDBATCH.cbl", "line": 28, "source": "branch:ORDBATCH:B1", "kind": "IF",
            "category": "validation", "significance": 4, "business": True, "signals": ["sets an error message"],
            "condition": "ORD-QTY <= ZERO", "conditions_explained": [], "fields": ["ORD-QTY"],
            "outcomes": [{"when": "true", "actions": ["MOVE 'Quantity must be positive' TO REPORT-LINE"]}],
            "reachable": False}
    artifact = {"rules": [rule], "groups": [], "stats": {"business_rules": 1, "candidates": 1,
                                                       "programs_with_business_rules": 1,
                                                       "unreachable_business_rules": 1}}
    md = render_catalog(artifact, {rule["id"]: {"name": {"text": "Quantity must be positive", "confidence": "high"}}})
    assert "Quantity must be positive (high)" in md
    assert "`cbl/ORDBATCH.cbl:28`" in md and "*unreachable*" in md
