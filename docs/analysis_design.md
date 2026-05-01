# Abstract Interpretation Design — Group ASTRA (02242)

## Overview

This document describes the two abstract domains implemented for string analysis
in the Program Analysis project, how they are wired into the JVM bytecode
interpreter, and how to run and evaluate them.

---

## 1. Baseline Domain: `StringAbs` (const + length interval)

**File:** `solutions/abstract_interpreter.py`

### Representation

```
StringAbs(const: Optional[str], length: Interval)
```

| Field    | Type              | Meaning                                      |
|----------|-------------------|----------------------------------------------|
| `const`  | `Optional[str]`   | Exact string value if known; `None` otherwise |
| `length` | `Interval(lo,hi)` | Over-approximation of the string length      |

### Key operations

| Operation      | Precision notes                                           |
|---------------|-----------------------------------------------------------|
| `join`        | Keeps `const` only if both sides agree; interval-joins lengths |
| `str_concat`  | Exact when both constants known; adds length intervals otherwise |
| `str_substring` | Exact when const + concrete indices; OOB when `j_hi > L.lo` |
| `str_equals`  | Exact for constant strings; `"false"` when lengths disjoint |
| `str_length`  | Returns length interval directly                         |
| `str_charAt`  | OOB if index outside known length; exact char code for const strings |

### Lattice

```
        ⊤  (any string, length [0,+∞))
       / \
   ...   ...
    |     |
   ⊥  (unreachable / bot)
```

`bot` is represented by `length = Interval(None, None)`.

---

## 2. Prefix Domain: `StringAbs2` (prefix + length + mayBeNull)

**File:** `solutions/string_abs2.py`  
**Interpreter:** `solutions/abstract_interpreter2.py`

### Representation

```
StringAbs2(prefix: Optional[str], length: Interval, may_be_null: bool)
```

| Field         | Type              | Meaning                                       |
|---------------|-------------------|-----------------------------------------------|
| `prefix`      | `Optional[str]`   | Longest known prefix of the string            |
| `length`      | `Interval(lo,hi)` | Over-approximation of the string length       |
| `may_be_null` | `bool`            | True if the reference may be `null`           |

### Key operations

| Operation        | Precision notes                                                     |
|-----------------|---------------------------------------------------------------------|
| `join`          | Computes longest common prefix via `_common_prefix`; joins lengths  |
| `str2_concat`   | Preserves left prefix + appends right prefix when right is fully known |
| `str2_substring`| Returns prefix of the slice when indices are concrete               |
| `str2_startsWith` | Exact `true`/`false` when known prefix is long enough             |
| `str2_equals`   | `false` when prefixes differ; `true` only for identical constants   |
| `str2_charat`   | OOB if index ≥ known length or < 0                                  |
| `str2_valueof_int` | Exact string representation for concrete integer intervals       |

### Lattice

```
        ⊤  (prefix="", length=[0,+∞), may_be_null=true)
       / \
  prefix  null
   info    only
    |       |
     \     /
       ⊥ (bot)
```

`bot` = `prefix=None, length=bot, may_be_null=False`
`null` = `prefix=None, length=bot, may_be_null=True`

---

## 3. Interpreter Architecture

Both interpreters share the same worklist-based fixpoint algorithm:

```
analyze_method_no_inputs(methodid)
  → build_initial_state(methodid)   # top value for all params
  → run_worklist(initial_state)     # fixpoint over CFG
  → List[str]                       # outcome labels
```

### State representation

```
State(
  heap  : dict[int, HeapObj]   # OID → object
  frames: Stack[Frame]         # call stack
)

Frame(
  locals : dict[int, AVal]     # local variables
  stack  : Stack[AVal]         # operand stack
  pc     : PC(method, offset)  # program counter
)

AVal = ('int', Interval) | ('ref', int | None)
```

String heap objects:
```python
heap[oid] = {
    "class":  jvm.String(),
    "fields": {"value": StringAbs | StringAbs2}
}
```

### Worklist algorithm

