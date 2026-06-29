# stryker-cxx contract

This repository should depend on `mutation-testing-elements` (`schemaVersion: 2.0`).
It accepts both:

- direct MTE payloads, and
- full `cxx-mutant.report.v1` payloads that include a nested `mutationTestingElements`.

## Required fields

- `mutationTestingElements.schemaVersion = "2.0"` (inside `cxx-mutant` wrapper, if using wrapper mode)
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
- `Pending`
- `RuntimeError`
