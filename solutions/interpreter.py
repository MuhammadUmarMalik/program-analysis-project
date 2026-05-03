import jpamb
from jpamb import jvm
from dataclasses import dataclass

import sys
from loguru import logger

logger.remove()
logger.add(sys.stderr, format="[{level}] {message}")


ASSERTIONS_ENABLED = True
methodid, input = jpamb.getcase()

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


@dataclass
class Bytecode:
    suite: jpamb.Suite
    methods: dict[jvm.AbsMethodID, list[jvm.Opcode]]
    # add this:
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
        return self.methods[pc.method][pc.offset]


@dataclass
class Stack[T]:
    items: list[T]

    def __bool__(self) -> bool:
        return len(self.items) > 0

    @classmethod
    def empty(cls):
        return cls([])

    def peek(self) -> T:
        return self.items[-1]

    def pop(self) -> T:
        return self.items.pop(-1)

    def push(self, value):
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
    locals: dict[int, jvm.Value]
    stack: Stack[jvm.Value]
    pc: PC

    def __str__(self):
        loc_str = ", ".join(f"{k}:{v}" for k, v in sorted(self.locals.items()))
        return f"<{{{loc_str}}}, {self.stack}, {self.pc}>"

    @staticmethod
    def from_method(method: jvm.AbsMethodID) -> "Frame":
        return Frame({}, Stack.empty(), PC(method, 0))

@dataclass
class State:
    heap: dict[int, jvm.Value]
    frames: Stack[Frame]

    def __str__(self):
        return f"{self.heap} {self.frames}"
    
@dataclass
class _ArrayObj:
    __slots__ = ("type", "length", "data")
    def __init__(self, elem_type, length):
        self.type = elem_type
        self.length = length
        # default-fill by element kind
        if isinstance(elem_type, jvm.Int) or elem_type == jvm.Int():
            self.data = [jvm.Value.int(0) for _ in range(length)]
        elif isinstance(elem_type, jvm.Char) or elem_type == jvm.Char():
            self.data = [jvm.Value.char('\x00') for _ in range(length)]
        else:
            # reference / other kinds start null
            self.data = [None for _ in range(length)]

@dataclass
class _ObjRef:
    __slots__ = ("class_name", "inited")
    def __init__(self, class_name: str):
        self.class_name = str(class_name)
        self.inited = False  # set true by <init
def _jump_pc(method: jvm.AbsMethodID, tgt: int) -> PC:
    # ensure ops loaded
    ops = bc.methods.get(method)
    if ops is None:
        ops = list(bc.suite.method_opcodes(method))
        bc.methods[method] = ops

    n = len(ops)

    # Heuristic 1: if tgt is a valid opcode index and doesn't equal that op's offset,
    # treat it as an index jump (common in simple methods).
    if 0 <= tgt < n and ops[tgt].offset != tgt:
        return PC(method, tgt)

    # Heuristic 2: try bytecode offset match (common in bigger methods).
    for i, op in enumerate(ops):
        if op.offset == tgt:
            return PC(method, i)

    # Heuristic 3: if still ambiguous but index is in-range, use index.
    if 0 <= tgt < n:
        return PC(method, tgt)

    raise ValueError(f"Branch target {tgt} not found in {method}")

def _is_void_type(t) -> bool:
    # Works with multiple possible encodings of void
    if t is None:                      # sometimes void is encoded as None
        return True
    # many bytecode APIs carry a JVM descriptor
    if getattr(t, "descriptor", None) == "V":
        return True
    # some expose a printable form
    s = str(t).lower()
    return s in {"v", "void", "returnvoid"}

