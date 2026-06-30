# Stryker.NET structural parity spec

## Purpose

`stryker-cxx` should work structurally like the Stryker family, especially
Stryker.NET: mutation should produce compiled mutation artifacts, prune mutants
that fail build/check, place the valid mutated artifact where the test runner
will load it, run the needed tests, then restore the original artifact.

The current source-overlay backend is useful as a compatibility path, but it is
not the target architecture. The target architecture is a compiled-artifact
backend for C++/ObjC++.

## Reference workflow

The Stryker-style lifecycle this spec targets is:

1. Initialize and validate parameters.
2. Analyze projects to discover mutation targets, build targets, and test
   projects.
3. Mutate targets into an intermediate representation.
4. Build mutated artifacts.
5. Remove compile/check-failing mutants and retry until the artifact builds or
   no valid mutants remain.
6. Place the mutated artifact where tests will execute it.
7. Analyze coverage to select tests for each mutant/session.
8. Run test sessions, preferably batching compatible mutants where safe.
9. Restore original artifacts.
10. Emit native and Mutation Testing Elements reports.

For C++, "in memory" should mean "not rewriting the user's checkout as the
execution mechanism." The practical equivalent is compiling mutated translation
units in scratch space and swapping object files, static libraries, shared
libraries, executables, or test-loaded bundles.

## Current gap

The current implementation now has lifecycle metadata, project analysis,
mutation artifact abstraction, compile pruning metadata, coverage-aware
scheduling, and artifact placement metadata.

It also has initial `compiled-executable`, `compiled-library`, and
`compiled-object` backends for CMake/CTest-style targets. Those backends mutate
scratch source, build a mutated artifact in scratch space, swap the linked
artifact into the original build/test location, run tests against it, and
restore the original artifact by hash. `compiled-object` additionally records
the object produced for each mutated translation unit from CMake's compile
database.

The structural gap remains:

- source-overlay is still the default compatibility backend;
- compiled artifact execution is CMake/CTest-first;
- compiled artifact batching is implemented for local single-worker sessions,
  but parallel compiled workers are still disabled;
- full build graph discovery beyond the CMake path is incomplete.

## Target architecture

### Backends

The engine should support explicit mutation artifact backends:

- `source-overlay`: legacy compatibility backend.
- `compiled-object`: compile mutated source to object files, record the object
  artifacts, relink the owning target, and swap the linked artifact before test
  execution.
- `compiled-library`: compile/relink mutated static or shared libraries and
  swap them into the test runtime location.
- `compiled-executable`: relink or rebuild the target executable and run tests
  against the mutated binary.

The default target backend for serious C++ workflows should become
`compiled-library` or `compiled-executable` when project analysis can prove the
target/test relationship. `source-overlay` remains an explicit fallback.

### Project model

Project analysis must produce a build graph with:

- source files;
- compile commands;
- object outputs;
- owning build target;
- linked libraries/executables;
- test targets;
- runtime load paths;
- commands needed to build one mutated artifact;
- commands needed to restore or rebuild originals.

For CMake/Ninja projects, this should use `compile_commands.json`, CMake file
API when available, and Ninja query output where possible.

For Make/Bazel/Meson/Xcode, adapters should expose the same normalized graph.

### Mutation IR

Mutants should be represented independently from the source overlay mechanism:

- mutant id;
- source file;
- source range;
- replacement text or AST rewrite;
- owning translation unit;
- owning build target;
- expected artifact outputs;
- compatibility flags for batching.

The IR must be able to materialize a mutated translation unit in scratch space
without modifying the user's checkout.

### Build artifact pipeline

The compiled backend should:

1. Create a scratch mutation workspace.
2. Materialize mutated translation units into scratch paths.
3. Rewrite compile commands so include paths, defines, working directories, and
   dependency files still resolve.
4. Compile mutated object files.
5. Relink or rebuild the owning library/executable when required.
6. Validate build/check success.
7. Produce a `CompiledMutationArtifact` record.

The artifact record should include:

- backend;
- target name;
- source mutants included;
- compile commands used;
- object outputs;
- linked artifact path;
- original artifact path;
- placement path;
- restoration plan;
- build/check logs;
- retained artifact path when requested.

### Compile pruning

Compile pruning should operate on artifact candidates, not source overlays.

Required behavior:

- build a candidate artifact containing one or more mutants;
- if compile/check fails, split candidates until failing mutants are isolated;
- mark compile-invalid mutants as pruned;
- rebuild with remaining compile-valid mutants;
- only schedule tests for compile-valid artifacts.

Native reports should preserve exact reasons while projecting to compatible MTE
statuses.

### Artifact placement and restoration

Before placing a mutated artifact, the runner must snapshot the original
artifact or know how to rebuild it deterministically.

Placement policies:

- `swap-file`: copy mutated artifact over the original path, then restore.
- `runtime-path`: place mutated artifact in a test-specific runtime directory.
- `loader-env`: run tests with environment variables pointing to mutated
  libraries.
- `relink-target`: relink a test target against mutated objects/libraries.

Restoration requirements:

- originals are restored after every test session;
- restoration runs even on timeout/failure;
- retained mutated artifacts are opt-in only;
- retained artifacts are reported with cleanup guidance;
- source checkout is not mutated by the compiled backend.

### Test scheduler

The scheduler should plan sessions over compiled artifacts:

- one mutant per session as the baseline;
- batch compatible mutants into one artifact where safe;
- split failed sessions for attribution;
- use coverage to select test subsets;
- avoid mixing mutants that affect the same translation unit region unsafely;
- keep deterministic output order.

### Reporting

Native reports should include:

- `mutationArtifact.backend`;
- `compiledArtifact` metadata for object/library/executable outputs;
- compile-pruning attempts and isolated invalid mutants;
- artifact placement policy;
- restoration result;
- retained artifacts;
- scheduler sessions mapped to artifact ids;
- coverage-selected tests per session.

The MTE projection should remain compatible. C++-specific structural metadata
stays in the native wrapper.

## CLI/config surface

Add explicit backend selection:

```yaml
mutationArtifact:
  backend: compiled-library
  fallback: source-overlay
  retainArtifactsFor:
    - SURVIVED
    - TIMEOUT
```

CLI equivalents:

```bash
stryker-cxx run \
  --artifact-backend compiled-library \
  --artifact-fallback source-overlay \
  --retain-artifacts-for SURVIVED,TIMEOUT
```

The runner should fail loudly when a compiled backend is requested but project
analysis cannot prove a safe artifact placement/restoration plan.

## Implementation phases

### Phase 1: backend contract

- Define `MutationArtifactBackend`.
- Move source overlay behind the backend interface.
- Add `CompiledMutationArtifact` data contracts.
- Add report/schema docs for backend selection.

Acceptance:

- source-overlay behavior remains available;
- reports identify backend truthfully;
- tests prove source checkout is untouched by backend abstraction.

### Phase 2: build graph discovery

- Add normalized build graph model.
- Implement CMake/Ninja discovery using compile database plus CMake/Ninja
  metadata.
- Map source files to object outputs and owning targets.

Acceptance:

- CMake fixture maps source to target and artifact output;
- unknown projects fail compiled backend preflight with actionable diagnostics;
- source-overlay fallback remains explicit.

### Phase 3: compiled object materialization

- Generate mutated translation units in scratch space.
- Rewrite compile commands to compile mutated object outputs.
- Record compile logs and object artifacts.

Acceptance:

- a single mutant can compile to an object file without modifying checkout
  source;
- compile-failing mutants are pruned before tests;
- object artifacts can be retained for proof.

### Phase 4: linked artifact placement

- Relink static/shared libraries or executables from mutated object artifacts.
- Snapshot original artifacts.
- Place mutated artifacts using a selected placement policy.
- Restore originals after sessions.

Acceptance:

- tests run against the mutated compiled artifact;
- originals are restored after killed, survived, timeout, and error sessions;
- retained mutated artifacts are opt-in and reported.

### Phase 5: scheduler over compiled artifacts

- Batch compatible mutants into compiled artifacts.
- Split failed batches for attribution.
- Use coverage-selected tests for compiled-artifact sessions.

Acceptance:

- per-mutant and batched sessions both run through compiled artifacts;
- coverage-selected sessions use targeted tests;
- output ordering remains deterministic.

### Phase 6: adapters beyond CMake/Ninja

- Keep source-overlay command adapters for Ninja, Make, Meson, Bazel, and Xcode.
- Keep compiled-artifact execution CMake/CTest-only until non-CMake adapters can
  prove source-to-object-to-linked-artifact ownership.
- Fail unsupported compiled-artifact adapters in preflight with clear
  diagnostics.
- Add Make/Meson/Bazel/Xcode compiled artifact adapters only when their build
  graph discovery can satisfy the artifact ownership contract.

Acceptance:

- supported compiled adapters expose the normalized artifact contract;
- unsupported compiled adapters fail preflight clearly;
- docs list support level by build system.

## Test strategy

Required fixtures:

- CMake static library plus test executable.
- CMake shared library plus runtime loader test.
- CMake executable target tested by an external command.
- Compile-failing mutant fixture.
- Coverage-selected fixture.
- Batch split fixture.
- Timeout restoration fixture.

Required tests:

- compiled backend never mutates checkout source;
- compile-invalid mutants are pruned before tests;
- mutated artifact causes a test failure/survival as expected;
- original artifact hash is restored after every outcome;
- retained artifact exists only when requested;
- native report captures backend, artifact, placement, restoration, scheduler,
  and coverage metadata;
- MTE projection remains compatible.

## Done definition

This spec is done only when a real CMake/Ninja C++ fixture can run mutation
testing through compiled artifacts with source-overlay disabled, and:

- the user checkout remains unchanged;
- mutants are compiled into object/library/executable artifacts;
- compile-failing mutants are pruned before test execution;
- tests run against placed mutated artifacts;
- originals are restored deterministically;
- retained mutated artifacts are opt-in and reported;
- full validation passes;
- docs clearly state supported and unsupported build systems.

## Non-goals

- Literal .NET-style in-memory assembly replacement.
- Full LLVM bitcode mutation as the first backend.
- Universal support for every C++ build system in the first implementation.
- Removing source-overlay compatibility immediately.

## Open questions

- Should the first compiled backend target static libraries, shared libraries,
  or executables?
- Should CMake file API be required for compiled backend mode?
- Should source-overlay fallback be automatic or require an explicit flag?
- How should object-level retained proof artifacts be named and cleaned up?
- Which build systems should be hard unsupported until adapters exist?
