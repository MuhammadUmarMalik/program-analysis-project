"""
abstract_interpreter2.py — Abstract interpreter using the Prefix+Length domain.

Drop-in replacement for abstract_interpreter.py:
  from abstract_interpreter2 import analyze_method_no_inputs, get_abstract_warnings

Key difference: String heap objects store StringAbs2 values instead of StringAbs,
enabling prefix-aware reasoning (startsWith, equals, concat, substring precision).
All other analysis logic (intervals, worklist, branching) is unchanged.
"""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass
from math import inf
from typing import Dict, List, Optional, Tuple

import jpamb
from jpamb import jvm
from loguru import logger

from string_abs2 import (
    StringAbs2,
    Interval,
    str2_concat,
    str2_substring,
    str2_length,
    str2_equals,
    str2_startswith,
    str2_endswith,
    str2_contains,
    str2_charat,
    str2_valueof_int,
)

NEG_INF = -inf
POS_INF = inf
warnings: List[str] = []

ASSERTIONS_ENABLED = True


# ---------------------------------------------------------------------------
# Interval helpers (using Interval from string_abs2)
# ---------------------------------------------------------------------------

def _join_ivl(a: Interval, b: Interval) -> Interval:
    return a.join(b)


def _add(a: Interval, b: Interval) -> Interval:
    return a.add(b)


def _sub(a: Interval, b: Interval) -> Interval:
    return a.sub_interval(b)


def _mul(a: Interval, b: Interval) -> Interval:
    if a.is_bot or b.is_bot:
        return Interval.bot()
    candidates = [a.lo * b.lo, a.lo * b.hi, a.hi * b.lo, a.hi * b.hi]  # type: ignore
    return Interval(min(candidates), max(candidates))


def _div(a: Interval, b: Interval) -> Interval:
    if a.is_bot or b.is_bot:
        return Interval.bot()
    if b.lo == 0 and b.hi == 0:
        return Interval.bot()
    if b.lo is not None and b.hi is not None and b.lo <= 0 <= b.hi:  # type: ignore
        return Interval.top()
    inv = [1 / b.lo, 1 / b.hi]  # type: ignore
    b1, b2 = min(inv), max(inv)
    candidates = [a.lo * b1, a.lo * b2, a.hi * b1, a.hi * b2]  # type: ignore
    return Interval(min(candidates), max(candidates))


def _rem(a: Interval, b: Interval) -> Interval:
    if a.is_bot or b.is_bot:
        return Interval.bot()
    if b.lo is not None and b.hi is not None and b.lo <= 0 <= b.hi:  # type: ignore
        return Interval.top()
    maxmag = max(abs(b.lo), abs(b.hi))  # type: ignore
    upper = max(0.0, maxmag - 1)
    return Interval(-upper, upper)


def _contains_zero(i: Interval) -> bool:
    if i.is_bot:
        return False
    return i.lo <= 0.0 <= i.hi  # type: ignore


def _ivl_cmp(a: Interval, b: Interval, cond: str) -> str:
    if a.is_bot or b.is_bot:
        return "false"
    al, ah, bl, bh = a.lo, a.hi, b.lo, b.hi
    if cond == "eq":
        if al == ah == bl == bh:
            return "true" if al == bl else "false"
        if ah < bl or bh < al:  # type: ignore
            return "false"
        return "maybe"
    if cond == "ne":
        if al == ah == bl == bh:
            return "false" if al == bl else "true"
        if ah < bl or bh < al:  # type: ignore
            return "true"
        return "maybe"
    if cond == "lt":
        if ah < bl:  # type: ignore
            return "true"
        if al >= bh:  # type: ignore
            return "false"
        return "maybe"
    if cond == "le":
        if ah <= bl:  # type: ignore
            return "true"
        if al > bh:  # type: ignore
            return "false"
        return "maybe"
    if cond == "gt":
        if bh < al:  # type: ignore
            return "true"
        if bl >= ah:  # type: ignore
            return "false"
        return "maybe"
    if cond == "ge":
        if bh <= al:  # type: ignore
            return "true"
        if bl > ah:  # type: ignore
            return "false"
        return "maybe"
    return "maybe"


