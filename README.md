# stryker-cxx

`stryker-cxx` is a standalone Stryker-style mutation tool for C++/ObjC++/Metal.
It discovers scoped source-level mutants, recompiles/reruns the supplied test command
for each mutant, and emits both a native report and `mutation-testing-elements`
(`schemaVersion: 2.0`) output.

The package:

- provides the `stryker-cxx run`, `list-mutants`, and `run-mutant` engine commands,
- consumes validated `mutation-testing-elements` (`schemaVersion: 2.0`) payloads,
- accepts both direct MTE payloads and full `stryker-cxx.report.v1` wrappers,
- exposes a stable JS surface for summarizing mutants.

## Install

```bash
npm install -g stryker-cxx
# or
npm install --save-dev stryker-cxx
```

## Usage

Create a starter config:

```bash
stryker-cxx init --preset cmake-gtest
```

Supported presets include `cmake`, `ctest`, `ninja`, `make`, `meson`, `bazel`,
`cmake-gtest`, `cmake-catch2`, `cmake-doctest`, `ninja-gtest`,
`meson-catch2`, and `bazel-gtest`.

Run mutation testing directly:

```bash
stryker-cxx run \
  --repo . \
  --since origin/main \
  --files src/foo.cpp \
  --build-command "ninja -C build target" \
  --test-command "./build/bin/target_test" \
  --report mutation.json
```

`--since <ref>` is the Stryker.NET-style spelling for `--base <ref>` and scopes
mutation to the local diff against that ref. Use `--mutation-level
Basic|Standard|Advanced|Complete` to select a Stryker-style default mutator set; an
explicit `--mutators` list still wins when a review needs an exact surface.

For common static-check phases, use `--check-system clang-tidy|cppcheck` with
optional `--check-args` to synthesize a check command from the current
`--files`. Explicit `--check-command` still takes precedence.

For isolated or debuggable workers, add `--worktree-mode copy` or
`--worktree-mode git-worktree`. `--retain-worktrees --worker-tmp-dir <path>`
keeps mutated worker directories for inspection, and `--env KEY=VALUE` injects
explicit build/check/test environment keys recorded in the native report. Use
`--retain-worktrees-for SURVIVED,TIMEOUT` to keep only workers whose final status
matches the supplied native status list, and `--retained-worktree-ttl-hours 24`
to remove older retained workers under the same worker temp root before a run.
Use `--worker-label pr-96205-proof` to tag retained worker paths and report
metadata for proof capture or CI artifact grouping.
Use `--distribution-manifest distribution.json` to archive the selected
shard/worker contract, including redacted commands and selected mutant IDs, for
CI fan-out or proof bundles. Its JSON schema is published at
`docs/schemas/stryker-cxx.distribution.schema.json`.
Use `--env-inherit PATH,HOME` or `--env-block GITHUB_TOKEN` when a gate needs an
explicit inherited-environment allow/deny policy.
With `--batch-mutants`, isolated `copy`/`git-worktree` runs can use `--jobs` to
probe multiple compatible batches concurrently while preserving stable report
ordering. Batching stays conservative: same-file adjacent-line edits and
source-structure mutators are isolated from mixed batches.

For Stryker.NET-style artifact execution, use an explicit compiled backend on a
prebuilt CMake/CTest project:

```bash
cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
cmake --build build --target target_test

stryker-cxx run \
  --repo . \
  --files src/foo.cpp \
  --build-system cmake \
  --build-dir build \
  --build-target target_test \
  --test-binary build/target_test \
  --test-command "ctest --test-dir build --output-on-failure" \
  --artifact-backend compiled-object \
  --batch-mutants \
  --report mutation.json
```

Use `--execution-backend auto|source-overlay|mutant-switch|compiled-artifact|llvm-switch`
to record the requested execution backend separately from the artifact backend.
`llvm-switch` is experimental: when compile-database or CMake/CTest ownership
evidence is available and every selected mutant is guardable, it uses the
single-compile guarded-source switch path; otherwise it reports an explicit
fallback.
This is intentionally not advertised as Mull-equivalent LLVM IR mutation yet:
native reports include `parity` / `execution.parity` metadata that calls out the
remaining IR/object instrumentation gap and the evidence available for each run.

