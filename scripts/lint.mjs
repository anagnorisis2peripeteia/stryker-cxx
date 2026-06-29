#!/usr/bin/env node
import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    stdio: "inherit",
    shell: process.platform === "win32",
    ...options,
  });
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

function canRun(command) {
  const probe = process.platform === "win32"
    ? spawnSync("where", [command], { stdio: "ignore", shell: true })
    : spawnSync("sh", ["-c", `command -v ${command}`], { stdio: "ignore" });
  return probe.status === 0;
}

function findPython() {
  for (const candidate of ["python3", "python"]) {
    if (canRun(candidate)) return candidate;
  }
  throw new Error("python3 or python is required for lint checks");
}

const lintTargets = [
  resolve("bin", "stryker-cxx.js"),
  resolve("scripts", "check-config-schema.mjs"),
  resolve("scripts", "run-tests.mjs"),
  resolve("scripts", "validate-full-spec.mjs"),
].filter((path) => existsSync(path));

for (const file of lintTargets) {
  run("node", ["--check", file]);
}

run(findPython(), ["-m", "compileall", "-q", "python"]);
