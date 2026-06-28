import assert from "node:assert/strict";
import test from "node:test";

import { assertMteReport, summarizeReport, toPlainMutationList } from "../src/index.js";

const sample = {
  schemaVersion: "2.0",
  language: "cpp",
  projectRoot: "/tmp/sample",
  files: {
    "src/foo.cpp": {
      source: "int main() { return 0; }\n",
      mutants: [
        {
          id: "src/foo.cpp:1:0:EqualityOperator:abc123",
          mutatorName: "EqualityOperator",
          description: "replaced equality operator",
          original: "==",
          replacement: "!=",
          status: "Killed",
          statusReason: "tests failed",
          runCommand: "./cxx-mutant run-mutant --id ...",
          location: {
            start: { line: 1, column: 1 },
            end: { line: 1, column: 3 },
          },
        },
        {
          id: "src/foo.cpp:2:3:LogicalOperator:def456",
          mutatorName: "LogicalOperator",
          description: "replaced boolean short-circuit operator",
          original: "&&",
          replacement: "||",
          status: "Survived",
          location: {
            start: { line: 2, column: 4 },
            end: { line: 2, column: 6 },
          },
        },
      ],
    },
  },
  testFiles: {},
};

const cxxReportWrapper = {
  schemaVersion: "cxx-mutant.report.v1",
  mutationTestingElements: sample,
  repo: "/tmp/sample",
  targetFiles: ["src/foo.cpp"],
  mutants: [],
};

test("valid MTE shape parses and summarizes", () => {
  const parsed = assertMteReport(sample);
  assert.equal(parsed.schemaVersion, "2.0");
  const flat = toPlainMutationList(parsed);
  assert.equal(flat.length, 2);
  const summary = summarizeReport(parsed);
  assert.equal(summary.total, 2);
  assert.equal(summary.killed, 1);
  assert.equal(summary.survived, 1);
  assert.equal(summary.score, 0.5);
});

test("valid cxx-mutant wrapper parses via mutationTestingElements", () => {
  const parsed = assertMteReport(cxxReportWrapper);
  assert.equal(parsed.schemaVersion, "2.0");
  const flat = toPlainMutationList(parsed);
  assert.equal(flat.length, 2);
  assert.equal(flat[0].status, "Killed");
  assert.equal(flat[1].status, "Survived");
});

test("invalid schema version is rejected", () => {
  const payload = structuredClone(sample);
  payload.schemaVersion = "1.0";
  assert.throws(() => assertMteReport(payload), /unexpected schemaVersion/);
});

test("adapter can consume cli output payload", () => {
  const summary = summarizeReport(sample);
  assert.equal(summary.projectRoot, "/tmp/sample");
  assert.equal(summary.survived, 1);
});
