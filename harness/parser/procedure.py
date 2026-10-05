"""Parse the PROCEDURE DIVISION into sections, paragraphs and a statement tree."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harness.cobol.tokens import LIT, PUNCT, WORD, Token, render
from harness.cobol.words import END_WORDS, RESERVED, VERBS, is_user_word, starts_statement
from harness.inventory.cobol_scan import _cics_options, sql_tables

MAINLINE = "(mainline)"

# Conditional phrases and the verbs that may carry them.
PHRASE_VERBS = {
    "READ": {"AT END", "NOT AT END", "INVALID KEY", "NOT INVALID KEY"},
    "RETURN": {"AT END", "NOT AT END"},
    "WRITE": {"INVALID KEY", "NOT INVALID KEY", "AT END-OF-PAGE", "NOT AT END-OF-PAGE"},
    "REWRITE": {"INVALID KEY", "NOT INVALID KEY"},
    "DELETE": {"INVALID KEY", "NOT INVALID KEY"},
    "START": {"INVALID KEY", "NOT INVALID KEY"},
    "ADD": {"ON SIZE ERROR", "NOT ON SIZE ERROR"},
    "SUBTRACT": {"ON SIZE ERROR", "NOT ON SIZE ERROR"},
    "MULTIPLY": {"ON SIZE ERROR", "NOT ON SIZE ERROR"},
    "DIVIDE": {"ON SIZE ERROR", "NOT ON SIZE ERROR"},
    "COMPUTE": {"ON SIZE ERROR", "NOT ON SIZE ERROR"},
    "CALL": {"ON EXCEPTION", "NOT ON EXCEPTION", "ON OVERFLOW"},
    "STRING": {"ON OVERFLOW", "NOT ON OVERFLOW"},
    "UNSTRING": {"ON OVERFLOW", "NOT ON OVERFLOW"},
    "ACCEPT": {"ON EXCEPTION", "NOT ON EXCEPTION"},
    "DISPLAY": {"ON EXCEPTION", "NOT ON EXCEPTION"},
}
TERMINAL_VERBS = {"GOBACK", "STOP RUN", "EXIT PROGRAM"}
CICS_HANDLERS = {"HANDLE"}     # EXEC CICS HANDLE CONDITION / AID / ABEND name procedures


@dataclass
class Paragraph:
    name: str
    section: str | None
    line: int
    file: str
    statements: list[dict] = field(default_factory=list)
    end_line: int = 0
    end_file: str = ""


@dataclass
class Section:
    name: str
    line: int
    file: str
    paragraphs: list[str] = field(default_factory=list)


@dataclass
class ProcedureDivision:
    using: list[str] = field(default_factory=list)
    returning: str | None = None
    sections: list[Section] = field(default_factory=list)
    paragraphs: list[Paragraph] = field(default_factory=list)
    problems: list[dict] = field(default_factory=list)
    line: int = 0
    file: str = ""


class _Parser:
    def __init__(self, tokens: list[Token], data_names: set[str]) -> None:
        self.t = tokens
        self.n = len(tokens)
        self.i = 0
        self.data_names = data_names
        self.problems: list[dict] = []

    # ------------------------------------------------------------ helpers
    def cur(self, k: int = 0) -> Token | None:
        j = self.i + k
        return self.t[j] if j < self.n else None

    def at_word(self, *values: str, k: int = 0) -> bool:
        tok = self.cur(k)
        return tok is not None and tok.is_word(*values)

    def at_period(self) -> bool:
        tok = self.cur()
        return tok is not None and tok.is_punct(".")

    def phrase_at(self, j: int) -> tuple[str, int] | None:
        """Conditional phrase starting at token j: (normalised name, token count)."""
        t = self.t
        k = j
        neg = False
        if k < self.n and t[k].is_word("NOT"):
            neg = True
            k += 1
        start = k
        if k < self.n and t[k].is_word("AT"):
            k += 1
        if k < self.n and t[k].is_word("END") and not (k + 1 < self.n and t[k + 1].is_word("PROGRAM")):
            name = "AT END"
        elif k < self.n and t[k].is_word("END-OF-PAGE", "EOP"):
            name = "AT END-OF-PAGE"
        else:
            k = start
            if k < self.n and t[k].is_word("INVALID"):
                k += 1
                if k < self.n and t[k].is_word("KEY"):
                    k += 1
                return ("NOT INVALID KEY" if neg else "INVALID KEY"), k - j
            if k < self.n and t[k].is_word("ON"):
                k += 1
            if k < self.n and t[k].is_word("SIZE") and k + 1 < self.n and t[k + 1].is_word("ERROR"):
                return ("NOT ON SIZE ERROR" if neg else "ON SIZE ERROR"), k + 2 - j
            if k < self.n and t[k].is_word("EXCEPTION"):
                return ("NOT ON EXCEPTION" if neg else "ON EXCEPTION"), k + 1 - j
            if k < self.n and t[k].is_word("OVERFLOW"):
                return ("NOT ON OVERFLOW" if neg else "ON OVERFLOW"), k + 1 - j
            return None
        return ("NOT " + name if neg else name), k + 1 - j

    def stop_here(self, j: int | None = None) -> bool:
        j = self.i if j is None else j
        if j >= self.n:
            return True
        tok = self.t[j]
        if tok.is_punct("."):
            return True
        if tok.kind == WORD and (tok.value in ("ELSE", "WHEN") or tok.value in END_WORDS):
            return True
        if tok.is_word("END") and j + 1 < self.n and self.t[j + 1].is_word("PROGRAM", "DECLARATIVES"):
            return True
        return self.phrase_at(j) is not None

    def statement_start(self, j: int) -> bool:
        return j < self.n and starts_statement(self.t, j)

    def problem(self, code: str, tok: Token, **details: Any) -> None:
        self.problems.append({"code": code, "line": tok.line, "file": tok.file, **details})

    # --------------------------------------------------------- statements
    def block(self) -> list[dict]:
        stmts: list[dict] = []
        while self.i < self.n and not self.stop_here():
            if self.statement_start(self.i):
                stmts.append(self.statement())
            else:
                stmts.append(self.unrecognised())
        return stmts

    def unrecognised(self) -> dict:
        start = self.cur()
        toks = []
        while self.i < self.n and not self.stop_here() and not self.statement_start(self.i):
            toks.append(self.t[self.i])
            self.i += 1
        self.problem("unrecognised_statement", start, text=render(toks)[:120])
        return self.node("?", start, toks)

    def node(self, verb: str, first: Token, toks: list[Token], **extra: Any) -> dict:
        last = toks[-1] if toks else first
        n: dict[str, Any] = {"verb": verb, "line": first.line, "file": first.file,
                             "end_line": last.line if last.file == first.file else first.line,
                             "text": render(toks)}
        n.update(extra)
        return n

    def statement(self) -> dict:
        tok = self.cur()
        verb = tok.value
        handler = getattr(self, f"_{verb.lower()}", None)
        if handler is not None:
            return handler()
        return self.simple(verb)

    def simple(self, verb: str) -> dict:
        first = self.cur()
        toks = [first]
        self.i += 1
        if verb == "EXIT" and self.at_word("PERFORM", "PARAGRAPH", "SECTION", "PROGRAM", "METHOD"):
            # EXIT PERFORM [CYCLE] etc. are single statements, not EXIT + PERFORM
            toks.append(self.cur())
            self.i += 1
            if self.at_word("CYCLE"):
                toks.append(self.cur())
                self.i += 1
        while self.i < self.n and not self.stop_here() and not self.statement_start(self.i):
            toks.append(self.t[self.i])
            self.i += 1
        if verb == "STOP" and len(toks) > 1 and toks[1].is_word("RUN"):
            verb = "STOP RUN"
        elif verb == "EXIT" and len(toks) > 1 and toks[1].kind == WORD:
            verb = f"EXIT {toks[1].value}"
        elif verb == "NEXT":
            verb = "NEXT SENTENCE"
        node = self.node(verb, first, toks)
        reads, writes = data_flow(verb.split()[0], toks)
        node["reads"], node["writes"] = reads, writes
        phrases = PHRASE_VERBS.get(verb)
        if phrases:
            self.phrases(node, phrases)
        end = f"END-{verb.split()[0]}"
        if self.at_word(end):
            self.i += 1
        return node

    def phrases(self, node: dict, allowed: set[str]) -> None:
        found = []
        while self.i < self.n:
            ph = self.phrase_at(self.i)
            if ph is None or ph[0] not in allowed:
                break
            name, count = ph
            self.i += count
            found.append({"phrase": name, "statements": self.block()})
        if found:
            node["phrases"] = found

    # ---------------------------------------------------------- compound
    def _if(self) -> dict:
        first = self.cur()
        self.i += 1
        cond: list[Token] = []
        while self.i < self.n and not self.stop_here() and not self.statement_start(self.i) \
                and not self.at_word("THEN"):
            cond.append(self.t[self.i])
            self.i += 1
        if self.at_word("THEN"):
            self.i += 1
        then = self.block()
        node = self.node("IF", first, [first] + cond, condition=render(cond), then=then)
        node["reads"] = names_in(cond)
        if self.at_word("ELSE"):
            self.i += 1
            node["else"] = self.block()
        if self.at_word("END-IF"):
            self.i += 1
            node["explicit_end"] = True
        return node

    def _evaluate(self) -> dict:
        first = self.cur()
        self.i += 1
        subj: list[Token] = []
        while self.i < self.n and not self.at_word("WHEN") and not self.stop_here():
            subj.append(self.t[self.i])
            self.i += 1
        branches: list[dict] = []
        while self.at_word("WHEN"):
            when_tok = self.cur()
            conditions: list[str] = []
            reads: list[str] = []
            other = False
            while self.at_word("WHEN"):
                self.i += 1
                if self.at_word("OTHER"):
                    other = True
                    self.i += 1
                    continue
                obj: list[Token] = []
                while self.i < self.n and not self.statement_start(self.i) and not self.at_word("WHEN") \
                        and not self.at_period() and not self.at_word("END-EVALUATE"):
                    obj.append(self.t[self.i])
                    self.i += 1
                conditions.append(render(obj))
                reads += names_in(obj)
            body = self.block()
            branches.append({"line": when_tok.line, "file": when_tok.file, "conditions": conditions,
                             "other": other, "reads": sorted(set(reads)), "statements": body})
        subjects = [render(s) for s in _split_also(subj)]
        node = self.node("EVALUATE", first, [first] + subj, subjects=subjects, branches=branches)
        node["reads"] = names_in(subj)
        if self.at_word("END-EVALUATE"):
            self.i += 1
            node["explicit_end"] = True
        return node

    def _perform(self) -> dict:
        first = self.cur()
        self.i += 1
        nxt = self.cur()
        after = self.cur(1)
        out_of_line = (
            nxt is not None and nxt.kind == WORD
            and (is_user_word(nxt.value) or nxt.value.isdigit())
            and not (after is not None and after.is_word("TIMES"))
            and not self.statement_start(self.i)
        )
        head: list[Token] = [first]
        node: dict[str, Any] = {}
        if out_of_line:
            target = self._procedure_name(head)
            node["target"] = target
            if self.at_word("THRU", "THROUGH"):
                head.append(self.cur())
                self.i += 1
                node["thru"] = self._procedure_name(head)
        loop = self._loop_options(head)
        if out_of_line:
            n = self.node("PERFORM", first, head, inline=False, loop=loop, **node)
            n["reads"] = loop.pop("_reads", [])
            n["writes"] = loop.pop("_writes", [])
            return n
        body = self.block()
        n = self.node("PERFORM", first, head, inline=True, loop=loop, body=body)
        n["reads"] = loop.pop("_reads", [])
        n["writes"] = loop.pop("_writes", [])
        if self.at_word("END-PERFORM"):
            self.i += 1
        else:
            self.problem("inline_perform_without_end_perform", first)
        return n

    def _procedure_name(self, head: list[Token]) -> str:
        tok = self.cur()
        head.append(tok)
        self.i += 1
        name = tok.value
        if self.at_word("OF", "IN") and self.cur(1) is not None:
            head.extend([self.cur(), self.cur(1)])
            name = f"{name} OF {self.cur(1).value}"
            self.i += 2
        return name

    def _loop_options(self, head: list[Token]) -> dict:
        loop: dict[str, Any] = {"type": "none"}
        reads: list[str] = []
        writes: list[str] = []
        tok = self.cur()
        if tok is not None and self.cur(1) is not None and self.cur(1).is_word("TIMES") \
                and tok.kind in (WORD, LIT):
            loop = {"type": "times", "count": tok.value}
            reads += names_in([tok])
            head.extend([tok, self.cur(1)])
            self.i += 2
        while True:
            if self.at_word("WITH") and self.at_word("TEST", k=1):
                head.append(self.cur())
                self.i += 1
            if self.at_word("TEST"):
                head.append(self.cur())
                self.i += 1
                if self.at_word("BEFORE", "AFTER"):
                    loop["test"] = self.cur().value
                    head.append(self.cur())
                    self.i += 1
                continue
            break
        if self.at_word("VARYING"):
            loop["type"] = "varying"
            loop["varying"] = []
            while self.at_word("VARYING", "AFTER"):
                head.append(self.cur())
                self.i += 1
                spec: dict[str, Any] = {}
                part: list[Token] = []
                key = "variable"
                while self.i < self.n and not self.stop_here() and not self.statement_start(self.i) \
                        and not self.at_word("AFTER"):
                    tk = self.cur()
                    if tk.is_word("FROM", "BY", "UNTIL"):
                        spec[key] = render(part)
                        if key == "variable":
                            writes += names_in(part)
                        else:
                            reads += names_in(part)
                        part = []
                        key = tk.value.lower()
                    else:
                        part.append(tk)
                    head.append(tk)
                    self.i += 1
                spec[key] = render(part)
                reads += names_in(part)
                loop["varying"].append(spec)
            loop["condition"] = loop["varying"][0].get("until")
        elif self.at_word("UNTIL"):
            head.append(self.cur())
            self.i += 1
            cond: list[Token] = []
            while self.i < self.n and not self.stop_here() and not self.statement_start(self.i):
                cond.append(self.cur())
                head.append(self.cur())
                self.i += 1
            loop["type"] = "until"
            loop["condition"] = render(cond)
            reads += names_in(cond)
        loop["_reads"] = sorted(set(reads))
        loop["_writes"] = sorted(set(writes))
        return loop

    def _search(self) -> dict:
        first = self.cur()
        toks = [first]
        self.i += 1
        while self.i < self.n and not self.stop_here() and not self.statement_start(self.i) \
                and self.phrase_at(self.i) is None:
            toks.append(self.cur())
            self.i += 1
        node = self.node("SEARCH", first, toks)
        if self.phrase_at(self.i) and self.phrase_at(self.i)[0] == "AT END":
            self.i += self.phrase_at(self.i)[1]
            node["at_end"] = self.block()
        branches = []
        while self.at_word("WHEN"):
            when_tok = self.cur()
            self.i += 1
            cond: list[Token] = []
            while self.i < self.n and not self.stop_here() and not self.statement_start(self.i):
                cond.append(self.cur())
                self.i += 1
            branches.append({"line": when_tok.line, "file": when_tok.file, "conditions": [render(cond)],
                             "other": False, "reads": names_in(cond), "statements": self.block()})
        node["branches"] = branches
        node["reads"] = names_in(toks[1:])
        if self.at_word("END-SEARCH"):
            self.i += 1
        return node

    def _go(self) -> dict:
        first = self.cur()
        toks = [first]
        self.i += 1
        if self.at_word("TO"):
            toks.append(self.cur())
            self.i += 1
        targets = []
        depending = None
        while self.i < self.n and not self.stop_here() and not self.statement_start(self.i):
            tk = self.cur()
            toks.append(tk)
            self.i += 1
            if tk.is_word("DEPENDING"):
                if self.at_word("ON"):
                    toks.append(self.cur())
                    self.i += 1
                if self.cur() is not None:
                    depending = self.cur().value
                    toks.append(self.cur())
                    self.i += 1
                break
            if tk.kind == WORD and (is_user_word(tk.value) or tk.value.isdigit()):
                targets.append(tk.value)
        node = self.node("GO TO", first, toks, targets=targets)
        if depending:
            node["depending_on"] = depending
            node["reads"] = [depending]
        return node

    def _alter(self) -> dict:
        first = self.cur()
        toks = [first]
        self.i += 1
        pairs = []
        words = []
        while self.i < self.n and not self.stop_here() and not self.statement_start(self.i):
            tk = self.cur()
            toks.append(tk)
            self.i += 1
            if tk.kind == WORD and is_user_word(tk.value):
                words.append(tk.value)
        for k in range(0, len(words) - 1, 2):
            pairs.append({"paragraph": words[k], "proceed_to": words[k + 1]})
        return self.node("ALTER", first, toks, alterations=pairs)

    def _call(self) -> dict:
        first = self.cur()
        toks = [first]
        self.i += 1
        while self.i < self.n and not self.stop_here() and not self.statement_start(self.i):
            toks.append(self.cur())
            self.i += 1
        target = toks[1] if len(toks) > 1 else None
        node = self.node("CALL", first, toks)
        if target is not None and target.kind == LIT:
            node["program"] = target.value.strip().upper()
        elif target is not None:
            node["program_variable"] = target.value
        using = []
        if any(t.is_word("USING") for t in toks):
            k = next(idx for idx, t in enumerate(toks) if t.is_word("USING"))
            using = names_in(toks[k + 1:])
        node["using"] = using
        node["reads"] = using + ([node["program_variable"]] if "program_variable" in node else [])
        node["writes"] = list(using)
        self.phrases(node, PHRASE_VERBS["CALL"])
        if self.at_word("END-CALL"):
            self.i += 1
        return node

    def _exec(self) -> dict:
        first = self.cur()
        toks = [first]
        self.i += 1
        while self.i < self.n and not self.cur().is_word("END-EXEC"):
            toks.append(self.cur())
            self.i += 1
        if self.i < self.n:
            toks.append(self.cur())
            self.i += 1
        lang = toks[1].value if len(toks) > 1 else ""
        body = toks[2:-1] if toks[-1].is_word("END-EXEC") else toks[2:]
        command = " ".join(t.value for t in body[:2] if t.kind == WORD) if lang == "SQL" else \
            (body[0].value if body and body[0].kind == WORD else "")
        if lang == "SQL" and body and body[0].kind == WORD:
            command = body[0].value
            if command in ("DECLARE", "BEGIN", "END") and len(body) > 1:
                command = f"{body[0].value} {body[1].value}" if body[1].kind == WORD else command
        node = self.node("EXEC", first, toks, language=lang, command=command)
        reads, writes = exec_flow(lang, body)
        node["reads"], node["writes"] = reads, writes
        if lang == "SQL":
            node["tables"] = [{"table": t, "operation": op} for t, op in sql_tables(body)]
        elif lang == "CICS":
            opts = _cics_options(body)
            node["options"] = {k: render(v) for k, v in opts.items()}
            if command == "HANDLE":
                node["handlers"] = [render(v) for k, v in opts.items() if v and k not in ("CONDITION", "AID", "ABEND")
                                    and len(v) == 1 and v[0].kind == WORD]
                if "LABEL" in opts and opts["LABEL"]:
                    node["handlers"].append(render(opts["LABEL"]))
        return node


def _split_also(toks: list[Token]) -> list[list[Token]]:
    parts: list[list[Token]] = [[]]
    for t in toks:
        if t.is_word("ALSO"):
            parts.append([])
        else:
            parts[-1].append(t)
    return [p for p in parts if p]


def names_in(toks: list[Token]) -> list[str]:
    """User-defined words in an expression, skipping FUNCTION names and qualifiers."""
    out: list[str] = []
    skip_next = False
    for t in toks:
        if t.kind != WORD:
            continue
        if skip_next:
            skip_next = False
            continue
        if t.value in ("FUNCTION", "DFHRESP", "DFHVALUE"):
            skip_next = True       # function / CICS translator argument names are not data
            continue
        if t.value in ("OF", "IN"):
            skip_next = True       # qualifier: keep the leading name only
            continue
        v = t.value[1:] if t.value.startswith(":") else t.value
        if is_user_word(v) and v not in RESERVED:
            out.append(v)
    seen = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def data_flow(verb: str, toks: list[Token]) -> tuple[list[str], list[str]]:
    """Data items read and written by a simple statement."""
    words = toks[1:]

    def split_at(*kw: str) -> tuple[list[Token], list[Token], str | None]:
        for k, t in enumerate(words):
            if t.is_word(*kw):
                return words[:k], words[k + 1:], t.value
        return words, [], None

    reads: list[str] = []
    writes: list[str] = []
    if verb == "MOVE":
        left, right, _ = split_at("TO")
        reads, writes = names_in(left), names_in(right)
    elif verb == "COMPUTE":
        k = next((idx for idx, t in enumerate(words) if t.is_punct("=") or t.is_word("EQUAL")), len(words))
        writes, reads = names_in(words[:k]), names_in(words[k + 1:])
    elif verb in ("ADD", "SUBTRACT", "MULTIPLY", "DIVIDE"):
        # ADD a TO b [GIVING c] [REMAINDER r]: operands are read; b (or c, r) is written
        before_giving, after_giving, giving = split_at("GIVING")
        sep = {"ADD": ("TO",), "SUBTRACT": ("FROM",), "MULTIPLY": ("BY",), "DIVIDE": ("INTO", "BY")}[verb]
        k = next((idx for idx, t in enumerate(before_giving) if t.is_word(*sep)), len(before_giving))
        left, right = before_giving[:k], before_giving[k + 1:]
        reads = names_in(left) + names_in(right)
        if giving:
            m = next((idx for idx, t in enumerate(after_giving) if t.is_word("REMAINDER")), len(after_giving))
            writes = names_in(after_giving[:m]) + names_in(after_giving[m + 1:])
        else:
            writes = names_in(right)
    elif verb in ("INITIALIZE", "ACCEPT", "SET"):
        left, right, kw = split_at("TO", "FROM", "REPLACING", "UP", "DOWN")
        writes, reads = names_in(left), names_in(right)
    elif verb == "READ":
        left, right, _ = split_at("INTO")
        writes = names_in(right[:1])
        key_l, key_r, _ = split_at("KEY")
        reads = names_in(key_r[1:2] if key_r and key_r[0].is_word("IS") else key_r[:1])
    elif verb in ("WRITE", "REWRITE", "RELEASE"):
        left, right, _ = split_at("FROM")
        writes, reads = names_in(left[:1]), names_in(right[:1])
    elif verb == "STRING":
        left, right, _ = split_at("INTO")
        reads, writes = names_in([t for t in left if not t.is_word("DELIMITED", "BY", "SIZE")]), names_in(right[:1])
    elif verb == "UNSTRING":
        left, right, _ = split_at("INTO")
        reads, writes = names_in(left[:1]), names_in(right)
    elif verb == "INSPECT":
        writes = names_in(words[:1])
        reads = names_in(words[1:])
    elif verb in ("DISPLAY", "EVALUATE", "IF"):
        reads = names_in(words)
    elif verb in ("OPEN", "CLOSE", "START", "DELETE", "RETURN"):
        reads = []
    else:
        reads = names_in(words)
    return reads, writes


def exec_flow(lang: str, body: list[Token]) -> tuple[list[str], list[str]]:
    reads: list[str] = []
    writes: list[str] = []
    if lang == "SQL":
        # Every SQL statement sets the SQLCA (SQLCODE, SQLSTATE ...).
        writes += ["SQLCODE", "SQLSTATE"]
        into = False
        for t in body:
            if t.is_word("INTO"):
                into = True
                continue
            if t.kind == WORD and t.value.startswith(":"):
                (writes if into else reads).append(t.value[1:])
            elif t.kind == WORD and into and t.value in ("FROM", "WHERE", "VALUES", "FOR", "WITH"):
                into = False
    elif lang == "CICS":
        opts = _cics_options(body)
        for key, arg in opts.items():
            names = names_in(arg)
            if key in ("INTO", "SET", "RESP", "RESP2", "ABCODE", "USERID", "TERMID",
                       "APPLID", "SYSID", "OPID", "DATASTRING", "TIME", "YYYYMMDD", "ABSTIME"):
                writes += names
            elif key in ("FROM", "RIDFLD", "COMMAREA", "PROGRAM", "TRANSID", "MAP", "MAPSET", "FILE",
                         "QUEUE", "LENGTH", "ITEM", "INTERVAL", "TEXT"):
                reads += names
    return sorted(set(reads)), sorted(set(writes))


def parse_procedure(tokens: list[Token], data_names: set[str]) -> ProcedureDivision:
    """tokens start at the PROCEDURE keyword of PROCEDURE DIVISION."""
    p = _Parser(tokens, data_names)
    result = ProcedureDivision(line=tokens[0].line if tokens else 0, file=tokens[0].file if tokens else "")
    # header
    p.i = 2
    if p.at_word("USING"):
        p.i += 1
        while p.i < p.n and not p.at_period() and not p.at_word("RETURNING"):
            tok = p.cur()
            if tok.kind == WORD and is_user_word(tok.value):
                result.using.append(tok.value)
            p.i += 1
    if p.at_word("RETURNING") and p.cur(1) is not None:
        result.returning = p.cur(1).value
        p.i += 2
    while p.i < p.n and not p.at_period():
        p.i += 1
    p.i += 1

    section: Section | None = None
    para: Paragraph | None = None
    while p.i < p.n:
        tok = p.cur()
        if tok.is_punct("."):
            p.i += 1
            continue
        if tok.is_word("DECLARATIVES") or (tok.is_word("END") and p.at_word("DECLARATIVES", k=1)):
            p.i += 1 if tok.value == "DECLARATIVES" else 2
            continue
        if tok.is_word("END") and p.at_word("PROGRAM", k=1):
            break
        nxt = p.cur(1)
        if tok.kind == WORD and (is_user_word(tok.value) or tok.value.isdigit()) and nxt is not None \
                and nxt.is_word("SECTION"):
            section = Section(tok.value, tok.line, tok.file)
            result.sections.append(section)
            p.i += 2
            if p.cur() is not None and p.cur().kind == WORD and p.cur().value.isdigit():
                p.i += 1
            continue
        if tok.kind == WORD and (is_user_word(tok.value) or tok.value.isdigit()) \
                and not p.statement_start(p.i) and (nxt is None or nxt.is_punct(".")):
            if nxt is None:
                p.problem("paragraph_name_without_period", tok)
            para = Paragraph(tok.value, section.name if section else None, tok.line, tok.file)
            result.paragraphs.append(para)
            if section:
                section.paragraphs.append(para.name)
            p.i += 2
            continue
        if para is None:
            para = Paragraph(MAINLINE, None, tok.line, tok.file)
            result.paragraphs.append(para)
        stmts = p.block()
        if not stmts and not p.at_period():
            # A stray scope terminator or phrase keyword: report and move on.
            p.problem("unexpected_token", tok, text=tok.text())
            p.i += 1
            continue
        para.statements.extend(stmts)

    for k, para in enumerate(result.paragraphs):
        last = _last_location(para)
        para.end_line, para.end_file = last
    result.problems = p.problems
    return result


def _last_location(para: Paragraph) -> tuple[int, str]:
    best = (para.line, para.file)

    def walk(stmts: list[dict]) -> None:
        nonlocal best
        for s in stmts:
            if s["file"] == para.file and s.get("end_line", s["line"]) > best[0]:
                best = (s.get("end_line", s["line"]), s["file"])
            for key in ("then", "else", "body", "at_end"):
                walk(s.get(key, []))
            for b in s.get("branches", []):
                walk(b["statements"])
            for ph in s.get("phrases", []):
                walk(ph["statements"])

    walk(para.statements)
    return best


def iter_statements(stmts: list[dict]):
    """Depth-first walk over a statement tree."""
    for s in stmts:
        yield s
        for key in ("then", "else", "body", "at_end"):
            yield from iter_statements(s.get(key, []))
        for b in s.get("branches", []):
            yield from iter_statements(b["statements"])
        for ph in s.get("phrases", []):
            yield from iter_statements(ph["statements"])
