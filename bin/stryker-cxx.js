#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { main } from "../src/cli.js";

const engineCommands = new Set(["run", "list-mutants", "run-mutant"]);
const directCommands = new Set(["--version", "-v"]);
const command = process.argv[2];

if (engineCommands.has(command)) {
  const binDir = path.dirname(fileURLToPath(import.meta.url));
  const repoRoot = path.resolve(binDir, "..");
  const pythonPath = path.join(repoRoot, "python");
  const env = {
    ...process.env,
    PYTHONPATH: process.env.PYTHONPATH ? `${pythonPath}${path.delimiter}${process.env.PYTHONPATH}` : pythonPath,
  };
  const result = spawnSync("python3", ["-m", "stryker_cxx.cli", ...process.argv.slice(2)], {
    env,
    stdio: "inherit",
  });
  process.exit(result.status ?? 1);
}

if (directCommands.has(command)) {
  const binDir = path.dirname(fileURLToPath(import.meta.url));
  const repoRoot = path.resolve(binDir, "..");
  const pythonPath = path.join(repoRoot, "python");
  const env = {
    ...process.env,
    PYTHONPATH: process.env.PYTHONPATH ? `${pythonPath}${path.delimiter}${process.env.PYTHONPATH}` : pythonPath,
  };
  const result = spawnSync("python3", ["-m", "stryker_cxx.cli", "--version"], {
    env,
    stdio: "inherit",
  });
  process.exit(result.status ?? 1);
}

process.exit(await main());
