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
- `Pending`
- `RuntimeError`
