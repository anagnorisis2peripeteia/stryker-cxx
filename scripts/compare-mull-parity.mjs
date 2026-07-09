#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";

const repoRoot = resolve(new URL("..", import.meta.url).pathname);
const outputPath = resolve(
  process.env.STRYKER_CXX_COMPARISON_REPORT
    || join(repoRoot, "agent_space", "stryker-cxx", "comparison", "mull-parity.json"),
);
// A real captured mull 0.34.0 (LLVM 14) report of this harness's fixture is committed at
// fixtures/benchmark/mull-report.json, so `npm run compare:mull` is a real head-to-head out of the
// box — no mull install required. Point MULL_REPORT at your own capture to override it.
const goldenMullReport = join(repoRoot, "fixtures", "benchmark", "mull-report.json");
const mullReportPath = process.env.MULL_REPORT
  ? resolve(process.env.MULL_REPORT)
  : existsSync(goldenMullReport)
    ? goldenMullReport
    : null;

// Fail closed on an explicit-but-missing MULL_REPORT: silently ignoring it would hide a typo'd
// path behind a misleading "mull: null" comparison. (An unset MULL_REPORT legitimately falls back
// to the golden report or null.)
if (process.env.MULL_REPORT && !existsSync(mullReportPath)) {
  throw new Error(`MULL_REPORT points to a file that does not exist: ${mullReportPath}`);
}

function run(command, args, options = {}) {
  return spawnSync(command, args, {
    encoding: "utf8",
    shell: process.platform === "win32",
    ...options,
  });
}

function requireOk(label, result) {
  if (result.status !== 0) {
    process.stderr.write(result.stderr || "");
    process.stdout.write(result.stdout || "");
    throw new Error(`${label} failed with exit ${result.status ?? "unknown"}`);
  }
}

function commandExists(command) {
  const probe = process.platform === "win32"
    ? spawnSync("where", [command], { stdio: "ignore", shell: true })
    : spawnSync("sh", ["-c", `command -v ${command}`], { stdio: "ignore" });
  return probe.status === 0;
}

function createFixture() {
  const fixture = mkdtempSync(join(tmpdir(), "stryker-cxx-mull-parity-"));
  writeFileSync(
    join(fixture, "sample.cpp"),
    [
      "int value(int input) {",
      "  if (input == 1) {",
      "    return 10;",
      "  }",
      "  return input ? 20 : 30;",
      "}",
      "int main() { return value(1) == 10 ? 0 : 1; }",
      "",
    ].join("\n"),
  );
  writeFileSync(
    join(fixture, "build.mjs"),
    [
      "import { spawnSync } from 'node:child_process';",
      "const result = spawnSync('c++', ['-std=c++17', 'sample.cpp', '-o', process.platform === 'win32' ? 'sample.exe' : 'sample'], { stdio: 'inherit' });",
      "process.exit(result.status ?? 1);",
      "",
    ].join("\n"),
  );
  writeFileSync(
    join(fixture, "test.mjs"),
    [
      "import { spawnSync } from 'node:child_process';",
      "const binary = process.platform === 'win32' ? '.\\\\sample.exe' : './sample';",
      "const result = spawnSync(binary, [], { stdio: 'inherit' });",
      "process.exit(result.status ?? 1);",
      "",
    ].join("\n"),
  );
  writeFileSync(
    join(fixture, "compile_commands.json"),
    `${JSON.stringify([
      {
        directory: fixture,
        command: "c++ -std=c++17 -c sample.cpp -o sample.o",
        file: join(fixture, "sample.cpp"),
        output: join(fixture, "sample.o"),
      },
    ], null, 2)}\n`,
  );
  requireOk("git init", run("git", ["init", "-q"], { cwd: fixture }));
  requireOk("git add", run("git", ["add", "sample.cpp", "build.mjs", "test.mjs", "compile_commands.json"], { cwd: fixture }));
  requireOk(
    "git commit",
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
        "comparison fixture",
      ],
      { cwd: fixture },
    ),
  );
  return fixture;
}

