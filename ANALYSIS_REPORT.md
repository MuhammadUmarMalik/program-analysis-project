# ASTRA Program Analysis Project — Full Codebase Report

**Group:** ASTRA | **Date:** 2026-05-01 | **Branch:** `dev`

---

## 1. Project Overview

This is a **JVM bytecode abstract interpretation** project built on top of **JPAMB** (Java Program Analysis Micro Benchmark). The goal is to statically analyze JVM methods — without running them — and predict which runtime errors they may produce, outputting confidence probabilities.

The system produces predictions for 6 error categories per method:
- `ok` — method always returns normally
- `divide by zero` — integer division by zero possible
- `assertion error` — `assert` statement may fail
- `out of bounds` — array or string index may exceed bounds
- `null pointer` — null dereference possible
- `*` — any other exception / infinite loop / unknown

---

## 2. Repository File Map

```
Program-Analysis-Project-main/
│
├── jpamb/                          ← JPAMB harness framework
│   ├── __init__.py                 ← Public API: Suite, parse_methodid, getmethodid, etc.
│   ├── cli.py                      ← CLI commands: build, test, interpret, evaluate, inspect, plot
│   ├── model.py                    ← Data model: Input, Case, Prediction, Response, Suite
│   ├── stats.py                    ← Statistics aggregator + Plotly visualizations
│   ├── logger.py                   ← loguru-based structured logging + subprocess wrapper
│   └── jvm/
│       ├── __init__.py             ← Re-exports base + opcode
│       ├── base.py                 ← JVM type system: Int, String, Array, MethodID, etc.
│       └── opcode.py               ← JVM opcode dataclasses (Push, Load, Store, Binary, If…)
│
├── solutions/                      ← Student-authored analysis engines
│   ├── abstract_interpreter.py     ← PRIMARY: Interval + StringAbs(const) abstract interpreter
│   ├── my_analyzer.py              ← Analyzer driver for abstract_interpreter.py
│   ├── novel_abstract_interpreter.py ← NOVEL: Interval + StringAbs(prefix+exact) interpreter
│   ├── novel_analyzer.py           ← Analyzer driver for novel_abstract_interpreter.py
│   ├── syntaxer.py                 ← Baseline: tree-sitter syntactic analysis
│   ├── syntactic_analysis.py       ← Extended syntactic analysis pipeline
│   ├── interpreter.py              ← Concrete interpreter (for reference)
│   ├── stringtree.py               ← String tree domain helper
│   ├── apriori.py                  ← Baseline apriori analyzer
│   ├── bytecoder.py                ← Bytecode-based baseline
│   └── cheater.py                  ← Reference/ground-truth analyzer
│
├── test/                           ← pytest suite
│   ├── test_abstract_interpreter.py
│   ├── test_cli.py / test_cli_reliability.py
│   ├── test_jpamb.py / test_jvm.py / test_jvm_bytecode.py
│   ├── test_model.py / test_scoring.py
│
├── src/main/java/jpamb/            ← Java benchmark source cases
│   ├── cases/Simple.java / Arrays.java / Strings.java / Loops.java / Calls.java / Tricky.java
│   └── utils/                      ← Java runtime helpers
│
├── CHANGELOG.md                    ← Upstream project changelog (v0.0.2 → vX.X.X)
├── my_analyzer_backup.py           ← Backup of earlier analyzer version
└── pyproject.toml                  ← Python 3.13+, uv, dependencies
```

---

## 3. Git Changelog — All Commits (Newest First)

### Commit 1 — `45e4d35` · 2026-05-01 · fix: resolve jpamb CLI and JVM descriptor bugs

**Files changed:** `jpamb/jvm/base.py`, `jpamb/cli.py`, `jpamb/model.py` (20 insertions, 12 deletions)

**Problems fixed:**

