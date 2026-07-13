# stryker-cxx contract

`stryker-cxx` is the standalone C++ mutation command. Its primary machine-readable
report is `stryker-cxx.report.v1`, and every native report embeds a
`mutation-testing-elements` (`schemaVersion: 2.0`) projection.

The JS adapter accepts both:

- direct MTE payloads, and
- full `stryker-cxx.report.v1` payloads that include a nested `mutationTestingElements`.

## Required fields

- `mutationTestingElements.schemaVersion = "2.0"` (inside the native wrapper, if using wrapper mode)
- `mutationTestingElements.files` map
- For each file, optional `source`
- For each mutant:
  - `id`
  - `mutatorName`
  - `description`
  - `original`
  - `replacement`
  - `status`
  - `statusReason`
  - `location.start.line`, `location.start.column`
  - `location.end.line`, `location.end.column`
  - optional `runCommand`, `nodeKind`

## Status set used by this adapter

- `Killed`
- `Survived`
- `NoCoverage`
- `Timeout`
- `Ignored`
- `Pending`
- `RuntimeError`

`Ignored` mutants remain visible in the MTE payload but are excluded from
adapter summary score calculation. A mutant can become ignored through
Stryker-style source comments or through native equivalent/noise suppression
recorded under `execution.analysis.equivalentSuppression` in the wrapper report.

## Lifecycle metadata

Native `stryker-cxx.report.v1` reports may include an optional `lifecycle`
object. The field is additive: historical reports without it remain valid.

## Execution mode metadata

Native reports include execution-mode metadata under `execution`:

- `execution.mode` is the legacy analysis-engine field (`token`, `clang`, or
  `clang-ast`) retained for compatibility;
- `execution.analysis.engine` is the normalized analysis-engine location for new
  consumers;
- `execution.executionMode` is the actual runner mode
  (`source-overlay` or `mutant-switch`);
- `execution.requestedExecutionMode` records the caller request when fallback is
  required;
- `execution.artifactBackend` is the actual mutation artifact backend used for
  the run;
- `execution.requestedArtifactBackend` records the caller-requested artifact
  backend when `--artifact-fallback source-overlay` downgrades an unsupported
  compiled-artifact request;
- `execution.artifactFallbackReason` records the unsupported compiled backend
  condition that caused an explicit source-overlay fallback;
- `execution.mutantSwitch` records whether switch execution was requested,
  whether it was enabled, the active-mutant environment variable, runtime guard
  count, and any fallback reason.
- `execution.mutantSwitch.guards[]` records deterministic candidate guard IDs
  for discovered mutants when mutant-switch execution is requested.
- `list-mutants` includes `mutantSwitchGuardId` so later `run` and
  `run-mutant` invocations can prove they selected the same candidate.
- Per-mutant `run.reproCommand` preserves available analysis mode,
  `--execution-mode`, compiled-artifact backend, build target, and non-default
  worktree mode so native reports can reproduce source-overlay, mutant-switch,
  and compiled-artifact runs from the recorded command.

`--execution-mode mutant-switch` currently builds one guarded source overlay for
safe expression-like mutants: boolean, integer, floating-point, character,
string, null, Objective-C boolean, and return-value replacements. It can also
guard supported single-line binary operator mutants by switching the whole
expression span, including conditional boundary, equality, logical, arithmetic,
assignment, bitwise, shift, and loop-boundary mutations. Token mode uses strict
operand-span detection for simple binary expressions; `clang-ast` mode uses AST
source ranges when available. Complex operator cases still fall back because an
operator token cannot be switched independently while preserving valid C++.
Overlapping guarded expression spans on the same line also fall back explicitly
instead of applying an ambiguous switch overlay.
Unsupported mutators, Metal sources, non-source-overlay artifact backends,
parallel jobs, or explicit source-overlay batching fall back to `source-overlay`
with `execution.mutantSwitch.fallbackReason` set. This keeps fallback explicit
rather than silently behaving like a source-overlay run.

Current lifecycle metadata uses `schemaVersion = "stryker-cxx.lifecycle.v1"` and
records:

- `artifactModel`, currently `source-level`;
- `phaseOrder`, the ordered lifecycle phase names;
- `phases[]`, each with `name`, `status`, and optional `detail`.

The current source-level runner emits these phases:

