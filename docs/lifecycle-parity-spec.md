# Stryker lifecycle parity spec

This document translates Stryker maintainer feedback into the next architecture
target for `stryker-cxx`.

The current tool is a useful source-level C/C++ mutation runner for PR gates.
Full Stryker-family parity requires a deeper engine lifecycle that resembles
Stryker.NET: analyze projects, build mutated artifacts, prune invalid mutants,
map tests through coverage, run optimized sessions, restore original artifacts,
and then report.

## Reference lifecycle

The target lifecycle is:

1. Initialize and validate parameters.
2. Analyze projects and discover target and test projects.
3. Mutate targets into an internal mutation representation.
4. Compile the mutated target representation.
5. Remove mutations that cause compilation errors and repeat compilation until
   the mutated target compiles.
6. Place the compiled mutated artifact where the test runner can load it.
7. Perform coverage analysis to map mutants to the tests that can kill them.
8. Run test sessions. The simplest mode runs one session per mutant; optimized
   mode runs multiple compatible mutations in one session and splits when
   attribution is needed.
9. Restore original artifacts.
10. Generate reports.

## Current fit

| Lifecycle step | `stryker-cxx` state | Gap |
| --- | --- | --- |
| Initialization | Mostly implemented | Good enough for current CLI/config surface. |
| Project analysis | Implemented, still widening | Compile databases, CMake/CTest target ownership, explicit commands, test metadata, and best-effort Ninja/Make/Meson/Bazel/Xcode source/test ownership are reported with deterministic analysis keys; broad real-world non-CMake graph ownership remains best-effort. |
| In-memory mutation | Partial C++ equivalent | Source-overlay remains the compatibility path; compiled-artifact backends mutate scratch source/artifacts and mutant-switch injects guarded expression-like mutants. |
| Compile mutated target | Implemented for supported backends | External build/check commands compile source-overlay or guarded overlays; CMake/CTest compiled-artifact backends build scratch artifacts. |
| Compile-error pruning loop | Implemented for optimized artifacts | Mutant-switch and compiled-artifact sessions record pruning attempts, pruned mutants, retries, and retry batches. |
| Save mutated artifact | Implemented for CMake/CTest backends, simple Make/Ninja/Meson/Bazel/Xcode executables, explicit Make/Ninja/Meson libraries, explicit-path Bazel/Xcode libraries, and explicit-path Make/Ninja/Meson/Bazel objects with compile databases | `compiled-executable`, `compiled-library`, and `compiled-object` place/swap artifacts and record hashes; Xcode object ownership still preflight-fallbacks. |
| Coverage analysis | Implemented, still provider-led | Supplied/helper/provider coverage is a normal report phase with coverage-aware scheduling metadata. |
| Test sessions | Implemented | Per-mutant execution is the strongest match. |
| Multi-mutant sessions | Implemented with conservative placement locking | Opt-in batching exists for isolated worktrees and compiled artifacts. Compiled artifact batches can build/check in parallel scratch workers, with artifact placement and tests serialized per original artifact. |
| Restore original artifacts | Implemented for supported backends | Source/worktree restoration and compiled artifact restore-by-hash are recorded; broader build-system artifact restoration remains future work. |
| Reports | Implemented | Native report plus MTE projection are in good shape. |

## Core architectural gaps

### Project analysis phase

`stryker-cxx` needs a first-class project analysis module before mutation
execution starts. For C/C++, this means:

- discover build systems, compile databases, build targets, and test targets;
- identify source targets and test targets separately;
- map source files to build targets where possible;
- map test executables/framework invocations to the target artifacts they test;
- emit analysis metadata into `stryker-cxx.report.v1`.

The current `project_analysis` module now reports compile database ownership,
CMake inline target and `target_sources(...)` source ownership, CTest test
metadata, explicit command overrides, best-effort Ninja/Make/Meson
executable/library, recursive Bazel package source ownership with `cc_test`
dependency-to-target relationships, and Xcode source/test ownership with unit
test dependency-to-target relationships for parseable fixture-style metadata,
deterministic analysis/ownership keys, and confidence levels. The remaining gap
is broad build-graph ownership for real-world Ninja, Make, Meson, Bazel, and
Xcode projects beyond their command adapters and simple declarative metadata.

### Mutation artifact model

Stryker.NET mutates an internal representation and compiles a mutated assembly.
The C/C++ equivalent needs an explicit mutation artifact model.

Near-term acceptable C++ variants:

- source overlay directory or virtual filesystem overlay;
- object-file/library replacement under an isolated build directory;
- compiler wrapper that rewrites source during compile;
- LLVM/Clang IR or AST rewrite artifact for future advanced modes.

The current source/worktree rewrite path can remain the compatibility baseline,
but it should sit behind a mutation artifact interface instead of being the
engine's only model.

### Compile-pruning loop

The engine should distinguish compile-error discovery from final mutant status.
For multi-mutant execution, compile failures should prune invalid mutants from a
candidate artifact and recompile until the artifact is valid or no candidates
remain.

Required behavior:

- identify compile/check failures for a candidate mutation set;
- attribute the failure to one or more mutants when possible;
- remove failing mutants from the candidate artifact;
- retry compilation with the remaining mutation set;
- record pruned mutants separately from test-killed/survived mutants;
- project pruned mutants to compatible report statuses without losing native
  reason metadata.

