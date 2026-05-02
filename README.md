# 02242-Program-Analysis-Project

Project Analysis Final Project Code for Project "ASTRA"

---

## Build

```bash
# Build all Java test cases
uv run jpamb -vv build
```

---

## Concrete Interpreter

```bash
# Run interpreter (stepwise, with warnings, 10s timeout)
uv run jpamb interpret -W --timeout 10 --stepwise solutions/interpreter.py

# Test interpreter against expected output
uv run jpamb test solutions/interpreter.py

# Run interpreter on a single method
uv run python solutions/interpreter.py "jpamb.cases.Arrays.binarySearch:(I)V"
```

---

## Abstract Interpreter (my_analyzer)

```bash
# Run full test suite
uv run jpamb test solutions/my_analyzer.py

# Run on a single method
uv run python solutions/my_analyzer.py "jpamb.cases.Arrays.arraySometimesNull:(I)V"

# Run pytest unit tests
uv run pytest test/test_abstract_interpreter.py

# Save evaluation results to file
uv run jpamb evaluate solutions/my_analyzer.py > my_analyzer_result.json
```

---

## Novel Abstract Interpreter (novel_analyzer)

```bash
# Run full test suite
uv run jpamb test solutions/novel_analyzer.py

# Run on a single method
uv run python solutions/novel_analyzer.py "jpamb.cases.Arrays.binarySearch:(I)V"

# Save evaluation results to file
uv run jpamb evaluate solutions/novel_analyzer.py > novel_analyzer_result.json
```

---

## Syntactic Analysis

```bash
# Run syntactic analysis on a single method
uv run solutions/syntactic_analysis.py "jpamb.cases.Strings.stringEqualsHello:(Ljava/lang/String;)V"

# Save evaluation results to file
uv run jpamb evaluate solutions/syntactic_analysis.py > syntactic_result.json
```

---

## Inspect Bytecode

```bash
# View bytecode of any method
uv run jpamb inspect "jpamb.cases.Arrays.binarySearch:(I)V"
uv run jpamb inspect "jpamb.cases.Strings.stringEqualsHello:(Ljava/lang/String;)V"
```

---

## Useful Flags

```bash
# Filter to a single test case
uv run jpamb test solutions/my_analyzer.py --filter "jpamb.cases.Arrays.arraySometimesNull:(I)V"

# Verbose output (show scores per case)
uv run jpamb test solutions/my_analyzer.py --verbose

# List all available test cases
uv run jpamb test solutions/my_analyzer.py --list
```
