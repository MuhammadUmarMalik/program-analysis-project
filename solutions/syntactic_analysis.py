#!/usr/bin/env python3

import sys
import re
import logging
import jpamb
import tree_sitter
import tree_sitter_java
from pathlib import Path

log = logging
log.basicConfig(level=logging.DEBUG)

JAVA_LANGUAGE = tree_sitter.Language(tree_sitter_java.language())
Parser = tree_sitter.Parser

# --- Utilities copied/adapted from original files ---


def text(node, my_bytes):
    return my_bytes[node.start_byte : node.end_byte].decode("utf-8")


def walk(node):
    yield node
    for c in node.children:
        yield from walk(c)


def node_to_sexp(node, my_bytes, *, named_only=False):
    kids = node.named_children if named_only else node.children
    txt = my_bytes[node.start_byte : node.end_byte].decode("utf-8")
    if not kids:
        return node.type + f' "{txt}"'
    return (
        f"({node.type} {txt} "
        + " ".join(node_to_sexp(c, my_bytes, named_only=named_only) for c in kids)
        + ")"
    )


def find_method_open_brace(src: str, name: str) -> int:
    sig = re.compile(
        rf"(?<!\.)\b{re.escape(name)}\s*\([^)]*\)\s*(?:throws\s+[\w\.,\s<>?\[\]]+)?\s*\{{",
        re.DOTALL,
    )
    m = sig.search(src)
    return m.end() - 1 if m else -1


def slice_balanced_block(src: str, open_idx: int):
    if open_idx < 0 or src[open_idx] != "{":
        return None
    i, n, depth = open_idx, len(src), 0
    in_sl = in_ml = in_str = in_chr = False
    esc = False
    start = open_idx + 1
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if in_sl:
            if ch == "\n":
                in_sl = False
        elif in_ml:
            if ch == "*" and nxt == "/":
                in_ml = False
                i += 1
        elif in_str:
            if not esc and ch == '"':
                in_str = False
            esc = (not esc) and ch == "\\"
        elif in_chr:
            if not esc and ch == "'":
                in_chr = False
            esc = (not esc) and ch == "\\"
        else:
            if ch == "/" and nxt == "/":
                in_sl = True
                i += 1
            elif ch == "/" and nxt == "*":
                in_ml = True
                i += 1
            elif ch == '"':
                in_str = True
                esc = False
            elif ch == "'":
                in_chr = True
                esc = False
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return src[start:i]
        i += 1
    return None


# --- Bytecode + local source analysis (your “mixed effort Analyzer”) ---


def analyze_bytecode_and_div0(methodid, method_arg):
    """
    Inspect bytecode and (a sliced) method body to detect:
      - has_assert (AST)
      - has_assert_in_bytecode (AssertionError in bytecode)
      - has_div_by_zero (literal "/ 0" in source)
    """
    has_assert = False
    has_assert_in_bytecode = False
    has_div_by_zero = False

    # Get method metadata & bytecode through jpamb
    try:
        m = jpamb.Suite().findmethod(methodid)
    except Exception:
        m = None
    log.debug("method metadata from jpamb: %s", bool(m))

    if m and "code" in m and "bytecode" in m["code"]:
        for inst in m["code"]["bytecode"]:
            if (
                inst.get("opr") == "invoke"
                and inst.get("method", {}).get("ref", {}).get("name")
                == "java/lang/AssertionError"
            ):
                has_assert_in_bytecode = True
                break
    else:
        has_assert_in_bytecode = False

    # Parse the method argument to find the source file and method body
    try:
        classname, methodname, args = re.match(r"(.*)\.(.*):(.*)", method_arg).groups()
    except Exception:
        # Fallback: try to locate method via methodid if parsing failed
        classname = (
            str(methodid.classname.name) if hasattr(methodid, "classname") else None
        )
        methodname = methodid.extension.name if hasattr(methodid, "extension") else None
        args = ""

    path = "./src/main/java/" + classname.replace(".", "/") + ".java"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = f.read()
    except FileNotFoundError:
        log.error("Source file not found: %s", path)
        data = ""

    open_idx = find_method_open_brace(data, methodname) if data else -1
    body = slice_balanced_block(data, open_idx) if data else None

    # Parse sliced method body with tree-sitter
    if body:
        parser = Parser(JAVA_LANGUAGE)
        my_bytes = body.encode()
        tree = parser.parse(my_bytes)
        root = tree.root_node

        log.debug(node_to_sexp(root, my_bytes))
        log.debug("method body:\n%s", body)

        for n in walk(root):
            if n.type == "assert_statement":
                has_assert = True
            if n.type == "binary_expression" and "/" in text(n, my_bytes):
                src = text(n, my_bytes)
                if re.search(r"/\s*0(?![0-9])", src):
                    has_div_by_zero = True

    return {
        "has_div_by_zero": has_div_by_zero,
        "has_assert": has_assert,
        "has_assert_in_bytecode": has_assert_in_bytecode,
    }


