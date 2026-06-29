# Fixtures

The `fixtures/` tree contains small projects and plugin manifests used to keep
adapter contracts concrete.

## Build-system adapters

- `fixtures/adapters/cmake-ctest`: CMake project with CTest registration.
- `fixtures/adapters/ninja`: hand-written `build.ninja`.
- `fixtures/adapters/make`: `Makefile` with `all`, `test`, and `clean`.
- `fixtures/adapters/meson`: Meson project with a registered test.
- `fixtures/adapters/bazel`: Bazel `cc_test`.

These are intentionally tiny. They exercise the command shapes emitted by
`--build-system`, not large project behavior.

## Test-framework adapters

- `fixtures/frameworks/gtest`
- `fixtures/frameworks/catch2`
- `fixtures/frameworks/doctest`
- `fixtures/frameworks/xctest`

These fixtures document expected source shape for framework-specific test
commands. Some require the corresponding framework dependency to be installed.

## Plugin compatibility

- `fixtures/plugins/token-mutator`: local mutator manifest.
- `fixtures/plugins/reporter-hook`: local hook/reporter manifest.
- `fixtures/plugins/provider-hooks`: local build/check/test runner and
  coverage-provider capability manifest.

Plugins are loaded explicitly by path or plugin directory. `stryker-cxx` never
installs plugins from the network during a mutation run.

## Config schema

- `fixtures/config/stryker-cxx.config.json`: JSON config fixture validated by
  the no-network `npm run schema:check` path.
- `fixtures/config/stryker-cxx.config.yml`: YAML config fixture validated by
  the same repo-local schema checker.
- Both fixtures are checked against
  `docs/schemas/stryker-cxx.config.schema.json`.
