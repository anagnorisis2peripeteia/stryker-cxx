# stryker-cxx spec

`stryker-cxx` is a standalone Stryker-style mutation runner for C, C++,
Objective-C++, and optionally Metal shader sources. Marmorkrebs is only an
orchestrator/consumer; this repository owns C++ mutation execution and native
reporting.

## Parity target

Parity means matching the parts of Stryker/Stryker.NET that matter for PR gates:

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

## Implemented surface

- `stryker-cxx run`
- `stryker-cxx list-mutants`
- `stryker-cxx run-mutant`
- `--base`, `--lines`, `--include`, `--exclude`
- `--mutators`, `--max-mutants`, `--include-metal`
- `--mode token`
- `--mode clang` using libclang parse validation and AST-confirmed source mutations
- `--timeout`
- `--jobs` with non-inplace worktree modes
- `--worktree-mode inplace|copy|git-worktree`
- `--allow-dirty`
- `--resume`
- `--shard-index`, `--shard-total`
- `--format json|markdown|html|sarif|mutation-testing-elements`
- `--threshold`, `--fail-on-empty`
- `stryker-cxx.yml` / `.stryker-cxx.yml` config loading
- source-level `// Stryker disable|restore` ignore comments with
  `next-line` and Stryker.NET-style `once` aliases

## Native report

The canonical native report is `stryker-cxx.report.v1`.

Required top-level fields:

- `schemaVersion = "stryker-cxx.report.v1"`
- `tool = "stryker-cxx"`
- `repo`, `base`
- `startedAt`, `completedAt`
- `threshold`
- `totalMutants`, `killed`, `survived`, `buildErrors`, `timeouts`, `ignored`
- `score` as a `0.0..1.0` fraction
- `execution.mode`, `execution.worktreeMode`, `execution.jobs`
- `commands.build`, `commands.test`
- `targetFiles`
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
- `detail`
- `nodeKind`
- `run.reproCommand`

Native statuses:

- `KILLED`
- `SURVIVED`
- `BUILD_ERROR`
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
- `CallRemoval`

Still missing for parity:

- no known first-parity gaps in the repository spec; remaining work is broader
  ecosystem hardening against real downstream repositories

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
- Stryker ignore comments and ignored MTE/native statuses;
- `CallRemoval` statement-level discovery;
- clang-mode behavior on compile-database fixtures.

Current tests cover CLI/report basics, timeout, copy mode, git-worktree mode,
dirty refusal, resume, direct MTE output, sharding, markdown/SARIF/HTML
artifacts, source ignore comments, `CallRemoval`, clang AST classifier behavior,
an optional real-libclang fixture, and JS MTE adapter behavior.

Mutator-specific examples and noise profiles live in
[`docs/mutators.md`](mutators.md).
