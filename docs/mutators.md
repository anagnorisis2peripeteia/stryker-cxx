# stryker-cxx mutators

This document describes the production mutators, their intended signal, and the
main noise risks to account for in PR-gate use.

## Defaults

### ConditionalBoundary

Rewrites boundary comparisons:

- `<=` -> `<`
- `>=` -> `>`
- `<` -> `<=`
- `>` -> `>=`

Use it for off-by-one and inclusive/exclusive boundary mistakes. Noise is
usually low in ordinary control flow, but template-heavy C++ can contain angle
brackets that look like comparisons; token mode only mutates bare `<` and `>`
with surrounding whitespace to reduce that risk. Clang mode requires a
`BINARY_OPERATOR` cursor.

### EqualityOperator

Rewrites equality checks:

- `==` -> `!=`
- `!=` -> `==`

Use it for assertion strength and branch-condition coverage. Noise is low, but
mutants inside generated comparison helpers may be equivalent; suppress those
with `// Stryker disable next-line EqualityOperator: reason`.

### LogicalOperator

Rewrites boolean short-circuit operators:

- `&&` -> `||`
- `||` -> `&&`

Use it for boolean guard coverage. Noise is moderate when defensive guards are
intentionally redundant or when short-circuit side effects are avoided by
construction. Prefer targeted ignore comments over disabling the mutator.

### ShiftOperator

Rewrites bit-shift operators:

- `<<` -> `>>`
- `>>` -> `<<`
- `<<=` -> `>>=`
- `>>=` -> `<<=`

Use it for low-level and geometry-like code where directional shift behavior is
critical. Noise is modest in packed flag code and bitfield manipulation where
over- or under-shifting can be equivalent in the local fixture.
Clang mode confirms these using `BINARY_OPERATOR` cursor context.

### BooleanLiteral

Swaps boolean literals:

- `true` -> `false`
- `false` -> `true`

Use it for feature flags, defaults, and branch toggles. Noise is moderate in
constant-returning stubs, generated code, and compile-time feature switches.
Clang mode confirms the literal with a boolean literal expression cursor.

## Additional mutators

### ArithmeticOperator

Rewrites arithmetic operators:

- `+` -> `-`
- `-` -> `+`
- `*` -> `/`
- `/` -> `*`

Use it for numeric logic, dimensions, offsets, and scoring formulas. Noise is
higher than default mutators because some numeric identities and saturation
paths can be equivalent for a narrow fixture. Use scoped runs or explicit
ignore comments for generated arithmetic.

### AssignmentOperator

Rewrites compound assignment:

- `+=` -> `-=`
- `-=` -> `+=`
- `*=` -> `/=`
- `/=` -> `*=`

Use it for accumulation and state-update coverage. Noise is low when tests
assert final state, but higher for dead stores and telemetry counters.

### BitwiseOperator

Rewrites bitwise operators:

- `&` -> `|`
- `|` -> `&`
- `^` -> `|`

Use it for flags, masks, and low-level code. Noise is moderate because masks can
be redundant for known constants. Clang mode confirms these as binary
operators.

### UnaryOperator

Rewrites logical negation:

- `!expr` -> `expr`
- `!expr` -> `!!expr`

Use it for negated guards and boolean normalization. Noise is moderate around
already-normalized booleans where `!!expr` can be equivalent.

### UpdateOperator

Rewrites increment and decrement:

- `++` -> `--`
- `--` -> `++`

Use it for loop and counter mutation scenarios. Noise is modest where loop
index direction is intentional and side effects are tightly scoped. This is
best run with focused line or file scopes.
Clang mode confirms these as `UNARY_OPERATOR` cursor contexts.

### ReturnValue

Rewrites boolean returns:

- `return true` -> `return false`
- `return false` -> `return true`

Use it for predicate functions and status helpers. Noise is low for predicates
with direct tests, but generated stubs and defensive defaults can be equivalent
for a local test scope. Clang mode confirms the mutation under a `RETURN_STMT`;
`clang-ast` mode can also use the return statement source range directly for
single-line parenthesized boolean returns such as `return (true)`.

