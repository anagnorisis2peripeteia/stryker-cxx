#!/usr/bin/env node
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

const packageJson = JSON.parse(readFileSync("package.json", "utf8"));
const scripts = new Set(Object.keys(packageJson.scripts ?? {}));
const externalScripts = new Set([
  "validate:stryker-cxx-provider",
]);
const docRoots = [
  "README.md",
  "CONTRIBUTING.md",
  "CHANGELOG.md",
  "SECURITY.md",
  "docs",
];

function collectMarkdown(path, out = []) {
  const stat = statSync(path);
  if (stat.isDirectory()) {
    for (const entry of readdirSync(path).sort()) {
      collectMarkdown(join(path, entry), out);
    }
    return out;
  }
  if (path.endsWith(".md")) out.push(path);
  return out;
}

const docs = [];
for (const root of docRoots) {
  docs.push(...collectMarkdown(resolve(root)));
}

const errors = [];
const npmRunPattern = /\bnpm\s+run\s+([A-Za-z0-9:_-]+)/g;
for (const doc of docs) {
  const rel = doc.slice(process.cwd().length + 1);
  const text = readFileSync(doc, "utf8");
  for (const match of text.matchAll(npmRunPattern)) {
    const script = match[1];
    if (!scripts.has(script) && !externalScripts.has(script)) {
      errors.push(`${rel}: references missing package script npm run ${script}`);
    }
  }
}

if (errors.length) {
  console.error("[docs:check] stale command references");
  for (const error of errors) console.error(`  - ${error}`);
  process.exit(1);
}

console.log(`[docs:check] ok ${docs.length} markdown files`);
