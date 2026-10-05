"""Stage 6: Mermaid generation and linting."""

import pytest

from harness.diagram.build import run_diagrams
from harness.diagram.mermaid import Flowchart, label, lint, node_id
from harness.diagram.validate import validate_diagrams


def test_ids_and_labels_are_safe():
    assert node_id("program:2000-MAIN") == "program_2000_MAIN"
    assert node_id("1A") == "n_1A"
    assert label('A "quoted" <x>') == '"A #quot;quoted#quot; #lt;x#gt;"'


def test_flowchart_render_passes_lint():
    fc = Flowchart("LR")
    fc.node("a", "Start (x)", "stadium", group="G")
    fc.node("b", 'IF X = "Y"?', "diamond")
    fc.edge("a", "b", "go")
    assert lint(fc.render("t")) == []


@pytest.mark.parametrize("text,problem", [
    ("flowchart TD\n  a[\"A\"]\n  a --> b\n", "undeclared node b"),
    ("graph\n  a[\"unbalanced]\n", "unbalanced quotes"),
    ("sequenceDiagram\n  participant A\n  A->>B: hi\n", "undeclared participant B"),
    ("sequenceDiagram\n  participant A\n  opt x\n  A->>A: hi\n", "1 blocks opened but 0 closed"),
    ("hello\n", "diagram type"),
])
def test_lint_catches_mistakes(text, problem):
    assert any(problem in p for p in lint(text))
