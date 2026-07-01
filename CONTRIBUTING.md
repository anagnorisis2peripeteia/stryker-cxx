# Contributing to stryker-cxx

`stryker-cxx` is a standalone Stryker-style mutation runner for C, C++,
Objective-C++, and optional Metal shader sources. Marmorkrebs and other
orchestrators should consume this tool; C++ mutation behavior belongs here.

## Local setup

Requirements:

- Node.js 20 or newer
- Python 3.10 or newer
- Git
- Optional: `libclang` Python binding for real `--mode clang` fixture coverage

Run the default checks:

```bash
npm run lint
npm test
npm run schema:check
npm run docs:check
npm run package:check
npm pack --dry-run
git diff --check
```

Maintainers with Cameron's local review tooling can also run the optional
cheap-then-strong review helper:

```bash
scripts/review-cascade.sh
```

This is a maintainer gate helper, not part of the public `stryker-cxx` CLI or
npm package. Configure it with `STRYKER_CXX_REVIEW_BASE`,
`STRYKER_CXX_REVIEW_TARGET_REPO`, and `STRYKER_CXX_REVIEW_CEILING` when needed.

Run the optional clang fixture locally:

```bash
python -m pip install libclang
npm test
```

The clang fixture is skipped when the binding is unavailable. CI exercises it in
the `optional libclang fixture` job by installing `libclang`, running `npm test`,
and running `npm run evidence:p1` so the `clang-ast` metadata proof cannot silently
degrade to a skipped local check.

## CI and release coverage

Pull requests and pushes run the cross-platform full-spec validation matrix on
Ubuntu and macOS with Node.js 20 and 22, plus an optional libclang fixture job.

Release tags named `v*` run the same validation before publishing to npm with
provenance. Manual release workflow runs default to a dry-run package publish
unless the repository maintainer intentionally dispatches a real publish.

## Compatibility contract

Changes must preserve the first-parity contract in `docs/spec.md`:

- deterministic discovery and stable mutant IDs;
- scoped runs via files, changed lines, and explicit line ranges;
- reproducible `run-mutant` commands;
- native `stryker-cxx.report.v1` output;
- embedded or direct `mutation-testing-elements` schema `2.0` output;
- canonical statuses only;
- resumable and isolated execution modes;
- source restoration after every in-place mutation;
- Stryker-style ignore comments;
- score semantics that exclude ignored mutants.

Do not add third-party status aliases to `stryker-cxx`. Normalization of
non-native tools belongs at the consuming boundary, such as Marmorkrebs.

## Code style and conventions

- Keep CLI flags kebab-case and config/report fields lowerCamelCase.
- Keep mutation status names in the native Stryker/MTE-style uppercase set.
- Match file-local formatting: Python uses 4-space indentation and type hints;
  TypeScript, JSON, YAML, and Markdown use 2-space indentation where applicable.
- Use the committed `.editorconfig` for whitespace, LF endings, and final
  newlines.
- Keep Stryker-compatible behavior in this repository and Marmorkrebs-specific
  normalization at the consuming boundary.

## Mutator changes

When adding or changing a mutator:

- update `MUTATORS`, `MUTATOR_DESCRIPTIONS`, and clang AST cursor mapping when
  applicable;
- add CLI proof coverage for discovery and execution;
- update `docs/mutators.md` with examples and known noise risks;
- keep token mode deterministic and dependency-free;
- ensure clang mode either confirms the source span against an AST cursor or
  clearly remains unsupported for that mutator.

## Build adapter changes

When adding or changing a build-system adapter:

- keep command-line flags kebab-case and provider config/report fields
  lowerCamelCase;
- update `python/stryker_cxx/build_adapters.py` for source-overlay command
  synthesis;
- update compiled-artifact helpers in `python/stryker_cxx/engine.py` only when
  the adapter can prove source-to-artifact ownership and restore originals;
- add a fixture under `fixtures/adapters/<system>/` whenever the tool can run in
  CI or a skipped contract test when the tool is optional locally;
- document supported and unsupported artifact backends in `docs/spec.md` and
  `docs/contract.md`.

## Reporter and plugin changes

When adding reporter or plugin behavior:

- keep plugin execution local-only; do not add network installation or implicit
  plugin discovery;
- validate manifest capability versions during initialization;
- include focused tests for hook ordering, redacted command metadata, and final
  report paths;
- update `docs/contract.md` when report metadata changes;
- keep Marmorkrebs normalization outside this repository unless the change is a
  provider-boundary contract update.

## Fixture changes

When adding fixtures:

- keep fixtures small and deterministic;
- prefer standard tool entry points already exercised by `npm run
  validate:full-spec`;
- make optional tool fixtures skip cleanly when the tool is unavailable;
- avoid committing generated build outputs, retained worktrees, coverage
  reports, or package tarballs;
- update `scripts/validate-full-spec.mjs` if the fixture should become part of
  the full-spec gate.

## Report and schema changes

When changing report shape:

- update `python/stryker_cxx/schema.py`;
- update Python schema tests;
- update JS adapter tests when MTE or wrapper parsing changes;
- update `docs/spec.md` and `docs/contract.md`;
- preserve backwards-compatible legacy fields unless a deliberate breaking
  change is documented.

## Pull request checklist

Before opening a PR:

- `npm test` passes;
- `npm run schema:check` passes;
- `npm run package:check` passes;
- `npm pack --dry-run` includes the Python engine, docs, fixtures, and CLI wrapper;
- `git diff --check` is clean;
- new runner behavior has a focused test;
- docs/spec updates are included for user-visible behavior;
- generated, noisy, or equivalent mutants are handled with Stryker ignore
  comments rather than hidden from reports.
