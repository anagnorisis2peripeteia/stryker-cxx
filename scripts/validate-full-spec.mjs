#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, resolve } from "node:path";

function run(command, args, options = {}) {
  console.log(`[run] ${command} ${args.join(" ")}`);
  const result = spawnSync(command, args, {
    stdio: "inherit",
    shell: process.platform === "win32",
    ...options,
  });
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

function cleanFixtureArtifacts() {
  rmSync("fixtures/adapters/make/math_test", { force: true });
  rmSync("fixtures/adapters/ninja/math_test", { force: true });
  rmSync("fixtures/adapters/ninja/.ninja_log", { force: true });
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
  throw new Error("python3 or python is required");
}

const python = findPython();
const pythonPath = resolve("python");
const pythonEnv = {
  ...process.env,
  PYTHONPATH: process.env.PYTHONPATH
    ? `${pythonPath}${delimiter}${process.env.PYTHONPATH}`
    : pythonPath,
};

console.log("[stryker-cxx] node/npm/python contract checks");
run("npm", ["run", "lint"]);
run("npm", ["test"]);
run("npm", ["run", "schema:check"]);
run("npm", ["run", "evidence:p0"]);
run("npm", ["run", "evidence:p1"]);
run("npm", ["run", "evidence:p2"]);
run("npm", ["run", "package:check"]);
run("git", ["diff", "--check"]);

console.log("[stryker-cxx] CLI smoke checks");
run("node", ["bin/stryker-cxx.js", "--version"]);
const tmpConfig = resolve(tmpdir(), "stryker-cxx-validation.yml");
run(python, [
  "-m",
  "stryker_cxx.cli",
  "init",
  "--path",
  tmpConfig,
  "--preset",
  "cmake",
  "--force",
], { env: pythonEnv });
if (!existsSync(tmpConfig)) {
  throw new Error(`config init smoke did not create ${tmpConfig}`);
}

console.log("[stryker-cxx] fixture smoke checks");
if (canRun("cmake") && canRun("ctest")) {
  const cmakeBuild = resolve(tmpdir(), "stryker-cxx-cmake-fixture");
  rmSync(cmakeBuild, { recursive: true, force: true });
  run("cmake", ["-S", "fixtures/adapters/cmake-ctest", "-B", cmakeBuild]);
  run("cmake", ["--build", cmakeBuild]);
  run("ctest", ["--test-dir", cmakeBuild, "--output-on-failure"]);
} else {
  console.log("[skip] cmake/ctest fixture smoke: cmake or ctest not installed");
}

if (canRun("ninja")) {
  run("ninja", ["-C", "fixtures/adapters/ninja", "test"]);
} else {
  console.log("[skip] ninja fixture smoke: ninja not installed");
}

if (canRun("make")) {
  run("make", ["-C", "fixtures/adapters/make", "test"]);
} else {
  console.log("[skip] make fixture smoke: make not installed");
}

if (canRun("meson")) {
  const mesonBuild = resolve(tmpdir(), "stryker-cxx-meson-fixture");
  rmSync(mesonBuild, { recursive: true, force: true });
  run("meson", ["setup", mesonBuild, "fixtures/adapters/meson"]);
  run("meson", ["test", "-C", mesonBuild]);
} else {
  console.log("[skip] meson fixture smoke: meson not installed");
}

if (canRun("bazel")) {
  run("bazel", ["test", "//..."], { cwd: resolve("fixtures/adapters/bazel") });
} else {
  console.log("[skip] bazel fixture smoke: bazel not installed");
}

cleanFixtureArtifacts();
console.log("[stryker-cxx] full-spec local validation completed");