- `initialization`
- `projectAnalysis`
- `mutationDiscovery`
- `mutationArtifact`
- `compilePruning`
- `coverageAnalysis`
- `testScheduling`
- `artifactRestoration`
- `reporting`

`compilePruning` is reported as `completed` for the source-overlay runner.
Compile and checker failures still appear as native `BUILD_ERROR` and
`CHECK_ERROR` mutant statuses for compatibility, but native reports also record
which mutants were pruned before test execution under
`execution.compilePruning`.

## Coverage integrity and build-error policy

Native reports include two additive objects (reports without them remain valid):

- `execution.coverageIntegrity` — `mutantsIntended`, `builtAndScored`, `coveragePercent`, and
  `buildErrors.{total, reconstructionMiss, genuineUncompilable}`. The split always sums to
  `total`. `genuineUncompilable` is set only when a faithful per-mutant compile (source-overlay,
  compiled-artifact build of one applied mutant, or the compile-pruning probe) positively
  attributed the failure to the mutation; every other build error (configure failure, shared
  mutant-switch/batch build, missing `compile_commands.json` entry) is a `reconstructionMiss`.
- `execution.buildErrorPolicy` — `tolerateUncompilable` (boolean) and `maxBuildErrorRate`
  (number in `[0, 1]` or null), the policy that governed the exit decision. Strict by default
  (any build error fails); `--tolerate-uncompilable-mutants` tolerates `genuineUncompilable`
  errors but never a `reconstructionMiss`, and `--max-build-error-rate` caps the total rate.

## Project analysis metadata

Native reports may include an optional `projectAnalysis` object with
`schemaVersion = "stryker-cxx.project-analysis.v1"`. It records the project
analysis performed before mutation execution:

- `confidence`, currently `high`, `medium`, or `low`;
- `targetFiles`, the requested mutation source files;
- `buildSystems`, detected or explicit build-system signals;
- `compileDatabase`, whether `compile_commands.json` was found and loaded;
- `sourceTargets`, source file metadata, compile-database match state, owning
  build targets, source metadata origin, and best-effort source ownership
  metadata;
- `buildTargets`, discovered or explicit build targets, including best-effort
  source-file ownership for CMake inline target sources, CMake
  `target_sources(...)`, Ninja, Make, Meson executable/library, and Bazel
  root or nested package targets when `BUILD`/`BUILD.bazel` files expose source
  lists;
- `testTargets`, discovered or explicit test targets/frameworks/commands,
  including best-effort related build target metadata for CTest commands and
  Bazel `cc_test` dependencies, plus Xcode unit/UI-test target dependencies;
- `commands`, the resolved build/check/test commands.

This metadata is descriptive. It does not yet replace explicit build and test
commands, and unknown projects degrade to the user-supplied command flow.
Compiled artifact replacement is supported for CMake/CTest executable, library,
and object targets; for explicit Make/Ninja/Meson/Bazel/Xcode executable
targets; and for explicit Make/Ninja/Meson library targets with an existing
original `lib<target>` artifact; and for Bazel/Xcode library targets when
`--artifact-path` names the original library artifact. Make/Ninja/Meson/Bazel
`compiled-object` targets are supported when `--artifact-path` names the linked
artifact and the build emits `compile_commands.json` entries for the mutated
sources. Xcode object ownership remains unsupported until its build graph
adapter can prove the source-to-artifact relationship.

## Mutation artifact metadata

Native reports may include an optional `mutationArtifact` object with
`schemaVersion = "stryker-cxx.mutation-artifact.v1"`. It describes how mutants
were materialized for the run:

- `mode`, currently `source-overlay`;
- `implementation`, one of the existing workspace implementations
  (`inplace`, `copy`, or `git-worktree`);
- `workspacePerMutant`, whether each mutant gets an isolated workspace;
- `parallelSafe`, whether the materialization strategy is safe for parallel
  execution;
- `supportsCompiledReplacement`, currently `false` until compiled artifact
  placement exists;
- `sourceOverlay`, the strategy/restoration policy used by the source overlay;
- `retainArtifacts` and `retainArtifactsFor`, the configured survivor/timeout
  artifact-retention policy.

Per-mutant native run records may also include `run.mutationArtifact` with the
materialized workspace path and retained-artifact details. This is intentionally
native-only metadata; the embedded Mutation Testing Elements projection remains
stable and does not expose local workspace paths.

