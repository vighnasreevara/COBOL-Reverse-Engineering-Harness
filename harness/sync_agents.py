"""Generate the GitHub Copilot agent/skill files from the Claude Code ones.

The .claude files are the source of truth; run ``python -m harness sync-agents``
after editing them. tests/test_agent_definitions.py fails if the copies drift.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPILOT_TOOLS = "['execute', 'read', 'edit', 'search']"


def _split(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    _, front, body = text.split("---\n", 2)
    return front, body


def _description(front: str) -> str:
    lines = front.splitlines()
    out: list[str] = []
    capture = False
    for line in lines:
        if line.startswith("description:"):
            rest = line[len("description:"):].strip()
            if rest and rest != ">":
                return rest
            capture = True
            continue
        if capture:
            if line.startswith(" "):
                out.append(line.strip())
            else:
                break
    return " ".join(out)


def _name(front: str) -> str:
    for line in front.splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    return ""


def sync(root: Path = ROOT) -> list[Path]:
    written: list[Path] = []
    for agent in sorted((root / ".claude" / "agents").glob("*.md")):
        front, body = _split(agent.read_text(encoding="utf-8"))
        target = root / ".github" / "agents" / f"{agent.stem}.agent.md"
        content = (f"---\nname: {_name(front)}\ndescription: {_description(front)}\n"
                   f"tools: {COPILOT_TOOLS}\n---\n" + body.replace(".claude/skills", ".github/skills"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(target)
    for skill in sorted((root / ".claude" / "skills").glob("*/SKILL.md")):
        target = root / ".github" / "skills" / skill.parent.name / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(skill.read_text(encoding="utf-8").replace(".claude/skills", ".github/skills"),
                          encoding="utf-8")
        written.append(target)
    return written
