from dataclasses import dataclass
from math import inf
from typing import Optional, Tuple, Union
from typing import Dict, List
import jpamb
from jpamb import jvm
from dataclasses import dataclass
import sys
from loguru import logger
import copy

# string expression trees
import os

# When this file is executed directly (e.g. `python solutions/abstract_interpreter_stringtree.py`)
# ensure the repository root is on `sys.path` so local packages like `solutions` and `jpamb`
# can be imported. This keeps the module runnable both as a script and as an imported module.
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from solutions import stringtree as st


# helper: format an expression or StringAbs into a pretty-printed tree
def _format_expr(expr_or_abs) -> str:
    # accept either a raw expr (from `stringtree`) or a StringAbs instance
    expr = None
    try:
        if expr_or_abs is None:
            return "None"
        # if it's a StringAbs, extract its .expr
        if isinstance(expr_or_abs, StringAbs):
            expr = expr_or_abs.expr
        else:
            expr = expr_or_abs
        try:
            simp = st.simplify(expr) if expr is not None else None
        except Exception:
            simp = expr
        try:
            pretty_s = st.pretty(simp, indent=2) if simp is not None else "None"
        except Exception:
            pretty_s = repr(simp)
        return pretty_s
    except Exception:
        return repr(expr_or_abs)


# Collector for string-operator trees encountered during a single analysis run.
STRING_OPS: List[str] = []


def record_string_op(name: str, details: str):
    """Record a short, pre-formatted description of a string operator use.

    Kept in-memory per run and printed at the end of the call.
    """
    try:
        STRING_OPS.append(f"{name}:\n{details}")
    except Exception:
        STRING_OPS.append(f"{name}: (formatting error)")

NEG_INF = -inf
POS_INF = inf
warnings = []

ASSERTIONS_ENABLED = True

@dataclass(frozen=True)
class Interval:
    lo: Optional[float]
    hi: Optional[float]

    @staticmethod
    def bot() -> 'Interval':
        return Interval(None, None)

    @staticmethod
    def top() -> 'Interval':
        return Interval(NEG_INF, POS_INF)

    @staticmethod
    def const(v: int) -> 'Interval':
        return Interval(v, v)

    @property
    def is_bot(self) -> bool:
        return self.lo is None and self.hi is None

    @property
    def is_top(self) -> bool:
        return (self.lo == NEG_INF and self.hi == POS_INF)

    def __str__(self) -> str:
        if self.is_bot:
            return "BOT"

        if self.is_top:
            return "TOP"

        def b(x):
            return "-inf" if x == NEG_INF else ("+inf" if x == POS_INF else str(int(x)))

        return f"[{b(self.lo)}..{b(self.hi)}]"

    # methods for concrete string test
    @staticmethod
    def abstract_str(s: str) -> "Interval":
        # abstraction of a *single* concrete string α(s) = [|s|, |s|]
        n = len(s)
        return Interval(n, n)

    # methods for set of strings tests
    @staticmethod
    def abstract_set(strings: set[str]) -> "Interval":
        if not strings:
            return Interval.bot()
        lengths = [len(s) for s in strings]
        return Interval(min(lengths), max(lengths))


@dataclass
class StringAbs:
    const: Optional[str]  # exact value if known
    length: Interval      # interval over |s|
    expr: Optional[object] = None

    @staticmethod
    def bot() -> "StringAbs":
        return StringAbs(None, Interval.bot(), None)

    @staticmethod
    def top() -> "StringAbs":
        # strings of any length >= 0
        return StringAbs(None, Interval(0, POS_INF), None)

    @staticmethod
    def const_str(s: str) -> "StringAbs":
        return StringAbs(s, Interval.const(len(s)), st.const(s))  # a single concrete string gets [n,n]

    @property
    def is_bot(self) -> bool:
        return self.length.is_bot

    def join(self, other: "StringAbs") -> "StringAbs":
        if self.is_bot:
            return other
        if other.is_bot:
            return self
            const = self.const if self.const == other.const else None
        expr = self.expr if self.expr == other.expr else None
        return StringAbs(const, join(self.length, other.length), expr)

    def __str__(self) -> str:
        if self.const is not None:
            return f"\"{self.const}\":{self.length}"
            return f"Str{self.length}"


# Abstract value:
#  - ('int', Interval)
#  - ('ref', heap_index_or_None)
AVal = tuple[str, Interval | None]

@dataclass
class PC:
    method: jvm.AbsMethodID
    offset: int

    def __iadd__(self, delta):
        self.offset += delta
        return self

    def __add__(self, delta):
        return PC(self.method, self.offset + delta)

    def __str__(self):
        return f"{self.method}:{self.offset}"


# -------------------------
# Bytecode loader
# -------------------------
@dataclass
class Bytecode:
    suite: jpamb.Suite
    methods: dict[jvm.AbsMethodID, list[jvm.Opcode]]
    offset_to_index: dict[jvm.AbsMethodID, dict[int, int]] = None

    def __post_init__(self):
        if self.offset_to_index is None:
            self.offset_to_index = {}

    def _ensure_loaded(self, method: jvm.AbsMethodID):
        if method not in self.methods:
            ops = list(self.suite.method_opcodes(method))
            self.methods[method] = ops
            # build a precise offset→index map
            self.offset_to_index[method] = {op.offset: i for i, op in enumerate(ops)}

    def __getitem__(self, pc: PC) -> jvm.Opcode:
        self._ensure_loaded(pc.method)
        # NOTE: pc.offset is treated as index; if you want real offsets, use offset_to_index.
        return self.methods[pc.method][pc.offset]


@dataclass
class Stack[T]:
    items: list[T]

    def __bool__(self) -> bool:
        return len(self.items) > 0

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    @classmethod
    def empty(cls):
        return cls([])

    def peek(self) -> T:
        return self.items[-1]

    def pop(self) -> T:
        return self.items.pop(-1)

    def push(self, value: T):
        self.items.append(value)
        return self

    def __str__(self):
        if not self:
            return "ϵ"
        return "".join(f"{v}" for v in self.items)


suite = jpamb.Suite()
bc = Bytecode(suite, dict())


@dataclass
class Frame:
    locals: dict[int, AVal]
    stack: Stack[AVal]
    pc: PC

    def __str__(self):
        locals_s = ", ".join(f"{k}:{v}" for k, v in sorted(self.locals.items()))
        return f"<{{{locals_s}}}, {self.stack}, {self.pc}>"

    @staticmethod
    def from_method(method: jvm.AbsMethodID) -> "Frame":
        return Frame({}, Stack.empty(), PC(method, 0))


@dataclass
class State:
    heap: dict[int, object]
    frames: Stack[Frame]

    def __str__(self):
        return f"{self.heap} {self.frames}"
    

@dataclass
class _ArrayObj:
    __slots__ = ("type", "length", "data")

    def __init__(self, elem_type, length: int):
        self.type = elem_type
        self.length = length
            # default-fill by element kind
        if isinstance(elem_type, jvm.Int) or elem_type == jvm.Int():
            self.data = [('int', Interval.const(0)) for _ in range(length)]
        elif isinstance(elem_type, jvm.Char) or elem_type == jvm.Char():
            self.data = [('int', Interval.const(0)) for _ in range(length)]
        else:
            # reference / other kinds start null
            self.data = [('ref', None) for _ in range(length)]


# -------------------------
# Helpers: Interval ops
# -------------------------
def norm(i: Interval) -> Interval:
    if i.is_bot:
        return i
    lo, hi = i.lo, i.hi
    if lo > hi:
        return Interval.bot()
    return i


def join(a: Interval, b: Interval) -> Interval:
    if a.is_bot:
        return b
    if b.is_bot:
        return a
    return Interval(min(a.lo, b.lo), max(a.hi, b.hi))


def leq(a: Interval, b: Interval) -> bool:
    # lattice order: a ⊑ b
    if a.is_bot:
        return True
    if b.is_bot:
        return False
    return b.lo <= a.lo and a.hi <= b.hi


def add(a: Interval, b: Interval) -> Interval:
    if a.is_bot or b.is_bot:
        return Interval.bot()
    return Interval(a.lo + b.lo, a.hi + b.hi)


def sub(a: Interval, b: Interval) -> Interval:
    if a.is_bot or b.is_bot:
        return Interval.bot()
    return Interval(a.lo - b.hi, a.hi - b.lo)


def mul_bounds(xs: Tuple[float, float], ys: Tuple[float, float]) -> Tuple[float, float]:
    products = [xs[0] * ys[0], xs[0] * ys[1], xs[1] * ys[0], xs[1] * ys[1]]
    return (min(products), max(products))


def mul(a: Interval, b: Interval) -> Interval:
    if a.is_bot or b.is_bot:
        return Interval.bot()
    lo, hi = mul_bounds((a.lo, a.hi), (b.lo, b.hi))
    return Interval(lo, hi)


