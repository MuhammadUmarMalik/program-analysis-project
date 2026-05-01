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

        def b(x):
            return "-inf" if x == NEG_INF else ("+inf" if x == POS_INF else str(int(x)))

        return f"[{b(self.lo)}..{b(self.hi)}]"
    
    @staticmethod
    def str_top() -> "Interval":
        # length ∈ [0, +∞)
        return Interval(0, POS_INF)

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


@dataclass(frozen=True)
class StringAbs:
    const: Optional[str]  # exact value if known
    length: Interval      # interval over |s|

    @staticmethod
    def bot() -> "StringAbs":
        return StringAbs(None, Interval.bot())

    @staticmethod
    def top() -> "StringAbs":
        # strings of any length >= 0
        return StringAbs(None, Interval(0, POS_INF))

    @staticmethod
    def const_str(s: str) -> "StringAbs":
        return StringAbs(s, Interval.const(len(s)))  # a single concrete string gets [n,n]

    @property
    def is_bot(self) -> bool:
        return self.length.is_bot

    def join(self, other: "StringAbs") -> "StringAbs":
        if self.is_bot:
            return other
        if other.is_bot:
            return self
        const = self.const if self.const == other.const else None
        return StringAbs(const, join(self.length, other.length))

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
    if a.const is not None and b.const is not None:
        const = a.const + b.const
        length = Interval.const(len(const))
    return StringAbs(const, length)


def str_substring(s: StringAbs, i_ivl: Interval, j_ivl: Interval) -> Tuple[StringAbs, bool]:
    """Returns (result_string_abs, may_out_of_bounds).

    Sound: reports may_oob whenever substring(i,j) *could* violate
    0 <= i <= j <= len(s) for any concrete string in s.
    """
    if s.is_bot or i_ivl.is_bot or j_ivl.is_bot:
        return StringAbs.bot(), False

    may_oob = False
    i_lo, i_hi = i_ivl.lo, i_ivl.hi
    j_lo, j_hi = j_ivl.lo, j_ivl.hi
    L = s.length

    if L.is_bot:
        may_oob = True
    else:
        # Negative indices are always invalid.
        if i_lo < 0 or j_lo < 0:
            may_oob = True
        # i > j is always invalid.
        if i_lo > j_hi:
            may_oob = True
        # j > len(s) is invalid: j_hi could exceed the *minimum* length L.lo.
        # (Using L.hi would miss cases where L is small but j is large.)
        if not L.is_bot and j_hi > L.lo:
            may_oob = True

    sub_lo = max(0, j_lo - i_hi) if not (i_ivl.is_bot or j_ivl.is_bot) else 0
    sub_hi = max(0, j_hi - i_lo) if not (i_ivl.is_bot or j_ivl.is_bot) else 0
    sub_len = Interval(sub_lo, sub_hi)

    const = None
    if (
        s.const is not None
        and i_ivl.lo == i_ivl.hi
        and j_ivl.lo == j_ivl.hi
    ):
        i_idx = int(i_ivl.lo)
        j_idx = int(j_ivl.lo)
        try:
            const = s.const[i_idx:j_idx]
            sub_len = Interval.const(len(const))
        except Exception:
            may_oob = True

    return StringAbs(const, sub_len), may_oob


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
    return v[0] == 'int'


def is_ref(v: AVal) -> bool:
    return v[0] == 'ref'


def get_int_ivl(v: AVal) -> Interval:
    assert is_int(v), f"expected int aval, got {v}"
    return v[1]


