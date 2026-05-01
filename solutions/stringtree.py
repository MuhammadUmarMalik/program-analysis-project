"""Small utility for building and simplifying a tree of string operations.

This module provides light-weight expression nodes to represent string
operations (constant strings, concatenation, substring) and helpers to
construct, simplify and attempt to concretize expressions.

Intended use: the abstract interpreter can attach a `StringExpr` to heap
string objects (e.g. in `state.heap[oid]["fields"]["expr"]`). Tests or a
post-processing step can then inspect the expression tree and attempt to
recover concrete strings when possible.
"""

from dataclasses import dataclass
from typing import Optional, Union


class StringExpr:
    """Base class for string expression nodes."""
    pass


@dataclass
class ConstExpr(StringExpr):
    value: str

    def __repr__(self):
        return f"Const({self.value!r})"


@dataclass
class ConcatExpr(StringExpr):
    left: StringExpr
    right: StringExpr

    def __repr__(self):
        return f"Concat({self.left!r}, {self.right!r})"


@dataclass
class SubstringExpr(StringExpr):
    base: StringExpr
    i: Optional[int]  # start index, or None if unknown
    j: Optional[int]  # end index (exclusive), or None if unknown

    def __repr__(self):
        return f"Substring({self.base!r}, {self.i}, {self.j})"


@dataclass
class UnknownExpr(StringExpr):
    reason: str = "unknown"

    def __repr__(self):
        return f"Unknown({self.reason})"


def const(s: str) -> ConstExpr:
    return ConstExpr(s)


def concat(a: StringExpr, b: StringExpr) -> StringExpr:
    # simplify trivial cases
    if isinstance(a, ConstExpr) and a.value == "":
        return b
    if isinstance(b, ConstExpr) and b.value == "":
        return a
    # flatten nested concats of constants
    if isinstance(a, ConstExpr) and isinstance(b, ConstExpr):
        return ConstExpr(a.value + b.value)
    return ConcatExpr(a, b)


def substring(base: StringExpr, i: Optional[int], j: Optional[int]) -> StringExpr:
    # if base is constant and indices concrete, return constant slice
    if isinstance(base, ConstExpr) and i is not None and j is not None:
        # guard indices
        try:
            s = base.value[i:j]
            return ConstExpr(s)
        except Exception:
            return UnknownExpr("substring-error")
    # if indices are None and base is substring, try to compose
    return SubstringExpr(base, i, j)


def unknown(reason: str = "unknown") -> UnknownExpr:
    return UnknownExpr(reason)


def eval_expr(expr: StringExpr) -> Optional[str]:
    """Try to evaluate the expression to a concrete string.

    Returns the concrete string if all parts are concrete, otherwise `None`.
    """
    if isinstance(expr, ConstExpr):
        return expr.value
    if isinstance(expr, ConcatExpr):
        L = eval_expr(expr.left)
        if L is None:
            return None
        R = eval_expr(expr.right)
        if R is None:
            return None
        return L + R
    if isinstance(expr, SubstringExpr):
        base_val = eval_expr(expr.base)
        if base_val is None or expr.i is None or expr.j is None:
            return None
        try:
            return base_val[expr.i:expr.j]
        except Exception:
            return None
    if isinstance(expr, UnknownExpr):
        return None
    return None


def simplify(expr: StringExpr) -> StringExpr:
    """Return a simplified version of the expression.

    Currently performs only a few local simplifications (constant folding,
    dropping empty concatenations).
    """
    if isinstance(expr, ConstExpr) or isinstance(expr, UnknownExpr):
        return expr
    if isinstance(expr, ConcatExpr):
        L = simplify(expr.left)
        R = simplify(expr.right)
        if isinstance(L, ConstExpr) and L.value == "":
            return R
        if isinstance(R, ConstExpr) and R.value == "":
            return L
        if isinstance(L, ConstExpr) and isinstance(R, ConstExpr):
            return ConstExpr(L.value + R.value)
        return ConcatExpr(L, R)
    if isinstance(expr, SubstringExpr):
        B = simplify(expr.base)
        if isinstance(B, ConstExpr) and expr.i is not None and expr.j is not None:
            try:
                return ConstExpr(B.value[expr.i:expr.j])
            except Exception:
                return UnknownExpr("substring-error")
        return SubstringExpr(B, expr.i, expr.j)
    return expr


def pretty(expr: Optional[StringExpr], indent: int = 0) -> str:
    """Return a human-friendly, indented multi-line representation of the
    expression tree. If `expr` is None, returns the literal string "None".

    `indent` is the number of spaces to use for the current indentation
    level (used internally for recursion).
    """
    if expr is None:
        return "None"

    sp = " " * indent

    if isinstance(expr, ConstExpr):
        return sp + repr(expr)

    if isinstance(expr, ConcatExpr):
        left = pretty(expr.left, indent + 2)
        right = pretty(expr.right, indent + 2)
        return f"{sp}Concat(\n{left},\n{right}\n{sp})"

    if isinstance(expr, SubstringExpr):
        base = pretty(expr.base, indent + 2)
        return f"{sp}Substring(\n{base},\n{sp}  {expr.i!r}, {expr.j!r}\n{sp})"

    if isinstance(expr, UnknownExpr):
        return sp + repr(expr)

    # fallback to repr for unknown node-types
    return sp + repr(expr)


__all__ = [
    "StringExpr",
    "ConstExpr",
    "ConcatExpr",
    "SubstringExpr",
    "UnknownExpr",
    "const",
    "concat",
    "substring",
    "unknown",
    "eval_expr",
    "simplify",
    "pretty",
]
