"""COPY / EXEC SQL INCLUDE expansion at token level, with REPLACING."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from harness.cobol.source import CodeLine, normalize
from harness.cobol.tokens import LIT, PSEUDO, PUNCT, WORD, Token, tokenize
from harness.common.textio import read_text

SYSTEM_INCLUDES = {"SQLCA", "SQLDA"}
MAX_DEPTH = 20


@dataclass
class Expansion:
    member: str
    kind: str                  # COPY or SQL_INCLUDE
    status: str                # expanded, missing, system, recursive, too_deep
    at_file: str
    at_line: int
    depth: int
    path: str | None = None
    replacing: list[dict] = field(default_factory=list)
    division: str | None = None


@dataclass
class _Pair:
    source: list[Token]
    target: list[Token]
    mode: str | None           # None, LEADING, TRAILING
    raw: dict


class Expander:
    def __init__(self, root: Path, members: dict[str, str]) -> None:
        self.root = root
        self.members = members            # member name -> path relative to root
        self._cache: dict[str, list[Token]] = {}

    def member_tokens(self, rel: str) -> list[Token]:
        if rel not in self._cache:
            tf = read_text(self.root / rel)
            self._cache[rel] = tokenize(normalize(tf.lines).code, rel).tokens
        return self._cache[rel]

    def expand(self, tokens: list[Token]) -> tuple[list[Token], list[Expansion]]:
        log: list[Expansion] = []
        out = self._expand(tokens, [], 0, log)
        return out, log

    def _expand(self, tokens: list[Token], stack: list[str], depth: int, log: list[Expansion]) -> list[Token]:
        out: list[Token] = []
        division: str | None = None
        i = 0
        n = len(tokens)
        while i < n:
            tok = tokens[i]
            if tok.kind == WORD and tok.value in ("DATA", "PROCEDURE", "ENVIRONMENT") and i + 1 < n \
                    and tokens[i + 1].is_word("DIVISION"):
                division = tok.value
            if tok.is_word("COPY") and i + 1 < n and tokens[i + 1].kind in (WORD, LIT):
                end, name, pairs = _parse_copy(tokens, i)
                out.extend(self._insert(name, "COPY", pairs, tok, stack, depth, division, log))
                i = end
                continue
            if tok.is_word("EXEC") and i + 3 < n and tokens[i + 1].is_word("SQL") \
                    and tokens[i + 2].is_word("INCLUDE") and tokens[i + 3].kind == WORD:
                name = tokens[i + 3].value
                end = i + 4
                while end < n and not tokens[end].is_word("END-EXEC"):
                    end += 1
                if name in SYSTEM_INCLUDES or name not in self.members:
                    status = "system" if name in SYSTEM_INCLUDES else "missing"
                    log.append(Expansion(name, "SQL_INCLUDE", status, tok.file, tok.line, depth, division=division))
                    out.extend(tokens[i:end + 1])     # keep the statement visible
                else:
                    out.extend(self._insert(name, "SQL_INCLUDE", [], tok, stack, depth, division, log))
                i = end + 1
                continue
            out.append(tok)
            i += 1
        return out

    def _insert(self, name: str, kind: str, pairs: list[_Pair], at: Token, stack: list[str],
                depth: int, division: str | None, log: list[Expansion]) -> list[Token]:
        rel = self.members.get(name)
        record = Expansion(name, kind, "expanded", at.file, at.line, depth, rel,
                           [p.raw for p in pairs], division)
        log.append(record)
        if rel is None:
            record.status = "missing"
            return []
        if name in stack:
            record.status = "recursive"
            return []
        if depth >= MAX_DEPTH:
            record.status = "too_deep"
            return []
        body = apply_replacing(self.member_tokens(rel), pairs)
        return self._expand(body, stack + [name], depth + 1, log)


def _operand_tokens(tok: Token) -> list[Token]:
    if tok.kind == PSEUDO:
        if not tok.value:
            return []
        return tokenize([CodeLine(tok.line, tok.value, False, False, tok.col)], tok.file).tokens
    return [tok]


def _parse_copy(tokens: list[Token], i: int) -> tuple[int, str, list[_Pair]]:
    n = len(tokens)
    name = tokens[i + 1].value.strip().upper()
    j = i + 2
    if j < n and tokens[j].is_word("OF", "IN"):
        j += 2
    if j < n and tokens[j].is_word("SUPPRESS"):
        j += 1
    pairs: list[_Pair] = []
    if j < n and tokens[j].is_word("REPLACING"):
        j += 1
        while j < n and not tokens[j].is_punct("."):
            mode = None
            if tokens[j].is_word("LEADING", "TRAILING"):
                mode = tokens[j].value
                j += 1
            if j + 2 < n and tokens[j + 1].is_word("BY"):
                left, right = tokens[j], tokens[j + 2]
                pairs.append(_Pair(_operand_tokens(left), _operand_tokens(right), mode,
                                   {"from": left.text(), "to": right.text(), **({"mode": mode} if mode else {})}))
                j += 3
            else:
                j += 1
    if j < n and tokens[j].is_punct("."):
        j += 1
    return j, name, pairs


def apply_replacing(tokens: list[Token], pairs: list[_Pair]) -> list[Token]:
    if not pairs:
        return list(tokens)
    out: list[Token] = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        replaced = False
        for p in pairs:
            if p.mode or _is_tag(p):
                if tok.kind != WORD or len(p.source) != 1:
                    continue
                old = p.source[0].value
                new = p.target[0].value if p.target else ""
                value = tok.value
                if p.mode == "LEADING" and value.startswith(old):
                    value = new + value[len(old):]
                elif p.mode == "TRAILING" and value.endswith(old):
                    value = value[:-len(old)] + new
                elif p.mode is None and old in value:
                    value = value.replace(old, new)
                if value != tok.value:
                    out.append(Token(WORD, value, tok.line, tok.col, tok.file))
                    i += 1
                    replaced = True
                    break
                continue
            k = len(p.source)
            if k and i + k <= n and all(_same(tokens[i + m], p.source[m]) for m in range(k)):
                out.extend(Token(t.kind, t.value, tok.line, tok.col, tok.file, t.quote) for t in p.target)
                i += k
                replaced = True
                break
        if not replaced:
            out.append(tok)
            i += 1
    return out


def _is_tag(p: _Pair) -> bool:
    """==:TAG:== style partial-word replacement."""
    return len(p.source) == 1 and p.source[0].kind == WORD and p.source[0].value.startswith(":") \
        and p.source[0].value.endswith(":") and len(p.source[0].value) > 2


def _same(a: Token, b: Token) -> bool:
    if a.kind != b.kind:
        return False
    return a.value == b.value if a.kind != PUNCT else a.value == b.value
