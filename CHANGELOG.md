# The Change Log

## Version X.X.X

- Add Docker Image
- Change official build version to be the one compiled through docker.

## Version 0.4.0 — String Analysis & Prefix Domain (Group ASTRA)

### New Features

- **StringAbs baseline domain** (`solutions/abstract_interpreter.py`): interval-based string length abstraction with sound `str_substring` (OOB check against `L.lo`) and precise `str_charAt` (resolves exact char code when string constant is known)
- **StringAbs2 prefix domain** (`solutions/string_abs2.py`): new `Prefix + Length + mayBeNull` abstract domain; static constructors `bot()`, `top()`, `null()`, `from_const(s)`, `unknown_nonnull()`; `join` (⊔) with `_common_prefix`; `check_null` / `restrict_nonnull` for null-guard splitting
- **StringAbs2 transfer functions** (`solutions/string_abs2.py`): `str2_concat`, `str2_substring`, `str2_length`, `str2_equals`, `str2_startswith`, `str2_endswith`, `str2_contains`, `str2_charat`, `str2_valueof_int` — all with sound OOB and precision checks
- **Abstract interpreter 2** (`solutions/abstract_interpreter2.py`): complete interpreter pipeline wired to the StringAbs2 prefix domain; drop-in replacement for `abstract_interpreter.py`
- **Benchmark** (`solutions/benchmark.py`): side-by-side comparison of baseline vs. prefix abstract interpreters across the full jpamb case suite
- **Novel abstract interpreter** (`solutions/novel_abstract_interpreter.py`): extended analysis with additional precision improvements
- **Novel analyzer** (`solutions/novel_analyzer.py`): driver for the novel abstract interpreter

### Bug Fixes — Concrete Interpreter (`solutions/interpreter.py`)

- `Store`: removed invalid `_ArrayObj()` call; String refs already handled by the `Reference` branch
- `Ifz`: fixed null-check for `Reference` (was referencing undefined variable `c` with wrong comparison)
- `Throw`: robustly extracts class name for `AssertionError` detection
- Added `@staticmethod` to `Frame.from_method`; renamed `locals` shadow to avoid built-in conflict
- Removed commented-out dead code blocks (`Bytecode`, `ObjRef`, `_jump_pc`)

### Bug Fixes — Abstract Interpreter (`solutions/abstract_interpreter.py`)

- `str_substring`: OOB soundness fixed — check now uses `j_hi > L.lo` (not `L.hi`)
- `charAt`: OOB check uses `L.lo`; exact char code resolved for constant strings
- Collapsed duplicate `Push _` branches; removed dead `norm_args` loop and redundant PC re-creation

### Bug Fixes — StringAbs2 (`solutions/string_abs2.py`)

- `_common_prefix`: fixed returning `None` instead of `""` for strings with no common prefix

### Bug Fixes — jpamb CLI (`jpamb/cli.py`, `jpamb/jvm/base.py`, `jpamb/model.py`)

- `Type.__eq__`: fixed using `<=` instead of `==` (broke all type equality checks)
- `Type.__lt__`: fixed using `<=` instead of `<` (broke ordering)
- `Array.descriptor()`: fixed to recurse with `descriptor()` not `encode()` so nested String arrays produce correct JVM descriptors
- `ParameterType.descriptors()`: fixed using `encode()` instead of `descriptor()`
- `ParameterType.math()`: fixed always returning `"double"` regardless of type
- Added `MethodID.descriptor()` and `AbsMethodID.jvm_str()` to produce proper JVM descriptors (`Ljava/lang/String;` instead of `L`)
- `cli.py`: switched build-test section to use `case.methodid.jvm_str()` — was passing invalid descriptor `(L)V` to Java causing exit code 1
- `model.py`: fixed `Input.decode()` parenthesis validation (was using `and` instead of `or`); fixed `CASE_RE` from `[^)]*` to `.*` so inputs containing `)` in string values parse correctly

### Improvements — Syntactic Analysis (`solutions/syntactic_analysis.py`)

- Added substring `(start, end)` OOB detection when literal string length is known
- Fixed misaligned comment blocks; replaced placeholder group name with "ASTRA"
- Renamed confusing `inf` variable to `star_pct`

### Improvements — Analyzer Pipeline (`solutions/my_analyzer.py`)

- Log `NotImplementedError` separately (known unimplemented opcode/method)
- Log full traceback for unexpected crashes so failures are diagnosable

### Tests (`tests/`)

- `test_string_abs.py`: unit tests for baseline StringAbs domain (join, concat, substring, equals, and soundness invariants)
- `test_string_abs2.py`: unit tests for StringAbs2 prefix domain including soundness invariants
- `test_interpreter.py`: integration smoke tests for both abstract interpreters against jpamb cases

### Documentation

- `docs/analysis_design.md`: full design document covering StringAbs baseline, StringAbs2 prefix domain, interpreter architecture, worklist algorithm, soundness invariants, and file map
- `README.md`: added quick-start commands and domain comparison table

## Version 0.3.0

- Move python packages out of lib
- Add two new cases in Simple
- Add more cli tools including `interpret`, `test`, and `inspect`.
- Many fixes

## Version 0.2.0

- Move python packages to lib
- Fix unlisted case in Collatz
- Fix unlisted case in Calls.callsAssertFib

## Version 0.1.0

- Add integer and char arrays
- Add arraysNotEmpty, arraysSpellsHello, arraySumIsLarge and fix allPrimesArePositive
- Add 'jpamb_utils/' to ease the parsing and printing of MethodIds and Types for python users
- Many fixes and upgrades

## Version 0.0.2

- We started capturing version
