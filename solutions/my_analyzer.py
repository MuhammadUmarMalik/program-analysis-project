import sys
import traceback
import jpamb

from abstract_interpreter import analyze_method_no_inputs, get_abstract_warnings

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
        print("no")
        return

    # Normal analysis mode (no concrete inputs)
    try:
        outcomes = analyze_method_no_inputs(methodid)
    except NotImplementedError as e:
        # Unimplemented opcode / method — not a bug; just return no outcomes.
        print(f"[static-analysis-engine] NotImplementedError: {e}", file=sys.stderr)
        outcomes = []
    except Exception as e:
        # Unexpected crash — log full traceback for debugging, but don't die.
        print(f"[static-analysis-engine] ERROR: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
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
        "*": 0,  # everything else (infinite loop / unknown / other exceptions)
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

    # --- Laplace smoothing so we never have zero probabilities ---
    total_raw = sum(buckets.values())

    if total_raw == 0:
        # no classified outcomes ⇒ be maximally agnostic: uniform
        for k in buckets:
            buckets[k] = 1
    else:
        # add-one smoothing
        for k in buckets:
            buckets[k] += 1

    total = sum(buckets.values())

    def pct(n: int) -> str:
        # percentage as integer 0–100, but never exactly 0 or 100
        p_float = 100.0 * n / total
        p = int(round(p_float))

        # clamp to [1, 99] to avoid 0 and 100 (which cause -inf if wrong)
        if p <= 0:
            p = 1
        elif p >= 100:
            p = 99
        return f"{p}%"

    # Output predictions for the 6 possible outcomes in required order
    print(f"ok;{pct(buckets['ok'])}")
    print(f"divide by zero;{pct(buckets['divide by zero'])}")
    print(f"assertion error;{pct(buckets['assertion error'])}")
    print(f"out of bounds;{pct(buckets['out of bounds'])}")
    print(f"null pointer;{pct(buckets['null pointer'])}")
    print(f"*;{pct(buckets['*'])}")


if __name__ == "__main__":
    main()