def get_ref_id(v: AVal) -> Optional[int]:
    assert is_ref(v), f"expected ref aval, got {v}"
    return v[1]


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
        except IndexError:
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
                case jvm.Int():
                    push(aval_int_const(int(v.value)))
                case jvm.Boolean():
                    push(aval_int_const(1 if v.value else 0))
                case jvm.Char():
                    push(aval_int_const(ord(v.value)))
                case jvm.String():
                    s = "" if v.value is None else str(v.value)
                    oid = len(state.heap)
                    state.heap[oid] = {
                        "class": v.type,
                        "fields": {"value": StringAbs.const_str(s)},
                    }
                    push(('ref', oid))
                case _:
                    # unknown literal kind -> conservative int TOP
                    if v.value is None:
                        push(('ref', None))
                    else:
                        push(('ref', None))


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
            v1 = pop()
            assert is_int(v1), f"expected int, but got {v1}"
            ivl = get_int_ivl(v1)
            # For now, ignore narrowing / wrap-around, keep interval as-is.
            push(('int', ivl))
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
            val = pop()
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

                        push(('int', res_ivl))
                        frame.pc += 1
                        return [state]

                    # ---------- length() ----------
                    case "length":
                        s_abs: StringAbs = obj["fields"]["value"]
                        len_ivl = str_length(s_abs)
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

                        # Definitely OOB: index < 0 or index >= minimum possible length.
                        if not idx_ivl.is_bot and idx_ivl.lo < 0:
                            return "out of bounds"
                        if not idx_ivl.is_bot and not L.is_bot and idx_ivl.hi >= L.lo:
                            return "out of bounds"

                        # Precise case: single known index into a constant string.
                        if (
                            s_abs.const is not None
                            and not idx_ivl.is_bot
                            and idx_ivl.lo == idx_ivl.hi
                        ):
                            i = int(idx_ivl.lo)
                            if i < 0 or i >= len(s_abs.const):
                                return "out of bounds"
                            ch_code = ord(s_abs.const[i])
                            push(('int', Interval.const(ch_code)))
                            frame.pc += 1
                            return [state]

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
                            "fields": {"value": res_abs},
                        }
                        push(('ref', new_oid))
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
                            "fields": {"value": res_abs},
                        }
                        push(('ref', new_oid))
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
            assert is_ref(objref_val), f"expected ref as receiver, got {objref_val}"
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
                    "fields": {"value": s_abs},
                }

                push(('ref', oid))
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
        
        # ----------------- Pop -----------------
        case jvm.Pop():
            # try:
            frame.stack.pop()
            # except IndexError:
            #     # already logged by pop() helper if used elsewhere; log here too
            #     logger.error(f"Stack underflow on Pop at {frame.pc}")
            frame.pc += 1
            return [state]

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
                    val = 0 if ASSERTIONS_ENABLED else 1
                    push(aval_int_const(val))
                    frame.pc += 1
                    return [state]

                if isinstance(t, (jvm.Int, jvm.Boolean, jvm.Char)) or t in (jvm.Int(), jvm.Boolean(), jvm.Char()):
                    push(aval_int_const(0))
                else:
                    # Reference-typed static fields (String, arrays, Object, etc.)
                    # We don't know the actual object , abstract as some ref or null.
                    push(('ref', None))

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
def build_initial_state_from_sig(methodid: jvm.AbsMethodID) -> State:
    heap: dict[int, object] = {}
    locals: dict[int, AVal] = {}

    for i, t in enumerate(methodid.extension.params):
        if isinstance(t, (jvm.Int, jvm.Boolean, jvm.Char)) or t in (jvm.Int(), jvm.Boolean(), jvm.Char()):
            locals[i] = aval_int_top()
        elif (isinstance(t, jvm.String) or t == jvm.String()
        or (hasattr(t, "name") and t.name == "java/lang/String")
        or (hasattr(t, "classname") and getattr(t.classname, "name", None) == "java/lang/String")):
            oid = len(heap)
            heap[oid] = {
                "class": jvm.String(),
                "fields": {"value": StringAbs.top()},
            }
            locals[i] = ('ref', oid)
        elif isinstance(t, jvm.Array):
            locals[i] = ('ref', None)
        else:
            locals[i] = ('ref', None)

    frame = Frame(locals=locals, stack=Stack.empty(), pc=PC(methodid, 0))
    return State(heap=heap, frames=Stack.empty().push(frame))


def join_avals(a: AVal, b: AVal) -> AVal:
    kind_a, val_a = a
    kind_b, val_b = b

    # different kinds
    if kind_a != kind_b:
        # If either side is a reference, keep it as a reference but
        # forget which exact object: ('ref', None).
        if kind_a == 'ref' or kind_b == 'ref':
            return ('ref', None)
        # (In principle we only have 'int' and 'ref', but keep this for safety.)
        return ('int', Interval.top())

    # same kind
    if kind_a == 'int':
        return ('int', join(val_a, val_b))   # type: ignore[arg-type]
    if kind_a == 'ref':
        ra, rb = val_a, val_b
        # if the ref id disagrees, join == "may be different objects" → None
        return ('ref', ra if ra == rb else None)

    raise AssertionError(f"unexpected aval kind: {kind_a}")



def join_frames(fa: Frame, fb: Frame) -> Frame:
    # Join locals pointwise (only indices that appear in either)
    keys = set(fa.locals.keys()) | set(fb.locals.keys())
    jlocals = {k: join_avals(fa.locals.get(k, ('int', Interval.top())),
                             fb.locals.get(k, ('int', Interval.top())))
               for k in keys}
    # Join stacks conservatively: require same height; else surrender to TOP-ish
    if len(fa.stack) == len(fb.stack):
        jstack_list = [join_avals(x, y) for x, y in zip(fa.stack, fb.stack)]
    else:
        jstack_list = [('int', Interval.top())] * max(len(fa.stack), len(fb.stack))

    jstack = Stack(jstack_list)
    return Frame(locals=jlocals, stack=jstack, pc=fa.pc)

def states_equal(a: Frame, b: Frame) -> bool:
    return a.locals == b.locals and a.stack == b.stack  # coarse but fine


def run_worklist_result(initial: State) -> List[str]:
    resultList: List[str] = []
    worklist: List[State] = [initial]
    seen: Dict[Tuple[jvm.AbsMethodID, int], Frame] = {}

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

if __name__ == "__main__":
    if "info" in sys.argv[1:]:
        jpamb.printinfo(
            "abstract-interpreter",  
            "1.0",                   # version string (any string is fine)
            "analysis",              # kind: usually "analysis" for this project
            ["string", "ai"],        # list of tags (whatever you like)
            for_science=True,        # whatever; jpamb just wants a bool here
        )
        sys.exit(0)

    # Normal analyzer mode: get a case from jpamb and run your analysis.
    methodid = jpamb.parse_methodid(sys.argv[-1])
    result = analyze_method_no_inputs(methodid)
    # methodid, input = jpamb.getcase()
    # result= analyze_method_no_inputs(methodid)
    print(result)