### ConditionalExpression

Rewrites ternary expressions:

- `cond ? a : b` -> `cond ? b : a`

Use it for branch logic and feature-flag behavior. It is opt-in and can be noisy
in condition-heavy expressions where ternaries are stylistic.
Clang mode confirms these as `CONDITIONAL_OPERATOR` cursor contexts.
`clang-ast` mode can also rewrite supported single-line conditional spans directly.

### IntegerLiteral

Rewrites basic integer literals:

- `0` -> `1`
- `1` -> `0`

Use it for sentinel values, boundary defaults, and simple branch fixtures. It is
opt-in rather than default because numeric literal mutation is noisy in
generated code, formatting constants, array sizes, and test scaffolding.
In `clang` mode, integer replacements are confirmed by `INTEGER_LITERAL`
cursor context. In `clang-ast`, the mutation is applied directly from supported
single-line cursor spans.

### FloatingPointLiteral

Rewrites basic floating-point literals:

- `0.0` -> `1.0`
- `1.2` -> `0.0`

Use it for numeric edge cases, threshold checks, and geometry/physics style
logic. It is opt-in because many floating constants are calibration data.
In `clang` mode, floating replacements are confirmed by `FLOATING_LITERAL`
or `CXX_FLOATING_LITERAL` cursor context. In `clang-ast`, matching cursor
spans get direct single-line literal replacement.

### NullLiteral

Rewrites null pointer literals:

- `nullptr` -> `NULL`
- `NULL` -> `nullptr`

Use it for old/new C++ nullability interop and platform code that still carries
`NULL`. It is opt-in because many projects treat these forms as equivalent
style choices rather than behavioral differences.
In `clang` mode, null rewrites are limited to null-pointer and null-expression
AST cursor contexts (`CXX_NULL_PTR_LITERAL_EXPR`, `GNU_NULL_EXPR`, and
`DECL_REF_EXPR`). In `clang-ast`, matching cursor spans get direct single-line
literal replacement.

### CharacterLiteral

Rewrites basic character literals:

- `'a'` -> `'x'`

Use it for status flags, parser edge cases, and low-width dispatch constants.
It is opt-in because changing literal text can alter binary protocols and
character-sensitive behavior.
In `clang` mode, character replacements are confirmed by
`CHARACTER_LITERAL`, `CXX_CHAR_LITERAL`, or `OBJC_CHAR_LITERAL` cursor
context. In `clang-ast`, matching cursor spans get direct single-line literal
replacement.

### StringLiteral

Rewrites basic string literals:

- `"foo"` -> `""`
- `""` -> `"x"`

Use it for localized default messages, format strings, and basic payloads. It is
opt-in because broad message text can be semantically significant even when the
program still compiles.
In `clang` mode, string replacements are confirmed by
`STRING_LITERAL`, `CXX_STRING_LITERAL`, or `OBJC_STRING_LITERAL` cursor
context. In `clang-ast`, matching cursor spans get direct single-line literal
replacement.

### StatementRemoval

Rewrites a single statement into a no-op statement:

- `int x = 1;` -> `;`

Use it for aggressive branch and assignment coverage where the absence of a
statement can expose hidden dependencies and side effects. This is higher-noise
than operator-level mutators; enable it intentionally for targeted lines and
mutator-aware triage.

### BlockRemoval

Rewrites a single-line compound statement with an empty block:

- `{ int x = 1; }` -> `{}`

Use it for control-flow-shape checks in small, tightly-scoped blocks. This mutator
is intentionally constrained to safe single-line blocks to avoid brittle cross-line
text edits.

### LoopBoundary

Rewrites loop boundary operators in loop conditions:

- `i < 10` -> `i <= 10` in `for/while/do-while`
- `i >= 10` -> `i > 10` in `for/while/do-while`