| File | Bug | Fix |
|------|-----|-----|
| `base.py` | `Type.__eq__` used `<=` (encode comparison) instead of `==` — broke all type equality checks | Changed to `type(self) == type(other) and self.encode() == other.encode()` |
| `base.py` | `Type.__lt__` used `<=` instead of `<` — broke ordering (irreflexivity) | Changed to `self.encode() < other.encode()` |
| `base.py` | `Array.descriptor()` called `self.encode()` which produces `[L` not `[Ljava/lang/String;` | Fixed to recurse: `"[" + self.contains.descriptor()` |
| `base.py` | `ParameterType.descriptors()` called `encode()` (raw JVM encoding) not `descriptor()` | Changed to `t.descriptor()` for each element |
| `base.py` | `ParameterType.math()` always returned the string `"double"` regardless of types | Fixed to `", ".join(t.math() for t in self._elements)` |
| `base.py` | `MethodID` had no `.descriptor()` method — only `.encode()` | Added `MethodID.descriptor()` using proper `descriptor()` calls |
| `base.py` | `AbsMethodID` had no `.jvm_str()` — calling `str()` on it produced `L` not `Ljava/lang/String;` | Added `jvm_str()` returning `classname.encode() + "." + extension.descriptor()` |
| `cli.py` | `build` command passed `str(case.methodid)` to Docker Java — produced invalid descriptor `(L)V` causing exit code 1 | Changed to `case.methodid.jvm_str()` |
| `cli.py` | A comment was indented inside the `with` block body — Python silently ignores it but is misleading | Corrected indentation |
| `model.py` | `Input.decode()` validation used `and` — only raised if **both** checks failed (wrong parenthesis validation logic) | Changed to `or` — raises if **either** first or last char is wrong |
| `model.py` | `CASE_RE` regex pattern `[^)]*` would fail to parse inputs that contained `)` (e.g., string values) | Changed to `.*` allowing any characters in the input group |
| `model.py` | Two commented-out duplicate `CASE_RE` lines were present | Removed duplicate lines |

**Impact:** This commit fixes the entire CI/CD test pipeline — the Docker build step was broken because Java received invalid method descriptors.

---

### Commit 2 — `25ebe8a` · 2026-05-01 · fix: resolve all bugs identified in code review pass

**Files changed:** `abstract_interpreter.py`, `interpreter.py`, `string_abs2.py` (new), `syntactic_analysis.py` (543 insertions, 71 deletions)

**Problems fixed:**

| File | Bug | Fix |
|------|-----|-----|
| `abstract_interpreter.py` | `str_substring` OOB check used `j_hi > L.lo` — unsound: checked against the lower bound of length (could miss OOB when `j_hi > L.hi`) | Fixed to `j_hi > L.hi` |
| `abstract_interpreter.py` | `charAt` OOB check used `idx_ivl.hi >= L.lo` — should use `L.hi` (maximum possible length) | Fixed to `L.hi` |
| `abstract_interpreter.py` | `Push` opcode had two `case _:` branches for unknown literal kind (dead duplicate) | Collapsed into single branch |
| `abstract_interpreter.py` | `NewArray` had `int(len_ivl.lo) if len_ivl.lo == len_ivl.hi else int(len_ivl.lo)` — trivial ternary, both branches identical | Simplified to just `int(len_ivl.lo)` |
| `abstract_interpreter.py` | `InvokeStatic` default path had a `norm_args` loop that did nothing (just appended unchanged values) | Removed dead loop |
| `abstract_interpreter.py` | `InvokeStatic` unnecessarily re-created `callee.pc = PC(callee.pc.method, callee.pc.offset)` | Removed redundant PC reconstruction |
| `interpreter.py` | `Ifz` reference branch referenced an undefined variable `c` (copy-paste from int branch) | Fixed to use correct variable |
| `interpreter.py` | `Store` called `_ArrayObj()` with no args — `_ArrayObj` requires arguments; would crash | Fixed constructor call |
| `interpreter.py` | `Frame.from_method` was missing `@staticmethod` decorator — called as instance method | Added `@staticmethod` |
| `interpreter.py` | `locals` variable shadowed Python built-in `locals()` | Renamed to avoid shadowing |
| `interpreter.py` | Several commented-out Bytecode/ObjRef/`_jump_pc` blocks were present | Removed dead commented code |
| `string_abs2.py` | `_common_prefix()` returned `None` instead of `""` for strings with no common prefix | Fixed to return `""` |
| `string_abs2.py` | File was untracked (not added to git) | Added to tracking |
| `syntactic_analysis.py` | Group name was set to an unprofessional placeholder string | Changed to `"ASTRA"` |
| `syntactic_analysis.py` | Variable named `inf` shadowed the `math.inf` import | Renamed to `star_pct` |

