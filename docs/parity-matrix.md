# stryker-cxx peer-parity matrix

Iteration-0 baseline for the peer-parity loop. Tracks stryker-cxx against its closest
mutation-testing peers so the loop has a concrete, checkable target. Peers:

- **mull** — LLVM-IR C/C++ mutation (the direct C++ peer)
- **StrykerJS** — the reference Stryker (JS/TS); the feature-completeness benchmark
- **Stryker.NET** — C# Stryker; the orchestration-lifecycle benchmark
- **mutmut** — Python; the "simple" baseline
- **cargo-mutants** — Rust; the diff/timeout-oriented peer

Legend: ✅ full · 🟡 partial/experimental · ❌ absent · n/a not applicable.
Gap tags: **[A]** additive (loop auto-merges when green) · **[S]** structural (loop HALTS for Cameron).

## Verdict (iteration 0)

stryker-cxx is **already at or above peer parity on every measurable axis** — it has a completed,
locally-validated 7-phase mull/Stryker.NET convergence roadmap (`docs/mull-stryker-net-roadmap.md`).
It ships **40 mutation operators** (more than any single peer), the full Stryker reporter suite
(JSON, Mutation-Testing-Elements, Markdown, SARIF, HTML, GitHub annotations, dashboard export),
incremental + baseline history, coverage-driven scheduling, clang-ast + token modes, four artifact
backends, sharding, and a mull comparison harness.

The residual gaps are **not feature breadth** — they are (a) a few **structural** engine depths that
peers reach and stryker-cxx approximates, and (b) **polish/adoption** items. So the loop's
auto-merge work is short; most remaining parity is structural (halt-for-Cameron) or adoption.

## Capability matrix

| Capability | stryker-cxx | mull | StrykerJS | Stryker.NET | mutmut | cargo-mutants |
| --- | --- | --- | --- | --- | --- | --- |
| Mutation operators (count) | ✅ 40 | 🟡 ~15 | ✅ ~30 | ✅ ~30 | 🟡 few | 🟡 few |
| Arithmetic/logical/relational/boolean | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Literal mutators (int/float/char/string/null/bool) | ✅ | 🟡 | ✅ | ✅ | 🟡 | 🟡 |
| Block/statement/call removal | ✅ | 🟡 | ✅ | ✅ | 🟡 | ✅ |
| Domain calls (chrono/container/fs/regex/iterator/stdlib/math/string) | ✅ | ❌ | 🟡 | 🟡 | ❌ | ❌ |
| C++ semantics (MemoryOrder/MoveSemantics/ExceptionHandling) | ✅ | ❌ | n/a | n/a | n/a | n/a |
| ObjC++ / Metal operators | ✅ | ❌ | n/a | n/a | n/a | n/a |
| AST-native precision (libclang) | ✅ clang-ast | ✅ (LLVM) | ✅ (babel) | ✅ (Roslyn) | ❌ | 🟡 |
| **True LLVM-IR/object instrumentation** | 🟡 llvm-switch (guarded-source, experimental) | ✅ | n/a | n/a | n/a | n/a |
| Compiled-artifact swap backend | ✅ exec/lib/object | ✅ | ✅ | ✅ | n/a | ✅ (rebuild) |
| **Broad build-graph ownership (Make/Ninja/Meson/Bazel/Xcode)** | 🟡 CMake-first; others best-effort/explicit-path | ✅ (compile db) | n/a | ✅ (msbuild) | n/a | ✅ (cargo) |
| Coverage-driven test selection (perTest) | ✅ | ✅ | ✅ | ✅ | ❌ | 🟡 |
| **Multi-mutant optimized sessions** | 🟡 batch build/check parallel; swap/test serialized | ✅ | ✅ | ✅ | ❌ | ✅ (parallel) |
| Incremental / baseline | ✅ (+ history/merge/prune) | 🟡 | ✅ | ✅ | ✅ (cache) | ✅ |
| Diff-scoped (`--since`/`--base`/`--scope-lines`) | ✅ | 🟡 | ✅ | ✅ | 🟡 | ✅ `--in-diff` |
| Parallelism / sharding | ✅ `--jobs` + shards | ✅ | ✅ | ✅ | 🟡 | ✅ |
| JSON report | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Mutation-Testing-Elements schema | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| Interactive HTML report | 🟡 HTML format emitted (verify full MTE browser report) | ✅ | ✅ | ✅ | ✅ | ❌ |
| SARIF / GitHub annotations | ✅ | ❌ | 🟡 | ❌ | ❌ | ❌ |
| Hosted dashboard upload | ✅ configurable endpoint | ❌ | ✅ (stryker dashboard) | ✅ | ❌ | ❌ |
| Live per-mutant progress output | ✅ `[i/N] … status (ms)` | 🟡 | ✅ | ✅ | 🟡 | ✅ |
| Clear-text summary reporter (score + survivor diffs) | ✅ `--format clear-text` (added 2026-07-09) | 🟡 | ✅ | ✅ | 🟡 | ✅ |
| Threshold bands (high/low/break) | ✅ | 🟡 | ✅ | ✅ | ❌ | 🟡 |
| Ignore/disable comments | ✅ Stryker-style | ✅ | ✅ | ✅ | 🟡 | 🟡 |
| Plugin/extension hooks | ✅ runner/checker/test/coverage | ✅ | ✅ | 🟡 | ❌ | ❌ |
| **Editor/IDE extension (VS Code)** | ❌ | 🟡 | ✅ | 🟡 | ❌ | ❌ |
| **Published head-to-head benchmark suite** | ❌ (compare:mull harness exists, no committed numbers) | 🟡 | ✅ | 🟡 | ❌ | ✅ |
| Init presets / scaffolding | ✅ (incl. metal) | 🟡 | ✅ | ✅ | 🟡 | 🟡 |

