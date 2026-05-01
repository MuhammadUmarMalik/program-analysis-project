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


# ---------------------------------------------------------------------------
# Transfer functions
# ---------------------------------------------------------------------------

def str2_concat(a: StringAbs2, b: StringAbs2) -> StringAbs2:
    """Abstract concatenation: a + b.

    Prefix rule:
      - If a is a full constant (prefix length == known exact length), the
        result prefix is a.prefix + b.prefix.
      - Otherwise only a.prefix is known (b is appended after a).
    Length rule: [a.lo + b.lo, a.hi + b.hi].
    """
    if a.is_bot or b.is_bot:
        return StringAbs2.bot()

    # Compute length
    length = a.length.add(b.length)

    # Prefix computation
    if (
        a.prefix is not None
        and not a.length.is_bot
        and a.length.lo == a.length.hi
        and a.length.lo == len(a.prefix)
    ):
        # a is a full constant string → extend with b.prefix
        if b.prefix is not None:
            prefix = a.prefix + b.prefix
            if not b.length.is_bot and b.length.lo == b.length.hi and b.length.lo == len(b.prefix):
                # b is also full constant → result is fully known
                length = Interval.const(len(prefix))
        else:
            prefix = a.prefix  # b prefix unknown; keep a.prefix
    else:
        prefix = a.prefix  # only a's prefix survives

    return StringAbs2(prefix, length, False)


def str2_substring(
    s: StringAbs2,
    i_ivl: Interval,
    j_ivl: Interval,
) -> tuple[StringAbs2, bool]:
    """Abstract substring(i, j).

    Returns (result_abs, may_out_of_bounds).
    Sound: reports may_oob when 0 <= i <= j <= |s| might be violated.
    Prefix rule:
      - If i_ivl = [0,0] and s has a known prefix of length >= j_hi,
        the result prefix is s.prefix[:j_hi].
      - If both i and j are concrete and s is a full constant, result is exact.
    """
    if s.is_bot or i_ivl.is_bot or j_ivl.is_bot:
        return StringAbs2.bot(), False

    may_oob = False
    L = s.length

    if L.is_bot:
        may_oob = True
    else:
        if i_ivl.lo < 0 or j_ivl.lo < 0:  # type: ignore[operator]
            may_oob = True
        if i_ivl.lo > j_ivl.hi:  # type: ignore[operator]
            may_oob = True
        # j could exceed minimum length
        if j_ivl.hi > L.lo:  # type: ignore[operator]
            may_oob = True

    # Result length = [max(0, j_lo - i_hi), max(0, j_hi - i_lo)]
    sub_lo = max(0.0, j_ivl.lo - i_ivl.hi)   # type: ignore[operator]
    sub_hi = max(0.0, j_ivl.hi - i_ivl.lo)   # type: ignore[operator]
    sub_len = Interval(sub_lo, sub_hi)

    # Prefix: precise if i=[0,0] and prefix is long enough
    prefix: Optional[str] = None
    if (
        not i_ivl.is_bot
        and i_ivl.lo == 0
        and i_ivl.hi == 0
        and s.prefix is not None
        and not j_ivl.is_bot
        and j_ivl.lo == j_ivl.hi
    ):
        j_const = int(j_ivl.lo)
        if j_const <= len(s.prefix):
            prefix = s.prefix[:j_const]
            sub_len = Interval.const(j_const)

    # Fully constant case
    if (
        s.prefix is not None
        and not s.length.is_bot
        and s.length.lo == s.length.hi
        and s.length.lo == len(s.prefix)
        and not i_ivl.is_bot
        and i_ivl.lo == i_ivl.hi
        and not j_ivl.is_bot
        and j_ivl.lo == j_ivl.hi
    ):
        i_idx = int(i_ivl.lo)
        j_idx = int(j_ivl.lo)
        try:
            const_val = s.prefix[i_idx:j_idx]
            prefix = const_val
            sub_len = Interval.const(len(const_val))
        except Exception:
            may_oob = True

    return StringAbs2(prefix, sub_len, False), may_oob


def str2_length(s: StringAbs2) -> Interval:
    """Abstract length()."""
    return s.length if not s.is_bot else Interval.bot()


def str2_equals(a: StringAbs2, b: StringAbs2) -> str:
    """Abstract equals(). Returns 'true' / 'false' / 'maybe'."""
    if a.is_bot or b.is_bot:
        return "false"

    # Both fully known constants
    if (
        a.prefix is not None
        and b.prefix is not None
        and not a.length.is_bot
        and a.length.lo == a.length.hi
        and a.length.lo == len(a.prefix)
        and not b.length.is_bot
        and b.length.lo == b.length.hi
        and b.length.lo == len(b.prefix)
    ):
        return "true" if a.prefix == b.prefix else "false"

    # Length intervals don't overlap → definitely unequal
    if not a.length.is_bot and not b.length.is_bot:
        if a.length.hi < b.length.lo or b.length.hi < a.length.lo:  # type: ignore[operator]
            return "false"

    # Prefixes differ at some known position → definitely unequal
    if a.prefix is not None and b.prefix is not None:
        min_len = min(len(a.prefix), len(b.prefix))
        if a.prefix[:min_len] != b.prefix[:min_len]:
            return "false"

    return "maybe"


