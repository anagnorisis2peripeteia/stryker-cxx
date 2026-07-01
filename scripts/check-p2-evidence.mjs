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
import { delimiter, join, resolve } from "node:path";

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
        `[p2:evidence] command failed: ${command} ${args.join(" ")}`,
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
  throw new Error("python3 or python is required for P2 evidence");
}

function shellQuote(value) {
  if (process.platform === "win32") return `"${String(value).replace(/"/g, '\\"')}"`;
  return `'${String(value).replace(/'/g, "'\\''")}'`;
}

function assert(condition, message) {
  if (!condition) throw new Error(`[p2:evidence] ${message}`);
}

function assertIncludes(text, needle, label) {
  assert(text.includes(needle), `${label} should include ${needle}`);
}

function read(path) {
  return readFileSync(path, "utf8");
}

function readJson(path) {
  assert(existsSync(path), `expected JSON artifact at ${path}`);
  return JSON.parse(read(path));
}

const root = process.cwd();
const python = findPython();
const pythonPath = resolve(root, "python");
const pythonEnv = {
  ...process.env,
  PYTHONPATH: process.env.PYTHONPATH
    ? `${pythonPath}${delimiter}${process.env.PYTHONPATH}`
    : pythonPath,
};
const evidenceRoot = join(tmpdir(), "stryker-cxx-p2-evidence");

rmSync(evidenceRoot, { recursive: true, force: true });
mkdirSync(evidenceRoot, { recursive: true });

