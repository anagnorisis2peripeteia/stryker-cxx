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
    _dashboard_payload,
    _discover_clang_ast_first,
    _mutation_testing_elements,
    _rejects_macro_candidate,
    _report_dict,
)
from stryker_cxx.cli import _adapter_commands, _checker_command
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
            thresholds={"high": 0.9, "low": 0.8, "break": 0.7, "status": "high"},
            timeoutSeconds=120,
            buildCommand="ninja -C build target",
            checkCommand="clang++ -fsyntax-only src/foo.cpp",
            testCommand="./build/bin/test_binary",
            execution={
                "mode": "token",
                "worktreeMode": "copy",
                "jobs": 4,
                "initialTest": True,
                "dryRunOnly": False,
                "timeoutFactor": 1.5,
                "timeoutConstantMs": 5000,
                "effectiveTimeoutMs": 6200,
                "batching": {
                    "enabled": True,
                    "batchSize": 4,
                    "batches": 1,
                    "splitBatches": 0,
                    "batchedMutants": 2,
                },
                "dashboard": {
                    "version": "1",
                    "retentionDays": 14,
                    "exportPath": "agent_space/stryker-cxx/dashboard.json",
                    "upload": {
                        "enabled": True,
                        "urlConfigured": True,
                        "authTokenEnv": "STRYKER_CXX_DASHBOARD_TOKEN",
                        "authHeader": "Authorization",
                    },
                },
                "resourceIsolation": {
                    "worktreeMode": "copy",
                    "workspacePerMutant": True,
                    "parallelSafe": True,
                    "workerCount": 4,
                    "artifactDir": "agent_space/stryker-cxx",
                    "retainWorktrees": True,
                    "retainWorktreesFor": ["SURVIVED", "TIMEOUT"],
                    "retainedWorktreeTtlHours": 24,
                    "retainedWorktreeCleanup": {
                        "enabled": True,
                        "ttlHours": 24,
                        "removed": 0,
                        "errors": [],
                    },
                    "workerTmpDir": "/tmp/stryker-cxx-workers",
                    "environmentKeys": ["STRYKER_CXX_FLAG"],
                    "environmentInheritedKeys": ["PATH"],
                    "environmentBlockedKeys": ["GITHUB_TOKEN"],
                    "redaction": {
                        "enabled": True,
                        "environmentValues": True,
                        "secretAssignmentPatterns": True,
                        "replacement": "[REDACTED]",
                    },
                },
            },
            dryRun={
                "status": "PASSED",
                "build": {"exitCode": 0, "durationMs": 100, "log": "agent_space/stryker-cxx/dry_run_build.log"},
                "test": {"exitCode": 0, "durationMs": 800, "log": "agent_space/stryker-cxx/dry_run_test.log"},
            },
            baseline={
                "enabled": True,
                "path": ".stryker-cxx-baseline.json",
                "cacheHits": 1,
                "cacheMisses": 1,
                "cacheWrites": 2,
                "maxAgeDays": 7,
                "branch": "main",
                "missReasons": {"older than 7d": 1},
            },
            config={
                "path": "stryker-cxx.yml",
                "hash": "abc123",
                "effective": {"mode": "token"},
            },
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
        self.assertEqual(payload["dryRun"]["status"], "PASSED")
        self.assertEqual(payload["execution"]["effectiveTimeoutMs"], 6200)
        self.assertEqual(payload["thresholds"]["break"], 0.7)
        self.assertEqual(payload["commands"]["check"], "clang++ -fsyntax-only src/foo.cpp")
        self.assertEqual(payload["checkErrors"], 0)
        self.assertEqual(payload["noCoverage"], 0)
        self.assertEqual(payload["baseline"]["cacheHits"], 1)
        self.assertEqual(payload["config"]["hash"], "abc123")
        self.assertEqual(
            payload["execution"]["resourceIsolation"]["retainWorktreesFor"],
            ["SURVIVED", "TIMEOUT"],
        )
        self.assertEqual(payload["execution"]["resourceIsolation"]["environmentInheritedKeys"], ["PATH"])
        self.assertEqual(payload["execution"]["resourceIsolation"]["environmentBlockedKeys"], ["GITHUB_TOKEN"])
        self.assertTrue(payload["execution"]["resourceIsolation"]["redaction"]["enabled"])
        self.assertEqual(payload["execution"]["dashboard"]["retentionDays"], 14)
        self.assertEqual(payload["summary"]["byStatus"]["SURVIVED"], 1)
        self.assertEqual(payload["summary"]["byFile"]["src/foo.cpp"]["survived"], 1)
        self.assertEqual(payload["summary"]["byMutator"]["ConditionalBoundary"]["killed"], 1)
        self.assertEqual(validate_report(payload), [])

    def test_report_redacts_secret_assignments(self) -> None:
        rep = self._base_report()
        rep.buildCommand = "SECRET_TOKEN=topsecret123 ninja -C build"
        rep.testCommand = "API_KEY=topsecret456 ./build/tests"

        payload = _report_dict(rep)

        self.assertNotIn("topsecret123", json.dumps(payload))
        self.assertNotIn("topsecret456", json.dumps(payload))
        self.assertEqual(payload["commands"]["build"], "SECRET_TOKEN=[REDACTED] ninja -C build")
        self.assertEqual(payload["commands"]["test"], "API_KEY=[REDACTED] ./build/tests")

    def test_dashboard_payload_contains_policy_and_provenance(self) -> None:
        payload = _dashboard_payload(self._base_report())

        self.assertEqual(payload["schemaVersion"], "stryker-cxx.dashboard.v1")
        self.assertEqual(payload["dashboardVersion"], "1")
        self.assertEqual(payload["retention"]["days"], 14)
        self.assertEqual(payload["retention"]["policy"], "delete-after-14-days")
        self.assertEqual(payload["provenance"]["configHash"], "abc123")
        self.assertEqual(payload["provenance"]["upload"]["authTokenEnv"], "STRYKER_CXX_DASHBOARD_TOKEN")

    def test_build_system_adapter_commands(self) -> None:
        cmake = _adapter_commands("cmake", "build", "all", None, "Foo.*")
        self.assertIn("cmake --build 'build' --target 'all'", cmake["build"])
        self.assertIn("ctest --test-dir 'build'", cmake["test"])
        self.assertIn("--tests-regex 'Foo.*'", cmake["test"])

        bazel = _adapter_commands("bazel", None, "//lib:target", "//lib:test", None)
        self.assertEqual(bazel["build"], "bazel build '//lib:target'")
        self.assertEqual(bazel["test"], "bazel test '//lib:test'")

        gtest = _adapter_commands(None, None, None, None, "Math.*", "gtest", "./math_tests", None)
        self.assertEqual(gtest["test"], "'./math_tests' --gtest_filter='Math.*'")

        catch2 = _adapter_commands("ninja", "build", "all", None, "[fast]", "catch2", "./catch_tests", None)
        self.assertEqual(catch2["test"], "'./catch_tests' --reporter compact '[fast]'")

        xctest = _adapter_commands(
            None,
            None,
            None,
            None,
            "MathFixtureTests/testAdd",
            "xctest",
            None,
            "Build/Products/Debug-iphonesimulator/MathFixtureTests.xctestrun",
            None,
            "platform=iOS Simulator,name=iPhone 15",
            ["MathFixtureTests/testAdd"],
            ["MathFixtureTests/testSlow"],
        )
        self.assertEqual(
            xctest["test"],
            "xcodebuild test-without-building "
            "-xctestrun 'Build/Products/Debug-iphonesimulator/MathFixtureTests.xctestrun' "
            "-destination 'platform=iOS Simulator,name=iPhone 15' "
            "'-only-testing:MathFixtureTests/testAdd' "
            "'-skip-testing:MathFixtureTests/testSlow'",
        )

    def test_checker_adapter_commands(self) -> None:
        clang_tidy = _checker_command(
            "clang-tidy",
            "--checks=-*,bugprone-*",
            "src/foo.cpp,src/bar.cpp",
        )
        cppcheck = _checker_command(
            "cppcheck",
            "--enable=warning,style",
            "src/foo.cpp",
        )

        self.assertEqual(
            clang_tidy,
            "clang-tidy '--checks=-*,bugprone-*' 'src/foo.cpp' 'src/bar.cpp'",
        )
        self.assertEqual(cppcheck, "cppcheck '--enable=warning,style' 'src/foo.cpp'")

    def test_macro_candidate_rejection_records_analysis_diagnostic(self) -> None:
        analysis = {"engine": "clang-ast", "macroRejectedMutants": 0, "macroRejections": []}
        mut = Mutant("EqualityOperator", "sample.cpp", 3, 14, "==", "!=")
        mut.id = "sample.cpp:3:14:EqualityOperator:macro"
        mut.nodeKind = "BINARY_OPERATOR"
        macro_range = {
            "kind": "MACRO_INSTANTIATION",
            "startLine": 3,
            "startColumn": 10,
            "endLine": 3,
            "endColumn": 20,
        }

        rejected = _rejects_macro_candidate(analysis, "sample.cpp", [macro_range], mut)

        self.assertTrue(rejected)
        self.assertEqual(analysis["macroRejectedMutants"], 1)
        self.assertEqual(len(analysis["macroRejections"]), 1)
        self.assertEqual(analysis["macroRejections"][0]["reason"], "candidate overlaps a macro expansion range")

    def test_framework_adapter_discovers_single_repo_local_test_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            build = repo / "build"
            build.mkdir()
            binary = build / "math_tests"
            binary.write_text("#!/bin/sh\nexit 0\n")
            binary.chmod(0o755)

            gtest = _adapter_commands(None, "build", None, None, "Math.*", "gtest", None, None, str(repo))

            self.assertEqual(gtest["test"], f"'{binary}' --gtest_filter='Math.*'")

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
                "status": "NO_COVERAGE",
            },
        ]
        payload = _mutation_testing_elements(rep)
        self.assertEqual(payload["schemaVersion"], "2.0")
        self.assertEqual(payload["language"], "cpp")
        self.assertTrue(validate_mte(payload) == [])

        flat = payload["files"]["src/foo.cpp"]["mutants"]
        self.assertEqual(flat[0]["status"], "Killed")
        self.assertEqual(flat[1]["status"], "NoCoverage")

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

    def test_clang_ast_first_discovery_uses_cursor_ranges_before_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.cpp"
            source.write_text("int main() { return 1 == 1; }\n")
            ranges = [
                {
                    "kind": "BINARY_OPERATOR",
                    "startLine": 1,
                    "startColumn": 23,
                    "endLine": 1,
                    "endColumn": 29,
                }
            ]

            mutants = _discover_clang_ast_first(
                tmp,
                "sample.cpp",
                None,
                ["EqualityOperator"],
                ranges,
            )

            self.assertEqual(len(mutants), 1)
            self.assertEqual(mutants[0].nodeKind, "BINARY_OPERATOR")
            self.assertEqual(mutants[0].rewriteStrategy, "clang-ast-source-range")
            self.assertEqual(mutants[0].sourceRange["kind"], "BINARY_OPERATOR")


if __name__ == "__main__":
    raise SystemExit(unittest.main())