## Compile pruning metadata

Native reports include `execution.compilePruning`. Source-overlay runs use
`strategy = "source-overlay-prune-and-retry"`; compiled artifact runs use
`strategy = "compiled-artifact-prune-and-retry"`. It records:

- `attempts`, compile-failing candidate batch attempts;
- `candidateMutants`, the number of mutants considered by those attempts;
- `failedBatches`, candidate batches that failed build/check;
- `retryBatches`, batches retried after compile-pruned mutants were removed;
- `prunedMutants`, `buildErrors`, and `checkErrors`;
- `records[]`, the native details for each pruned mutant.

Each pruned mutant remains visible in `mutants[]` with status `BUILD_ERROR` or
`CHECK_ERROR`, `resultSource = "compile-pruning"`, and
`run.testSkippedReason = "compile-pruned"`. This keeps current consumers stable
while separating compile-invalid mutants from test-executed mutants.

## Coverage and scheduler metadata

Native reports distinguish coverage state at both run and mutant level:

- `coverage.enabled`, `coverage.provider`, and `coverage.files` describe the
  coverage source;
- `coverage.coveredMutants`, `coverage.noCoverageMutants`, and
  `coverage.unknownCoverageMutants` separate covered, not-covered, and
  unknown-coverage mutants;
- `coverage.testLevel`, `coverage.testMappedFiles`, and
  `coverage.testSelectedMutants` describe test-level selection when available;
- each native mutant run may include `run.coverageStatus` with `covered`,
  `not-covered`, or `unknown`.

Native reports include `execution.testScheduler` with
`schemaVersion = "stryker-cxx.test-scheduler.v1"`. It summarizes the sessions
that actually ran:

- `strategy`, currently `per-mutant` or `batched`;
- `sessions`, `batchSessions`, `perMutantSessions`, and `splitSessions`;
- `coverageSelectedSessions`, for sessions using coverage-selected commands;
- `groups[]`, with deterministic `sessionId`, session type, batch id, split
  source, selected tests, test command, mutant ids, optional active mutant-switch
  guards, and final grouped status.

When batching is enabled, native reports also include
`execution.batching.plan[]`. The plan is emitted before batch probes run and
uses the same deterministic batch ids as executed batch sessions. Each entry
contains the batch index, batch id, session type, heuristic, mutant ids, and
source locations. Entries also include placement diagnostics for each planned
mutant, including whether it seeded a batch, joined an existing batch, or started
a new batch because of a deterministic constraint such as adjacent-line
isolation, source-structure isolation, or the configured batch-size limit. When
test-level coverage is available, placement diagnostics also record
`coverage-selected affinity` when a mutant joins a batch with the same selected
test set, or `coverage-union minimized` when it joins the compatible batch that
adds the fewest new selected tests to the eventual batch command.

Batched sessions can now use coverage-selected tests. When a batch contains
multiple covered mutants, the scheduler runs the coverage command template with
the ordered union of covered tests for that batch.

## Plugin compatibility

Plugin manifests are loaded locally and deterministically before mutation work
starts. Capability declarations may be boolean flags or objects. Missing
capability versions are treated as v1 for compatibility; explicit `version`,
`apiVersion`, or `capabilityVersion` values must be `1`, `1.0`, or `v1`.
Unsupported capability versions fail during initialization before build, check,
test, coverage, or reporter hooks can run.

Native reports include `execution.pluginLifecycle` with
`schemaVersion = "stryker-cxx.plugin-lifecycle.v1"`. It records:

- `supportedEvents`, the explicit local plugin lifecycle phases:
  `initialization`, `projectAnalysis`, `mutationDiscovery`,
  `artifactCreation`, `coverageAnalysis`, `scheduling`, `execution`,
  `reporting`, and `cleanup`;
- `legacyAliases`, currently `preRun -> initialization` and
  `postRun -> cleanup`;
- `loadOrder`, the deterministic manifest order used for hook execution;
- `registeredHooks`, including plugin name, hook name, canonical event, command
  count, and redacted command text;
- `runs`, including phase, hook, plugin, redacted command text, exit code,
  duration, log path, and redacted environment summary;
- `localOnly = true` and `networkInstall = false`, because mutation runs never
  install plugins or discover plugins from the network.

