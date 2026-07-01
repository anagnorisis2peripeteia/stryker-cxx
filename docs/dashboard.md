# Dashboard export and upload policy

`stryker-cxx` supports local dashboard artifacts and explicit dashboard uploads.
It does not upload mutation data unless the caller supplies
`--dashboard-upload-url`.

## Local artifacts

- `--format html` writes a static dashboard with filtering and sortable mutant
  rows.
- `--dashboard-export <path>` writes `stryker-cxx.dashboard.v1` JSON for CI
  artifacts or downstream ingestion.
- Both artifact types are safe to archive without a hosted dashboard service.

## Upload behavior

`--dashboard-upload-url <url>` performs an HTTP POST with the dashboard JSON
payload. The URL is intentionally explicit; no default service is contacted.
`--dashboard-auth-token-env <KEY>` can require a bearer token from the named
environment variable. The token value is used only as an HTTP header and is not
serialized into reports or dashboard payloads. `--dashboard-auth-header <name>`
defaults to `Authorization`; custom header names receive the raw token value.
`--dashboard-upload-retries <n>` retries failed uploads up to `n` additional
times, and `--dashboard-upload-retry-delay-ms <n>` controls the delay between
attempts. The default is zero retries.

`--dashboard-version <v>` records a hosted-dashboard compatibility version in
the payload. `--dashboard-retention-days <n>` records caller-managed retention
metadata so a hosted service can reject or expire uploads consistently.
`--dashboard-project`, `--dashboard-branch`, `--dashboard-commit`, and
`--dashboard-build-url` record explicit CI/project provenance when environment
variables are unavailable or ambiguous.

Dashboard payloads include:

- `dashboardVersion`;
- `toolVersion`;
- `generatedAt`;
- `retention.days` and `retention.policy`;
- `privacy.sourceFilesIncluded`, `privacy.mutantSourceSnippetsIncluded`,
  `privacy.secretValuesRedacted`, and `privacy.environmentValuesRedacted`;
- `provenance.reportSchemaVersion`;
- `provenance.toolVersion`;
- `provenance.configHash` and `provenance.configPath`;
- `score`, `thresholds`, and `thresholdStatus` for CI gating without
  recomputing threshold-band policy;
- `analysis.sourcePrecision` for source-range confidence across native JSON,
  MTE, and dashboard artifacts;
- `projectAnalysis.buildGraph` ownership summary for source/build/test evidence;
- `parity` / `execution.parity` for the eight-gap Mull/Stryker.NET convergence
  checklist and explicit LLVM IR/object instrumentation boundary;
- explicit or environment-derived CI project, branch, commit, top-level
  `runId`, and build URL metadata;
- upload auth metadata containing the env var/header names, never token values;
- upload status metadata under `provenance.upload.status`, with `disabled`,
  `notAttempted`, `attempting`, `succeeded`, or `failed` states and HTTP
  status/error detail when available;
- upload retry metadata under `provenance.upload.maxAttempts`,
  `provenance.upload.retryDelayMs`, and `provenance.upload.attempts[]`, where
  each attempt records its attempt number, status, and status code or error.

The native report records local dashboard export execution under
`execution.dashboard.export`, including whether export was enabled, the export
path, status, byte count, and write timestamp. This keeps local CI artifacts
auditable even when no hosted dashboard service is used.

Recommended hosted-service requirements:

- authenticate upload URLs with short-lived credentials or pre-signed URLs;
- record the `schemaVersion`, package version, commit SHA, and CI run ID;
- reject unknown schema versions by default;
- retain uploaded dashboards according to the repository's CI artifact policy;
- avoid storing source text unless the caller explicitly opts into it.

## Privacy boundary

Dashboard exports include mutant metadata: file paths, line numbers, mutator
names, statuses, and summary counts. They should be treated as code-review
artifacts and not uploaded to third-party services without repository approval.