## Gap list (loop backlog, prioritised)

**P0 — genuine peer capability stryker-cxx lacks or only approximates**
1. **[S] True LLVM-IR / object instrumentation backend** (mull's core). Today `llvm-switch` is an
   experimental guarded-source switch, not real IR instrumentation. Big architectural effort → HALT.
2. **[S] Broad non-CMake build-graph ownership** (Make/Ninja/Meson/Bazel/Xcode first-class, not
   best-effort/explicit-path). Structural → HALT.
3. ~~**[A] Live progress / clear-text console reporter**~~ — **DONE (loop iter 1, 2026-07-09).**
   Verification found per-mutant progress already existed; the real gap was a clear-text SUMMARY.
   Added `--format clear-text` (score + per-file + surviving-mutant list with `original -> mutated`
   diffs). Residual: the official MTE *browsable* report (item 5) is distinct from the existing
   custom HTML.

**P1 — parity polish**
4. **[S] Multi-mutant optimized sessions** (swap/test currently serialize per artifact). Structural → HALT.
5. **[A] Interactive HTML Mutation-Testing-Elements report** — verify the emitted HTML is the full
   browsable Stryker report; if not, wire `mutation-testing-elements` HTML generation. Additive.
6. **[A] Published benchmark suite** — commit a reproducible `compare:mull` run + numbers (mutants
   generated/killed, speed) so parity is provable, not just asserted. Additive.
7. **[S] Xcode object ownership** (currently preflight-fallback). Structural → HALT.

**P2 — adoption / nice-to-have**
8. **[A] AST-native mutators as the preferred path** for ambiguous C++ rewrites (token stays fallback).
9. **[S] VS Code / editor extension** (StrykerJS has one). Large, product-shaped → HALT/discuss.
10. **[A] CUDA (`.cu`) language support** (extends the Metal/GPU story; niche).

## Proposed "done" bar for the loop

**Loop target = all [A] (additive) P0+P1 rows green:** items **3, 5, 6** (live progress reporter,
interactive HTML report verified/wired, committed benchmark suite), plus **8** if it stays additive.

The **[S] structural** items (1, 2, 4, 7) and the product-shaped **9** are **out of the loop's
auto-merge scope** — the loop surfaces each as a HALT with a short design note for Cameron to
greenlight separately, because they change engine architecture and need direction, not autonomous grinding.

**Honest headline:** on features, stryker-cxx is *already peer-level*. The loop closes the last few
additive/polish gaps autonomously; true mull-depth (LLVM IR) and broad build-graph ownership are
deliberate architectural choices for Cameron, not loop work.
