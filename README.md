# 02242-Program-Analysis-Project
Project Analysis Final Project Code for Project "ASTRA"

See [docs/analysis_design.md](docs/analysis_design.md) for the full design
document covering both abstract domains and the interpreter architecture.

## Quick start

```bash
# Build jpamb test suite
uv run jpamb -vv build

# Run concrete interpreter
uv run jpamb interpret -W --timeout 10 --stepwise solutions/interpreter.py

# Run unit tests
uv run pytest tests/ -v

# Run main analyzer (baseline StringAbs domain)
uv run jpamb test solutions/my_analyzer.py

# Evaluate and dump JSON results
uv run jpamb evaluate solutions/my_analyzer.py > results_baseline.json

# Syntactic fast-path analyzer
uv run jpamb evaluate solutions/syntactic_analysis.py > results_syntactic.json

# Benchmark: baseline vs prefix abstract interpreter
python solutions/benchmark.py --max-cases 100

# String-tree analyzer (expression-tree tracking)
python solutions/abstract_interpreter_stringtree.py \
    'jpamb.cases.Strings.stringEqualsHello:(Ljava/lang/String;)V'
```

## Abstract domains

| Domain       | File                          | Key extras               |
|-------------|-------------------------------|--------------------------|
| StringAbs   | `solutions/abstract_interpreter.py` | const + length interval |
| StringAbs2  | `solutions/string_abs2.py`    | prefix + length + null flag |
| StringTree  | `solutions/stringtree.py`     | symbolic expression trees |
