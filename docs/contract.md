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

## Project analysis metadata

Native reports may include an optional `projectAnalysis` object with
`schemaVersion = "stryker-cxx.project-analysis.v1"`. It records the project
analysis performed before mutation execution:

- `confidence`, currently `high`, `medium`, or `low`;
- `targetFiles`, the requested mutation source files;
- `buildSystems`, detected or explicit build-system signals;
- `compileDatabase`, whether `compile_commands.json` was found and loaded;
- `sourceTargets`, source file metadata and compile-database match state;
- `buildTargets`, discovered or explicit build targets;
- `testTargets`, discovered or explicit test targets/frameworks/commands;
- `commands`, the resolved build/check/test commands.

This metadata is descriptive. It does not yet replace explicit build and test
commands, and unknown projects degrade to the user-supplied command flow.

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

Native reports include `execution.compilePruning` with
`strategy = "source-overlay-prune-and-retry"`. It records:

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
