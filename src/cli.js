#!/usr/bin/env node
import path from "node:path";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { assertMteReport, summarizeReport, survivors } from "./index.js";

function usage() {
  return [
    "Usage:",
    "  stryker-cxx --mte <path> [--summary|--survivors|--json]",
    "",
    "Options:",
    "  --mte <path>      required: mutation-testing-elements JSON file",
    "  --summary          print compact summary and exit",
    "  --survivors        print surviving mutant IDs",
    "  --json             emit summary JSON (used with --summary)",
    "",
  ].join("\n");
}

function parseArgs(argv) {
  const opts = { mode: "plain", format: "text" };
  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--help" || arg === "-h") {
      opts.help = true;
    } else if (arg === "--mte") {
      opts.mte = argv[i + 1];
      if (!opts.mte || opts.mte.startsWith("--")) {
        throw new Error("--mte requires a file path");
      }
      i += 1;
    } else if (arg === "--summary") {
      opts.mode = "summary";
    } else if (arg === "--survivors") {
      opts.mode = "survivors";
    } else if (arg === "--json") {
      opts.format = "json";
    } else {
      throw new Error(`unknown argument: ${arg}`);
    }
  }
  return opts;
}

function renderSummary(rep, toJson) {
  const summary = summarizeReport(rep);
  if (toJson) {
    return JSON.stringify(summary, null, 2);
  }
  const rows = [
    `language=${summary.language}`,
    `total=${summary.total}`,
    `killed=${summary.killed}`,
    `survived=${summary.survived}`,
    `compileErrors=${summary.compileErrors}`,
    `timedOut=${summary.timedOut}`,
    `pending=${summary.pending}`,
    `runtimeErrors=${summary.runtimeErrors}`,
    `score=${summary.score.toFixed(3)}`,
  ];
  return rows.join("\n");
}

function renderSurvivors(rep, toJson) {
  const list = survivors(rep);
  if (toJson) {
    return JSON.stringify(list, null, 2);
  }
  if (list.length === 0) {
    return "(no surviving mutants)";
  }
  return list
    .map((mut) => `${mut.file}:${mut.location.start.line}:${mut.location.start.column} ${mut.mutatorName} ${mut.original}->${mut.replacement}`)
    .join("\n");
}

export async function main(argv = process.argv) {
  let opts;
  try {
    opts = parseArgs(argv);
  } catch (err) {
    console.error(String(err));
    console.error(usage());
    return 2;
  }

  if (opts.help || !opts.mte) {
    console.log(usage());
    return opts.help ? 0 : 2;
  }

  const abs = path.resolve(process.cwd(), opts.mte);
  const raw = await readFile(abs, "utf8");
  const parsed = JSON.parse(raw);
  const report = assertMteReport(parsed);

  if (opts.mode === "survivors") {
    console.log(renderSurvivors(report, opts.format === "json"));
    return 0;
  }

  console.log(renderSummary(report, opts.format === "json"));
  return 0;
}

const __file = fileURLToPath(import.meta.url);
if (process.argv[1] && __file === path.resolve(process.argv[1])) {
  const code = await main();
  process.exitCode = code;
}
