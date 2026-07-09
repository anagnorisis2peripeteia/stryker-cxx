#!/usr/bin/env node
// Reproducible mutant-generation benchmark. Runs `stryker-cxx list-mutants` on the committed
// fixtures/benchmark fixture, times it, and checks the DETERMINISTIC mutant metrics (total +
// per-mutator counts) against fixtures/benchmark/baseline.json — failing on drift so a change
// that silently alters mutant generation is caught. Timing is informational (machine-specific).
//
// Head-to-head vs mull is a separate, opt-in comparison (mull is not required to run this):
//   npm run compare:mull                       # stryker-cxx side + mull command capability
//   MULL_REPORT=<mull mutation-testing-elements.json> npm run compare:mull   # count deltas
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

// fileURLToPath (not URL.pathname, which yields "/C:/…" and breaks path joins on Windows).
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const fixtureDir = join(repoRoot, "fixtures", "benchmark");
const baseline = JSON.parse(readFileSync(join(fixtureDir, "baseline.json"), "utf8"));

const start = performance.now();
const result = spawnSync(
  process.execPath,
  [
    join(repoRoot, "bin", "stryker-cxx.js"),
    "list-mutants",
    "--repo",
    fixtureDir,
    "--files",
    baseline.fixture,
    "--mutation-level",
    baseline.mutationLevel,
    "--format",
    "json",
  ],
  { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 },
);
const elapsedMs = Math.round(performance.now() - start);

if (result.status !== 0) {
  console.error(`[bench] stryker-cxx list-mutants failed:\n${result.stderr || result.stdout || ""}`);
  process.exit(1);
}

const mutants = JSON.parse(result.stdout);
const byMutator = {};
for (const mutant of mutants) byMutator[mutant.mutator] = (byMutator[mutant.mutator] || 0) + 1;
const total = mutants.length;

console.log(`[bench] fixture=${baseline.fixture} level=${baseline.mutationLevel}`);
console.log(`[bench] total mutants: ${total} (baseline ${baseline.totalMutants}) generated in ${elapsedMs}ms`);
for (const [mutator, count] of Object.entries(byMutator).sort()) {
  const base = baseline.byMutator[mutator] ?? 0;
  console.log(`  ${mutator.padEnd(26)} ${String(count).padStart(4)}${count !== base ? `  (baseline ${base})` : ""}`);
}

const drift = [];
const keys = new Set([...Object.keys(byMutator), ...Object.keys(baseline.byMutator)]);
for (const key of keys) {
  const now = byMutator[key] || 0;
  const was = baseline.byMutator[key] || 0;
  if (now !== was) drift.push(`${key}: ${was} -> ${now}`);
}

if (total !== baseline.totalMutants || drift.length > 0) {
  console.error(`[bench] REGRESSION: deterministic mutant metrics drifted from the baseline:`);
  for (const line of drift) console.error(`  ${line}`);
  console.error(`[bench] If this change is intended, refresh fixtures/benchmark/baseline.json.`);
  process.exit(1);
}

console.log(`[bench] OK — deterministic mutant metrics match the baseline.`);
console.log(`[bench] Head-to-head vs mull is opt-in: npm run compare:mull (MULL_REPORT=<mull mte.json> for deltas).`);
