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
        `[p1:evidence] command failed: ${command} ${args.join(" ")}`,
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
  throw new Error("python3 or python is required for P1 evidence");
}

function assert(condition, message) {
  if (!condition) throw new Error(`[p1:evidence] ${message}`);
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
    ? `${pythonPath}${delimiter}${process.env.PYTHONPATH}`
    : pythonPath,
};
const evidenceRoot = join(tmpdir(), "stryker-cxx-p1-evidence");

rmSync(evidenceRoot, { recursive: true, force: true });
mkdirSync(evidenceRoot, { recursive: true });

function createRepo(name, files) {
  const repo = join(evidenceRoot, name);
  mkdirSync(repo, { recursive: true });
  for (const [path, contents] of Object.entries(files)) {
    const full = join(repo, path);
    mkdirSync(full.split(/[\\/]/).slice(0, -1).join("/") || repo, { recursive: true });
    writeFileSync(full, contents);
  }
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
      "p1 evidence fixture",
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

function readJson(path) {
  assert(existsSync(path), `expected JSON artifact at ${path}`);
  return JSON.parse(readFileSync(path, "utf8"));
}

function checkDiscoveryMetadata() {
  const repo = createRepo("discovery-metadata", {
    "sample.cpp": [
      "#include <algorithm>",
      "#ifdef FEATURE_FLAG",
      "#endif",
      "#if 1",
      "#endif",
      "struct Node { int value; void touch(); };",
      "void objc(id obj) {",
      "  [obj touch];",
      "}",
      "BOOL enabled() { return YES; }",
      "kernel void shade(uint gid [[thread_position_in_grid]]) {}",
      "bool both(bool a, bool b) { return a && b; }",
      "int shifted(int x) { int a = x << 1; a <<= 2; return a >> 1; }",
      "int sign(int x) { int a = -x; return +a; }",
      "int updates(int x) { x++; ++x; x--; --x; return x; }",
      "int mask(int x, int y) { return x ^ y; }",
      "int modulo(int x, int y) { int r = x % y; r %= 3; r &= y; r |= 1; r ^= 2; return r; }",
      "int moved(int x) { auto y = std::move(x); auto z = std::forward<int>(y); return z; }",
      "int ends(std::vector<int>& values) { return values.front() + values.back(); }",
      "int state(std::vector<int>& values) { return values.empty() ? 0 : values.size() + values.capacity(); }",
      "bool string_checks(std::string& label) { return label.find(\"x\") == 0 || label.rfind(\"z\") == 0 || label.starts_with(\"a\") || label.ends_with(\"z\"); }",
      "int standard_minmax() { return std::min(1, 2) + std::max(1, 2); }",
      "void standard_bounds(std::vector<int>& values) { auto lower = std::lower_bound(values.begin(), values.end(), 2); auto upper = std::upper_bound(values.begin(), values.end(), 2); auto first = std::begin(values); auto last = std::end(values); }",
      "void standard_algorithms(std::vector<int>& values, auto pred) { std::sort(values.begin(), values.end()); std::stable_sort(values.begin(), values.end()); std::partition(values.begin(), values.end(), pred); std::stable_partition(values.begin(), values.end(), pred); }",
      "bool standard_predicates(std::vector<int>& values) { return std::is_sorted(values.begin(), values.end()) || std::is_heap(values.begin(), values.end()); }",
      "double math_checks(double x) { return std::ceil(x) + std::floor(x) + std::round(x) + std::trunc(x); }",
      "int iterator_checks(std::vector<int>& values) { return *std::next(values.begin()) + *std::prev(values.end()); }",
      "auto chrono_checks(auto duration) { return std::chrono::floor<std::chrono::seconds>(duration) + std::chrono::ceil<std::chrono::seconds>(duration); }",
      "bool regex_checks(auto text, auto match, auto re) { return std::regex_match(text, match, re) || std::regex_search(text, match, re); }",
      "bool filesystem_checks(auto path) { return std::filesystem::exists(path) || std::filesystem::is_empty(path) || std::filesystem::is_regular_file(path) || std::filesystem::is_directory(path); }",
      "int main() { return 1 == 1 && both(true, false) ? 0 : 1; }",
      "",
    ].join("\n"),
    "shader.metal": "kernel void shade(device float* out, constant float* scale, threadgroup float* scratch, uint gid [[thread_position_in_grid]]) {}\n",
  });

  const listed = JSON.parse(
    cli(repo, [
      "list-mutants",
      "--repo",
      repo,
      "--files",
      "sample.cpp",
      "--mutators",
      [
        "EqualityOperator",
        "LogicalOperator",
        "ShiftOperator",
        "UnaryOperator",
        "UpdateOperator",
        "BitwiseOperator",
        "ArithmeticOperator",
        "AssignmentOperator",
        "StandardLibraryCall",
        "MoveSemantics",
        "ContainerCall",
        "ContainerStateCall",
        "StringCall",
        "MathCall",
        "IteratorCall",
        "ChronoCall",
        "RegexCall",
        "FilesystemCall",
        "ObjCMessageSend",
        "ObjCBoolLiteral",
        "MetalThreadPosition",
      ].join(","),
    ]).stdout,
  );
  const names = new Set(listed.map((mutant) => mutant.mutator));
  for (const mutator of [
    "EqualityOperator",
    "LogicalOperator",
    "ShiftOperator",
    "UnaryOperator",
    "UpdateOperator",
    "BitwiseOperator",
    "ArithmeticOperator",
    "AssignmentOperator",
    "StandardLibraryCall",
    "MoveSemantics",
    "ContainerCall",
    "ContainerStateCall",
    "StringCall",
    "MathCall",
    "IteratorCall",
    "ChronoCall",
    "RegexCall",
    "FilesystemCall",
    "ObjCMessageSend",
    "ObjCBoolLiteral",
    "MetalThreadPosition",
  ]) {
    assert(names.has(mutator), `catalog evidence missing ${mutator}`);
  }
  const ranged = listed.filter((mutant) => mutant.sourceRange && mutant.rewriteStrategy);
  assert(ranged.length >= 2, "list-mutants should expose sourceRange and rewriteStrategy for range-aware candidates");
  assert(
    ranged.some((mutant) => mutant.rewriteStrategy === "token-binary-expression"),
    "expected token binary expression rewrite strategy in list-mutants output",
  );
  const catalogPairs = new Set(listed.map((mutant) => `${mutant.mutator}:${mutant.original}->${mutant.mutated}`));
  assert(catalogPairs.has("ShiftOperator:<<->>>"), "catalog evidence missing left-shift mutation");
  assert(catalogPairs.has("ShiftOperator:<<=->>>="), "catalog evidence missing compound left-shift mutation");
  assert(catalogPairs.has("UnaryOperator:-->+"), "catalog evidence missing unary negative sign mutation");
  assert(catalogPairs.has("UnaryOperator:+->-"), "catalog evidence missing unary positive sign mutation");
  assert(catalogPairs.has("UpdateOperator:x++->x--"), "catalog evidence missing suffix increment/decrement mutation");
  assert(catalogPairs.has("UpdateOperator:++x->--x"), "catalog evidence missing prefix increment/decrement mutation");
  assert(catalogPairs.has("UpdateOperator:x--->x++"), "catalog evidence missing suffix decrement/increment mutation");
  assert(catalogPairs.has("UpdateOperator:--x->++x"), "catalog evidence missing prefix decrement/increment mutation");
  assert(catalogPairs.has("BitwiseOperator:^->|"), "catalog evidence missing xor-to-or mutation");
  assert(catalogPairs.has("BitwiseOperator:^->&"), "catalog evidence missing xor-to-and mutation");
  assert(catalogPairs.has("ArithmeticOperator:%->*"), "catalog evidence missing modulo arithmetic mutation");
  assert(catalogPairs.has("AssignmentOperator:%=->*="), "catalog evidence missing modulo compound-assignment mutation");
  assert(catalogPairs.has("AssignmentOperator:&=->|="), "catalog evidence missing bitwise and-assignment mutation");
  assert(catalogPairs.has("AssignmentOperator:|=->&="), "catalog evidence missing bitwise or-assignment mutation");
  assert(catalogPairs.has("AssignmentOperator:^=->|="), "catalog evidence missing bitwise xor-assignment mutation");
  assert(catalogPairs.has("StandardLibraryCall:std::min(1, 2)->std::max(1, 2)"), "catalog evidence missing min/max standard-library mutation");
  assert(catalogPairs.has("StandardLibraryCall:std::max(1, 2)->std::min(1, 2)"), "catalog evidence missing max/min standard-library mutation");
  assert(catalogPairs.has("StandardLibraryCall:std::lower_bound(values.begin(), values.end(), 2)->std::upper_bound(values.begin(), values.end(), 2)"), "catalog evidence missing lower_bound/upper_bound standard-library mutation");
  assert(catalogPairs.has("StandardLibraryCall:std::upper_bound(values.begin(), values.end(), 2)->std::lower_bound(values.begin(), values.end(), 2)"), "catalog evidence missing upper_bound/lower_bound standard-library mutation");
  assert(catalogPairs.has("StandardLibraryCall:std::begin(values)->std::end(values)"), "catalog evidence missing begin/end standard-library mutation");
  assert(catalogPairs.has("StandardLibraryCall:std::end(values)->std::begin(values)"), "catalog evidence missing end/begin standard-library mutation");
  assert(catalogPairs.has("StandardLibraryCall:std::sort(values.begin(), values.end())->std::stable_sort(values.begin(), values.end())"), "catalog evidence missing sort/stable_sort standard-library mutation");
  assert(catalogPairs.has("StandardLibraryCall:std::stable_sort(values.begin(), values.end())->std::sort(values.begin(), values.end())"), "catalog evidence missing stable_sort/sort standard-library mutation");
  assert(catalogPairs.has("StandardLibraryCall:std::partition(values.begin(), values.end(), pred)->std::stable_partition(values.begin(), values.end(), pred)"), "catalog evidence missing partition/stable_partition standard-library mutation");
  assert(catalogPairs.has("StandardLibraryCall:std::stable_partition(values.begin(), values.end(), pred)->std::partition(values.begin(), values.end(), pred)"), "catalog evidence missing stable_partition/partition standard-library mutation");
  assert(catalogPairs.has("StandardLibraryCall:std::is_sorted(values.begin(), values.end())->std::is_heap(values.begin(), values.end())"), "catalog evidence missing is_sorted/is_heap standard-library mutation");
  assert(catalogPairs.has("StandardLibraryCall:std::is_heap(values.begin(), values.end())->std::is_sorted(values.begin(), values.end())"), "catalog evidence missing is_heap/is_sorted standard-library mutation");
  assert(catalogPairs.has("MoveSemantics:std::move(x)->x"), "catalog evidence missing std::move wrapper removal");
  assert(catalogPairs.has("MoveSemantics:std::forward<int>(y)->y"), "catalog evidence missing std::forward wrapper removal");
  assert(catalogPairs.has("ContainerCall:values.front()->values.back()"), "catalog evidence missing front/back container call mutation");
  assert(catalogPairs.has("ContainerCall:values.back()->values.front()"), "catalog evidence missing back/front container call mutation");
  assert(catalogPairs.has("ContainerStateCall:values.empty()->values.size()"), "catalog evidence missing empty/size container state mutation");
  assert(catalogPairs.has("ContainerStateCall:values.size()->values.empty()"), "catalog evidence missing size/empty container state mutation");
  assert(catalogPairs.has("ContainerStateCall:values.capacity()->values.size()"), "catalog evidence missing capacity/size container state mutation");
  assert(catalogPairs.has('StringCall:label.find("x")->label.rfind("x")'), "catalog evidence missing find/rfind string call mutation");
  assert(catalogPairs.has('StringCall:label.rfind("z")->label.find("z")'), "catalog evidence missing rfind/find string call mutation");
  assert(catalogPairs.has('StringCall:label.starts_with("a")->label.ends_with("a")'), "catalog evidence missing starts_with/ends_with string call mutation");
  assert(catalogPairs.has('StringCall:label.ends_with("z")->label.starts_with("z")'), "catalog evidence missing ends_with/starts_with string call mutation");
  assert(catalogPairs.has("MathCall:std::ceil(x)->std::floor(x)"), "catalog evidence missing ceil/floor math call mutation");
  assert(catalogPairs.has("MathCall:std::floor(x)->std::ceil(x)"), "catalog evidence missing floor/ceil math call mutation");
  assert(catalogPairs.has("MathCall:std::round(x)->std::trunc(x)"), "catalog evidence missing round/trunc math call mutation");
  assert(catalogPairs.has("MathCall:std::trunc(x)->std::round(x)"), "catalog evidence missing trunc/round math call mutation");
  assert(catalogPairs.has("IteratorCall:std::next(values.begin())->std::prev(values.begin())"), "catalog evidence missing next/prev iterator call mutation");
  assert(catalogPairs.has("IteratorCall:std::prev(values.end())->std::next(values.end())"), "catalog evidence missing prev/next iterator call mutation");
  assert(catalogPairs.has("ChronoCall:std::chrono::floor<std::chrono::seconds>(duration)->std::chrono::ceil<std::chrono::seconds>(duration)"), "catalog evidence missing floor/ceil chrono call mutation");
  assert(catalogPairs.has("ChronoCall:std::chrono::ceil<std::chrono::seconds>(duration)->std::chrono::floor<std::chrono::seconds>(duration)"), "catalog evidence missing ceil/floor chrono call mutation");
  assert(catalogPairs.has("RegexCall:std::regex_match(text, match, re)->std::regex_search(text, match, re)"), "catalog evidence missing regex_match/regex_search mutation");
  assert(catalogPairs.has("RegexCall:std::regex_search(text, match, re)->std::regex_match(text, match, re)"), "catalog evidence missing regex_search/regex_match mutation");
  assert(catalogPairs.has("FilesystemCall:std::filesystem::exists(path)->std::filesystem::is_empty(path)"), "catalog evidence missing exists/is_empty filesystem mutation");
  assert(catalogPairs.has("FilesystemCall:std::filesystem::is_empty(path)->std::filesystem::exists(path)"), "catalog evidence missing is_empty/exists filesystem mutation");
  assert(catalogPairs.has("FilesystemCall:std::filesystem::is_regular_file(path)->std::filesystem::is_directory(path)"), "catalog evidence missing is_regular_file/is_directory filesystem mutation");
  assert(catalogPairs.has("FilesystemCall:std::filesystem::is_directory(path)->std::filesystem::is_regular_file(path)"), "catalog evidence missing is_directory/is_regular_file filesystem mutation");

  const metal = JSON.parse(
    cli(repo, [
      "list-mutants",
      "--repo",
      repo,
      "--files",
      "shader.metal",
      "--include-metal",
      "--mutators",
      "MetalAddressSpace",
    ]).stdout,
  );
  assertEqual(new Set(metal.map((mutant) => mutant.mutator)).size, 1, "Metal evidence mutator set size");
  assertEqual(metal[0]?.mutator, "MetalAddressSpace", "Metal address-space mutator");
  return join(repo, "sample.cpp");
}

function checkOptionalAstFirst() {
  const probe = run(
    python,
    ["-c", "import clang.cindex"],
    { cwd: root, env: pythonEnv, allowedStatuses: [0, 1] },
  );
  if (probe.status !== 0) {
    console.log("[p1:evidence] skip clang-ast proof: optional clang bindings unavailable");
    return null;
  }
  const repo = createRepo("clang-ast", {
    "sample.cpp": "bool flag() { return true; }\nint main() { return flag() ? 0 : 1; }\n",
  });
  const listed = JSON.parse(
    cli(repo, [
      "list-mutants",
      "--repo",
      repo,
      "--files",
      "sample.cpp",
      "--mode",
      "clang-ast",
      "--mutators",
      "BooleanLiteral,ReturnValue",
    ]).stdout,
  );
  assert(listed.length > 0, "clang-ast proof should discover mutants");
  assert(
    listed.every((mutant) => mutant.nodeKind && mutant.sourceRange && mutant.rewriteStrategy),
    "clang-ast proof should expose nodeKind, sourceRange, and rewriteStrategy",
  );
  return join(repo, "sample.cpp");
}

function checkProjectAndSchedulerEvidence() {
  const repo = createRepo("project-scheduler", {
    "sample.cpp": [
      "int a() { return 1 == 1; }",
      "int b() { return 2 == 2; }",
      "int spacer() { return 0; }",
      "int c() { return 3 == 3; }",
      "int d() { return 4 == 4; }",
      "",
    ].join("\n"),
    "compile_commands.json": JSON.stringify([
      {
        directory: "${repo}",
        command: "c++ -std=c++17 -c sample.cpp -o sample.o",
        file: "sample.cpp",
        output: "sample.o",
      },
    ]),
  });
  writeFileSync(
    join(repo, "compile_commands.json"),
    JSON.stringify([
      {
        directory: repo,
        command: "c++ -std=c++17 -c sample.cpp -o sample.o",
        file: join(repo, "sample.cpp"),
        output: join(repo, "sample.o"),
      },
    ]),
  );
  run("git", ["add", "compile_commands.json"], { cwd: repo });
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
      "compile database evidence",
    ],
    { cwd: repo },
  );

  const coverage = join(repo, "coverage.json");
  writeFileSync(
    coverage,
    JSON.stringify({
      files: {
        "sample.cpp": {
          coveredLines: [1, 2, 4, 5],
          coveredTests: {
            1: ["MathTest.B", "MathTest.Shared"],
            2: ["MathTest.A", "MathTest.Shared"],
            4: ["MathTest.A", "MathTest.C", "MathTest.Shared"],
            5: ["MathTest.B", "MathTest.D", "MathTest.Shared"],
          },
        },
      },
    }),
  );
  const report = join(repo, "p1-project-scheduler.json");
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
      "false",
      "--coverage-file",
      coverage,
      "--coverage-test-command-template",
      "true {tests_space}",
      "--report",
      report,
      "--mutators",
      "EqualityOperator",
      "--batch-mutants",
      "--batch-size",
      "2",
      "--worktree-mode",
      "copy",
      "--skip-initial-test",
      "--quiet",
    ],
    [2],
  );
  const payload = readJson(report);
  assertEqual(payload.projectAnalysis.compileDatabase.present, true, "compile database present");
  assertEqual(payload.projectAnalysis.compileDatabase.status, "loaded", "compile database status");
  assertEqual(
    payload.projectAnalysis.sourceTargets[0].ownership.kind,
    "compile-database-unit",
    "source ownership kind",
  );
  assertEqual(payload.execution.testScheduler.schemaVersion, "stryker-cxx.test-scheduler.v1", "scheduler schema");
  assertEqual(payload.execution.testScheduler.strategy, "batched", "scheduler strategy");
  assertEqual(payload.execution.testScheduler.batchSessions, 2, "batch session count");
  assertEqual(payload.execution.testScheduler.coverageSelectedSessions, 2, "coverage-selected session count");
  assertEqual(payload.execution.batching.plan.length, 2, "batch plan count");
  assertEqual(payload.execution.batching.plan[0].batchIndex, 1, "first planned batch index");
  assertEqual(payload.execution.batching.plan[1].batchIndex, 2, "second planned batch index");
  assert(
    payload.execution.batching.plan.every((batch) => batch.heuristic === "first-fit non-overlap"),
    "batch plan should record the deterministic heuristic",
  );
  assert(
    payload.execution.batching.plan.some((batch) =>
      batch.placement.some((placement) =>
        placement.placement === "new-batch" &&
        placement.placementReasons.includes("same-file adjacent-line isolation")
      )
    ),
    "batch plan should record placement reasons",
  );
  assert(
    payload.execution.batching.plan.some((batch) =>
      batch.placement.some((placement) => placement.placementReasons.includes("coverage-union minimized"))
    ),
    "batch plan should record coverage union minimization",
  );
  assert(
    payload.execution.batching.plan.every((batch) => batch.mutantIds.length === 2 && batch.locations.length === 2),
    "batch plan should record mutant ids and source locations",
  );
  assertEqual(payload.execution.testScheduler.groups[0].sessionId, "session-0001", "first session ID");
  assertEqual(payload.execution.testScheduler.groups[1].sessionId, "session-0002", "second session ID");
  assert(
    payload.execution.testScheduler.groups.every((group) => group.coverageSelected && group.selectedTests.length === 3),
    "batch scheduler groups should preserve selected test metadata",
  );
  return report;
}

