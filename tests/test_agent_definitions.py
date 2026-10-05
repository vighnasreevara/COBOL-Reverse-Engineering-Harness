"""The Claude Code and GitHub Copilot copies of each agent and skill must say the same thing."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = text.split("---\n", 2)[2] if text.startswith("---\n") else text
    return re.sub(r"\.(claude|github)/skills", ".X/skills", text)


def test_skills_in_sync():
    for skill in (ROOT / ".claude" / "skills").glob("*/SKILL.md"):
        twin = ROOT / ".github" / "skills" / skill.parent.name / "SKILL.md"
        assert twin.exists(), f"missing Copilot copy of {skill.parent.name}"
        assert body(skill) == body(twin), f"{skill.parent.name} differs between .claude and .github"


def test_agents_in_sync():
    for agent in (ROOT / ".claude" / "agents").glob("*.md"):
        twin = ROOT / ".github" / "agents" / f"{agent.stem}.agent.md"
        assert twin.exists(), f"missing Copilot copy of {agent.stem}"
        assert body(agent) == body(twin), f"{agent.stem} differs between .claude and .github"


def test_agents_reference_existing_skills():
    for agent in list((ROOT / ".claude" / "agents").glob("*.md")) + list((ROOT / ".github" / "agents").glob("*.md")):
        for ref in re.findall(r"`(\.(?:claude|github)/skills/[^`]+)`", agent.read_text(encoding="utf-8")):
            assert (ROOT / ref).exists(), f"{agent.name} points at missing {ref}"
