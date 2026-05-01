import sys
import jpamb

from novel_abstract_interpreter import analyze_method_no_inputs, get_abstract_warnings

ANALYZER_NAME = "mixed effort Analyzer"
ANALYZER_VERSION = "2.0 interval+strings"
STUDENT_GROUP = "ASTRA"
TAGS = ["mixed", "python"]


def main():
    # Register with jpamb / obtain current method id
    methodid = jpamb.getmethodid(
        ANALYZER_NAME,
        ANALYZER_VERSION,
        STUDENT_GROUP,
        TAGS,
        for_science=True,
    )

    # Info mode: jpamb asks for metadata
    if len(sys.argv) == 2 and sys.argv[1] == "info":
        print(ANALYZER_NAME)
        print(ANALYZER_VERSION)
        print(STUDENT_GROUP)
        print(",".join(TAGS))
        print("no")  # or "yes" if you want to share system info
        return

    # Normal analysis mode (no concrete inputs)
    try:
        outcomes = analyze_method_no_inputs(methodid)
    except Exception:
        # If the abstract interpreter crashes, fall back to "no info"
        outcomes = []

    # Optionally fetch warnings (not used in scoring, but available)
    _warnings = get_abstract_warnings()

    # Map outcomes to the 6 categories
    buckets = {
        "ok": 0,
        "divide by zero": 0,
        "assertion error": 0,
        "out of bounds": 0,
        "null pointer": 0,
        "*": 0,  # everything else 
    }

    for r in outcomes:
        if r in buckets:
            buckets[r] += 1
        elif r == "negative array size":
            # treat like an index-related error
            buckets["out of bounds"] += 1
        elif r in ("exception",):
            buckets["*"] += 1
        else:
            # any unexpected label from the interpreter
            buckets["*"] += 1

    # ---------- hypothesis for observed error labels ----------
    error_labels = ["divide by zero", "assertion error", "out of bounds", "null pointer"]

    raw_errors = sum(buckets[l] for l in error_labels)
    raw_ok = buckets["ok"]

    if raw_errors > 0 and raw_ok > 0:
        # we saw at least one error path and at least one ok path  -> give error labels a small boost before smoothing
        for l in error_labels:
            if buckets[l] > 0:
                buckets[l] += 1  # small bonus

    total_raw = sum(buckets.values())

    # ---------- special case: only ok observed ----------
    if total_raw > 0 and buckets["ok"] == total_raw:
        # very optimistic if analysis saw no error outcomes
        probs = {
            "ok": 0.85,
            "divide by zero": 0.03,
            "assertion error": 0.03,
            "out of bounds": 0.03,
            "null pointer": 0.03,
            "*": 0.03,
        }
    else:
        # ---------- biased priors: favour ok ----------
        alpha_ok = 3.0     # prior mass for ok
        alpha_err = 1.0    # prior mass for each error classA

        total_alpha = alpha_ok + alpha_err * (len(buckets) - 1)
        denom = total_raw + total_alpha

        probs = {}
        for k in buckets:
            if k == "ok":
                probs[k] = (buckets[k] + alpha_ok) / denom
            else:
                probs[k] = (buckets[k] + alpha_err) / denom

    def pct(p: float) -> str:
        perc = int(round(100.0 * p))
        if perc <= 0:
            perc = 1
        elif perc >= 100:
            perc = 99
        return f"{perc}%"

    # Output predictions for the 6 possible outcomes
    print(f"ok;{pct(probs['ok'])}")
    print(f"divide by zero;{pct(probs['divide by zero'])}")
    print(f"assertion error;{pct(probs['assertion error'])}")
    print(f"out of bounds;{pct(probs['out of bounds'])}")
    print(f"null pointer;{pct(probs['null pointer'])}")
    print(f"*;{pct(probs['*'])}")


if __name__ == "__main__":
    main()