---

### Commit 3 — `14eb190` · 2026-05-01 · Refactor: initial project setup

This was the foundational commit that brought in the full project structure — all 101 files, 24,587 lines. It established:
- The complete JPAMB framework (`jpamb/`)
- All solution files (`solutions/`)
- The Java benchmark sources (`src/main/java/`)
- Test suite (`test/`)
- Claude Code skills, settings, and `.gitignore`
- Project metadata (`pyproject.toml`, `CITATION.cff`, `LICENSE`, `CONTRIBUTING.md`)

---

## 4. How the System Works — Full Architecture

### 4.1 Abstract Value Domain

The interpreter operates over **abstract values** (AVals) that represent sets of concrete values:

```
AVal = ('int', Interval) | ('ref', heap_id_or_None)
```

**Interval lattice** — represents sets of integers:

```
BOT = (None, None)        # empty set (unreachable)
[lo..hi]                  # set of integers in range
TOP = [-inf..+inf]        # any integer
```

Interval operations:
- `join(a, b)` — lattice join (least upper bound): `[min(a.lo, b.lo), max(a.hi, b.hi)]`
- `add`, `sub`, `mul`, `div`, `rem` — over-approximate arithmetic
- `div` — conservative: if divisor may be 0, returns TOP (not BOT)
- `ivl_cmp`, `ivl_cond_zero` — returns `"true"` / `"false"` / `"maybe"` for branches

### 4.2 String Abstract Domain

**`abstract_interpreter.py` — `StringAbs(const, length)`:**

```
StringAbs.const    = Optional[str]   # exact concrete value if known
StringAbs.length   = Interval        # over-approximate of |s|
```

- Exact string known → `const` is set, `length = [n,n]`
- String unknown → `const = None`, `length = [0,+inf]`
- Supports: `concat`, `substring`, `length`, `charAt`, `equals`, `contains`, `startsWith`, `endsWith`

**`novel_abstract_interpreter.py` — `StringAbs(prefix, length, exact)`:**

```
StringAbs.prefix   = Optional[str]   # first ≤8 chars if known (PREFIX_K = 8)
StringAbs.length   = Interval        # over-approximate of |s|
StringAbs.exact    = bool            # True iff prefix IS the full string
```

This is the **novel domain** — a prefix abstraction that is strictly more precise than just tracking length, but bounded to `K=8` characters to keep analysis tractable.

Join operation for prefix domain:

```python
# join prefixes: longest common prefix
p1, p2 = self.prefix, other.prefix
i = 0
while i < min(len(p1), len(p2), K) and p1[i] == p2[i]:
    i += 1
new_prefix = p1[:i]
```

### 4.3 Worklist Algorithm

Both interpreters use the same fixed-point worklist algorithm:

```
run_worklist_result(initial_state):
    worklist = [initial_state]
    seen: map<(method, pc_offset) → Frame>   # most precise frame seen so far
    results = []

    while worklist not empty:
        st = worklist.pop()
        key = (method, pc_offset)

        if key in seen:
            joined = join_frames(seen[key], current_frame)
            if fixed point reached → skip (no change)
            else → update seen[key], re-run from joined frame

        outcomes = step_abstract(st)
        if outcome is a string → add to results
        else → add successor states to worklist
```

