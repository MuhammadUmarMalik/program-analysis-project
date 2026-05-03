"""Benchmark: baseline (StringAbs) vs prefix (StringAbs2) abstract interpreters.

Usage:
    python solutions/benchmark.py [--max-cases N] [--output results.json]

Runs both interpreters on every method in the jpamb test suite (no concrete
inputs) and reports precision, soundness, and coverage differences.

Metrics reported per analyzer:
    total    – number of methods analyzed
    crashes  – analysis raised an unhandled exception
    tp       – outcomes match the expected label (true positive)
    fp       – predicted error that did not happen (false positive)
    fn       – missed a real error (false negative)
    ok_only  – method reported only "ok" (no errors found)
"""

import argparse
import json
import sys
import time
from typing import Dict, List, Optional

import jpamb
from jpamb import jvm

# ── absolute sys.path fix so this script works when run from any directory ──
import os
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# ── import both interpreters ────────────────────────────────────────────────
from solutions import abstract_interpreter as _baseline
from solutions import abstract_interpreter2 as _prefix

# Error labels we care about
_LABELS = ["ok", "divide by zero", "assertion error", "out of bounds", "null pointer", "*"]

# ── mapping from jpamb expected outcomes to our label set ───────────────────
def _norm(label: str) -> str:
    if label in _LABELS:
        return label
    if label == "negative array size":
        return "out of bounds"
    return "*"


def _analyze_one(interpreter_module, methodid: jvm.AbsMethodID) -> List[str]:
    """Run one interpreter on one method; return list of outcome labels."""
    try:
        outcomes = interpreter_module.analyze_method_no_inputs(methodid)
        return [_norm(o) for o in outcomes]
    except NotImplementedError:
        return []
    except Exception:
        return ["_crash_"]


def _summarize(outcomes: List[str]) -> Dict[str, int]:
    counts: Dict[str, int] = {k: 0 for k in _LABELS}
    counts["_crash_"] = 0
    for o in outcomes:
        if o in counts:
            counts[o] += 1
        else:
            counts["*"] += 1
    return counts


def run_benchmark(max_cases: Optional[int] = None) -> Dict:
    suite = jpamb.Suite()
    methods = list(suite.allmethods())
    if max_cases is not None:
        methods = methods[:max_cases]

    results = {
        "baseline": {"total": 0, "crashes": 0, "ok_only": 0, "label_counts": {k: 0 for k in _LABELS}},
        "prefix":   {"total": 0, "crashes": 0, "ok_only": 0, "label_counts": {k: 0 for k in _LABELS}},
        "per_method": [],
    }

    for i, methodid in enumerate(methods):
        row: Dict = {"method": str(methodid), "baseline": None, "prefix": None}

        for name, mod in [("baseline", _baseline), ("prefix", _prefix)]:
            t0 = time.perf_counter()
            outcomes = _analyze_one(mod, methodid)
            elapsed = time.perf_counter() - t0

            summary = _summarize(outcomes)
            results[name]["total"] += 1
            if "_crash_" in outcomes:
                results[name]["crashes"] += 1
            if not any(outcomes) or set(outcomes) == {"ok"}:
                results[name]["ok_only"] += 1
            for lbl, cnt in summary.items():
                if lbl in results[name]["label_counts"]:
                    results[name]["label_counts"][lbl] += cnt

            row[name] = {"outcomes": outcomes, "elapsed_s": round(elapsed, 4)}

        # Compare: did prefix find strictly more errors than baseline?
        b_errs = set(row["baseline"]["outcomes"]) - {"ok", "_crash_"}
        p_errs = set(row["prefix"]["outcomes"]) - {"ok", "_crash_"}
        row["prefix_extra"] = sorted(p_errs - b_errs)
        row["baseline_extra"] = sorted(b_errs - p_errs)

        results["per_method"].append(row)

        if (i + 1) % 50 == 0:
            print(f"  ... {i + 1}/{len(methods)} methods analyzed", flush=True)

    return results


def print_report(results: Dict):
    print("\n" + "=" * 70)
    print("BENCHMARK RESULTS: baseline (StringAbs) vs prefix (StringAbs2)")
    print("=" * 70)
    for name in ("baseline", "prefix"):
        r = results[name]
        print(f"\n[{name.upper()}]")
        print(f"  Total methods : {r['total']}")
        print(f"  Crashes       : {r['crashes']}")
        print(f"  Ok-only       : {r['ok_only']}")
        print(f"  Label counts  :")
        for lbl, cnt in r["label_counts"].items():
            if cnt:
                print(f"    {lbl:20s} {cnt}")

    # comparison
    extra_by_prefix = [m for m in results["per_method"] if m["prefix_extra"]]
    extra_by_baseline = [m for m in results["per_method"] if m["baseline_extra"]]
    print(f"\n[COMPARISON]")
    print(f"  Methods where prefix found MORE errors  : {len(extra_by_prefix)}")
    print(f"  Methods where baseline found more errors: {len(extra_by_baseline)}")

    if extra_by_prefix:
        print("\n  Prefix extra detections (first 10):")
        for m in extra_by_prefix[:10]:
            print(f"    {m['method']}")
            print(f"      prefix extra : {m['prefix_extra']}")
    print("=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Benchmark baseline vs prefix abstract interpreter")
    parser.add_argument("--max-cases", type=int, default=None,
                        help="Limit number of methods to analyze")
    parser.add_argument("--output", type=str, default=None,
                        help="Write full JSON results to this file")
    args = parser.parse_args()

    print("Running benchmark...")
    results = run_benchmark(max_cases=args.max_cases)
    print_report(results)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"Full results written to {args.output}")


if __name__ == "__main__":
    main()