# --- Pure syntactic analysis on full source (strings + assert + division) ---


def analyze_string_ops_and_assert(methodid):
    """
    PURE syntactic checks on the full source file:
      - detect String method calls (by method name)
      - detect assert statements
      - detect any division operator syntactically
      - detect literal division-by-zero (/ 0)
    """
    parser = Parser(JAVA_LANGUAGE)

    try:
        srcfile = jpamb.sourcefile(methodid).relative_to(Path.cwd())
    except Exception as e:
        log.error("Failed to get sourcefile for %s: %s", methodid, e)
        return {
            "has_string_ops": False,
            "string_counts": {},
            "has_assert": False,
            "has_div_operator": False,
            "has_div_zero_literal": False,
            "has_null_deref": False,
            "has_string_oob": False,
        }

    try:
        with open(srcfile, "rb") as f:
            log.debug("parse sourcefile %s", srcfile)
            data = f.read()
            tree = parser.parse(data)
    except Exception as e:
        log.error("Failed to open/parse %s: %s", srcfile, e)
        return {
            "has_string_ops": False,
            "string_counts": {},
            "has_assert": False,
            "has_div_operator": False,
            "has_div_zero_literal": False,
            "has_null_deref": False,
            "has_string_oob": False,
        }

    simple_classname = str(methodid.classname.name)
    log.debug("%s", simple_classname)

    # --- find class node (same style as example) ---
    class_q = tree_sitter.Query(
        JAVA_LANGUAGE,
        f"""
        (class_declaration 
            name: ((identifier) @class-name 
                   (#eq? @class-name "{simple_classname}"))) @class
        """,
    )

    class_caps = tree_sitter.QueryCursor(class_q).captures(tree.root_node)
    class_nodes = class_caps.get("class", [])

    if not class_nodes:
        log.error("could not find a class of name %s in %s", simple_classname, srcfile)
        return {
            "has_string_ops": False,
            "string_counts": {},
            "has_assert": False,
            "has_div_operator": False,
            "has_div_zero_literal": False,
            "has_null_deref": False,
            "has_string_oob": False,
        }

    class_node = class_nodes[0]

    # --- find correct method node (copying the style from syntaxer) ---
    method_name = methodid.extension.name

    method_q = tree_sitter.Query(
        JAVA_LANGUAGE,
        f"""
        (method_declaration name: 
          ((identifier) @method-name (#eq? @method-name "{method_name}"))
        ) @method
        """,
    )

    method_caps = tree_sitter.QueryCursor(method_q).captures(class_node)
    method_nodes = method_caps.get("method", [])

    method_node = None

    for cand in method_nodes:
        p = cand.child_by_field_name("parameters")
        if not p:
            log.debug("Could not find parameters of %s", method_name)
            continue

        params = [c for c in p.children if c.type == "formal_parameter"]

        if len(params) != len(methodid.extension.params):
            continue

        # We don't actually check the types (same as your example),
        # just ensure a type node exists for each parameter.
        for tn, t in zip(methodid.extension.params, params):
            tp = t.child_by_field_name("type")
            if tp is None or tp.text is None:
                break
            # TODO: compare tn with tp.text if you want full type matching
        else:
            method_node = cand
            break

    if method_node is None:
        log.warning(
            "could not find a method of name %s in %s", method_name, simple_classname
        )
        return {
            "has_string_ops": False,
            "string_counts": {},
            "has_assert": False,
            "has_div_operator": False,
            "has_div_zero_literal": False,
            "has_null_deref": False,
            "has_string_oob": False,
        }

    body = method_node.child_by_field_name("body")
    if not (body and body.text):
        log.error("Method has no body?")
        return {
            "has_string_ops": False,
            "string_counts": {},
            "has_assert": False,
            "has_div_operator": False,
            "has_div_zero_literal": False,
            "has_null_deref": False,
            "has_string_oob": False,
        }
    for tline in body.text.splitlines():
        log.debug("line: %s", tline.decode())

    # ------------------------------------------------------------
    # 1. STRING OPERATION COUNTS
    # ------------------------------------------------------------

    string_methods = {
        "equals",
        "length",
        "concat",
        "substring",
        "contains",
        "startsWith",
        "endsWith",
        "charAt",
    }

    string_call_q = tree_sitter.Query(
        JAVA_LANGUAGE,
        """
        (method_invocation
            name: (identifier) @method-name
        ) @call
        """,
    )
    string_caps = tree_sitter.QueryCursor(string_call_q).captures(body)
    method_name_nodes = string_caps.get("method-name", [])

    counts = {m: 0 for m in string_methods}

    for node in method_name_nodes:
        if node.text is None:
            continue
        name = node.text.decode()
        if name in counts:
            counts[name] += 1

    has_string_ops = any(v > 0 for v in counts.values())

    # ------------------------------------------------------------
    # 2. ASSERT STATEMENTS
    # ------------------------------------------------------------

    assert_q = tree_sitter.Query(JAVA_LANGUAGE, """(assert_statement) @assert""")
    assert_caps = tree_sitter.QueryCursor(assert_q).captures(body)
    has_assert = bool(assert_caps.get("assert"))

    # ------------------------------------------------------------
    # 3. DIVISION OPERATORS + DIVISION-BY-ZERO LITERAL
    # ------------------------------------------------------------

    has_div_operator = False
    has_div_zero_literal = False

    for n in walk(body):
        # only consider real binary expressions, not comments / random nodes
        if n.type != "binary_expression":
            continue
        if n.text is None:
            continue

        src = n.text.decode()

        # detect any division operator syntactically
        if "/" in src:
            has_div_operator = True
            log.debug("division candidate (syntactic): %s", src)

        # literal "/ 0" or "/0"
        if re.search(r"/\s*0(?![0-9])", src):
            has_div_zero_literal = True
            log.debug("division-by-zero literal (syntactic): %s", src)

    # ------------------------------------------------------------


        # ------------------------------------------------------------
    # 4. NULL POINTER & STRING OUT-OF-BOUNDS HEURISTICS
    # ------------------------------------------------------------

    body_src = body.text.decode()

    # --- possible-null variables ---
    maybe_null_vars = set()

    # String s = null;
    for m in re.finditer(r'\bString\s+([A-Za-z_]\w*)\s*=\s*null\b', body_src):
        maybe_null_vars.add(m.group(1))

    # s = null;
    for m in re.finditer(r'\b([A-Za-z_]\w*)\s*=\s*null\b', body_src):
        maybe_null_vars.add(m.group(1))

    # any call like: s.foo(...)
    has_null_deref = False
    for m in re.finditer(r'\b([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*\(', body_src):
        var = m.group(1)
        if var in maybe_null_vars:
            has_null_deref = True
            log.debug("null dereference candidate: %s.%s(...)", var, m.group(2))
            break

    # --- string literal lengths ---
    string_literal_lengths = {}

    # crude but effective: String s = "hi";
    for m in re.finditer(r'\bString\s+([A-Za-z_]\w*)\s*=\s*"([^"\n]*)"', body_src):
        var = m.group(1)
        lit = m.group(2)
        string_literal_lengths[var] = len(lit)

    # look for s.charAt(k) where k >= len(literal)
    has_string_oob = False
    for m in re.finditer(r'\b([A-Za-z_]\w*)\s*\.\s*charAt\s*\(\s*(\d+)\s*\)', body_src):
        var = m.group(1)
        idx = int(m.group(2))
        if var in string_literal_lengths:
            length = string_literal_lengths[var]
            if idx >= length:
                has_string_oob = True
                log.debug(
                    "string literal OOB candidate: %s.charAt(%d) with length %d",
                    var, idx, length
                )
                break


    return {
        "has_string_ops": has_string_ops,
        "string_counts": counts,
        "has_assert": has_assert,
        "has_div_operator": has_div_operator,
        "has_div_zero_literal": has_div_zero_literal,
        "has_null_deref": has_null_deref,
        "has_string_oob": has_string_oob,
    }