def contains_zero(i: Interval) -> bool:
    if i.is_bot:
        return False
    return i.lo <= 0.0 <= i.hi


def div(a: Interval, b: Interval) -> Interval:
    # If divisor *may* be 0, be conservative.
    if a.is_bot or b.is_bot:
        return Interval.bot()
    if b.lo == 0 and b.hi == 0:
        # caller should have trapped "divide by zero"
        return Interval.bot()

    if contains_zero(b):
        return Interval.top()

    inv_candidates = [1 / b.lo, 1 / b.hi]
    b1, b2 = min(inv_candidates), max(inv_candidates)
    lo, hi = mul_bounds((a.lo, a.hi), (b1, b2))
    return Interval(lo, hi)


def rem(a: Interval, b: Interval) -> Interval:
    if a.is_bot or b.is_bot:
        return Interval.bot()
    if contains_zero(b):
        return Interval.top()
    if b.lo == NEG_INF or b.hi == POS_INF:
        return Interval.top()
    maxmag = max(abs(b.lo), abs(b.hi))
    if maxmag == POS_INF:
        return Interval.top()
    upper = max(0, maxmag - 1)
    return Interval(-upper, upper)


def ivl_cmp(a: Interval, b: Interval, cond: str) -> str:
    if a.is_bot or b.is_bot:
        return "false"
    if cond == "eq":
        if a.lo == a.hi == b.lo == b.hi:
            return "true" if a.lo == b.lo else "false"
        if a.hi < b.lo or b.hi < a.lo:
            return "false"
        return "maybe"
    if cond == "ne":
        if a.lo == a.hi == b.lo == b.hi:
            return "false" if a.lo == b.lo else "true"
        if a.hi < b.lo or b.hi < a.lo:
            return "true"
        return "maybe"
    if cond == "lt":
        if a.hi < b.lo:
            return "true"
        if a.lo >= b.hi:
            return "false"
        return "maybe"
    if cond == "le":
        if a.hi <= b.lo:
            return "true"
        if a.lo > b.hi:
            return "false"
        return "maybe"
    if cond == "gt":
        if b.hi < a.lo:
            return "true"
        if b.lo >= a.hi:
            return "false"
        return "maybe"
    if cond == "ge":
        if b.hi <= a.lo:
            return "true"
        if b.lo > a.hi:
            return "false"
        return "maybe"
    return "maybe"


def ivl_cond_zero(i: Interval, cond: str) -> str:
    # returns "true"/"false"/"maybe"
    if i.is_bot:
        return "false"  # dead
    lo, hi = i.lo, i.hi
    if cond == "eq":
        if lo == 0.0 and hi == 0.0:
            return "true"
        if hi < 0.0 or lo > 0.0:
            return "false"
        return "maybe"
    if cond == "ne":
        if lo == 0.0 and hi == 0.0:
            return "false"
        if hi < 0.0 or lo > 0.0:
            return "true"
        return "maybe"
    if cond == "lt":
        if hi < 0.0:
            return "true"
        if lo >= 0.0:
            return "false"
        return "maybe"
    if cond == "le":
        if hi <= 0.0:
            return "true"
        if lo > 0.0:
            return "false"
        return "maybe"
    if cond == "gt":
        if lo > 0.0:
            return "true"
        if hi <= 0.0:
            return "false"
        return "maybe"
    if cond == "ge":
        if lo >= 0.0:
            return "true"
        if hi < 0.0:
            return "false"
        return "maybe"
    return "maybe"


# -------------------------
# Operations for strings
# -------------------------
def str_concat(a: StringAbs, b: StringAbs) -> StringAbs:
    if a.is_bot or b.is_bot:
        return StringAbs.bot()
    length = add(a.length, b.length)
    const = None
    expr = None
    if a.const is not None and b.const is not None:
        const = a.const + b.const
        length = Interval.const(len(const))
        expr = st.const(const)
    else:
        if a.expr is not None or b.expr is not None:
            left = a.expr if a.expr is not None else st.unknown()
            right = b.expr if b.expr is not None else st.unknown()
            expr = st.concat(left, right)
    return StringAbs(const, length, expr)


def str_substring(s: StringAbs, i_ivl: Interval, j_ivl: Interval) -> Tuple[StringAbs, bool]:
    """
    Returns (result_string_abs, may_out_of_bounds)
    """
    if s.is_bot or i_ivl.is_bot or j_ivl.is_bot:
        return StringAbs.bot(), False

    may_oob = False

    # over-approximate index range
    i_lo, i_hi = i_ivl.lo, i_ivl.hi
    j_lo, j_hi = j_ivl.lo, j_ivl.hi

    # length of original string
    L = s.length

    if L.is_bot:
        may_oob = True
    else:
        if i_lo < 0 or j_lo < 0:
            may_oob = True
        if i_hi >= L.hi or j_hi > L.hi:
            may_oob = True
        if i_lo > j_hi:
            may_oob = True

    sub_lo = max(0, j_lo - i_hi)
    sub_hi = max(0, j_hi - i_lo)
    sub_len = Interval(sub_lo, sub_hi)

    const = None
    expr = None
    if (
        s.const is not None
        and not i_ivl.is_bot
        and not j_ivl.is_bot
        and i_ivl.lo == i_ivl.hi
        and j_ivl.lo == j_ivl.hi
    ):
        i = int(i_ivl.lo)
        j = int(j_ivl.lo)
        try:
            const = s.const[i:j]
            sub_len = Interval.const(len(const))
            expr = st.const(const)
        except Exception:
            may_oob = True
    else:
        if s.expr is not None and not i_ivl.is_bot and not j_ivl.is_bot:
            if i_ivl.lo == i_ivl.hi and j_ivl.lo == j_ivl.hi:
                expr = st.substring(s.expr, int(i_ivl.lo), int(j_ivl.lo))

    return StringAbs(const, sub_len, expr), may_oob


def str_length(s: StringAbs) -> Interval:
    return s.length if not s.is_bot else Interval.bot()


def str_equals(a: StringAbs, b: StringAbs) -> str:
    if a.is_bot or b.is_bot:
        return "false"
    if a.const is not None and b.const is not None:
        return "true" if a.const == b.const else "false"
    if a.length.hi < b.length.lo or b.length.hi < a.length.lo:
        return "false"
    return "maybe"


def str_contains(a: StringAbs, b: StringAbs) -> str:
    if a.is_bot or b.is_bot:
        return "false"
    if a.const is not None and b.const is not None:
        return "true" if b.const in a.const else "false"
    if not a.length.is_bot and not b.length.is_bot:
        if b.length.lo > a.length.hi:
            return "false"
    return "maybe"


def str_startswith(a: StringAbs, b: StringAbs) -> str:
    if a.is_bot or b.is_bot:
        return "false"
    if a.const is not None and b.const is not None:
        return "true" if a.const.startswith(b.const) else "false"
    if not a.length.is_bot and not b.length.is_bot and b.length.lo > a.length.hi:
        return "false"
    return "maybe"


def str_endswith(a: StringAbs, b: StringAbs) -> str:
    if a.is_bot or b.is_bot:
        return "false"
    if a.const is not None and b.const is not None:
        return "true" if a.const.endswith(b.const) else "false"
    if not a.length.is_bot and not b.length.is_bot and b.length.lo > a.length.hi:
        return "false"
    return "maybe"


# -------------------------
# Helpers for AVal
# -------------------------
def aval_int_const(n: int) -> AVal:
    return ('int', Interval.const(int(n)))


def aval_int_top() -> AVal:
    return ('int', Interval.top())


def is_int(v: AVal) -> bool:
    try:
        if isinstance(v, tuple):
            return v[0] == 'int'
        # support jpamb.jvm.Value objects
        if hasattr(v, 'type'):
            return isinstance(v.type, jvm.Int)
    except Exception:
        pass
    return False


def is_ref(v: AVal) -> bool:
    try:
        if isinstance(v, tuple):
            return v[0] == 'ref'
        # support jpamb.jvm.Value objects whose .type is a Reference/Object/Array/String
        if hasattr(v, 'type'):
            return isinstance(v.type, (jvm.Reference, jvm.Object, jvm.Array, jvm.String))
    except Exception:
        pass
    return False


def get_int_ivl(v: AVal) -> Interval:
    assert is_int(v), f"expected int aval, got {v}"
    if isinstance(v, tuple):
        return v[1]
    # jpamb Value object
    if hasattr(v, 'value'):
        if v.value is None:
            return Interval.top()
        return Interval.const(int(v.value))
    raise AssertionError(f"unexpected int aval: {v}")


def get_ref_id(v: AVal) -> Optional[int]:
    assert is_ref(v), f"expected ref aval, got {v}"
    if isinstance(v, tuple):
        return v[1]
    if hasattr(v, 'value'):
        return v.value
    raise AssertionError(f"unexpected ref aval: {v}")