def str2_startswith(a: StringAbs2, b: StringAbs2) -> str:
    """Abstract startsWith(). Returns 'true' / 'false' / 'maybe'."""
    if a.is_bot or b.is_bot:
        return "false"

    # b is longer than a → definitely false
    if not a.length.is_bot and not b.length.is_bot:
        if b.length.lo > a.length.hi:  # type: ignore[operator]
            return "false"

    # Both prefixes known: check directly
    if a.prefix is not None and b.prefix is not None:
        # If b is a full constant (b.prefix has exact length)
        b_is_full = (
            not b.length.is_bot
            and b.length.lo == b.length.hi
            and b.length.lo == len(b.prefix)
        )
        if b_is_full:
            # a.prefix must start with b.prefix
            if a.prefix.startswith(b.prefix):
                return "true"
            else:
                return "false"
        # b.prefix is partial: if a.prefix starts with b.prefix, might be true
        min_len = min(len(a.prefix), len(b.prefix))
        if a.prefix[:min_len] != b.prefix[:min_len]:
            return "false"

    return "maybe"


def str2_endswith(a: StringAbs2, b: StringAbs2) -> str:
    """Abstract endsWith(). Returns 'true' / 'false' / 'maybe'."""
    if a.is_bot or b.is_bot:
        return "false"

    if not a.length.is_bot and not b.length.is_bot:
        if b.length.lo > a.length.hi:  # type: ignore[operator]
            return "false"

    # Fully known constants
    if (
        a.prefix is not None
        and b.prefix is not None
        and not a.length.is_bot
        and a.length.lo == a.length.hi
        and a.length.lo == len(a.prefix)
        and not b.length.is_bot
        and b.length.lo == b.length.hi
        and b.length.lo == len(b.prefix)
    ):
        return "true" if a.prefix.endswith(b.prefix) else "false"

    return "maybe"


def str2_contains(a: StringAbs2, b: StringAbs2) -> str:
    """Abstract contains(). Returns 'true' / 'false' / 'maybe'."""
    if a.is_bot or b.is_bot:
        return "false"

    # b is longer than a → false
    if not a.length.is_bot and not b.length.is_bot:
        if b.length.lo > a.length.hi:  # type: ignore[operator]
            return "false"

    # Both fully known constants
    if (
        a.prefix is not None
        and b.prefix is not None
        and not a.length.is_bot
        and a.length.lo == a.length.hi
        and a.length.lo == len(a.prefix)
        and not b.length.is_bot
        and b.length.lo == b.length.hi
        and b.length.lo == len(b.prefix)
    ):
        return "true" if b.prefix in a.prefix else "false"

    return "maybe"


def str2_charat(s: StringAbs2, idx_ivl: Interval) -> tuple[Interval, bool]:
    """Abstract charAt(idx).

    Returns (char_code_interval, may_out_of_bounds).
    """
    if s.is_bot or idx_ivl.is_bot:
        return Interval.bot(), False

    may_oob = False
    L = s.length

    # Negative index
    if idx_ivl.lo < 0:  # type: ignore[operator]
        may_oob = True

    # Index might be >= min length
    if not L.is_bot and idx_ivl.hi >= L.lo:  # type: ignore[operator]
        may_oob = True

    # Exact value when string constant and index is single
    if (
        s.prefix is not None
        and not s.length.is_bot
        and s.length.lo == s.length.hi
        and s.length.lo == len(s.prefix)
        and not idx_ivl.is_bot
        and idx_ivl.lo == idx_ivl.hi
    ):
        idx = int(idx_ivl.lo)
        if idx < 0 or idx >= len(s.prefix):
            return Interval.bot(), True
        code = ord(s.prefix[idx])
        return Interval.const(code), may_oob

    return Interval(0.0, 65535.0), may_oob


def str2_valueof_int(ivl: Interval) -> StringAbs2:
    """Abstract String.valueOf(int).

    If the integer is a single known value, we know the exact string.
    Otherwise we know length ∈ [1, 11] (max 10 digits + sign).
    """
    if ivl.is_bot:
        return StringAbs2.bot()
    if ivl.lo == ivl.hi and ivl.lo not in (-POS_INF, POS_INF):
        s = str(int(ivl.lo))
        return StringAbs2.from_const(s)
    # general int: 1–11 chars (covers -2147483648 to 2147483647)
    return StringAbs2(None, Interval(1.0, 11.0), False)