def _ivl_cond_zero(i: Interval, cond: str) -> str:
    if i.is_bot:
        return "false"
    lo, hi = i.lo, i.hi
    if cond == "eq":
        if lo == 0 and hi == 0:
            return "true"
        if hi < 0 or lo > 0:  # type: ignore
            return "false"
        return "maybe"
    if cond == "ne":
        if lo == 0 and hi == 0:
            return "false"
        if hi < 0 or lo > 0:  # type: ignore
            return "true"
        return "maybe"
    if cond == "lt":
        if hi < 0:  # type: ignore
            return "true"
        if lo >= 0:  # type: ignore
            return "false"
        return "maybe"
    if cond == "le":
        if hi <= 0:  # type: ignore
            return "true"
        if lo > 0:  # type: ignore
            return "false"
        return "maybe"
    if cond == "gt":
        if lo > 0:  # type: ignore
            return "true"
        if hi <= 0:  # type: ignore
            return "false"
        return "maybe"
    if cond == "ge":
        if lo >= 0:  # type: ignore
            return "true"
        if hi < 0:  # type: ignore
            return "false"
        return "maybe"
    return "maybe"


# ---------------------------------------------------------------------------
# AVal type: ('int', Interval) | ('ref', Optional[int])
# ---------------------------------------------------------------------------

AVal = tuple[str, object]


def aval_int_const(n: int) -> AVal:
    return ('int', Interval.const(n))


def aval_int_top() -> AVal:
    return ('int', Interval.top())


def is_int(v: AVal) -> bool:
    return v[0] == 'int'


def is_ref(v: AVal) -> bool:
    return v[0] == 'ref'


def get_int_ivl(v: AVal) -> Interval:
    assert is_int(v), f"expected int aval, got {v}"
    return v[1]  # type: ignore[return-value]


def get_ref_id(v: AVal) -> Optional[int]:
    assert is_ref(v), f"expected ref aval, got {v}"
    return v[1]  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# PC / Bytecode / Stack / Frame / State
# ---------------------------------------------------------------------------

@dataclass
class PC:
    method: jvm.AbsMethodID
    offset: int

    def __iadd__(self, delta: int) -> "PC":
        self.offset += delta
        return self

    def __add__(self, delta: int) -> "PC":
        return PC(self.method, self.offset + delta)

    def __str__(self) -> str:
        return f"{self.method}:{self.offset}"


@dataclass
class Bytecode:
    suite: jpamb.Suite
    methods: dict
    offset_to_index: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.offset_to_index is None:
            self.offset_to_index = {}

    def _ensure_loaded(self, method: jvm.AbsMethodID) -> None:
        if method not in self.methods:
            ops = list(self.suite.method_opcodes(method))
            self.methods[method] = ops
            self.offset_to_index[method] = {op.offset: i for i, op in enumerate(ops)}

    def __getitem__(self, pc: PC) -> jvm.Opcode:
        self._ensure_loaded(pc.method)
        return self.methods[pc.method][pc.offset]


