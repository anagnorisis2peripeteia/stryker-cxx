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

Run mutation testing directly:

```bash
stryker-cxx run \
  --repo . \
  --base origin/main \
  --files src/foo.cpp \
  --build-command "ninja -C build target" \
  --test-command "./build/bin/target_test" \
  --report mutation.json
```

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

## Project map

- `src/index.js`: contract parser + summary helpers
- `src/cli.js`: CLI adapter entrypoint
- `bin/stryker-cxx.js`: executable wrapper and Python engine dispatcher
- `python/stryker_cxx/`: C++ mutation engine
- `tests/adapter.test.mjs`: JS API and schema tests
- `tests/python/`: engine contract tests
