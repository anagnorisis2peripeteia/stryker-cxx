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

### ReturnValue

Rewrites boolean returns:

- `return true` -> `return false`
- `return false` -> `return true`

Use it for predicate functions and status helpers. Noise is low for predicates
with direct tests, but generated stubs and defensive defaults can be equivalent
for a local test scope. Clang mode confirms the mutation under a `RETURN_STMT`.

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

## Gate guidance

Use the default mutator set for fast PR gates:

- `ConditionalBoundary`
- `EqualityOperator`
- `LogicalOperator`
- `BooleanLiteral`

Add the remaining mutators for focused risk areas or nightly runs. For noisy
legacy code, prefer `--lines`, `--base`, and narrow `--mutators` over globally
lowering thresholds.
