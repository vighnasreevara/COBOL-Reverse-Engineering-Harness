"""Stage 2 end to end: inventory -> parser on small hand-written systems."""

import json
from collections import Counter
from pathlib import Path

import pytest
from conftest import fixed

from harness.cli import main
from harness.inventory.builder import InventoryBuilder, ScanOptions
from harness.parser.build import run_parser
from harness.parser.validate import validate_manifest


def write(root: Path, rel: str, lines):
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text("\n".join(lines), encoding="utf-8")


def make_inventory(src: Path, out: Path) -> Path:
    inv = InventoryBuilder(ScanOptions(root=src, timestamp=False)).build()
    out.mkdir(parents=True, exist_ok=True)
    path = out / "inventory_artifact.json"
    path.write_text(json.dumps(inv), encoding="utf-8")
    return path


def test_small_system(tmp_path):
    src = tmp_path / "src"
    write(src, "PROCS.cpy", fixed("01 P-AREA PIC X.", "HELPER-PARA.", "    DISPLAY 'X'."))
    write(src, "PGM1.cbl", fixed(
        "IDENTIFICATION DIVISION.", "PROGRAM-ID. PGM1.", "DATA DIVISION.", "WORKING-STORAGE SECTION.",
        "01 WS-A PIC X.", "    COPY PROCS.", "PROCEDURE DIVISION.",
        "    PERFORM HELPER-PARA", "    MOVE WS-A TO WS-UNDEFINED", "    MOVE FUNCTION NOPE(WS-A) TO WS-A",
        "    GOBACK."))
    inv = make_inventory(src, tmp_path / "out" / "inventory")
    manifest = run_parser(inv, tmp_path / "out" / "parser", timestamp=False)
    assert validate_manifest(manifest, tmp_path / "out" / "parser") == []
    codes = Counter(i["code"] for i in manifest["issues"])
    assert codes == {"procedure_code_in_data_division": 1, "undefined_procedure": 1,
                     "undefined_data_name": 1, "unknown_intrinsic_function": 1}
    und = next(i for i in manifest["issues"] if i["code"] == "undefined_procedure")
    assert "PROCS.cpy" in und["message"]


def test_cli_parse(tmp_path, capsys):
    src = tmp_path / "src"
    write(src, "P.cbl", fixed("IDENTIFICATION DIVISION.", "PROGRAM-ID. P.", "PROCEDURE DIVISION.", "    GOBACK."))
    inv = make_inventory(src, tmp_path / "inv")
    assert main(["parse", "--inventory", str(inv), "--out", str(tmp_path / "parser"), "--no-timestamp"]) == 0
    assert "Parser complete" in capsys.readouterr().out
    assert main(["validate-parser", str(tmp_path / "parser" / "parser_artifact.json")]) == 0
