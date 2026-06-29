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
- `npm pack --dry-run`;
- whitespace checks via `git diff --check`;
- CLI version and config-init smoke checks;
- config preset checks for build-system and framework starter files;
- plugin compatibility checks for mutators, reporter hooks, runner/checker/test
  provider hooks, coverage-provider hooks, fixture plugin directories, and
  resource-isolation report metadata;
- resource-control checks for explicit environment injection, retained copy
  worktrees, per-status retention policies, retained-worktree cleanup TTL, and
  custom worker temp roots;
- inherited-environment allow/block policy checks for build/check/test commands;
- report redaction checks proving explicit env values and sensitive
  shell-style assignments are not serialized into report artifacts;
- opt-in literal mutator checks for `IntegerLiteral` and `NullLiteral`;
- baseline policy checks for branch mismatches, max-age expiry, and explicit
  cache-miss reasons;
- baseline maintenance checks for merge, info, prune, and repo file-existence
  diagnostics;
- parallel batch-probe checks for isolated worktree batching with stable
  per-mutant report ordering;
- framework adapter checks for automatic repo-local test-binary discovery;
- XCTest adapter checks for destination, only-testing, and skip-testing command
  synthesis;
- checker adapter checks for `clang-tidy` and `cppcheck` command synthesis;
- test-level coverage selection checks using coverage-provided covering tests;
- helper-generated test-level coverage checks using per-test coverage exports
  and per-mutant selected commands;
- clang-ast direct source-range checks for parenthesized boolean returns when
  libclang is available;
- macro-expansion rejection diagnostic checks for clang-backed discovery;
- dashboard payload policy checks for version, retention, upload-auth metadata,
  and provenance fields;
- Markdown, SARIF, HTML, GitHub annotation, and Mutation Testing Elements
  artifact generation checks;
- fixture smokes for CMake/CTest, Ninja, Make, Meson, and Bazel when the
  corresponding tools are installed.

Optional fixture tools are skipped when missing. Core contract, schema, package,
and whitespace checks are mandatory.

For a release, run this before creating a signed tag and before publishing
through the provenance workflow.