**Termination guarantee:** The `abstract_interpreter.py` adds **widening** to ensure termination on loops:
- After `_WIDEN_THRESHOLD = 5` joins at the same program point, widening is applied
- `_widen_interval(prev, curr)`: if the lower bound decreased, set to `-inf`; if upper bound increased, set to `+inf`
- This blows intervals to TOP quickly, ensuring the worklist shrinks

`novel_abstract_interpreter.py` uses **pure join** without widening — faster but potentially non-terminating on some loops.

### 4.4 Initial State Construction

```python
build_initial_state_from_sig(methodid):
    for each parameter type:
        Int/Boolean/Char → locals[i] = ('int', TOP)        # any integer
        String           → heap[oid] = StringAbs.top()     # any string
                           locals[i] = ('ref', oid)
        Array            → locals[i] = ('ref', None)       # may be null
        other            → locals[i] = ('ref', None)
```

This gives the **most conservative** initial state — all inputs are unknown.

### 4.5 Opcode Dispatch — `step_abstract(state)`

The `step_abstract` function is a large `match/case` over JVM opcodes. Key behaviors:

| Opcode | Behavior |
|--------|----------|
| `Push(Int)` | Push `Interval.const(v)` |
| `Push(String "s")` | Allocate heap object with `StringAbs.const_str(s)`, push ref |
| `Load(i)` | Push `locals[i]` |
| `Store(i)` | Pop, set `locals[i]` |
| `Binary(Int, Add/Sub/Mul)` | Interval arithmetic, push result |
| `Binary(Int, Div)` | If divisor interval contains 0 → `return "divide by zero"` |
| `Binary(Int, Rem)` | Same as Div, but result is `[-maxmag+1, maxmag-1]` |
| `If(cond, target)` | Evaluate `ivl_cmp` → if "maybe", **fork into two states** |
| `Ifz(cond, target)` | Same for zero-comparisons; handles both int and ref |
| `Goto(target)` | Set `pc.offset = target` |
| `Return` | Pop frame; if no more frames → `return "ok"` |
| `ArrayLoad` | Null check, OOB check, return element (abstract) |
| `ArrayStore` | Null check, OOB check, write element |
| `ArrayLength` | Null check, push concrete `Interval.const(arr.length)` |
| `NewArray(len)` | If `len` may be negative → `"negative array size"` |
| `InvokeVirtual String.*` | Full String method modeling (8 methods) |
| `InvokeStatic String.valueOf` | Model conversion to abstract string |
| `InvokeStatic Character.*` | Model `isDigit`, `isWhitespace`, `getNumericValue` |
| `InvokeStatic other` | Push new frame for callee (inter-procedural) |
| `InvokeSpecial` | Handle `<init>` constructors (Object, String, AssertionError) |
| `Throw` | If `obj["class"] == AssertionError` → `"assertion error"`, else `"exception"` |
| `New(cls)` | Allocate heap object, push ref |
| `Dup` | Peek top of stack, push again |
| `Incr(i, d)` | `locals[i] += d` (interval arithmetic) |
| `Get($assertionsDisabled)` | Push 0 if `ASSERTIONS_ENABLED=True` (enables assertion checking) |
| `Cast(Int→Short)` | If interval fits in `[-32768, 32767]` → keep; else → short range |

### 4.6 Branching and State Forking

When a conditional branch cannot be resolved statically (result is `"maybe"`):

```python
st_true, st_false = fork_state_on_top_frame(state)
# st_true  → deep-copy heap, clone frame, set pc to branch target
# st_false → deep-copy heap, clone frame, advance pc by 1
return [st_true, st_false]
```

Both successor states are added to the worklist. This is **path-sensitive** exploration.

---

## 5. Analyzer Drivers

### 5.1 `my_analyzer.py` (paired with `abstract_interpreter.py`)

**Probability estimation — Laplace (add-one) smoothing:**

1. Run `analyze_method_no_inputs()` → get list of outcome strings
2. Count outcomes into 6 buckets
3. Map `negative array size` → `out of bounds`; `exception` → `*`
4. If total = 0 (no classified outcomes) → set all buckets to 1 (uniform prior)
5. Else → add 1 to every bucket (add-one smoothing)
6. Compute percentage: `p = count / total`, round to int, clamp to `[1%, 99%]`

