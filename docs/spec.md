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
[Full parity requirements](#full-parity-requirements). The lifecycle-level gap
and action plan are tracked in
[`docs/lifecycle-parity-spec.md`](lifecycle-parity-spec.md). The stricter
Stryker.NET structural target, where source overlays become a compatibility
backend and mutation runs through compiled artifacts, is tracked in
[`docs/stryker-net-structural-parity-spec.md`](stryker-net-structural-parity-spec.md).

## Implemented surface

- `stryker-cxx run`
- `stryker-cxx init`
- `stryker-cxx list-mutants`
- `stryker-cxx run-mutant`
- `--base`, `--since`, `--lines`, `--include`, `--exclude`
- `--mutators`, `--mutation-level Standard|Advanced|Complete`,
  `--max-mutants`, `--include-metal`
- `--mode token`
- `--mode clang` using libclang parse validation and AST-confirmed source mutations
- `--mode clang-ast` using AST cursor ranges before source rewrite candidate generation
- `--timeout`
- initial dry-run/original-test validation by default
- `--skip-initial-test`
- `--dry-run-only`
- `--timeout-factor`, `--timeout-constant-ms`
- `--equivalent-suppression off|conservative|aggressive`
- `--check-command`
- `--check-system clang-tidy|cppcheck`, `--check-args`
- `--skip-tests`
- `--coverage-file`, `--coverage-analysis`, `--coverage-provider`
- `--execution-backend auto|source-overlay|mutant-switch|compiled-artifact|llvm-switch`
- `--coverage-helper-command-template`, `--coverage-helper-tests`
- `--incremental`
- `--baseline-file`, `--write-baseline`, `--clear-baseline`
- `parity-audit`
- `--batch-mutants`, `--batch-size`
- local plugin manifests via `--plugin` / `--plugin-dir`
- plugin-contributed token mutators
- reporter selection metadata via `--reporter`
- build/test adapter synthesis via
  `--build-system cmake|ctest|ninja|make|meson|bazel|xcodebuild`
- adapter controls: `--build-dir`, `--build-target`, `--test-target`,
  `--test-filter`, `--xcode-workspace`, `--xcode-project`, `--xcode-scheme`,
  `--xcode-configuration`, `--xcode-sdk`, `--xcode-destination`,
  `--artifact-path`
- framework adapter controls: `--test-framework gtest|catch2|doctest|xctest`,
  `--test-binary`, `--xctest-bundle`, `--xctest-destination`,
  `--xctest-only-testing`, `--xctest-skip-testing`
- baseline maintenance commands: `baseline-info`, `baseline-history`,
  `baseline-merge`, `baseline-prune`
- dashboard export/upload: `--dashboard-export`, `--dashboard-upload-url`
- executable plugin lifecycle hooks: `initialization`, `projectAnalysis`,
  `mutationDiscovery`, `artifactCreation`, `coverageAnalysis`, `scheduling`,
  `execution`, `reporting`, `cleanup`, plus legacy `preRun` / `postRun`
  aliases and reporter commands
- explicit `execution.reporterRuns[]` diagnostics for requested reporters that
  no loaded plugin reporter command provides
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
- Native status vocabulary (`KILLED`, `SURVIVED`, `BUILD_ERROR`,
  `CHECK_ERROR`, `NO_COVERAGE`, `TIMEOUT`, `IGNORED`, `RUNTIME_ERROR`) and
  command surface (`run`, `run-mutant`, `list-mutants`) stay stable at the seam.
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
- `toolVersion`
- `repo`, `base`
- `startedAt`, `completedAt`
- `threshold`
- `thresholds.high`, `thresholds.low`, `thresholds.break`,
  `thresholds.status`
- `timeoutSeconds`
- `totalMutants`, `killed`, `survived`, `buildErrors`, `checkErrors`,
  `noCoverage`, `timeouts`, `ignored`
- `score` as a `0.0..1.0` fraction
- `execution.mode`, `execution.executionMode`, `execution.worktreeMode`,
  `execution.jobs`
- `execution.analysis.engine`
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
- `ShiftOperator`
- `UpdateOperator`
- `ConditionalExpression`
- `UnaryOperator`
- `ReturnValue`
- `IntegerLiteral`
- `NullLiteral`
- `CharacterLiteral`
- `FloatingPointLiteral`
- `StringLiteral`
- `CallRemoval`
- `StatementRemoval`
- `BlockRemoval`
- `LoopBoundary`
- `LoopCondition`
- `StandardLibraryCall`
- `MoveSemantics`
- `StringCall`
- `MathCall`
- `IteratorCall`
- `ChronoCall`
- `RegexCall`
- `FilesystemCall`
- `MemoryOrder`
- `MemberAccessOperator`
- `ExceptionHandling`
- `PreprocessorGuard`
- `ObjCMessageSend`
- `ObjCBoolLiteral`
- `MetalThreadPosition`
- `MetalAddressSpace`
- clang-ast direct source-range candidates for single-line boolean literals and
  boolean returns, single-operator binary expression rewrites, ternary
  conditional-expression branch swaps, loop-boundary/condition rewrites,
  statement removals, block removals, and C++ call-family rewrites

Open-ended ecosystem parity boundaries:

- richer mixed-mutant batching heuristics beyond current source-proximity,
  source-structure isolation, split attribution, and isolated parallel batch
  probes;
- richer C/C++ mutator catalog variants beyond the current conditional-boundary,
  operator, statement, literal, standard-library, string/search call,
  math call, iterator call, chrono call, regex call, filesystem call,
  memory-order, member-access, preprocessor, ObjC++, and Metal families;
- richer AST-first source rewrite coverage and macro-safe rewrites;
- richer plugin compatibility surface beyond local command-provider hooks;
- richer checker/build-system adapters beyond the current generic, Xcode,
  checker, and provider hooks;
- richer hosted-dashboard service protocol and CI annotation polish beyond the
  current explicit upload auth/provenance/retention conventions;
- deeper project integrations beyond generic build/test framework adapters;
- richer resource-isolation controls beyond retained worktree policies, custom
  worker temp roots, and explicit env injection;
- live npm token setup, signed tags, and production hosted-dashboard service
  operations.

## Full-spec parity matrix (explicit boundary view)

This matrix separates the implemented local engine roadmap from broader
Stryker-family ecosystem parity boundaries.

### Public-repo parity posture (2026-06-29)

- The items in the ✅ section are required for current standalone PR-gate use and
  are intended to be stable.
- The ⚠️ section names broader Stryker-family or language-ecosystem boundaries.
  They are not required for the validated local engine roadmap unless promoted
  into concrete acceptance criteria.

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
- CMake/CTest compiled-artifact backends for executable, library, and object
  materialization, including artifact swapping, restoration-by-hash, and
  compiled batch sessions.
- Project analysis maps CMake/CTest, Ninja, Make, Meson, Bazel, and Xcode
  fixture sources to build targets, relates discovered test targets to the build target,
  emits `projectAnalysis.buildGraph` source/build/test node ownership evidence
  where the build metadata exposes that relationship, and emits
  deterministic analysis/ownership keys for baseline and resume stability.
- Explicit `--artifact-fallback source-overlay` downgrade for unsupported
  compiled-artifact requests, with requested/actual backend and fallback reason
  recorded in the native report.
- Plugin surfaces for token mutators, reporter hooks, and runner/checker/test
  providers.
- Config-loader plugin capability via `capabilities.configLoader`, applying
  JSON manifests before defaults are finalized.
- Basic dashboard upload/export policy plus explicit auth/env redaction metadata.
- PR/CI-facing reporting including Markdown/HTML/SARIF/GitHub annotations and
  mutation element projection.
- Core mutator families and ignore-comment support used by `stryker-cxx`/Marmorkrebs.
- Conservative equivalent/noise suppression for generated-code markers,
  duplicate logical/bitwise operands, duplicate conditional branches,
  arithmetic identity rewrites, and duplicate-operand standard-library min/max
  calls, with explicit `off` and `aggressive` modes.
- Concrete CI validation workflow entry (`npm run validate:full-spec`) and
  contract-schema checks in-repo without network.

### ⚠️ explicit parity boundaries beyond the implemented engine roadmap

- Full mutator breadth beyond the implemented focused families is not complete.
- Equivalent-mutant and deeper equivalent-noise suppression includes conservative
  built-in filters, but perfect equivalent-mutant detection is not claimed.
- Advanced test orchestration features beyond local command synthesis are still
  minimal (no built-in multi-process distributed runner framework).
- Objective-C++ and Metal AST-native candidate synthesis covers selected message
  sends, Objective-C boolean literals, thread-position attributes, and Metal
  address-space qualifiers, but remains partial and still follows the token-path
  default for most runs.
- External ecosystem parity items that are tool-family dependent (for example
  full StrykerJS/Stryker.NET reporter ecosystems, plugin hosts, and hosted dashboard
  orchestration semantics) remain intentionally outside this repo’s responsibility.
- Single-compilation Mull-style mutant-switch execution is implemented for safe
  expression-like literals, return-value mutants, token-mode unary expression
  spans for logical-not and sign flips, token-mode binary expression spans with
  simple, call, or parenthesized operands, token-mode conditional-expression
  branch swaps, token-mode loop-boundary and loop-condition spans,
  token-mode update-expression spans for prefix/suffix increment and decrement,
  expression-statement and single-line block removals, statement-level call removals,
  throw-statement removals, Objective-C++ message-send removals,
  standard-library call substitutions,
  move-semantics call-wrapper removals, math call substitutions, iterator call
  substitutions, chrono call
  substitutions, regex predicate substitutions, filesystem predicate
  substitutions, container member-call substitutions, container state/capacity
  substitutions, string search substitutions, memory-order constants, and
  single-line clang-ast binary expression spans.
  Same-span alternatives are emitted as chained runtime guards; ambiguous or
  partially overlapping operator spans still fall back because switching only an
  operator token is not valid C++.
- Source-overlay-only fallback mutators still report structured source ranges and
  rewrite strategies for auditability, including member-access, preprocessor
  guard, Metal thread-position, and Metal address-space rewrites.
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

### P0 implementation tickets

These tickets are the first blocking slice for Stryker/Stryker.NET parity. P0
completion means the engine has moved from source-rewrite execution as the only
real model to an explicit lifecycle with a switchable compiled mutation
artifact path, while retaining source overlay as the compatibility fallback.

#### P0-1: Add explicit execution-mode selection

Files:

- `python/stryker_cxx/cli.py`
- `python/stryker_cxx/engine.py`
- `python/stryker_cxx/schema.py`
- `python/stryker_cxx/payload_contract.py`
- `tests/python/test_cli_contracts.py`
- `tests/python/test_schema_contracts.py`

Implementation work:

- add `--execution-mode source-overlay|mutant-switch` to the CLI;
- add `execution.executionMode` to YAML config under `execution`;
- keep `source-overlay` as the default and preserve existing `--mode token|clang|clang-ast`
  as the analysis engine selector;
- treat current `execution.mode` config/report usage as a legacy analysis-engine
  alias and normalize new reports to `execution.analysis.engine`;
- reject unknown execution modes during config/CLI validation;
- emit `execution.executionMode`, `execution.mutantSwitch.enabled`, and
  `execution.mutantSwitch.fallbackReason` in `stryker-cxx.report.v1`;
- project execution-mode metadata into MTE custom metadata without changing MTE
  status semantics.

Acceptance criteria:

- existing source-overlay runs produce behavior-compatible results except for
  additive report metadata;
- `--execution-mode mutant-switch` reaches a distinct engine path even before
  full optimization is enabled;
- unsupported projects downgrade to `source-overlay` with an explicit fallback
  reason instead of silently behaving like source overlay;
- config-schema validation covers both valid modes and invalid values.

Test cases:

- CLI accepts `--execution-mode source-overlay`;
- CLI accepts `--execution-mode mutant-switch`;
- CLI rejects an unknown execution mode;
- reports contain deterministic execution-mode metadata;
- legacy reports without execution-mode metadata still validate.

#### P0-2: Introduce a mutant-switch artifact model

Files:

- `python/stryker_cxx/mutation_artifacts.py`
- `python/stryker_cxx/engine.py`
- `python/stryker_cxx/project_analysis.py`
- `python/stryker_cxx/schema.py`
- `fixtures/`
- `tests/python/test_cli_contracts.py`

Implementation work:

- add a `mutant-switch` mutation artifact descriptor alongside
  `source-overlay` and existing compiled-artifact metadata;
- represent a compiled switch artifact as one run unit containing many guarded
  mutant injection points;
- add deterministic guard IDs derived from the existing mutant ID, mutator, file,
  span, original text, and replacement text;
- define activation by environment variable first, for example
  `STRYKER_CXX_ACTIVE_MUTANT=<mutant-id>`;
- record artifact path, guard count, activation method, and fallback reason;
- keep Metal and unsupported source constructs on `source-overlay` until a safe
  guard strategy exists.

Acceptance criteria:

- source-overlay materialization remains unchanged;
- mutant-switch materialization can be selected and reported independently;
- generated guard IDs are stable across `list-mutants`, `run`, resume, and
  `run-mutant`;
- unsupported files or build systems fall back with report evidence.

Test cases:

- artifact metadata reports `executionMode: mutant-switch`;
- guard IDs remain stable across two discovery runs;
- unsupported Metal fixture falls back to `source-overlay` with a specific reason;
- retained artifacts include mutant-switch metadata when enabled.

#### P0-3: Build the first single-compile runner path

Files:

- `python/stryker_cxx/engine.py`
- `python/stryker_cxx/mutation_artifacts.py`
- `python/stryker_cxx/build_adapters.py`
- `fixtures/`
- `tests/python/test_cli_contracts.py`

Implementation work:

- generate a guarded source overlay for supported expression-level mutants;
- compile that guarded overlay once for the selected run unit;
- run the test command once per mutant with only that mutant ID active;
- preserve existing status mapping for `KILLED`, `SURVIVED`, `TIMEOUT`,
  `NO_COVERAGE`, `CHECK_ERROR`, `BUILD_ERROR`, and `RUNTIME_ERROR`;
- fall back to source-overlay for mutators that cannot be safely guarded;
- make `run-mutant` activate a single guard in the compiled artifact path.

Acceptance criteria:

- a fixture with multiple simple C++ mutants performs one build/check phase for
  the switch artifact and multiple test sessions;
- per-mutant results match source-overlay results on the same fixture;
- the report proves the compile count, active mutant selector, and artifact path;
- source-overlay remains the default until switch mode is explicitly selected.

Test cases:

- Boolean/return/literal plus token-mode and clang-ast binary operator mutants
  compile once and run as separate active mutants;
- killed/survived outcomes match equivalent source-overlay fixture run;
- timeout classification works in mutant-switch mode;
- `run-mutant <id>` uses the same active-mutant selector metadata.

#### P0-4: Formalize compile-pruning for switch artifacts

Files:

- `python/stryker_cxx/engine.py`
- `python/stryker_cxx/mutation_artifacts.py`
- `python/stryker_cxx/schema.py`
- `tests/python/test_cli_contracts.py`

Implementation work:

- compile the candidate mutant-switch artifact before test execution;
- when compile/check fails, identify the failing mutant where possible;
- remove or disable compile-failing guards from the candidate set;
- recompile until the switch artifact is valid or no runnable mutants remain;
- record pruned mutants separately from tested mutants with native reason
  metadata while projecting to compatible MTE statuses.

Acceptance criteria:

- compile-invalid mutants do not prevent valid mutants in the same artifact from
  being tested;
- compile-pruned mutants carry native reason metadata and deterministic IDs;
- report summary separates compile-pruned counts from test-executed counts;
- MTE output remains compatible with existing consumers.

Test cases:

- one compile-failing mutant and one valid mutant produce one pruned result and
  one tested result;
- pruning retries are counted in `execution.compilePruning`;
- fallback to source-overlay preserves compile-error status for unsupported
  attribution.

#### P0-5: Make coverage and scheduling explicit phases

Files:

- `python/stryker_cxx/engine.py`
- `python/stryker_cxx/schema.py`
- `docs/contract.md`
- `tests/python/test_cli_contracts.py`
- `tests/python/test_schema_contracts.py`

Implementation work:

- always emit lifecycle phase metadata for discovery, artifact creation,
  compile pruning, coverage analysis, scheduling, execution, restoration, and
  reporting;
- run coverage analysis before scheduling even when no coverage provider is
  configured;
- make scheduler output explicit: per-mutant sessions, coverage-selected
  sessions, batch sessions, split sessions, and fallback sessions;
- apply the same scheduler metadata to source-overlay and mutant-switch modes.

Acceptance criteria:

- no-coverage mutants are identified before test execution;
- unknown coverage is distinct from no coverage;
- coverage-selected test commands are recorded per mutant/session;
- source-overlay and mutant-switch reports use the same lifecycle shape.

Test cases:

- no coverage provider records `coverage.provider: none` and unknown coverage;
- supplied coverage marks uncovered mutants `NO_COVERAGE` before execution;
- coverage-selected sessions are reported in deterministic order;
- lifecycle metadata validates in old and new reports.

#### P0-6: Restore and retain artifacts at the artifact layer

Files:

- `python/stryker_cxx/mutation_artifacts.py`
- `python/stryker_cxx/engine.py`
- `python/stryker_cxx/schema.py`
- `tests/python/test_cli_contracts.py`

Implementation work:

- restore original source, object, library, executable, or overlay artifacts
  after each run unit;
- record restoration strategy, restored paths, retained paths, and cleanup
  outcome in the report;
- preserve `--retain-worktrees`, `--retain-worktrees-for`, and TTL behavior;
- extend retention to mutant-switch artifacts for survivor/timeout proof.

Acceptance criteria:

- original artifacts are restored after source-overlay and mutant-switch runs;
- retained artifact paths are opt-in and reported;
- cleanup failures are reported as infrastructure failures, not mutant results;
- proof-oriented retained artifacts can be tied back to a mutant ID and session.

Test cases:

- source files are restored after source-overlay execution;
- generated switch artifact is cleaned up by default;
- `--retain-worktrees-for SURVIVED,TIMEOUT` retains matching artifacts only;
- restoration metadata validates through `schema.py`.

### P0 completion evidence

Do not claim P0 completion until all of this evidence exists:

- `npm test`;
- `npm run validate:full-spec`;
- `npm run schema:check`;
- `npm run evidence:p0`;
- a fixture report showing `execution.executionMode: mutant-switch`;
- a fixture report showing fallback from `mutant-switch` to `source-overlay`;
- a fixture report showing compile-pruning under `mutant-switch`;
- a fixture report showing lifecycle phases from discovery through restoration;
- a Marmorkrebs provider validation against the local `stryker-cxx` binary after
  the report contract changes land.

### P1 implementation tickets

P1 work expands correctness and breadth after the P0 execution lifecycle exists.
These tickets should not block the first mutant-switch engine path, but they are
required before claiming broad Stryker-family parity.

#### P1-1: Expand AST-first mutator coverage

Files:

- `python/stryker_cxx/engine.py`
- `python/stryker_cxx/schema.py`
- `docs/mutators.md`
- `fixtures/`
- `tests/python/test_cli_contracts.py`

Implementation work:

- generate AST-native candidates for more binary, unary, call, return, branch,
  assignment, loop, literal, and statement forms;
- keep token mode as fallback but prefer AST source ranges when available;
- extend ObjC++ and Metal coverage only where source ranges and semantics are
  reliable;
- document examples, risks, and language support for every new mutator shape;
- report `nodeKind`, `rewriteStrategy`, and `sourceRange` consistently.

Acceptance criteria:

- AST and token modes produce stable IDs for equivalent source locations;
- macro-expanded or ambiguous ranges are rejected with diagnostics;
- new mutators include fixture coverage and mutator documentation;
- generated candidates avoid comments, strings, macros, and unrelated text.
- `list-mutants` exposes native `nodeKind`, `rewriteStrategy`, and
  `sourceRange` metadata so orchestration layers can reason about the same
  mutation source boundaries that reports use.

Test cases:

- AST-first branch/return/literal candidates mutate only the intended range;
- ObjC++ and Metal candidates are accepted only for supported safe forms;
- macro expansion candidates are rejected and reported;
- MTE projection preserves the native mutator names.
- `npm run evidence:p1` proves the public discovery metadata surface and skips
  only the optional `clang-ast` runtime proof when Python clang bindings are not
  installed.

#### P1-2: Improve equivalent/noise suppression

Files:

- `python/stryker_cxx/engine.py`
- `python/stryker_cxx/schema.py`
- `docs/mutators.md`
- `tests/python/test_cli_contracts.py`

Implementation work:

- expand conservative suppression for high-confidence equivalent or duplicate
  mutants;
- keep `--equivalent-suppression off|conservative|aggressive` behavior stable;
- record suppression rules and reasons in native reports;
- avoid hiding mutants unless the rule is deterministic and explainable.

Acceptance criteria:

- conservative mode suppresses only high-confidence equivalent/noise cases;
- aggressive mode is visibly opt-in and reports every extra suppression reason;
- disabled mode emits all discovered candidates except explicit ignore comments;
- score calculations exclude ignored/suppressed mutants consistently.

Test cases:

- duplicate logical/bitwise/conditional mutants are ignored in conservative mode;
- duplicate standard-library operands and duplicate standard-library ranges are
  ignored in conservative mode when the duplicated arguments are pure;
- aggressive-only rules do not run in conservative mode;
- `off` exposes candidates that conservative/aggressive would suppress;
- reports list suppression mode, count, and per-mutant reason.
- `npm run evidence:p1` proves conservative duplicate/identity suppression,
  disabled suppression, and aggressive-only null-literal suppression through
  native reports.

#### P1-3: Deepen project and target discovery

Files:

- `python/stryker_cxx/project_analysis.py`
- `python/stryker_cxx/build_adapters.py`
- `python/stryker_cxx/engine.py`
- `python/stryker_cxx/schema.py`
- `fixtures/`
- `tests/python/test_cli_contracts.py`

Implementation work:

- map source files to build targets using compile databases and build-system
  metadata where available;
- distinguish target projects, test projects, test binaries, and test filters;
- improve CMake/CTest, Ninja, Make, Meson, Bazel, and Xcode confidence metadata;
- preserve explicit user-supplied commands as authoritative overrides;
- emit target/test ownership confidence in `projectAnalysis`.

Acceptance criteria:

- explicit command workflows continue to work unchanged;
- supported fixture projects report target/test discovery metadata;
- unknown projects degrade to explicit command mode with clear diagnostics;
- analysis results are stable enough for baseline and resume keys.

Test cases:

- CMake/CTest fixture maps sources to test command metadata;
- CMake `target_sources(...)` fixtures map source files to owning targets;
- Meson library fixtures map source files to owning targets;
- nested Bazel package fixtures map source files to owning targets;
- nested Bazel `cc_test` fixtures map `deps` to related build targets;
- Xcode unit-test fixtures map `PBXTargetDependency` to related build targets;
- compile-database fixture reports owning compile commands;
- Bazel/Meson/Ninja/Make fixtures report best-effort build/test targets and
  source ownership;
- explicit `--build-command` and `--test-command` override discovery.
- `npm run evidence:p1` proves compile-database source ownership in
  `projectAnalysis`.

#### P1-4: Extract a reusable test-session scheduler

Files:

- `python/stryker_cxx/engine.py`
- `python/stryker_cxx/schema.py`
- `tests/python/test_cli_contracts.py`

Implementation work:

- make per-mutant, coverage-selected, batched, split, and fallback sessions part
  of one deterministic scheduler model;
- keep scheduling independent from mutation materialization where practical;
- record planned batch composition, session IDs, mutant IDs, selected commands,
  and split causes;
- preserve deterministic report ordering across parallel workers.

Acceptance criteria:

- source-overlay and mutant-switch modes use the same scheduler metadata shape;
- failed optimized sessions split into attributable per-mutant sessions;
- `--jobs` output remains deterministic;
- resume/baseline keys are independent of worker ordering.

Test cases:

- batch survivor sessions record all included mutant IDs;
- batch plans record stable batch ids, source locations, and the batching
  heuristic before probes execute;
- batch plans explain deterministic placement constraints such as adjacent-line,
  source-structure, and batch-size isolation;
- test-level coverage selects batch placement affinity before generic first-fit
  placement so compatible mutants with the same selected tests are grouped, then
  minimizes selected-test union growth for remaining compatible batches;
- failed batches split and report the split cause;
- coverage-selected commands appear at both session and mutant level;
- repeated parallel runs produce stable report order.
- `npm run evidence:p1` proves deterministic batched, coverage-selected
  scheduler groups with stable session IDs and planned batch metadata.

### P2 implementation tickets

P2 work is ecosystem maturity. It is still part of full Stryker-family parity,
but it should follow the engine lifecycle and correctness work.

#### P2-1: Harden plugin and reporter host parity

Files:

- `python/stryker_cxx/cli.py`
- `python/stryker_cxx/engine.py`
- `python/stryker_cxx/schema.py`
- `docs/contract.md`
- `tests/python/test_cli_contracts.py`

Implementation work:

- formalize plugin lifecycle events around discovery, artifact creation,
  coverage, scheduling, execution, reporting, and cleanup;
- version plugin manifests and reject unsupported capability versions;
- keep plugin loading deterministic and local-only;
- expose richer reporter metadata without requiring network services.

Acceptance criteria:

- plugin failures include phase, plugin name, command, and redacted environment;
- reporter hooks can consume native reports and MTE projections;
- reporter command execution is summarized under `execution.reporterRuns` with
  plugin, reporter, status, exit code, log path, and redacted environment;
- missing requested reporter commands are summarized under
  `execution.reporterRuns` with `status = "notFound"`, a reason, and the sorted
  available reporter command names;
- manifest compatibility is validated before execution starts;
- no plugin installation or network lookup happens during mutation runs.

Test cases:

- unsupported plugin capability version fails during initialization;
- reporter hook receives the final native report path;
- reporter run metadata records executed reporter hook status;
- missing requested reporter command records `status = "notFound"` without
  failing the mutation run or doing network lookup;
- plugin command secrets are redacted in reports;
- plugin order remains stable across runs.
- `npm run evidence:p2` proves local-only lifecycle events, reporter report-path
  delivery, missing requested reporter diagnostics, command redaction,
  deterministic plugin load order, and unsupported capability-version rejection.

#### P2-2: Mature package, release, and public contribution surfaces

Files:

- `README.md`
- `CONTRIBUTING.md`
- `CHANGELOG.md`
- `docs/release.md`
- `docs/signing.md`
- `.github/`
- `package.json`

Implementation work:

- keep release smoke tests aligned with the public package contract;
- document mutator contribution rules and fixture expectations;
- require changelog entries for user-facing behavior changes;
- preserve npm provenance/signing expectations;
- keep CI coverage close to Stryker/Stryker.NET repository conventions where the
  C++ runtime allows it.

Acceptance criteria:

- release dry-run succeeds on supported platforms;
- contribution docs explain how to add a mutator, adapter, reporter, and fixture;
- CI proves schema, full-spec validation, lint, package dry-run, and tests;
- package contents include only intended runtime/docs/fixtures.

Test cases:

- `npm pack --dry-run`;
- `npm run package:check`;
- release-smoke workflow validates package contents;
- docs lint/check script catches stale command names;
- fixture contribution checks run in `npm run validate:full-spec`.
- `npm run evidence:p2` proves CI/release workflow coverage, package script/file
  policy, release provenance docs, and contribution guide sections.

#### P2-3: Keep Marmorkrebs as orchestrator-only integration

Files:

- `docs/contract.md`
- `docs/spec.md`
- external: `marmorkrebs/docs/stryker-cxx-spec.md`
- external: `marmorkrebs/docs/local-vs-pr-usage.md`
- external: Marmorkrebs provider tests

Implementation work:

- keep `stryker-cxx` responsible for mutation behavior and native report shape;
- keep Marmorkrebs responsible for orchestration, PR/local scope translation, and
  output normalization into review workflows;
- forward required C++ engine options such as `--base`, `--files`, `--lines`,
  `--execution-mode`, coverage, and provider paths;
- validate against a real `stryker-cxx` binary, not only canned payloads.

Acceptance criteria:

- Marmorkrebs does not reimplement C++ mutation behavior;
- provider validation accepts `stryker-cxx.report.v1` as the native boundary;
- PR/local flows can select `mutant-switch` or fallback source-overlay behavior;
- docs are clear that C++ debate belongs in provider docs, not the Marmorkrebs
  top-level README.

Test cases:

- Marmorkrebs forwards `--lines` and `--execution-mode`;
- Marmorkrebs ingests native reports with `execution.executionMode`;
- provider validation fails on malformed native payloads;
- review output remains stable across source-overlay and mutant-switch reports.
- `npm run evidence:p2` proves the standalone repo documents Marmorkrebs as an
  orchestrator/consumer boundary. Marmorkrebs' own provider validation remains
  the external proof for option forwarding and native report ingest.

### Suggested PR slice order

1. P0-1 only: CLI/config/report contract for `executionMode` and analysis-engine
   normalization.
2. P0-2 only: mutation artifact metadata and stable guard IDs, with fallback
   reporting but no optimized execution yet.
3. P0-3 only: first guarded single-compile fixture path for simple C++ mutants.
4. P0-4 only: compile-pruning loop for switch artifacts.
5. P0-5 and P0-6 together if small enough: lifecycle phase metadata, scheduler
   metadata, restoration, and retention evidence.
6. Marmorkrebs paired PR: provider option forwarding and native report ingest for
   the new `executionMode` contract.
7. P1 tickets in independent mutator, suppression, discovery, and scheduler PRs.
8. P2 tickets after the engine behavior is stable and public-facing docs can be
   made definitive.

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
`--coverage-analysis off|all|perTest|perTestInIsolation` names the Stryker-style
coverage mode in reports. `off` disables coverage classification, `all` uses
line coverage without per-test command selection, and the `perTest` modes enable
per-mutant selected test commands when covering-test metadata is available.
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
- `--since <ref>` as the Stryker.NET-style alias for `--base <ref>`;
- `--baseline-file`;
- `--baseline-max-age-days`;
- `--baseline-branch`;
- `--write-baseline`;
- `--clear-baseline`;
- `baseline-info`, `baseline-history`, `baseline-merge`, and
  `baseline-prune` maintenance commands;
- `parity-audit --report <path>` for the report's eight-gap
  Mull/Stryker.NET parity checklist.

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
- newest-first history entries with update timestamps, branch, file, line,
  mutator, and status;
- by-day status buckets for cache age/history visualization;
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
- `execution.batching.plan`;

Current constraints:

- source-overlay batching requires `--worktree-mode copy` or
  `--worktree-mode git-worktree`; compiled-artifact batching uses scratch
  artifact builds and does not mutate checkout source;
- batch probes can run in parallel, but report entries are emitted in stable
  batch order for deterministic attribution;
- compiled-artifact batch probes can run parallel scratch builds/checks, while
  artifact placement and test execution are serialized per original artifact to
  avoid swap races;
- same-line and adjacent-line mutants in the same file are not batched;
- when test-level coverage is available, compatible mutants with the same
  selected test set are preferred for the same batch, and otherwise compatible
  batches that add fewer selected tests are preferred;
- source-structure mutators such as statement/block/call removal, preprocessor
  guard flips, exception removal, and Objective-C message-send removal are
  isolated from mixed batches;
- failed/killed batches are rerun as individual mutants.

### Single-compile mutant-switch execution (Mull-style)

`stryker-cxx` should support a runtime-mutant-switch mode to reduce compile
churn on large C++ targets, inspired by Mull and Stryker.NET's execution model.

Required behavior:

- implement `--execution-mode {source-overlay|mutant-switch}` (default `source-overlay`);
- when `mutant-switch` is enabled, compile once per run unit with all mutant
  injection points encoded as runtime-switchable flags;
- per-mutant run sets exactly one active mutant selector without re-writing source
  on disk;
- support opt-out when the project or backend cannot safely host switchable
  guards (unsupported compilers, sanitizer/linker constraints, Metal, or
  language constructs that cannot be guarded cleanly);
- preserve deterministic mutant IDs and keep IDs stable across resume/replay;
- retain fallback behavior so unsupported projects run through existing
  source-overlay semantics without false assumptions;
- explicitly report why a project was downgraded from `mutant-switch` to
  `source-overlay` for diagnosis.

Target CLI/config:

- `--execution-mode source-overlay|mutant-switch` (default `source-overlay`);
- `--execution-backend auto|source-overlay|mutant-switch|compiled-artifact|llvm-switch`
  (default `auto`);
- `execution.executionMode` in `mutation-testing-elements` and
  `stryker-cxx.report.v1`;
- `execution.executionBackend`, `execution.requestedExecutionBackend`, and
  `execution.executionBackendFallbackReason` in `stryker-cxx.report.v1`;
- `execution.llvmSwitch` records whether the experimental guarded-source switch
  backend was active and why it fell back when inactive;
- `parity` and `execution.parity` record the eight known Mull/Stryker.NET gaps,
  per-run evidence, and remaining work without claiming true LLVM IR mutation;
- `execution.mutantSwitch.enabled`;
- `execution.mutantSwitch.fallbackReason`;
- per-mutant `run.mutantSwitchEnabled` metadata;
- artifact naming/tagging for compiled mutation binaries when switch mode is active.

Suggested reporting fields:

- `execution.executionMode` (enum: `source-overlay`, `mutant-switch`);
- `execution.singleCompileEnabled`;
- `execution.singleCompile.binaryArtifact`;
- `execution.singleCompile.activationMethod`;
- `execution.mutantSwitch.fallbackReason`;
- `execution.mutantSwitch.activeMutantEnvironment`;
- `execution.mutantSwitch.runtimeGuardCount`;

Acceptance criteria:

- on `mutant-switch` mode, repeated compile step for each mutant must not be required;
- each run must execute with exactly one active mutant selector and map run result
  deterministically to one native mutant;
- no-coverage/kill/survive/timeouts semantics remain identical to `source-overlay`;
- when fallback is triggered, the run status remains correct and explicit.

### Expanded C/C++ mutator catalog

The current mutators cover the first useful set. Full parity requires broader
C/C++ semantics.

Mutation-level presets are implemented as Stryker-style default mutator sets:
`Standard` is the historical safe default, `Advanced` adds broader arithmetic,
return, literal, unary, assignment, bitwise, shift, and update families, and
`Complete` enables every built-in mutator. An explicit `--mutators` list remains
authoritative for exact PR-scope proof runs.

Implemented opt-in mutator targets:

- `ConditionalBoundary` for Stryker-style `<`/`<=` and `>`/`>=` boundary
  changes outside loop-header-specific handling;
- `StandardLibraryCall` for low-noise standard-library call substitutions such as
  `std::min(1, 2)`/`std::max(1, 2)`, selected algorithm predicates,
  `std::lower_bound(...)`/`std::upper_bound(...)`,
  `std::begin(values)`/`std::end(values)`,
  `std::sort(...)`/`std::stable_sort(...)`,
  `std::partition(...)`/`std::stable_partition(...)`, and
  `std::is_sorted(...)`/`std::is_heap(...)`;
- `MoveSemantics` for C++ `std::move(...)` and `std::forward<T>(...)`
  value-category wrapper removal;
- `ContainerCall` for no-argument C++ container member substitutions such as
  `values.front()`/`values.back()` and `values.begin()`/`values.end()`;
- `ContainerStateCall` for no-argument C++ container state/capacity member
  substitutions such as `values.empty()`/`values.size()` and
  `values.capacity()`/`values.size()`;
- `StringCall` for C++ string/search member substitutions such as
  `label.find("x")`/`label.rfind("x")` and
  `label.starts_with("a")`/`label.ends_with("a")`;
- `MathCall` for C/C++ math call substitutions such as
  `std::ceil(x)`/`std::floor(x)` and `std::round(x)`/`std::trunc(x)`;
- `IteratorCall` for C++ iterator movement substitutions such as
  `std::next(it)`/`std::prev(it)`;
- `ChronoCall` for C++ chrono rounding substitutions such as
  `std::chrono::floor<T>(d)`/`std::chrono::ceil<T>(d)`;
- `RegexCall` for C++ regex predicate substitutions such as
  `std::regex_match(text, match, re)`/`std::regex_search(text, match, re)`;
- `FilesystemCall` for C++ filesystem predicate substitutions such as
  `std::filesystem::exists(path)`/`std::filesystem::is_empty(path)` and
  `std::filesystem::is_regular_file(path)`/`std::filesystem::is_directory(path)`;
- `MemoryOrder` for C++ atomic `std::memory_order_*` and
  `std::memory_order::*` constant substitutions, with clang confirmation for
  enum-reference cursor spans;
- `UnaryOperator` for conservative unary sign flips and logical-not expression
  rewrites;
- `MemberAccessOperator` for pointer/value member-access variants;
- `ExceptionHandling` for single-line throw-statement removal;
- `PreprocessorGuard` for simple `#if 0`/`#if 1` and `#ifdef`/`#ifndef` cases;
- `ObjCMessageSend` for simple statement-level Objective-C++ message sends;
- `ObjCBoolLiteral` for Objective-C `YES`/`NO` literals;
- `MetalThreadPosition` for selected Metal thread-position attributes;
- `MetalAddressSpace` for selected Metal `device`/`constant`/`threadgroup`
  address-space qualifiers.

Post-roadmap catalog expansion is deeper breadth inside those families, richer
language-specific AST generation, and more equivalent-mutant reduction beyond
the current generated-code, duplicate-logical/bitwise, duplicate-conditional,
arithmetic-identity, and duplicate standard-library operand filters.

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
- reject macro/preprocessor cursor ranges before source rewrite candidate
  generation and record rejected ranges in `execution.analysis`;
- reject macro-expanded spans that cannot be rewritten safely;
- emit node kind and rewrite strategy in the report;
- keep token mode as a dependency-free fallback.

The report should include:

- `execution.analysis.engine = token|clang-confirmed|clang-ast`;
- `execution.analysis.sourcePrecision` summarizes source-range confidence and
  rewrite strategy coverage;
- per-mutant `nodeKind`;
- per-mutant `rewriteStrategy`;
- per-mutant `sourceRange`;
- per-mutant `sourcePrecision`;
- `execution.analysis.macroRejectedMutants`;
- `execution.analysis.macroRejections[]` diagnostics for candidates rejected
  because they overlap macro expansion ranges.

Post-roadmap AST expansion:

- broader AST cursor coverage beyond direct boolean literal/return,
  ternary conditional-expression, loop, statement, block, and literal ranges
  plus token replacements inside cursor spans;
- broader Objective-C++ and Metal AST-specific candidate generation beyond the
  current selected message-send, boolean-literal, thread-position, and
  address-space cases.

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
- Xcode workspace/project build and test command synthesis through
  `xcodebuild`, including scheme, configuration, SDK, and destination controls;
- checker command synthesis for `clang`/`clang++ -fsyntax-only`, `clang-tidy`,
  and `cppcheck`;

Compiled artifact support matrix:

- `source-overlay`: default compatibility backend for all generic command
  adapters.
- Compiled artifact backends require an explicit supported `--build-system`;
  unknown projects fail compiled-backend preflight unless the caller opts into
  `--artifact-fallback source-overlay`.
- `compiled-executable`: CMake/CTest targets and simple
  Make/Ninja/Meson/Bazel/Xcode executable targets with an existing original
  executable artifact, batch-capable with parallel scratch workers. Bazel
  requires an explicit `--build-target` label and `--test-binary` artifact path.
  Xcode requires `--test-binary` plus either `--build-target` or
  `--xcode-scheme`; scratch builds force `CONFIGURATION_BUILD_DIR` for
  deterministic artifact discovery.
- `compiled-library`: CMake/CTest library targets and explicit
  Make/Ninja/Meson library targets with an existing original `lib<target>`
  artifact, plus Bazel/Xcode library targets when `--artifact-path` names the
  original library artifact, batch-capable with parallel scratch workers.
- `compiled-object`: CMake/CTest targets with compile database object
  discovery, plus explicit Make/Ninja/Meson/Bazel targets when
  `--artifact-path` names the linked artifact and `compile_commands.json`
  proves the mutated source-to-object path, batch-capable with parallel scratch
  workers.
- Make/Ninja/Meson `compiled-library` support is explicit-target artifact
  discovery; Bazel/Xcode `compiled-library` support requires explicit
  `--artifact-path`. Xcode `compiled-object` stays unsupported until that
  adapter can prove source-to-object-to-linked-artifact ownership.

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

- explicit lifecycle hooks under `hooks.initialization`,
  `hooks.projectAnalysis`, `hooks.mutationDiscovery`, `hooks.artifactCreation`,
  `hooks.coverageAnalysis`, `hooks.scheduling`, `hooks.execution`,
  `hooks.reporting`, and `hooks.cleanup`;
- legacy `hooks.preRun` and `hooks.postRun` aliases, mapped to
  `initialization` and `cleanup`;
- reporter command hooks selected by `--reporter`;
- reporter metadata capture from plugin `reporters[].metadata` into
  `execution.reporterMetadata`;
- reporter command execution metadata capture into `execution.reporterRuns`;
- missing requested reporter command diagnostics in `execution.reporterRuns`
  using `status = "notFound"` and a sorted available-reporter list;
- build/check/test provider commands via `capabilities.runner`,
  `capabilities.buildRunner`, `capabilities.checker`, or
  `capabilities.testRunner`;
- coverage file generation/loading via `capabilities.coverageProvider`.
- configuration synthesis via `capabilities.configLoader` JSON output merged into base
  execution config before defaults.
- lifecycle metadata under `execution.pluginLifecycle`, including deterministic
  load order, registered hooks, run records, redacted command text, redacted
  environment summaries, and local-only/no-network-install policy.
- compatibility fixture tests for plugin directories, provider hooks, reporter
  hooks, coverage-provider hooks, config-loader manifests, and plugin-contributed
  token mutators.

### Reporter and dashboard maturity

Current JSON/MTE/Markdown/HTML/SARIF output is enough for early automation.
HTML is a static local dashboard with filtering/sorting and per-file summaries.
Dashboard JSON can be exported or explicitly uploaded by URL.

Required behavior:

- HTML report with sortable/filterable mutants;
- per-file and per-mutator score breakdowns, including a CI-friendly Markdown
  mutator summary table;
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

- Markdown with totals, per-mutator status summary, survivors, and ignored
  mutants;
- HTML local dashboard;
- SARIF;
- GitHub Actions log annotations via `--format github-annotations`, using
  warnings for survivors, notices for no-coverage mutants, and errors for
  build/check/timeout failures;
- direct Mutation Testing Elements JSON.
- dashboard export/upload policy with explicit upload URL, optional
  `--dashboard-auth-token-env`, dashboard payload version metadata, provenance
  fields, threshold-band status, CI project/branch/commit/run/build URL
  metadata, caller-managed retention metadata, privacy/redaction metadata,
  local export artifact metadata, retry-attempt metadata, and upload outcome
  metadata.

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
  optional worker label, injected environment keys, and whether workers are
  parallel-safe;
- report artifacts redact explicit `--env` values from `config.effective`, scrub
  shell-style sensitive assignments such as `TOKEN=value` from serialized
  command/plugin strings, and record the policy under
  `execution.resourceIsolation.redaction`;
- `--worker-tmp-dir` selects the parent directory for `copy` and `git-worktree`
  worker checkouts;
- `--worker-label pr-96205-proof` labels retained worker paths and report
  metadata for CI/proof grouping;
- `--distribution-manifest distribution.json` writes a deterministic,
  redacted shard/work manifest with selected mutants, shard identity, worker
  identity, commands, execution mode, and artifact backend so CI workers can
  archive or merge proof without a hosted coordinator. The manifest contract is
  published as `docs/schemas/stryker-cxx.distribution.schema.json`;
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

External release operations:

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

`--equivalent-suppression` controls built-in high-confidence suppression:

- `conservative` (default): generated-code markers, duplicate pure
  logical/bitwise operands, duplicate conditional branches, arithmetic identity
  rewrites, duplicate-operand standard-library min/max calls, and duplicate-range
  standard-library lower/upper-bound calls become `IGNORED`;
- `off`: every discovered mutant remains executable unless comments, coverage,
  or other explicit controls suppress it;
- `aggressive`: includes conservative rules plus generated-looking paths and
  style-equivalent null literal rewrites.

Native reports record this under `execution.analysis.equivalentSuppression`,
including `mode`, `suppressedMutants`, and per-mutant suppression reasons.

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
- sharding behavior and distribution manifest artifact generation;
- markdown, SARIF, and HTML artifact generation;
- native report schema-required `toolVersion`, `summary.byStatus`,
  `summary.byFile`, and `summary.byMutator` buckets;
- dashboard payload version, retention, privacy/redaction metadata,
  tool version provenance, threshold-band status, CI provenance, upload-auth
  metadata, local export artifact metadata, retry-attempt metadata, and upload
  outcome metadata;
- Stryker ignore comments and ignored MTE/native statuses;
- plugin runner/checker/test provider hooks;
- plugin coverage-provider hooks;
- fixture plugin-directory compatibility across provider, reporter, coverage,
  and token-mutator manifests;
- helper-generated test-level coverage selection;
- report-level `execution.resourceIsolation` metadata;
- report-level `execution.analysis.equivalentSuppression` metadata;
- `CallRemoval` statement-level discovery;
- `StatementRemoval` and `BlockRemoval` statement/block discovery;
- `LoopBoundary` and `LoopCondition` loop-header discovery;
- `ConditionalBoundary`, `StandardLibraryCall`, `MoveSemantics`,
  `ContainerCall`, `ContainerStateCall`, `StringCall`, `MathCall`,
  `IteratorCall`, `ChronoCall`, `RegexCall`, `FilesystemCall`, `MemoryOrder`, `MemberAccessOperator`,
  `ExceptionHandling`, `PreprocessorGuard`,
  `ObjCMessageSend`, `ObjCBoolLiteral`, `MetalThreadPosition`, and
  `MetalAddressSpace` catalog entries;
- opt-in `IntegerLiteral`, `NullLiteral`, `CharacterLiteral`,
  `FloatingPointLiteral`, and `StringLiteral` catalog entries;
- clang-mode behavior on compile-database fixtures;
- clang-ast direct source-range behavior for parenthesized boolean returns,
  single-operator binary expression rewrites,
  ternary conditional-expression branch swaps,
  loop-boundary and loop-condition rewrites,
  integer, null, character, floating-point, string, standard-library call,
  move-semantics call wrapper, container member-call, container state/capacity,
  and string search rewrites;
- macro-expansion rejection diagnostics for clang-backed candidate filtering.

Current tests cover CLI/report basics, timeout, copy mode, git-worktree mode,
dirty refusal, resume, direct MTE output, sharding, markdown/SARIF/HTML
artifacts, source ignore comments, `CallRemoval`, `LoopBoundary`,
`LoopCondition`, expanded opt-in C++/ObjC++/Metal catalog mutators,
clang AST classifier behavior, opt-in literal mutators,
conservative equivalent/noise suppression and disabled-suppression mode,
checker failures, coverage-driven `NO_COVERAGE`, an optional real-libclang
fixture, helper-generated test-level coverage, plugin-directory compatibility,
clang-ast direct return source-range mutation, clang-ast direct loop boundary
and condition source-range mutation, and JS MTE adapter behavior.

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
