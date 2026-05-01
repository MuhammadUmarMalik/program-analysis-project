"""
string_abs2.py — Prefix + Length abstract domain for strings (StringAbs2).

Abstract value for a Java String:
  prefix    : Optional[str]  — longest known prefix; None means unknown
  length    : Interval       — over-approximation of |s|
  may_be_null: bool          — True if the reference may be null

Lattice order (⊑):
  BOT ⊑ everything ⊑ TOP
  BOT  = (prefix=None, length=BOT, may_be_null=False)
  TOP  = (prefix=None, length=[0,+∞), may_be_null=True)
  NULL = (prefix=None, length=BOT, may_be_null=True)

Abstraction function α(s):
  α(s) = StringAbs2(prefix=s, length=[|s|,|s|], may_be_null=False)
  α(null) = NULL
"""

from __future__ import annotations

from dataclasses import dataclass
from math import inf
from typing import Optional

POS_INF = inf


# ---------------------------------------------------------------------------
# Interval (re-used from abstract_interpreter; duplicated here to keep this
# module self-contained for testing)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Interval:
    lo: Optional[float]
    hi: Optional[float]

    @staticmethod
    def bot() -> "Interval":
        return Interval(None, None)

    @staticmethod
    def top() -> "Interval":
        return Interval(-POS_INF, POS_INF)

    @staticmethod
    def const(v: int) -> "Interval":
        return Interval(float(v), float(v))

    @staticmethod
    def nonneg() -> "Interval":
        return Interval(0.0, POS_INF)

    @property
    def is_bot(self) -> bool:
        return self.lo is None and self.hi is None

    def join(self, other: "Interval") -> "Interval":
        if self.is_bot:
            return other
        if other.is_bot:
            return self
        return Interval(min(self.lo, other.lo), max(self.hi, other.hi))  # type: ignore[arg-type]

    def add(self, other: "Interval") -> "Interval":
        if self.is_bot or other.is_bot:
            return Interval.bot()
        return Interval(self.lo + other.lo, self.hi + other.hi)  # type: ignore[operator]

    def sub_interval(self, other: "Interval") -> "Interval":
        """[a,b] - [c,d] = [a-d, b-c]"""
        if self.is_bot or other.is_bot:
            return Interval.bot()
        return Interval(self.lo - other.hi, self.hi - other.lo)  # type: ignore[operator]

    def clamp_nonneg(self) -> "Interval":
        if self.is_bot:
            return self
        lo = max(0.0, self.lo)  # type: ignore[type-var]
        hi = max(0.0, self.hi)  # type: ignore[type-var]
        if lo > hi:
            return Interval.bot()
        return Interval(lo, hi)

    def __str__(self) -> str:
        if self.is_bot:
            return "⊥"
        def _b(x: float) -> str:
            if x == -POS_INF:
                return "-∞"
            if x == POS_INF:
                return "+∞"
            return str(int(x))
        return f"[{_b(self.lo)},{_b(self.hi)}]"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# StringAbs2
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StringAbs2:
    """Abstract value for a (possibly-null) Java String reference."""

    prefix: Optional[str]   # known prefix (None = unknown)
    length: Interval         # abstract length of the string value
    may_be_null: bool        # True ↔ null is in the abstract set

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @staticmethod
    def bot() -> "StringAbs2":
        """Bottom: empty set — no strings and not null."""
        return StringAbs2(None, Interval.bot(), False)

    @staticmethod
    def top() -> "StringAbs2":
        """Top: any string or null."""
        return StringAbs2(None, Interval.nonneg(), True)

    @staticmethod
    def null() -> "StringAbs2":
        """Exactly the null reference."""
        return StringAbs2(None, Interval.bot(), True)

    @staticmethod
    def from_const(s: str) -> "StringAbs2":
        """Abstraction of a single concrete string (not null)."""
        return StringAbs2(s, Interval.const(len(s)), False)

    @staticmethod
    def unknown_nonnull() -> "StringAbs2":
        """A non-null string with unknown content and unknown length."""
        return StringAbs2(None, Interval.nonneg(), False)

    # ------------------------------------------------------------------
    # Lattice properties
    # ------------------------------------------------------------------

    @property
    def is_bot(self) -> bool:
        """True iff this abstract value represents the empty set."""
        return self.length.is_bot and not self.may_be_null

    @property
    def is_null(self) -> bool:
        """True iff this abstract value is exactly {null}."""
        return self.length.is_bot and self.may_be_null

    @property
    def definitely_nonnull(self) -> bool:
        return not self.may_be_null

    # ------------------------------------------------------------------
    # Abstraction function α
    # ------------------------------------------------------------------

    @staticmethod
    def alpha(s: Optional[str]) -> "StringAbs2":
        """α: concrete string (or None for null) → abstract value."""
        if s is None:
            return StringAbs2.null()
        return StringAbs2.from_const(s)

    # ------------------------------------------------------------------
    # Join (⊔)
    # ------------------------------------------------------------------

    def join(self, other: "StringAbs2") -> "StringAbs2":
        """Least upper bound in the lattice."""
        # identity cases
        if self.is_bot:
            return other
        if other.is_bot:
            return self

        # common prefix: longest prefix shared by both
        prefix = _common_prefix(self.prefix, other.prefix)

        # join length intervals
        length = self.length.join(other.length)

        # null flag
        may_be_null = self.may_be_null or other.may_be_null

        return StringAbs2(prefix, length, may_be_null)

    # ------------------------------------------------------------------
    # Null check
    # ------------------------------------------------------------------

    def check_null(self) -> tuple[bool, bool]:
        """Returns (may_be_null, may_be_nonnull)."""
        return self.may_be_null, not self.length.is_bot

    # ------------------------------------------------------------------
    # Restrict to non-null (after a null-check guard)
    # ------------------------------------------------------------------

    def restrict_nonnull(self) -> "StringAbs2":
        if self.length.is_bot:
            return StringAbs2.bot()
        return StringAbs2(self.prefix, self.length, False)

    # ------------------------------------------------------------------
    # String representation
    # ------------------------------------------------------------------

    def __str__(self) -> str:
        null_tag = "∪{null}" if self.may_be_null else ""
        if self.length.is_bot:
            return "⊥" if not self.may_be_null else "null"
        prefix_tag = f'"{self.prefix}"*' if self.prefix is not None else "?"
        return f"Str({prefix_tag},{self.length}){null_tag}"


# ---------------------------------------------------------------------------
# Helper: common prefix of two optional strings
# ---------------------------------------------------------------------------

def _common_prefix(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """Longest common prefix of two (possibly-None) prefixes.

    None means "unknown prefix", which contributes nothing to the common
    prefix (since the concrete string could start with anything).
    """
    if a is None or b is None:
        return None
    result = []
    for ca, cb in zip(a, b):
        if ca == cb:
            result.append(ca)
        else:
            break
    return "".join(result) if result else None
