# Mull and Stryker.NET convergence roadmap

This roadmap tracks the seven-phase plan for making `stryker-cxx` closer to
Mull's C/C++ execution strengths and Stryker.NET's orchestration maturity.

Status: implemented and locally validated before the parity-audit extension;
current work adds explicit eight-gap report metadata and should be revalidated.

Validation evidence:

- `npm run validate:full-spec` in `stryker-cxx`;
- Marmorkrebs TypeScript check, lint, build, and test pipeline.

Boundary: `llvm-switch` is an experimental guarded-source switch backend. It
matches Mull's single-compile activation shape for supported source ranges, but
does not claim Mull's LLVM IR instrumentation internals.

## Phase 1: parity comparison harness

Status: implemented.

- `npm run compare:mull` creates a controlled C++ fixture, runs the local
  `stryker-cxx` binary, and writes `stryker-cxx.comparison.v1` to
  `agent_space/stryker-cxx/comparison/mull-parity.json`.
- If `MULL_REPORT=<mutation-testing-elements.json>` is supplied, the harness
  normalizes the Mull Mutation Testing Elements report and emits count deltas.
- If Mull is not installed or no report is supplied, the harness records Mull
  command capability and still proves the `stryker-cxx` side.

The harness is intentionally backend-neutral: it compares observable mutation
results and report contracts before claiming execution-engine equivalence.

## Phase 2: Mull-like execution backend

Target state:

- add an experimental `llvm-switch` or equivalent backend for CMake and
  compile-database projects;
- preserve `stryker-cxx.report.v1` regardless of backend;
- keep `source-overlay` as the compatibility fallback for ObjC++/Metal and
  build graphs that cannot prove LLVM artifact ownership.

Current implementation:

- `--execution-backend auto|source-overlay|mutant-switch|compiled-artifact|llvm-switch`
  is a first-class CLI/config/report contract;
- `mutant-switch` can be selected via backend spelling and still emits the
  existing single-compile guard metadata;
- `llvm-switch` is an experimental guarded-source switch implementation for
  compile-database or CMake/CTest-backed runs with guardable mutants; unsupported
  projects still report explicit fallback instead of claiming LLVM parity.
- native reports now include `parity` metadata that marks this as partial until a
  true LLVM IR/object instrumentation backend exists.

## Phase 3: build graph ownership

Target state:

- model source-to-object-to-linked-artifact ownership explicitly;
- use CMake file API and compile database evidence where available;
- reject unsupported Xcode/Bazel object ownership paths in preflight with
  actionable diagnostics.

Current implementation:

- compiled-artifact backends are explicit and report requested/actual artifact
  backend plus fallback reason;
- build/test adapter surfaces exist for CMake/CTest, Ninja, Make, Meson, Bazel,
  and Xcode-oriented command synthesis;
- `projectAnalysis.buildGraph` records source nodes, build/test target nodes,
  compile-database match/miss evidence, and ownership diagnostics;
- CMake File API codemodel replies under the selected build directory are used
  as high-confidence target/source/artifact ownership evidence when present;
- unsupported ownership paths remain fallback/preflight decisions rather than
  hidden source-overlay behavior.

Boundary beyond this phase:

- object-level ownership is now explicit report evidence, but still adapter/proof
  driven rather than a Mull-equivalent LLVM ownership graph.

## Phase 4: coverage and mixed-mutant scheduler

Target state:

- expose coverage modes equivalent to `off`, `all`, `perTest`, and
  `perTestInIsolation`;
- mix only compatible mutants in a session;
- split failing mixed sessions until attribution is deterministic.

Current implementation:

- `--coverage-analysis off|all|perTest|perTestInIsolation` is wired through
  CLI/config/reporting;
- `perTest` modes can select per-mutant commands from supplied coverage data;
- existing batch mode groups compatible mutants and splits failed batches for
  deterministic attribution.
- `execution.parity` records whether a run used coverage-selected scheduling or
  only the generic deterministic scheduler path.

## Phase 5: AST precision layer

Target state:

- deepen libclang AST-native candidate generation;
- reject macro-expanded or ambiguous rewrite spans with recorded diagnostics;
- prefer source-range certainty over token heuristics when available.

Current implementation:

- `--mode clang-ast` exposes the AST-native candidate path beside token mode;
- source-range and rewrite-strategy metadata are recorded in native reports;
- `execution.analysis.sourcePrecision` summarizes AST-direct, AST-confirmed,
  token-range, and token-only mutants, and each mutant carries a
  `sourcePrecision` tag;
- conservative macro/equivalent suppression records rejected mutants and ranges
  instead of silently dropping them.

Boundary beyond this phase:

- token mutators still carry much of the broad language coverage; AST-native
  mutators should become the preferred path for ambiguous C++ rewrites.

## Phase 6: reporter and dashboard maturity

Target state:

- keep native JSON, MTE, Markdown, SARIF, HTML, GitHub annotation, and dashboard
  exports in sync;
- make baseline comparison and threshold bands first-class in every report;
- preserve provenance for dashboard upload/export and CI proof bundles.

Current implementation:

- native JSON, Mutation Testing Elements, Markdown, SARIF, HTML, and GitHub
  annotation formats are contract-tested through the repo validation path;
- baseline cache commands and threshold bands are first-class CLI/report fields;
- `--since <ref>` is accepted as the Stryker.NET-style alias for `--base <ref>`;
- dashboard export now carries `analysis.sourcePrecision` and
  `projectAnalysis.buildGraph` summaries so CI artifacts preserve the same
  parity evidence as native JSON;
- dashboard export/upload metadata includes retry, retention, project, branch,
  commit, build URL, and auth-header provenance.
- `stryker-cxx parity-audit --report <path>` renders the native parity metadata
  as JSON or Markdown for CI and review bundles.

## Phase 7: Mull interop

Target state:

- import Mull Mutation Testing Elements reports into `stryker-cxx.comparison.v1`;
- document when Marmorkrebs should use `--tool mull` vs `--tool stryker-cxx`;
- use Mull as a benchmark and optional plain-C++ backend, not as a replacement
  for `stryker-cxx` provider semantics.

Current implementation:

- `npm run compare:mull` imports an optional Mull MTE report via `MULL_REPORT`
  and records normalized deltas beside the local `stryker-cxx` run;
- the comparison artifact records backend, source-precision, and build-graph
  parity evidence, not just status counts;
- Marmorkrebs documents `mull` as an optional C++-only path and keeps
  `stryker-cxx` as the canonical C++/ObjC++/Metal provider;
- the comparison contract treats Mull as benchmark/interoperability evidence,
  not as proof that `stryker-cxx` has Mull's LLVM backend.
