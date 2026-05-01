"""Tests for StringAbs baseline domain (abstract_interpreter.py)."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from solutions.abstract_interpreter import (
    StringAbs, Interval,
    str_concat, str_substring, str_length, str_equals,
    str_contains, str_startswith, str_endswith,
)


# ── Interval helpers ────────────────────────────────────────────────────────

class TestInterval:
    def test_bot_is_bot(self):
        assert Interval.bot().is_bot

    def test_const_not_bot(self):
        ivl = Interval.const(5)
        assert not ivl.is_bot
        assert ivl.lo == ivl.hi == 5

    def test_join_with_bot(self):
        b = Interval.bot()
        i = Interval.const(3)
        # join(bot, x) == x
        from solutions.abstract_interpreter import join
        assert join(b, i) == i
        assert join(i, b) == i

    def test_join_two(self):
        from solutions.abstract_interpreter import join
        a = Interval(1, 3)
        b = Interval(5, 7)
        j = join(a, b)
        assert j.lo == 1 and j.hi == 7


# ── StringAbs domain ────────────────────────────────────────────────────────

class TestStringAbsBaseline:
    def test_const_str(self):
        s = StringAbs.const_str("hello")
        assert s.const == "hello"
        assert s.length == Interval.const(5)
        assert not s.is_bot

    def test_bot(self):
        assert StringAbs.bot().is_bot

    def test_top_not_bot(self):
        assert not StringAbs.top().is_bot

    def test_join_same_const(self):
        a = StringAbs.const_str("hi")
        b = StringAbs.const_str("hi")
        j = a.join(b)
        assert j.const == "hi"
        assert j.length == Interval.const(2)

    def test_join_different_const_erases(self):
        a = StringAbs.const_str("foo")
        b = StringAbs.const_str("bar")
        j = a.join(b)
        assert j.const is None
        assert j.length == Interval(3, 3)

    def test_join_with_bot_is_identity(self):
        a = StringAbs.const_str("hello")
        j = a.join(StringAbs.bot())
        assert j.const == "hello"
        j2 = StringAbs.bot().join(a)
        assert j2.const == "hello"


# ── Transfer functions ───────────────────────────────────────────────────────

class TestStrConcat:
    def test_concat_consts(self):
        a = StringAbs.const_str("foo")
        b = StringAbs.const_str("bar")
        r = str_concat(a, b)
        assert r.const == "foobar"
        assert r.length == Interval.const(6)

    def test_concat_with_bot(self):
        a = StringAbs.const_str("x")
        r = str_concat(a, StringAbs.bot())
        assert r.is_bot

    def test_concat_unknown_length(self):
        a = StringAbs(None, Interval(2, 4), None)
        b = StringAbs(None, Interval(1, 3), None)
        r = str_concat(a, b)
        assert r.length.lo == 3
        assert r.length.hi == 7


class TestStrSubstring:
    def test_exact_slice(self):
        s = StringAbs.const_str("hello")
        i = Interval.const(1)
        j = Interval.const(3)
        r, oob = str_substring(s, i, j)
        assert r.const == "el"
        assert not oob

    def test_oob_when_end_exceeds_min_length(self):
        # length is [3,5], end index is 4 → may OOB (4 > 3)
        s = StringAbs(None, Interval(3, 5), None)
        i = Interval.const(0)
        j = Interval.const(4)
        _, oob = str_substring(s, i, j)
        assert oob

    def test_negative_start_oob(self):
        s = StringAbs.const_str("abc")
        i = Interval.const(-1)
        j = Interval.const(2)
        _, oob = str_substring(s, i, j)
        assert oob

    def test_safe_slice_no_oob(self):
        # length exactly 5, slice [1,3] is always within bounds
        s = StringAbs.const_str("hello")
        i = Interval.const(1)
        j = Interval.const(3)
        _, oob = str_substring(s, i, j)
        assert not oob


class TestStrEquals:
    def test_same_const(self):
        a = StringAbs.const_str("abc")
        assert str_equals(a, a) == "true"

    def test_different_const(self):
        a = StringAbs.const_str("abc")
        b = StringAbs.const_str("xyz")
        assert str_equals(a, b) == "false"

    def test_disjoint_lengths(self):
        a = StringAbs(None, Interval(1, 2), None)
        b = StringAbs(None, Interval(5, 6), None)
        assert str_equals(a, b) == "false"

    def test_overlapping_lengths_maybe(self):
        a = StringAbs(None, Interval(3, 5), None)
        b = StringAbs(None, Interval(4, 6), None)
        assert str_equals(a, b) == "maybe"


class TestStrContains:
    def test_contains_true(self):
        a = StringAbs.const_str("hello world")
        b = StringAbs.const_str("world")
        assert str_contains(a, b) == "true"

    def test_contains_false_needle_longer(self):
        a = StringAbs(None, Interval(3, 5), None)
        b = StringAbs(None, Interval(6, 8), None)
        assert str_contains(a, b) == "false"


class TestStrStartsEndsWith:
    def test_startswith_true(self):
        a = StringAbs.const_str("foobar")
        b = StringAbs.const_str("foo")
        assert str_startswith(a, b) == "true"

    def test_startswith_false(self):
        a = StringAbs.const_str("foobar")
        b = StringAbs.const_str("baz")
        assert str_startswith(a, b) == "false"

    def test_endswith_true(self):
        a = StringAbs.const_str("foobar")
        b = StringAbs.const_str("bar")
        assert str_endswith(a, b) == "true"

    def test_endswith_false(self):
        a = StringAbs.const_str("foobar")
        b = StringAbs.const_str("foo")
        assert str_endswith(a, b) == "false"