# --------------------------------------
# Helpers for maybe branch in if and ifz
# --------------------------------------
def clone_frame(fr: Frame) -> Frame:
    return Frame(
        locals=dict(fr.locals),
        stack=Stack(list(fr.stack.items)),
        pc=PC(fr.pc.method, fr.pc.offset),
    )

def fork_state_on_top_frame(state: State) -> tuple[State, State]:
    # copy heap for both branches
    heap_true = copy.deepcopy(state.heap)
    heap_false = copy.deepcopy(state.heap)

    # copy frames list
    frames_prefix = state.frames.items[:-1]
    fr = state.frames.peek()
    fr_true = clone_frame(fr)
    fr_false = clone_frame(fr)

    st_true = State(heap=heap_true, frames=Stack(list(frames_prefix) + [fr_true]))
    st_false = State(heap=heap_false, frames=Stack(list(frames_prefix) + [fr_false]))
    return st_true, st_false

# -------------------------
# STEP
# -------------------------
def step_abstract(state: State) -> list[State] | str:
    assert isinstance(state, State), f"expected frame but got {state}"
    frame = state.frames.peek()
    opr = bc[frame.pc]
    logger.debug(f"STEP {opr}\n{state}")

    def push(v: AVal):
        frame.stack.push(v)

    def pop() -> AVal:
        try:
            return frame.stack.pop()
        except IndexError as e:
            # Log detailed debugging information to help locate the underflow
            logger.error(f"Stack underflow in step_abstract while handling {opr!r}")
            logger.error(f"Current frame: {frame}")
            logger.error(f"Frames stack: {state.frames}")
            logger.error(f"Heap keys: {list(state.heap.keys())}")
            # Recover conservatively by returning an unknown int interval
            # so the analysis can continue rather than crash the harness.
            return ('int', Interval.top())

    match opr:

        # ----------------- Push -----------------
        case jvm.Push(value=v):
            match v.type:
                case jvm.String():
                    s = "" if v.value is None else str(v.value)
                    s_abs = StringAbs.const_str(s)
                    oid = len(state.heap)
                    state.heap[oid] = {
                        "class": v.type,
                        "fields": {"value": s_abs, "expr": s_abs.expr},
                    }
                    # Log the created string object's const, expression tree and any concrete value
                    expr = s_abs.expr
                    try:
                        simp = st.simplify(expr) if expr is not None else None
                    except Exception:
                        simp = expr
                    try:
                        con = st.eval_expr(expr) if expr is not None else s_abs.const
                    except Exception:
                        con = s_abs.const
                    # pretty-print the expression tree for easier inspection
                    try:
                        pretty_s = st.pretty(simp, indent=2) if simp is not None else "None"
                    except Exception:
                        pretty_s = repr(simp)
                    logger.info(f"created String@{oid}: const={s_abs.const!r} expr=\n{pretty_s} concrete={con!r}")
                    frame.stack.push(jvm.Value.ref(oid))
                case _:
                    # unknown literal kind -> conservative int TOP
                    frame.stack.push(v)

            frame.pc += 1
            return [state]
        # ----------------- Load ----------------------------------
        case jvm.Load(type=t, index=i):
            v = frame.locals[i]
            push(v)
            frame.pc += 1
            return [state]

        # ----------------- Binary int operations -----------------
        case jvm.Binary(type=jvm.Int(), operant=o):
            v2 = pop()
            v1 = pop()
            assert is_int(v1) and is_int(v2), f"expected ints, got {v1}, {v2}"
            i1 = get_int_ivl(v1)
            i2 = get_int_ivl(v2)

            match o:
                case jvm.BinaryOpr.Add:
                    res = add(i1, i2)
                    push(('int', res))
                case jvm.BinaryOpr.Sub:
                    res = sub(i1, i2)
                    push(('int', res))
                case jvm.BinaryOpr.Mul:
                    res = mul(i1, i2)
                    push(('int', res))
                case jvm.BinaryOpr.Div:
                    if contains_zero(i2):
                        return "divide by zero"
                    res = div(i1, i2)
                    push(('int', res))
                case jvm.BinaryOpr.Rem:
                    if contains_zero(i2):
                        return "divide by zero"
                    res = rem(i1, i2)
                    push(('int', res))
                case _:
                    raise NotImplementedError(f"Unhandled binary operant: {o}")

            frame.pc += 1
            return [state]

        # ----------------- Return -----------------
        case jvm.Return(type=type_):
            if type_ is not None:
                v1 = pop()
            else:
                v1 = None

            state.frames.pop()
            if state.frames:
                frame = state.frames.peek()
                if type_ is not None:
                    frame.stack.push(v1)
                frame.pc += 1
                return [state]
            else:
                return "ok"

        # ----------------- Cast Int->Short (approximate) -----------------
        case jvm.Cast(from_=jvm.Int(), to_=jvm.Short()):
            v1 = frame.stack.pop()
            # accept both tuple-form AVals and jpamb.Value objects
            assert is_int(v1), f"expected type int, but got {v1}"
            # Try to compute a concrete short when we have a concrete int
            if isinstance(v1, tuple):
                ivl = get_int_ivl(v1)
                if not ivl.is_bot and ivl.lo == ivl.hi and ivl.lo not in (NEG_INF, POS_INF):
                    raw = int(ivl.lo)
                    short_val = ((raw + 2*15) % 216) - 2*15
                    frame.stack.push(('int', Interval.const(int(short_val))))
                else:
                    frame.stack.push(('int', Interval.top()))
            else:
                # jpamb.Value-like object
                try:
                    if getattr(v1, 'value', None) is not None:
                        raw = int(v1.value)
                        short_val = ((raw + 2*15) % 216) - 2*15
                        frame.stack.push(('int', Interval.const(int(short_val))))
                    else:
                        frame.stack.push(('int', Interval.top()))
                except Exception:
                    frame.stack.push(('int', Interval.top()))
            frame.pc += 1
            return [state]

        # ----------------- If (binary) -----------------
        case jvm.If(condition=cond, target=t):
            v2 = pop()
            v1 = pop()
            c = (cond or "").lower()

            if is_int(v1) and is_int(v2):
                i1 = get_int_ivl(v1)
                i2 = get_int_ivl(v2)
                res = ivl_cmp(i1, i2, c)
                if res == "true":
                    frame.pc.offset = t
                    return [state]
                if res == "false":
                    frame.pc += 1
                    return [state]
                # maybe -> branch
                st_true, st_false = fork_state_on_top_frame(state)
                st_true.frames.peek().pc.offset = t
                st_false.frames.peek().pc += 1
                return [st_true, st_false]

            elif is_ref(v1) and is_ref(v2):
                r1 = get_ref_id(v1)
                r2 = get_ref_id(v2)
                if c == "is":
                    take = (r1 == r2)
                elif c == "isnot":
                    take = (r1 != r2)
                else:
                    raise NotImplementedError(f"Unknown If condition: {cond!r}")

                if take:
                    frame.pc.offset = t
                else:
                    frame.pc += 1
                return [state]

            else:
                raise TypeError(f"if expected two ints or two refs, got {v1}, {v2}")

        # ----------------- Ifz -----------------
        case jvm.Ifz(condition=cond, target=tgt):
            v1 = pop()
            cond = (cond or "").lower()

            if is_int(v1):
                ivl = get_int_ivl(v1)
                res = ivl_cond_zero(ivl, cond)
                if res == "true":
                    take = True
                elif res == "false":
                    take = False
                else: #maybe
                    st_true, st_false = fork_state_on_top_frame(state)
                    st_true.frames.peek().pc.offset = tgt
                    st_false.frames.peek().pc += 1
                    return [st_true, st_false]
            elif is_ref(v1):
                ref_id = get_ref_id(v1)
                if cond == "is":
                    take = (ref_id is None)
                elif cond == "isnot":
                    take = (ref_id is not None)
                else:
                    raise NotImplementedError(f"Don't know how to handle ref Ifz condition: {cond!r}")
            else:
                raise TypeError(f"ifz expected int or ref, but got {v1}")

            if take:
                frame.pc.offset = tgt
            else:
                frame.pc += 1
            return [state]

        # ----------------- Goto -----------------
        case jvm.Goto(target=tgt):
            frame.pc = PC(frame.pc.method, tgt)
            return [state]

        # ----------------- Store -----------------
        case jvm.Store(type=t, index=i):
            # pop value to store
            val = frame.stack.pop()

            # References: accept either tuple-form ('ref', id) or jpamb.Value.ref
            if isinstance(t, jvm.Reference) or isinstance(t, jvm.Array) or t == jvm.String() or isinstance(t, jvm.String):
                frame.locals[i] = val
                frame.pc += 1
                return [state]

            # For primitive kinds, normalize and store a tuple-form AVal
            if t is jvm.Int() or isinstance(t, jvm.Int):
                if is_int(val):
                    ivl = get_int_ivl(val)
                    frame.locals[i] = ('int', ivl)
                else:
                    frame.locals[i] = ('int', Interval.top())
                frame.pc += 1
                return [state]

            if t is jvm.Boolean() or isinstance(t, jvm.Boolean):
                if is_int(val):
                    ivl = get_int_ivl(val)
                    frame.locals[i] = ('int', ivl)
                else:
                    frame.locals[i] = ('int', Interval.top())
                frame.pc += 1
                return [state]

            if t is jvm.Char() or isinstance(t, jvm.Char):
                if is_int(val):
                    ivl = get_int_ivl(val)
                    frame.locals[i] = ('int', ivl)
                else:
                    frame.locals[i] = ('int', Interval.top())
                frame.pc += 1
                return [state]

            # Fallback: store conservatively
            frame.locals[i] = val
            frame.pc += 1
            return [state]

       # ----------------- ArrayLoad -----------------
        case jvm.ArrayLoad(type=t):
            index_val = pop()
            arr_val = pop()

            assert is_ref(arr_val), f"expected Reference, but got {arr_val}"
            ref = get_ref_id(arr_val)

            if ref is None:
                return "null pointer"

            instance = state.heap.get(ref)
            if not isinstance(instance, _ArrayObj):
                return "null pointer"

            assert is_int(index_val), f"expected int index, got {index_val}"
            idx_ivl = get_int_ivl(index_val)
            if idx_ivl.is_bot:
                return "out of bounds"

            # definitely OOB?
            if idx_ivl.lo is not None and idx_ivl.hi is not None:
                if idx_ivl.hi < 0 or idx_ivl.lo >= instance.length:
                    return "out of bounds"
                # maybe OOB inside range: we just over-approx the element
                # and don't refine heap.

            # element kind
            if isinstance(instance.type, (jvm.Int, jvm.Char)) or instance.type in (jvm.Int(), jvm.Char()):
                elem = ('int', Interval.top())
            else:
                elem = ('ref', None)

            push(elem)
            frame.pc += 1
            return [state]
        
        # ----------------- ArrayLength -----------------
        case jvm.ArrayLength():
            arr_val = pop()
            ref = get_ref_id(arr_val) if is_ref(arr_val) else None
            if ref is None:
                return "null pointer"

            instance = state.heap.get(ref)
            if not isinstance(instance, _ArrayObj):
                return "null pointer"

            push(aval_int_const(instance.length))
            frame.pc += 1
            return [state]

        # ----------------- ArrayStore -----------------
        case jvm.ArrayStore(type=t):
            value = pop()
            index_val = pop()
            arr_val = pop()

            if not is_ref(arr_val):
                return "null pointer"
            ref = get_ref_id(arr_val)
            if ref is None:
                return "null pointer"

            instance = state.heap.get(ref)
            if not isinstance(instance, _ArrayObj):
                return "null pointer"

            assert is_int(index_val), f"expected int index, got {index_val}"
            idx_ivl = get_int_ivl(index_val)
            if idx_ivl.lo != idx_ivl.hi:
                # if the interval might go OOB, report
                if idx_ivl.lo < 0 or idx_ivl.hi >= instance.length:
                    return "out of bounds"
                # otherwise, we could in theory write to multiple cells; just ignore.
                frame.pc += 1
                return [state]

            idx = int(idx_ivl.lo)
            if idx < 0 or idx >= instance.length:
                return "out of bounds"

            instance.data[idx] = value
            frame.pc += 1
            return [state]

        # ----------------- NewArray -----------------
        case jvm.NewArray(type=elem_type, dim=dims):
            length_val = pop()
            assert is_int(length_val), f"expected int length, got {length_val}"
            len_ivl = get_int_ivl(length_val)

            if len_ivl.lo is None or len_ivl.hi is None:
                return "negative array size"
            if len_ivl.hi < 0:
                return "negative array size"
            if len_ivl.lo < 0 <= len_ivl.hi:
                # may be negative
                return "negative array size"

            # for now, require concrete length
            size = int(len_ivl.lo) if len_ivl.lo == len_ivl.hi else int(len_ivl.lo)
            arr_obj = _ArrayObj(elem_type, size)
            idx = len(state.heap)
            state.heap[idx] = arr_obj
            push(('ref', idx))
            frame.pc += 1
            return [state]

        # ----------------- InvokeVirtual -----------------
        case jvm.InvokeVirtual(method=m):
            cls = m.classname.slashed()
            name = m.methodid.name
            param_types = list(m.extension.params or [])
            argc = len(param_types)

            args = [pop() for _ in range(argc)][::-1]
            objref_val = pop()
            if not is_ref(objref_val):
                # receiver not a reference -> conservative null-pointer
                return "null pointer"
            oid = get_ref_id(objref_val)
            if oid is None:
                return "null pointer"

            obj = state.heap.get(oid)
            if obj is None:
                return "null pointer"

            if cls == "java/lang/String":
                match name:
                    # ---------- equals(Object) ----------
                    case "equals":
                        recv_abs: StringAbs = obj["fields"]["value"]
                        arg_ref = args[0]
                        # prepare for optional arg abstract value for logging
                        arg_abs: StringAbs | None = None
                        if not is_ref(arg_ref):
                            res_ivl = Interval.const(0)
                        else:
                            arg_oid = get_ref_id(arg_ref)
                            if arg_oid is None:
                                res_ivl = Interval.const(0)
                            else:
                                arg_obj = state.heap.get(arg_oid)
                                if arg_obj is None or arg_obj["class"] != jvm.String():
                                    res_ivl = Interval.const(0)
                                else:
                                    arg_abs: StringAbs = arg_obj["fields"]["value"]
                                    res = str_equals(recv_abs, arg_abs)
                                    if res == "true":
                                        res_ivl = Interval.const(1)
                                    elif res == "false":
                                        res_ivl = Interval.const(0)
                                    else:
                                        res_ivl = Interval(0, 1)

                        # Log the operator/expression trees involved in this call
                        try:
                            recv_pretty = _format_expr(recv_abs)
                            arg_pretty = _format_expr(arg_abs)
                        except Exception:
                            recv_pretty = _format_expr(recv_abs)
                            arg_pretty = _format_expr(None)
                        logger.info(f"string-ops equals recv expr=\n{recv_pretty}\narg expr=\n{arg_pretty}")
                        # record for end-of-call dump
                        try:
                            record_string_op("equals", f"recv expr=\n{recv_pretty}\narg expr=\n{arg_pretty}")
                        except Exception:
                            pass

                        push(('int', res_ivl))
                        frame.pc += 1
                        return [state]

                    # ---------- length() ----------
                    case "length":
                        s_abs: StringAbs = obj["fields"]["value"]
                        len_ivl = str_length(s_abs)
                        # Log the receiver's expression tree
                        logger.info(f"string-ops length recv expr=\n{_format_expr(s_abs)}")
                        try:
                            record_string_op("length", f"recv expr=\n{_format_expr(s_abs)}")
                        except Exception:
                            pass
                        push(('int', len_ivl))
                        frame.pc += 1
                        return [state]

                    # ---------- charAt(int) ----------
                    case "charAt":
                        index_val = args[0]
                        assert is_int(index_val), f"expected int index to charAt, got {index_val}"
                        idx_ivl = get_int_ivl(index_val)
                        s_abs: StringAbs = obj["fields"]["value"]
                        L = s_abs.length

                        # precise case
                        if (
                            not idx_ivl.is_bot
                            and not L.is_bot
                            and idx_ivl.lo == idx_ivl.hi
                            and L.lo == L.hi
                        ):
                            i = int(idx_ivl.lo)
                            n = int(L.lo)
                            if i < 0 or i >= n:
                                return "out of bounds"
                            # Log the receiver expr/tree before returning
                            logger.info(f"string-ops charAt recv expr=\n{_format_expr(s_abs)}")
                            try:
                                record_string_op("charAt", f"recv expr=\n{_format_expr(s_abs)}")
                            except Exception:
                                pass
                            push(('int', Interval(0, 65535)))
                            frame.pc += 1
                            return [state]

                        # conservative case
                        if idx_ivl.lo < 0 or (not L.is_bot and idx_ivl.hi >= L.hi):
                            return "out of bounds"

                        logger.info(f"string-ops charAt recv expr=\n{_format_expr(s_abs)}")
                        try:
                            record_string_op("charAt", f"recv expr=\n{_format_expr(s_abs)}")
                        except Exception:
                            pass
                        push(('int', Interval(0, 65535)))
                        frame.pc += 1
                        return [state]

                    # ---------- concat(String) ----------
                    case "concat":
                        recv_abs: StringAbs = obj["fields"]["value"]
                        arg_ref = args[0]
                        if not is_ref(arg_ref):
                            res_abs = StringAbs.top()
                        else:
                            arg_oid = get_ref_id(arg_ref)
                            if arg_oid is None:
                                res_abs = StringAbs.top()
                            else:
                                arg_obj = state.heap.get(arg_oid)
                                if arg_obj is None or arg_obj["class"] != jvm.String():
                                    res_abs = StringAbs.top()
                                else:
                                    arg_abs: StringAbs = arg_obj["fields"]["value"]
                                    res_abs = str_concat(recv_abs, arg_abs)

                        new_oid = len(state.heap)
                        state.heap[new_oid] = {
                            "class": jvm.String(),
                            "fields": {"value": res_abs, "expr": getattr(res_abs, 'expr', None)},
                        }
                        # Log the concatenation result: const, expr, concrete value (when available)
                        expr = getattr(res_abs, 'expr', None)
                        try:
                            simp = st.simplify(expr) if expr is not None else None
                        except Exception:
                            simp = expr
                        try:
                            con = st.eval_expr(expr) if expr is not None else res_abs.const
                        except Exception:
                            con = res_abs.const
                        logger.info(f"concat -> String@{new_oid}: const={res_abs.const!r} expr={simp!r} concrete={con!r}")
                        # also log operand/result expression trees
                        logger.info(f"string-ops concat left expr=\n{_format_expr(recv_abs)}\nright expr=\n{_format_expr(arg_abs if 'arg_abs' in locals() else None)}\nresult expr=\n{_format_expr(res_abs)}")
                        try:
                            record_string_op("concat", f"left expr=\n{_format_expr(recv_abs)}\nright expr=\n{_format_expr(arg_abs if 'arg_abs' in locals() else None)}\nresult expr=\n{_format_expr(res_abs)}")
                        except Exception:
                            pass
                        frame.stack.push(jvm.Value.ref(new_oid))
                        
                        frame.pc += 1
                        return [state]

                    # ---------- substring(int,int) ----------
                    case "substring":     
                        i_val, j_val = args
                        s_abs: StringAbs = obj["fields"]["value"]

                        if not (is_int(i_val) and is_int(j_val)):
                            res_abs = StringAbs.top()
                            may_oob = True
                        else:
                            i_ivl = get_int_ivl(i_val)
                            j_ivl = get_int_ivl(j_val)
                            res_abs, may_oob = str_substring(s_abs, i_ivl, j_ivl)

                        if may_oob:
                            return "out of bounds"

                        new_oid = len(state.heap)
                        state.heap[new_oid] = {
                            "class": jvm.String(),
                            "fields": {"value": res_abs, "expr": getattr(res_abs, 'expr', None)},
                        }
                        # Log the substring result: const, expr, concrete value (when available)
                        expr = getattr(res_abs, 'expr', None)
                        try:
                            simp = st.simplify(expr) if expr is not None else None
                        except Exception:
                            simp = expr
                        try:
                            con = st.eval_expr(expr) if expr is not None else res_abs.const
                        except Exception:
                            con = res_abs.const
                        logger.info(f"substring -> String@{new_oid}: const={res_abs.const!r} expr={simp!r} concrete={con!r}")
                        # log the base and result expression trees
                        logger.info(f"string-ops substring base expr=\n{_format_expr(s_abs)}\nresult expr=\n{_format_expr(res_abs)}")
                        try:
                            record_string_op("substring", f"base expr=\n{_format_expr(s_abs)}\nresult expr=\n{_format_expr(res_abs)}")
                        except Exception:
                            pass
                        frame.stack.push(jvm.Value.ref(new_oid))
                        frame.pc += 1
                        return [state]

                    # ---------- contains(String) ----------
                    case "contains":
                        recv_abs: StringAbs = obj["fields"]["value"]
                        arg_ref = args[0]
                        if not is_ref(arg_ref):
                            res_ivl = Interval(0, 1)
                        else:
                            arg_oid = get_ref_id(arg_ref)
                            if arg_oid is None:
                                res_ivl = Interval.const(0)
                            else:
                                arg_obj = state.heap.get(arg_oid)
                                if arg_obj is None or arg_obj["class"] != jvm.String():
                                    res_ivl = Interval(0, 1)
                                else:
                                    arg_abs: StringAbs = arg_obj["fields"]["value"]
                                    res = str_contains(recv_abs, arg_abs)
                                    if res == "true":
                                        res_ivl = Interval.const(1)
                                    elif res == "false":
                                        res_ivl = Interval.const(0)
                                    else:
                                        res_ivl = Interval(0, 1)
                        push(('int', res_ivl))
                        frame.pc += 1
                        return [state]

            # ---------- StringBuilder support (for `+` concatenation compiled patterns) ----------
            if cls == "java/lang/StringBuilder":
                match name:
                    case "append":
                        # append returns the same StringBuilder instance; update its internal buffer
                        # args[0] may be an int, char, ref to String, or other
                        arg = args[0] if args else None
                        # ensure builder object has a 'buf' StringAbs field
                        fields = obj.setdefault("fields", {})
                        if "buf" not in fields:
                            # start with empty string
                            fields["buf"] = StringAbs.const_str("")

                        cur_buf: StringAbs = fields["buf"]

                        # convert arg to a StringAbs
                        if is_ref(arg):
                            arg_oid = get_ref_id(arg)
                            if arg_oid is None:
                                arg_abs = StringAbs.top()
                            else:
                                arg_obj = state.heap.get(arg_oid)
                                if arg_obj is None or arg_obj.get("class") != jvm.String():
                                    arg_abs = StringAbs.top()
                                else:
                                    arg_abs = arg_obj["fields"]["value"]
                        elif is_int(arg):
                            ivl = get_int_ivl(arg)
                            if not ivl.is_bot and ivl.lo == ivl.hi:
                                arg_abs = StringAbs.const_str(str(int(ivl.lo)))
                            else:
                                arg_abs = StringAbs.top()
                        else:
                            arg_abs = StringAbs.top()

                        new_buf = str_concat(cur_buf, arg_abs)
                        fields["buf"] = new_buf
                        # also store the current expression tree on the builder
                        # so heap-dumps include the string-expression for the buffer
                        fields["expr"] = getattr(new_buf, 'expr', None)

                        # log the append operation
                        logger.info(f"string-ops sb.append recv buf=\n{_format_expr(cur_buf)}\narg=\n{_format_expr(arg_abs)}\nnew buf=\n{_format_expr(new_buf)}")
                        try:
                            record_string_op("sb.append", f"recv buf=\n{_format_expr(cur_buf)}\narg=\n{_format_expr(arg_abs)}\nnew buf=\n{_format_expr(new_buf)}")
                        except Exception:
                            pass

                        # append returns the builder (ref to same oid)
                        push(('ref', oid))
                        frame.pc += 1
                        return [state]

                    case "toString":
                        # produce a new String object whose value is the builder buffer
                        fields = obj.get("fields", {})
                        buf_abs: StringAbs = fields.get("buf", StringAbs.top())

                        new_oid = len(state.heap)
                        state.heap[new_oid] = {
                            "class": jvm.String(),
                            "fields": {"value": buf_abs, "expr": getattr(buf_abs, 'expr', None)},
                        }

                        logger.info(f"sb.toString -> String@{new_oid}: const={buf_abs.const!r} expr=\n{_format_expr(buf_abs)}")
                        try:
                            record_string_op("sb.toString", f"result expr=\n{_format_expr(buf_abs)}")
                        except Exception:
                            pass

                        frame.stack.push(jvm.Value.ref(new_oid))
                        frame.pc += 1
                        return [state]

                    # ---------- startsWith(String) ----------
                    case "startsWith":
                        recv_abs: StringAbs = obj["fields"]["value"]
                        arg_ref = args[0]
                        if not is_ref(arg_ref):
                            res_ivl = Interval(0, 1)
                        else:
                            arg_oid = get_ref_id(arg_ref)
                            if arg_oid is None:
                                res_ivl = Interval.const(0)
                            else:
                                arg_obj = state.heap.get(arg_oid)
                                if arg_obj is None or arg_obj["class"] != jvm.String():
                                    res_ivl = Interval(0, 1)
                                else:
                                    arg_abs: StringAbs = arg_obj["fields"]["value"]
                                    res = str_startswith(recv_abs, arg_abs)
                                    if res == "true":
                                        res_ivl = Interval.const(1)
                                    elif res == "false":
                                        res_ivl = Interval.const(0)
                                    else:
                                        res_ivl = Interval(0, 1)
                        push(('int', res_ivl))
                        frame.pc += 1
                        return [state]

                    # ---------- endsWith(String) ----------
                    case "endsWith":
                        recv_abs: StringAbs = obj["fields"]["value"]
                        arg_ref = args[0]
                        if not is_ref(arg_ref):
                            res_ivl = Interval(0, 1)
                        else:
                            arg_oid = get_ref_id(arg_ref)
                            if arg_oid is None:
                                res_ivl = Interval.const(0)
                            else:
                                arg_obj = state.heap.get(arg_oid)
                                if arg_obj is None or arg_obj["class"] != jvm.String():
                                    res_ivl = Interval(0, 1)
                                else:
                                    arg_abs: StringAbs = arg_obj["fields"]["value"]
                                    res = str_endswith(recv_abs, arg_abs)
                                    if res == "true":
                                        res_ivl = Interval.const(1)
                                    elif res == "false":
                                        res_ivl = Interval.const(0)
                                    else:
                                        res_ivl = Interval(0, 1)
                        push(('int', res_ivl))
                        frame.pc += 1
                        return [state]

                    case _:
                        raise NotImplementedError(
                            f"InvokeVirtual for java/lang/String.{name} not handled"
                        )

            # non-String virtual call
            raise NotImplementedError(
                f"InvokeVirtual not implemented for {cls}.{name}{param_types}"
            )

        # ----------------- InvokeSpecial -----------------
        case jvm.InvokeSpecial(method=m, is_interface=_):
            args_list = m.extension.params._elements
            args = [pop() for _ in range(len(args_list))][::-1]
            objref_val = pop()
            if not is_ref(objref_val):
                return [state]
            oid = get_ref_id(objref_val)
            obj = state.heap.get(oid)

            if m.classname.name == "java/lang/Object" and m.methodid.name == "<init>":
                frame.pc += 1
                return [state]
            elif m.classname.name == "java/lang/AssertionError" and m.methodid.name == "<init>":
                frame.pc += 1
                return [state]
            elif m.classname.name == "java/lang/String" and m.methodid.name == "<init>":
                # String constructor: ignore body
                frame.pc += 1
                return [state]

            newframe = Frame.from_method(m)
            state.frames.push(newframe)
            return [state]

        # ----------------- InvokeStatic -----------------
        case jvm.InvokeStatic(method=m):
            cls = m.classname.name
            name = m.methodid.name
            param_types = list(m.extension.params or [])
            argc = len(param_types)

            args = [pop() for _ in range(argc)][::-1]

            # ---------- java/lang/String.valueOf ----------
            if cls == "java/lang/String" and name == "valueOf":
                if args:
                    arg = args[0]
                    s_abs: StringAbs
                    if is_int(arg):
                        ivl = get_int_ivl(arg)
                        if not ivl.is_bot and ivl.lo == ivl.hi and ivl.lo not in (NEG_INF, POS_INF):
                            s = str(int(ivl.lo))
                            s_abs = StringAbs.const_str(s)
                        else:
                            s_abs = StringAbs.top()
                    else:
                        s_abs = StringAbs.top()
                else:
                    s_abs = StringAbs.const_str("")

                oid = len(state.heap)
                state.heap[oid] = {
                    "class": jvm.String(),
                    "fields": {"value": s_abs, "expr": s_abs.expr},
                }
                # Log the result of valueOf: const, expr, concrete value (when available)
                expr = getattr(s_abs, 'expr', None)
                try:
                    simp = st.simplify(expr) if expr is not None else None
                except Exception:
                    simp = expr
                try:
                    con = st.eval_expr(expr) if expr is not None else s_abs.const
                except Exception:
                    con = s_abs.const
                logger.info(f"valueOf -> String@{oid}: const={s_abs.const!r} expr={simp!r} concrete={con!r}")
                logger.info(f"string-ops valueOf arg expr=\n{_format_expr(args[0] if args else None)}\nresult expr=\n{_format_expr(s_abs)}")
                try:
                    record_string_op("valueOf", f"arg expr=\n{_format_expr(args[0] if args else None)}\nresult expr=\n{_format_expr(s_abs)}")
                except Exception:
                    pass

                frame.stack.push(jvm.Value.ref(oid))
                frame.pc += 1
                return [state]

            if cls == "java/lang/String":
                raise NotImplementedError(f"Static String method not supported: {name}")

            # ---------- java/lang/Character ----------
            if cls == "java/lang/Character":
                code_ivl = Interval.bot()
                if args and is_int(args[0]):
                    code_ivl = get_int_ivl(args[0])

                # Character.getNumericValue(char)
                if name == "getNumericValue":
                    if not code_ivl.is_bot and code_ivl.lo == code_ivl.hi:
                        c = int(code_ivl.lo)
                        ch = chr(c)
                        if "0" <= ch <= "9":
                            n = ord(ch) - ord("0")
                        else:
                            n = -1
                        res_ivl = Interval.const(n)
                    else:
                        # over-approx: result in [-1,9]
                        res_ivl = Interval(-1, 9)
                    push(('int', res_ivl))
                    frame.pc += 1
                    return [state]

                # Character.isDigit(char)
                if name == "isDigit":
                    if not code_ivl.is_bot and code_ivl.lo == code_ivl.hi:
                        c = int(code_ivl.lo)
                        ch = chr(c)
                        res_ivl = Interval.const(1 if ch.isdigit() else 0)
                    else:
                        res_ivl = Interval(0, 1)
                    push(('int', res_ivl))
                    frame.pc += 1
                    return [state]

                # Character.isWhitespace(char)
                if name == "isWhitespace":
                    if not code_ivl.is_bot and code_ivl.lo == code_ivl.hi:
                        c = int(code_ivl.lo)
                        ch = chr(c)
                        res_ivl = Interval.const(1 if ch.isspace() else 0)
                    else:
                        res_ivl = Interval(0, 1)
                    push(('int', res_ivl))
                    frame.pc += 1
                    return [state]

                raise NotImplementedError(
                    f"InvokeStatic for java/lang/Character.{name} not handled"
                )

            # ---------- Default: normal static call ----------
            norm_args: list[AVal] = []
            for t, v in zip(param_types, args):
                norm_args.append(v)

            callee = Frame.from_method(m)
            callee.locals = {i: v for i, v in enumerate(norm_args)}
            callee.pc = PC(callee.pc.method, callee.pc.offset)
            state.frames.push(callee)
            return [state]

        # ----------------- InvokeDynamic -----------------
        case jvm.InvokeDynamic(method=m):
            # Handle invokedynamic-based string concatenation (StringConcatFactory)
            name = m.methodid.name
            param_types = list(m.extension.params or [])
            argc = len(param_types)

            # pop arguments (invokedynamic is like a static call)
            args = [pop() for _ in range(argc)][::-1]

            # Recognize common concat factories (makeConcat, makeConcatWithConstants)
            if name.startswith("makeConcat"):
                # If this is makeConcatWithConstants, there may be a "recipe"
                # string attached to the method metadata (bootstrap info) that
                # interleaves constant segments between the runtime args. Try
                # to read that recipe and use it to build a more precise
                # concatenation expression. Fall back to folding args alone.
                recipe = None
                ext = getattr(m, 'extension', None)
                if ext is not None:
                    # common attribute names to check for a recipe
                    recipe = getattr(ext, 'recipe', None) or getattr(ext, 'constant', None) or getattr(ext, 'constants', None) or getattr(ext, 'bsm_args', None)

                parts: list[StringAbs] = []
                # If no recipe attribute is available on the method extension,
                # try to inspect the opcode object itself for possible
                # bootstrap/constant info (useful when the decompiler placed
                # the recipe on the opcode JSON rather than the method id).
                if recipe is None:
                    try:
                        op = bc[frame.pc]
                        logger.debug(f"invokedynamic opcode.__dict__ = {getattr(op, '__dict__', None)!r}")
                        # also log any public attributes that may contain constants
                        for k in ['index', 'bsm_args', 'args', 'const', 'recipe', 'bootstrap_args']:
                            if hasattr(op, k):
                                logger.debug(f"invokedynamic opcode attr {k} = {getattr(op, k)!r}")

                        # If the decompiler placed the concat recipe in the class
                        # bootstrapmethods table, try to read it. The opcode
                        # commonly contains an `index` which refers to
                        # `bootstrapmethods[index]` in the class JSON.
                        idx = getattr(op, 'index', None)
                        if isinstance(idx, int):
                            try:
                                cls_json = suite.findclass(frame.pc.method.classname)
                                bms = cls_json.get('bootstrapmethods', [])
                                if 0 <= idx < len(bms):
                                    # expected layout: bootstrapmethods[i].method.args[0].value
                                    mobj = bms[idx].get('method', {})
                                    marg = None
                                    if isinstance(mobj, dict):
                                        args_list = mobj.get('args', [])
                                        if args_list and isinstance(args_list[0], dict):
                                            marg = args_list[0].get('value')
                                    if isinstance(marg, str):
                                        
                                        recipe = marg.replace('\\u0001', '\u0001')
                                        logger.debug(f"found recipe in bootstrapmethods[{idx}] = {recipe!r}")
                            except Exception:
                                pass
                    except Exception:
                        pass
                if isinstance(recipe, str):
                    # recipe commonly uses the U+0001 placeholder; accept both
                    # literal and escaped forms
                    rec = recipe.replace('\\u0001', '\u0001')
                    segs = rec.split('\u0001')
                    # interleave constant segments and args
                    for i, seg in enumerate(segs):
                        if seg:
                            parts.append(StringAbs.const_str(seg))
                        if i < len(args):
                            a = args[i]
                            if is_ref(a):
                                arg_oid = get_ref_id(a)
                                if arg_oid is None:
                                    parts.append(StringAbs.top())
                                else:
                                    arg_obj = state.heap.get(arg_oid)
                                    if arg_obj is None or arg_obj.get('class') != jvm.String():
                                        parts.append(StringAbs.top())
                                    else:
                                        parts.append(arg_obj['fields']['value'])
                            elif is_int(a):
                                ivl = get_int_ivl(a)
                                if not ivl.is_bot and ivl.lo == ivl.hi:
                                    parts.append(StringAbs.const_str(str(int(ivl.lo))))
                                else:
                                    parts.append(StringAbs.top())
                            else:
                                parts.append(StringAbs.top())
                else:
                    # No recipe available: fold runtime args only
                    for a in args:
                        if is_ref(a):
                            arg_oid = get_ref_id(a)
                            if arg_oid is None:
                                parts.append(StringAbs.top())
                            else:
                                arg_obj = state.heap.get(arg_oid)
                                if arg_obj is None or arg_obj.get('class') != jvm.String():
                                    parts.append(StringAbs.top())
                                else:
                                    parts.append(arg_obj['fields']['value'])
                        elif is_int(a):
                            ivl = get_int_ivl(a)
                            if not ivl.is_bot and ivl.lo == ivl.hi:
                                parts.append(StringAbs.const_str(str(int(ivl.lo))))
                            else:
                                parts.append(StringAbs.top())
                        else:
                            parts.append(StringAbs.top())

                # fold parts into a single StringAbs
                s_abs: StringAbs | None = None
                for p in parts:
                    if s_abs is None:
                        s_abs = p
                    else:
                        s_abs = str_concat(s_abs, p)

                if s_abs is None:
                    s_abs = StringAbs.top()

                # If we didn't find any recipe constants, try to log the
                # invokedynamic metadata once so we can inspect where the
                # recipe lives in the decompiler output for future parsing.
                if recipe is None:
                    try:
                        logger.debug(f"invokedynamic metadata: method={m!r} extension={getattr(m, 'extension', None)!r}")
                        ext = getattr(m, 'extension', None)
                        if ext is not None:
                            # print attribute names and repr of small fields
                            keys = [k for k in dir(ext) if not k.startswith('_')]
                            logger.debug(f"invokedynamic extension attrs: {keys}")
                            for k in keys:
                                v = getattr(ext, k)
                                if isinstance(v, (str, bytes)) and len(str(v)) < 200:
                                    logger.debug(f"ext.{k} = {v!r}")
                        # also inspect the opcode object for any raw json or bsm info
                        try:
                            logger.debug(f"opcode object attrs: {dir(opr)}")
                            for k in [k for k in dir(opr) if not k.startswith('_')]:
                                try:
                                    val = getattr(opr, k)
                                    if isinstance(val, (str, bytes)) and len(str(val)) < 200:
                                        logger.debug(f"opr.{k} = {val!r}")
                                except Exception:
                                    pass
                        except Exception:
                            pass
                    except Exception:
                        pass

                new_oid = len(state.heap)
                state.heap[new_oid] = {
                    "class": jvm.String(),
                    "fields": {"value": s_abs, "expr": getattr(s_abs, 'expr', None)},
                }

                logger.info(f"invokedynamic {name} -> String@{new_oid}: const={s_abs.const!r} expr=\n{_format_expr(s_abs)}")
                try:
                    record_string_op("invokedynamic." + name, f"args exprs=... result expr=\n{_format_expr(s_abs)}")
                except Exception:
                    pass

                frame.stack.push(jvm.Value.ref(new_oid))
                frame.pc += 1
                return [state]

            # Fallback: unknown invokedynamic
            raise NotImplementedError(f"InvokeDynamic not implemented for {name}")

        # ----------------- Throw -----------------
        case jvm.Throw():
            objref_val = pop()
            if not is_ref(objref_val):
                return "exception"
            oid = get_ref_id(objref_val)
            obj = state.heap.get(oid)

            if obj is None:
                return "exception"

            if obj["class"].name == "java/lang/AssertionError":
                return "assertion error"
            return "exception"

        # ----------------- New -----------------
        case jvm.New(classname=cls):
            heap = state.heap
            idx = len(heap)
            heap[idx] = {"class": cls, "fields": {}}
            push(('ref', idx))
            frame.pc += 1
            return [state]

        # ----------------- Dup -----------------
        case jvm.Dup():
            v = frame.stack.peek()
            push(v)
            frame.pc += 1
            return [state]

        # ----------------- Pop -----------------
        case jvm.Pop():
            # discard the top of the stack
            try:
                frame.stack.pop()
            except IndexError:
                # already logged by pop() helper if used elsewhere; log here too
                logger.error(f"Stack underflow on Pop at {frame.pc}")
            frame.pc += 1
            return [state]

        # ----------------- Incr -----------------
        case jvm.Incr(index=i, amount=d):
            v = frame.locals[i]
            assert is_int(v), f"expected int, but got {v}"
            ivl = get_int_ivl(v)
            ivl2 = add(ivl, Interval.const(d))
            frame.locals[i] = ('int', ivl2)
            frame.pc += 1
            return [state]

        # ----------------- Get (static fields only) -----------------
        case jvm.Get(field=f):
            fld = opr.field
            t = fld.extension.type
            name = fld.extension.name
            cls = getattr(fld, "classname", "")

            if opr.static:
                if name == "$assertionsDisabled":
                    # 0 means assertions enabled in our setup (mirror old logic)
                    val = 0 if ASSERTIONS_ENABLED else 1
                    push(aval_int_const(val))
                    frame.pc += 1
                    return [state]

                # any other static field: just 0
                push(aval_int_const(0))
                frame.pc += 1
                return [state]

            objref_val = pop()
            raise NotImplementedError("Get instance field not implemented")

        # ----------------- Fallback -----------------
        case a:
            a.help()
            raise NotImplementedError(f"Don't know how to handle: {a!r}")


