"""Tests for StringAbs2 prefix domain (string_abs2.py)."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from solutions.string_abs2 import (
    StringAbs2, Interval,
    str2_concat, str2_substring, str2_length, str2_equals,
    str2_startswith, str2_endswith, str2_contains,
    str2_charat, str2_valueof_int,
)


# ── StringAbs2 domain ────────────────────────────────────────────────────────

class TestStringAbs2Core:
    def test_from_const(self):
        s = StringAbs2.from_const("hello")
        assert s.prefix == "hello"
        assert s.length == Interval.const(5)
        assert not s.may_be_null

    def test_bot(self):
        b = StringAbs2.bot()
        assert b.length.is_bot
        assert not b.may_be_null

    def test_null(self):
        n = StringAbs2.null()
        assert n.may_be_null
        assert n.length.is_bot

    def test_top_not_null_required(self):
        t = StringAbs2.top()
        assert t.may_be_null  # top over-approximates null too

    def test_join_same_const_preserves_prefix(self):
        a = StringAbs2.from_const("hello")
        b = StringAbs2.from_const("hello")
        j = a.join(b)
        assert j.prefix == "hello"

    def test_join_different_const_shares_prefix(self):
        a = StringAbs2.from_const("foobar")
        b = StringAbs2.from_const("foobaz")
        j = a.join(b)
        assert j.prefix == "fooba"

    def test_join_no_common_prefix(self):
        a = StringAbs2.from_const("abc")
        b = StringAbs2.from_const("xyz")
        j = a.join(b)
        assert j.prefix == ""

    def test_join_with_bot(self):
        a = StringAbs2.from_const("hi")
        j = a.join(StringAbs2.bot())
        assert j.prefix == "hi"
        j2 = StringAbs2.bot().join(a)
        assert j2.prefix == "hi"

    def test_join_null_propagates(self):
        a = StringAbs2.from_const("x")
        n = StringAbs2.null()
        j = a.join(n)
        assert j.may_be_null

    def test_join_lengths_joined(self):
        a = StringAbs2.from_const("ab")
        b = StringAbs2.from_const("abcde")
        j = a.join(b)
        assert j.length.lo == 2
        assert j.length.hi == 5


# ── Transfer functions ───────────────────────────────────────────────────────

class TestStr2Concat:
    def test_concat_two_consts(self):
        a = StringAbs2.from_const("foo")
        b = StringAbs2.from_const("bar")
        r = str2_concat(a, b)
        assert r.prefix == "foobar"
        assert r.length == Interval.const(6)

    def test_concat_preserves_left_prefix_when_right_unknown(self):
        a = StringAbs2.from_const("pre")
        b = StringAbs2(None, Interval(0, 5), False)
        r = str2_concat(a, b)
        assert r.prefix == "pre"

    def test_concat_with_bot_is_bot(self):
        a = StringAbs2.from_const("x")
        r = str2_concat(a, StringAbs2.bot())
        assert r.length.is_bot

    def test_concat_null_propagates(self):
        a = StringAbs2.from_const("x")
        b = StringAbs2.null()
        r = str2_concat(a, b)
        assert r.may_be_null


class TestStr2Substring:
    def test_exact_slice_const(self):
        s = StringAbs2.from_const("hello")
        r, oob = str2_substring(s, Interval.const(1), Interval.const(3))
        assert r.prefix == "el"
        assert not oob

    def test_oob_negative_start(self):
        s = StringAbs2.from_const("abc")
        _, oob = str2_substring(s, Interval.const(-1), Interval.const(2))
        assert oob

    def test_oob_end_beyond_min_length(self):
        s = StringAbs2(None, Interval(3, 5), False)
        # end=4, min length=3 → may be OOB
        _, oob = str2_substring(s, Interval.const(0), Interval.const(4))
        assert oob

    def test_safe_slice(self):
        s = StringAbs2.from_const("hello")
        r, oob = str2_substring(s, Interval.const(0), Interval.const(5))
        assert not oob

    def test_unknown_indices_return_top_not_crash(self):
        s = StringAbs2.from_const("hello")
        r, _ = str2_substring(s, Interval(0, 10), Interval(0, 10))
        assert r is not None


class TestStr2Equals:
    def test_same_const_true(self):
        a = StringAbs2.from_const("abc")
        assert str2_equals(a, a) == "true"

    def test_different_const_false(self):
        a = StringAbs2.from_const("abc")
        b = StringAbs2.from_const("xyz")
        assert str2_equals(a, b) == "false"

    def test_different_prefix_false(self):
        a = StringAbs2.from_const("alpha")
        b = StringAbs2.from_const("beta")
        assert str2_equals(a, b) == "false"

    def test_overlapping_lengths_maybe(self):
        a = StringAbs2(None, Interval(3, 5), False)
        b = StringAbs2(None, Interval(4, 6), False)
        assert str2_equals(a, b) == "maybe"


class TestStr2StartsWith:
    def test_true_case(self):
        a = StringAbs2.from_const("foobar")
        b = StringAbs2.from_const("foo")
        assert str2_startswith(a, b) == "true"

    def test_false_case(self):
        a = StringAbs2.from_const("foobar")
        b = StringAbs2.from_const("bar")
        assert str2_startswith(a, b) == "false"

    def test_prefix_match_returns_true(self):
        # a has prefix "foo", b is exactly "fo"
        a = StringAbs2("foo", Interval(3, 10), False)
        b = StringAbs2.from_const("fo")
        assert str2_startswith(a, b) == "true"

    def test_prefix_mismatch_returns_false(self):
        a = StringAbs2("foo", Interval(3, 10), False)
        b = StringAbs2.from_const("bar")
        assert str2_startswith(a, b) == "false"


class TestStr2EndsWith:
    def test_true_case(self):
        a = StringAbs2.from_const("foobar")
        b = StringAbs2.from_const("bar")
        assert str2_endswith(a, b) == "true"

    def test_false_case(self):
        a = StringAbs2.from_const("foobar")
        b = StringAbs2.from_const("foo")
        assert str2_endswith(a, b) == "false"


class TestStr2Contains:
    def test_true_case(self):
        a = StringAbs2.from_const("hello world")
        b = StringAbs2.from_const("world")
        assert str2_contains(a, b) == "true"

    def test_false_needle_longer(self):
        a = StringAbs2(None, Interval(2, 3), False)
        b = StringAbs2(None, Interval(5, 7), False)
        assert str2_contains(a, b) == "false"


class TestStr2CharField:
    def test_known_index(self):
        s = StringAbs2.from_const("abc")
        result, oob = str2_charat(s, Interval.const(1))
        assert not oob
        assert result[0] == 'int'

    def test_oob_index(self):
        s = StringAbs2.from_const("abc")
        _, oob = str2_charat(s, Interval.const(5))
        assert oob

    def test_negative_index_oob(self):
        s = StringAbs2.from_const("abc")
        _, oob = str2_charat(s, Interval.const(-1))
        assert oob


class TestStr2ValueOf:
    def test_const_int(self):
        r = str2_valueof_int(Interval.const(42))
        assert r.prefix == "42"
        assert r.length == Interval.const(2)

    def test_unknown_int_gives_top(self):
        r = str2_valueof_int(Interval(-100, 100))
        assert r.prefix is not None or r.length.lo >= 1


# ── Soundness invariant ──────────────────────────────────────────────────────

class TestSoundnessInvariants:
    """For every concrete string s, α(s) must over-approximate all real outputs."""

    def test_concat_length_sound(self):
        """len(a+b) must be in [lo, hi] of concat result."""
        for s1 in ["", "a", "hello", "x" * 10]:
            for s2 in ["", "b", "world"]:
                a = StringAbs2.from_const(s1)
                b = StringAbs2.from_const(s2)
                r = str2_concat(a, b)
                expected_len = len(s1) + len(s2)
                assert r.length.lo <= expected_len <= r.length.hi, (
                    f"concat({s1!r}, {s2!r}): expected {expected_len} in {r.length}"
                )

    def test_substring_length_sound(self):
        """len(s[i:j]) must be in result length interval."""
        for s in ["hello", "abcde", "x"]:
            for i in range(len(s) + 1):
                for j in range(i, len(s) + 1):
                    abs_s = StringAbs2.from_const(s)
                    r, oob = str2_substring(abs_s, Interval.const(i), Interval.const(j))
                    assert not oob, f"false OOB for {s!r}[{i}:{j}]"
                    expected = len(s[i:j])
                    assert r.length.lo <= expected <= r.length.hi

    def test_prefix_is_actual_prefix(self):
        """If result has a prefix string p, it must be an actual prefix of any concrete result."""
        a = StringAbs2.from_const("foo")
        b = StringAbs2.from_const("bar")
        r = str2_concat(a, b)
        if r.prefix:
            assert "foobar".startswith(r.prefix)
