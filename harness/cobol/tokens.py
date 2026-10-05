"""Tokenizer for COBOL program text (including embedded EXEC SQL/CICS).

Statements span lines freely, so pattern matching works on tokens rather
than on lines. Literals are kept as single tokens, which means words inside
strings, DISPLAY text or SQL held in MOVE literals can never be mistaken
for statements. Every token remembers its file, line and column, so code
expanded from a copybook still points at the copybook.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from harness.cobol.source import CodeLine, Note

WORD = "WORD"
LIT = "LIT"
PUNCT = "PUNCT"
PSEUDO = "PSEUDO"     # ==pseudo-text== in COPY ... REPLACING
PIC = "PIC"           # the character-string after PIC / PICTURE

_WORD_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_:#@$")
_LITERAL_PREFIXES = {"X", "N", "G", "Z", "NX", "U", "B", "BX"}


@dataclass(frozen=True)
class Token:
    kind: str
    value: str       # WORD values are upper-cased; LIT values are unquoted
    line: int
    col: int = 0
    file: str = ""
    quote: str = ""  # quote character of a literal

    def is_word(self, *values: str) -> bool:
        return self.kind == WORD and (not values or self.value in values)

    def is_punct(self, value: str) -> bool:
        return self.kind == PUNCT and self.value == value

    def text(self) -> str:
        if self.kind == LIT:
            q = self.quote or "'"
            return q + self.value.replace(q, q + q) + q
        if self.kind == PSEUDO:
            return f"=={self.value}=="
        return self.value


@dataclass
class TokenStream:
    tokens: list[Token]
    notes: list[Note] = field(default_factory=list)


def tokenize(code: list[CodeLine], path: str = "") -> TokenStream:
    tokens: list[Token] = []
    notes: list[Note] = []

    # An open literal carried across a continuation line.
    open_quote: str | None = None
    open_value: list[str] = []
    open_line = 0
    open_col = 0
    # Commas matter inside EXEC blocks (SQL lists), not in COBOL proper.
    in_exec = False
    expect_pic = False

    def close_literal() -> None:
        nonlocal open_quote, open_value
        tokens.append(Token(LIT, "".join(open_value), open_line, open_col, path, open_quote or "'"))
        open_quote = None
        open_value = []

    for cl in code:
        text = cl.text
        offset = cl.start_col
        i = 0
        if open_quote is not None:
            if cl.continuation:
                body = text.lstrip()
                offset += len(text) - len(body)
                if body.startswith(open_quote):
                    body = body[1:]
                    offset += 1
                text = body
            else:
                notes.append(Note("unterminated_literal", open_line))
                close_literal()
        elif cl.continuation:
            # Continued word or number: glue onto the previous token.
            body = text.lstrip()
            offset += len(text) - len(body)
            text = body
            if tokens and tokens[-1].kind == WORD and text and text[0] in _WORD_CHARS:
                j = 0
                while j < len(text) and text[j] in _WORD_CHARS:
                    j += 1
                prev = tokens.pop()
                tokens.append(Token(WORD, prev.value + text[:j].upper(), prev.line, prev.col, path))
                text = text[j:]
                offset += j

        n = len(text)
        while i < n:
            if open_quote is not None:
                j = i
                while j < n:
                    if text[j] == open_quote:
                        if j + 1 < n and text[j + 1] == open_quote:
                            open_value.append(text[i:j + 1])
                            j += 2
                            i = j
                            continue
                        open_value.append(text[i:j])
                        close_literal()
                        i = j + 1
                        break
                    j += 1
                else:
                    # Literal runs to the end of the line (fixed format pads to column 72).
                    open_value.append(text[i:])
                    i = n
                continue

            ch = text[i]
            col = offset + i
            if ch in " \t":
                i += 1
                continue
            if expect_pic:
                expect_pic = False
                j = i
                while j < n and text[j] not in " \t":
                    j += 1
                chunk = text[i:j]
                upper = chunk.upper()
                if upper == "IS":
                    tokens.append(Token(WORD, "IS", cl.number, col, path))
                    expect_pic = True
                    i = j
                    continue
                # A period, comma or semicolon ending the string is a separator.
                period = False
                if chunk[-1:] in (".", ",", ";"):
                    period = chunk[-1] == "."
                    chunk = chunk[:-1]
                if chunk:
                    tokens.append(Token(PIC, chunk.upper(), cl.number, col, path))
                if period:
                    tokens.append(Token(PUNCT, ".", cl.number, col + len(chunk), path))
                i = j
                continue
            if ch in ",;":
                if in_exec:
                    tokens.append(Token(PUNCT, ch, cl.number, col, path))
                i += 1
                continue
            if ch in "'\"":
                open_quote = ch
                open_value = []
                open_line = cl.number
                open_col = col
                i += 1
                continue
            if ch == "=" and text.startswith("==", i):
                end = text.find("==", i + 2)
                if end == -1:
                    tokens.append(Token(PSEUDO, text[i + 2:].strip(), cl.number, col, path))
                    i = n
                else:
                    tokens.append(Token(PSEUDO, text[i + 2:end].strip(), cl.number, col, path))
                    i = end + 2
                continue
            if ch == "." and i + 1 < n and text[i + 1].isdigit() and (i == 0 or text[i - 1] in " (+-*/=<>"):
                # decimal literal without a leading zero, e.g. ".10"
                j = i + 1
                while j < n and text[j].isdigit():
                    j += 1
                tokens.append(Token(WORD, text[i:j], cl.number, col, path))
                i = j
                continue
            if ch in _WORD_CHARS:
                j = i
                while j < n:
                    c = text[j]
                    if c == ":" and j > i and text[i] != ":":
                        break       # reference modification X(1:5); only :HOST-VAR and :TAG: words hold ':'
                    if c in _WORD_CHARS:
                        j += 1
                    elif c == "." and j + 1 < n and text[j + 1] in _WORD_CHARS and j > i:
                        # qualified SQL names (SCHEMA.TABLE) and decimal numbers
                        j += 1
                    else:
                        break
                word = text[i:j]
                if j < n and text[j] in "'\"" and word.upper() in _LITERAL_PREFIXES:
                    open_quote = text[j]
                    open_value = []
                    open_line = cl.number
                    open_col = col
                    i = j + 1
                    continue
                upper = word.upper()
                if upper == "EXEC":
                    in_exec = True
                elif upper == "END-EXEC":
                    in_exec = False
                elif upper in ("PIC", "PICTURE") and not in_exec:
                    expect_pic = True
                tokens.append(Token(WORD, upper, cl.number, col, path))
                i = j
                continue
            tokens.append(Token(PUNCT, ch, cl.number, col, path))
            i += 1

    if open_quote is not None:
        notes.append(Note("unterminated_literal", open_line))
        close_literal()
    return TokenStream(tokens, notes)


def render(tokens: list[Token]) -> str:
    """Readable source text for a token run (used for conditions and statement text)."""
    out: list[str] = []
    for tok in tokens:
        t = tok.text()
        if out and (t in (")", ",", ".", ":") or out[-1].endswith(("(", ":"))):
            out[-1] = out[-1] + t
        else:
            out.append(t)
    return " ".join(out)
