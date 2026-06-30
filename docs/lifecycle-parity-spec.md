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
| Project analysis | Partial | File and mutant discovery exist, but target/test project discovery is shallow. |
| In-memory mutation | Missing | Mutations are source/worktree rewrites, not an internal compiler-artifact model. |
| Compile mutated target | Partial | External build/check commands compile disk state. |
| Compile-error pruning loop | Weak | Compile/check failures become statuses instead of pruning and recompiling a valid mutation set. |
| Save mutated artifact | Missing | No first-class object/library/binary placement model. |
| Coverage analysis | Partial | Supplied/helper/provider coverage exists, but coverage is not a normal engine phase. |
| Test sessions | Implemented | Per-mutant execution is the strongest match. |
| Multi-mutant sessions | Partial | Opt-in batching exists for isolated worktrees, but it is conservative and source-level. |
| Restore original artifacts | Source-level only | Source/worktree restoration exists; artifact restoration does not. |
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

The current `build_adapters` module is command synthesis, not project analysis.

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

Status: implemented for descriptive report metadata and common fixture
detection; deeper graph ownership remains future work.

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

Status: implemented for source-overlay materialization; compiled artifact
replacement remains future work.

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