function runStrykerCxx(fixture) {
  const report = join(fixture, "stryker-cxx.json");
  const node = process.execPath;
  const result = run(
    node,
    [
      join(repoRoot, "bin", "stryker-cxx.js"),
      "run",
      "--repo",
      fixture,
      "--files",
      "sample.cpp",
      "--build-command",
      `${node} build.mjs`,
      "--test-command",
      `${node} test.mjs`,
      "--mutators",
      "EqualityOperator,ConditionalExpression",
      "--execution-backend",
      "llvm-switch",
      "--max-mutants",
      "4",
      "--threshold-break",
      "0",
      "--report",
      report,
      "--quiet",
    ],
    { cwd: fixture },
  );
  requireOk("stryker-cxx comparison run", result);
  return JSON.parse(readFileSync(report, "utf8"));
}

function normalizeStrykerCxx(report) {
  const byStatus = report.summary?.byStatus || {};
  const buildGraph = report.projectAnalysis?.buildGraph || {};
  const sourcePrecision = report.execution?.analysis?.sourcePrecision || {};
  return {
    tool: "stryker-cxx",
    schemaVersion: report.schemaVersion,
    totalMutants: Number(report.totalMutants ?? report.mutants?.length ?? 0),
    killed: Number(report.killed ?? byStatus.KILLED ?? 0),
    survived: Number(report.survived ?? byStatus.SURVIVED ?? 0),
    timeout: Number(report.timeout ?? byStatus.TIMEOUT ?? 0),
    noCoverage: Number(report.noCoverage ?? byStatus.NO_COVERAGE ?? 0),
    buildErrors: Number(byStatus.BUILD_ERROR ?? 0),
    checkErrors: Number(byStatus.CHECK_ERROR ?? 0),
    ignored: Number(report.ignored ?? byStatus.IGNORED ?? 0),
    score: report.score,
    executionMode: report.execution?.executionMode,
    requestedExecutionMode: report.execution?.requestedExecutionMode,
    executionBackend: report.execution?.executionBackend,
    requestedExecutionBackend: report.execution?.requestedExecutionBackend,
    executionBackendFallbackReason: report.execution?.executionBackendFallbackReason,
    artifactBackend: report.execution?.artifactBackend,
    requestedArtifactBackend: report.execution?.requestedArtifactBackend,
    artifactFallbackReason: report.execution?.artifactFallbackReason,
    sourcePrecision: {
      schemaVersion: sourcePrecision.schemaVersion,
      totalMutants: Number(sourcePrecision.totalMutants ?? 0),
      withSourceRange: Number(sourcePrecision.withSourceRange ?? 0),
      astDirectMutants: Number(sourcePrecision.astDirectMutants ?? 0),
      tokenOnlyMutants: Number(sourcePrecision.tokenOnlyMutants ?? 0),
      byKind: sourcePrecision.byKind || {},
    },
    buildGraph: {
      schemaVersion: buildGraph.schemaVersion,
      confidence: buildGraph.confidence,
      ownershipModel: buildGraph.ownershipModel,
      compileDatabase: buildGraph.compileDatabase || {},
      diagnostics: buildGraph.diagnostics || [],
    },
    mutators: Object.keys(report.summary?.byMutator || {}).sort(),
  };
}

function normalizeMteStatus(status) {
  switch (status) {
    case "Killed":
      return "killed";
    case "Survived":
      return "survived";
    case "Timeout":
      return "timeout";
    case "NoCoverage":
      return "noCoverage";
    case "Ignored":
      return "ignored";
    case "CompileError":
      return "buildErrors";
    case "RuntimeError":
      return "runtimeErrors";
    default:
      return "unknown";
  }
}

