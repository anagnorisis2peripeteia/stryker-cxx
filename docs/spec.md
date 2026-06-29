# stryker-cxx spec

`stryker-cxx` is a standalone Stryker-style mutation runner for C, C++,
Objective-C++, and optionally Metal shader sources. Marmorkrebs is only an
orchestrator/consumer; this repository owns C++ mutation execution and native
reporting.

## Parity target

First parity means matching the parts of Stryker/Stryker.NET that matter for PR
gates:

- deterministic mutant discovery and stable mutant IDs;
- scoped runs over files, changed lines, and optional line ranges;
- reproducible per-mutant execution;
- CI-stable exit codes;
- native machine-readable reports plus `mutation-testing-elements` projection;
- survivor, timeout, and no-coverage reporting;
- resumable runs and isolated execution modes;
- human-readable reports suitable for review bodies and CI artifacts;
- extensible mutator configuration.

Whole-program LLVM instrumentation and perfect equivalent-mutant detection are
not required for the first parity target.

Full Stryker-family parity means becoming a standalone C/C++ mutation runner
with comparable lifecycle behavior to StrykerJS and Stryker.NET, not merely a
Marmorkrebs provider. That requires the additional components listed in
[Full parity requirements](#full-parity-requirements).

## Implemented surface

- `stryker-cxx run`
- `stryker-cxx init`
- `stryker-cxx list-mutants`
- `stryker-cxx run-mutant`
- `--base`, `--lines`, `--include`, `--exclude`
- `--mutators`, `--max-mutants`, `--include-metal`
- `--mode token`
- `--mode clang` using libclang parse validation and AST-confirmed source mutations
- `--mode clang-ast` using AST cursor ranges before source rewrite candidate generation
- `--timeout`
- initial dry-run/original-test validation by default
- `--skip-initial-test`
- `--dry-run-only`
- `--timeout-factor`, `--timeout-constant-ms`
- `--check-command`
- `--check-system clang-tidy|cppcheck`, `--check-args`
- `--skip-tests`
- `--coverage-file`, `--coverage-provider`
- `--coverage-helper-command-template`, `--coverage-helper-tests`
- `--incremental`
- `--baseline-file`, `--write-baseline`, `--clear-baseline`
- `--batch-mutants`, `--batch-size`
- local plugin manifests via `--plugin` / `--plugin-dir`
- plugin-contributed token mutators
- reporter selection metadata via `--reporter`
- build/test adapter synthesis via `--build-system cmake|ctest|ninja|make|meson|bazel`
- adapter controls: `--build-dir`, `--build-target`, `--test-target`,
  `--test-filter`
- framework adapter controls: `--test-framework gtest|catch2|doctest|xctest`,
  `--test-binary`, `--xctest-bundle`, `--xctest-destination`,
  `--xctest-only-testing`, `--xctest-skip-testing`
- baseline maintenance commands: `baseline-info`, `baseline-merge`,
  `baseline-prune`
- dashboard export/upload: `--dashboard-export`, `--dashboard-upload-url`
- executable plugin hooks: `preRun`, `postRun`, reporter commands
- plugin provider hooks for build/check/test runner phases and coverage files
- resource controls: `--retain-worktrees`, `--retain-worktrees-for`,
  `--retained-worktree-ttl-hours`, `--worker-tmp-dir`, `--env KEY=VALUE`,
  `--env-inherit`, `--env-block`
- convention compatibility: CLI flags are kebab-case, config/report fields are
  lowerCamelCase, mutation status names remain Stryker/MTE-style uppercase, and
  Marmorkrebs-specific normalization stays outside this repository
- repository convention policy: `.editorconfig`, contribution guidance, package
  contents, release metadata, and docs must stay in-tree so standalone
  contributors can follow the same style and gate expectations as the official
  Stryker family repos

Repository convention guardrails:

- `.editorconfig` defines whitespace and line-ending behavior for all tracked files.
- Python implementation remains typed where practical and uses 4-space indentation.
- TypeScript/JS/JSON/YAML/Markdown uses 2-space indentation and final newlines.
- CLI flags remain kebab-case; config/report schema fields remain lowerCamelCase.
- canonical status vocabulary remains native Stryker-style uppercase names with
  migration-only compatibility aliases isolated to legacy output mode.
- production/proof behavior changes must pass `npm run lint`, `npm test`,
  `npm run schema:check`, `npm run validate:full-spec`, and `npm pack --dry-run`.
- version surface: `stryker-cxx --version`
- release docs, changelog, and tag-triggered release smoke workflow
- npm provenance publish workflow
- fixture projects for build-system adapters and plugin compatibility
- package contents allowlist and Node engine metadata
- signed-release and dashboard-upload policy docs
- machine-readable JSON Schemas under `docs/schemas/`
- report `config.path`, `config.hash`, and `config.effective`
- cross-platform full-spec local validation script: `npm run validate:full-spec`
- `--jobs` with non-inplace worktree modes
- `--worktree-mode inplace|copy|git-worktree`
- `--allow-dirty`
- `--resume`
- `--shard-index`, `--shard-total`
- `--format json|markdown|html|sarif|mutation-testing-elements|github-annotations`
- `--threshold`, `--fail-on-empty`
- `--threshold-high`, `--threshold-low`, `--threshold-break`
- `stryker-cxx.yml` / `.stryker-cxx.yml` config loading
- unknown config keys are rejected by default
- `stryker-cxx init --path stryker-cxx.yml`
- `stryker-cxx init --preset cmake|cmake-gtest|cmake-catch2|cmake-doctest`
- `stryker-cxx init --preset ctest|ninja|ninja-gtest|make|meson|meson-catch2`
- `stryker-cxx init --preset bazel|bazel-gtest`
- source-level `// Stryker disable|restore` ignore comments with
  `next-line` and Stryker.NET-style `once` aliases

## Conventions parity gate

For each new feature or adapter extension, the following convention check is part
of parity acceptance:

- `.editorconfig` and report schemas stay the source of truth for formatting and
  contract shape.
- CLI flags remain kebab-case; config/report schema fields remain lowerCamelCase.
- Native status vocabulary and command surface stay stable at the seam
  (`KILLED`, `SURVIVED`, `TIMEOUT`, `run`, `run-mutant`, `list-mutants`).
- `mutation-testing-elements` remains a projection target; `stryker-cxx.report.v1`
  is the native contract.
- Env-variable keys and artifact names are preserved for reproducibility while
  explicit env values remain redacted in artifacts.
- Python keeps 4-space indentation and practical typing; JS/TS/JSON/YAML keeps
  2-space indentation and shell-safe command composition.
- Every user-visible behavior and schema change updates
  `docs/spec.md`, `docs/contract.md`, and `docs/validation.md` in the same PR.
- Changes touching parser/CLI behavior in Marmorkrebs include paired provider-level
  parser tests and `marmorkrebs docs/stryker-cxx-spec.md` updates.

Conventional verification commands for this section:

- `npm run lint`
- `npm test`
- `npm run schema:check`
- `npm run validate:full-spec`
- `npm pack --dry-run`
- `git diff --check`
- fixture smokes for installed systems (cmake/ctest, ninja, make, meson, bazel).

## Native report

The canonical native report is `stryker-cxx.report.v1`.

Required top-level fields:

- `schemaVersion = "stryker-cxx.report.v1"`
- `tool = "stryker-cxx"`
- `repo`, `base`
- `startedAt`, `completedAt`
- `threshold`
- `thresholds.high`, `thresholds.low`, `thresholds.break`,
  `thresholds.status`
- `timeoutSeconds`
- `totalMutants`, `killed`, `survived`, `buildErrors`, `checkErrors`,
  `noCoverage`, `timeouts`, `ignored`
- `score` as a `0.0..1.0` fraction
- `execution.mode`, `execution.worktreeMode`, `execution.jobs`
- `execution.initialTest`, `execution.dryRunOnly`
- `execution.timeoutFactor`, `execution.timeoutConstantMs`,
  `execution.effectiveTimeoutMs`
- `dryRun.status`
- `coverage.enabled`, `coverage.provider`, `coverage.coveredMutants`,
  `coverage.noCoverageMutants`
- `baseline.enabled`, `baseline.path`, `baseline.cacheHits`,
  `baseline.cacheMisses`, `baseline.cacheWrites`
- `commands.build`, `commands.check`, `commands.test`
- `targetFiles`
- `summary.byFile`, `summary.byMutator`, `summary.byStatus`
- `mutants`
- `mutationTestingElements`

Required mutant fields:

- `id`
- `file`, `line`, `column` or `col`
- `mutator`
- `original`, `mutated`
- `status`
- `durationMs`
- `buildLog`, `testLog`
- `checkLog`
- `detail`
- `nodeKind`
- `resultSource`
- `baselineKey`
- `run.reproCommand`

Native statuses:

- `KILLED`
- `SURVIVED`
- `BUILD_ERROR`
- `CHECK_ERROR`
- `NO_COVERAGE`
- `TIMEOUT`
- `IGNORED`
- `PENDING`
- `RUNTIME_ERROR`

## MTE projection

The embedded MTE projection must use `schemaVersion: "2.0"` and canonical
Stryker-style statuses only:

- `Killed`
- `Survived`
- `NoCoverage`
- `Timeout`
- `Ignored`
- `Pending`
- `RuntimeError`

Native-to-MTE mapping:

- `KILLED` -> `Killed`
- `SURVIVED` -> `Survived`
- `BUILD_ERROR` -> `NoCoverage`
- `CHECK_ERROR` -> `RuntimeError`
- `NO_COVERAGE` -> `NoCoverage`
- `TIMEOUT` -> `Timeout`
- `IGNORED` -> `Ignored`
- `PENDING` -> `Pending`
- other infrastructure statuses -> `RuntimeError`

Compatibility aliases such as `CompileError` and `TimedOut` do not belong in
`stryker-cxx`. Marmorkrebs may normalize third-party tool output at its boundary.

## Exit codes

- `0`: completed and met threshold.
- `1`: usage or infrastructure error.
- `2`: completed but score was below threshold.
- `3`: no mutants and `--fail-on-empty` was set.

## Mutators

Default mutators:

- `ConditionalBoundary`
- `EqualityOperator`
- `LogicalOperator`
- `BooleanLiteral`

Additional implemented mutators:

- `ArithmeticOperator`
- `AssignmentOperator`
- `BitwiseOperator`
- `UnaryOperator`
- `ReturnValue`
- `IntegerLiteral`
- `NullLiteral`
- `CallRemoval`
- clang-ast direct source-range candidates for single-line boolean literals and
  boolean return statements

Remaining for parity:

- richer baseline visualization and history UX beyond the current
  info/merge/prune commands;
- richer mixed-mutant batching heuristics beyond current overlap grouping,
  split attribution, and isolated parallel batch probes;
- larger C/C++ mutator catalog beyond current operator, call, integer, null,
  and boolean-return mutators;
- richer AST-first source rewrite coverage and macro-safe rewrites;
- richer plugin compatibility surface beyond local command-provider hooks;
- richer checker/build-system adapters beyond the current generic, checker, and
  provider hooks;
- richer hosted-dashboard service protocol and CI annotation polish beyond the
  current explicit upload auth/provenance/retention conventions;
- deeper project integrations beyond generic build/test framework adapters;
- richer resource-isolation controls beyond retained worktree policies, custom
  worker temp roots, and explicit env injection;
- live npm token setup, signed tags, and production hosted-dashboard service
  operations.

## Full-spec parity matrix (explicit gap view)

This matrix separates what is already implemented from the remaining Stryker-family
surface that still needs work for `full parity` mode.

### ✅ implemented in `stryker-cxx`

- Deterministic mutant discovery, stable mutant IDs, and resume/continuation.
- Scoped mutation from `--base`, `--files`, `--lines`, `--include`, and
  `--exclude`.
- Reproducible single-mutant execution via `run-mutant`.
- Runtime isolation contracts for copy and git-worktree modes, with dry-run/check/test
  lifecycle metadata, timeouts, and environment injection controls.
- `mutation-testing-elements` projection as `schemaVersion: "2.0"` and stable
  native `stryker-cxx.report.v1`.
- `run`/`list-mutants`/`run-mutant` command surfaces with baseline, batching,
  sharding, coverage selection, checker phase, and threshold bands.
- Plugin surfaces for token mutators, reporter hooks, and runner/checker/test
  providers.
- Basic dashboard upload/export policy plus explicit auth/env redaction metadata.
- PR/CI-facing reporting including Markdown/HTML/SARIF/GitHub annotations and
  mutation element projection.
- Core mutator families and ignore-comment support used by `stryker-cxx`/Marmorkrebs.
- Concrete CI validation workflow entry (`npm run validate:full-spec`) and
  contract-schema checks in-repo without network.

### ⚠️ remaining for parity to mimic upstream Stryker family behavior

- Full mutator breadth (e.g. richer control-flow, statement/block, loop, and
  standard-library style mutations) is not complete.
- Equivalent-mutant and deeper equivalent-noise suppression is limited to
  scoped ignore-comment controls.
- Advanced test orchestration features beyond local command synthesis are still
  minimal (no built-in multi-process distributed runner framework).
- Objective-C++ and Metal AST-native candidate synthesis is intentionally partial and
  still follows the token-path default for most runs.
- External ecosystem parity items that are tool-family dependent (for example
  full StrykerJS/Stryker.NET reporter ecosystems, plugin hosts, and hosted dashboard
  orchestration semantics) remain intentionally outside this repo’s responsibility.
- Public Stryker ecosystem process controls such as PR-only workflows and release-
  platform expectations are tracked at the platform/repo level, not in this engine
  runtime itself.

### 🧭 cross-check baseline used for this matrix

- `cxx-mutant` historical spec baseline (`docs/cxx-mutant-spec.md` in the legacy repo):
  preserved where practical, with behavior now moved into a Stryker-native boundary.
- Stryker-family conventions in `marmorkrebs` interoperability: `stryker-cxx`
  output is treated as authoritative for native behavior and is only normalized at
  orchestration boundaries.
- `marmorkrebs` local/PR gating behavior expectations in
  `docs/stryker-cxx-spec.md` and `docs/local-vs-pr-usage.md`.

## Full parity requirements

This section is the target spec for turning `stryker-cxx` from a useful PR-gate
runner into a mature Stryker-family implementation.

### Initial dry-run and original-test validation

Before executing mutants, `stryker-cxx run` must be able to run the unmodified
project through the configured build/check/test lifecycle.

Required behavior:

- fail fast when the unmutated build command fails;
- fail fast when the unmutated check command fails;
- fail fast when the unmutated test command fails;
- record dry-run command, exit code, duration, stdout/stderr artifact paths, and
  failure reason in `stryker-cxx.report.v1`;
- support a `--dry-run-only` mode for CI setup/debugging;
- support an explicit opt-out for trusted advanced workflows;
- use dry-run timing as input to timeout calibration.

The native report should include:

- `dryRun.status`;
- `dryRun.build.exitCode`;
- `dryRun.build.durationMs`;
- `dryRun.check.exitCode`;
- `dryRun.check.durationMs`;
- `dryRun.test.exitCode`;
- `dryRun.test.durationMs`;
- `dryRun.artifacts`.

### Timeout calibration

Fixed `--timeout` is not enough for Stryker-grade behavior. The runner must
support calibrated mutant timeouts derived from the dry-run duration.

Required behavior:

- keep existing fixed `--timeout` behavior as an override;
- add timeout factor and timeout constant configuration;
- calculate an effective timeout per mutant when no fixed timeout is supplied;
- record the effective timeout in the report;
- distinguish mutant `TIMEOUT` from infrastructure/runtime failures.

Suggested model:

- `effectiveTimeoutMs = dryRunTestMs * timeoutFactor + timeoutConstantMs`;
- `--timeout-factor`;
- `--timeout-constant-ms`;
- `execution.effectiveTimeoutMs`;

### Coverage-aware test selection and `NoCoverage`

`stryker-cxx` accepts supplied line coverage via simple JSON, `llvm-cov export`
JSON, or LCOV and marks mutants outside covered lines as native `NO_COVERAGE`.
`stryker-cxx` accepts test-level coverage mappings in JSON coverage files and
can select per-mutant test commands via `--coverage-test-command-template`.
When test-level maps are not available up front, `stryker-cxx` can run a
framework-native coverage helper once per named test via
`--coverage-helper-command-template` and `--coverage-helper-tests`; each helper
run writes JSON or LCOV coverage to `{coverage_file}` and the covered lines are
merged into per-test `coveredBy` metadata.

Required behavior:

- ingest coverage data from `llvm-cov`/`llvm-profdata` first;
- map source locations to covering tests when test-level coverage is available;
- generate test-level maps from explicit framework test names and helper
  commands;
- support command placeholders `{tests}`, `{tests_csv}`, `{tests_space}`, and
  `{first_test}` for per-mutant selected test commands;
- classify mutants outside covered lines as native `NO_COVERAGE`;
- project native `NO_COVERAGE` to MTE `NoCoverage`;
- exclude `NO_COVERAGE` from killed/survived execution counts while preserving
  it in total mutant counts;
- allow coverage to be advisory when only file/line coverage is available.

The native report should include:

- `coverage.provider`;
- `coverage.files`;
- `coverage.coveredMutants`;
- `coverage.noCoverageMutants`;
- `coverage.testSelectedMutants`;
- `coverage.testSelectionMisses`;
- per-mutant `coveredBy` when test-level mapping is known;
- per-mutant `run.selectedTestCommand` when test-level selection is applied.
- `coverage.helper` provenance when helper commands generated test-level maps.

### Incremental and baseline mode

`stryker-cxx` supports an opt-in baseline cache that can reuse prior mutant
results safely when the mutant identity, source hash, mode, and command/config
hash match.

Required behavior:

- stable mutant IDs must remain valid across runs when the mutated source span
  and mutator are unchanged;
- cache keys must include tool version, config hash, relevant command hash,
  source hash, and mutant identity;
- support reusing killed/survived/timeout/no-coverage results when safe;
- support invalidating cache entries on source, config, runner, or mutator
  changes;
- record whether each mutant result was executed or restored from cache.

Implemented CLI/config:

- `--incremental`;
- `--baseline-file`;
- `--baseline-max-age-days`;
- `--baseline-branch`;
- `--write-baseline`;
- `--clear-baseline`;
- `baseline-info`, `baseline-merge`, and `baseline-prune` maintenance
  commands;

The native report should include:

- `baseline.enabled`;
- `baseline.path`;
- `baseline.cacheHits`;
- `baseline.cacheMisses`;
- `baseline.maxAgeDays`;
- `baseline.branch`;
- `baseline.missReasons`;
- per-mutant `resultSource = executed|baseline`;
- per-mutant `run.baselineMissReason` when a compatible key exists but policy
  rejects reuse.

Baseline maintenance output should include:

- entry counts by status;
- entry counts by branch;
- oldest/newest update timestamps;
- optional repo file-existence diagnostics for stale entries.

### Mutant batching and mixed-mutant execution

One-mutant-per-run is correct but expensive. `stryker-cxx` supports opt-in
safe batching for isolated worktree modes.

Required behavior:

- group compatible mutants into a single mutated worktree when safe;
- never batch mutants that overlap source spans or make attribution ambiguous;
- split batches when a test failure requires isolating the responsible mutant;
- preserve deterministic per-mutant results;
- expose batching as opt-in until proven stable on large C++ projects.

Implemented CLI/config:

- `--batch-mutants`;
- `--batch-size`;
- `--jobs` for parallel batch probes when using isolated worktrees;
- `execution.batching.enabled`;
- `execution.batching.parallelWorkers`;

Current constraints:

- batching requires `--worktree-mode copy` or `--worktree-mode git-worktree`;
- batch probes can run in parallel, but report entries are emitted in stable
  batch order for deterministic attribution;
- same-line mutants are not batched;
- failed/killed batches are rerun as individual mutants.

### Expanded C/C++ mutator catalog

The current mutators cover the first useful set. Full parity requires broader
C/C++ semantics.

Additional mutator targets:

- integer, floating-point, character, string, and null literal replacement;
- update operator replacement and removal;
- ternary/conditional-expression mutation;
- statement and block removal where syntactically safe;
- loop boundary and loop condition mutation;
- member access and pointer/member-access variants where safe;
- exception and error-path mutation;
- preprocessor-guard mutation for simple `#if`/`#ifdef` cases;
- standard-library call substitutions where low-noise rules exist;
- Objective-C++ message-send mutation where source parsing supports it;
- Metal shader numeric, comparison, and thread-position mutations.

Each mutator must document:

- examples;
- safety constraints;
- known equivalent-mutant risks;
- language-mode support;
- token-mode support;
- clang-mode support.

### AST-first rewrite engine

`--mode clang` uses source discovery with AST confirmation. `--mode clang-ast`
uses libclang cursor ranges before generating source rewrite candidates and
records rewrite metadata.

Required behavior:

- generate mutation candidates from AST nodes where libclang or another frontend
  exposes reliable spans;
- preserve comments, formatting, macro boundaries, and unrelated source text;
- reject macro-expanded spans that cannot be rewritten safely;
- emit node kind and rewrite strategy in the report;
- keep token mode as a dependency-free fallback.

The report should include:

- `execution.analysis.engine = token|clang-confirmed|clang-ast`;
- per-mutant `nodeKind`;
- per-mutant `rewriteStrategy`;
- per-mutant `sourceRange`;
- `execution.analysis.macroRejectedMutants`;
- `execution.analysis.macroRejections[]` diagnostics for candidates rejected
  because they overlap macro expansion ranges.

Remaining work:

- broader AST cursor coverage beyond direct boolean literal/return ranges and
  token replacements inside cursor spans;
- Objective-C++ and Metal AST-specific candidate generation.

### Compile/checker phase

C++ mutation often fails before tests execute. `stryker-cxx` models this as a
first-class generic `--check-command` phase instead of folding everything into
build/test output.

Required behavior:

- support a checker command that compiles or type-checks the mutated project;
- run checker before tests when configured;
- classify checker failures separately from test failures;
- preserve build/check/test logs independently;
- allow checker-only mutation campaigns for fast PR feedback.

Implemented CLI/config:

- `--check-command`;
- `--check-system clang-tidy|cppcheck`;
- `--check-args`;
- `--skip-tests`;
- `commands.check`;
- native status `CHECK_ERROR`;

MTE projection may continue to map compile/check failures to a compatible
Stryker status, but the native report must keep the C++-specific detail.

### Test runner and build-system integrations

Shell commands are portable, but mature local use needs first-class adapters.
`stryker-cxx` can synthesize build/test commands for common build systems.

Implemented generic adapters:

- CMake configure/build/test;
- CTest test discovery and filtering;
- Ninja build invocation;
- Make build invocation;
- Bazel targets;
- Meson test/build;
- checker command synthesis for `clang-tidy` and `cppcheck`;

Implemented framework adapters:

- GoogleTest filtering;
- Catch2 filtering;
- doctest filtering;
- XCTest bundle invocation;
- XCTest `xcodebuild test-without-building` destination, only-testing, and
  skip-testing controls;
- automatic single-binary discovery under `--build-dir`, `build`,
  `cmake-build-*`, `out`, or `bin` for GoogleTest, Catch2, and doctest.

Each adapter should define:

- discovery behavior;
- build/check/test commands emitted;
- test-filter capability;
- coverage capability;
- expected artifact paths;
- platform support.

### Plugin architecture

Stryker-family tools are extensible. `stryker-cxx` exposes local plugin
manifests without making Marmorkrebs responsible for C++ behavior.

Required plugin types:

- token mutator plugins;
- reporter metadata plugins;
- test-runner plugins;
- checker/build plugins;
- coverage-provider plugins;
- config-loader plugins.

Required behavior:

- versioned local JSON manifest loading;
- deterministic plugin loading order;
- explicit plugin capability declaration;
- plugin failure diagnostics;
- no implicit network/plugin install during mutation runs.

Implemented executable hooks:

- `hooks.preRun`;
- `hooks.postRun`;
- reporter command hooks selected by `--reporter`;
- build/check/test provider commands via `capabilities.runner`,
  `capabilities.buildRunner`, `capabilities.checker`, or
  `capabilities.testRunner`;
- coverage file generation/loading via `capabilities.coverageProvider`.
- compatibility fixture tests for plugin directories, provider hooks, reporter
  hooks, coverage-provider hooks, and plugin-contributed token mutators.

### Reporter and dashboard maturity

Current JSON/MTE/Markdown/HTML/SARIF output is enough for early automation.
HTML is a static local dashboard with filtering/sorting and per-file summaries.
Dashboard JSON can be exported or explicitly uploaded by URL.

Required behavior:

- HTML report with sortable/filterable mutants;
- per-file and per-mutator score breakdowns;
- machine-readable summary optimized for CI annotations;
- SARIF locations for survivors and timeouts;
- optional dashboard upload/export format;
- report assets that can be archived as CI artifacts without external services.

The native report should include:

- `summary.byFile`;
- `summary.byMutator`;
- `summary.byStatus`;
- `reporters`;
- `artifactDir`.

Implemented artifact formats:

- Markdown;
- HTML local dashboard;
- SARIF;
- GitHub Actions log annotations via `--format github-annotations`;
- direct Mutation Testing Elements JSON.
- dashboard export/upload policy with explicit upload URL, optional
  `--dashboard-auth-token-env`, dashboard payload version metadata, provenance
  fields, and caller-managed retention metadata.

### Threshold model

`--threshold` is preserved as a compatibility alias for `thresholds.break`.
The full threshold model supports Stryker-style high/low/break bands.

Required behavior:

- `high` marks a healthy score;
- `low` marks warning/degraded score;
- `break` fails the run;
- preserve the existing single threshold as a compatibility alias for `break`;
- report threshold band and final classification.

Suggested config:

- `thresholds.high`;
- `thresholds.low`;
- `thresholds.break`;

### Resource isolation and worker contract

Parallel C++ mutation needs an explicit worker contract.

Implemented behavior:

- `--worktree-mode inplace|copy|git-worktree` defines workspace ownership;
- `--jobs > 1` is rejected for `inplace` to prevent shared mutable build output races;
- `--batch-mutants` requires `copy` or `git-worktree` isolation;
- per-mutant and per-batch logs are written under `--artifact-dir` or
  `agent_space/stryker-cxx`;
- provider hooks receive `STRYKER_CXX_PHASE`, `STRYKER_CXX_PROVIDER`,
  `STRYKER_CXX_COMMAND`, `STRYKER_CXX_ORIGINAL_COMMAND`,
  `STRYKER_CXX_LOG`, and `STRYKER_CXX_REPO`;
- `--env KEY=VALUE` injects explicit environment variables into build/check/test
  commands and records only the injected keys in reports;
- `--env-inherit PATH,HOME` switches inherited build/check/test env to an
  allowlist, while `--env-block GITHUB_TOKEN` removes named inherited variables;
- coverage-provider hooks receive `STRYKER_CXX_COVERAGE_FILE`,
  `STRYKER_CXX_COVERAGE_PROVIDER`, `STRYKER_CXX_ARTIFACT_DIR`, and
  `STRYKER_CXX_REPO`;
- native reports include `execution.resourceIsolation` with worktree mode,
  worker count, artifact directory, retained-worktree mode, worker temp root,
  injected environment keys, and whether workers are parallel-safe;
- report artifacts redact explicit `--env` values from `config.effective`, scrub
  shell-style sensitive assignments such as `TOKEN=value` from serialized
  command/plugin strings, and record the policy under
  `execution.resourceIsolation.redaction`;
- `--worker-tmp-dir` selects the parent directory for `copy` and `git-worktree`
  worker checkouts;
- `--retain-worktrees` leaves generated `copy` and `git-worktree` workspaces in
  place in their mutated state for debugging and records the retained path per
  mutant/batch.
- `--retain-worktrees-for SURVIVED,TIMEOUT` enables retention only for selected
  native statuses and records the policy in `execution.resourceIsolation`.
- `--retained-worktree-ttl-hours 24` removes older retained `copy` or
  `git-worktree` workers under `--worker-tmp-dir` before starting a new run and
  records cleanup metadata in `execution.resourceIsolation`.

### Configuration completeness and initialization

The YAML config surface is a stable public contract for current runner options.
`stryker-cxx init` writes a starter config and unknown keys are rejected by
default.

Required behavior:

- publish a complete config schema;
- validate unknown keys by default;
- support `stryker-cxx init`;
- support build-system presets: `cmake`, `ctest`, `ninja`, `make`, `meson`,
  and `bazel`;
- support framework presets: `cmake-gtest`, `cmake-catch2`,
  `cmake-doctest`, `ninja-gtest`, `meson-catch2`, and `bazel-gtest`;
- support comments/examples for common CMake, CTest, Ninja, Bazel, and Make
  projects;
- document CLI-overrides-config precedence;
- expose effective config in the native report.
- validate JSON and YAML config fixtures through a repo-local no-network schema
  checker.

The native report should include:

- `config.path`;
- `config.hash`;
- `config.effective`.

### Packaging, release, and compatibility maturity

A standalone Stryker-family implementation needs predictable installation and
contribution surfaces.

Required behavior:

- versioned CLI contract;
- changelog and release notes;
- package smoke tests;
- platform matrix for macOS, Linux, and Windows/WSL;
- dependency policy for optional libclang mode;
- backwards-compatible report schema changes;
- contribution guide for new mutators and runner adapters;
- fixture projects that exercise real build systems.

Implemented:

- `stryker-cxx --version`;
- `CHANGELOG.md`;
- `docs/release.md`;
- tag-triggered `release-smoke` workflow running tests and `npm pack --dry-run`
  across Ubuntu, macOS, Windows, Node 20, and Node 22.
- release `publish` workflow using npm provenance;
- fixture projects under `fixtures/` for build-system adapters, framework source
  shapes, and plugin compatibility.
- explicit package contents allowlist in `package.json`;
- Node engine requirement in `package.json`;
- signing/provenance policy in `docs/signing.md`;
- dashboard export/upload policy in `docs/dashboard.md`.

Remaining work:

- configure `NPM_TOKEN` in the public repo;
- create actual signed release tags;
- run full validation and release smoke workflows.

## Ignore comments

`stryker-cxx` supports source-level suppression comments modelled on
StrykerJS and Stryker.NET:

- `// Stryker disable all: reason`
- `// Stryker restore all`
- `// Stryker disable next-line EqualityOperator: reason`
- `// Stryker restore next-line EqualityOperator`
- `// Stryker disable once Arithmetic: reason`

`next-line` and `once` both apply to the following source line. Mutator lists
are comma-separated and use `stryker-cxx` mutator names. Ignored mutants remain
in native and MTE reports with status `IGNORED` / `Ignored`, carry
`ignoreReason`, do not execute, and are excluded from score calculation.

## Analysis modes

Token mode is the production path today. It must stay deterministic, skip
comments and string/character literals, avoid preprocessor lines, and avoid
common template punctuation traps.

Clang mode parses the translation unit with libclang, reuses deterministic
source-level discovery, and keeps only mutants whose source span is inside a
mutator-appropriate AST cursor such as `BINARY_OPERATOR`, `RETURN_STMT`, or
`CALL_EXPR`. It records the confirming `nodeKind` in the report. Token mode
remains the dependency-free default.

## Safety requirements

- Refuse dirty target files in `inplace` mode unless `--allow-dirty` is set.
- Restore target files after each mutation.
- Avoid resetting unrelated files.
- Keep parallel mutation out of `inplace` mode.
- Support `copy` and `git-worktree` isolation.
- Write logs under a configurable artifact directory.
- Preserve enough report data to resume completed mutants.

## Required proof tests

The test suite must prove:

- direct CLI run writes `stryker-cxx.report.v1`;
- direct MTE output validates;
- source files are restored after mutation;
- survivor, killed, timeout, and no-mutant exit paths;
- `list-mutants` and `run-mutant`;
- dirty-tree refusal;
- resume preserves completed results;
- `copy` and `git-worktree` modes;
- sharding behavior;
- markdown, SARIF, and HTML artifact generation;
- dashboard payload version, retention, provenance, and upload-auth metadata;
- Stryker ignore comments and ignored MTE/native statuses;
- plugin runner/checker/test provider hooks;
- plugin coverage-provider hooks;
- fixture plugin-directory compatibility across provider, reporter, coverage,
  and token-mutator manifests;
- helper-generated test-level coverage selection;
- report-level `execution.resourceIsolation` metadata;
- `CallRemoval` statement-level discovery;
- opt-in `IntegerLiteral` and `NullLiteral` catalog entries;
- clang-mode behavior on compile-database fixtures;
- clang-ast direct source-range behavior for parenthesized boolean returns;
- macro-expansion rejection diagnostics for clang-backed candidate filtering.

Current tests cover CLI/report basics, timeout, copy mode, git-worktree mode,
dirty refusal, resume, direct MTE output, sharding, markdown/SARIF/HTML
artifacts, source ignore comments, `CallRemoval`, clang AST classifier behavior,
opt-in literal mutators, checker failures, coverage-driven `NO_COVERAGE`, an
optional real-libclang fixture, helper-generated test-level coverage,
plugin-directory compatibility, clang-ast direct return source-range mutation,
and JS MTE adapter behavior.

### Completion evidence for full parity (required before claiming full spec)

- `npm test`
- `npm run validate:full-spec`
- `npm run schema:check`
- `npm pack --dry-run`
- `npm run validate:stryker-cxx-provider` passes in `marmorkrebs` against a
  real `stryker-cxx` binary or binary path.
- `marmorkrebs` and `stryker-cxx` docs remain consistent around:
  - supported `--tool` path (`marmorkrebs` + `--tool stryker-cxx`);
  - payload boundary (`stryker-cxx.report.v1` as native input to orchestration);
  - status meaning (`KILLED/SURVIVED/BUILD_ERROR/CHECK_ERROR/NO_COVERAGE/TIMEOUT/IGNORED/RUNTIME_ERROR` as native).
- No PR flow uses `cxx-mutant` as the production provider; legacy references are
  migration notes only.

Mutator-specific examples and noise profiles live in
[`docs/mutators.md`](mutators.md).
