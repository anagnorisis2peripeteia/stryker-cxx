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

`--dashboard-upload-url <url>` performs a single HTTP POST with the dashboard JSON
payload. The URL is intentionally explicit; no default service is contacted.
`--dashboard-auth-token-env <KEY>` can require a bearer token from the named
environment variable. The token value is used only as an HTTP header and is not
serialized into reports or dashboard payloads. `--dashboard-auth-header <name>`
defaults to `Authorization`; custom header names receive the raw token value.

`--dashboard-version <v>` records a hosted-dashboard compatibility version in
the payload. `--dashboard-retention-days <n>` records caller-managed retention
metadata so a hosted service can reject or expire uploads consistently.

Dashboard payloads include:

- `dashboardVersion`;
- `generatedAt`;
- `retention.days` and `retention.policy`;
- `provenance.reportSchemaVersion`;
- `provenance.configHash` and `provenance.configPath`;
- CI commit/run IDs when exposed by common CI environment variables;
- upload auth metadata containing the env var/header names, never token values.

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