Use it for off-by-one and termination-logic mistakes where loop continuation
conditions are sensitive to inclusive/exclusive boundaries. Noise is usually low in
simple loops, but mutation scope is intentionally constrained to loop header
conditions to reduce over-generation in increment/update expressions.

### LoopCondition

Rewrites loop conditions by negating the loop predicate:

- `while (i < 10)` -> `while (!(i < 10))`
- `for ( ; i < 10; ++i)` -> `for ( ; !(i < 10); ++i)`

Use it for termination and progress-control fault injection where loop entry/exit
changes are likely to surface hidden state and control-flow assumptions. Noise is
higher in defensive loops and intentionally redundant guards.

### StandardLibraryCall

Rewrites selected standard-library call targets with same-shape alternatives:

- `std::min` -> `std::max`
- `std::max` -> `std::min`
- `std::all_of` -> `std::any_of`
- `std::any_of` -> `std::all_of`
- `std::none_of` -> `std::any_of`
- `std::equal` -> `std::mismatch`
- `std::mismatch` -> `std::equal`
- `std::lower_bound` -> `std::upper_bound`
- `std::upper_bound` -> `std::lower_bound`
- `std::begin` -> `std::end`
- `std::end` -> `std::begin`
- `std::cbegin` -> `std::cend`
- `std::cend` -> `std::cbegin`
- `std::sort` -> `std::stable_sort`
- `std::stable_sort` -> `std::sort`
- `std::partition` -> `std::stable_partition`
- `std::stable_partition` -> `std::partition`
- `std::is_sorted` -> `std::is_heap`
- `std::is_heap` -> `std::is_sorted`

Use it for algorithm-choice, boundary aggregation, iterator-range, and
lower/upper-bound, ordering, partitioning, and sortedness/heap predicate faults.
It is opt-in because template overloads, ADL-sensitive code, and algorithm
return-type differences can produce compile errors or equivalent mutants in
narrow fixtures.

### MemoryOrder

Rewrites selected C++ atomic memory-order constants with same-type alternatives:

- `std::memory_order_relaxed` -> `std::memory_order_seq_cst`
- `std::memory_order_seq_cst` -> `std::memory_order_relaxed`
- `std::memory_order_acquire` -> `std::memory_order_relaxed`
- `std::memory_order_release` -> `std::memory_order_relaxed`
- `std::memory_order_acq_rel` -> `std::memory_order_seq_cst`
- `std::memory_order_consume` -> `std::memory_order_acquire`
- `std::memory_order::relaxed` -> `std::memory_order::seq_cst`
- `std::memory_order::seq_cst` -> `std::memory_order::relaxed`
- `std::memory_order::acquire` -> `std::memory_order::relaxed`
- `std::memory_order::release` -> `std::memory_order::relaxed`
- `std::memory_order::acq_rel` -> `std::memory_order::seq_cst`
- `std::memory_order::consume` -> `std::memory_order::acquire`

Use it for C++ concurrency code where ordering strength and acquire/release
semantics are part of the behavior under test. It is opt-in because weakly
ordered behavior often needs stress, sanitizer, or model-checking style tests to
observe a failure reliably.

In clang-backed modes, `MemoryOrder` candidates are confirmed against enum
reference/member-reference cursor spans before execution.

### MemberAccessOperator

Rewrites member-access operators:

- `object.member` -> `object->member`
- `ptr->member` -> `ptr.member`
- `object.*member` -> `object->*member`
- `ptr->*member` -> `ptr.*member`

Use it for pointer/value ownership and API-contract checks. It is intentionally
compile-error friendly: incorrect ownership assumptions often surface in the
build/check phase rather than the test phase.

### ExceptionHandling

Rewrites a single-line throw statement into a no-op statement:

- `throw;` -> `(void)0;`
- `throw error;` -> `(void)0;`

Use it for error-path and exception-propagation coverage. It is constrained to
single-line throw statements and can be equivalent when the branch is unreachable
or when callers intentionally ignore failure paths.

