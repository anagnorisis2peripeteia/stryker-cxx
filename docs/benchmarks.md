# Benchmarks

## Reproducible mutant-generation benchmark

`npm run bench` runs `stryker-cxx list-mutants` over the committed fixture
`fixtures/benchmark/sample.cpp` (a spread of mutable C++ constructs — arithmetic, relational,
logical, bitwise, unary, literals, calls, loops, control flow) at `--mutation-level Complete`,
times it, and checks the **deterministic** mutant metrics (total + per-mutator counts) against
`fixtures/benchmark/baseline.json`.

- A drift from the baseline **fails** the benchmark (and the `tests/benchmark.test.mjs` guard),
  so a change that silently alters mutant generation is caught.
- Timing is informational (machine-specific), not asserted.
- To refresh the baseline after an intended mutant-generation change, re-run
  `stryker-cxx list-mutants --repo fixtures/benchmark --files sample.cpp --mutation-level Complete
  --format json` and update `fixtures/benchmark/baseline.json`.

Current baseline: **83 mutants across 19 mutators**.

## Head-to-head vs mull

[mull](https://mull.readthedocs.io/) is the closest C/C++ peer (LLVM-IR mutation). Two comparisons
are wired up; the first runs out of the box, the second is a captured full-fixture study.

### 1. `compare:mull` harness (runs with no mull install)

```bash
npm run compare:mull                                   # real head-to-head using the committed golden report
MULL_REPORT=<mull mutation-testing-elements.json> npm run compare:mull   # compare your own capture instead
```

A **real captured mull 0.34.0 (LLVM 14) report** of the harness fixture is committed at
`fixtures/benchmark/mull-report.json`, so `npm run compare:mull` is a genuine head-to-head out of
the box — no mull install required. `MULL_REPORT` overrides it with your own capture. The harness
(`scripts/compare-mull-parity.mjs`) normalizes both sides to Mutation Testing Elements counts and
records backend, source-precision, and build-graph parity evidence beside them — it treats mull as
benchmark/interoperability evidence, not as proof that stryker-cxx has mull's LLVM-IR backend (see
`docs/mull-stryker-net-roadmap.md`).

### 2. Full-fixture operator study (captured 2026-07-09)

Both tools were run over the same `fixtures/benchmark/sample.cpp` (mull 0.34.0 / LLVM 14.0.0,
`clang++-14`). mull is an *execution* tool that needs a passing baseline test, so its driver `main()`
was rewritten to exercise the four functions and return 0; the four functions under test
(`classify`, `blend`, `contains`, `label`) are **byte-identical** across both runs.

| | stryker-cxx | mull 0.34.0 |
|---|---|---|
| **Total mutants** | **83** | **44** |
| **Distinct operators** | 19 | 20 |
| Mode | static AST/token generation (`list-mutants`) | LLVM-IR, covered/reachable only |

Operator-family coverage on this fixture (mutant counts):

| Family | stryker-cxx | mull |
|---|---|---|
| Arithmetic (binary) | 8 | 7 (`sub_to_add`, `mul_to_div`, `div_to_mul`, `rem_to_div`) |
| Compound-assignment | 6 | 6 (`add_assign_to_sub_assign`, `sub_assign_to_add_assign`) |
| Relational / boundary | 14 (ConditionalBoundary 7 + ConditionalExpression 7) | 14 (directional `ge/gt/le/lt` swaps) |
| Equality | 7 | 3 (`eq_to_ne`) |
| Bitwise / shift | 2 | 1 (`lshift_to_rshift`) |
| Increment / decrement | 2 | 2 (`pre_inc_to_pre_dec`) |
| Constant / call-return replacement | — | 11 (`init_const` 5, `assign_const` 1, `replace_scalar_call` 5) |
| **Literals (int 17, float 5, bool 2, string 4)** | **28** | 0 |
| Statement removal | 6 | 0 |
| Return-value | 2 | 0 |
| Logical | 2 | 0 |
| Loop boundary / condition | 4 | 0 |
| Member access | 1 | 0 |
| Container-state call | 1 | 0 |

**Reading:** the two taxonomies overlap on arithmetic/assignment/relational/equality/shift/increment
but are **not 1:1**. stryker-cxx is broader on *source-level* constructs — literals, strings,
statement removal, loop and logical operators, member access: **~43 mutants here have no mull
equivalent**. mull is finer on *directional relational* swaps (each `<` yields both `<=` and `>=`)
and adds IR-level constant / call-return replacement that stryker-cxx does not emit identically.
Neither tool is a strict superset of the other; stryker-cxx generates ~1.9× more mutants on this
fixture, concentrated in the source-level families mull's IR model omits.

Reproduce the mull side (on a Linux box with mull 0.34 + LLVM 14):

```bash
# mull.yml in cwd:  mutators:\n  - cxx_all
clang++-14 -fpass-plugin=/usr/lib/mull-ir-frontend-14 -g -grecord-command-line sample.cpp -o sample
mull-runner-14 ./sample --reporters Elements --report-dir . --report-name mull-report
```