Compiled backends currently support CMake/CTest targets:

- `compiled-executable` swaps a rebuilt executable into the original test
  location.
- `compiled-library` swaps a rebuilt shared/static library into the original
  test location.
- `compiled-object` records mutated object artifacts, relinks the owning target,
  swaps the linked artifact, and restores the original artifact by hash.

`source-overlay` remains the default compatibility backend for generic command
adapters. Ninja, Make, Meson, Bazel, and Xcode command synthesis are available
for source-overlay runs; `compiled-executable` also supports CMake/CTest plus
simple Make/Ninja/Meson/Bazel/Xcode executable targets when the caller supplies
an explicit target and original artifact path. `compiled-library` also supports
explicit Make/Ninja/Meson library targets when an original `lib<target>` artifact
already exists and can be restored by hash, plus Bazel/Xcode library targets
when `--artifact-path` names the original library to swap/restore.
`compiled-object` supports CMake/CTest and explicit Make/Ninja/Meson targets
when `--artifact-path` names the linked artifact and `compile_commands.json`
proves the mutated source-to-object path. Bazel `compiled-object` targets are
also supported when `--artifact-path` names the linked artifact and the Bazel
build emits usable compile-database entries for the mutated sources. Xcode
object support remains blocked until that adapter exposes equivalent object
ownership.
Compiled artifact batching supports `--jobs > 1` when `--batch-mutants` is set:
workers build/check in parallel scratch directories, then serialize
swap/test/restore with a per-original-artifact placement lock.
Reports record env keys for reproducibility, but redact explicit env values and
shell-style sensitive assignments such as `TOKEN=value` from serialized report
artifacts.
Equivalent/noise suppression defaults to `--equivalent-suppression conservative`,
which marks generated-code markers, duplicate logical operands, and arithmetic
identity rewrites as `IGNORED` with report metadata. Use
`--equivalent-suppression off` when a proof run needs every discovered mutant to
execute.

For incremental runs, `--baseline-max-age-days <n>` and
`--baseline-branch <name>` bound cache reuse by freshness and branch lifecycle.
Rejected cache entries are reported under `baseline.missReasons` and per-mutant
`run.baselineMissReason`.
Use `stryker-cxx baseline-info --baseline-file <path>` to inspect cache status
and `stryker-cxx baseline-history --baseline-file <path>` to review newest
entries, by-day status buckets, branches, mutant locations, and optional repo
file-existence diagnostics before or after merge/prune maintenance.
Use `stryker-cxx parity-audit --report mutation.json --format markdown` to turn
the native eight-gap Mull/Stryker.NET parity metadata into a CI-readable
checklist. Add `--profile review` to fail missing/unknown parity evidence, or
`--profile strict` to fail partial parity evidence too.

For dashboard uploads, `--dashboard-upload-url <url>` remains explicit and
optional. Add `--dashboard-auth-token-env STRYKER_CXX_DASHBOARD_TOKEN`,
`--dashboard-version 1`, `--dashboard-retention-days 14`,
`--dashboard-project`, `--dashboard-branch`, `--dashboard-commit`, and
`--dashboard-build-url` when a hosted collector needs bearer-token auth,
compatibility metadata, CI provenance, and retention policy. Add
`--dashboard-upload-retries <n>` and `--dashboard-upload-retry-delay-ms <n>` to
record deterministic retry-attempt metadata for flaky hosted collectors.

For Xcode/XCTest projects, `--build-system xcodebuild --xcode-workspace <path>
--xcode-scheme <name>` synthesizes `xcodebuild build` and `xcodebuild test`
commands. Add `--xcode-project`, `--xcode-configuration`, `--xcode-sdk`, or
`--xcode-destination` for project/simulator-specific runs. `--test-framework
xctest --xctest-bundle <path>` keeps the simple `xcrun xctest` path, while
`--xctest-destination`, `--xctest-only-testing`, or `--xctest-skip-testing`
synthesize `xcodebuild test-without-building -xctestrun ...` commands for
prebuilt bundles.