# -------------------------
# Initial state construction
# -------------------------
def build_initial_state_from_sig(methodid: jvm.AbsMethodID, concrete_input=None) -> State:
    """Build initial abstract state for a method.

    If `concrete_input` (a `jpamb.model.Input`) is provided, try to use the
    concrete values to initialize parameter locals/heap objects. Otherwise,
    fall back to top/unknown abstractions.
    """
    heap: dict[int, object] = {}
    locals: dict[int, AVal] = {}

    for i, t in enumerate(methodid.extension.params):
        # Handle primitive parameter types
        if isinstance(t, (jvm.Int, jvm.Boolean, jvm.Char)) or t in (jvm.Int(), jvm.Boolean(), jvm.Char()):
            # If a concrete input value exists for this parameter, use it
            if concrete_input is not None and i < len(concrete_input.values):
                val = concrete_input.values[i]
                if val.type == jvm.Int():
                    locals[i] = ('int', Interval.const(int(val.value)))
                else:
                    locals[i] = aval_int_top()
            else:
                locals[i] = aval_int_top()
        # Handle string parameters: they might appear as `jvm.String()` or as an
        # object type `jvm.Object(ClassName('java/lang/String'))` coming from
        # ParameterType.decode(). Detect both forms.
        elif isinstance(t, jvm.String) or t == jvm.String() or (
            hasattr(jvm, 'Object') and isinstance(t, jvm.Object) and getattr(t, 'name', None) == jvm.ClassName('java/lang/String')
        ):
            # If a concrete string input is provided, allocate a concrete heap
            # object with the known value; otherwise allocate a top string.
            if concrete_input is not None and i < len(concrete_input.values):
                val = concrete_input.values[i]
                if val.type == jvm.String():
                    oid = len(heap)
                    heap[oid] = {
                        "class": jvm.String(),
                        "fields": {"value": StringAbs.const_str(val.value), "expr": st.const(val.value)},
                    }
                    locals[i] = jvm.Value.ref(oid)
                else:
                    oid = len(heap)
                    heap[oid] = {"class": jvm.String(), "fields": {"value": StringAbs.top()}}
                    locals[i] = jvm.Value.ref(oid)
            else:
                oid = len(heap)
                heap[oid] = {
                    "class": jvm.String(),
                    "fields": {"value": StringAbs.top()},
                }
                locals[i] = jvm.Value.ref(oid)
        elif isinstance(t, jvm.Array):
            # No concrete array inputs in no-input mode: treat as unknown ref
            locals[i] = jvm.Value.ref(None)
        else:
            locals[i] = jvm.Value.ref(None)

    frame = Frame(locals=locals, stack=Stack.empty(), pc=PC(methodid, 0))
    return State(heap=heap, frames=Stack.empty().push(frame))


