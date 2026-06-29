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
npm pack --dry-run
git diff --check
```

Run the optional clang fixture locally:

```bash
python -m pip install libclang
npm test
```

The clang fixture is skipped when the binding is unavailable and exercised in
CI by the `optional libclang fixture` job.

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
- `npm pack --dry-run` includes the Python engine, docs, tests, and CLI wrapper;
- `git diff --check` is clean;
- new runner behavior has a focused test;
- docs/spec updates are included for user-visible behavior;
- generated, noisy, or equivalent mutants are handled with Stryker ignore
  comments rather than hidden from reports.
