from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stryker_cxx.engine import (
    Report,
    Mutant,
    _clang_matching_kinds,
    _clang_mutation_is_ast_confirmed,
    _clang_primary_node_kind,
    _mutation_testing_elements,
    _report_dict,
)
from stryker_cxx.schema import (
    validate_mte,
    validate_report,
    require_mte,
    require_report,
)


class TestContracts(unittest.TestCase):
    def _base_report(self) -> Report:
        rep = Report(
            target_files=["src/foo.cpp", "src/bar.mm"],
            repo="/tmp/repo",
            base="origin/main",
            threshold=0.8,
            timeoutSeconds=120,
            buildCommand="ninja -C build target",
            testCommand="./build/bin/test_binary",
            execution={"mode": "token", "worktreeMode": "copy", "jobs": 4},
            total=2,
            killed=1,
            survived=1,
        )
        rep.mutants = [
            {
                "id": "src/foo.cpp:1:0:EqualityOperator:abc123",
                "file": "src/foo.cpp",
                "line": 1,
                "column": 0,
                "mutator": "EqualityOperator",
                "original": "==",
                "mutated": "!=",
                "status": "SURVIVED",
                "durationMs": 15,
                "buildLog": "agent_space/stryker-cxx/build_a.log",
                "testLog": "agent_space/stryker-cxx/test_a.log",
                "detail": "all targeted tests passed",
                "run": {"reproCommand": "stryker-cxx run-mutant --id src/foo.cpp:1:0:EqualityOperator:abc123"},
            },
            {
                "id": "src/bar.mm:2:4:ConditionalBoundary:def456",
                "file": "src/bar.mm",
                "line": 2,
                "column": 4,
                "mutator": "ConditionalBoundary",
                "original": "<=",
                "mutated": "<",
                "status": "KILLED",
                "durationMs": 22,
            },
        ]
        return rep

    def test_report_v1_schema(self) -> None:
        rep = self._base_report()
        payload = _report_dict(rep)
        self.assertEqual(payload["schemaVersion"], "stryker-cxx.report.v1")
        self.assertEqual(validate_report(payload), [])

    def test_report_validator_catches_missing_total_mutants(self) -> None:
        rep = self._base_report()
        payload = _report_dict(rep)
        payload.pop("totalMutants")
        errors = validate_report(payload)
        self.assertTrue(any("totalMutants" in item for item in errors))

    def test_require_report_guard(self) -> None:
        rep = self._base_report()
        payload = _report_dict(rep)
        payload["score"] = 1.25
        with self.assertRaises(ValueError) as cm:
            require_report(payload)
        self.assertIn("score", str(cm.exception))

    def test_mutation_testing_elements_schema_and_mapping(self) -> None:
        rep = self._base_report()
        rep.repo = None
        rep.mutants = [
            {
                "id": "src/foo.cpp:1:0:EqualityOperator:abc123",
                "file": "src/foo.cpp",
                "line": 1,
                "col": 0,
                "mutator": "EqualityOperator",
                "original": "==",
                "mutated": "!=",
                "status": "KILLED",
            },
            {
                "id": "src/foo.cpp:4:9:LogicalOperator:def456",
                "file": "src/foo.cpp",
                "line": 4,
                "col": 9,
                "mutator": "LogicalOperator",
                "original": "&&",
                "mutated": "||",
                "status": "TIMEOUT",
            },
        ]
        payload = _mutation_testing_elements(rep)
        self.assertEqual(payload["schemaVersion"], "2.0")
        self.assertEqual(payload["language"], "cpp")
        self.assertTrue(validate_mte(payload) == [])

        flat = payload["files"]["src/foo.cpp"]["mutants"]
        self.assertEqual(flat[0]["status"], "Killed")
        self.assertEqual(flat[1]["status"], "Timeout")

    def test_ignored_mutants_are_valid_native_and_mte_statuses(self) -> None:
        rep = Report(
            target_files=["src/foo.cpp"],
            repo="/tmp/repo",
            total=1,
            ignored=1,
            buildCommand="true",
            testCommand="true",
        )
        rep.mutants = [
            {
                "id": "src/foo.cpp:1:0:EqualityOperator:ignored",
                "file": "src/foo.cpp",
                "line": 1,
                "col": 0,
                "mutator": "EqualityOperator",
                "original": "==",
                "mutated": "!=",
                "status": "IGNORED",
                "detail": "equivalent guard",
                "ignoreReason": "equivalent guard",
            }
        ]

        native = _report_dict(rep)
        self.assertEqual(validate_report(native), [])
        self.assertEqual(native["ignored"], 1)
        flat = native["mutationTestingElements"]["files"]["src/foo.cpp"]["mutants"]
        self.assertEqual(flat[0]["status"], "Ignored")
        self.assertEqual(flat[0]["statusReason"], "equivalent guard")

    def test_require_mte_guard(self) -> None:
        rep = self._base_report()
        rep.repo = None
        payload = _mutation_testing_elements(rep)
        # status outside the MTE vocabulary should fail hard.
        payload["files"]["src/foo.cpp"]["mutants"][0]["status"] = "Unknown"
        with self.assertRaises(ValueError) as cm:
            require_mte(payload)
        self.assertIn("unexpected status", str(cm.exception))