def join_avals(a: AVal, b: AVal) -> AVal:
    # Accept both tuple-form AVals and jpamb.jvm.Value objects.
    def norm(x):
        if isinstance(x, tuple):
            return x
        # jpamb Value object
        if hasattr(x, 'type'):
            if is_int(x):
                return ('int', get_int_ivl(x))
            if is_ref(x):
                return ('ref', get_ref_id(x))
        raise AssertionError(f"unexpected aval value: {x}")

    a_n = norm(a)
    b_n = norm(b)

    # different kinds
    if a_n[0] != b_n[0]:
        # any int vs ref clash → give up to int TOP
        return ('int', Interval.top())

    kind = a_n[0]
    if kind == 'int':
        return ('int', join(a_n[1], b_n[1]))         # type: ignore[arg-type]
    if kind == 'ref':
        ra, rb = a_n[1], b_n[1]
        # if the ref id disagrees, join == "may be different objects" → None
        return ('ref', ra if ra == rb else None)

    raise AssertionError(f"unexpected aval kind: {kind}")


def join_frames(fa: Frame, fb: Frame) -> Frame:
    # Join locals pointwise (only indices that appear in either)
    keys = set(fa.locals.keys()) | set(fb.locals.keys())
    jlocals = {k: join_avals(fa.locals.get(k, ('int', Interval.top())),
                             fb.locals.get(k, ('int', Interval.top())))
               for k in keys}
    # Join stacks conservatively: require same height; else surrender to TOP-ish
    # debug: log stack lengths when joining frames
    logger.debug(f"join_frames: len(fa.stack)={len(fa.stack)} len(fb.stack)={len(fb.stack)}")
    if len(fa.stack) == len(fb.stack):
        jstack_list = [ join_avals(x, y) for x, y in zip(fa.stack, fb.stack) ]
    else:
        jstack_list = [ ('int', Interval.top()) ] * max(len(fa.stack), len(fb.stack))
    # wrap the joined stack into a Stack object so callers can use push/pop
    jstack = Stack(list(jstack_list))
    # pc join is the *same* key so pc equal here.
    return Frame(locals=jlocals, stack=jstack, pc=fa.pc)