@dataclass
class Stack[T]:
    items: list

    def __bool__(self) -> bool:
        return bool(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    @classmethod
    def empty(cls) -> "Stack":
        return cls([])

    def peek(self) -> T:
        return self.items[-1]

    def pop(self) -> T:
        return self.items.pop(-1)

    def push(self, value: T) -> "Stack":
        self.items.append(value)
        return self


@dataclass
class Frame:
    locals: dict
    stack: Stack
    pc: PC

    @staticmethod
    def from_method(method: jvm.AbsMethodID) -> "Frame":
        return Frame({}, Stack.empty(), PC(method, 0))


@dataclass
class _ArrayObj:
    __slots__ = ("type", "length", "data")

    def __init__(self, elem_type, length: int) -> None:
        self.type = elem_type
        self.length = length
        if isinstance(elem_type, (jvm.Int, jvm.Char)) or elem_type in (jvm.Int(), jvm.Char()):
            self.data = [('int', Interval.const(0)) for _ in range(length)]
        else:
            self.data = [('ref', None) for _ in range(length)]


@dataclass
class State:
    heap: dict
    frames: Stack


suite = jpamb.Suite()
bc = Bytecode(suite, {})


# ---------------------------------------------------------------------------
# Fork helper
# ---------------------------------------------------------------------------

def _clone_frame(fr: Frame) -> Frame:
    return Frame(dict(fr.locals), Stack(list(fr.stack.items)), PC(fr.pc.method, fr.pc.offset))


def _fork(state: State) -> tuple[State, State]:
    h_t = copy.deepcopy(state.heap)
    h_f = copy.deepcopy(state.heap)
    prefix = state.frames.items[:-1]
    fr = state.frames.peek()
    fr_t, fr_f = _clone_frame(fr), _clone_frame(fr)
    return (
        State(heap=h_t, frames=Stack(list(prefix) + [fr_t])),
        State(heap=h_f, frames=Stack(list(prefix) + [fr_f])),
    )


# ---------------------------------------------------------------------------
# step_abstract2
# ---------------------------------------------------------------------------

def step_abstract2(state: State) -> list | str:
    frame = state.frames.peek()
    opr = bc[frame.pc]

    def push(v: AVal) -> None:
        frame.stack.push(v)

    def pop() -> AVal:
        try:
            return frame.stack.pop()
        except IndexError:
            logger.error(f"Stack underflow at {opr!r}")
            return ('int', Interval.top())

    match opr:

        # --- Push ---
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
                    state.heap[oid] = {"class": v.type, "fields": {"value": StringAbs2.from_const(s)}}
                    push(('ref', oid))
                case _:
                    push(('ref', None))
            frame.pc += 1
            return [state]

        # --- Load ---
        case jvm.Load(type=_, index=i):
            push(frame.locals[i])
            frame.pc += 1
            return [state]

        # --- Binary int ---
        case jvm.Binary(type=jvm.Int(), operant=o):
            v2, v1 = pop(), pop()
            i1, i2 = get_int_ivl(v1), get_int_ivl(v2)
            match o:
                case jvm.BinaryOpr.Add:
                    push(('int', _add(i1, i2)))
                case jvm.BinaryOpr.Sub:
                    push(('int', _sub(i1, i2)))
                case jvm.BinaryOpr.Mul:
                    push(('int', _mul(i1, i2)))
                case jvm.BinaryOpr.Div:
                    if _contains_zero(i2):
                        return "divide by zero"
                    push(('int', _div(i1, i2)))
                case jvm.BinaryOpr.Rem:
                    if _contains_zero(i2):
                        return "divide by zero"
                    push(('int', _rem(i1, i2)))
                case _:
                    raise NotImplementedError(f"Binary: {o}")
            frame.pc += 1
            return [state]

        # --- Return ---
        case jvm.Return(type=type_):
            ret_val = pop() if type_ is not None else None
            state.frames.pop()
            if state.frames:
                if ret_val is not None:
                    state.frames.peek().stack.push(ret_val)
                state.frames.peek().pc += 1
                return [state]
            return "ok"

        # --- Cast ---
        case jvm.Cast(from_=jvm.Int(), to_=jvm.Short()):
            v1 = pop()
            push(('int', get_int_ivl(v1)))
            frame.pc += 1
            return [state]

        # --- If ---
        case jvm.If(condition=cond, target=t):
            v2, v1 = pop(), pop()
            c = (cond or "").lower()
            if is_int(v1) and is_int(v2):
                res = _ivl_cmp(get_int_ivl(v1), get_int_ivl(v2), c)
                if res == "true":
                    frame.pc.offset = t
                    return [state]
                if res == "false":
                    frame.pc += 1
                    return [state]
                st_t, st_f = _fork(state)
                st_t.frames.peek().pc.offset = t
                st_f.frames.peek().pc += 1
                return [st_t, st_f]
            elif is_ref(v1) and is_ref(v2):
                r1, r2 = get_ref_id(v1), get_ref_id(v2)
                take = (r1 == r2) if c == "is" else (r1 != r2)
                frame.pc.offset = t if take else frame.pc.offset + 1
                return [state]
            raise TypeError(f"If expected ints or refs, got {v1}, {v2}")

        # --- Ifz ---
        case jvm.Ifz(condition=cond, target=tgt):
            v1 = pop()
            c = (cond or "").lower()
            if is_int(v1):
                res = _ivl_cond_zero(get_int_ivl(v1), c)
                if res == "true":
                    frame.pc.offset = tgt
                    return [state]
                if res == "false":
                    frame.pc += 1
                    return [state]
                st_t, st_f = _fork(state)
                st_t.frames.peek().pc.offset = tgt
                st_f.frames.peek().pc += 1
                return [st_t, st_f]
            elif is_ref(v1):
                ref_id = get_ref_id(v1)
                take = (ref_id is None) if c == "is" else (ref_id is not None)
                frame.pc.offset = tgt if take else frame.pc.offset + 1
                return [state]
            raise TypeError(f"Ifz expected int or ref, got {v1}")

        # --- Goto ---
        case jvm.Goto(target=tgt):
            frame.pc = PC(frame.pc.method, tgt)
            return [state]

        # --- Store ---
        case jvm.Store(type=_, index=i):
            frame.locals[i] = pop()
            frame.pc += 1
            return [state]

        # --- ArrayLoad ---
        case jvm.ArrayLoad(type=t):
            idx_val = pop()
            arr_val = pop()
            ref = get_ref_id(arr_val)
            if ref is None:
                return "null pointer"
            inst = state.heap.get(ref)
            if not isinstance(inst, _ArrayObj):
                return "null pointer"
            idx_ivl = get_int_ivl(idx_val)
            if idx_ivl.is_bot or idx_ivl.hi < 0 or idx_ivl.lo >= inst.length:  # type: ignore
                return "out of bounds"
            push(('int', Interval.top()) if isinstance(inst.type, (jvm.Int, jvm.Char)) else ('ref', None))
            frame.pc += 1
            return [state]

        # --- ArrayLength ---
        case jvm.ArrayLength():
            arr_val = pop()
            ref = get_ref_id(arr_val) if is_ref(arr_val) else None
            if ref is None:
                return "null pointer"
            inst = state.heap.get(ref)
            if not isinstance(inst, _ArrayObj):
                return "null pointer"
            push(aval_int_const(inst.length))
            frame.pc += 1
            return [state]

        # --- ArrayStore ---
        case jvm.ArrayStore(type=_):
            value = pop()
            idx_val = pop()
            arr_val = pop()
            if not is_ref(arr_val) or get_ref_id(arr_val) is None:
                return "null pointer"
            inst = state.heap.get(get_ref_id(arr_val))
            if not isinstance(inst, _ArrayObj):
                return "null pointer"
            idx_ivl = get_int_ivl(idx_val)
            if idx_ivl.lo < 0 or idx_ivl.hi >= inst.length:  # type: ignore
                return "out of bounds"
            frame.pc += 1
            return [state]

        # --- NewArray ---
        case jvm.NewArray(type=elem_type, dim=_):
            len_val = pop()
            len_ivl = get_int_ivl(len_val)
            if len_ivl.is_bot or len_ivl.hi < 0 or (len_ivl.lo is not None and len_ivl.lo < 0):  # type: ignore
                return "negative array size"
            size = int(len_ivl.lo) if len_ivl.lo == len_ivl.hi else int(len_ivl.lo or 0)
            arr = _ArrayObj(elem_type, max(0, size))
            idx = len(state.heap)
            state.heap[idx] = arr
            push(('ref', idx))
            frame.pc += 1
            return [state]

        # --- InvokeVirtual ---
        case jvm.InvokeVirtual(method=m):
            cls = m.classname.slashed()
            name = m.methodid.name
            argc = len(list(m.extension.params or []))
            args = [pop() for _ in range(argc)][::-1]
            obj_ref = pop()

            if not is_ref(obj_ref):
                return "null pointer"
            oid = get_ref_id(obj_ref)
            if oid is None:
                return "null pointer"
            obj = state.heap.get(oid)
            if obj is None:
                return "null pointer"

            if cls == "java/lang/String":
                s_abs: StringAbs2 = obj["fields"]["value"]

                if name == "length":
                    push(('int', str2_length(s_abs)))
                    frame.pc += 1
                    return [state]

                if name == "charAt":
                    idx_val = args[0]
                    idx_ivl = get_int_ivl(idx_val)
                    char_ivl, may_oob = str2_charat(s_abs, idx_ivl)
                    if may_oob:
                        return "out of bounds"
                    push(('int', char_ivl))
                    frame.pc += 1
                    return [state]

                if name == "equals":
                    arg_ref = args[0]
                    if not is_ref(arg_ref) or get_ref_id(arg_ref) is None:
                        push(aval_int_const(0))
                    else:
                        arg_obj = state.heap.get(get_ref_id(arg_ref))
                        if arg_obj is None:
                            push(aval_int_const(0))
                        else:
                            arg_abs: StringAbs2 = arg_obj["fields"]["value"]
                            res = str2_equals(s_abs, arg_abs)
                            push(aval_int_const(1 if res == "true" else 0) if res != "maybe" else ('int', Interval(0.0, 1.0)))
                    frame.pc += 1
                    return [state]

                if name == "concat":
                    arg_ref = args[0]
                    if not is_ref(arg_ref) or get_ref_id(arg_ref) is None:
                        res_abs = StringAbs2.unknown_nonnull()
                    else:
                        arg_obj = state.heap.get(get_ref_id(arg_ref))
                        arg_abs = arg_obj["fields"]["value"] if arg_obj else StringAbs2.top()
                        res_abs = str2_concat(s_abs, arg_abs)
                    new_oid = len(state.heap)
                    state.heap[new_oid] = {"class": jvm.String(), "fields": {"value": res_abs}}
                    push(('ref', new_oid))
                    frame.pc += 1
                    return [state]

                if name == "substring":
                    i_val, j_val = args[0], args[1]
                    res_abs, may_oob = str2_substring(s_abs, get_int_ivl(i_val), get_int_ivl(j_val))
                    if may_oob:
                        return "out of bounds"
                    new_oid = len(state.heap)
                    state.heap[new_oid] = {"class": jvm.String(), "fields": {"value": res_abs}}
                    push(('ref', new_oid))
                    frame.pc += 1
                    return [state]

                if name == "startsWith":
                    arg_ref = args[0]
                    arg_abs = state.heap.get(get_ref_id(arg_ref), {}).get("fields", {}).get("value", StringAbs2.top()) if is_ref(arg_ref) and get_ref_id(arg_ref) is not None else StringAbs2.top()
                    res = str2_startswith(s_abs, arg_abs)
                    push(aval_int_const(1 if res == "true" else 0) if res != "maybe" else ('int', Interval(0.0, 1.0)))
                    frame.pc += 1
                    return [state]

                if name == "endsWith":
                    arg_ref = args[0]
                    arg_abs = state.heap.get(get_ref_id(arg_ref), {}).get("fields", {}).get("value", StringAbs2.top()) if is_ref(arg_ref) and get_ref_id(arg_ref) is not None else StringAbs2.top()
                    res = str2_endswith(s_abs, arg_abs)
                    push(aval_int_const(1 if res == "true" else 0) if res != "maybe" else ('int', Interval(0.0, 1.0)))
                    frame.pc += 1
                    return [state]

                if name == "contains":
                    arg_ref = args[0]
                    arg_abs = state.heap.get(get_ref_id(arg_ref), {}).get("fields", {}).get("value", StringAbs2.top()) if is_ref(arg_ref) and get_ref_id(arg_ref) is not None else StringAbs2.top()
                    res = str2_contains(s_abs, arg_abs)
                    push(aval_int_const(1 if res == "true" else 0) if res != "maybe" else ('int', Interval(0.0, 1.0)))
                    frame.pc += 1
                    return [state]

                raise NotImplementedError(f"String.{name} not handled")

            raise NotImplementedError(f"InvokeVirtual: {cls}.{name}")

        # --- InvokeSpecial ---
        case jvm.InvokeSpecial(method=m, is_interface=_):
            args_list = m.extension.params._elements
            [pop() for _ in range(len(args_list))]
            pop()  # receiver
            if m.methodid.name == "<init>" and m.classname.name in (
                "java/lang/Object", "java/lang/AssertionError", "java/lang/String"
            ):
                frame.pc += 1
                return [state]
            newframe = Frame.from_method(m)
            state.frames.push(newframe)
            return [state]

        # --- InvokeStatic ---
        case jvm.InvokeStatic(method=m):
            cls = m.classname.name
            name = m.methodid.name
            argc = len(list(m.extension.params or []))
            args = [pop() for _ in range(argc)][::-1]

            if cls == "java/lang/String" and name == "valueOf":
                arg = args[0] if args else None
                if arg is not None and is_int(arg):
                    s_abs = str2_valueof_int(get_int_ivl(arg))
                else:
                    s_abs = StringAbs2.unknown_nonnull()
                oid = len(state.heap)
                state.heap[oid] = {"class": jvm.String(), "fields": {"value": s_abs}}
                push(('ref', oid))
                frame.pc += 1
                return [state]

            if cls == "java/lang/String":
                raise NotImplementedError(f"Static String.{name}")

            if cls == "java/lang/Character":
                code_ivl = get_int_ivl(args[0]) if args and is_int(args[0]) else Interval.bot()
                if name == "isDigit":
                    if not code_ivl.is_bot and code_ivl.lo == code_ivl.hi:
                        push(aval_int_const(1 if chr(int(code_ivl.lo)).isdigit() else 0))
                    else:
                        push(('int', Interval(0.0, 1.0)))
                elif name == "isWhitespace":
                    if not code_ivl.is_bot and code_ivl.lo == code_ivl.hi:
                        push(aval_int_const(1 if chr(int(code_ivl.lo)).isspace() else 0))
                    else:
                        push(('int', Interval(0.0, 1.0)))
                elif name == "getNumericValue":
                    if not code_ivl.is_bot and code_ivl.lo == code_ivl.hi:
                        ch = chr(int(code_ivl.lo))
                        n = (ord(ch) - ord('0')) if '0' <= ch <= '9' else -1
                        push(aval_int_const(n))
                    else:
                        push(('int', Interval(-1.0, 9.0)))
                else:
                    raise NotImplementedError(f"Character.{name}")
                frame.pc += 1
                return [state]

            # normal static call
            callee = Frame.from_method(m)
            callee.locals = {i: v for i, v in enumerate(args)}
            state.frames.push(callee)
            return [state]

        # --- Throw ---
        case jvm.Throw():
            obj_ref = pop()
            oid = get_ref_id(obj_ref) if is_ref(obj_ref) else None
            obj = state.heap.get(oid) if oid is not None else None
            if obj is not None:
                cls_val = obj.get("class")
                cls_name = cls_val.name if hasattr(cls_val, "name") else str(cls_val)
                if "AssertionError" in cls_name:
                    return "assertion error"
            return "exception"

        # --- Pop ---
        case jvm.Pop():
            pop()
            frame.pc += 1
            return [state]

        # --- New ---
        case jvm.New(classname=cls):
            idx = len(state.heap)
            state.heap[idx] = {"class": cls, "fields": {}}
            push(('ref', idx))
            frame.pc += 1
            return [state]

        # --- Dup ---
        case jvm.Dup():
            push(frame.stack.peek())
            frame.pc += 1
            return [state]

        # --- Incr ---
        case jvm.Incr(index=i, amount=d):
            v = frame.locals[i]
            ivl = get_int_ivl(v)
            frame.locals[i] = ('int', _add(ivl, Interval.const(d)))
            frame.pc += 1
            return [state]

        # --- Get ---
        case jvm.Get(field=f):
            fld = opr.field
            t = fld.extension.type
            name = fld.extension.name
            if opr.static:
                if name == "$assertionsDisabled":
                    push(aval_int_const(0 if ASSERTIONS_ENABLED else 1))
                elif isinstance(t, (jvm.Int, jvm.Boolean, jvm.Char)) or t in (jvm.Int(), jvm.Boolean(), jvm.Char()):
                    push(aval_int_const(0))
                else:
                    push(('ref', None))
                frame.pc += 1
                return [state]
            pop()
            raise NotImplementedError("Get instance field not implemented")

        case a:
            a.help()
            raise NotImplementedError(f"Unhandled: {a!r}")


# ---------------------------------------------------------------------------
# Initial state + worklist
# ---------------------------------------------------------------------------

def _build_initial_state(methodid: jvm.AbsMethodID) -> State:
    heap: dict = {}
    locals_: dict = {}
    for i, t in enumerate(methodid.extension.params):
        if isinstance(t, (jvm.Int, jvm.Boolean, jvm.Char)) or t in (jvm.Int(), jvm.Boolean(), jvm.Char()):
            locals_[i] = aval_int_top()
        elif isinstance(t, jvm.String) or t == jvm.String():
            oid = len(heap)
            heap[oid] = {"class": jvm.String(), "fields": {"value": StringAbs2.top()}}
            locals_[i] = ('ref', oid)
        else:
            locals_[i] = ('ref', None)
    return State(heap=heap, frames=Stack.empty().push(Frame(locals=locals_, stack=Stack.empty(), pc=PC(methodid, 0))))


def _join_avals(a: AVal, b: AVal) -> AVal:
    ka, va = a
    kb, vb = b
    if ka != kb:
        return ('ref', None)
    if ka == 'int':
        return ('int', va.join(vb))  # type: ignore[union-attr]
    return ('ref', va if va == vb else None)


def _join_frames(fa: Frame, fb: Frame) -> Frame:
    keys = set(fa.locals) | set(fb.locals)
    jlocals = {k: _join_avals(fa.locals.get(k, aval_int_top()), fb.locals.get(k, aval_int_top())) for k in keys}
    if len(fa.stack) == len(fb.stack):
        jstack = Stack([_join_avals(x, y) for x, y in zip(fa.stack, fb.stack)])
    else:
        jstack = Stack([aval_int_top()] * max(len(fa.stack), len(fb.stack)))
    return Frame(locals=jlocals, stack=jstack, pc=fa.pc)


def _frames_equal(a: Frame, b: Frame) -> bool:
    return a.locals == b.locals and list(a.stack) == list(b.stack)


def run_worklist(initial: State) -> List[str]:
    results: List[str] = []
    worklist: List[State] = [initial]
    seen: Dict[tuple, Frame] = {}

    while worklist:
        st = worklist.pop()
        if not st.frames:
            continue
        fr = st.frames.peek()
        key = (fr.pc.method, fr.pc.offset)
        if key in seen:
            joined = _join_frames(seen[key], fr)
            if _frames_equal(joined, seen[key]):
                continue
            seen[key] = joined
            st.frames.items[-1] = joined
        else:
            seen[key] = fr

        res = step_abstract2(st)
        if isinstance(res, str):
            results.append(res)
            continue
        for nxt in res:
            if isinstance(nxt, str):
                results.append(nxt)
            elif nxt.frames:
                worklist.append(nxt)

    return results


def analyze_method_no_inputs(methodid: jvm.AbsMethodID) -> List[str]:
    warnings.clear()
    return run_worklist(_build_initial_state(methodid))


def get_abstract_warnings() -> List[str]:
    return warnings


if __name__ == "__main__":
    if "info" in sys.argv[1:]:
        jpamb.printinfo("abstract-interpreter-prefix", "2.0", "analysis", ["string", "prefix"], for_science=True)
        sys.exit(0)
    methodid = jpamb.parse_methodid(sys.argv[-1])
    print(analyze_method_no_inputs(methodid))