class TestPersistence(unittest.TestCase):
    def test_mute_projection_is_stable_for_file_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.cpp"
            source.write_text("int main() { return 0; }\n")

            rep = Report(
                target_files=["sample.cpp"],
                repo=tmp,
                total=1,
                killed=0,
                survived=1,
            )
            rep.mutants = [
                {
                    "id": "sample.cpp:1:0:UnaryOperator:aaa111",
                    "file": "sample.cpp",
                    "line": 1,
                    "col": 0,
                    "mutator": "UnaryOperator",
                    "original": "!",
                    "mutated": "",
                    "status": "SURVIVED",
                }
            ]
            payload = _mutation_testing_elements(rep)
            self.assertIn("sample.cpp", payload["files"])
            self.assertIn("source", payload["files"]["sample.cpp"])
            self.assertEqual(payload["files"]["sample.cpp"]["source"], "int main() { return 0; }\n")
            # Ensure JSON serialization stays valid with stable keys in sorted run order.
            json.dumps(payload)


class TestClangAstConfirmation(unittest.TestCase):
    def test_ast_classifier_confirms_mutator_specific_cursor_kinds(self) -> None:
        self.assertTrue(_clang_mutation_is_ast_confirmed("EqualityOperator", ["UNEXPOSED_EXPR", "BINARY_OPERATOR"]))
        self.assertTrue(_clang_mutation_is_ast_confirmed("CallRemoval", ["CALL_EXPR", "COMPOUND_STMT"]))
        self.assertTrue(_clang_mutation_is_ast_confirmed("ReturnValue", ["RETURN_STMT", "FUNCTION_DECL"]))
        self.assertFalse(_clang_mutation_is_ast_confirmed("EqualityOperator", ["TEMPLATE_REF", "TYPE_REF"]))
        self.assertEqual(_clang_primary_node_kind("EqualityOperator", ["UNEXPOSED_EXPR", "BINARY_OPERATOR"]), "BINARY_OPERATOR")

    def test_clang_matching_kinds_uses_source_span_containment(self) -> None:
        ranges = [
            {"kind": "FUNCTION_DECL", "startLine": 1, "startColumn": 1, "endLine": 5, "endColumn": 2},
            {"kind": "BINARY_OPERATOR", "startLine": 3, "startColumn": 10, "endLine": 3, "endColumn": 16},
            {"kind": "TYPE_REF", "startLine": 3, "startColumn": 1, "endLine": 3, "endColumn": 4},
        ]

        kinds = _clang_matching_kinds(ranges, line=3, col=11, original="==")
        self.assertEqual(kinds[0], "BINARY_OPERATOR")
        self.assertIn("FUNCTION_DECL", kinds)
        self.assertNotIn("TYPE_REF", kinds)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
