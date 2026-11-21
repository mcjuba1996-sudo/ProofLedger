"""Safe calculator: decimals and + - * / ( ) only. No eval, no names, no files."""
import re
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, localcontext

MAX_LEN = 200
MAX_DEPTH = 40
LIMIT = Decimal(10) ** 30
_TOK = re.compile(r"\s*(\d+(?:\.\d+)?|[()+\-*/])")


class CalcError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _tokenize(s: str):
    if not isinstance(s, str) or len(s) > MAX_LEN:
        raise CalcError("invalid_expression")
    s = s.rstrip()
    out, pos = [], 0
    while pos < len(s):
        m = _TOK.match(s, pos)
        if not m:
            raise CalcError("invalid_expression")
        out.append(m.group(1))
        pos = m.end()
    if not out:
        raise CalcError("invalid_expression")
    return out


class _Parser:
    def __init__(self, toks):
        self.t, self.i, self.depth = toks, 0, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self):
        v = self.peek()
        self.i += 1
        return v

    def expr(self):
        v = self.term()
        while self.peek() in ("+", "-"):
            op = self.take()
            r = self.term()
            v = v + r if op == "+" else v - r
            self.check(v)
        return v

    def term(self):
        v = self.unary()
        while self.peek() in ("*", "/"):
            op = self.take()
            r = self.unary()
            if op == "*":
                v = v * r
            else:
                if r == 0:
                    raise CalcError("division_by_zero")
                v = v / r
            self.check(v)
        return v

    def unary(self):
        p = self.peek()
        if p in ("-", "+"):
            self.take()
            self.depth += 1
            if self.depth > MAX_DEPTH:
                raise CalcError("invalid_expression")
            v = self.unary()
            self.depth -= 1
            return -v if p == "-" else v
        return self.atom()

    def atom(self):
        p = self.take()
        if p is None:
            raise CalcError("invalid_expression")
        if p == "(":
            self.depth += 1
            if self.depth > MAX_DEPTH:
                raise CalcError("invalid_expression")
            v = self.expr()
            if self.take() != ")":
                raise CalcError("invalid_expression")
            self.depth -= 1
            return v
        if p[0].isdigit():
            return Decimal(p)
        raise CalcError("invalid_expression")

    @staticmethod
    def check(v):
        if abs(v) > LIMIT:
            raise CalcError("overflow")


def calculate(expression: str) -> str:
    """Return the result as a normalized plain decimal string, or raise CalcError."""
    toks = _tokenize(expression)
    try:
        # Guard digits keep cancellation such as 1/3*3 stable. A fresh Context
        # avoids inheriting a caller's precision, rounding mode or traps.
        with localcontext(Context(prec=50)) as ctx:
            p = _Parser(toks)
            v = p.expr()
            if p.peek() is not None:
                raise CalcError("invalid_expression")
            _Parser.check(v)
            ctx.prec = 40
            v = +v
            if v == 0:
                return "0"
            return format(v.normalize(), "f")
    except (DivisionByZero, InvalidOperation):
        raise CalcError("invalid_expression")
    except RecursionError:
        raise CalcError("invalid_expression")
