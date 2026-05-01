# For abstract interval interpreter 
1. Added StringAbs dataclass:
Holds an optional constant string (const)
And an interval for the string length (length: Interval)
Extended AVal to handle strings indirectly via heap objects:
('ref', heap_id) -> heap[heap_id]["fields"]["value"] = StringAbs(...)
Now, we push them as ('ref', oid) in the abstract interpreter too; 
the difference is that the heap object now carries abstract string info (StringAbs) instead of just the concrete Python string.
# How Interval abstraction works:
1. Abstracting concrete strings
For a concrete string s:
α(s) = [|s|, |s|]
Example:
"hello" -> [5,5]

2. Abstracting a set of strings
For a set S:
α(S) = [min(|s| for s∈S), max(|s| for s∈S)]
Example:
{"hi", "hello"} -> [2,5]

# When we join two abstractions you enlarge the length interval:
When joining s1 and s2:
If both share same constant -> keep it
Else constant becomes None
Length interval is joined pointwise:
join([2,2], [5,5]) = [2,5]
So:
"hi" -> StringAbs.const_str("hi") has length [2,2]
"hello" -> length [5,5]
join -> const=None, length=join([2,2],[5,5]) = [2,5]

# When we concat:
If both strings are constants:
"ab" + "xyz" -> "abxyz" with length [5,5]

If only intervals are known:
s1.length ∈ [1,4]
s2.length ∈ [3,8]
-> concat length ∈ [1+3, 4+8] = [4,12]

# For substring(i, j)
Given index intervals i ∈ [i_lo, i_hi], j ∈ [j_lo, j_hi],
the possible substring lengths lie in:

sub_lo = max(0, j_lo - i_hi) - The smallest possible length (best case: j small, i big)
sub_hi = max(0, j_hi - i_lo) - The largest possible length (worst case: j big, i small)

substring length ∈ [sub_lo, sub_hi]

Exact case:
"hello" (len [5,5])
i = [1,1], j = [4,4]

sub_lo = max(0, 4 - 1) = 3
sub_hi = 3
So the abstract length becomes:
sub_len = Interval(sub_lo, sub_hi) 
-> [3,3]

# Int abstraction

All arithmetic is performed using interval operations:

+ -> interval addition
- -> interval subtraction
* -> interval multiplication via bounding products :

If divisor interval contains 0 -> result = TOP
% ->  over-approximated symmetric range

# Branching:
If uses ivl_cmp -> returns "true", "false", "maybe"
"maybe" -> state is split into two (branching)

# Array abstraction
Each array cell stores an abstract value:
int/char arrays store ('int', some_interval)
reference arrays store ('ref', None) conservatively

ArrayLoad
If index interval is definitely out of bounds -> "out of bounds"
If index is maybe invalid -> still "out of bounds"
Otherwise -> return TOP element for int arrays

ArrayStore
If index is not exact [i,i] and may be invalid -> "out of bounds"
Otherwise update that index

# Mapping:
JVM Value Kind  ->	Abstract Representation
int/char/boolean -> Interval(lo, hi)
String -> StringAbs(const?, length_interval)
Reference -> ('ref', heap_id or None)
Arrays	-> _ArrayObj(elem_type, length, data[]) where each cell holds an AVal


# Extra explanation for Join:
1. Case 1 — Both constants identical
s1 = StringAbs(const="hello", length=[5,5])
s2 = StringAbs(const="hello", length=[5,5])
join(s1, s2)
→ const="hello", length=[5,5]
Why?
Both abstract states agree the string is definitely "hello".

2. Case 2 — Different concrete strings
s1 = StringAbs(const="hi", length=[2,2])
s2 = StringAbs(const="hello", length=[5,5])
join → const=None, length=[2,5]
Why?
We cannot keep a specific constant because "hi" ≠ "hello".
The abstraction must say: “could be multiple strings”, so const disappears.

3. Case 3 — One state has a constant, the other doesn’t
s1 = StringAbs(const="hi", length=[2,2])
s2 = StringAbs(const=None, length=[0,10])
join → const=None, length=[0,10]
Why?
Second string could be anything.
You must conservatively drop the constant.

4. Case 4 — Both unknown
s1 = StringAbs(const=None, length=[1,4])
s2 = StringAbs(const=None, length=[3,8])
join → const=None, length=[1,8]