```
seen: dict[(method, pc_offset), Frame]  # joined frames per PC
worklist: [State]

while worklist:
    state = worklist.pop()
    frame = state.frames.peek()
    key = (frame.pc.method, frame.pc.offset)
    if key in seen:
        joined = join_frames(seen[key], frame)
        if joined == seen[key]:  # fixed point reached
            continue
        seen[key] = joined
    else:
        seen[key] = frame
    next_states = step_abstract(state)
    for ns in next_states:
        if isinstance(ns, str):  # outcome label
            collect(ns)
        else:
            worklist.append(ns)
```

---

## 4. Outcome Labels

| Label             | Meaning                              |
|------------------|--------------------------------------|
| `ok`             | Method may terminate normally        |
| `divide by zero` | Integer division by zero             |
| `assertion error`| `assert` statement may fail          |
| `out of bounds`  | Array or string index out of range   |
| `null pointer`   | Null dereference                     |
| `*`              | Any other exception / unhandled case |

---

## 5. Running the Analyzers

### Baseline analyzer (StringAbs)

```bash
# Evaluate against jpamb test suite
uv run jpamb test solutions/my_analyzer.py

# Evaluate and write JSON results
uv run jpamb evaluate solutions/my_analyzer.py > results_baseline.json
```

### Syntactic fast-path analyzer

```bash
uv run jpamb evaluate solutions/syntactic_analysis.py > results_syntactic.json
```

### String-tree analyzer (expression trees)

```bash
python solutions/abstract_interpreter_stringtree.py \
    'jpamb.cases.Strings.stringEqualsHello:(Ljava/lang/String;)V'
```

### Benchmark: baseline vs prefix comparison

```bash
python solutions/benchmark.py --max-cases 100
python solutions/benchmark.py --output results_cmp.json
```

### Unit tests

```bash
uv run pytest tests/ -v
```

---

## 6. Key Design Decisions

### Why a prefix domain?

The baseline `StringAbs` domain tracks only `(const?, length_interval)`. This
is sufficient for many checks (length OOB, equals on constants) but loses
information about the *structure* of strings immediately after `concat` or
`substring` on non-constant inputs.

The prefix domain `StringAbs2` additionally tracks the longest known prefix of
every string value. This gives us:

- **Precise `startsWith`**: if the stored prefix is long enough to answer the
  query, we avoid a `"maybe"` result.
- **Better `equals`**: two strings whose prefixes differ are definitely unequal,
  even when we don't know their full content.
- **Null-safety tracking**: the `may_be_null` flag lets us distinguish "definitely
  non-null string" from "possibly null reference" and report null-pointer errors
  precisely.

### Soundness

Every transfer function must satisfy the *soundness condition*:
> For every concrete string `s` in the concretisation of the abstract value `a`,
> the concrete result of applying the operation to `s` must be in the
> concretisation of the abstract result.

Key soundness invariants tested in `tests/test_string_abs2.py`:

- **concat length**: `len(s1 + s2)` is always within `result.length`
- **substring length**: `len(s[i:j])` is always within `result.length`
- **prefix correctness**: if `result.prefix == p`, then every concrete
  result string starts with `p`

---

## 7. File Map

| File                                | Purpose                                      |
|-------------------------------------|----------------------------------------------|
| `solutions/abstract_interpreter.py` | Baseline interpreter + StringAbs domain      |
| `solutions/string_abs2.py`          | StringAbs2 domain + transfer functions       |
| `solutions/abstract_interpreter2.py`| Full interpreter using StringAbs2            |
| `solutions/abstract_interpreter_stringtree.py` | Interpreter with expression-tree tracking |
| `solutions/stringtree.py`           | String expression node types + helpers       |
| `solutions/syntactic_analysis.py`   | Fast AST-based heuristic analyzer            |
| `solutions/my_analyzer.py`          | Main jpamb-compatible analyzer script        |
| `solutions/benchmark.py`            | Benchmark baseline vs prefix                 |
| `tests/test_string_abs.py`          | Unit tests for StringAbs baseline            |
| `tests/test_string_abs2.py`         | Unit tests for StringAbs2 prefix domain      |
| `tests/test_interpreter.py`         | Smoke tests for interpreters                 |
