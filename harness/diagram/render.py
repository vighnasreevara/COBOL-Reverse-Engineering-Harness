"""Markdown galleries that embed the Mermaid diagrams (render in GitHub and VS Code preview)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.rules.render import annotation_index


def gallery(artifact: dict[str, Any], out_dir: Path, annotations_path: Path | None) -> list[Path]:
    captions = annotation_index(annotations_path)
    system = [d for d in artifact["diagrams"] if not d["id"].startswith("diagram:program_")]
    programs = [d for d in artifact["diagrams"] if d["id"].startswith("diagram:program_")]
    written = []
    for name, title, items in (("diagrams.md", "System diagrams", system),
                               ("diagrams_programs.md", "Program diagrams", programs)):
        lines = [f"# {title}", "", "Generated from the pipeline artifacts; captions marked with a confidence level "
                 "were written by the diagram agent and checked.", ""]
        for d in items:
            cap = captions.get(d["id"], {}).get("caption")
            lines += [f"## {d['title']}", ""]
            if cap:
                lines += [f"{cap['text']} *({cap['confidence']})*", ""]
            text = (out_dir / d["file"]).read_text(encoding="utf-8")
            lines += ["```mermaid", text.rstrip(), "```", "", f"Source: `{d['file']}`", ""]
        path = out_dir / name
        path.write_text("\n".join(lines), encoding="utf-8")
        written.append(path)
    return written