### Coverage as a normal phase

Coverage should become a standard phase between artifact creation and test
execution, even when no provider is configured.

Required behavior:

- record whether coverage was unavailable, supplied, helper-generated, or
  discovered automatically;
- map mutants to covering tests when possible;
- mark no-coverage mutants before test execution;
- select targeted test sessions from coverage data;
- preserve fallback behavior when coverage is absent.

### Test-session scheduler

Per-mutant execution should remain the baseline scheduler. A deeper scheduler
should plan sessions over mutation artifacts.

Required behavior:

- run one session per mutant when no batching is enabled;
- group compatible mutants for optimized sessions;
- split failed sessions for attribution;
- keep deterministic output order;
- work with coverage-selected tests;
- avoid in-place parallel mutation.

### Artifact restoration

Restoration should cover the artifact level, not only source text.

Required behavior:

- restore or replace original object/library/binary artifacts after test
  sessions;
- clean temporary overlay/build products unless retention is requested;
- preserve debuggable retained artifacts for survivor/timeout proof;
- record restoration policy and retained paths in the report.

## Action plan

### Phase 1: lifecycle documentation and contract metadata

Status: implemented for additive report metadata.

Implementation work:

- add report fields for lifecycle phase metadata without changing existing
  required fields;
- document lifecycle states in `docs/contract.md`;
- add tests proving legacy reports still validate when lifecycle metadata is
  absent.

Acceptance criteria:

- `validate_report` accepts old reports and reports with lifecycle metadata;
- `docs/spec.md` links to this lifecycle parity spec;
- `npm test`, `npm run lint`, and `npm pack --dry-run` pass.

### Phase 2: project analysis module

Status: implemented for descriptive report metadata, common fixture detection,
deterministic analysis keys, and parseable Ninja/Make/Meson/Bazel/Xcode
source/test ownership; deeper real-world graph ownership remains future work.

Implementation work:

- add `python/stryker_cxx/project_analysis.py`;
- move compile database, build-target, and test-target discovery into it;
- make CLI/config initialization call analysis before mutation execution;
- report discovered targets, test targets, and confidence levels.

Acceptance criteria:

- existing explicit `--files`, `--build-command`, and `--test-command` flows keep
  working;
- CMake/CTest and compile database fixtures produce analysis metadata;
- unknown projects degrade to explicit user-supplied commands.

### Phase 3: mutation artifact interface

Status: implemented for source-overlay materialization, mutant-switch guarded
artifacts, and supported compiled artifact replacement.

Implementation work:

- add a mutation artifact module that can materialize source overlays and later
  object/library replacements;
- move in-place/copy/git-worktree source mutation behind that interface;
- keep current behavior as the default source-overlay implementation.

Acceptance criteria:

- `run`, `run-mutant`, batching, and retained worktrees behave as before;
- reports identify the artifact mode used;
- source restoration tests still pass.

### Phase 4: compile-pruning loop

Status: implemented for source-overlay runs and batched prune-and-retry.

Implementation work:

- build candidate mutation sets through the artifact interface;
- compile/check the candidate artifact;
- prune compile-error mutants and retry;
- record pruned mutants with native reason metadata.

Acceptance criteria:

- compile-failing mutants are separated from test-executed mutants;
- batch compile failures can be split and attributed;
- MTE projection remains compatible while native report preserves detail.

### Phase 5: coverage phase and scheduler

Status: implemented for source-overlay runs, per-mutant sessions, and batched
coverage-selected sessions.

Implementation work:

- make coverage loading/discovery a named lifecycle phase;
- feed coverage-selected tests into both per-mutant and batched sessions;
- add scheduler metadata for session grouping and split attribution.

Acceptance criteria:

- no-coverage, covered, and unknown-coverage mutants are distinguishable in the
  native report;
- per-mutant fallback works with no coverage;
- batched sessions remain deterministic and split on failure.

### Phase 6: artifact placement and restoration

Status: implemented for source-overlay restoration, mutant-switch artifacts,
retained proof artifacts, and supported compiled artifact placement/restoration.

Implementation work:

- add artifact placement policies for source overlays and compiled outputs;
- restore original artifacts after sessions;
- retain selected artifacts for survivor/timeout proof.

Acceptance criteria:

- original build outputs are restored or replaced after mutation runs;
- retained artifacts are opt-in and reported;
- cleanup is deterministic across copy and git-worktree modes.

## Non-goals for first lifecycle parity

- perfect equivalent-mutant detection;
- whole-program LLVM instrumentation as a required default;
- automatic support for every C++ build system;
- replacing current source-level mode;
- hiding native C++ compile/check failure detail behind only MTE statuses.

## Open design questions

- Should the first artifact implementation be source overlays or compiler-wrapper
  rewriting?
- Should compile-pruned mutants count as `BUILD_ERROR`, `CHECK_ERROR`, or a new
  native lifecycle outcome projected to existing MTE statuses?
- How much project analysis should be automatic before requiring an explicit
  config file?
- Which coverage provider should become the default fixture: `llvm-cov`, CTest,
  or provider hooks only?
- Should optimized sessions be enabled only in isolated modes, or can a future
  artifact model make safe in-place scheduling possible?
