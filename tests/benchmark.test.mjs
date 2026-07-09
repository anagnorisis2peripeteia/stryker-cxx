import assert from "node:assert/strict";
import test from "node:test";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

// The benchmark's deterministic mutant metrics must match the committed baseline; a drift
// (silent change to mutant generation) fails the benchmark, and therefore this test.
test("benchmark: mutant metrics match the committed baseline", () => {
  const result = spawnSync(process.execPath, ["scripts/benchmark.mjs"], {
    cwd: repoRoot,
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /total mutants: \d+/);
  assert.match(result.stdout, /deterministic mutant metrics match the baseline/);
});