**Rationale:** Prevents outputting 0% (which causes `-inf` score if wrong) and 100% (which causes `-inf` if wrong). Laplace smoothing is safe but conservative.

### 5.2 `novel_analyzer.py` (paired with `novel_abstract_interpreter.py`)

**More sophisticated probability estimation — Bayesian with biased priors:**

```python
# If we see BOTH ok and error paths:
#     boost error bucket counts by +1 before prior application
#
# Special case: ONLY ok observed → high confidence "ok" distribution
if only_ok:
    probs = {ok: 0.85, each_error: 0.03}
else:
    # Bayesian with asymmetric priors: alpha_ok=3.0, alpha_err=1.0
    probs[k] = (count[k] + alpha) / (total_raw + total_alpha)
```

**Why this is better:** The biased prior toward `ok` reflects the real distribution of Java programs (most terminate normally). If the interpreter found both ok and error paths, errors are boosted to avoid under-confidence. The special `only_ok` case gives a strong 85% confidence rather than diluting with uniform uncertainty.

---

## 6. Novel Domain — Prefix Abstraction (`novel_abstract_interpreter.py`)

The key innovation in the novel interpreter is the **prefix domain** for strings.

### What it adds over the baseline

| Feature | Baseline `StringAbs` | Novel `StringAbs` |
|---------|---------------------|-------------------|
| Tracks exact value? | Yes (full string if known) | Yes (if `\|s\| ≤ K` and `exact=True`) |
| Tracks partial info? | No — either exact or just length | Yes — tracks first ≤8 chars always |
| Join behavior | Loses const if strings differ | Computes longest common prefix |
| `equals` check | Exact match or length-disjoint | Prefix mismatch → `"false"` without exactness |
| `charAt` precision | Only for exact strings | Can return precise char code if index within known prefix |
| `startsWith` precision | Exact or length check | If `b.exact` and `b.prefix` fits in `a.prefix` → precise |

### Example of prefix advantage

```java
// Java method:
String s = "hello world";
if (s.startsWith("hello")) { ... }
```

- **Baseline:** `startsWith` returns `"maybe"` (unless exact match)
- **Novel:** `s.prefix = "hello wo"`, `b.prefix = "hello"`, `b.exact = True`, `len("hello") ≤ len(prefix)` → `"true"` exactly

### `charAt` precision in novel domain

```python
# Case 1: exact short string → return precise char code
if s_abs.exact and idx_ivl.lo == idx_ivl.hi and L.lo == L.hi:
    c = s_abs.prefix[int(idx_ivl.lo)]
    push(('int', Interval.const(ord(c))))

# Case 2: index within known prefix → still precise
elif 0 <= i < len(s_abs.prefix):
    c = s_abs.prefix[i]
    push(('int', Interval.const(ord(c))))

# Case 3: fallback → [0, 65535]
```

---

## 7. Syntactic Analyzer (`syntaxer.py`)

The simplest analyzer — uses **tree-sitter** to parse Java source files without bytecode:

1. Parse the source file for the given class
2. Find the specific method by name + parameter count
3. Search the method body for `assert_statement` nodes
4. If found: `assertion error;80%` / else: `assertion error;20%`

**Limitation:** Only detects `assertion error`. All other error categories get no prediction. This is the baseline that the abstract interpreter should decisively beat.

---

## 8. JPAMB Framework

### `jpamb/model.py` — Data model

- `Input` — tuple of JVM values, encoded as `(v1, v2, ...)`
- `Case(methodid, input, expected_result)` — one test case
- `Prediction(wager, result)` — probabilistic prediction using wager-based confidence
- `Response` — collection of predictions for all 6 categories
- `Suite` — singleton access to decompiled class files, source files, test cases

### Wager-based scoring

```
Positive wager w > 0 → confident in a specific outcome
  p = (w + 1) / (w + 2)
  score = 1 - 1/(w+1)  if correct, else -w (heavy penalty)

Negative wager w < 0 → confident it is NOT the specific outcome
Wager 0               → no information
```

