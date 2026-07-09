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

## Head-to-head vs mull (opt-in)

mull is the closest C/C++ peer. The comparison is opt-in — mull is not required to run the
benchmark above:

```bash
npm run compare:mull                                   # stryker-cxx side + mull command capability
MULL_REPORT=<mull mutation-testing-elements.json> npm run compare:mull   # normalized count deltas
```

The harness (`scripts/compare-mull-parity.mjs`) records backend, source-precision, and build-graph
parity evidence beside the counts — it treats mull as benchmark/interoperability evidence, not as
proof that stryker-cxx has mull's LLVM-IR backend (see `docs/mull-stryker-net-roadmap.md`).