function checkEquivalentSuppressionEvidence() {
  const repo = createRepo("equivalent-suppression", {
    "sample.cpp": [
      "#include <algorithm>",
      "bool redundant(bool flag) { return flag && flag; }",
      "int identity(int x) { return x + 0; }",
      "bool redundant_bits(int flags) { return (flags & flags) != 0; }",
      "int redundant_min(int x) { return std::min(x, x); }",
      "Iter redundant_range(Iter first, int value) { return std::lower_bound(first, first, value); }",
      "int redundant_choice(bool flag, int x) { return flag ? x : x; }",
      "int* null_style() { return nullptr; }",
      "",
    ].join("\n"),
  });

  const conservativeReport = join(repo, "equivalent-conservative.json");
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
      "false",
      "--skip-initial-test",
      "--report",
      conservativeReport,
      "--mutators",
      "LogicalOperator,ArithmeticOperator,BitwiseOperator,StandardLibraryCall,ConditionalExpression",
      "--quiet",
    ],
  );
  const conservative = readJson(conservativeReport);
  assertEqual(conservative.ignored, 6, "conservative suppression ignored count");
  assertEqual(
    conservative.execution.analysis.equivalentSuppression.mode,
    "conservative",
    "conservative suppression mode",
  );
  const conservativeRules = new Set(
    conservative.execution.analysis.equivalentSuppression.suppressions.map((item) => item.ruleId),
  );
  for (const rule of [
    "duplicate-logical-operand",
    "arithmetic-identity",
    "duplicate-bitwise-operand",
    "duplicate-standard-library-operands",
    "duplicate-standard-library-range",
    "duplicate-conditional-branches",
  ]) {
    assert(conservativeRules.has(rule), `conservative suppression missing ${rule}`);
  }

  const offReport = join(repo, "equivalent-off.json");
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
      "false",
      "--skip-initial-test",
      "--report",
      offReport,
      "--mutators",
      "LogicalOperator,ArithmeticOperator",
      "--equivalent-suppression",
      "off",
      "--quiet",
    ],
  );
  const disabled = readJson(offReport);
  assertEqual(disabled.ignored, 0, "disabled suppression ignored count");
  assert(disabled.killed >= 2, "disabled suppression should expose runnable mutants");
  assertEqual(disabled.execution.analysis.equivalentSuppression.mode, "off", "disabled suppression mode");

  const aggressiveReport = join(repo, "equivalent-aggressive.json");
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
      "false",
      "--skip-initial-test",
      "--report",
      aggressiveReport,
      "--mutators",
      "NullLiteral",
      "--equivalent-suppression",
      "aggressive",
      "--quiet",
    ],
  );
  const aggressive = readJson(aggressiveReport);
  assertEqual(aggressive.ignored, 1, "aggressive suppression ignored count");
  assertEqual(
    aggressive.execution.analysis.equivalentSuppression.suppressions[0].ruleId,
    "style-equivalent-null-literal",
    "aggressive-only suppression rule",
  );
  return aggressiveReport;
}

const evidence = [
  checkDiscoveryMetadata(),
  checkProjectAndSchedulerEvidence(),
  checkEquivalentSuppressionEvidence(),
  checkOptionalAstFirst(),
].filter(Boolean);

for (const artifact of evidence) {
  console.log(`[p1:evidence] ${artifact}`);
}
console.log("[p1:evidence] ok");