def states_equal(a: Frame, b: Frame) -> bool:
    return a.locals == b.locals and a.stack == b.stack  # coarse but fine


def run_worklist_result(initial: State) -> List[str]:
    resultList: List[str] = []
    worklist: List[State] = [initial]
    # Map PC key -> (joined Frame, merged heap snapshot)
    seen: Dict[Tuple[jvm.AbsMethodID, int], tuple[Frame, dict]] = {}

    while worklist:
        st = worklist.pop()
        if not st.frames:
            continue

        fr = st.frames.peek()
        key = (fr.pc.method, fr.pc.offset)

        if key in seen:
            joined = join_frames(seen[key], fr)
            if states_equal(joined, seen[key]):
                continue
            seen[key] = joined
            # update the frame at the top of the stack
            st.frames.items[-1] = joined
        else:
            seen[key] = fr

        res = step_abstract(st)
        # Normalize step result: support a single State, a list of State, or an error string
        if isinstance(res, State):
            res = [res]
        if isinstance(res, str):
            resultList.append(res)
            continue

        for nxt in res:
            if isinstance(nxt, str):
                resultList.append(nxt)
                continue
            if not nxt.frames:
                continue
            worklist.append(nxt)

    return resultList

def analyze_method_no_inputs(methodid: jvm.AbsMethodID) -> List[str]:
    """Entry for analyzer/test mode (no concrete inputs)."""
    warnings.clear()
    return run_worklist_result(build_initial_state_from_sig(methodid))


