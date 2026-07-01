#!/usr/bin/env node
import { spawnSync } from "node:child_process";

const allowedRootFiles = new Set([
  "CHANGELOG.md",
  "CONTRIBUTING.md",
  "LICENSE",
  "README.md",
  "SECURITY.md",
  "package.json",
]);
const allowedPrefixes = [
  "bin/",
  "docs/",
  "fixtures/",
  "python/",
  "src/",
];
const requiredFiles = [
  "bin/stryker-cxx.js",
  "python/stryker_cxx/engine.py",
  "python/stryker_cxx/cli.py",
  "python/stryker_cxx/schema.py",
  "src/index.js",
  "src/payload-contract.js",
  "docs/spec.md",
  "docs/contract.md",
  "docs/schemas/stryker-cxx.report.schema.json",
  "fixtures/config/stryker-cxx.config.json",
  "README.md",
  "CONTRIBUTING.md",
  "CHANGELOG.md",
  "SECURITY.md",
  "LICENSE",
  "package.json",
];

function parsePackJson(stdout) {
  const text = stdout.trim();
  if (!text) throw new Error("npm pack --json produced no stdout");
  try {
    return JSON.parse(text);
  } catch {
    const start = text.indexOf("[");
    const end = text.lastIndexOf("]");
    if (start === -1 || end === -1 || end <= start) throw new Error("npm pack --json output was not JSON");
    return JSON.parse(text.slice(start, end + 1));
  }
}

function assertAllowed(filePath, errors) {
  if (allowedRootFiles.has(filePath)) return;
  if (allowedPrefixes.some((prefix) => filePath.startsWith(prefix))) return;
  errors.push(`unexpected package file: ${filePath}`);
}

const result = spawnSync("npm", ["pack", "--dry-run", "--json"], {
  encoding: "utf8",
  shell: process.platform === "win32",
});
if (result.status !== 0) {
  process.stderr.write(result.stderr || "");
  process.stdout.write(result.stdout || "");
  process.exit(result.status ?? 1);
}

const pack = parsePackJson(result.stdout);
if (!Array.isArray(pack) || !pack[0] || !Array.isArray(pack[0].files)) {
  throw new Error("npm pack --json did not include a files array");
}

const files = pack[0].files.map((entry) => entry.path).sort();
const fileSet = new Set(files);
const errors = [];

for (const filePath of files) assertAllowed(filePath, errors);
for (const required of requiredFiles) {
  if (!fileSet.has(required)) errors.push(`missing required package file: ${required}`);
}
if (files.some((filePath) => filePath.startsWith("tests/"))) {
  errors.push("tests/ must not be included in the npm package");
}
if (files.some((filePath) => filePath.startsWith("scripts/"))) {
  errors.push("scripts/ must not be included in the npm package");
}
if (files.some((filePath) => filePath.startsWith(".github/"))) {
  errors.push(".github/ must not be included in the npm package");
}

if (errors.length) {
  console.error("[package:check] package contents failed");
  for (const error of errors) console.error(`  - ${error}`);
  process.exit(1);
}

console.log(`[package:check] ok ${pack[0].filename} (${files.length} files)`);