function normalizeMullElements(report) {
  const files = report.files || {};
  const counts = {
    killed: 0,
    survived: 0,
    timeout: 0,
    noCoverage: 0,
    ignored: 0,
    buildErrors: 0,
    runtimeErrors: 0,
    unknown: 0,
  };
  const mutators = new Set();
  let totalMutants = 0;
  for (const file of Object.values(files)) {
    for (const mutant of file.mutants || []) {
      totalMutants += 1;
      counts[normalizeMteStatus(mutant.status)] += 1;
      if (mutant.mutatorName) mutators.add(mutant.mutatorName);
    }
  }
  return {
    tool: "mull",
    schemaVersion: report.schemaVersion,
    totalMutants,
    ...counts,
    mutators: [...mutators].sort(),
  };
}

function mullCapability() {
  const commands = ["mull-runner", "mull-reporter", "mull-cxx"];
  const available = commands.filter(commandExists);
  if (mullReportPath && existsSync(mullReportPath)) {
    const isGolden = mullReportPath === goldenMullReport && !process.env.MULL_REPORT;
    return {
      available,
      reportPath: mullReportPath,
      usingGoldenReport: isGolden,
      note: isGolden
        ? "Using the committed golden mull 0.34.0 (LLVM 14) report of this fixture (fixtures/benchmark/mull-report.json); comparison includes the normalized Mull Mutation Testing Elements report. Set MULL_REPORT to compare your own capture."
        : "MULL_REPORT supplied; comparison includes normalized Mull Mutation Testing Elements report.",
    };
  }
  return {
    available,
    reportPath: null,
    note: available.length
      ? "Mull executable detected, but no MULL_REPORT was supplied. Direct execution requires a project-specific Mull IR frontend/runner build, so this harness records capability only."
      : "Mull is not installed on PATH. Supply MULL_REPORT=<mutation-testing-elements.json> to compare captured Mull output.",
  };
}

function diffSummaries(left, right) {
  if (!right) return null;
  const fields = ["totalMutants", "killed", "survived", "timeout", "noCoverage", "ignored", "buildErrors"];
  return Object.fromEntries(fields.map((field) => [field, Number(left[field] || 0) - Number(right[field] || 0)]));
}

const fixture = createFixture();
const stryker = normalizeStrykerCxx(runStrykerCxx(fixture));
const mull = mullReportPath && existsSync(mullReportPath)
  ? normalizeMullElements(JSON.parse(readFileSync(mullReportPath, "utf8")))
  : null;

const comparison = {
  schemaVersion: "stryker-cxx.comparison.v1",
  generatedAt: new Date().toISOString(),
  fixture: {
    name: "source-overlay-basic",
    repo: fixture,
    files: ["sample.cpp"],
    purpose: "Phase 1 parity harness for comparing stryker-cxx against Mull-compatible Mutation Testing Elements output.",
  },
  strykerCxx: stryker,
  mull,
  mullCapability: mullCapability(),
  parityEvidence: {
    backend: {
      strykerCxxExecutionBackend: stryker.executionBackend,
      strykerCxxRequestedExecutionBackend: stryker.requestedExecutionBackend,
      strykerCxxFallbackReason: stryker.executionBackendFallbackReason,
      mullReferenceModel: "LLVM/instrumented-mutant-switching when supplied through a captured Mull MTE report",
      structurallyEquivalentBackend: stryker.executionBackend === "llvm-switch",
    },
    sourcePrecision: stryker.sourcePrecision,
    buildGraph: stryker.buildGraph,
  },
  delta: diffSummaries(stryker, mull),
  verdict: {
    status: stryker.totalMutants > 0 ? "ok" : "failed",
    notes: [
      "This is a harness-level comparison, not semantic equivalence proof.",
      "When Mull is not installed or no MULL_REPORT is supplied, the harness still proves the stryker-cxx fixture side and records Mull capability.",
    ],
  },
};

mkdirSync(dirname(outputPath), { recursive: true });
writeFileSync(outputPath, `${JSON.stringify(comparison, null, 2)}\n`);
console.log(`[compare:mull] ${outputPath}`);
if (comparison.verdict.status !== "ok") process.exit(1);