Plugin hook commands receive `STRYKER_CXX_PLUGIN`, `STRYKER_CXX_HOOK`,
`STRYKER_CXX_PHASE`, `STRYKER_CXX_REPORT`, and `STRYKER_CXX_ARTIFACT_DIR`.
Explicit environment values are not serialized into reports; report artifacts
record only keys and redact sensitive shell assignments in command text.

Reporter commands selected through `--reporter` are also summarized under
`execution.reporterRuns[]`. Each entry records the plugin, reporter name, hook,
phase, redacted command, status, exit code, duration, log path, and the same
redacted environment summary used by plugin lifecycle records. If a requested
reporter is not provided by any loaded plugin reporter command, the run still
completes and `execution.reporterRuns[]` includes a `status = "notFound"`
diagnostic with the requested reporter name, reason, and sorted
`availableReporters` list. This makes local reporter resolution explicit without
installing or discovering plugins from the network.

## Artifact placement and restoration metadata

Native reports include `artifactPlacement` with
`schemaVersion = "stryker-cxx.artifact-placement.v1"`. It formalizes where
mutation artifacts are placed and how originals are restored:

- `mode`, either `source-overlay` or `compiled-artifact`;
- `implementation`, matching `inplace`, `copy`, or `git-worktree`;
- `artifactRoot`, `workerTmpDir`, and `workerLabel`;
- `restoreOriginals`, which is `true` for source-overlay and compiled-artifact
  runners;
- `retainArtifacts` and `retainArtifactsFor`;
- `sourceOverlay.restorePolicy` and `sourceOverlay.placement`;
- `compiledArtifacts.supported`, placement, kind, and restore policy fields for
  compiled artifact replacement.

Per-run native metadata may include `run.artifactPlacement` with the materialized
workspace, whether the materialized artifact was restored or retained, retained
path/reason, and cleanup guidance. In `inplace` mode the source line is restored
after each mutation session; in isolated `copy` and `git-worktree` modes the
workspace is removed unless retention was explicitly requested.

## Compiled artifact backend metadata

`source-overlay` remains available as a compatibility backend, but native runs
can now select `--artifact-backend compiled-executable`,
`--artifact-backend compiled-library`, or `--artifact-backend compiled-object`
for CMake/CTest-style targets. `compiled-executable` also supports simple
Make/Ninja/Meson/Bazel/Xcode executable targets when the original executable
artifact already exists and can be swapped/restored by path. Bazel requires an
explicit `--build-target` label and `--test-binary` artifact path. Xcode
requires `--test-binary` plus either `--build-target` or `--xcode-scheme`; the
scratch build sets `CONFIGURATION_BUILD_DIR` so the rebuilt executable can be
located deterministically. `compiled-library` supports CMake/CTest libraries,
explicit Make/Ninja/Meson `lib<target>` artifacts, and Bazel/Xcode libraries
only when `--artifact-path` names the original library artifact to
swap/restore. `compiled-object` supports Bazel when `--artifact-path` names the
linked artifact and the Bazel build emits compile-database entries proving the
mutated source-to-object path.

The compiled artifact backends:

- materializes mutated source in a scratch workspace, not in the user's
  checkout;
- configures when required and builds the mutated target in scratch space;
- records mutated object artifacts for `compiled-object`;
- snapshots the original linked artifact;
- swaps the mutated linked artifact into the original build/test location;
- runs the configured tests against that swapped artifact;
- restores the original artifact after the session;
- records the proof under top-level `compiledArtifacts[]` and
  per-mutant `run.compiledArtifact`.

Compiled artifact native reports use:

- `mutationArtifact.mode = "compiled-artifact"`;
- `mutationArtifact.backend = "compiled-executable"`, `"compiled-library"`, or
  `"compiled-object"`;
- `artifactPlacement.compiledArtifacts.supported = true`;
- `compiledArtifacts[].placementPolicy = "swap-file"`;
- `compiledArtifacts[].sourceCheckoutMutation = false`;
- `compiledArtifacts[].originalRestored = true` when the original artifact hash
  after restoration matches the pre-session hash.

Compiled batch sessions use the same metadata shape with
`run.scheduler.sessionType = "batch"`. When `--jobs > 1` is used with compiled
batching, workers build and check in parallel scratch directories, then use a
per-original-artifact placement lock while swapping, testing, and restoring the
linked artifact.