def get_abstract_warnings() -> List[str]:
    """Return list of warning strings emitted during analysis."""
    return warnings


def _dump_heap(heap: dict[int, object]):
    """Pretty-print the string-relevant portion of the heap.

    For each heap object that looks like a Java `String` (has a `value`
    field or class `java/lang/String`), print its oid, the abstract
    value, a simplified expression (if any), its concrete value (if
    recoverable) and a pretty-printed expression tree.
    """
    logger.info("--- HEAP DUMP START ---")
    for oid in sorted(heap.keys()):
        obj = heap[oid]
        fields = obj.get("fields", {}) if isinstance(obj, dict) else {}
        # choose objects that either are String class or have a 'value' field
        is_string = False
        try:
            if isinstance(obj.get("class"), jvm.String) or obj.get("class") == jvm.String():
                is_string = True
        except Exception:
            # defensive: obj['class'] may be non-dict type
            pass
        if "value" not in fields and not is_string:
            continue

        s_abs = fields.get("value")
        expr = fields.get("expr") if fields else None

        # try to simplify and evaluate
        try:
            simp = st.simplify(expr) if expr is not None else (s_abs.expr if s_abs is not None else None)
        except Exception:
            simp = expr
        try:
            con = st.eval_expr(simp) if simp is not None else (s_abs.const if s_abs is not None else None)
        except Exception:
            con = (s_abs.const if s_abs is not None else None)

        try:
            pretty_s = st.pretty(simp, indent=2) if simp is not None else "None"
        except Exception:
            pretty_s = repr(simp)

        logger.info(f"String@{oid}: const={getattr(s_abs, 'const', None)!r} concrete={con!r}\nexpr=\n{pretty_s}")

    logger.info("--- HEAP DUMP END ---")

