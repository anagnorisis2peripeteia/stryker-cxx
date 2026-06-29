#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { delimiter, resolve } from "node:path";

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

function findPython() {
  for (const candidate of ["python3", "python"]) {
    const result = spawnSync(candidate, ["--version"], {
      stdio: "ignore",
      shell: process.platform === "win32",
    });
    if (result.status === 0) return candidate;
  }
  throw new Error("python3 or python is required");
}

const pythonPath = resolve("python");
const env = {
  ...process.env,
  PYTHONPATH: process.env.PYTHONPATH
    ? `${pythonPath}${delimiter}${process.env.PYTHONPATH}`
    : pythonPath,
};

const jsTests = readdirSync("tests")
  .filter((name) => name.endsWith(".test.mjs"))
  .map((name) => `tests/${name}`);
run("node", ["--test", ...jsTests]);
run(findPython(), ["-m", "unittest", "discover", "-s", "tests/python"], { env });