For `--test-framework gtest|catch2|doctest`, `--test-binary` is optional when
there is exactly one repo-local executable test binary under `--build-dir`,
`build`, `cmake-build-*`, `out`, or `bin`. Pass `--test-binary` to disambiguate.

Report artifacts include `--format markdown`, `html`, `sarif`,
`github-annotations`, and `mutation-testing-elements`.

Coverage JSON may include per-line covering tests. With
`--coverage-test-command-template`, `stryker-cxx` substitutes `{tests}`,
`{tests_csv}`, `{tests_space}`, or `{first_test}` and runs the selected command
for mutants whose line has test-level coverage metadata.
When native test-level coverage is not already available, pair
`--coverage-helper-command-template` with `--coverage-helper-tests` to run a
coverage export command once per named test. The helper template receives
`{test}` and `{coverage_file}` placeholders plus `STRYKER_CXX_COVERAGE_TEST`
and `STRYKER_CXX_COVERAGE_FILE` environment variables; each generated JSON or
LCOV file is merged into the same `coveredBy` mapping.

List or reproduce individual mutants:

```bash
stryker-cxx list-mutants --repo . --files src/foo.cpp
stryker-cxx run-mutant --repo . --id src/foo.cpp:1:0:EqualityOperator:abc123 \
  --build-command "ninja -C build target" \
  --test-command "./build/bin/target_test" \
  --report one-mutant.json
```

Consume an existing MTE report:

```bash
stryker-cxx --mte ./mutation-testing-elements.json --summary
stryker-cxx --mte ./mutation-testing-elements.json --summary --json
stryker-cxx --mte ./mutation-testing-elements.json --survivors
stryker-cxx --mte ./mutation-testing-elements.json --survivors --json
```

### Supported input shapes

- direct MTE payload: top-level `schemaVersion: "2.0"`, `files` map
- wrapped `stryker-cxx` payload: top-level `schemaVersion != "2.0"` with
  `mutationTestingElements` containing the MTE object.

## Compatibility contract

The CLI expects fields in MTE shape:

- `schemaVersion = "2.0"`
- `language = "cpp"` or `"objc"`
- `files` map keyed by source path
- per mutant: `id`, `mutatorName`, `original`, `replacement`, `status`, `location`

## Spec

The Stryker/Stryker.NET parity checklist lives in [`docs/spec.md`](docs/spec.md).
The Stryker.NET structural parity target lives in
[`docs/stryker-net-structural-parity-spec.md`](docs/stryker-net-structural-parity-spec.md).
The Stryker lifecycle parity roadmap lives in
[`docs/lifecycle-parity-spec.md`](docs/lifecycle-parity-spec.md).
Mutator behavior and noise profiles are documented in
[`docs/mutators.md`](docs/mutators.md).
Contribution and CI expectations are documented in
[`CONTRIBUTING.md`](CONTRIBUTING.md).
Fixture projects and plugin compatibility manifests are documented in
[`docs/fixtures.md`](docs/fixtures.md).
Release signing/provenance and dashboard upload policies are documented in
[`docs/signing.md`](docs/signing.md) and [`docs/dashboard.md`](docs/dashboard.md).
The npm provenance release workflow lives in
[`.github/workflows/release.yml`](.github/workflows/release.yml).
Security reporting is documented in [`SECURITY.md`](SECURITY.md).
Machine-readable schemas live under [`docs/schemas/`](docs/schemas/).
Full-spec validation is documented in [`docs/validation.md`](docs/validation.md).

## Project map

- `src/index.js`: contract parser + summary helpers
- `src/cli.js`: CLI adapter entrypoint
- `bin/stryker-cxx.js`: executable wrapper and Python engine dispatcher
- `python/stryker_cxx/`: C++ mutation engine
- `tests/adapter.test.mjs`: JS API and schema tests
- `tests/python/`: engine contract tests
- `fixtures/`: adapter projects and plugin compatibility manifests