if __name__ == "__main__":
    # Support the jpamb 'info' 
    if "info" in sys.argv[1:]:
        
        jpamb.printinfo(
            "abstract-interpreter-stringtree",
            "1.0",
            "analysis",
            ["string", "ai"],
            for_science=True,
        )
        # after printing metadata for the jpamb probe, exit immediately
        sys.exit(0)

    # Support optional concrete input argument placed before the method id
    # e.g. `script.py "(\"hello\")" jpamb.cases...` or when called by the
    # harness which may pass an input token as the second-to-last arg.
    methodid = jpamb.parse_methodid(sys.argv[-1])
    concrete_input = None
    if len(sys.argv) >= 3 and not sys.argv[-2].startswith("--"):
        try:
            concrete_input = jpamb.parse_input(sys.argv[-2])
        except Exception:
            concrete_input = None

    # Build initial state so we can optionally dump its heap after analysis
    # Reset per-run collector for string-operator trees
    STRING_OPS.clear()
    initial = build_initial_state_from_sig(methodid, concrete_input)
    result = run_worklist_result(initial)
    print(result)

    # Print collected string-operator trees for this call (if any)
    if STRING_OPS:
        logger.info("--- STRING OPS DUMP START ---")
        for entry in STRING_OPS:
            logger.info(entry)
        logger.info("--- STRING OPS DUMP END ---")

    # If the caller passed the heap-dump flag, print a final heap dump
    if "--heap-dump" in sys.argv[1:]:
        _dump_heap(initial.heap)

    # ------- Produce a simple outcome distribution (percentages) -------
    # Format matches the simple 'label;NN%' lines used by `solutions/my_analyzer.py`.
    try:
        outcomes = result if isinstance(result, list) else [result]
    except Exception:
        outcomes = []

    # All known labels
    labels = [
        'ok',
        'divide by zero',
        'assertion error',
        'out of bounds',
        'null pointer',
        '*'
    ]

    # Count occurrences
    counts = {k: 0 for k in labels}
    for r in outcomes:
        counts[r if r in counts else '*'] += 1

    total = sum(counts.values())

    # Default if nothing observed
    if total == 0:
        counts['ok'] = 1
        total = 1

    # Apply Laplace smoothing so no label observed in the run gets a
    # zero-percentage (which can produce -inf when the harness takes logs).
    # We add +1 to every count (simple additive smoothing), then compute
    # integer percentages. After rounding, ensure that any label that was
    # actually observed (counts[k] > 0) has at least 1% assigned; adjust
    # the largest-probability label downward to keep the sum == 100.

    # Smoothed counts (add-one smoothing)
    smoothed = {k: counts[k] + 1 for k in labels}
    total_s = sum(smoothed.values())

    # Initial rounded percentages
    percents = {k: int(round(smoothed[k] * 100.0 / total_s)) for k in labels}

    # If nothing observed originally (total==0), keep the original fallback
    if total == 0:
        percents = {k: 0 for k in labels}
        percents['ok'] = 100
    else:
        # Ensure any label with a non-zero raw count gets at least 1%
        for k in labels:
            if counts[k] > 0 and percents[k] == 0:
                percents[k] = 1

        # Fix rounding so percentages sum to 100 by adding/subtracting
        diff = 100 - sum(percents.values())
        if diff != 0:
            # Adjust the label with the largest smoothed weight to absorb diff
            # (prefer 'ok' if it's among the largest)
            candidates = sorted(labels, key=lambda k: (smoothed[k], k), reverse=True)
            for c in candidates:
                # ensure we don't make any label negative
                if percents[c] + diff >= 0:
                    percents[c] += diff
                    break

    # Output
    for k in labels:
        print(f"{k};{percents[k]}%")
    # ------------------------------------------------------------------