function createRepo(name) {
  const repo = join(evidenceRoot, name);
  mkdirSync(repo, { recursive: true });
  writeFileSync(repo + "/sample.cpp", "int main() { return 1 == 1 ? 0 : 1; }\n");
  run("git", ["init", "-q"], { cwd: repo });
  run("git", ["add", "."], { cwd: repo });
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
      "p2 evidence fixture",
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

function checkPluginReporterEvidence() {
  const repo = createRepo("plugin-reporter");
  const helper = join(repo, "plugin_helper.py");
  writeFileSync(
    helper,
    [
      "from pathlib import Path",
      "import os",
      "import sys",
      "phase = sys.argv[1]",
      "Path('plugin-lifecycle.txt').open('a', encoding='utf-8').write(phase + '\\n')",
      "if phase == 'reporter':",
      "    report = os.environ.get('STRYKER_CXX_REPORT')",
      "    if report:",
      "        Path('p2-report-copy.json').write_text(Path(report).read_text(encoding='utf-8'), encoding='utf-8')",
      "",
    ].join("\n"),
  );
  const passScript = join(repo, "pass.py");
  const failScript = join(repo, "fail.py");
  writeFileSync(passScript, "import sys\nsys.exit(0)\n");
  writeFileSync(failScript, "import sys\nsys.exit(1)\n");

  const helperCmd = (phase, extra = "") =>
    `${shellQuote(python)} ${shellQuote(helper)} ${shellQuote(phase)}${extra ? ` ${extra}` : ""}`;
  const plugin = join(repo, "p2-plugin.json");
  writeFileSync(
    plugin,
    JSON.stringify(
      {
        name: "p2-plugin",
        version: "0.1.0",
        capabilities: {
          hooks: true,
        },
        hooks: {
          initialization: helperCmd("initialization", "API_KEY=supersecret"),
          projectAnalysis: helperCmd("projectAnalysis"),
          mutationDiscovery: helperCmd("mutationDiscovery"),
          artifactCreation: helperCmd("artifactCreation"),
          coverageAnalysis: helperCmd("coverageAnalysis"),
          scheduling: helperCmd("scheduling"),
          execution: helperCmd("execution"),
          reporting: helperCmd("reporting"),
          cleanup: helperCmd("cleanup"),
          postRun: helperCmd("postRun"),
        },
        reporters: [
          {
            name: "copy-json",
            command: helperCmd("reporter"),
            metadata: {
              scope: "local",
              format: "json",
            },
          },
        ],
      },
      null,
      2,
    ),
  );

  const report = join(repo, "p2-plugin-report.json");
  cli(
    repo,
    [
      "run",
      "--repo",
      repo,
      "--files",
      "sample.cpp",
      "--build-command",
      `${shellQuote(python)} ${shellQuote(passScript)}`,
      "--test-command",
      `${shellQuote(python)} ${shellQuote(failScript)}`,
      "--skip-initial-test",
      "--plugin",
      plugin,
      "--reporter",
      "copy-json",
      "--reporter",
      "missing-json",
      "--max-mutants",
      "1",
      "--report",
      report,
      "--quiet",
    ],
  );
  const payload = readJson(report);
  const lifecycle = payload.execution.pluginLifecycle;
  assert(lifecycle.localOnly === true, "plugin lifecycle should be local-only");
  assert(lifecycle.networkInstall === false, "plugin lifecycle should forbid network install");
  assert(lifecycle.loadOrder.join(",") === "p2-plugin", "plugin load order should be deterministic");
  for (const phase of [
    "initialization",
    "projectAnalysis",
    "mutationDiscovery",
    "artifactCreation",
    "coverageAnalysis",
    "scheduling",
    "execution",
    "reporting",
    "cleanup",
  ]) {
    assert(lifecycle.runs.some((item) => item.phase === phase), `missing plugin lifecycle run ${phase}`);
  }
  const serialized = JSON.stringify(payload);
  assert(!serialized.includes("supersecret"), "plugin report must redact secret assignment values");
  assert(serialized.includes("[REDACTED]"), "plugin report should show redaction marker");
  assert(
    payload.execution.reporterMetadata?.[0]?.metadata?.format === "json",
    "reporter metadata should be recorded",
  );
  assert(
    payload.execution.reporterRuns?.[0]?.plugin === "p2-plugin",
    "reporter run should record plugin name",
  );
  assert(
    payload.execution.reporterRuns?.[0]?.reporter === "copy-json",
    "reporter run should record reporter name",
  );
  assert(
    payload.execution.reporterRuns?.[0]?.status === "passed",
    "reporter run should record status",
  );
  assert(
    payload.execution.reporterRuns?.[0]?.exitCode === 0,
    "reporter run should record exit code",
  );
  const missingReporter = payload.execution.reporterRuns?.find((item) => item.reporter === "missing-json");
  assert(
    missingReporter?.status === "notFound",
    "missing requested reporter should be recorded as notFound",
  );
  assert(
    missingReporter?.availableReporters?.join(",") === "copy-json",
    "missing requested reporter should record available reporter command names",
  );
  assert(existsSync(join(repo, "p2-report-copy.json")), "reporter hook should receive final native report path");

  const futurePlugin = join(repo, "future-plugin.json");
  writeFileSync(
    futurePlugin,
    JSON.stringify({
      name: "future-plugin",
      version: "0.1.0",
      capabilities: {
        runner: {
          version: "2.0",
          name: "future-runner",
          buildCommand: "true",
        },
      },
    }),
  );
  const failed = cli(
    repo,
    [
      "run",
      "--repo",
      repo,
      "--files",
      "sample.cpp",
      "--build-command",
      `${shellQuote(python)} ${shellQuote(passScript)}`,
      "--test-command",
      `${shellQuote(python)} ${shellQuote(passScript)}`,
      "--plugin",
      futurePlugin,
      "--dry-run-only",
      "--report",
      join(repo, "future-plugin-report.json"),
      "--quiet",
    ],
    [1],
  );
  assertIncludes(
    `${failed.stdout}\n${failed.stderr}`,
    "unsupported capability version for runner: 2.0",
    "plugin capability validation",
  );
  return report;
}

function checkWorkflowEvidence() {
  const ci = read(".github/workflows/ci.yml");
  assertIncludes(ci, "npm run lint", "ci workflow");
  assertIncludes(ci, "npm test", "ci workflow");
  assertIncludes(ci, "npm run schema:check", "ci workflow");
  assertIncludes(ci, "npm run package:check", "ci workflow");
  assertIncludes(ci, "npm run validate:full-spec", "ci workflow");
  assertIncludes(ci, "python -m pip install libclang", "ci optional clang fixture");
  assertIncludes(ci, "npm run evidence:p1", "ci optional clang fixture");
  assertIncludes(ci, "ubuntu-latest", "ci matrix");
  assertIncludes(ci, "macos-latest", "ci matrix");
  assertIncludes(ci, '"20"', "ci node matrix");
  assertIncludes(ci, '"22"', "ci node matrix");

  const smoke = read(".github/workflows/release-smoke.yml");
  assertIncludes(smoke, "windows-latest", "release smoke matrix");
  assertIncludes(smoke, "npm run validate:full-spec", "release smoke workflow");
  assertIncludes(smoke, "npm run package:check", "release smoke workflow");
  assertIncludes(smoke, "npm pack --dry-run", "release smoke workflow");

  const release = read(".github/workflows/release.yml");
  assertIncludes(release, "id-token: write", "release workflow");
  assertIncludes(release, "npm publish --provenance --access public", "release workflow");
  assertIncludes(release, "npm run release:dry-run", "release workflow");
  assertIncludes(release, "NPM_TOKEN", "release workflow");

  const publish = read(".github/workflows/publish.yml");
  assertIncludes(publish, "id-token: write", "publish workflow");
  assertIncludes(publish, "npm publish --provenance --access public", "publish workflow");
  assertIncludes(publish, "--dry-run", "publish workflow");
  assertIncludes(publish, "NPM_TOKEN", "publish workflow");
  return ".github/workflows/ci.yml";
}

function checkPackageAndDocsEvidence() {
  const pkg = readJson("package.json");
  assert(pkg.engines?.node === ">=20", "package should require Node >=20");
  for (const script of ["validate:full-spec", "evidence:p0", "evidence:p1", "evidence:p2", "release:dry-run"]) {
    assert(pkg.scripts?.[script], `package script missing ${script}`);
  }
  for (const file of ["bin/", "src/", "python/", "docs/", "fixtures/"]) {
    assert(pkg.files?.includes(file), `package files should include ${file}`);
  }

  const contributing = read("CONTRIBUTING.md");
  for (const heading of [
    "## Mutator changes",
    "## Build adapter changes",
    "## Reporter and plugin changes",
    "## Fixture changes",
    "## Pull request checklist",
  ]) {
    assertIncludes(contributing, heading, "contribution guide");
  }
  const release = read("docs/release.md");
  assertIncludes(release, "npm run validate:full-spec", "release docs");
  assertIncludes(release, "npm run package:check", "release docs");
  assertIncludes(release, "npm publish --provenance --access public", "release docs");
  const signing = read("docs/signing.md");
  assertIncludes(signing, "id-token: write", "signing docs");
  assertIncludes(signing, "NODE_AUTH_TOKEN", "signing docs");
  const dashboard = read("docs/dashboard.md");
  assertIncludes(dashboard, "--dashboard-upload-retries", "dashboard docs");
  assertIncludes(dashboard, "provenance.upload.attempts", "dashboard docs");
  assertIncludes(dashboard, "execution.dashboard.export", "dashboard docs");
  const spec = read("docs/spec.md");
  assertIncludes(spec, "Marmorkrebs is only an", "Marmorkrebs boundary spec");
  assertIncludes(spec, "orchestrator/consumer", "Marmorkrebs boundary spec");
  assertIncludes(spec, "normalization stays outside this repository", "Marmorkrebs boundary spec");
  return "docs/release.md";
}

const evidence = [
  checkPluginReporterEvidence(),
  checkWorkflowEvidence(),
  checkPackageAndDocsEvidence(),
];

for (const artifact of evidence) {
  console.log(`[p2:evidence] ${artifact}`);
}
console.log("[p2:evidence] ok");
