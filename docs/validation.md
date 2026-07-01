# Full-spec validation

Use this when checking whether `stryker-cxx` is ready to treat as a complete
Stryker-style C++ runner for local PR-gate use.

```bash
npm run validate:full-spec
```

The validation entrypoint is cross-platform (`node scripts/validate-full-spec.mjs`).
The shell script is retained as a Unix convenience wrapper.

The script runs:

- JS and Python contract tests;
- syntax lint across CLI and script JS plus Python module compile checks;
- no-network JSON Schema validation for JSON and YAML config fixtures;
- native report JSON Schema checks for required `toolVersion`, summary buckets,
  execution-mode metadata, and dashboard upload-status metadata;
- `npm pack --dry-run`;
- whitespace checks via `git diff --check`;
- CLI version and config-init smoke checks;
- execution-mode CLI/config checks for source-overlay, mutant-switch fallback,
  and invalid values;
- `npm run evidence:p0` generates temporary reports proving mutant-switch
  execution, mutant-switch fallback to source-overlay, compile-pruning, and
  lifecycle/restoration metadata;
- `npm run evidence:p1` generates temporary artifacts proving list-mutants
  source-range/rewrite metadata, expanded C++/ObjC++/Metal catalog discovery,
  conservative/off/aggressive equivalent-suppression behavior,
  compile-database project ownership, and deterministic batched
  coverage-selected scheduler metadata. If optional Python clang bindings are
  available, it also proves `clang-ast` discovery exposes `nodeKind`,
  `sourceRange`, and `rewriteStrategy`;
- `npm run evidence:p2` generates temporary artifacts proving local-only plugin
  lifecycle/reporter behavior, capability-version rejection, redacted plugin
  command metadata, CI/release workflow coverage, package allowlist policy, and
  standalone/Marmorkrebs boundary documentation;
- config preset checks for build-system, Xcodebuild, and framework starter
  files;
- plugin compatibility checks for mutators, reporter hooks, reporter metadata,
  runner/checker/test provider hooks, config-loader plugins, coverage-provider
  hooks, fixture plugin directories, and resource-isolation report metadata;
- resource-control checks for explicit environment injection, retained copy
  worktrees, per-status retention policies, retained-worktree cleanup TTL,
  worker labels, and custom worker temp roots;
- inherited-environment allow/block policy checks for build/check/test commands;
- report redaction checks proving explicit env values and sensitive
  shell-style assignments are not serialized into report artifacts;
	- opt-in literal mutator checks for `IntegerLiteral`, `NullLiteral`,
	  `CharacterLiteral`, `FloatingPointLiteral`, and `StringLiteral`;
	- expanded C++/ObjC++/Metal catalog checks for `ConditionalBoundary`,
	  `ShiftOperator` including compound shifts, `StandardLibraryCall`,
	  `MemoryOrder`, `UnaryOperator` sign rewrites, `MemberAccessOperator`, `ExceptionHandling`,
	  `PreprocessorGuard`, `ObjCMessageSend`, `ObjCBoolLiteral`, modulo
	  arithmetic/compound-assignment rewrites, bitwise XOR alternatives and
	  compound-assignment rewrites, `MetalThreadPosition`, and
	  `MetalAddressSpace`;
	- conservative equivalent/noise suppression checks for generated/identity-style,
	  duplicate logical/bitwise operand, duplicate conditional branches, and
	  duplicate standard-library operand cases plus disabled-suppression
	  execution;
	- baseline policy checks for branch mismatches, max-age expiry, and explicit
	  cache-miss reasons;
- baseline maintenance checks for merge, info, history, prune, by-day buckets,
  and repo file-existence diagnostics;
- parallel batch-probe checks for isolated worktree batching and compiled
  artifact batching, including stable per-mutant report ordering, conservative
  proximity heuristics, and per-artifact placement-lock metadata;
- framework adapter checks for automatic repo-local test-binary discovery;
- Xcodebuild adapter checks for workspace/project, scheme, configuration, SDK,
  destination, only-testing, and skip-testing command synthesis;
- XCTest adapter checks for destination, only-testing, and skip-testing command
  synthesis;
- checker adapter checks for `clang`/`clang++ -fsyntax-only`, `clang-tidy`,
  and `cppcheck` command synthesis;
- test-level coverage selection checks using coverage-provided covering tests;
- helper-generated test-level coverage checks using per-test coverage exports
  and per-mutant selected commands;
- clang-ast direct source-range checks for parenthesized boolean returns,
  ternary conditional-expression branch swaps, integer/null/character/
  floating-point/string rewrites, statement removals, and block removals when
  libclang is available;
- the CI `optional libclang fixture` job installs Python `libclang`, runs
  `npm test`, and runs `npm run evidence:p1` so the optional `clang-ast`
  proof is enforced in at least one workflow environment;
- macro-expansion rejection diagnostic checks for clang-backed discovery;
- dashboard payload policy checks for version, retention, upload-auth metadata,
  upload outcome metadata, CI project/branch/commit/build URL provenance fields,
  typed threshold-band status, and fallback provenance;
- Markdown, including per-mutator summary tables, SARIF, HTML, GitHub
  annotation severity mapping, and Mutation Testing Elements artifact
  generation checks;
- fixture smokes for CMake/CTest, Ninja, Make, Meson, and Bazel when the
  corresponding tools are installed.

Optional fixture tools are skipped when missing. Core contract, schema, package,
and whitespace checks are mandatory.

For a release, run this before creating a signed tag and before publishing
through the provenance workflow.
