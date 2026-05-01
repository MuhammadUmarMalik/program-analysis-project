# 02242-Program-Analysis-Project
Project Analysis Final Project Code for Project "ASTRA"

# Command to run current code
uv run jpamb -vv build

# Command to run Interpreter
uv run jpamb interpret -W  --timeout 10 --stepwise solutions/interpreter.py

# Command to test abstraction (run tests)
uv run pytest test\test_abstract_interpreter.py

# Command to run analyzer
uv run jpamb test  .\solutions\my_analyzer.py

# Command to run syntactic analysis for strings
uv run solutions/syntactic_analysis.py 'any method from cases' (example:'jpamb.cases.Strings.stringEqualsHello:(Ljava lang/String;)V')

# Command to get scores/results in a file for syntactic analysis
uv run jpamb evaluate ./solutions/syntactic_analysis.py > syntactic_result.json

# Command to test novel abstraction (run tests)
uv run jpamb test .\solutions\novel_analyzer.py
