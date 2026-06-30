from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from stryker_cxx.cli import _adapter_commands, _checker_command


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CliContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.source = self.repo / "sample.cpp"
        self.source.write_text("int main() { if (1 == 1) return 0; return 1; }\n")
        self._git("init", "-q")
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "init")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=True,
            text=True,
            capture_output=True,
        )

    def _cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        package_root = Path(__file__).resolve().parents[2] / "python"
        env["PYTHONPATH"] = (
            f"{package_root}{os.pathsep}{env['PYTHONPATH']}"
            if env.get("PYTHONPATH")
            else str(package_root)
        )
        return subprocess.run(
            [sys.executable, "-m", "stryker_cxx.cli", *args],
            cwd=self.repo,
            env=env,
            text=True,
            capture_output=True,
        )

    def test_run_writes_native_report_and_restores_source(self) -> None:
        report = self.repo / "mutation.json"
        original = self.source.read_text()

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["schemaVersion"], "stryker-cxx.report.v1")
        self.assertEqual(payload["tool"], "stryker-cxx")
        self.assertEqual(payload["dryRun"]["status"], "PASSED")
        self.assertTrue(payload["execution"]["initialTest"])
        self.assertGreaterEqual(payload["execution"]["effectiveTimeoutMs"], 5000)
        self.assertIn("config", payload)
        self.assertIn("effective", payload["config"])
        self.assertEqual(payload["totalMutants"], 1)
        self.assertEqual(payload["survived"], 1)
        self.assertEqual(payload["score"], 0)
        self.assertEqual(payload["projectAnalysis"]["schemaVersion"], "stryker-cxx.project-analysis.v1")
        self.assertEqual(payload["projectAnalysis"]["confidence"], "high")
        self.assertIn("sample.cpp", payload["projectAnalysis"]["targetFiles"])
        self.assertEqual(payload["mutationArtifact"]["schemaVersion"], "stryker-cxx.mutation-artifact.v1")
        self.assertEqual(payload["mutationArtifact"]["mode"], "source-overlay")
        self.assertEqual(payload["mutationArtifact"]["implementation"], "inplace")
        self.assertEqual(payload["artifactPlacement"]["schemaVersion"], "stryker-cxx.artifact-placement.v1")
        self.assertEqual(payload["artifactPlacement"]["sourceOverlay"]["restorePolicy"], "restore-mutated-source")
        self.assertTrue(payload["artifactPlacement"]["restoreOriginals"])
        self.assertFalse(payload["artifactPlacement"]["compiledArtifacts"]["supported"])
        lifecycle_by_name = {
            phase["name"]: phase
            for phase in payload["lifecycle"]["phases"]
        }
        self.assertEqual(lifecycle_by_name["mutationArtifact"]["detail"]["artifactMode"], "source-overlay")
        self.assertEqual(lifecycle_by_name["mutationArtifact"]["detail"]["implementation"], "inplace")
        self.assertTrue(lifecycle_by_name["artifactRestoration"]["detail"]["restoreOriginals"])
        self.assertEqual(payload["mutationTestingElements"]["schemaVersion"], "2.0")
        first = payload["mutationTestingElements"]["files"]["sample.cpp"]["mutants"][0]
        self.assertEqual(first["status"], "Survived")
        self.assertIn("stryker-cxx run-mutant", first["runCommand"])
        placement = payload["mutants"][0]["run"]["artifactPlacement"]
        self.assertTrue(placement["originalArtifactsRestored"])
        self.assertTrue(placement["materializedArtifactRestored"])
        self.assertFalse(placement["materializedArtifactRetained"])

    def test_init_writes_default_config_and_refuses_overwrite(self) -> None:
        config = self.repo / "stryker-cxx.yml"

        created = self._cli("init", "--path", str(config))
        refused = self._cli("init", "--path", str(config))

        self.assertEqual(created.returncode, 0, created.stderr + created.stdout)
        self.assertIn("schemaVersion: stryker-cxx.config.v1", config.read_text())
        self.assertEqual(refused.returncode, 1)
        self.assertIn("config already exists", refused.stdout)

    def test_init_can_write_build_system_preset_config(self) -> None:
        config = self.repo / "cmake-stryker-cxx.yml"

        created = self._cli("init", "--path", str(config), "--preset", "cmake")

        self.assertEqual(created.returncode, 0, created.stderr + created.stdout)
        text = config.read_text()
        self.assertIn('buildSystem: "cmake"', text)
        self.assertIn('buildCommand: ""', text)

    def test_init_can_write_xcodebuild_preset_config(self) -> None:
        config = self.repo / "xcodebuild-stryker-cxx.yml"

        created = self._cli("init", "--path", str(config), "--preset", "xcodebuild")

        self.assertEqual(created.returncode, 0, created.stderr + created.stdout)
        text = config.read_text()
        self.assertIn('buildSystem: "xcodebuild"', text)
        self.assertIn('xcodeConfiguration: "Debug"', text)
        self.assertIn('testFramework: "xctest"', text)

    def test_init_can_write_framework_preset_config(self) -> None:
        config = self.repo / "cmake-gtest-stryker-cxx.yml"

        created = self._cli("init", "--path", str(config), "--preset", "cmake-gtest")

        self.assertEqual(created.returncode, 0, created.stderr + created.stdout)
        text = config.read_text()
        self.assertIn('buildSystem: "cmake"', text)
        self.assertIn('testFramework: "gtest"', text)
        self.assertIn('testBinary: ""', text)

    def test_xcodebuild_adapter_synthesizes_build_and_test_commands(self) -> None:
        commands = _adapter_commands(
            "xcodebuild",
            None,
            None,
            None,
            "AppTests/testFast",
            xcode_workspace="App.xcworkspace",
            xcode_scheme="AppTests",
            xcode_configuration="Debug",
            xcode_sdk="iphonesimulator",
            xcode_destination="platform=iOS Simulator,name=iPhone 15",
            xctest_only_testing=["AppTests/testSpecific"],
            xctest_skip_testing=["AppTests/testSlow"],
        )

        self.assertEqual(
            commands["build"],
            "xcodebuild build -workspace 'App.xcworkspace' -scheme 'AppTests' "
            "-configuration 'Debug' -sdk 'iphonesimulator' "
            "-destination 'platform=iOS Simulator,name=iPhone 15'",
        )
        self.assertEqual(
            commands["test"],
            "xcodebuild test -workspace 'App.xcworkspace' -scheme 'AppTests' "
            "-configuration 'Debug' -sdk 'iphonesimulator' "
            "-destination 'platform=iOS Simulator,name=iPhone 15' "
            "'-only-testing:AppTests/testSpecific' '-skip-testing:AppTests/testSlow'",
        )

    def test_clang_checker_adapter_synthesizes_fsyntax_only_command(self) -> None:
        command = _checker_command(
            "clang++",
            "-std=c++20 -I include",
            "sample.cpp,sources/view.mm",
        )

        self.assertEqual(
            command,
            "clang++ -fsyntax-only '-std=c++20' '-I' 'include' 'sample.cpp' 'sources/view.mm'",
        )

    def test_config_unknown_keys_are_rejected(self) -> None:
        config = self.repo / "stryker-cxx.yml"
        config.write_text("schemaVersion: stryker-cxx.config.v1\nunknownThing: true\n")

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(self.repo / "bad-config.json"),
            "--max-mutants",
            "1",
            "--quiet",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown config keys", result.stdout)

    def test_baseline_merge_and_prune_commands(self) -> None:
        first = self.repo / "first-baseline.json"
        second = self.repo / "second-baseline.json"
        merged = self.repo / "merged-baseline.json"
        first.write_text(json.dumps({
            "schemaVersion": "stryker-cxx.baseline.v1",
            "entries": {
                "a": {
                    "updatedAt": "2026-01-01T00:00:00Z",
                    "branch": "main",
                    "mutant": {"id": "mut-a", "file": "sample.cpp", "line": 1, "mutator": "EqualityOperator", "status": "KILLED"},
                }
            },
        }))
        second.write_text(json.dumps({
            "schemaVersion": "stryker-cxx.baseline.v1",
            "entries": {
                "b": {
                    "updatedAt": "2026-01-02T00:00:00Z",
                    "branch": "feature",
                    "mutant": {"id": "mut-b", "file": "missing.cpp", "line": 2, "mutator": "LogicalOperator", "status": "SURVIVED"},
                }
            },
        }))

        merge = self._cli("baseline-merge", "--output", str(merged), str(first), str(second))
        info = self._cli("baseline-info", "--baseline-file", str(merged), "--repo", str(self.repo))
        history = self._cli("baseline-history", "--baseline-file", str(merged), "--repo", str(self.repo), "--limit", "1")
        prune = self._cli("baseline-prune", "--baseline-file", str(merged), "--repo", str(self.repo))

        self.assertEqual(merge.returncode, 0, merge.stderr + merge.stdout)
        self.assertEqual(info.returncode, 0, info.stderr + info.stdout)
        self.assertEqual(history.returncode, 0, history.stderr + history.stdout)
        self.assertEqual(prune.returncode, 0, prune.stderr + prune.stdout)
        info_payload = json.loads(info.stdout)
        self.assertEqual(info_payload["entries"], 2)
        self.assertEqual(info_payload["byStatus"], {"KILLED": 1, "SURVIVED": 1})
        self.assertEqual(info_payload["fileExistence"], {"present": 1, "missing": 1})
        history_payload = json.loads(history.stdout)
        self.assertEqual(history_payload["entries"], 2)
        self.assertEqual(history_payload["matchedEntries"], 2)
        self.assertEqual(history_payload["byDay"]["2026-01-01"]["KILLED"], 1)
        self.assertEqual(history_payload["byDay"]["2026-01-02"]["SURVIVED"], 1)
        self.assertEqual(history_payload["history"][0]["key"], "b")
        self.assertEqual(history_payload["history"][0]["fileExists"], False)
        payload = json.loads(merged.read_text())
        self.assertEqual(list(payload["entries"].keys()), ["a"])

    def test_list_mutants_and_run_mutant_reproduce_a_survivor(self) -> None:
        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--max-mutants",
            "1",
        )
        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        mutant_id = json.loads(listed.stdout)[0]["id"]

        report = self.repo / "one-mutant.json"
        result = self._cli(
            "run-mutant",
            "--repo",
            str(self.repo),
            "--id",
            mutant_id,
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(report),
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["totalMutants"], 1)
        self.assertEqual(payload["mutants"][0]["id"], mutant_id)
        self.assertEqual(payload["mutants"][0]["status"], "SURVIVED")

    def test_plugin_mutator_is_available_to_list_mutants(self) -> None:
        plugin = self.repo / "literal-plugin.json"
        plugin.write_text(json.dumps({
            "name": "literal-plugin",
            "version": "0.1.0",
            "mutators": [
                {
                    "name": "IntegerOneToZero",
                    "description": "replace integer one with zero",
                    "replacements": [["1", "0"]],
                }
            ],
            "reporters": [{"name": "plugin-json"}],
        }))

        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--mutators",
            "IntegerOneToZero",
            "--plugin",
            str(plugin),
        )

        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        payload = json.loads(listed.stdout)
        self.assertEqual(payload[0]["mutator"], "IntegerOneToZero")

    def test_plugin_hooks_and_reporter_commands_run_locally(self) -> None:
        plugin = self.repo / "hook-plugin.json"
        plugin.write_text(json.dumps({
            "name": "hook-plugin",
            "version": "0.1.0",
            "hooks": {
                "preRun": "printf pre > hook-pre.txt",
                "postRun": "printf post > hook-post.txt",
            },
            "reporters": [
                {
                    "name": "copy-json",
                    "command": "cp \"$STRYKER_CXX_REPORT\" hook-report.json",
                }
            ],
        }))
        report = self.repo / "hook.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(report),
            "--plugin",
            str(plugin),
            "--reporter",
            "copy-json",
            "--dry-run-only",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual((self.repo / "hook-pre.txt").read_text(), "pre")
        self.assertEqual((self.repo / "hook-post.txt").read_text(), "post")
        self.assertTrue((self.repo / "hook-report.json").exists())

    def test_plugin_reporter_metadata_is_recorded_in_execution_payload(self) -> None:
        plugin = self.repo / "metadata-plugin.json"
        plugin.write_text(json.dumps({
            "name": "metadata-plugin",
            "version": "0.1.0",
            "reporters": [
                {
                    "name": "copy-json",
                    "command": "cp \"$STRYKER_CXX_REPORT\" metadata-report.json",
                    "metadata": {
                        "scope": "local",
                        "format": "json",
                    },
                }
            ],
        }))
        report = self.repo / "metadata.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(report),
            "--plugin",
            str(plugin),
            "--reporter",
            "copy-json",
            "--dry-run-only",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        metadata = payload["execution"].get("reporterMetadata")
        self.assertEqual(len(metadata), 1)
        self.assertEqual(
            metadata[0],
            {
                "plugin": "metadata-plugin",
                "reporter": "copy-json",
                "metadata": {"scope": "local", "format": "json"},
            },
        )

    def test_plugin_runner_provider_commands_override_build_check_test_phases(self) -> None:
        plugin = self.repo / "runner-plugin.json"
        plugin.write_text(json.dumps({
            "name": "runner-plugin",
            "version": "0.1.0",
            "capabilities": {
                "runner": {
                    "name": "local-runner",
                    "buildCommand": "printf build-provider > provider-build.txt",
                    "checkCommand": "printf check-provider > provider-check.txt",
                    "testCommand": "printf test-provider > provider-test.txt",
                }
            },
        }))
        report = self.repo / "runner-provider.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "false",
            "--check-command",
            "false",
            "--test-command",
            "false",
            "--report",
            str(report),
            "--plugin",
            str(plugin),
            "--dry-run-only",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual((self.repo / "provider-build.txt").read_text(), "build-provider")
        self.assertEqual((self.repo / "provider-check.txt").read_text(), "check-provider")
        self.assertEqual((self.repo / "provider-test.txt").read_text(), "test-provider")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["dryRun"]["build"]["provider"], "local-runner")
        self.assertEqual(payload["dryRun"]["check"]["provider"], "local-runner")
        self.assertEqual(payload["dryRun"]["test"]["provider"], "local-runner")
        self.assertEqual(payload["execution"]["providers"]["phases"]["build"], "local-runner")

    def test_plugin_coverage_provider_can_generate_coverage_file(self) -> None:
        plugin = self.repo / "coverage-plugin.json"
        plugin.write_text(json.dumps({
            "name": "coverage-plugin",
            "version": "0.1.0",
            "capabilities": {
                "coverageProvider": {
                    "name": "plugin-json",
                    "command": "printf '{\"files\":{\"sample.cpp\":{\"coveredLines\":[]}}}' > \"$STRYKER_CXX_COVERAGE_FILE\"",
                }
            },
        }))
        report = self.repo / "coverage-provider.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--plugin",
            str(plugin),
            "--skip-initial-test",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["coverage"]["provider"], "plugin-json")
        self.assertEqual(payload["coverage"]["plugin"]["plugin"], "coverage-plugin")
        self.assertEqual(payload["noCoverage"], 1)
        self.assertEqual(payload["mutants"][0]["status"], "NO_COVERAGE")

    def test_fixture_plugin_directories_cover_compatibility_surface(self) -> None:
        fixture_root = Path(__file__).resolve().parents[2] / "fixtures" / "plugins"
        token_plugin = fixture_root / "token-mutator"
        provider_plugin = fixture_root / "provider-hooks"
        reporter_plugin = fixture_root / "reporter-hook"

        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--plugin-dir",
            str(token_plugin),
            "--mutators",
            "IntegerOneToZero",
        )

        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        mutants = json.loads(listed.stdout)
        self.assertTrue(any(mut["mutator"] == "IntegerOneToZero" for mut in mutants))

        report = self.repo / "fixture-plugins.json"
        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "false",
            "--check-command",
            "false",
            "--test-command",
            "false",
            "--plugin-dir",
            str(provider_plugin),
            "--plugin-dir",
            str(reporter_plugin),
            "--reporter",
            "copy-json",
            "--dry-run-only",
            "--report",
            str(report),
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual((self.repo / "stryker-cxx-provider-build.txt").read_text(), "build-provider")
        self.assertEqual((self.repo / "stryker-cxx-provider-check.txt").read_text(), "check-provider")
        self.assertEqual((self.repo / "stryker-cxx-provider-test.txt").read_text(), "test-provider")
        self.assertEqual((self.repo / "stryker-cxx-plugin-pre.txt").read_text(), "preRun")
        self.assertEqual((self.repo / "stryker-cxx-plugin-post.txt").read_text(), "postRun")
        self.assertTrue((self.repo / "stryker-cxx-plugin-report.json").exists())
        payload = json.loads(report.read_text())
        self.assertEqual(payload["coverage"]["provider"], "fixture-coverage")
        self.assertEqual(payload["coverage"]["plugin"]["plugin"], "provider-hooks-fixture")
        self.assertEqual(payload["dryRun"]["build"]["provider"], "fixture-runner")
        reporter_metadata = payload["execution"].get("reporterMetadata")
        self.assertEqual(
            reporter_metadata,
            [
                {
                    "plugin": "reporter-hook-fixture",
                    "reporter": "copy-json",
                    "metadata": {"format": "json", "source": "fixture"},
                }
            ],
        )

    def test_plugin_config_loader_contributes_effective_defaults(self) -> None:
        fixture_root = Path(__file__).resolve().parents[2] / "fixtures" / "plugins" / "config-loader"
        config = self.repo / "loader.config.json"
        config.write_text(
            json.dumps(
                {
                    "schemaVersion": "stryker-cxx.config.v1",
                    "plugins": [str(fixture_root / "stryker-cxx-plugin.json")],
                },
                indent=2,
            )
        )

        result = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--config",
            str(config),
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        mutants = json.loads(result.stdout)
        self.assertEqual(len(mutants), 1)

    def test_killed_mutant_meets_default_threshold(self) -> None:
        report = self.repo / "killed.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["killed"], 1)
        self.assertEqual(payload["score"], 1)

    def test_fail_on_empty_uses_ci_exit_code_three(self) -> None:
        empty = self.repo / "empty.cpp"
        empty.write_text("int main() { return 0; }\n")
        self._git("add", "empty.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "empty")

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "empty.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(self.repo / "empty.json"),
            "--mutators",
            "EqualityOperator",
            "--fail-on-empty",
            "--quiet",
        )

        self.assertEqual(result.returncode, 3, result.stderr + result.stdout)

    def test_inplace_dirty_target_is_rejected(self) -> None:
        self.source.write_text(self.source.read_text() + "// local edit\n")

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(self.repo / "dirty.json"),
            "--max-mutants",
            "1",
            "--quiet",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to mutate dirty files", result.stdout)

    def test_resume_keeps_completed_mutant_result(self) -> None:
        report = self.repo / "resume.json"
        first = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--quiet",
        )
        self.assertEqual(first.returncode, 2, first.stderr + first.stdout)

        second = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--resume",
            str(report),
            "--max-mutants",
            "1",
            "--quiet",
        )

        self.assertEqual(second.returncode, 2, second.stderr + second.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["survived"], 1)
        self.assertEqual(payload["killed"], 0)
        self.assertEqual(payload["mutants"][0]["status"], "SURVIVED")

    def test_incremental_baseline_reuses_compatible_result(self) -> None:
        first_report = self.repo / "baseline-first.json"
        second_report = self.repo / "baseline-second.json"
        baseline = self.repo / "stryker-baseline.json"

        first = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(first_report),
            "--max-mutants",
            "1",
            "--skip-initial-test",
            "--write-baseline",
            str(baseline),
            "--quiet",
        )
        self.assertEqual(first.returncode, 2, first.stderr + first.stdout)

        second = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(second_report),
            "--max-mutants",
            "1",
            "--skip-initial-test",
            "--incremental",
            "--baseline-file",
            str(baseline),
            "--quiet",
        )

        self.assertEqual(second.returncode, 2, second.stderr + second.stdout)
        payload = json.loads(second_report.read_text())
        self.assertEqual(payload["baseline"]["cacheHits"], 1)
        self.assertEqual(payload["baseline"]["cacheMisses"], 0)
        self.assertEqual(payload["mutants"][0]["resultSource"], "baseline")
        self.assertEqual(payload["mutants"][0]["status"], "SURVIVED")

    def test_incremental_baseline_branch_policy_reports_miss_reason(self) -> None:
        first_report = self.repo / "baseline-branch-first.json"
        second_report = self.repo / "baseline-branch-second.json"
        baseline = self.repo / "stryker-branch-baseline.json"

        first = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(first_report),
            "--max-mutants",
            "1",
            "--skip-initial-test",
            "--write-baseline",
            str(baseline),
            "--baseline-branch",
            "main",
            "--quiet",
        )
        self.assertEqual(first.returncode, 2, first.stderr + first.stdout)

        second = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(second_report),
            "--max-mutants",
            "1",
            "--skip-initial-test",
            "--incremental",
            "--baseline-file",
            str(baseline),
            "--baseline-branch",
            "feature",
            "--quiet",
        )

        self.assertEqual(second.returncode, 2, second.stderr + second.stdout)
        payload = json.loads(second_report.read_text())
        self.assertEqual(payload["baseline"]["cacheHits"], 0)
        self.assertEqual(payload["baseline"]["cacheMisses"], 1)
        self.assertEqual(payload["baseline"]["missReasons"], {"branch mismatch main": 1})
        self.assertEqual(payload["mutants"][0]["run"]["baselineMissReason"], "branch mismatch main")
        self.assertEqual(payload["mutants"][0]["resultSource"], "executed")

    def test_incremental_baseline_max_age_policy_reports_miss_reason(self) -> None:
        first_report = self.repo / "baseline-age-first.json"
        second_report = self.repo / "baseline-age-second.json"
        baseline = self.repo / "stryker-age-baseline.json"

        first = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(first_report),
            "--max-mutants",
            "1",
            "--skip-initial-test",
            "--write-baseline",
            str(baseline),
            "--quiet",
        )
        self.assertEqual(first.returncode, 2, first.stderr + first.stdout)
        payload = json.loads(baseline.read_text())
        for entry in payload["entries"].values():
            entry["updatedAt"] = "2000-01-01T00:00:00Z"
        baseline.write_text(json.dumps(payload))

        second = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(second_report),
            "--max-mutants",
            "1",
            "--skip-initial-test",
            "--incremental",
            "--baseline-file",
            str(baseline),
            "--baseline-max-age-days",
            "1",
            "--quiet",
        )

        self.assertEqual(second.returncode, 2, second.stderr + second.stdout)
        payload = json.loads(second_report.read_text())
        self.assertEqual(payload["baseline"]["cacheHits"], 0)
        self.assertEqual(payload["baseline"]["cacheMisses"], 1)
        self.assertEqual(payload["baseline"]["missReasons"], {"older than 1d": 1})
        self.assertEqual(payload["mutants"][0]["run"]["baselineMissReason"], "older than 1d")

    def test_batch_mutants_marks_surviving_batch_with_batch_source(self) -> None:
        self.source.write_text(
            "int a() { return 1 == 1; }\n"
            "int b() { return 2 == 2; }\n"
            "int c() { return 3 == 3; }\n"
            "int d() { return 4 == 4; }\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "batch-mutants")
        report = self.repo / "batch.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(report),
            "--mutators",
            "EqualityOperator",
            "--batch-mutants",
            "--batch-size",
            "2",
            "--jobs",
            "2",
            "--worktree-mode",
            "copy",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["batching"]["batches"], 2)
        self.assertEqual(payload["execution"]["batching"]["batchedMutants"], 4)
        self.assertEqual(payload["execution"]["batching"]["parallelWorkers"], 2)
        self.assertEqual(payload["execution"]["batching"]["splitBatches"], 0)
        self.assertIn("same-file adjacent-line isolation", payload["execution"]["batching"]["heuristics"])
        self.assertEqual([m["resultSource"] for m in payload["mutants"]], ["batch", "batch", "batch", "batch"])
        lines_by_batch: dict[str, list[int]] = {}
        for mut in payload["mutants"]:
            lines_by_batch.setdefault(mut["run"]["batchId"], []).append(mut["line"])
        self.assertEqual(sorted(sorted(lines) for lines in lines_by_batch.values()), [[1, 3], [2, 4]])

    def test_batch_compile_failure_prunes_and_retries_remaining_mutants(self) -> None:
        self.source.write_text(
            "int a() { return 1 == 1; }\n"
            "int b() { return 2 == 2; }\n"
            "int c() { return 3 == 3; }\n"
            "int d() { return 4 == 4; }\n"
            "int e() { return 5 == 5; }\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "batch-pruning")
        report = self.repo / "batch-pruning.json"
        build_command = (
            "python3 -c \"import pathlib, sys; "
            "sys.exit(1 if 'return 1 != 1' in pathlib.Path('sample.cpp').read_text() else 0)\""
        )

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            build_command,
            "--test-command",
            "true",
            "--report",
            str(report),
            "--mutators",
            "EqualityOperator",
            "--batch-mutants",
            "--batch-size",
            "3",
            "--worktree-mode",
            "copy",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["buildErrors"], 1)
        self.assertEqual(payload["survived"], 4)
        self.assertEqual(payload["execution"]["compilePruning"]["prunedMutants"], 1)
        self.assertEqual(payload["execution"]["compilePruning"]["buildErrors"], 1)
        self.assertEqual(payload["execution"]["compilePruning"]["retryBatches"], 1)
        by_line = {mut["line"]: mut for mut in payload["mutants"]}
        self.assertEqual(by_line[1]["status"], "BUILD_ERROR")
        self.assertEqual(by_line[1]["resultSource"], "compile-pruning")
        self.assertEqual(by_line[1]["run"]["testSkippedReason"], "compile-pruned")
        self.assertEqual(by_line[2]["status"], "SURVIVED")
        self.assertEqual(by_line[3]["status"], "SURVIVED")
        self.assertEqual(by_line[4]["status"], "SURVIVED")
        self.assertEqual(by_line[5]["status"], "SURVIVED")
        self.assertEqual(by_line[2]["resultSource"], "batch")
        self.assertEqual(by_line[3]["resultSource"], "batch")
        self.assertEqual(by_line[4]["resultSource"], "batch")
        self.assertEqual(by_line[5]["resultSource"], "batch")

    def test_timeout_maps_to_canonical_mte_timeout(self) -> None:
        report = self.repo / "timeout.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            'python3 -c "import time; time.sleep(2)"',
            "--timeout",
            "1",
            "--skip-initial-test",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["timeouts"], 1)
        self.assertEqual(payload["mutants"][0]["status"], "TIMEOUT")
        first = payload["mutationTestingElements"]["files"]["sample.cpp"]["mutants"][0]
        self.assertEqual(first["status"], "Timeout")

    def test_copy_worktree_mode_leaves_source_untouched(self) -> None:
        report = self.repo / "copy.json"
        original = self.source.read_text()

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--worktree-mode",
            "copy",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["worktreeMode"], "copy")
        self.assertTrue(payload["execution"]["resourceIsolation"]["workspacePerMutant"])
        self.assertTrue(payload["execution"]["resourceIsolation"]["parallelSafe"])
        self.assertEqual(payload["killed"], 1)

    def test_compiled_executable_backend_swaps_binary_and_restores_original(self) -> None:
        self.source.write_text(
            "#include <cstdlib>\n"
            "int value(int input) {\n"
            "  if (input == 1) {\n"
            "    return 2;\n"
            "  }\n"
            "  return 0;\n"
            "}\n"
            "int main() {\n"
            "  return value(1) == 2 ? EXIT_SUCCESS : EXIT_FAILURE;\n"
            "}\n"
        )
        (self.repo / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.16)\n"
            "project(stryker_cxx_compiled_fixture LANGUAGES CXX)\n"
            "enable_testing()\n"
            "add_executable(sample sample.cpp)\n"
            "target_compile_features(sample PRIVATE cxx_std_17)\n"
            "add_test(NAME sample COMMAND sample)\n"
        )
        self._git("add", "sample.cpp", "CMakeLists.txt")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "compiled-backend")
        subprocess.run(["cmake", "-S", ".", "-B", "build"], cwd=self.repo, check=True, text=True, capture_output=True)
        subprocess.run(["cmake", "--build", "build", "--target", "sample"], cwd=self.repo, check=True, text=True, capture_output=True)
        original_source = self.source.read_text()
        executable = self.repo / "build" / "sample"
        original_executable_hash = _sha256(executable)
        report = self.repo / "compiled-executable.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-system",
            "cmake",
            "--build-dir",
            "build",
            "--build-target",
            "sample",
            "--test-binary",
            "build/sample",
            "--test-command",
            "ctest --test-dir build --output-on-failure",
            "--artifact-backend",
            "compiled-executable",
            "--mutators",
            "EqualityOperator",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original_source)
        self.assertEqual(_sha256(executable), original_executable_hash)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["killed"], 1)
        self.assertEqual(payload["mutationArtifact"]["mode"], "compiled-artifact")
        self.assertEqual(payload["mutationArtifact"]["backend"], "compiled-executable")
        self.assertTrue(payload["mutationArtifact"]["supportsCompiledReplacement"])
        self.assertTrue(payload["artifactPlacement"]["compiledArtifacts"]["supported"])
        self.assertEqual(payload["artifactPlacement"]["compiledArtifacts"]["placement"], "swap-file")
        self.assertEqual(payload["lifecycle"]["artifactModel"], "compiled-artifact")
        lifecycle_by_name = {
            phase["name"]: phase
            for phase in payload["lifecycle"]["phases"]
        }
        self.assertEqual(lifecycle_by_name["mutationArtifact"]["status"], "compiledArtifact")
        self.assertEqual(len(payload["compiledArtifacts"]), 1)
        compiled = payload["compiledArtifacts"][0]
        self.assertEqual(compiled["backend"], "compiled-executable")
        self.assertEqual(compiled["target"], "sample")
        self.assertFalse(compiled["sourceCheckoutMutation"])
        self.assertTrue(compiled["originalRestored"])
        self.assertEqual(compiled["originalHashBefore"], original_executable_hash)
        self.assertEqual(compiled["originalHashAfter"], original_executable_hash)
        run = payload["mutants"][0]["run"]
        self.assertEqual(run["artifactBackend"], "compiled-executable")
        self.assertEqual(run["compiledArtifact"]["placementPolicy"], "swap-file")
        self.assertTrue(run["artifactPlacement"]["originalArtifactsRestored"])

    def test_compiled_backend_rejects_non_cmake_adapter_in_preflight(self) -> None:
        report = self.repo / "compiled-unsupported.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-system",
            "ninja",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--artifact-backend",
            "compiled-executable",
            "--report",
            str(report),
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertIn("requires --build-system cmake/ctest", result.stderr)

    def test_compiled_library_backend_swaps_shared_library_and_restores_original(self) -> None:
        self.source.write_text(
            "int value(int input) {\n"
            "  if (input == 1) {\n"
            "    return 2;\n"
            "  }\n"
            "  return 0;\n"
            "}\n"
        )
        (self.repo / "test_main.cpp").write_text(
            "#include <cstdlib>\n"
            "int value(int input);\n"
            "int main() {\n"
            "  return value(1) == 2 ? EXIT_SUCCESS : EXIT_FAILURE;\n"
            "}\n"
        )
        (self.repo / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.16)\n"
            "project(stryker_cxx_compiled_library_fixture LANGUAGES CXX)\n"
            "enable_testing()\n"
            "add_library(mathlib SHARED sample.cpp)\n"
            "target_compile_features(mathlib PRIVATE cxx_std_17)\n"
            "add_executable(sample_test test_main.cpp)\n"
            "target_compile_features(sample_test PRIVATE cxx_std_17)\n"
            "target_link_libraries(sample_test PRIVATE mathlib)\n"
            "add_test(NAME sample COMMAND sample_test)\n"
        )
        self._git("add", "sample.cpp", "test_main.cpp", "CMakeLists.txt")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "compiled-library-backend")
        subprocess.run(["cmake", "-S", ".", "-B", "build"], cwd=self.repo, check=True, text=True, capture_output=True)
        subprocess.run(["cmake", "--build", "build", "--target", "mathlib"], cwd=self.repo, check=True, text=True, capture_output=True)
        subprocess.run(["cmake", "--build", "build", "--target", "sample_test"], cwd=self.repo, check=True, text=True, capture_output=True)
        original_source = self.source.read_text()
        libraries = sorted((self.repo / "build").glob("libmathlib.*"))
        self.assertTrue(libraries)
        library = libraries[0]
        original_library_hash = _sha256(library)
        report = self.repo / "compiled-library.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-system",
            "cmake",
            "--build-dir",
            "build",
            "--build-target",
            "mathlib",
            "--test-binary",
            "build/sample_test",
            "--test-command",
            "ctest --test-dir build --output-on-failure",
            "--artifact-backend",
            "compiled-library",
            "--mutators",
            "EqualityOperator",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original_source)
        self.assertEqual(_sha256(library), original_library_hash)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["killed"], 1)
        self.assertEqual(payload["mutationArtifact"]["mode"], "compiled-artifact")
        self.assertEqual(payload["mutationArtifact"]["backend"], "compiled-library")
        self.assertEqual(payload["mutationArtifact"]["compiledArtifacts"]["kinds"], ["library"])
        self.assertTrue(payload["artifactPlacement"]["compiledArtifacts"]["supported"])
        self.assertEqual(payload["artifactPlacement"]["compiledArtifacts"]["kind"], "library")
        self.assertEqual(payload["lifecycle"]["artifactModel"], "compiled-artifact")
        self.assertEqual(len(payload["compiledArtifacts"]), 1)
        compiled = payload["compiledArtifacts"][0]
        self.assertEqual(compiled["backend"], "compiled-library")
        self.assertEqual(compiled["kind"], "library")
        self.assertEqual(compiled["target"], "mathlib")
        self.assertFalse(compiled["sourceCheckoutMutation"])
        self.assertTrue(compiled["originalRestored"])
        self.assertEqual(compiled["originalHashBefore"], original_library_hash)
        self.assertEqual(compiled["originalHashAfter"], original_library_hash)
        run = payload["mutants"][0]["run"]
        self.assertEqual(run["artifactBackend"], "compiled-library")
        self.assertEqual(run["compiledArtifact"]["placementPolicy"], "swap-file")
        self.assertTrue(run["artifactPlacement"]["originalArtifactsRestored"])

    def test_compiled_object_backend_records_object_artifact_and_restores_original(self) -> None:
        self.source.write_text(
            "#include <cstdlib>\n"
            "int value(int input) {\n"
            "  if (input == 1) {\n"
            "    return 2;\n"
            "  }\n"
            "  return 0;\n"
            "}\n"
            "int main() {\n"
            "  return value(1) == 2 ? EXIT_SUCCESS : EXIT_FAILURE;\n"
            "}\n"
        )
        (self.repo / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.16)\n"
            "project(stryker_cxx_compiled_object_fixture LANGUAGES CXX)\n"
            "enable_testing()\n"
            "add_executable(sample sample.cpp)\n"
            "target_compile_features(sample PRIVATE cxx_std_17)\n"
            "add_test(NAME sample COMMAND sample)\n"
        )
        self._git("add", "sample.cpp", "CMakeLists.txt")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "compiled-object-backend")
        subprocess.run(["cmake", "-S", ".", "-B", "build"], cwd=self.repo, check=True, text=True, capture_output=True)
        subprocess.run(["cmake", "--build", "build", "--target", "sample"], cwd=self.repo, check=True, text=True, capture_output=True)
        original_source = self.source.read_text()
        executable = self.repo / "build" / "sample"
        original_executable_hash = _sha256(executable)
        report = self.repo / "compiled-object.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-system",
            "cmake",
            "--build-dir",
            "build",
            "--build-target",
            "sample",
            "--test-binary",
            "build/sample",
            "--test-command",
            "ctest --test-dir build --output-on-failure",
            "--artifact-backend",
            "compiled-object",
            "--mutators",
            "EqualityOperator",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original_source)
        self.assertEqual(_sha256(executable), original_executable_hash)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["killed"], 1)
        self.assertEqual(payload["mutationArtifact"]["mode"], "compiled-artifact")
        self.assertEqual(payload["mutationArtifact"]["backend"], "compiled-object")
        self.assertEqual(payload["mutationArtifact"]["compiledArtifacts"]["kinds"], ["object"])
        self.assertEqual(payload["execution"]["compilePruning"]["candidateArtifactMode"], "compiled-object")
        self.assertEqual(payload["execution"]["compilePruning"]["strategy"], "compiled-artifact-prune-and-retry")
        self.assertEqual(len(payload["compiledArtifacts"]), 1)
        compiled = payload["compiledArtifacts"][0]
        self.assertEqual(compiled["backend"], "compiled-object")
        self.assertEqual(compiled["kind"], "object")
        self.assertTrue(compiled["originalRestored"])
        self.assertEqual(compiled["originalHashBefore"], original_executable_hash)
        self.assertEqual(compiled["originalHashAfter"], original_executable_hash)
        self.assertEqual(len(compiled["objectArtifacts"]), 1)
        obj = compiled["objectArtifacts"][0]
        self.assertEqual(obj["mutantId"], payload["mutants"][0]["id"])
        self.assertTrue(obj["compileCommandFound"])
        self.assertTrue(obj["objectProduced"])
        self.assertTrue(obj["objectArtifact"].endswith(".o"))
        self.assertIsInstance(obj["objectHash"], str)
        run = payload["mutants"][0]["run"]
        self.assertEqual(run["artifactBackend"], "compiled-object")
        self.assertTrue(run["artifactPlacement"]["originalArtifactsRestored"])

    def test_compiled_backend_batches_surviving_mutants_without_source_overlay(self) -> None:
        self.source.write_text(
            "bool feature_a() {\n"
            "  return true;\n"
            "}\n"
            "\n"
            "bool feature_b() {\n"
            "  return true;\n"
            "}\n"
            "\n"
            "int value() {\n"
            "  return 2;\n"
            "}\n"
        )
        (self.repo / "test_main.cpp").write_text(
            "#include <cstdlib>\n"
            "int value();\n"
            "int main() {\n"
            "  return value() == 2 ? EXIT_SUCCESS : EXIT_FAILURE;\n"
            "}\n"
        )
        (self.repo / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.16)\n"
            "project(stryker_cxx_compiled_batch_fixture LANGUAGES CXX)\n"
            "enable_testing()\n"
            "add_executable(sample_test sample.cpp test_main.cpp)\n"
            "target_compile_features(sample_test PRIVATE cxx_std_17)\n"
            "add_test(NAME sample COMMAND sample_test)\n"
        )
        self._git("add", "sample.cpp", "test_main.cpp", "CMakeLists.txt")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "compiled-batch-backend")
        subprocess.run(["cmake", "-S", ".", "-B", "build"], cwd=self.repo, check=True, text=True, capture_output=True)
        subprocess.run(["cmake", "--build", "build", "--target", "sample_test"], cwd=self.repo, check=True, text=True, capture_output=True)
        original_source = self.source.read_text()
        executable = self.repo / "build" / "sample_test"
        original_executable_hash = _sha256(executable)
        report = self.repo / "compiled-batch.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-system",
            "cmake",
            "--build-dir",
            "build",
            "--build-target",
            "sample_test",
            "--test-binary",
            "build/sample_test",
            "--test-command",
            "ctest --test-dir build --output-on-failure",
            "--artifact-backend",
            "compiled-executable",
            "--batch-mutants",
            "--batch-size",
            "2",
            "--mutators",
            "BooleanLiteral",
            "--report",
            str(report),
            "--max-mutants",
            "2",
            "--threshold-break",
            "0",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original_source)
        self.assertEqual(_sha256(executable), original_executable_hash)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["survived"], 2)
        self.assertEqual(payload["execution"]["batching"]["batches"], 1)
        self.assertEqual(payload["execution"]["batching"]["batchedMutants"], 2)
        self.assertEqual(payload["execution"]["batching"]["splitBatches"], 0)
        self.assertEqual([mut["resultSource"] for mut in payload["mutants"]], ["batch", "batch"])
        for mut in payload["mutants"]:
            run = mut["run"]
            self.assertEqual(run["artifactBackend"], "compiled-executable")
            self.assertEqual(run["worktreeMode"], "compiled-artifact")
            self.assertEqual(run["scheduler"]["sessionType"], "batch")
            self.assertEqual(run["compiledArtifact"]["backend"], "compiled-executable")
            self.assertTrue(run["compiledArtifact"]["originalRestored"])
            self.assertFalse(run["compiledArtifact"]["sourceCheckoutMutation"])

    def test_worker_tmp_env_and_retained_worktree_are_reported(self) -> None:
        report = self.repo / "retained-copy.json"
        old_blocked = os.environ.get("STRYKER_CXX_BLOCKED")
        os.environ["STRYKER_CXX_BLOCKED"] = "secret"

        try:
            with tempfile.TemporaryDirectory() as worker_tmp:
                stale = Path(worker_tmp) / "stryker-cxx-copy-stale"
                stale.mkdir()
                os.utime(stale, (1, 1))
                result = self._cli(
                    "run",
                    "--repo",
                    str(self.repo),
                    "--files",
                    "sample.cpp",
                    "--build-command",
                    "true",
                    "--test-command",
                    'test "$STRYKER_CXX_FLAG" = "yes" && test -z "$STRYKER_CXX_BLOCKED"',
                    "--skip-initial-test",
                    "--report",
                    str(report),
                    "--max-mutants",
                    "1",
                    "--worktree-mode",
                    "copy",
                    "--worker-tmp-dir",
                    worker_tmp,
                    "--worker-label",
                    "pr 96205 proof",
                    "--retained-worktree-ttl-hours",
                    "0",
                    "--retain-worktrees",
                    "--env",
                    "STRYKER_CXX_FLAG=yes",
                    "--env",
                    "SECRET_TOKEN=topsecret123",
                    "--env-inherit",
                    "PATH",
                    "--env-block",
                    "STRYKER_CXX_BLOCKED",
                    "--quiet",
                )

                self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
                report_text = report.read_text()
                self.assertNotIn("topsecret123", report_text)
                payload = json.loads(report_text)
                isolation = payload["execution"]["resourceIsolation"]
                self.assertTrue(isolation["retainWorktrees"])
                self.assertEqual(isolation["retainWorktreesFor"], ["ALL"])
                self.assertEqual(isolation["retainedWorktreeTtlHours"], 0.0)
                self.assertTrue(payload["artifactPlacement"]["retainArtifacts"])
                self.assertEqual(payload["artifactPlacement"]["retainArtifactsFor"], ["ALL"])
                self.assertGreaterEqual(isolation["retainedWorktreeCleanup"]["removed"], 1)
                self.assertFalse(stale.exists())
                self.assertEqual(isolation["workerTmpDir"], worker_tmp)
                self.assertEqual(isolation["workerLabel"], "pr-96205-proof")
                self.assertEqual(isolation["environmentKeys"], ["SECRET_TOKEN", "STRYKER_CXX_FLAG"])
                self.assertEqual(isolation["environmentInheritedKeys"], ["PATH"])
                self.assertEqual(isolation["environmentBlockedKeys"], ["STRYKER_CXX_BLOCKED"])
                self.assertTrue(isolation["redaction"]["enabled"])
                self.assertEqual(
                    payload["config"]["effective"]["env"],
                    ["STRYKER_CXX_FLAG=[REDACTED]", "SECRET_TOKEN=[REDACTED]"],
                )
                retained = payload["mutants"][0]["run"]["retainedWorktree"]
                placement = payload["mutants"][0]["run"]["artifactPlacement"]
                self.assertTrue(placement["materializedArtifactRetained"])
                self.assertFalse(placement["materializedArtifactRestored"])
                self.assertEqual(placement["retainedPath"], retained)
                self.assertIn("cleanupGuidance", placement)
                self.assertTrue(retained.startswith(worker_tmp))
                self.assertIn("stryker-cxx-copy-pr-96205-proof-", retained)
                self.assertEqual(payload["mutants"][0]["run"]["workerLabel"], "pr-96205-proof")
                self.assertEqual(payload["mutants"][0]["run"]["retainedWorktreeLabel"], "pr-96205-proof")
                self.assertIn("!=", (Path(retained) / "sample.cpp").read_text())
                self.assertEqual(
                    payload["mutants"][0]["run"]["environmentKeys"],
                    ["SECRET_TOKEN", "STRYKER_CXX_FLAG"],
                )
                self.assertEqual(payload["mutants"][0]["run"]["environmentInheritedKeys"], ["PATH"])
                self.assertEqual(payload["mutants"][0]["run"]["environmentBlockedKeys"], ["STRYKER_CXX_BLOCKED"])
                self.assertEqual(payload["mutants"][0]["status"], "SURVIVED")
        finally:
            if old_blocked is None:
                os.environ.pop("STRYKER_CXX_BLOCKED", None)
            else:
                os.environ["STRYKER_CXX_BLOCKED"] = old_blocked

    def test_retain_worktrees_for_keeps_only_selected_statuses(self) -> None:
        survived_report = self.repo / "retained-survivor.json"
        killed_report = self.repo / "retained-killed.json"

        with tempfile.TemporaryDirectory() as worker_tmp:
            survived = self._cli(
                "run",
                "--repo",
                str(self.repo),
                "--files",
                "sample.cpp",
                "--build-command",
                "true",
                "--test-command",
                "true",
                "--skip-initial-test",
                "--report",
                str(survived_report),
                "--max-mutants",
                "1",
                "--worktree-mode",
                "copy",
                "--worker-tmp-dir",
                worker_tmp,
                "--retain-worktrees-for",
                "SURVIVED",
                "--quiet",
            )

            self.assertEqual(survived.returncode, 2, survived.stderr + survived.stdout)
            survived_payload = json.loads(survived_report.read_text())
            isolation = survived_payload["execution"]["resourceIsolation"]
            self.assertTrue(isolation["retainWorktrees"])
            self.assertEqual(isolation["retainWorktreesFor"], ["SURVIVED"])
            self.assertIn("retainedWorktree", survived_payload["mutants"][0]["run"])

            killed = self._cli(
                "run",
                "--repo",
                str(self.repo),
                "--files",
                "sample.cpp",
                "--build-command",
                "true",
                "--test-command",
                "false",
                "--skip-initial-test",
                "--report",
                str(killed_report),
                "--max-mutants",
                "1",
                "--worktree-mode",
                "copy",
                "--worker-tmp-dir",
                worker_tmp,
                "--retain-worktrees-for",
                "SURVIVED",
                "--quiet",
            )

            self.assertEqual(killed.returncode, 0, killed.stderr + killed.stdout)
            killed_payload = json.loads(killed_report.read_text())
            self.assertEqual(killed_payload["mutants"][0]["status"], "KILLED")
            self.assertNotIn("retainedWorktree", killed_payload["mutants"][0]["run"])

    def test_mutation_testing_elements_format_writes_direct_mte_payload(self) -> None:
        report = self.repo / "mte.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--format",
            "mutation-testing-elements",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["schemaVersion"], "2.0")
        self.assertEqual(payload["language"], "cpp")
        self.assertIn("sample.cpp", payload["files"])

    def test_stryker_disable_next_line_marks_mutant_ignored_without_running_it(self) -> None:
        self.source.write_text(
            "// Stryker disable next-line EqualityOperator: equivalent guard\n"
            "int main() { if (1 == 1) return 0; return 1; }\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "ignored-mutant")
        report = self.repo / "ignored.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--mutators",
            "EqualityOperator",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["totalMutants"], 1)
        self.assertEqual(payload["ignored"], 1)
        self.assertEqual(payload["killed"], 0)
        self.assertEqual(payload["survived"], 0)
        self.assertEqual(payload["score"], 1)
        self.assertEqual(payload["mutants"][0]["status"], "IGNORED")
        self.assertIn("equivalent guard", payload["mutants"][0]["ignoreReason"])
        first = payload["mutationTestingElements"]["files"]["sample.cpp"]["mutants"][0]
        self.assertEqual(first["status"], "Ignored")
        self.assertIn("equivalent guard", first["statusReason"])

    def test_stryker_restore_next_line_and_list_mutants_expose_ignore_state(self) -> None:
        self.source.write_text(
            "// Stryker disable all: generated comparison\n"
            "// Stryker restore next-line EqualityOperator\n"
            "int keep() { return 1 == 1; }\n"
            "int skip() { return 2 == 2; }\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "restore-next-line")
        report = self.repo / "restore.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--mutators",
            "EqualityOperator",
            "--quiet",
        )
        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--mutators",
            "EqualityOperator",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["totalMutants"], 2)
        self.assertEqual(payload["killed"], 1)
        self.assertEqual(payload["ignored"], 1)
        statuses = [mut["status"] for mut in json.loads(listed.stdout)]
        self.assertEqual(statuses, ["PENDING", "IGNORED"])

    def test_equivalent_suppression_marks_high_confidence_noise_ignored(self) -> None:
        self.source.write_text(
            "bool redundant(bool flag) { return flag && flag; }\n"
            "int identity(int x) { return x + 0; }\n"
            "bool redundant_bits(int flags) { return (flags & flags) != 0; }\n"
            "int redundant_min(int x) { return std::min(x, x); }\n"
            "int redundant_choice(bool flag, int x) { return flag ? x : x; }\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "equivalent-suppression")
        report = self.repo / "equivalent-suppression.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--mutators",
            "LogicalOperator,ArithmeticOperator,BitwiseOperator,StandardLibraryCall,ConditionalExpression",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["totalMutants"], 5)
        self.assertEqual(payload["ignored"], 5)
        self.assertEqual(payload["killed"], 0)
        reasons = {mut["ignoreReason"] for mut in payload["mutants"]}
        self.assertIn("equivalent duplicate logical operand", reasons)
        self.assertIn("equivalent arithmetic identity", reasons)
        self.assertIn("equivalent duplicate bitwise operand", reasons)
        self.assertIn("equivalent duplicate standard-library operands", reasons)
        self.assertIn("equivalent duplicate conditional branches", reasons)
        suppression = payload["execution"]["analysis"]["equivalentSuppression"]
        self.assertEqual(suppression["mode"], "conservative")
        self.assertEqual(suppression["suppressedMutants"], 5)

    def test_equivalent_suppression_can_be_disabled(self) -> None:
        self.source.write_text(
            "bool redundant(bool flag) { return flag && flag; }\n"
            "int identity(int x) { return x + 0; }\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "equivalent-suppression-off")
        report = self.repo / "equivalent-suppression-off.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--mutators",
            "LogicalOperator,ArithmeticOperator",
            "--equivalent-suppression",
            "off",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["totalMutants"], 2)
        self.assertEqual(payload["ignored"], 0)
        self.assertEqual(payload["killed"], 2)
        self.assertEqual(payload["execution"]["analysis"]["equivalentSuppression"]["mode"], "off")

    def test_call_removal_mutator_removes_statement_level_calls(self) -> None:
        self.source.write_text(
            "void touched() {}\n"
            "int main() {\n"
            "  touched();\n"
            "  return 0;\n"
            "}\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "call-removal")
        report = self.repo / "call-removal.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--mutators",
            "CallRemoval",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["totalMutants"], 1)
        self.assertEqual(payload["killed"], 1)
        self.assertEqual(payload["mutants"][0]["mutator"], "CallRemoval")
        self.assertEqual(payload["mutants"][0]["original"], "touched()")
        self.assertEqual(payload["mutants"][0]["mutated"], "(void)0")

    def test_statement_removal_mutator_discovers_simple_statements(self) -> None:
        self.source.write_text(
            "int x = 0;\n"
            "int main() {\n"
            "  int y = 1;\n"
            "  return y;\n"
            "}\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "statement-removal")

        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--mutators",
            "StatementRemoval",
        )

        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        payload = json.loads(listed.stdout)
        self.assertEqual({mut["mutator"] for mut in payload}, {"StatementRemoval"})
        self.assertTrue(
            any(
                mut["original"].strip() == "int x = 0;" and mut["mutated"] == ";"
                for mut in payload
            )
        )
        self.assertTrue(
            any(
                mut["original"].strip() == "int y = 1;" and mut["mutated"] == ";"
                for mut in payload
            )
        )

    def test_block_removal_mutator_discovers_simple_blocks(self) -> None:
        self.source.write_text(
            "{ int x = 1; }\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "block-removal")

        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--mutators",
            "BlockRemoval",
        )

        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        payload = json.loads(listed.stdout)
        self.assertEqual({mut["mutator"] for mut in payload}, {"BlockRemoval"})
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["original"], "{ int x = 1; }")
        self.assertEqual(payload[0]["mutated"], "{}")

    def test_statement_removal_mutator_runs_simple_statements(self) -> None:
        self.source.write_text(
            "int x = 0;\n"
            "int main() {\n"
            "  int y = 1;\n"
            "  if (y == 1) {\n"
            "    return y;\n"
            "  }\n"
            "}\n"
        )
        self._git("add", "sample.cpp")
        self._git(
            "-c",
            "user.name=stryker-cxx",
            "-c",
            "user.email=stryker-cxx@example.invalid",
            "commit",
            "-q",
            "-m",
            "statement-run",
        )

        report = self.repo / "statement-removal-run.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--mutators",
            "StatementRemoval",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["totalMutants"], 2)
        self.assertEqual(payload["killed"], 2)
        self.assertEqual(payload["mutants"][0]["mutator"], "StatementRemoval")
        self.assertEqual(payload["mutants"][0]["mutated"], ";")

    def test_shift_operator_mutator_discovers_candidates(self) -> None:
        self.source.write_text(
            "int shifted(int x) {\n"
            "  int a = x << 2;\n"
            "  int b = x >> 1;\n"
            "  return a + b;\n"
            "}\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "shift-operator-fixture")

        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--mutators",
            "ShiftOperator",
        )

        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        payload = json.loads(listed.stdout)
        mutator_names = {mut["mutator"] for mut in payload}
        self.assertEqual(mutator_names, {"ShiftOperator"})
        pairs = {(mut["original"], mut["mutated"]) for mut in payload}
        self.assertIn(("<<", ">>"), pairs)
        self.assertIn((">>", "<<"), pairs)

    def test_update_operator_mutator_discovers_candidates(self) -> None:
        self.source.write_text(
            "int updated(int x) {\n"
            "  int i = 0;\n"
            "  ++i;\n"
            "  --i;\n"
            "  i++;\n"
            "  return --x;\n"
            "}\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "update-operator-fixture")

        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--mutators",
            "UpdateOperator",
        )

        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        payload = json.loads(listed.stdout)
        self.assertTrue(payload)
        self.assertEqual({mut["mutator"] for mut in payload}, {"UpdateOperator"})
        pairs = {(mut["original"], mut["mutated"]) for mut in payload}
        self.assertIn(("++", "--"), pairs)
        self.assertIn(("--", "++"), pairs)

    def test_loop_boundary_mutator_discovers_candidates(self) -> None:
        self.source.write_text(
            "void loops(int n) {\n"
            "  for (int i = 0; i < n; ++i) {\n"
            "  }\n"
            "  while (n >= 0) {}\n"
            "  do {\n"
            "    --n;\n"
            "  } while (n > 0);\n"
            "}\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "loop-boundary-fixture")

        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--mutators",
            "LoopBoundary",
        )

        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        payload = json.loads(listed.stdout)
        self.assertEqual({mut["mutator"] for mut in payload}, {"LoopBoundary"})
        pairs = {(mut["original"], mut["mutated"]) for mut in payload}
        self.assertIn(("<", "<="), pairs)
        self.assertIn((">=", ">"), pairs)
        self.assertIn((">", ">="), pairs)

    def test_loop_condition_mutator_discovers_candidates(self) -> None:
        self.source.write_text(
            "void loops(int n) {\n"
            "  for (int i = 0; i < n; ++i) {\n"
            "  }\n"
            "  while (n >= 0) {}\n"
            "  do {\n"
            "    --n;\n"
            "  } while (n > 0);\n"
            "}\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "loop-condition-fixture")

        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--mutators",
            "LoopCondition",
        )

        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        payload = json.loads(listed.stdout)
        self.assertEqual({mut["mutator"] for mut in payload}, {"LoopCondition"})
        pairs = {(mut["original"], mut["mutated"]) for mut in payload}
        self.assertIn(("i < n", "!(i < n)"), pairs)
        self.assertIn(("n >= 0", "!(n >= 0)"), pairs)
        self.assertIn(("n > 0", "!(n > 0)"), pairs)

    def test_expanded_cxx_catalog_discovers_opt_in_candidates(self) -> None:
        self.source.write_text(
            "#include <algorithm>\n"
            "#ifdef FEATURE_FLAG\n"
            "#endif\n"
            "#if 1\n"
            "#endif\n"
            "struct Node { int value; void touch(); };\n"
            "void objc(id obj) {\n"
            "  [obj touch];\n"
            "}\n"
            "BOOL enabled() { return YES; }\n"
            "kernel void shade(uint gid [[thread_position_in_grid]]) {}\n"
            "void cases(Node node, Node* ptr, bool fail) {\n"
            "  int low = std::min(1, 2);\n"
            "  int high = std::max(1, 2);\n"
            "  auto lower = std::lower_bound(values.begin(), values.end(), 2);\n"
            "  auto upper = std::upper_bound(values.begin(), values.end(), 2);\n"
            "  auto first = std::begin(values);\n"
            "  auto last = std::end(values);\n"
            "  std::sort(values.begin(), values.end());\n"
            "  std::stable_sort(values.begin(), values.end());\n"
            "  std::partition(values.begin(), values.end(), pred);\n"
            "  std::stable_partition(values.begin(), values.end(), pred);\n"
            "  bool sorted = std::is_sorted(values.begin(), values.end());\n"
            "  bool heap = std::is_heap(values.begin(), values.end());\n"
            "  auto order = std::memory_order_relaxed;\n"
            "  int left = node.value;\n"
            "  int right = ptr->value;\n"
            "  bool boundary = left <= right;\n"
            "  if (fail) { throw std::runtime_error(\"x\"); }\n"
            "}\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "expanded-cxx-catalog-fixture")

        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--mutators",
            ",".join([
                "ConditionalBoundary",
                "StandardLibraryCall",
                "MemoryOrder",
                "MemberAccessOperator",
                "ExceptionHandling",
                "PreprocessorGuard",
                "ObjCMessageSend",
                "ObjCBoolLiteral",
                "MetalThreadPosition",
            ]),
        )

        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        payload = json.loads(listed.stdout)
        mutator_names = {mut["mutator"] for mut in payload}
        self.assertEqual(
            mutator_names,
            {
                "ConditionalBoundary",
                "StandardLibraryCall",
                "MemoryOrder",
                "MemberAccessOperator",
                "ExceptionHandling",
                "PreprocessorGuard",
                "ObjCMessageSend",
                "ObjCBoolLiteral",
                "MetalThreadPosition",
            },
        )
        pairs = {(mut["mutator"], mut["original"], mut["mutated"]) for mut in payload}
        self.assertIn(("ConditionalBoundary", "<=", "<"), pairs)
        self.assertIn(("StandardLibraryCall", "std::min", "std::max"), pairs)
        self.assertIn(("StandardLibraryCall", "std::max", "std::min"), pairs)
        self.assertIn(("StandardLibraryCall", "std::lower_bound", "std::upper_bound"), pairs)
        self.assertIn(("StandardLibraryCall", "std::upper_bound", "std::lower_bound"), pairs)
        self.assertIn(("StandardLibraryCall", "std::begin", "std::end"), pairs)
        self.assertIn(("StandardLibraryCall", "std::end", "std::begin"), pairs)
        self.assertIn(("StandardLibraryCall", "std::sort", "std::stable_sort"), pairs)
        self.assertIn(("StandardLibraryCall", "std::stable_sort", "std::sort"), pairs)
        self.assertIn(("StandardLibraryCall", "std::partition", "std::stable_partition"), pairs)
        self.assertIn(("StandardLibraryCall", "std::stable_partition", "std::partition"), pairs)
        self.assertIn(("StandardLibraryCall", "std::is_sorted", "std::is_heap"), pairs)
        self.assertIn(("StandardLibraryCall", "std::is_heap", "std::is_sorted"), pairs)
        self.assertIn(("MemoryOrder", "std::memory_order_relaxed", "std::memory_order_seq_cst"), pairs)
        self.assertIn(("MemberAccessOperator", ".", "->"), pairs)
        self.assertIn(("MemberAccessOperator", "->", "."), pairs)
        self.assertIn(("PreprocessorGuard", "ifdef", "ifndef"), pairs)
        self.assertIn(("PreprocessorGuard", "1", "0"), pairs)
        self.assertIn(("ObjCBoolLiteral", "YES", "NO"), pairs)
        self.assertIn(("MetalThreadPosition", "thread_position_in_grid", "thread_position_in_threadgroup"), pairs)
        self.assertTrue(any(mut["mutator"] == "ExceptionHandling" and mut["mutated"] == "(void)0;" for mut in payload))
        self.assertTrue(any(mut["mutator"] == "ObjCMessageSend" and mut["mutated"] == "(void)0" for mut in payload))

    def test_metal_source_catalog_discovers_address_space_candidates(self) -> None:
        shader = self.repo / "shader.metal"
        shader.write_text(
            "kernel void shade(device float* out, constant float* scale, threadgroup float* scratch, uint gid [[thread_position_in_grid]]) {}\n"
        )
        self._git("add", "shader.metal")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "metal-address-space-fixture")

        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "shader.metal",
            "--include-metal",
            "--mutators",
            "MetalAddressSpace",
        )

        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        payload = json.loads(listed.stdout)
        self.assertEqual({mut["mutator"] for mut in payload}, {"MetalAddressSpace"})
        pairs = {(mut["original"], mut["mutated"]) for mut in payload}
        self.assertIn(("device", "constant"), pairs)
        self.assertIn(("constant", "device"), pairs)
        self.assertIn(("threadgroup", "device"), pairs)
        self.assertNotIn(("thread_position_in_grid", "thread_position_in_threadgroup"), pairs)

    def test_conditional_expression_mutator_discovers_candidates(self) -> None:
        self.source.write_text(
            "int choose(int x) {\n"
            "  return x ? 1 : 0;\n"
            "}\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "conditional-expression-fixture")

        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--mutators",
            "ConditionalExpression",
        )

        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        payload = json.loads(listed.stdout)
        self.assertTrue(payload)
        self.assertEqual({mut["mutator"] for mut in payload}, {"ConditionalExpression"})
        pairs = {(mut["original"].strip(), mut["mutated"].strip()) for mut in payload}
        self.assertTrue(("1 : 0", "0 : 1") in pairs or ("1 : 0", "0: 1") in pairs)

    def test_literal_mutators_are_available_as_opt_in_catalog_entries(self) -> None:
        self.source.write_text(
            "#include <cstddef>\n"
            "int value() { return 1; }\n"
            "void* ptr() { return nullptr; }\n"
            'const char* label() { return "alpha"; }\n'
            "char flag = 'A';\n"
            "double scale = 0.5;\n"
        )
        self._git("add", "sample.cpp")
        self._git(
            "-c",
            "user.name=stryker-cxx",
            "-c",
            "user.email=stryker-cxx@example.invalid",
            "commit",
            "-q",
            "-m",
            "literal-mutators",
        )
        report = self.repo / "literal-mutators.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--mutators",
            "IntegerLiteral,NullLiteral,CharacterLiteral,FloatingPointLiteral,StringLiteral",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        mutators = {mut["mutator"] for mut in payload["mutants"]}
        self.assertIn("IntegerLiteral", mutators)
        self.assertIn("NullLiteral", mutators)
        self.assertIn("CharacterLiteral", mutators)
        self.assertIn("FloatingPointLiteral", mutators)
        self.assertIn("StringLiteral", mutators)

    def test_clang_mode_runs_compile_database_fixture_when_bindings_are_available(self) -> None:
        try:
            from clang import cindex  # type: ignore
            cindex.Index.create()
        except Exception as exc:
            self.skipTest(f"optional libclang binding is unavailable: {exc}")

        self.source.write_text(
            "int main() {\n"
            "  if (1 == 1) return 0;\n"
            "  return 1;\n"
            "}\n"
        )
        compile_db = [
            {
                "directory": str(self.repo),
                "command": "clang++ -std=c++17 -c sample.cpp -o sample.o",
                "file": str(self.source),
            }
        ]
        (self.repo / "compile_commands.json").write_text(json.dumps(compile_db))
        self._git("add", "sample.cpp", "compile_commands.json")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "clang-fixture")
        report = self.repo / "clang.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--mutators",
            "EqualityOperator",
            "--mode",
            "clang",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["mode"], "clang")
        self.assertEqual(payload["totalMutants"], 1)
        self.assertEqual(payload["killed"], 1)
        self.assertEqual(payload["mutants"][0]["nodeKind"], "BINARY_OPERATOR")

    def test_clang_ast_mode_generates_direct_return_range_mutant_when_available(self) -> None:
        try:
            from clang import cindex  # type: ignore
            cindex.Index.create()
        except Exception as exc:
            self.skipTest(f"optional libclang binding is unavailable: {exc}")

        self.source.write_text(
            "bool flag() { return (true); }\n"
            "int main() { return flag() ? 0 : 1; }\n"
        )
        compile_db = [
            {
                "directory": str(self.repo),
                "command": "clang++ -std=c++17 -c sample.cpp -o sample.o",
                "file": str(self.source),
            }
        ]
        (self.repo / "compile_commands.json").write_text(json.dumps(compile_db))
        self._git("add", "sample.cpp", "compile_commands.json")
        self._git(
            "-c",
            "user.name=stryker-cxx",
            "-c",
            "user.email=stryker-cxx@example.invalid",
            "commit",
            "-q",
            "-m",
            "clang-ast-return-fixture",
        )
        report = self.repo / "clang-ast-return.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--mutators",
            "ReturnValue",
            "--mode",
            "clang-ast",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["totalMutants"], 1)
        self.assertEqual(payload["mutants"][0]["original"], "return (true)")
        self.assertEqual(payload["mutants"][0]["mutated"], "return (false)")
        self.assertEqual(payload["mutants"][0]["nodeKind"], "RETURN_STMT")
        self.assertEqual(payload["mutants"][0]["rewriteStrategy"], "clang-ast-direct-return")

    def test_clang_ast_mode_generates_direct_conditional_expression_mutant_when_available(self) -> None:
        try:
            from clang import cindex  # type: ignore
            cindex.Index.create()
        except Exception as exc:
            self.skipTest(f"optional libclang binding is unavailable: {exc}")

        self.source.write_text(
            "int choose(int x) { return x ? 1 : 0; }\n"
        )
        compile_db = [
            {
                "directory": str(self.repo),
                "command": "clang++ -std=c++17 -c sample.cpp -o sample.o",
                "file": str(self.source),
            }
        ]
        (self.repo / "compile_commands.json").write_text(json.dumps(compile_db))
        self._git("add", "sample.cpp", "compile_commands.json")
        self._git(
            "-c",
            "user.name=stryker-cxx",
            "-c",
            "user.email=stryker-cxx@example.invalid",
            "commit",
            "-q",
            "-m",
            "clang-ast-conditional-fixture",
        )
        report = self.repo / "clang-ast-conditional-expression.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--mutators",
            "ConditionalExpression",
            "--mode",
            "clang-ast",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["totalMutants"], 1)
        self.assertEqual(payload["mutants"][0]["original"].strip(), "1 : 0")
        self.assertEqual(payload["mutants"][0]["mutated"].strip(), "0 : 1")
        self.assertEqual(payload["mutants"][0]["nodeKind"], "CONDITIONAL_OPERATOR")
        self.assertEqual(payload["mutants"][0]["rewriteStrategy"], "clang-ast-direct-conditional")

    def test_clang_ast_mode_generates_direct_statement_range_mutant_when_available(self) -> None:
        try:
            from clang import cindex  # type: ignore
            cindex.Index.create()
        except Exception as exc:
            self.skipTest(f"optional libclang binding is unavailable: {exc}")

        self.source.write_text(
            "int main() {\n"
            "  int x = 1;\n"
            "  return x;\n"
            "}\n"
        )
        compile_db = [
            {
                "directory": str(self.repo),
                "command": "clang++ -std=c++17 -c sample.cpp -o sample.o",
                "file": str(self.source),
            }
        ]
        (self.repo / "compile_commands.json").write_text(json.dumps(compile_db))
        self._git("add", "sample.cpp", "compile_commands.json")
        self._git(
            "-c",
            "user.name=stryker-cxx",
            "-c",
            "user.email=stryker-cxx@example.invalid",
            "commit",
            "-q",
            "-m",
            "clang-ast-statement-fixture",
        )
        report = self.repo / "clang-ast-statement.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--mutators",
            "StatementRemoval",
            "--mode",
            "clang-ast",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["totalMutants"], 1)
        self.assertEqual(payload["mutants"][0]["original"].strip(), "int x = 1;")
        self.assertEqual(payload["mutants"][0]["mutated"], ";")
        self.assertEqual(payload["mutants"][0]["nodeKind"], "DECL_STMT")
        self.assertEqual(payload["mutants"][0]["rewriteStrategy"], "clang-ast-direct-statement")

    def test_clang_ast_mode_generates_direct_block_range_mutant_when_available(self) -> None:
        try:
            from clang import cindex  # type: ignore
            cindex.Index.create()
        except Exception as exc:
            self.skipTest(f"optional libclang binding is unavailable: {exc}")

        self.source.write_text(
            "int main() {{ int x = 1; } return 0; }\n"
        )
        compile_db = [
            {
                "directory": str(self.repo),
                "command": "clang++ -std=c++17 -c sample.cpp -o sample.o",
                "file": str(self.source),
            }
        ]
        (self.repo / "compile_commands.json").write_text(json.dumps(compile_db))
        self._git("add", "sample.cpp", "compile_commands.json")
        self._git(
            "-c",
            "user.name=stryker-cxx",
            "-c",
            "user.email=stryker-cxx@example.invalid",
            "commit",
            "-q",
            "-m",
            "clang-ast-block-fixture",
        )
        report = self.repo / "clang-ast-block.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--mutators",
            "BlockRemoval",
            "--mode",
            "clang-ast",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["totalMutants"], 1)
        self.assertEqual(payload["mutants"][0]["original"], "{ int x = 1; }")
        self.assertEqual(payload["mutants"][0]["mutated"], "{}")
        self.assertEqual(payload["mutants"][0]["nodeKind"], "COMPOUND_STMT")
        self.assertEqual(payload["mutants"][0]["rewriteStrategy"], "clang-ast-direct-block")

    def test_clang_ast_mode_generates_direct_integer_and_null_literals(self) -> None:
        try:
            from clang import cindex  # type: ignore
            cindex.Index.create()
        except Exception as exc:
            self.skipTest(f"optional libclang binding is unavailable: {exc}")

        self.source.write_text(
            "int value() { return 0; }\n"
            "void* ptr() { return nullptr; }\n"
        )
        compile_db = [
            {
                "directory": str(self.repo),
                "command": "clang++ -std=c++17 -c sample.cpp -o sample.o",
                "file": str(self.source),
            }
        ]
        (self.repo / "compile_commands.json").write_text(json.dumps(compile_db))
        self._git("add", "sample.cpp", "compile_commands.json")
        self._git(
            "-c",
            "user.name=stryker-cxx",
            "-c",
            "user.email=stryker-cxx@example.invalid",
            "commit",
            "-q",
            "-m",
            "clang-ast-literal-fixture",
        )
        report = self.repo / "clang-ast-literals.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--mutators",
            "IntegerLiteral,NullLiteral",
            "--mode",
            "clang-ast",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["totalMutants"], 2)
        for mut in payload["mutants"]:
            self.assertEqual(mut["rewriteStrategy"], "clang-ast-direct-literal")
            self.assertIn(mut["nodeKind"], {"INTEGER_LITERAL", "CXX_NULL_PTR_LITERAL_EXPR", "GNU_NULL_EXPR", "DECL_REF_EXPR"})
        int_mut = next(m for m in payload["mutants"] if m["mutator"] == "IntegerLiteral")
        null_mut = next(m for m in payload["mutants"] if m["mutator"] == "NullLiteral")
        self.assertEqual(int_mut["original"], "0")
        self.assertEqual(int_mut["mutated"], "1")
        self.assertEqual(null_mut["original"], "nullptr")
        self.assertEqual(null_mut["mutated"], "NULL")

    def test_clang_ast_mode_generates_direct_character_floating_and_string_literals_when_available(self) -> None:
        try:
            from clang import cindex  # type: ignore
            cindex.Index.create()
        except Exception as exc:
            self.skipTest(f"optional libclang binding is unavailable: {exc}")

        self.source.write_text(
            "char c = 'A';\n"
            "const char* s = \"x\";\n"
            "double d = 0.5;\n"
        )
        compile_db = [
            {
                "directory": str(self.repo),
                "command": "clang++ -std=c++17 -c sample.cpp -o sample.o",
                "file": str(self.source),
            }
        ]
        (self.repo / "compile_commands.json").write_text(json.dumps(compile_db))
        self._git("add", "sample.cpp", "compile_commands.json")
        self._git(
            "-c",
            "user.name=stryker-cxx",
            "-c",
            "user.email=stryker-cxx@example.invalid",
            "commit",
            "-q",
            "clang-ast-extra-literal-fixture",
        )
        report = self.repo / "clang-ast-extra-literals.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--mutators",
            "CharacterLiteral,FloatingPointLiteral,StringLiteral",
            "--mode",
            "clang-ast",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["totalMutants"], 3)
        for mut in payload["mutants"]:
            self.assertEqual(mut["rewriteStrategy"], "clang-ast-direct-literal")
            self.assertIn(mut["nodeKind"], {"CHARACTER_LITERAL", "STRING_LITERAL", "FLOATING_LITERAL", "CXX_CHAR_LITERAL", "OBJC_CHAR_LITERAL", "CXX_STRING_LITERAL", "OBJC_STRING_LITERAL", "CXX_FLOATING_LITERAL"})

        char_mut = next(m for m in payload["mutants"] if m["mutator"] == "CharacterLiteral")
        float_mut = next(m for m in payload["mutants"] if m["mutator"] == "FloatingPointLiteral")
        string_mut = next(m for m in payload["mutants"] if m["mutator"] == "StringLiteral")
        self.assertEqual(char_mut["original"].strip(), "'A'")
        self.assertEqual(char_mut["mutated"].strip(), "'x'")
        self.assertEqual(float_mut["original"].strip(), "0.5")
        self.assertEqual(float_mut["mutated"].strip(), "1.0")
        self.assertEqual(string_mut["original"].strip(), '"x"')
        self.assertEqual(string_mut["mutated"].strip(), '""')

    def test_git_worktree_mode_runs_without_mutating_source(self) -> None:
        report = self.repo / "worktree.json"
        original = self.source.read_text()

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--worktree-mode",
            "git-worktree",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["worktreeMode"], "git-worktree")
        self.assertEqual(payload["killed"], 1)

    def test_sharding_runs_a_stable_subset(self) -> None:
        self.source.write_text(
            "int main() { if (1 == 1) return 0; if (2 == 2) return 0; return 1; }\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "two-mutants")
        report = self.repo / "shard.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(report),
            "--mutators",
            "EqualityOperator",
            "--shard-index",
            "2",
            "--shard-total",
            "2",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["totalMutants"], 1)
        self.assertEqual(payload["killed"], 1)
        self.assertIn(":EqualityOperator:", payload["mutants"][0]["id"])

    def test_markdown_and_sarif_artifacts_are_generated(self) -> None:
        markdown_report = self.repo / "human.json"
        markdown = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(markdown_report),
            "--max-mutants",
            "1",
            "--format",
            "markdown",
            "--quiet",
        )
        self.assertEqual(markdown.returncode, 2, markdown.stderr + markdown.stdout)
        markdown_artifact = self.repo / "human.json.md"
        self.assertTrue(markdown_artifact.exists())
        markdown_text = markdown_artifact.read_text()
        self.assertIn("# stryker-cxx report", markdown_text)
        self.assertIn("## Mutator summary", markdown_text)
        self.assertIn("| mutator | total | killed | survived | build errors | check errors | no coverage | timeouts | ignored | score |", markdown_text)

        sarif_report = self.repo / "code-scanning.json"
        sarif = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--skip-initial-test",
            "--report",
            str(sarif_report),
            "--max-mutants",
            "1",
            "--format",
            "sarif",
            "--quiet",
        )
        self.assertEqual(sarif.returncode, 0, sarif.stderr + sarif.stdout)
        sarif_artifact = self.repo / "code-scanning.json.sarif"
        self.assertTrue(sarif_artifact.exists())
        self.assertEqual(json.loads(sarif_artifact.read_text())["version"], "2.1.0")

        annotations_report = self.repo / "annotations.json"
        annotations = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(annotations_report),
            "--max-mutants",
            "1",
            "--format",
            "github-annotations",
            "--quiet",
        )
        self.assertEqual(annotations.returncode, 2, annotations.stderr + annotations.stdout)
        annotations_artifact = self.repo / "annotations.json.github-annotations"
        self.assertTrue(annotations_artifact.exists())
        self.assertIn("::warning file=sample.cpp,line=1,", annotations_artifact.read_text())

        error_annotations_report = self.repo / "error-annotations.json"
        error_annotations = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "false",
            "--test-command",
            "true",
            "--skip-initial-test",
            "--report",
            str(error_annotations_report),
            "--max-mutants",
            "1",
            "--format",
            "github-annotations",
            "--quiet",
        )
        self.assertEqual(error_annotations.returncode, 2, error_annotations.stderr + error_annotations.stdout)
        error_annotations_artifact = self.repo / "error-annotations.json.github-annotations"
        self.assertTrue(error_annotations_artifact.exists())
        self.assertIn("::error file=sample.cpp,line=1,", error_annotations_artifact.read_text())

        html_report = self.repo / "page.json"
        html = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--report",
            str(html_report),
            "--max-mutants",
            "1",
            "--format",
            "html",
            "--quiet",
        )
        self.assertEqual(html.returncode, 1, html.stderr + html.stdout)
        html_artifact = self.repo / "page.json.html"
        self.assertTrue(html_artifact.exists())
        self.assertIn("<h1>stryker-cxx report</h1>", html_artifact.read_text())
        self.assertIn("id='filter'", html_artifact.read_text())
        self.assertIn("data-sort='status'", html_artifact.read_text())

    def test_initial_dry_run_failure_stops_before_mutant_execution(self) -> None:
        report = self.repo / "dry-run-failed.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--quiet",
        )

        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["dryRun"]["status"], "FAILED")
        self.assertEqual(payload["dryRun"]["failureReason"], "initial tests failed")
        self.assertEqual(payload["killed"], 0)
        self.assertEqual(payload["survived"], 0)
        self.assertEqual(payload["mutants"], [])

    def test_dry_run_only_writes_lifecycle_report_without_mutating(self) -> None:
        report = self.repo / "dry-run-only.json"
        original = self.source.read_text()

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--dry-run-only",
            "--timeout-factor",
            "1.0",
            "--timeout-constant-ms",
            "250",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["dryRun"]["status"], "PASSED")
        self.assertTrue(payload["execution"]["dryRunOnly"])
        self.assertGreaterEqual(payload["execution"]["effectiveTimeoutMs"], 250)
        self.assertEqual(payload["totalMutants"], 1)
        self.assertEqual(payload["mutants"], [])

    def test_dashboard_export_writes_compact_dashboard_payload(self) -> None:
        report = self.repo / "dashboard-run.json"
        dashboard = self.repo / "dashboard-export.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(report),
            "--dashboard-export",
            str(dashboard),
            "--dashboard-version",
            "1",
            "--dashboard-retention-days",
            "14",
            "--dashboard-project",
            "openclaw/stryker-cxx-fixture",
            "--dashboard-branch",
            "feature/dashboard",
            "--dashboard-commit",
            "abc123",
            "--dashboard-build-url",
            "https://ci.example/build/123",
            "--dashboard-auth-token-env",
            "STRYKER_CXX_DASHBOARD_TOKEN",
            "--max-mutants",
            "1",
            "--dry-run-only",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(dashboard.read_text())
        self.assertEqual(payload["schemaVersion"], "stryker-cxx.dashboard.v1")
        self.assertEqual(payload["dashboardVersion"], "1")
        self.assertEqual(payload["toolVersion"], "0.1.0")
        self.assertEqual(payload["retention"]["days"], 14)
        self.assertFalse(payload["privacy"]["sourceFilesIncluded"])
        self.assertTrue(payload["privacy"]["secretValuesRedacted"])
        self.assertEqual(payload["project"], "openclaw/stryker-cxx-fixture")
        self.assertEqual(payload["branch"], "feature/dashboard")
        self.assertEqual(payload["commit"], "abc123")
        self.assertIn("runId", payload)
        self.assertEqual(payload["buildUrl"], "https://ci.example/build/123")
        self.assertEqual(payload["provenance"]["ci"]["buildUrl"], "https://ci.example/build/123")
        self.assertEqual(payload["provenance"]["upload"]["authTokenEnv"], "STRYKER_CXX_DASHBOARD_TOKEN")
        self.assertEqual(payload["provenance"]["upload"]["status"], "disabled")
        self.assertIn(payload["thresholdStatus"], {"high", "low", "break"})
        self.assertEqual(payload["counts"]["totalMutants"], 1)

    def test_check_command_failure_is_reported_separately(self) -> None:
        report = self.repo / "check-error.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--check-command",
            "false",
            "--test-command",
            "true",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["checkErrors"], 1)
        self.assertEqual(payload["mutants"][0]["status"], "CHECK_ERROR")
        self.assertEqual(payload["mutants"][0]["resultSource"], "compile-pruning")
        self.assertEqual(payload["mutants"][0]["run"]["testSkippedReason"], "compile-pruned")
        self.assertEqual(payload["execution"]["compilePruning"]["prunedMutants"], 1)
        self.assertEqual(payload["execution"]["compilePruning"]["checkErrors"], 1)
        self.assertEqual(payload["commands"]["check"], "false")

    def test_coverage_file_marks_uncovered_mutants_without_execution(self) -> None:
        report = self.repo / "coverage.json"
        coverage = self.repo / "coverage-input.json"
        coverage.write_text(json.dumps({"files": {"sample.cpp": {"coveredLines": []}}}))

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--coverage-file",
            str(coverage),
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["noCoverage"], 1)
        self.assertEqual(payload["coverage"]["noCoverageMutants"], 1)
        self.assertEqual(payload["coverage"]["unknownCoverageMutants"], 0)
        self.assertEqual(payload["mutants"][0]["status"], "NO_COVERAGE")
        self.assertEqual(payload["mutants"][0]["run"]["coverageStatus"], "not-covered")
        first = payload["mutationTestingElements"]["files"]["sample.cpp"]["mutants"][0]
        self.assertEqual(first["status"], "NoCoverage")

    def test_coverage_file_can_select_per_mutant_test_command(self) -> None:
        report = self.repo / "coverage-selected-tests.json"
        coverage = self.repo / "coverage-tests-input.json"
        coverage.write_text(json.dumps({
            "files": {
                "sample.cpp": {
                    "coveredLines": [1],
                    "coveredTests": {"1": ["MathTest.Basic"]},
                }
            }
        }))

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--coverage-file",
            str(coverage),
            "--coverage-test-command-template",
            "test {first_test} = MathTest.Basic",
            "--skip-initial-test",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["coverage"]["testSelectedMutants"], 1)
        self.assertEqual(payload["coverage"]["testSelectionMisses"], 0)
        self.assertEqual(payload["mutants"][0]["run"]["coverageStatus"], "covered")
        self.assertEqual(payload["mutants"][0]["run"]["coveredBy"], ["MathTest.Basic"])
        self.assertEqual(payload["mutants"][0]["run"]["selectedTestCommand"], "test MathTest.Basic = MathTest.Basic")
        self.assertEqual(payload["mutants"][0]["run"]["scheduler"]["coverageSelected"], True)
        self.assertEqual(payload["execution"]["testScheduler"]["coverageSelectedSessions"], 1)
        self.assertEqual(payload["mutants"][0]["status"], "SURVIVED")
        mte = payload["mutationTestingElements"]["files"]["sample.cpp"]["mutants"][0]
        self.assertEqual(mte["coveredBy"], ["MathTest.Basic"])

    def test_batched_coverage_selection_uses_union_test_command(self) -> None:
        self.source.write_text(
            "int a() { return 1 == 1; }\n"
            "int b() { return 2 == 2; }\n"
            "int c() { return 3 == 3; }\n"
            "int d() { return 4 == 4; }\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "batch-coverage")
        report = self.repo / "batch-coverage.json"
        coverage = self.repo / "batch-coverage-input.json"
        coverage.write_text(json.dumps({
            "files": {
                "sample.cpp": {
                    "coveredLines": [1, 2, 3, 4],
                    "coveredTests": {
                        "1": ["MathTest.A"],
                        "2": ["MathTest.B"],
                        "3": ["MathTest.C"],
                        "4": ["MathTest.D"],
                    },
                }
            }
        }))

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--coverage-file",
            str(coverage),
            "--coverage-test-command-template",
            "true {tests_space}",
            "--report",
            str(report),
            "--mutators",
            "EqualityOperator",
            "--batch-mutants",
            "--batch-size",
            "2",
            "--worktree-mode",
            "copy",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["survived"], 4)
        self.assertEqual(payload["coverage"]["coveredMutants"], 4)
        self.assertEqual(payload["coverage"]["testSelectedMutants"], 4)
        self.assertEqual(payload["execution"]["testScheduler"]["strategy"], "batched")
        self.assertEqual(payload["execution"]["testScheduler"]["batchSessions"], 2)
        self.assertEqual(payload["execution"]["testScheduler"]["coverageSelectedSessions"], 2)
        for group in payload["execution"]["testScheduler"]["groups"]:
            self.assertTrue(group["coverageSelected"])
            self.assertTrue(group["testCommand"].startswith("true "))
            self.assertEqual(len(group["selectedTests"]), 2)
        for mut in payload["mutants"]:
            self.assertEqual(mut["resultSource"], "batch")
            self.assertEqual(mut["run"]["coverageStatus"], "covered")
            self.assertTrue(mut["run"]["scheduler"]["coverageSelected"])

    def test_coverage_helper_generates_test_level_mapping(self) -> None:
        report = self.repo / "coverage-helper-selected-tests.json"
        helper = self.repo / "write_coverage.py"
        helper.write_text(
            "import json, os\n"
            "with open(os.environ['STRYKER_CXX_COVERAGE_FILE'], 'w') as f:\n"
            "    json.dump({'files': {'sample.cpp': {'coveredLines': [1]}}}, f)\n"
        )

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--coverage-helper-command-template",
            f"{sys.executable} {helper}",
            "--coverage-helper-tests",
            "MathTest.Basic",
            "--coverage-test-command-template",
            "test {first_test}",
            "--skip-initial-test",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["coverage"]["helper"]["testCount"], 1)
        self.assertEqual(payload["coverage"]["testSelectedMutants"], 1)
        self.assertEqual(payload["mutants"][0]["run"]["coveredBy"], ["MathTest.Basic"])
        self.assertEqual(payload["mutants"][0]["run"]["selectedTestCommand"], "test MathTest.Basic")

    def test_threshold_bands_preserve_break_exit_behavior(self) -> None:
        report = self.repo / "thresholds.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--threshold-high",
            "0.9",
            "--threshold-low",
            "0.5",
            "--threshold-break",
            "0",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["score"], 0)
        self.assertEqual(payload["threshold"], 0)
        self.assertEqual(payload["thresholds"], {"high": 0.9, "low": 0.5, "break": 0.0, "status": "low"})
        self.assertEqual(payload["summary"]["byStatus"]["SURVIVED"], 1)
        self.assertEqual(payload["summary"]["byFile"]["sample.cpp"]["survived"], 1)
        self.assertEqual(payload["summary"]["byMutator"]["EqualityOperator"]["survived"], 1)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