### PreprocessorGuard

Rewrites simple preprocessor guards:

- `#ifdef NAME` -> `#ifndef NAME`
- `#ifndef NAME` -> `#ifdef NAME`
- `#if 1` -> `#if 0`
- `#if 0` -> `#if 1`

Use it for feature-flag and platform-guard coverage. Token and clang modes do not
try to evaluate arbitrary preprocessor expressions; complex `#if defined(...)`
logic should be covered through focused tests or explicit configuration variants.

### ObjCMessageSend

Rewrites a statement-level Objective-C message send into a no-op expression:

- `[object doWork];` -> `(void)0;`

Use it for Objective-C++ side-effect coverage around delegate calls, notifications,
and lifecycle hooks. The source-level implementation only removes simple
single-line message-send statements.

### ObjCBoolLiteral

Rewrites Objective-C boolean literals:

- `YES` -> `NO`
- `NO` -> `YES`

Use it for Objective-C++ feature gates, delegate decisions, and Cocoa-style
boolean branches. It is separate from `BooleanLiteral` so C++ `true`/`false`
defaults can stay conservative while Objective-C++ runs opt into `YES`/`NO`
coverage explicitly.

### MetalThreadPosition

Rewrites selected Metal shader thread-position attributes:

- `thread_position_in_grid` -> `thread_position_in_threadgroup`
- `thread_position_in_threadgroup` -> `thread_position_in_grid`
- `thread_index_in_threadgroup` -> `threads_per_threadgroup`
- `threads_per_threadgroup` -> `thread_index_in_threadgroup`

Use it for shader index-space and dispatch-shape coverage. It is opt-in because
some mutations intentionally produce compile errors when host/kernel contracts no
longer agree.

### MetalAddressSpace

Rewrites selected Metal address-space qualifiers in `.metal` sources:

- `device` -> `constant`
- `constant` -> `device`
- `threadgroup` -> `device`

Use it for host/kernel memory-contract faults and shader data-flow assumptions.
The matcher is intentionally scoped to qualifier-like tokens in Metal sources
and avoids threadgroup attribute calls such as `[[threadgroup(0)]]`.

### CallRemoval

Rewrites a statement-level function call:

- `side_effect();` -> `(void)0;`

Use it for side-effect coverage, notifications, logging hooks, and imperative
state changes. Noise is higher for intentionally optional logging or metrics
calls. The token implementation only removes simple statement-level calls where
the call expression is the whole statement; clang mode confirms a call-expression
cursor.

## Suppressing equivalent or intentionally noisy mutants

Use Stryker-style comments near the source:

- `// Stryker disable all: generated code`
- `// Stryker restore all`
- `// Stryker disable next-line EqualityOperator: equivalent guard`
- `// Stryker disable once ArithmeticOperator: saturated value`

Ignored mutants remain in reports as `IGNORED` / `Ignored`, carry the supplied
reason, do not run, and are excluded from the score.

`--equivalent-suppression conservative` is enabled by default. It automatically
marks high-confidence noisy mutants as ignored when a source file carries a
generated-code marker, when a logical or bitwise mutation touches duplicate pure
operands such as `flag && flag` or `flags & flags`, when an arithmetic mutation
changes an identity such as `x + 0` / `x - 0` or `x * 1` / `x / 1`, or when
`std::min(x, x)`/`std::max(x, x)` would only swap duplicate operands. The native
report records these under `execution.analysis.equivalentSuppression`. Use
`--equivalent-suppression off` for raw proof runs, or `aggressive` to also
suppress generated-looking paths and style-equivalent null literal rewrites.

## Gate guidance

Use the default mutator set for fast PR gates:

- `ConditionalBoundary`
- `EqualityOperator`
- `LogicalOperator`
- `BooleanLiteral`

Add the remaining mutators for focused risk areas or nightly runs. For noisy
legacy code, prefer `--lines`, `--base`, and narrow `--mutators` over globally
lowering thresholds.
