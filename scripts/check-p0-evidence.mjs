#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd,
    env: options.env ?? process.env,
    encoding: "utf8",
    shell: false,
  });
  const allowedStatuses = options.allowedStatuses ?? [0];
  if (!allowedStatuses.includes(result.status ?? 1)) {
    throw new Error(
      [
        `[p0:evidence] command failed: ${command} ${args.join(" ")}`,
        `cwd: ${options.cwd ?? process.cwd()}`,
        `status: ${result.status}`,
        result.stdout ? `stdout:\n${result.stdout}` : "",
        result.stderr ? `stderr:\n${result.stderr}` : "",
      ]
        .filter(Boolean)
        .join("\n"),
    );
  }
  return result;
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
  throw new Error("python3 or python is required for P0 evidence");
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(`[p0:evidence] ${message}`);
  }
}

function assertEqual(actual, expected, message) {
  assert(
    actual === expected,
    `${message}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
  );
}

const root = process.cwd();
const python = findPython();
const pythonPath = resolve(root, "python");
const pythonEnv = {
  ...process.env,
  PYTHONPATH: process.env.PYTHONPATH
    ? `${pythonPath}${process.platform === "win32" ? ";" : ":"}${process.env.PYTHONPATH}`
    : pythonPath,
};
const evidenceRoot = join(tmpdir(), "stryker-cxx-p0-evidence");

rmSync(evidenceRoot, { recursive: true, force: true });
mkdirSync(evidenceRoot, { recursive: true });

function createRepo(name, source) {
  const repo = join(evidenceRoot, name);
  mkdirSync(repo, { recursive: true });
  writeFileSync(join(repo, "sample.cpp"), source);
  run("git", ["init", "-q"], { cwd: repo });
  run("git", ["add", "sample.cpp"], { cwd: repo });
  run(
    "git",
    [
      "-c",
      "user.name=stryker-cxx",
      "-c",
      "user.email=stryker-cxx@example.invalid",
      "commit",
      "-q",
      "-m",
      "p0 evidence fixture",
    ],
    { cwd: repo },
  );
  return repo;
}

function cli(repo, args, allowedStatuses = [0]) {
  return run(python, ["-m", "stryker_cxx.cli", ...args], {
    cwd: repo,
    env: pythonEnv,
    allowedStatuses,
  });
}

function readReport(path) {
  assert(existsSync(path), `expected report at ${path}`);
  return JSON.parse(readFileSync(path, "utf8"));
}

function phase(report, name) {
  return report.lifecycle?.phases?.find((item) => item.name === name);
}

function checkMutantSwitchReport() {
  const repo = createRepo(
    "mutant-switch",
    "bool flag() { return true; }\nint main() { return flag() ? 0 : 1; }\n",
  );
  const reportPath = join(repo, "mutant-switch.json");
  cli(
    repo,
    [
      "run",
      "--repo",
      repo,
      "--files",
      "sample.cpp",
      "--build-command",
      "true",
      "--test-command",
      "true",
      "--report",
      reportPath,
      "--max-mutants",
      "1",
      "--mutators",
      "BooleanLiteral",
      "--execution-mode",
      "mutant-switch",
      "--skip-initial-test",
      "--quiet",
    ],
    [2],
  );
  const report = readReport(reportPath);
  assertEqual(report.execution.requestedExecutionMode, "mutant-switch", "requested execution mode");
  assertEqual(report.execution.executionMode, "mutant-switch", "actual execution mode");
  assertEqual(report.execution.singleCompile.builds, 1, "single-compile build count");
  assertEqual(report.mutationArtifact.mode, "mutant-switch", "mutation artifact mode");
  assertEqual(report.lifecycle.artifactModel, "mutant-switch", "lifecycle artifact model");
  assertEqual(phase(report, "mutationArtifact")?.status, "mutantSwitch", "mutation artifact phase");
  return reportPath;
}

function checkFallbackReport() {
  const repo = createRepo(
    "mutant-switch-fallback",
    "struct Node { int value; }; int main() { Node node{1}; return node.value; }\n",
  );
  const reportPath = join(repo, "mutant-switch-fallback.json");
  cli(
    repo,
    [
      "run",
      "--repo",
      repo,
      "--files",
      "sample.cpp",
      "--build-command",
      "true",
      "--test-command",
      "true",
      "--report",
      reportPath,
      "--max-mutants",
      "1",
      "--mutators",
      "MemberAccessOperator",
      "--execution-mode",
      "mutant-switch",
      "--skip-initial-test",
      "--quiet",
    ],
    [2],
  );
  const report = readReport(reportPath);
  assertEqual(report.execution.requestedExecutionMode, "mutant-switch", "fallback requested execution mode");
  assertEqual(report.execution.executionMode, "source-overlay", "fallback actual execution mode");
  assertEqual(report.execution.mutantSwitch.enabled, false, "fallback disables mutant switch");
  assert(
    typeof report.execution.mutantSwitch.fallbackReason === "string"
      && report.execution.mutantSwitch.fallbackReason.length > 0,
    "fallback report records a reason",
  );
  return reportPath;
}

function checkPruningReport() {
  const repo = createRepo(
    "mutant-switch-prune",
    [
      "bool a() { return true; }",
      "bool b() { return false; }",
      "int main() { return a() && !b() ? 0 : 1; }",
      "",
    ].join("\n"),
  );
  const listed = cli(repo, [
    "list-mutants",
    "--repo",
    repo,
    "--files",
    "sample.cpp",
    "--mutators",
    "BooleanLiteral",
    "--max-mutants",
    "2",
  ]);
  const listedPayload = JSON.parse(listed.stdout);
  assert(listedPayload.length >= 2, "compile-pruning fixture needs two BooleanLiteral mutants");
  const badGuard = listedPayload[1].mutantSwitchGuardId;
  const buildScript = join(repo, "switch_build.py");
  writeFileSync(
    buildScript,
    [
      "from pathlib import Path",
      "import sys",
      "count = Path('build-count.txt')",
      "count.write_text((count.read_text() if count.exists() else '') + 'b')",
      `sys.exit(1 if ${JSON.stringify(badGuard)} in Path('sample.cpp').read_text() else 0)`,
      "",
    ].join("\n"),
  );

  const reportPath = join(repo, "mutant-switch-prune.json");
  cli(
    repo,
    [
      "run",
      "--repo",
      repo,
      "--files",
      "sample.cpp",
      "--build-command",
      `${python} ${buildScript}`,
      "--test-command",
      "printf test >> test-count.txt",
      "--report",
      reportPath,
      "--max-mutants",
      "2",
      "--mutators",
      "BooleanLiteral",
      "--execution-mode",
      "mutant-switch",
      "--skip-initial-test",
      "--quiet",
    ],
    [2],
  );
  const report = readReport(reportPath);
  assertEqual(report.execution.executionMode, "mutant-switch", "pruning execution mode");
  assertEqual(
    report.execution.compilePruning.strategy,
    "mutant-switch-prune-and-retry",
    "compile-pruning strategy",
  );
  assertEqual(report.execution.compilePruning.prunedMutants, 1, "compile-pruned mutant count");
  assertEqual(report.lifecycle.artifactModel, "mutant-switch", "pruning lifecycle artifact model");
  assertEqual(phase(report, "compilePruning")?.detail?.prunedMutants, 1, "compile-pruning phase metadata");
  assertEqual(phase(report, "artifactRestoration")?.status, "mutantSwitch", "artifact restoration phase");
  return reportPath;
}

const evidence = [
  checkMutantSwitchReport(),
  checkFallbackReport(),
  checkPruningReport(),
];

for (const reportPath of evidence) {
  console.log(`[p0:evidence] ${reportPath}`);
}
console.log("[p0:evidence] ok");
