"""JSON schema validation shared by all stages (uses jsonschema when installed)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


def schema_problems(document: Any, schema_name: str, limit: int = 50) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        return []
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))[:limit]
    return [f"schema {schema_name}: {'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
            for e in errors]
