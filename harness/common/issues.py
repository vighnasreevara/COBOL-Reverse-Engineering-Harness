"""Findings that every stage reports in the same shape."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SEVERITIES = ("error", "warning", "info")


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    path: str | None = None
    line: int | None = None
    subject: str | None = None          # graph node id the issue is about
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r}")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.path is not None:
            out["path"] = self.path
        if self.line is not None:
            out["line"] = self.line
        if self.subject is not None:
            out["subject"] = self.subject
        if self.details:
            out["details"] = self.details
        return out


class IssueLog:
    def __init__(self) -> None:
        self._items: list[Issue] = []

    def add(self, severity: str, code: str, message: str, **kw: Any) -> Issue:
        issue = Issue(severity, code, message, **kw)
        self._items.append(issue)
        return issue

    def extend(self, issues: list[Issue]) -> None:
        self._items.extend(issues)

    def __iter__(self):
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def sorted(self) -> list[Issue]:
        rank = {s: i for i, s in enumerate(SEVERITIES)}
        return sorted(
            self._items,
            key=lambda i: (rank[i.severity], i.code, i.path or "", i.line or 0, i.message),
        )
