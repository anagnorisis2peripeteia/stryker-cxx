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
          runCommand: "./stryker-cxx run-mutant --id ...",
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
  schemaVersion: "stryker-cxx.report.v1",
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

test("valid stryker-cxx wrapper parses via mutationTestingElements", () => {
  const parsed = assertMteReport(cxxReportWrapper);
  assert.equal(parsed.schemaVersion, "2.0");
  const flat = toPlainMutationList(parsed);
  assert.equal(flat.length, 2);
  assert.equal(flat[0].status, "Killed");
  assert.equal(flat[1].status, "Survived");
});

test("summarizes standard timeout and noCoverage statuses", () => {
  const compatibilitySample = {
    schemaVersion: "2.0",
    language: "cpp",
    projectRoot: "/tmp/sample",
    files: {
      "src/foo.cpp": {
        source: "int main() { return 0; }\n",
        mutants: [
          {
            id: "src/foo.cpp:1:0:ArithmeticOperator:abc123",
            mutatorName: "ArithmeticOperator",
            original: "+",
            replacement: "-",
            status: "Timeout",
            location: { start: { line: 1, column: 1 }, end: { line: 1, column: 2 } },
          },
          {
            id: "src/foo.cpp:2:0:ArithmeticOperator:def456",
            mutatorName: "ArithmeticOperator",
            original: "+",
            replacement: "-",
            status: "NoCoverage",
            location: { start: { line: 2, column: 1 }, end: { line: 2, column: 2 } },
          },
        ],
      },
    },
    testFiles: {},
  };
  const parsed = assertMteReport(compatibilitySample);
  const summary = summarizeReport(parsed);
  assert.equal(summary.timedOut, 1);
  assert.equal(summary.compileErrors, 1);
  assert.equal(summary.score, 0.0);
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