### `jpamb/jvm/base.py` — JVM type system

Key types: `Int`, `Long`, `Boolean`, `Char`, `Short`, `Float`, `Double`, `String`, `Array`, `Void`

Fixed in latest commit:
- `Type.__eq__` now correctly checks type identity + encoding
- `Array.descriptor()` now correctly recurses for nested array types
- `AbsMethodID.jvm_str()` produces valid JVM method descriptors

### `jpamb/cli.py` — CLI commands

- `build` — compiles Java, decompiles to JSON, runs test cases via Docker
- `test` — runs an analyzer against all test cases and scores it
- `interpret` — runs concrete interpreter on specific method
- `evaluate` — benchmarks across full suite with timing
- `checkhealth` — validates environment (Docker, folder structure)
- `inspect` — shows decompiled bytecode for a method

---

## 9. Test Suite

Located in `test/`:

| File | What it tests |
|------|--------------|
| `test_abstract_interpreter.py` | `StringAbs` operations, `Interval` arithmetic, interpreter smoke tests on methods |
| `test_jvm.py` | JVM type parsing, encoding, equality |
| `test_jvm_bytecode.py` | Opcode parsing from decompiled JSON |
| `test_model.py` | `Input.decode`, `Case` parsing, `CASE_RE` regex |
| `test_scoring.py` | Wager → probability conversion, scoring formulas |
| `test_cli.py` | CLI command invocation |
| `test_cli_reliability.py` | Reliability of CLI (timeout handling, error recovery) |
| `test_jpamb.py` | Suite singleton, `method_opcodes` |

---

## 10. What Improved and Why — Summary

### Correctness improvements (Commit 2)

- **`str_substring` soundness:** The OOB check `j_hi > L.lo` was unsound — it compared the maximum end index against the **minimum** possible string length, potentially missing cases where `j_hi > L.hi`. Now compares against `L.hi` (worst case).
- **`charAt` soundness:** Same pattern — comparing against `L.lo` could miss OOB. Fixed to `L.hi`.
- **`interpreter.py` crash fixes:** Three separate bugs could crash the concrete interpreter (undefined variable, no-arg constructor, wrong method type).

### Precision improvements (Novel domain)

- Prefix tracking enables partial string knowledge to propagate through `startsWith`, `equals`, `charAt` without requiring full concrete knowledge.
- Biased Bayesian priors in `novel_analyzer.py` produce better-calibrated probability estimates than flat Laplace smoothing.

### Infrastructure fixes (Commit 1)

- The Docker build pipeline was entirely broken due to invalid JVM descriptors. The fix allows `jpamb build --test` to actually work and populate `target/stats/`.
- `model.py`'s `Input.decode` and `CASE_RE` fixes enable correct parsing of test cases with string inputs containing special characters.

---

## 11. Known Limitations and Areas for Future Improvement

1. **No widening in `novel_abstract_interpreter.py`** — the worklist may diverge on loops with string operations that change length iteratively.
2. **Instance fields not supported** — `Get` opcode raises `NotImplementedError` for non-static fields. Only static fields are handled.
3. **`ArrayStore` in novel interpreter** drops the updated value without storing it (the `join_avals` call from baseline is missing) — means array content updates are less precise.
4. **Array size over-approximation** — `NewArray` always uses `len_ivl.lo` even when `lo ≠ hi`, creating an array of the minimum possible size rather than modeling uncertainty.
5. **Heap aliasing** — `join_avals` on refs returns `None` if IDs differ, losing all information about the object. Full shape analysis would retain field information.
6. **No narrowing after branches** — when a branch is taken (e.g., `if x > 0`), the interpreter does not refine `x`'s interval in the taken branch. Adding narrowing/refinement would substantially reduce false positives.
7. **`InvokeVirtual` non-String** — any virtual call on non-String objects raises `NotImplementedError`. Real Java programs with custom objects would crash the analysis.
