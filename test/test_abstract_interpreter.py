from math import isclose
from hypothesis import given, assume
from hypothesis.strategies import text, sets, integers
import sys
from pathlib import Path


# Ensure project root is on sys.path so 'solutions' can be imported
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from solutions import abstract_interpreter as ai


# ---------------- Test for Integer Abstraction ---------------
@given(integers(), integers())
def test_interval_add_matches_concrete_add(x, y):
    a = ai.Interval.const(x)
    b = ai.Interval.const(y)
    res = ai.add(a, b)
    s = x + y
    assert res.lo <= s <= res.hi
    assert res.lo == res.hi == s


@given(integers(), integers())
def test_interval_sub_matches_concrete_sub(x, y):
    a = ai.Interval.const(x)
    b = ai.Interval.const(y)
    res = ai.sub(a, b)
    s = x - y
    assert res.lo <= s <= res.hi
    assert res.lo == res.hi == s
 

@given(integers(), integers())
def test_interval_mul_matches_concrete_mul(x, y):
    a = ai.Interval.const(x)
    b = ai.Interval.const(y)
    res = ai.mul(a, b)
    p = x * y
    assert res.lo <= p <= res.hi
    assert res.lo == res.hi == p


@given(integers(), integers().filter(lambda z: z != 0))
def test_interval_div_includes_concrete_div(x, y):
    a = ai.Interval.const(x)
    b = ai.Interval.const(y)
    res = ai.div(a, b)
    q = x / y  # real division

    assert isclose(res.lo, q, rel_tol=1e-12, abs_tol=1e-12)
    assert isclose(res.hi, q, rel_tol=1e-12, abs_tol=1e-12)


@given(integers(), integers().filter(lambda z: z != 0))
def test_interval_rem_includes_concrete_rem(x, y):
    a = ai.Interval.const(x)
    b = ai.Interval.const(y)
    res = ai.rem(a, b)
    r = x % y
    assert res.lo <= r <= res.hi


@given(integers(), integers())
def test_ivl_cmp_eq_precise(x, y):
    a = ai.Interval.const(x)
    b = ai.Interval.const(y)
    res = ai.ivl_cmp(a, b, "eq")
    expected = "true" if x == y else "false"
    assert res == expected


@given(integers())
def test_ivl_cond_zero_eq_precise(x):
    i = ai.Interval.const(x)
    res = ai.ivl_cond_zero(i, "eq")
    expected = "true" if x == 0 else "false"
    assert res == expected


# ---------- Tests for Array Abstraction ---------------
@given(integers(min_value=0, max_value=50))
def test_arrayobj_int_elements_initialised_to_zero(n):
    arr = ai._ArrayObj(ai.jvm.Int(), n)
    assert arr.length == n
    assert len(arr.data) == n
    for kind, ivl in arr.data:
        assert kind == 'int'
        assert ivl.lo == ivl.hi == 0


@given(integers(min_value=0, max_value=50))
def test_arrayobj_char_elements_initialised_to_zero(n):
    arr = ai._ArrayObj(ai.jvm.Char(), n)
    assert arr.length == n
    assert len(arr.data) == n
    for kind, ivl in arr.data:
        assert kind == 'int'
        assert ivl.lo == ivl.hi == 0


@given(integers(min_value=0, max_value=50))
def test_arrayobj_ref_elements_initialised_to_null(n):
    arr = ai._ArrayObj(object(), n)
    assert arr.length == n
    assert len(arr.data) == n
    for kind, ref in arr.data:
        assert kind == 'ref'
        assert ref is None

# ---------------- Test for String Abstraction ---------------
@given(text())
def test_single_string_abstraction(xs):
    ivl = ai.Interval.abstract_str(xs)
    assert ivl.lo <= len(xs) <= ivl.hi
    assert ivl.lo == ivl.hi == len(xs)

@given(sets(text(), min_size=1))
def test_set_abstraction(xs):
    ivl = ai.Interval.abstract_set(xs)
    for s in xs:
        assert ivl.lo <= len(s) <= ivl.hi

# -------- Tests for String Operations --------
@given(text())
def test_str_length_matches_concrete(s):
    a = ai.StringAbs.const_str(s)
    ivl = ai.str_length(a)
    assert ivl.lo == ivl.hi == len(s)


@given(text(), text())
def test_str_concat_concrete(s1, s2):
    a = ai.StringAbs.const_str(s1)
    b = ai.StringAbs.const_str(s2)
    res = ai.str_concat(a, b)

    expected = s1 + s2
    assert res.const == expected
    assert res.length.lo == res.length.hi == len(expected)


@given(text(), text())
def test_str_equals_concrete(s1, s2):
    a = ai.StringAbs.const_str(s1)
    b = ai.StringAbs.const_str(s2)
    res = ai.str_equals(a, b)
    expected = "true" if s1 == s2 else "false"
    assert res == expected


@given(text(), text())
def test_str_contains_concrete(haystack, needle):
    a = ai.StringAbs.const_str(haystack)
    b = ai.StringAbs.const_str(needle)
    res = ai.str_contains(a, b)
    expected = "true" if needle in haystack else "false"
    assert res == expected


@given(text(), text())
def test_str_startswith_concrete(s, prefix):
    a = ai.StringAbs.const_str(s)
    b = ai.StringAbs.const_str(prefix)
    res = ai.str_startswith(a, b)
    expected = "true" if s.startswith(prefix) else "false"
    assert res == expected


@given(text(), text())
def test_str_endswith_concrete(s, suffix):
    a = ai.StringAbs.const_str(s)
    b = ai.StringAbs.const_str(suffix)
    res = ai.str_endswith(a, b)
    expected = "true" if s.endswith(suffix) else "false"
    assert res == expected


@given(text(), integers(min_value=0, max_value=50), integers(min_value=0, max_value=50))
def test_str_substring_inbounds_exact(s, i, j):
    if len(s) == 0:
        assume(False)

    n = len(s)
    i = max(0, min(i, n - 1))
    j = max(i, min(j, n))

    a = ai.StringAbs.const_str(s)
    i_ivl = ai.Interval.const(i)
    j_ivl = ai.Interval.const(j)

    res, may_oob = ai.str_substring(a, i_ivl, j_ivl)

    assert not may_oob
    expected = s[i:j]
    assert res.const == expected
    assert res.length.lo == res.length.hi == len(expected)

# --------- Test for error flags -------
def test_str_substring_oob_flags_negative_start():
    s_abs = ai.StringAbs.const_str("abc")
    res, may_oob = ai.str_substring(s_abs, ai.Interval.const(-1), ai.Interval.const(1))
    assert may_oob


def test_str_substring_oob_flags_end_too_large():
    s_abs = ai.StringAbs.const_str("abc")
    res, may_oob = ai.str_substring(s_abs, ai.Interval.const(0), ai.Interval.const(5))
    assert may_oob
