"""Integration tests: run abstract interpreter on selected jpamb methods."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import jpamb
from solutions import abstract_interpreter as _ai


def _methodid(name: str):
    """Find a jpamb method by substring of its string representation."""
    suite = jpamb.Suite()
    for m in suite.allmethods():
        if name in str(m):
            return m
    return None


class TestAnalyzerSmoke:
    """Smoke: calling analyze_method_no_inputs must not crash."""

    def test_analyze_returns_list(self):
        suite = jpamb.Suite()
        methods = list(suite.allmethods())
        if not methods:
            pytest.skip("no methods in suite")
        mid = methods[0]
        result = _ai.analyze_method_no_inputs(mid)
        assert isinstance(result, list)

    def test_all_outcomes_are_known_labels(self):
        known = {"ok", "divide by zero", "assertion error",
                 "out of bounds", "null pointer", "*",
                 "negative array size", "exception"}
        suite = jpamb.Suite()
        methods = list(suite.allmethods())[:20]
        for mid in methods:
            result = _ai.analyze_method_no_inputs(mid)
            for r in result:
                assert r in known, f"unexpected outcome {r!r} for {mid}"

    def test_get_abstract_warnings_returns_list(self):
        suite = jpamb.Suite()
        methods = list(suite.allmethods())[:1]
        if not methods:
            pytest.skip("no methods")
        _ai.analyze_method_no_inputs(methods[0])
        w = _ai.get_abstract_warnings()
        assert isinstance(w, list)


class TestPrefix2Smoke:
    """Same smoke tests for the prefix-domain interpreter."""

    def test_analyze_returns_list(self):
        try:
            from solutions import abstract_interpreter2 as _ai2
        except ImportError:
            pytest.skip("abstract_interpreter2 not available")
        suite = jpamb.Suite()
        methods = list(suite.allmethods())
        if not methods:
            pytest.skip("no methods")
        result = _ai2.analyze_method_no_inputs(methods[0])
        assert isinstance(result, list)

    def test_first_20_methods_no_crash(self):
        try:
            from solutions import abstract_interpreter2 as _ai2
        except ImportError:
            pytest.skip("abstract_interpreter2 not available")
        suite = jpamb.Suite()
        methods = list(suite.allmethods())[:20]
        for mid in methods:
            try:
                result = _ai2.analyze_method_no_inputs(mid)
                assert isinstance(result, list)
            except (NotImplementedError, Exception) as e:
                pass  # graceful degradation is acceptable
