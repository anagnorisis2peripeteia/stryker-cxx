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
from stryker_cxx.build_adapters import adapter_commands, checker_command
from stryker_cxx.schema import (
    validate_mte,
    validate_report,
    require_mte,
    require_report,
)
from stryker_cxx.payload_contract import (
    extract_mte_payload,
    native_to_mte_status,
    supported_mte_statuses,
    supported_native_statuses,
)
from stryker_cxx.project_analysis import analyze_project
from stryker_cxx.mutation_artifacts import (
    materialize_mutation_artifact,
    mutation_artifact_metadata,
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
                "compilePruning": {
                    "enabled": True,
                    "strategy": "source-overlay-prune-and-retry",
                    "candidateArtifactMode": "source-overlay",
                    "attempts": 0,
                    "candidateMutants": 0,
                    "failedBatches": 0,
                    "retryBatches": 0,
                    "prunedMutants": 0,
                    "buildErrors": 0,
                    "checkErrors": 0,
                    "records": [],
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
                "mutationArtifact": {
                    "schemaVersion": "stryker-cxx.mutation-artifact.v1",
                    "mode": "source-overlay",
                    "implementation": "copy",
                    "workspacePerMutant": True,
                    "parallelSafe": True,
                    "supportsCompiledReplacement": False,
                    "retainArtifacts": True,
                    "retainArtifactsFor": ["SURVIVED", "TIMEOUT"],
                    "sourceOverlay": {
                        "strategy": "isolated-copy",
                        "restoration": "discard-copy-or-retain",
                    },
                },
                "artifactPlacement": {
                    "schemaVersion": "stryker-cxx.artifact-placement.v1",
                    "mode": "source-overlay",
                    "implementation": "copy",
                    "artifactRoot": "agent_space/stryker-cxx",
                    "workerTmpDir": "/tmp/stryker-cxx-workers",
                    "restoreOriginals": True,
                    "retainArtifacts": True,
                    "retainArtifactsFor": ["SURVIVED", "TIMEOUT"],
                    "sourceOverlay": {
                        "restorePolicy": "discard-copy-or-retain",
                        "placement": "isolated-copy",
                    },
                    "compiledArtifacts": {
                        "supported": False,
                        "placement": "not-supported",
                        "restorePolicy": "not-supported",
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
        self.assertEqual(payload["toolVersion"], "0.1.0")
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
        self.assertEqual(payload["mutationArtifact"]["schemaVersion"], "stryker-cxx.mutation-artifact.v1")
        self.assertEqual(payload["mutationArtifact"]["mode"], "source-overlay")
        self.assertEqual(payload["mutationArtifact"]["implementation"], "copy")
        self.assertEqual(payload["artifactPlacement"]["schemaVersion"], "stryker-cxx.artifact-placement.v1")
        self.assertTrue(payload["artifactPlacement"]["restoreOriginals"])
        self.assertFalse(payload["artifactPlacement"]["compiledArtifacts"]["supported"])
        self.assertEqual(payload["execution"]["compilePruning"]["strategy"], "source-overlay-prune-and-retry")
        self.assertEqual(payload["execution"]["testScheduler"]["schemaVersion"], "stryker-cxx.test-scheduler.v1")
        self.assertEqual(payload["execution"]["testScheduler"]["strategy"], "batched")
        self.assertEqual(payload["lifecycle"]["schemaVersion"], "stryker-cxx.lifecycle.v1")
        self.assertIn("projectAnalysis", payload["lifecycle"]["phaseOrder"])
        self.assertIn("coverageAnalysis", payload["lifecycle"]["phaseOrder"])
        lifecycle_by_name = {
            phase["name"]: phase
            for phase in payload["lifecycle"]["phases"]
        }
        self.assertEqual(lifecycle_by_name["mutationArtifact"]["status"], "sourceLevel")
        self.assertEqual(lifecycle_by_name["mutationArtifact"]["detail"]["artifactMode"], "source-overlay")
        self.assertEqual(lifecycle_by_name["mutationArtifact"]["detail"]["implementation"], "copy")
        self.assertEqual(lifecycle_by_name["compilePruning"]["status"], "completed")
        self.assertEqual(lifecycle_by_name["compilePruning"]["detail"]["prunedMutants"], 0)
        self.assertEqual(lifecycle_by_name["testScheduling"]["detail"]["scheduler"], "batched")
        self.assertTrue(lifecycle_by_name["artifactRestoration"]["detail"]["restoreOriginals"])
        self.assertEqual(payload["summary"]["byStatus"]["SURVIVED"], 1)
        self.assertEqual(payload["summary"]["byFile"]["src/foo.cpp"]["survived"], 1)
        self.assertEqual(payload["summary"]["byMutator"]["ConditionalBoundary"]["killed"], 1)
        self.assertEqual(validate_report(payload), [])

    def test_report_validator_accepts_legacy_payload_without_lifecycle(self) -> None:
        payload = _report_dict(self._base_report())
        payload.pop("lifecycle")

        self.assertEqual(validate_report(payload), [])

    def test_report_validator_checks_lifecycle_shape_when_present(self) -> None:
        payload = _report_dict(self._base_report())
        payload["lifecycle"]["phases"][0]["detail"] = "not an object"

        errors = validate_report(payload)

        self.assertTrue(any("lifecycle.phases[0].detail" in item for item in errors))

    def test_report_validator_checks_mutation_artifact_shape_when_present(self) -> None:
        payload = _report_dict(self._base_report())
        payload["mutationArtifact"]["workspacePerMutant"] = "yes"

        errors = validate_report(payload)

        self.assertTrue(any("mutationArtifact.workspacePerMutant" in item for item in errors))

    def test_report_validator_checks_artifact_placement_shape_when_present(self) -> None:
        payload = _report_dict(self._base_report())
        payload["artifactPlacement"]["restoreOriginals"] = "yes"

        errors = validate_report(payload)

        self.assertTrue(any("artifactPlacement.restoreOriginals" in item for item in errors))

    def test_report_validator_checks_compile_pruning_shape_when_present(self) -> None:
        payload = _report_dict(self._base_report())
        payload["execution"]["compilePruning"]["prunedMutants"] = "one"

        errors = validate_report(payload)

        self.assertTrue(any("execution.compilePruning.prunedMutants" in item for item in errors))

    def test_report_validator_checks_test_scheduler_shape_when_present(self) -> None:
        payload = _report_dict(self._base_report())
        payload["execution"]["testScheduler"]["sessions"] = "one"

        errors = validate_report(payload)

        self.assertTrue(any("execution.testScheduler.sessions" in item for item in errors))

    def test_mutation_artifact_metadata_describes_source_overlay(self) -> None:
        metadata = mutation_artifact_metadata(
            "git-worktree",
            worker_tmp_dir="/tmp/stryker-cxx",
            retain_worktrees=True,
            retain_worktrees_for=["SURVIVED"],
            worker_label="worker-1",
        )

        self.assertEqual(metadata["schemaVersion"], "stryker-cxx.mutation-artifact.v1")
        self.assertEqual(metadata["mode"], "source-overlay")
        self.assertEqual(metadata["implementation"], "git-worktree")
        self.assertTrue(metadata["workspacePerMutant"])
        self.assertEqual(metadata["sourceOverlay"]["strategy"], "isolated-git-worktree")

    def test_mutation_artifact_materializes_inplace_and_copy_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root) / "repo"
            repo.mkdir()
            (repo / "sample.cpp").write_text("int main() { return 0; }\n")

            with materialize_mutation_artifact(str(repo), "inplace") as artifact:
                self.assertEqual(artifact.work_repo, str(repo))
                self.assertEqual(artifact.run_metadata()["implementation"], "inplace")

            copy_path = ""
            with materialize_mutation_artifact(str(repo), "copy", worker_tmp_dir=root) as artifact:
                copy_path = artifact.work_repo
                self.assertNotEqual(copy_path, str(repo))
                self.assertTrue((Path(copy_path) / "sample.cpp").exists())
                self.assertEqual(artifact.run_metadata()["mode"], "source-overlay")

            self.assertFalse(Path(copy_path).exists())

    def test_report_validator_accepts_project_analysis_metadata(self) -> None:
        rep = self._base_report()
        rep.execution["projectAnalysis"] = {
            "schemaVersion": "stryker-cxx.project-analysis.v1",
            "confidence": "high",
            "targetFiles": ["src/foo.cpp"],
            "buildSystems": [{"name": "cmake", "source": "explicit", "confidence": "high"}],
            "compileDatabase": {"present": False},
            "sourceTargets": [{"file": "src/foo.cpp", "confidence": "medium"}],
            "buildTargets": [{"name": "all", "kind": "build", "confidence": "high"}],
            "testTargets": [{"name": "math", "kind": "ctest", "confidence": "medium"}],
            "commands": {"build": "cmake --build build", "check": None, "test": "ctest"},
        }
        payload = _report_dict(rep)

        self.assertEqual(payload["projectAnalysis"]["confidence"], "high")
        self.assertEqual(validate_report(payload), [])
        phase = {
            item["name"]: item
            for item in payload["lifecycle"]["phases"]
        }["projectAnalysis"]
        self.assertEqual(phase["detail"]["confidence"], "high")

    def test_project_analysis_detects_cmake_ctest_fixture(self) -> None:
        repo = Path(__file__).resolve().parents[2] / "fixtures" / "adapters" / "cmake-ctest"

        analysis = analyze_project(
            str(repo),
            ["math_test.cpp"],
            build_system="cmake",
            build_dir="build",
            test_command="ctest --test-dir build",
        )

        self.assertEqual(analysis["confidence"], "high")
        self.assertIn("cmake", {item["name"] for item in analysis["buildSystems"]})
        self.assertIn("math_test", {item["name"] for item in analysis["buildTargets"]})
        self.assertIn("math", {item["name"] for item in analysis["testTargets"]})

    def test_project_analysis_detects_compile_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            source = repo / "sample.cpp"
            source.write_text("int main() { return 0; }\n")
            compile_db = [
                {
                    "directory": str(repo),
                    "command": "clang++ -std=c++17 -c sample.cpp -o sample.o",
                    "file": str(source),
                }
            ]
            (repo / "compile_commands.json").write_text(json.dumps(compile_db))

            analysis = analyze_project(str(repo), ["sample.cpp"])

        self.assertTrue(analysis["compileDatabase"]["present"])
        self.assertEqual(analysis["compileDatabase"]["entries"], 1)
        self.assertEqual(analysis["sourceTargets"][0]["compileDatabaseMatched"], True)

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
        self.assertEqual(payload["toolVersion"], "0.1.0")
        self.assertEqual(payload["retention"]["days"], 14)
        self.assertEqual(payload["retention"]["policy"], "delete-after-14-days")
        self.assertFalse(payload["privacy"]["sourceFilesIncluded"])
        self.assertTrue(payload["privacy"]["mutantSourceSnippetsIncluded"])
        self.assertTrue(payload["privacy"]["secretValuesRedacted"])
        self.assertEqual(payload["provenance"]["configHash"], "abc123")
        self.assertEqual(payload["provenance"]["toolVersion"], "0.1.0")
        self.assertEqual(payload["provenance"]["upload"]["authTokenEnv"], "STRYKER_CXX_DASHBOARD_TOKEN")
        self.assertEqual(payload["thresholdStatus"], "high")
        self.assertEqual(payload["thresholds"]["status"], "high")
        self.assertIn("runId", payload)
        self.assertEqual(payload["provenance"]["upload"]["status"], "notAttempted")

    def test_build_system_adapter_commands(self) -> None:
        cmake = adapter_commands("cmake", "build", "all", None, "Foo.*")
        self.assertIn("cmake --build 'build' --target 'all'", cmake["build"])
        self.assertIn("ctest --test-dir 'build'", cmake["test"])
        self.assertIn("--tests-regex 'Foo.*'", cmake["test"])

        bazel = adapter_commands("bazel", None, "//lib:target", "//lib:test", None)
        self.assertEqual(bazel["build"], "bazel build '//lib:target'")
        self.assertEqual(bazel["test"], "bazel test '//lib:test'")

        gtest = adapter_commands(None, None, None, None, "Math.*", "gtest", "./math_tests", None)
        self.assertEqual(gtest["test"], "'./math_tests' --gtest_filter='Math.*'")

        catch2 = adapter_commands("ninja", "build", "all", None, "[fast]", "catch2", "./catch_tests", None)
        self.assertEqual(catch2["test"], "'./catch_tests' --reporter compact '[fast]'")

        xctest = adapter_commands(
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
        clang_tidy = checker_command(
            "clang-tidy",
            "--checks=-*,bugprone-*",
            "src/foo.cpp,src/bar.cpp",
        )
        cppcheck = checker_command(
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

            gtest = adapter_commands(None, "build", None, None, "Math.*", "gtest", None, None, str(repo))

            self.assertEqual(gtest["test"], f"'{binary}' --gtest_filter='Math.*'")

    def test_report_validator_catches_missing_total_mutants(self) -> None:
        rep = self._base_report()
        payload = _report_dict(rep)
        payload.pop("totalMutants")
        errors = validate_report(payload)
        self.assertTrue(any("totalMutants" in item for item in errors))

    def test_report_validator_catches_missing_tool_version(self) -> None:
        rep = self._base_report()
        payload = _report_dict(rep)
        payload.pop("toolVersion")
        errors = validate_report(payload)
        self.assertTrue(any("toolVersion" in item for item in errors))

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

    def test_payload_contract_extracts_direct_and_wrapped_mte(self) -> None:
        rep = self._base_report()
        payload = _mutation_testing_elements(rep)
        self.assertIs(extract_mte_payload(payload), payload)

        nested = dict(payload)
        nested.pop("schemaVersion")
        wrapped = {
            "schemaVersion": "stryker-cxx.report.v1",
            "mutationTestingElements": nested,
        }
        extracted = extract_mte_payload(wrapped)

        self.assertEqual(extracted["schemaVersion"], "2.0")
        self.assertEqual(validate_mte(extracted), [])

    def test_payload_contract_owns_status_projection(self) -> None:
        self.assertEqual(native_to_mte_status("KILLED"), "Killed")
        self.assertEqual(native_to_mte_status("BUILD_ERROR"), "NoCoverage")
        self.assertEqual(native_to_mte_status("CHECK_ERROR"), "RuntimeError")
        self.assertEqual(native_to_mte_status("unknown"), "RuntimeError")
        self.assertIn("NO_COVERAGE", supported_native_statuses())
        self.assertIn("NoCoverage", supported_mte_statuses())

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
        self.assertTrue(_clang_mutation_is_ast_confirmed("ShiftOperator", ["UNEXPOSED_EXPR", "BINARY_OPERATOR"]))
        self.assertTrue(_clang_mutation_is_ast_confirmed("UpdateOperator", ["UNEXPOSED_EXPR", "UNARY_OPERATOR"]))
        self.assertTrue(_clang_mutation_is_ast_confirmed("CallRemoval", ["CALL_EXPR", "COMPOUND_STMT"]))
        self.assertTrue(_clang_mutation_is_ast_confirmed("StatementRemoval", ["EXPR_STMT", "COMPOUND_STMT"]))
        self.assertTrue(_clang_mutation_is_ast_confirmed("BlockRemoval", ["COMPOUND_STMT", "STMT_EXPR"]))
        self.assertTrue(_clang_mutation_is_ast_confirmed("ReturnValue", ["RETURN_STMT", "FUNCTION_DECL"]))
        self.assertTrue(_clang_mutation_is_ast_confirmed("ConditionalExpression", ["COMPOUND_STMT", "CONDITIONAL_OPERATOR"]))
        self.assertTrue(_clang_mutation_is_ast_confirmed("LoopBoundary", ["FOR_STMT", "BINARY_OPERATOR"]))
        self.assertTrue(_clang_mutation_is_ast_confirmed("LoopCondition", ["WHILE_STMT", "BINARY_OPERATOR"]))
        self.assertTrue(_clang_mutation_is_ast_confirmed("StandardLibraryCall", ["CALL_EXPR", "DECL_REF_EXPR"]))
        self.assertTrue(_clang_mutation_is_ast_confirmed("MemoryOrder", ["CALL_EXPR", "DECL_REF_EXPR"]))
        self.assertTrue(_clang_mutation_is_ast_confirmed("MemberAccessOperator", ["MEMBER_REF_EXPR", "UNEXPOSED_EXPR"]))
        self.assertTrue(_clang_mutation_is_ast_confirmed("ExceptionHandling", ["CXX_THROW_EXPR", "COMPOUND_STMT"]))
        self.assertTrue(_clang_mutation_is_ast_confirmed("ObjCMessageSend", ["OBJC_MESSAGE_EXPR", "COMPOUND_STMT"]))
        self.assertTrue(_clang_mutation_is_ast_confirmed("ObjCBoolLiteral", ["OBJC_BOOL_LITERAL_EXPR"]))
        self.assertTrue(_clang_mutation_is_ast_confirmed("MetalThreadPosition", ["PARM_DECL", "UNEXPOSED_ATTR"]))
        self.assertTrue(_clang_mutation_is_ast_confirmed("MetalAddressSpace", ["PARM_DECL", "TYPE_REF"]))
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

    def test_clang_ast_first_discovery_generates_direct_conditional_expression_mutant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.cpp"
            source.write_text("int choose(bool flag) { return flag ? 1 : 2; }\n")
            ranges = [
                {
                    "kind": "CONDITIONAL_OPERATOR",
                    "startLine": 1,
                    "startColumn": 1,
                    "endLine": 1,
                    "endColumn": 48,
                }
            ]

            mutants = _discover_clang_ast_first(
                tmp,
                "sample.cpp",
                None,
                ["ConditionalExpression"],
                ranges,
            )

            conditional_mutants = [mut for mut in mutants if mut.mutator == "ConditionalExpression"]
            self.assertTrue(
                any(mut.original == "flag ? 1 : 2" and mut.mutated == "flag ? 2 : 1" for mut in conditional_mutants)
            )
            self.assertTrue(all(mut.nodeKind == "CONDITIONAL_OPERATOR" for mut in conditional_mutants))
            self.assertTrue(all(mut.rewriteStrategy == "clang-ast-direct-conditional" for mut in conditional_mutants))
            self.assertTrue(all(mut.sourceRange["kind"] == "CONDITIONAL_OPERATOR" for mut in conditional_mutants))

    def test_clang_ast_first_discovery_handles_loop_boundary_and_condition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.cpp"
            source.write_text("for (int i = 0; i < 10; ++i) {}\nwhile (i >= 0) {}\n")
            ranges = [
                {
                    "kind": "FOR_STMT",
                    "startLine": 1,
                    "startColumn": 1,
                    "endLine": 1,
                    "endColumn": 31,
                },
                {
                    "kind": "WHILE_STMT",
                    "startLine": 2,
                    "startColumn": 1,
                    "endLine": 2,
                    "endColumn": 18,
                },
            ]

            loop_boundary_mutants = _discover_clang_ast_first(
                tmp,
                "sample.cpp",
                None,
                ["LoopBoundary"],
                ranges,
            )
            self.assertTrue(any(mut.mutator == "LoopBoundary" and mut.original == "<" for mut in loop_boundary_mutants))
            self.assertTrue(any(mut.nodeKind == "FOR_STMT" for mut in loop_boundary_mutants))

            loop_condition_mutants = _discover_clang_ast_first(
                tmp,
                "sample.cpp",
                None,
                ["LoopCondition"],
                ranges,
            )
            self.assertTrue(any(mut.mutator == "LoopCondition" and mut.original == "i < 10" for mut in loop_condition_mutants))
            self.assertTrue(any(mut.mutator == "LoopCondition" and mut.original == "i >= 0" for mut in loop_condition_mutants))
            self.assertEqual(all(mut.rewriteStrategy == "clang-ast-source-range" for mut in loop_boundary_mutants + loop_condition_mutants), True)

    def test_clang_ast_first_discovery_handles_expanded_source_range_mutators(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.cpp"
            source.write_text("void f(Node node, bool fail) { node.value; if (fail) { throw; } }\n")
            ranges = [
                {
                    "kind": "MEMBER_REF_EXPR",
                    "startLine": 1,
                    "startColumn": 1,
                    "endLine": 1,
                    "endColumn": 68,
                },
                {
                    "kind": "CXX_THROW_EXPR",
                    "startLine": 1,
                    "startColumn": 1,
                    "endLine": 1,
                    "endColumn": 68,
                },
            ]

            member_mutants = _discover_clang_ast_first(
                tmp,
                "sample.cpp",
                None,
                ["MemberAccessOperator"],
                ranges,
            )
            self.assertTrue(any(mut.mutator == "MemberAccessOperator" and mut.original == "." for mut in member_mutants))
            self.assertEqual(all(mut.rewriteStrategy == "clang-ast-source-range" for mut in member_mutants), True)

            exception_mutants = _discover_clang_ast_first(
                tmp,
                "sample.cpp",
                None,
                ["ExceptionHandling"],
                ranges,
            )
            self.assertTrue(any(mut.mutator == "ExceptionHandling" and mut.mutated == "(void)0;" for mut in exception_mutants))
            self.assertEqual(all(mut.rewriteStrategy == "clang-ast-source-range" for mut in exception_mutants), True)

            objc_source = Path(tmp) / "sample.mm"
            objc_source.write_text("BOOL enabled() { return YES; }\n")
            objc_ranges = [
                {
                    "kind": "OBJC_BOOL_LITERAL_EXPR",
                    "startLine": 1,
                    "startColumn": 1,
                    "endLine": 1,
                    "endColumn": 31,
                }
            ]
            objc_mutants = _discover_clang_ast_first(
                tmp,
                "sample.mm",
                None,
                ["ObjCBoolLiteral"],
                objc_ranges,
            )
            self.assertTrue(any(mut.mutator == "ObjCBoolLiteral" and mut.original == "YES" for mut in objc_mutants))
            self.assertEqual(all(mut.rewriteStrategy == "clang-ast-source-range" for mut in objc_mutants), True)

            metal_source = Path(tmp) / "shader.metal"
            metal_source.write_text("kernel void shade(device float* out) {}\n")
            metal_ranges = [
                {
                    "kind": "PARM_DECL",
                    "startLine": 1,
                    "startColumn": 1,
                    "endLine": 1,
                    "endColumn": 41,
                }
            ]
            metal_mutants = _discover_clang_ast_first(
                tmp,
                "shader.metal",
                None,
                ["MetalAddressSpace"],
                metal_ranges,
            )
            self.assertTrue(any(mut.mutator == "MetalAddressSpace" and mut.original == "device" for mut in metal_mutants))
            self.assertEqual(all(mut.rewriteStrategy == "clang-ast-source-range" for mut in metal_mutants), True)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