def step(state: State) -> State | str:
    assert isinstance(state, State), f"expected frame but got {state}"
    frame = state.frames.peek()
    opr = bc[frame.pc]
    logger.debug(f"STEP {opr}\n{state}")
    match opr:

        case jvm.Push(value=v):
            # If we're pushing a String constant, allocate an object and push a ref
            match v.type:
                case jvm.String():
                    oid = len(state.heap)
                    state.heap[oid] = {
                        "class": v.type,          # or a proper String class ID if you have one
                        "fields": {"value": v.value}
                    }
                    frame.stack.push(jvm.Value.ref(oid))
                case _:
                    # normal behavior for ints, bools, etc.
                    frame.stack.push(v)

            frame.pc += 1
            return state

        case jvm.Load(type=jvm.Int(), index=i):
            logger.debug(frame.locals)
            frame.stack.push(frame.locals[i])
            frame.pc += 1
            return state
        case jvm.Binary(type=jvm.Int(), operant=o): #jvm.BinaryOpr.Div
            v2, v1 = frame.stack.pop(), frame.stack.pop()
            assert v1.type is jvm.Int(), f"expected int, but got {v1}"
            assert v2.type is jvm.Int(), f"expected int, but got {v2}"
            
            match o:
                case jvm.BinaryOpr.Add: 
                    frame.stack.push(jvm.Value.int(v1.value + v2.value))
                case jvm.BinaryOpr.Sub: 
                    frame.stack.push(jvm.Value.int(v1.value - v2.value))
                case jvm.BinaryOpr.Mul: 
                    frame.stack.push(jvm.Value.int(v1.value * v2.value))
                case jvm.BinaryOpr.Div: 
                    if v2.value == 0:
                        return "divide by zero"
                    frame.stack.push(jvm.Value.int(v1.value // v2.value))
                case jvm.BinaryOpr.Rem: 
                    if v2.value == 0:
                        return "divide by zero"
                    frame.stack.push(jvm.Value.int(v1.value % v2.value))
                case _: 
                    raise NotImplementedError(f"Unhandled binary operant: {o}")

            frame.pc += 1
            return state
        case jvm.Return(type=type):
            if type is not None: 
                v1 = frame.stack.pop()
                assert v1.type == type, f"expected {type}, but got {v1}"
            
            state.frames.pop()
            if state.frames:
                frame = state.frames.peek()
                if type is not None:
                    frame.stack.push(v1)
                frame.pc += 1
                return state
            else:
                return "ok"
        case jvm.Cast(from_=jvm.Int(), to_=jvm.Short()):
            v1 = frame.stack.pop()
            assert v1.type == jvm.Int(), f"expected type int, but got {v1}"
            short_val = ((v1.value + 2*15) % 216) - 2*15 
            frame.stack.push(jvm.Value(type=jvm.Int(), value=short_val))
            frame.pc += 1
            return state     
        case jvm.If(condition=cond, target=t):
            v2 = frame.stack.pop()
            v1 = frame.stack.pop()
            c = (cond or "").lower()
            if isinstance(v1.type, jvm.Int) and isinstance(v2.type, jvm.Int):
                if c == "eq":
                     take =  (v1.value == v2.value)
                elif c == "ne":
                    take =  (v1.value != v2.value)
                elif c == "lt":
                    take =  (v1.value < v2.value)
                elif c == "le":
                    take =  (v1.value <= v2.value)
                elif c == "gt":
                    take =  (v1.value > v2.value)
                elif c == "ge":
                    take =  (v1.value >= v2.value)
                else:
                    raise NotImplementedError(f"Unknown If condition: {cond!r}")
            elif isinstance(v1.type, jvm.Reference) and isinstance(v2.type, jvm.Reference):
                if c == "is":
                    take = (v1 == v2)
                elif c == "isnot":
                    take = (v1 != v2)
                else:
                    raise NotImplementedError(f"Unknown If condition: {cond!r}")
            else:
                raise TypeError(f"if expected two ints or two refs, got {v1}, {v2}")
            if take:
                frame.pc.offset = t
            else:
                frame.pc += 1
            return state  
        case jvm.Ifz(condition=cond, target=tgt):
            v1 = frame.stack.pop()
            cond = (cond or "").lower()
            if isinstance(v1.type, jvm.Int):
                match cond:
                    case "eq":
                        take = v1.value == 0
                    case "ne":
                        take = v1.value != 0
                    case "lt":
                        take = v1.value < 0
                    case "le":
                        take = v1.value <= 0
                    case "gt":
                        take = v1.value > 0
                    case "ge":
                        take = v1.value >= 0
                    case _:
                        raise NotImplementedError(f"Don't know how to handle the condition: {opr!r}")    
            elif isinstance(v1.type, jvm.Reference):
                if cond == "is":
                    take = (v1.value is None)
                elif cond == "isnot":
                    take = (v1.value is not None)
                else:
                    raise NotImplementedError(f"Unknown Ifz ref condition: {cond!r}")
            else:
                raise TypeError(f"ifz expected int or ref, but got {v1}")
        
            if take:
                frame.pc.offset = tgt
            else:
                frame.pc += 1
            return state   
        case jvm.Goto(target=tgt):
            frame.pc = PC(frame.pc.method, tgt) 
            #frame.pc = _jump_pc(frame.pc.method, tgt)
            return state
        case jvm.Load(type=t, index=i):
            assert frame.locals[i].type is t, f"expected {t}, but got {frame.locals[i]}"
            frame.stack.push(frame.locals[i])
            frame.pc += 1
            return state
        case jvm.Store(type=t, index=i):
            # pop value to store
            val = frame.stack.pop()

            if isinstance(t, jvm.Reference):
                # accept any object as a reference; don’t assert identity
                frame.locals[i] = val
                frame.pc += 1
                return state

            # String refs arrive as jvm.Reference in bytecode; they are handled above.
            # Remaining types: Int, Boolean (stored as int).
            if t is jvm.Int():
                val = jvm.Value.int(val.value)
            elif t is jvm.Boolean():
                val = jvm.Value.int(1 if val.value != 0 else 0)
            elif not (val.type is t):
                assert False, f"not implemented for type {t}"
            # write local
            frame.locals[i] = val

            frame.pc += 1
            return state
        case jvm.ArrayLoad(type=t):
            # Stack: ..., arrayref, index  → pop in reverse order

            index = frame.stack.pop().value
            arr = frame.stack.pop()
            
            assert arr.type is jvm.Reference(), f"expected Reference, but got {arr}"

            ref = arr.value
            if ref is None:
                return "null pointer"

            instance = state.heap[ref]

            if not isinstance(instance, _ArrayObj):
                return "null pointer"
            if index < 0:
                return "out of bounds"
            if index >= instance.length:
                return "out of bounds"
            
            elem = instance.data[index]
        
            if t is jvm.Char():
                frame.stack.push(jvm.Value.int(ord(elem.value)))
                logger.debug(f"push Char: {elem.value}" + "Its type is Char")
                #frame.stack.push(jvm.Value.char(instance.data[index].value))
            elif t is jvm.Int():
                frame.stack.push(jvm.Value.int(elem.value))
                logger.debug(f"push INT: {elem.value}" + "Its type is Int")
            else:
                frame.stack.push(elem)
                logger.debug(f"push Ref: {elem.value}" + "Its type is Ref")

            frame.pc += 1
            return state

        case jvm.ArrayLength():
            arr = frame.stack.pop()

            ref = getattr(arr, "value", arr)
            if ref is None:
                return "null pointer"
            
            logger.debug(f"ArrayIndex: {ref}")
            instance = state.heap[ref]

            if not isinstance(instance, _ArrayObj):
                return "null pointer"
                #raise TypeError(f"Expected _ArrayObj From ArrayLength, got {type(arr)}")
            frame.stack.push(jvm.Value.int(instance.length))
            frame.pc += 1
            return state

        case jvm.ArrayStore(type=t):
            # Stack: ..., arrayref, index, value  → pop in reverse order
            value = frame.stack.pop()
            index = frame.stack.pop().value
            arr   = frame.stack.pop().value


            if arr is None:
                return "null pointer" 
            
            instance = state.heap[arr]
  
            if not isinstance(instance, _ArrayObj):
                return "null pointer"
                #raise TypeError(f"Expected _ArrayObj in ArrayStore, got {type(instance)}")
            if index < 0 or index >= instance.length:
                return "out of bounds"
            
            logger.debug(f"Storing {value} of type {t} at index {index} of array {instance} of type {instance.type}")
            
            elem_t = instance.type

            if isinstance(elem_t, jvm.Char):
                value = jvm.Value.int(ord(value.value))
            elif isinstance(elem_t, jvm.Int):
                value = jvm.Value.int(value.value)
            else:
                value = value  # reference or other type; accept as-is
            
            instance.data[index] = value
            frame.pc += 1
            return state  

        case jvm.NewArray( type=elem_type, dim=dims):
            # Array length is on the stack):
            length = frame.stack.pop()
            if length.value < 0:
                return "negative array size"
            arr_type = elem_type # jvm.Array(elem_type)
            arr_obj = _ArrayObj(arr_type, length.value)
            idx = len(state.heap)
            state.heap[idx] = arr_obj

            frame.stack.push(jvm.Value.ref(idx))
            
            #frame.stack.push(arr_obj) # a Value of array type
            frame.pc += 1
            return state
        

        case jvm.InvokeVirtual(method=m):
            # Class and method metadata
            cls = m.classname.slashed()       # e.g. "java/lang/String"
            name = m.methodid.name           # e.g. "equals", "length"
            param_types = list(m.extension.params or [])
            ret_type = m.extension.return_type

            # Pop arguments first (reverse order), then reverse to normal order
            args = [frame.stack.pop() for _ in range(len(param_types))][::-1]

            # Pop receiver (this)
            objref = frame.stack.pop()

            # --- NULL RECEIVER CHECK ---
            if objref.value is None:
                return "null pointer"

            oid = objref.value
            obj = state.heap.get(oid)

            # ---------- java/lang/String methods ----------
            if cls == "java/lang/String":
                # All our string objects have a "value" field with the Python str
                text = obj["fields"]["value"]

                # 1) equals(Object) : boolean
                if name == "equals":
                    arg_ref = args[0]
                    if arg_ref.value is None:
                        # "hello".equals(null) -> false
                        frame.stack.push(jvm.Value.int(0))
                    else:
                        arg_obj = state.heap.get(arg_ref.value)
                        arg_text = arg_obj["fields"]["value"]
                        result = (text == arg_text)
                        frame.stack.push(jvm.Value.int(1 if result else 0))
                    frame.pc += 1
                    return state

                # 2) length() : int
                if name == "length" and len(args) == 0:
                    frame.stack.push(jvm.Value.int(len(text)))
                    frame.pc += 1
                    return state

                # 3) charAt(int) : char
                if name == "charAt" and len(args) == 1:
                    index = args[0].value
                    if index < 0 or index >= len(text):
                        # matches JVM's StringIndexOutOfBoundsException
                        return "out of bounds"
                    ch = text[index]
                    frame.stack.push(jvm.Value.int(ord(ch)))
                    frame.pc += 1
                    return state

                # 4) concat(String) : String
                if name == "concat" and len(args) == 1:
                    arg_ref = args[0]
                    if arg_ref.value is None:
                        # Java's String.concat throws NPE on null argument
                        return "null pointer"
                    arg_obj = state.heap.get(arg_ref.value)
                    arg_text = arg_obj["fields"]["value"]
                    new_text = text + arg_text
                    new_oid = len(state.heap)
                    state.heap[new_oid] = {
                        "class": obj["class"],
                        "fields": {"value": new_text},
                    }
                    frame.stack.push(jvm.Value.ref(new_oid))
                    frame.pc += 1
                    return state

                # 5) substring(int, int) : String
                if name == "substring" and len(args) == 2:
                    start = args[0].value
                    end   = args[1].value
                    # Java bounds: 0 <= start <= end <= length
                    if start < 0 or end < start or end > len(text):
                        return "out of bounds"
                    sub = text[start:end]
                    new_oid = len(state.heap)
                    state.heap[new_oid] = {
                        "class": obj["class"],
                        "fields": {"value": sub},
                    }
                    frame.stack.push(jvm.Value.ref(new_oid))
                    frame.pc += 1
                    return state

                # 6) contains(CharSequence) : boolean
                if name == "contains" and len(args) == 1:
                    arg_ref = args[0]
                    if arg_ref.value is None:
                        result = False
                    else:
                        arg_obj = state.heap.get(arg_ref.value)
                        arg_text = arg_obj["fields"]["value"]
                        result = (arg_text in text)
                    frame.stack.push(jvm.Value.int(1 if result else 0))
                    frame.pc += 1
                    return state

                # 7) startsWith(String) : boolean
                if name == "startsWith" and len(args) == 1:
                    arg_ref = args[0]
                    if arg_ref.value is None:
                        result = False
                    else:
                        arg_obj = state.heap.get(arg_ref.value)
                        arg_text = arg_obj["fields"]["value"]
                        result = text.startswith(arg_text)
                    frame.stack.push(jvm.Value.int(1 if result else 0))
                    frame.pc += 1
                    return state

                # 8) endsWith(String) : boolean
                if name == "endsWith" and len(args) == 1:
                    arg_ref = args[0]
                    if arg_ref.value is None:
                        result = False
                    else:
                        arg_obj = state.heap.get(arg_ref.value)
                        arg_text = arg_obj["fields"]["value"]
                        result = text.endswith(arg_text)
                    frame.stack.push(jvm.Value.int(1 if result else 0))
                    frame.pc += 1
                    return state

                # Any other String method not yet modeled
                raise NotImplementedError(
                    f"InvokeVirtual: unhandled java/lang/String method {name}{param_types}->{ret_type}"
                )

            # ---------- fallback for other classes ----------
            raise NotImplementedError(
                f"InvokeVirtual not implemented for {cls}.{name}{param_types}->{ret_type}"
            )

        case jvm.InvokeSpecial(method=m, is_interface=_):
            #cls, name, arg_types, ret = _minfo(m)
            #arg_types = m.get("args", []) or []
            #args_list = list(getattr(m, "args", []) or [])
            args_list = m.extension.params._elements

            args = [frame.stack.pop() for _ in range(len(args_list))][::-1]
            index_objref = frame.stack.pop()  # consume the receiver
            obj = state.heap.get(index_objref.value)

            #raise NotImplementedError("InvokeSpecial not implemented yet" + "This is m:"+repr(m) + "This is obj:"+repr(obj) + "WTF:")# + obj.get("class"))
            if m.classname.name == "java/lang/Object" and m.methodid.name == "<init>":
                # Object.<init> is a no-op
                frame.pc += 1
                return state
            elif m.classname.name == "java/lang/AssertionError" and m.methodid.name == "<init>":  
                # AssertionError.<init> is a no-op
                frame.pc += 1
                return state
            elif m.classname.name == "java/lang/String" and m.methodid.name == "<init>":
                # String constructor: object is already allocated, we just ignore the body
                frame.pc += 1
                return state
            
            newframe = Frame.from_method(m)

            state.frames.push(newframe)
            
            return state
            #raise NotImplementedError("InvokeSpecial not implemented yet" + "This is m:"+repr(m) + "This is obj:"+repr(obj))

        case jvm.InvokeStatic(method=m):
            cls = m.classname.name          # e.g. "java/lang/Character"
            name = m.methodid.name          # e.g. "getNumericValue"
            param_types = list(m.extension.params or [])
            argc = len(param_types)

            # Pop args in reverse, then restore normal order
            args = [frame.stack.pop() for _ in range(argc)][::-1]

            # ---------- Handle java/lang/Character as "native" ----------
            # --- prevent trying to interpret java/lang/String from JSON ---
            
            if cls == "java/lang/String" and name == "valueOf":
                # For our purposes, we only need a simple conversion:
                # turn the first argument into a Python string.
                if args:
                    arg = args[0]
                    # Decode based on type
                    if isinstance(arg.type, jvm.Int):
                        s = str(arg.value)
                    elif isinstance(arg.type, jvm.Char):
                        s = arg.value
                    else:
                        # Fallback: just use Python's str(...)
                        s = str(arg.value)
                else:
                    # String.valueOf() with no args (unlikely here, but harmless)
                    s = ""

                # Allocate a new java/lang/String object in the heap
                oid = len(state.heap)
                state.heap[oid] = {
                    "class": jvm.String(),
                    "fields": {"value": s},
                }

                # Push a reference to this new String
                frame.stack.push(jvm.Value.ref(oid))
                frame.pc += 1
                return state
            
            elif cls == "java/lang/String":
                # If needed, you can implement a tiny subset like valueOf here.
                # For now, just fail explicitly instead of trying to load String.json:
                raise NotImplementedError(f"Static String method not supported: {name}")
            


            if cls == "java/lang/Character":
                # Most Character methods here take a single char/int argument
                code = args[0].value if args else None
                ch = chr(code) if code is not None else "\x00"

                # Character.getNumericValue(char)
                if name == "getNumericValue":
                    if "0" <= ch <= "9":
                        n = ord(ch) - ord("0")
                    else:
                        # good enough for our tests; real JVM has more cases
                        n = -1
                    frame.stack.push(jvm.Value.int(n))
                    frame.pc += 1
                    return state

                # Character.isDigit(char)
                if name == "isDigit":
                    frame.stack.push(jvm.Value.int(1 if ch.isdigit() else 0))
                    frame.pc += 1
                    return state

                # Character.isWhitespace(char)
                if name == "isWhitespace":
                    frame.stack.push(jvm.Value.int(1 if ch.isspace() else 0))
                    frame.pc += 1
                    return state

                # If some other Character method shows up, fail loudly so you see it
                raise NotImplementedError(
                    f"InvokeStatic for java/lang/Character.{name} not handled"
                )

            # ---------- Default: real static call (your existing behavior) ----------
            norm_args: list[jvm.Value] = []
            for t, v in zip(param_types, args):
                norm_args.append(v)  # no coercion for now

            callee = Frame.from_method(m)
            callee.locals = {i: v for i, v in enumerate(norm_args)}
            callee.pc = PC(callee.pc.method, callee.pc.offset)
            state.frames.push(callee)
            return state
                
        case jvm.Throw():

            index_objref = frame.stack.pop()  # consume the receiver
            obj = state.heap.get(index_objref.value)
            
            #raise NotImplementedError("Throw not implemented yet" + "This is obj:"+repr(obj))
            if obj is None:
                return "exception" 

            cls_val = obj["class"]
            cls_name = cls_val.name if hasattr(cls_val, "name") else str(cls_val)
            if "AssertionError" in cls_name:
                return "assertion error"
            return "exception"

        case jvm.New(classname=cls):
            #frame.stack.push(_ObjRef(str(cls)))
            heap = state.heap
            idx = len(heap)
            heap[idx] = {"class": cls, "fields": {}}
            frame.stack.push(jvm.Value.ref(idx))

            frame.pc += 1
            return state
  
        case jvm.Dup():
            v = frame.stack.peek()
            frame.stack.push(v)
            frame.pc += 1   
            return state
        
        case jvm.Incr(index=i, amount=d):
            v = frame.locals[i]
            assert v.type is jvm.Int(), f"expected int, but got {v}"
            v = jvm.Value.int(v.value + d)
            frame.locals[i] = v
            frame.pc += 1
            return state

        case jvm.Get(field=f):
            # Field metadata (objects, not dicts)
            fld = opr.field                  # AbsFieldID(classname=..., extension=FieldID(name=..., type=...))
            t   = fld.extension.type         # e.g., jvm.Boolean(), jvm.Int(), ...
            name = fld.extension.name
            cls  = getattr(fld, "classname", "")  # e.g., "jpamb/cases/Calls"

            # ---------- STATIC FIELD READ ----------
            if opr.static:
                if name == "$assertionsDisabled":
                    # Force 'assert' disabled for these classes (push boolean true == 0)
                    #if cls in ("jpamb/cases/Calls", "jpamb/cases/Arrays"):
                    frame.stack.push(jvm.Value.int(0 if ASSERTIONS_ENABLED else 1))
                    frame.pc += 1
                    return state

                    # Fallback: honor your global switch, if you want to keep it
                    # (Also: don't rely on identity equality for the type.)
                    #enabled = bool(globals().get("ASSERTIONS_ENABLED", True))
                
                frame.stack.push(jvm.Value.int(0))
                frame.pc += 1
                return state
            # Instance fields not implemented yet
            objref = frame.stack.pop()
            assert False, "Get instance field not implemented"

        case a:
            a.help()
            raise NotImplementedError(f"Don't know how to handle: {a!r}")

frame = Frame.from_method(methodid)
heap: dict[int, jvm.Value] = {}

for i, v in enumerate(input.values):
    logger.debug(v)
    match v.type:
        case jvm.Boolean():
             frame.locals[i] = jvm.Value.int(1 if v.value else 0)
        case jvm.Int():
             frame.locals[i] = v
        case jvm.Char():
            frame.locals[i] = jvm.Value.int(ord(v.value))
        case jvm.String():
            # Allocate java/lang/String object in heap
            obj = {
                "class": v.type,     # or a real class ID if available
                "fields": {"value": v.value}
            }
            oid = len(heap)
            heap[oid] = obj
            # Store a reference, not the raw String value
            frame.locals[i] = jvm.Value.ref(oid)

        case jvm.Array():
            # v.value might be chars ('a') or code points (97); normalize to chars
            logger.warning("Array input values not implemented ....value " + repr(v.value) + " at index " + repr(i) + " of " + repr(input.values))
            
            if(v.type.contains == jvm.Char()):
                logger.debug("Array of Char detected")
                elems = [jvm.Value.int(x) for x in (v.value or [])]     
                arr_inst = _ArrayObj(elem_type=jvm.Char(), length=len(elems))
                arr_inst.data = elems
                oid = len(heap)
                heap[oid] = arr_inst
                frame.locals[i] = jvm.Value.ref(oid)

            elif(v.type.contains == jvm.Int()):
                elems = [jvm.Value.int(x) for x in (v.value or [])]     
                arr_inst = _ArrayObj(elem_type=jvm.Int(), length=len(elems))
                arr_inst.data = elems
                oid = len(heap)
                heap[oid] = arr_inst
                frame.locals[i] = jvm.Value.ref(oid)
            else:
                logger.debug("Array of unknown type detected" + repr(v.type))
                assert False, "CRAP"
            
        case _:
            logger.warning(f"Unhandled input value type: {v.type} and value {v.value} at index {i} of {input.values}")
            assert False, repr(v)

state = State(heap, Stack.empty().push(frame))

for x in range(50000):
    state = step(state)
    if isinstance(state, str):
        print(state)
        break
else:
    print("*")
