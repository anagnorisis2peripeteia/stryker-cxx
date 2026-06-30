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

`compilePruning` is currently reported as `notSupported`; compile and checker
failures still appear as native mutant statuses until the lifecycle parity
roadmap adds a pruning loop.