# --- Main entrypoint ---

if __name__ == "__main__":
    # Use the my_analyzer metadata for info output (keeps original identity)
    methodid = jpamb.getmethodid(
        "mixed effort Analyzer",
        "1.0 beta",
        "Shit on wheels",
        ["mixed", "python"],
        for_science=True,
    )

    if len(sys.argv) == 2 and sys.argv[1] == "info":
        print("mixed effort Analyzer")
        print("1.0 beta")
        print("Shit on wheels")
        print("mixed,python")
        print("no")
        sys.exit(0)

    if len(sys.argv) < 2:
        print("Usage: <script> <ClassName.methodName:(Sig)> or 'info'")
        sys.exit(1)

    method_arg = sys.argv[1]

    # Run the bytecode/div-zero analysis and get detected facts
    facts = analyze_bytecode_and_div0(methodid, method_arg)

    # Run String and assertion syntactic checks
    synt_facts = analyze_string_ops_and_assert(methodid)

    # ---------------- Syntactic Analysis (strings + syntactic div/assert) ----------
    if synt_facts:
        print("=== Syntactic Analysis ===")

        print(f"string-operations: {synt_facts.get('has_string_ops', False)}")
        print("string-counts:")
        for k, v in synt_facts.get("string_counts", {}).items():
            print(f"  {k}: {v}")

        print(f"assert-detected: {synt_facts.get('has_assert', False)}")
        print(f"division-operator: {synt_facts.get('has_div_operator', False)}")
        print(
            f"division-by-zero-literal: {synt_facts.get('has_div_zero_literal', False)}"
        )
        print(
            f"has_null_deref: {synt_facts.get('has_null_deref', False)}"
        )
        print(
            f"oob-detected: {synt_facts.get('has_string_oob', False)}"
        )
        print()

    # ---------------- Control-flow / bytecode-based facts -------------------------
    has_div_by_zero = facts.get("has_div_by_zero", False)
    has_assert = facts.get("has_assert", False)
    has_assert_in_bytecode = facts.get("has_assert_in_bytecode", False)

    print("=== Control-flow / bytecode facts ===")
    print(f"source-has-assert-stmt:       {has_assert}")
    print(f"bytecode-has-AssertionError:  {has_assert_in_bytecode}")
    print(f"source-has-literal-div-by-0:  {has_div_by_zero}")
    print()

        # --- Heuristic probabilities (using syntactic + bytecode features) ---

    synt_has_assert = synt_facts.get("has_assert", False)
    synt_div_zero = synt_facts.get("has_div_zero_literal", False)
    synt_div_operator = synt_facts.get("has_div_operator", False)
    synt_null_deref = synt_facts.get("has_null_deref", False)
    synt_string_oob = synt_facts.get("has_string_oob", False)

    # Divide-by-zero flags
    div_strong = has_div_by_zero or synt_div_zero            # literal /0
    div_weak = synt_div_operator and not div_strong          # some division, but not clearly /0

    # Assertion flag
    assert_flag = has_assert or has_assert_in_bytecode or synt_has_assert

    # Null-pointer and string-OOB flags
    npe_flag = synt_null_deref
    oob_flag = synt_string_oob

    # --- Map flags to probabilities ---

    # divide by zero
    if div_strong:
        div0 = 80
    elif div_weak:
        div0 = 60
    else:
        div0 = 20

    # assertion error: asserts might or might not fail
    asrt = 40 if assert_flag else 20

    # null pointer
    npe = 80 if npe_flag else 20

    # out of bounds (string index)
    oob = 80 if oob_flag else 20

    # ok: high only when we see no strong error signals
    if not (div_strong or assert_flag or npe_flag or oob_flag):
        ok = 80
    else:
        ok = 40

    # keep * as some neutral-ish background category
    inf = 20

    preds = [
        f"ok;{ok}%",
        f"divide by zero;{div0}%",
        f"assertion error;{asrt}%",
        f"out of bounds;{oob}%",
        f"null pointer;{npe}%",
        f"*;{inf}%",
    ]

    print("=== Prediction Heuristics ===")
    for p in preds:
        print(p)


    sys.exit(0)
