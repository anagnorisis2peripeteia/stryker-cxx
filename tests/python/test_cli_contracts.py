from __future__ import annotations

import json
import hashlib
import os
import shutil
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
        self.assertEqual(payload["execution"]["mode"], "token")
        self.assertEqual(payload["execution"]["analysis"]["engine"], "token")
        self.assertEqual(payload["execution"]["executionMode"], "source-overlay")
        self.assertEqual(payload["execution"]["requestedExecutionMode"], "source-overlay")
        self.assertFalse(payload["execution"]["mutantSwitch"]["enabled"])
        self.assertEqual(payload["mutationTestingElements"]["executionMode"], "source-overlay")
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

    def test_execution_mode_mutant_switch_reports_source_overlay_fallback(self) -> None:
        self.source.write_text("struct Node { int value; }; int main() { Node node{1}; return node.value; }\n")
        self._git("add", "sample.cpp")
        self._git(
            "-c",
            "user.name=stryker-cxx",
            "-c",
            "user.email=stryker-cxx@example.invalid",
            "commit",
            "-q",
            "-m",
            "unsupported switch mutator fixture",
        )
        report = self.repo / "mutant-switch-fallback.json"
        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--mutators",
            "MemberAccessOperator",
            "--max-mutants",
            "1",
        )
        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        listed_mutant = json.loads(listed.stdout)[0]

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
            "--mutators",
            "MemberAccessOperator",
            "--execution-mode",
            "mutant-switch",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["requestedExecutionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["executionMode"], "source-overlay")
        self.assertTrue(payload["execution"]["mutantSwitch"]["requested"])
        self.assertFalse(payload["execution"]["mutantSwitch"]["enabled"])
        self.assertIn("MemberAccessOperator", payload["execution"]["mutantSwitch"]["fallbackReason"])
        self.assertEqual(
            payload["execution"]["mutantSwitch"]["activationEnvironment"],
            "STRYKER_CXX_ACTIVE_MUTANT",
        )
        self.assertEqual(payload["execution"]["mutantSwitch"]["candidateGuardCount"], 1)
        self.assertEqual(payload["execution"]["mutantSwitch"]["runtimeGuardCount"], 0)
        self.assertEqual(
            payload["execution"]["mutantSwitch"]["guards"],
            [
                {
                    "mutantId": listed_mutant["id"],
                    "guardId": listed_mutant["mutantSwitchGuardId"],
                }
            ],
        )
        self.assertEqual(
            payload["execution"]["mutantSwitch"]["artifactCandidate"]["mode"],
            "mutant-switch",
        )
        self.assertEqual(
            payload["mutants"][0]["run"]["mutantSwitchGuardId"],
            listed_mutant["mutantSwitchGuardId"],
        )
        self.assertEqual(payload["mutationTestingElements"]["executionMode"], "source-overlay")
        self.assertEqual(payload["mutationTestingElements"]["strykerCxx"]["requestedExecutionMode"], "mutant-switch")

    def test_execution_mode_mutant_switch_runs_guardable_boolean_once(self) -> None:
        self.source.write_text("bool flag() { return true; }\nint main() { return flag() ? 0 : 1; }\n")
        self._git("add", "sample.cpp")
        self._git(
            "-c",
            "user.name=stryker-cxx",
            "-c",
            "user.email=stryker-cxx@example.invalid",
            "commit",
            "-q",
            "-m",
            "boolean fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-boolean.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "BooleanLiteral",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["requestedExecutionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertTrue(payload["execution"]["mutantSwitch"]["enabled"])
        self.assertIsNone(payload["execution"]["mutantSwitch"]["fallbackReason"])
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["mutationArtifact"]["mode"], "mutant-switch")
        self.assertEqual(payload["artifactPlacement"]["mode"], "mutant-switch")
        self.assertTrue(payload["artifactPlacement"]["mutantSwitch"]["guardedSourceOverlay"])
        self.assertEqual(
            payload["artifactPlacement"]["mutantSwitch"]["activationEnvironment"],
            "STRYKER_CXX_ACTIVE_MUTANT",
        )
        self.assertEqual(payload["lifecycle"]["artifactModel"], "mutant-switch")
        lifecycle_by_name = {phase["name"]: phase for phase in payload["lifecycle"]["phases"]}
        self.assertEqual(lifecycle_by_name["mutationArtifact"]["status"], "mutantSwitch")
        self.assertEqual(lifecycle_by_name["artifactRestoration"]["status"], "mutantSwitch")
        self.assertEqual(payload["mutationTestingElements"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["mutants"][0]["resultSource"], "mutant-switch")
        self.assertEqual(payload["mutants"][0]["run"]["executionMode"], "mutant-switch")
        self.assertTrue(payload["mutants"][0]["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_return_and_literal_mutants_in_one_compile(self) -> None:
        self.source.write_text(
            "bool flag() { return true; }\n"
            "int value() { return 0; }\n"
            "int main() { return flag() ? value() : value(); }\n"
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
            "return and literal switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-return-literal.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "2",
            "--mutators",
            "ReturnValue,IntegerLiteral",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "testtest")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 2)
        self.assertEqual({mut["mutator"] for mut in payload["mutants"]}, {"ReturnValue", "IntegerLiteral"})
        self.assertEqual({mut["resultSource"] for mut in payload["mutants"]}, {"mutant-switch"})
        for mut in payload["mutants"]:
            self.assertEqual(mut["run"]["executionMode"], "mutant-switch")
            self.assertTrue(mut["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_token_binary_operator_spans(self) -> None:
        self.source.write_text(
            "bool both(bool a, bool b) { return a && b; }\n"
            "int main() { return both(1 == 1, true) ? 0 : 1; }\n"
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
            "binary operator switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-binary-operators.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "2",
            "--mutators",
            "EqualityOperator,LogicalOperator",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "testtest")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 2)
        self.assertEqual({mut["mutator"] for mut in payload["mutants"]}, {"EqualityOperator", "LogicalOperator"})
        self.assertEqual({mut["resultSource"] for mut in payload["mutants"]}, {"mutant-switch"})
        for mut in payload["mutants"]:
            self.assertEqual(mut["run"]["executionMode"], "mutant-switch")
            self.assertEqual(mut["rewriteStrategy"], "token-binary-expression")
            self.assertEqual(mut["sourceRange"]["kind"], "TOKEN_BINARY_EXPRESSION")
            self.assertTrue(mut["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_call_operand_operator_spans(self) -> None:
        self.source.write_text(
            "bool ready() { return true; }\n"
            "bool enabled() { return true; }\n"
            "int main() { return ready() && enabled() ? 0 : 1; }\n"
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
            "call operand switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-call-operands.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "LogicalOperator",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "LogicalOperator")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-binary-expression")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_BINARY_EXPRESSION")
        self.assertEqual(mutant["original"], "&&")
        self.assertEqual(mutant["mutated"], "||")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_parenthesized_operator_spans(self) -> None:
        self.source.write_text(
            "bool matches(int a, int b) { return (a + 1) == (b + 2); }\n"
            "int main() { return matches(0, -1) ? 0 : 1; }\n"
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
            "parenthesized operand switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-parenthesized-operands.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "EqualityOperator",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "EqualityOperator")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-binary-expression")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_BINARY_EXPRESSION")
        self.assertEqual(mutant["original"], "==")
        self.assertEqual(mutant["mutated"], "!=")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_logical_not_unary_spans(self) -> None:
        self.source.write_text(
            "bool ready(bool flag) { return !flag; }\n"
            "int main() { return ready(false) ? 0 : 1; }\n"
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
            "logical-not switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-logical-not.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "2",
            "--mutators",
            "UnaryOperator",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "testtest")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 2)
        self.assertEqual({mut["mutator"] for mut in payload["mutants"]}, {"UnaryOperator"})
        self.assertEqual({mut["original"] for mut in payload["mutants"]}, {"!"})
        self.assertEqual({mut["mutated"] for mut in payload["mutants"]}, {"", "!!"})
        self.assertEqual({mut["resultSource"] for mut in payload["mutants"]}, {"mutant-switch"})
        for mut in payload["mutants"]:
            self.assertEqual(mut["run"]["executionMode"], "mutant-switch")
            self.assertEqual(mut["rewriteStrategy"], "token-unary-expression")
            self.assertEqual(mut["sourceRange"]["kind"], "TOKEN_UNARY_EXPRESSION")
            self.assertTrue(mut["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_unary_sign_spans(self) -> None:
        self.source.write_text(
            "int value(int input) { return -input; }\n"
            "int main() { return value(1) < 0 ? 0 : 1; }\n"
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
            "unary sign switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-unary-sign.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "UnaryOperator",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "UnaryOperator")
        self.assertEqual(mutant["original"], "-")
        self.assertEqual(mutant["mutated"], "+")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-unary-sign")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_UNARY_EXPRESSION")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_conditional_expression_spans(self) -> None:
        self.source.write_text(
            "int choose(bool flag) { return flag ? 1 : 2; }\n"
            "int main() { return choose(true) == 1 ? 0 : 1; }\n"
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
            "conditional expression switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-conditional-expression.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "ConditionalExpression",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "ConditionalExpression")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertIn("flag ? 1 : 2", mutant["original"])
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_loop_condition_spans(self) -> None:
        self.source.write_text(
            "int count(int n) {\n"
            "  int total = 0;\n"
            "  while (n > 0) { total += n; --n; }\n"
            "  return total;\n"
            "}\n"
            "int main() { return count(2) == 3 ? 0 : 1; }\n"
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
            "loop condition switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-loop-condition.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "LoopCondition",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "LoopCondition")
        self.assertEqual(mutant["original"], "n > 0")
        self.assertEqual(mutant["mutated"], "!(n > 0)")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-loop-condition")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_LOOP_CONDITION")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_loop_boundary_spans(self) -> None:
        self.source.write_text(
            "int count(int n) {\n"
            "  int total = 0;\n"
            "  while (n > 0) { total += n; --n; }\n"
            "  return total;\n"
            "}\n"
            "int main() { return count(2) == 3 ? 0 : 1; }\n"
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
            "loop boundary switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-loop-boundary.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "LoopBoundary",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "LoopBoundary")
        self.assertEqual(mutant["original"], ">")
        self.assertEqual(mutant["mutated"], ">=")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-loop-boundary")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_LOOP_CONDITION")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_move_semantics_call_wrappers(self) -> None:
        self.source.write_text(
            "#include <utility>\n"
            "int value(int input) { return std::move(input); }\n"
            "int main() { return value(3) == 3 ? 0 : 1; }\n"
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
            "move semantics switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-move-semantics.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "MoveSemantics",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "MoveSemantics")
        self.assertEqual(mutant["original"], "std::move(input)")
        self.assertEqual(mutant["mutated"], "input")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-call-wrapper-removal")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_CALL_EXPRESSION")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_math_call_replacements(self) -> None:
        self.source.write_text(
            "#include <cmath>\n"
            "double value(double input) { return std::ceil(input); }\n"
            "int main() { return value(1.2) == 2.0 ? 0 : 1; }\n"
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
            "math call switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-math-call.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "MathCall",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "MathCall")
        self.assertEqual(mutant["original"], "std::ceil(input)")
        self.assertEqual(mutant["mutated"], "std::floor(input)")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-math-call")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_CALL_EXPRESSION")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_iterator_call_replacements(self) -> None:
        self.source.write_text(
            "#include <iterator>\n"
            "#include <vector>\n"
            "auto value(std::vector<int>& values) { return std::next(values.begin()); }\n"
            "int main() { std::vector<int> values{1, 2}; return *value(values) == 2 ? 0 : 1; }\n"
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
            "iterator call switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-iterator-call.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "IteratorCall",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "IteratorCall")
        self.assertEqual(mutant["original"], "std::next(values.begin())")
        self.assertEqual(mutant["mutated"], "std::prev(values.begin())")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-iterator-call")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_CALL_EXPRESSION")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_chrono_call_replacements(self) -> None:
        self.source.write_text(
            "#include <chrono>\n"
            "auto value(std::chrono::milliseconds duration) { return std::chrono::floor<std::chrono::seconds>(duration); }\n"
            "int main() { return value(std::chrono::milliseconds(1200)).count() == 1 ? 0 : 1; }\n"
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
            "chrono call switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-chrono-call.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "ChronoCall",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "ChronoCall")
        self.assertEqual(mutant["original"], "std::chrono::floor<std::chrono::seconds>(duration)")
        self.assertEqual(mutant["mutated"], "std::chrono::ceil<std::chrono::seconds>(duration)")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-chrono-call")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_CALL_EXPRESSION")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_regex_call_replacements(self) -> None:
        self.source.write_text(
            "#include <regex>\n"
            "#include <string>\n"
            "bool value(const std::string& text, const std::regex& re) { std::smatch match; return std::regex_match(text, match, re); }\n"
            "int main() { return value(\"abc\", std::regex(\"abc\")) ? 0 : 1; }\n"
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
            "regex call switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-regex-call.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "RegexCall",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "RegexCall")
        self.assertEqual(mutant["original"], "std::regex_match(text, match, re)")
        self.assertEqual(mutant["mutated"], "std::regex_search(text, match, re)")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-regex-call")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_CALL_EXPRESSION")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_filesystem_call_replacements(self) -> None:
        self.source.write_text(
            "#include <filesystem>\n"
            "bool value(const std::filesystem::path& path) { return std::filesystem::exists(path); }\n"
            "int main() { return value(\"sample.cpp\") ? 0 : 1; }\n"
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
            "filesystem call switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-filesystem-call.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "FilesystemCall",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "FilesystemCall")
        self.assertEqual(mutant["original"], "std::filesystem::exists(path)")
        self.assertEqual(mutant["mutated"], "std::filesystem::is_empty(path)")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-filesystem-call")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_CALL_EXPRESSION")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_container_call_replacements(self) -> None:
        self.source.write_text(
            "#include <vector>\n"
            "int value(std::vector<int>& values) { return values.front(); }\n"
            "int main() { std::vector<int> values{1, 2}; return value(values) == 1 ? 0 : 1; }\n"
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
            "container call switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-container-call.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "ContainerCall",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "ContainerCall")
        self.assertEqual(mutant["original"], "values.front()")
        self.assertEqual(mutant["mutated"], "values.back()")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-container-call")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_CALL_EXPRESSION")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_container_state_call_replacements(self) -> None:
        self.source.write_text(
            "#include <vector>\n"
            "bool value(std::vector<int>& values) { return values.empty(); }\n"
            "int main() { std::vector<int> values; return value(values) ? 0 : 1; }\n"
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
            "container state call switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-container-state-call.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "ContainerStateCall",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "ContainerStateCall")
        self.assertEqual(mutant["original"], "values.empty()")
        self.assertEqual(mutant["mutated"], "values.size()")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-container-state-call")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_CALL_EXPRESSION")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_string_call_replacements(self) -> None:
        self.source.write_text(
            "#include <string>\n"
            "std::size_t value(std::string& label) { return label.find(\"x\"); }\n"
            "int main() { std::string label{\"x\"}; return value(label) == 0 ? 0 : 1; }\n"
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
            "string call switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-string-call.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "StringCall",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "StringCall")
        self.assertEqual(mutant["original"], 'label.find("x")')
        self.assertEqual(mutant["mutated"], 'label.rfind("x")')
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-string-call")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_CALL_EXPRESSION")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_standard_library_call_replacements(self) -> None:
        self.source.write_text(
            "#include <algorithm>\n"
            "int value() { return std::min(1, 2); }\n"
            "int main() { return value() == 1 ? 0 : 1; }\n"
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
            "standard library call switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-standard-library-call.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "StandardLibraryCall",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "StandardLibraryCall")
        self.assertEqual(mutant["original"], "std::min(1, 2)")
        self.assertEqual(mutant["mutated"], "std::max(1, 2)")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-standard-library-call")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_CALL_EXPRESSION")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_memory_order_replacements(self) -> None:
        self.source.write_text(
            "#include <atomic>\n"
            "int value(std::atomic<int>& flag) { return flag.load(std::memory_order_relaxed); }\n"
            "int main() { std::atomic<int> flag{1}; return value(flag) == 1 ? 0 : 1; }\n"
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
            "memory order switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-memory-order.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "MemoryOrder",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "MemoryOrder")
        self.assertEqual(mutant["original"], "std::memory_order_relaxed")
        self.assertEqual(mutant["mutated"], "std::memory_order_seq_cst")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_update_operator_replacements(self) -> None:
        self.source.write_text(
            "int value(int input) { return input++; }\n"
            "int main() { return value(1) == 1 ? 0 : 1; }\n"
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
            "update operator switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-update-operator.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "UpdateOperator",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "UpdateOperator")
        self.assertEqual(mutant["original"], "input++")
        self.assertEqual(mutant["mutated"], "input--")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-update-expression")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_UPDATE_EXPRESSION")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_call_removal_replacements(self) -> None:
        self.source.write_text(
            "void touched() {}\n"
            "int main() {\n"
            "  touched();\n"
            "  return 0;\n"
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
            "call removal switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-call-removal.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "CallRemoval",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "CallRemoval")
        self.assertEqual(mutant["original"], "touched()")
        self.assertEqual(mutant["mutated"], "(void)0")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-call-removal")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_CALL_EXPRESSION")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_exception_handling_replacements(self) -> None:
        self.source.write_text(
            "#include <stdexcept>\n"
            "int main() {\n"
            "  throw std::runtime_error(\"x\");\n"
            "  return 0;\n"
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
            "exception switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-exception-handling.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "ExceptionHandling",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "ExceptionHandling")
        self.assertEqual(mutant["original"], 'throw std::runtime_error("x");')
        self.assertEqual(mutant["mutated"], "(void)0;")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-throw-statement")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_THROW_STATEMENT")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_expression_statement_removal(self) -> None:
        self.source.write_text(
            "int main() {\n"
            "  1 + 1;\n"
            "  return 0;\n"
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
            "statement removal switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-statement-removal.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "StatementRemoval",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "StatementRemoval")
        self.assertEqual(mutant["original"], "1 + 1;")
        self.assertEqual(mutant["mutated"], ";")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-statement-removal")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_STATEMENT")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_block_removal(self) -> None:
        self.source.write_text(
            "int main() {\n"
            "  { 1 + 1; }\n"
            "  return 0;\n"
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
            "block removal switch fixture",
        )
        original = self.source.read_text()
        report = self.repo / "mutant-switch-block-removal.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "BlockRemoval",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "BlockRemoval")
        self.assertEqual(mutant["original"], "{ 1 + 1; }")
        self.assertEqual(mutant["mutated"], "{}")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-block-removal")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_BLOCK")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_guards_objc_message_send_replacements(self) -> None:
        source = self.repo / "sample.mm"
        source.write_text(
            "void objc(id obj) {\n"
            "  [obj touch];\n"
            "}\n"
            "int main() { return 0; }\n"
        )
        self._git("add", "sample.mm")
        self._git(
            "-c",
            "user.name=stryker-cxx",
            "-c",
            "user.email=stryker-cxx@example.invalid",
            "commit",
            "-q",
            "-m",
            "objc message switch fixture",
        )
        original = source.read_text()
        report = self.repo / "mutant-switch-objc-message-send.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.mm",
            "--build-command",
            "printf build >> build-count.txt && clang++ -x objective-c++ -fsyntax-only sample.mm",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "ObjCMessageSend",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(source.read_text(), original)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        mutant = payload["mutants"][0]
        self.assertEqual(mutant["mutator"], "ObjCMessageSend")
        self.assertEqual(mutant["original"], "[obj touch]")
        self.assertEqual(mutant["mutated"], "(void)0")
        self.assertEqual(mutant["resultSource"], "mutant-switch")
        self.assertEqual(mutant["run"]["executionMode"], "mutant-switch")
        self.assertEqual(mutant["rewriteStrategy"], "token-objc-message-send")
        self.assertEqual(mutant["sourceRange"]["kind"], "TOKEN_OBJC_MESSAGE_EXPR")
        self.assertTrue(mutant["run"]["mutantSwitchGuardId"].startswith("msw-"))

    def test_execution_mode_mutant_switch_falls_back_for_overlapping_token_spans(self) -> None:
        self.source.write_text("int main() { return 1 + 1 + 1; }\n")
        self._git("add", "sample.cpp")
        self._git(
            "-c",
            "user.name=stryker-cxx",
            "-c",
            "user.email=stryker-cxx@example.invalid",
            "commit",
            "-q",
            "-m",
            "overlapping switch spans fixture",
        )
        report = self.repo / "mutant-switch-overlap-fallback.json"

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
            "2",
            "--mutators",
            "ArithmeticOperator",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["requestedExecutionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["executionMode"], "source-overlay")
        self.assertFalse(payload["execution"]["mutantSwitch"]["enabled"])
        self.assertIn("overlapping mutant-switch expression spans", payload["execution"]["mutantSwitch"]["fallbackReason"])
        self.assertEqual(payload["totalMutants"], 2)

    def test_execution_mode_mutant_switch_prunes_compile_failing_guard(self) -> None:
        self.source.write_text(
            "bool a() { return true; }\n"
            "bool b() { return false; }\n"
            "int main() { return a() && !b() ? 0 : 1; }\n"
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
            "two booleans",
        )
        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--mutators",
            "BooleanLiteral",
            "--max-mutants",
            "2",
        )
        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        listed_payload = json.loads(listed.stdout)
        bad_guard = listed_payload[1]["mutantSwitchGuardId"]
        build_script = self.repo / "switch_build.py"
        build_script.write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "Path('build-count.txt').write_text(Path('build-count.txt').read_text() + 'b' if Path('build-count.txt').exists() else 'b')\n"
            f"sys.exit(1 if {bad_guard!r} in Path('sample.cpp').read_text() else 0)\n"
        )
        report = self.repo / "mutant-switch-prune.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            f"{sys.executable} {build_script}",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--max-mutants",
            "2",
            "--mutators",
            "BooleanLiteral",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["compilePruning"]["strategy"], "mutant-switch-prune-and-retry")
        self.assertEqual(payload["execution"]["compilePruning"]["attempts"], 1)
        self.assertEqual(payload["execution"]["compilePruning"]["retryBatches"], 1)
        self.assertEqual(payload["execution"]["compilePruning"]["prunedMutants"], 1)
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 4)
        self.assertEqual(payload["lifecycle"]["artifactModel"], "mutant-switch")
        lifecycle_by_name = {phase["name"]: phase for phase in payload["lifecycle"]["phases"]}
        self.assertEqual(lifecycle_by_name["compilePruning"]["detail"]["attempts"], 1)
        self.assertEqual(lifecycle_by_name["compilePruning"]["detail"]["retryBatches"], 1)
        self.assertEqual(lifecycle_by_name["artifactRestoration"]["status"], "mutantSwitch")
        self.assertEqual((self.repo / "build-count.txt").read_text(), "bbbb")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        by_status = {mut["status"]: mut for mut in payload["mutants"]}
        self.assertIn("BUILD_ERROR", by_status)
        self.assertIn("SURVIVED", by_status)
        self.assertEqual(by_status["BUILD_ERROR"]["resultSource"], "compile-pruning")
        self.assertEqual(by_status["SURVIVED"]["resultSource"], "mutant-switch")
        self.assertTrue(by_status["BUILD_ERROR"]["run"]["compilePruning"]["pruned"])

    def test_config_rejects_unknown_execution_mode_value(self) -> None:
        config = self.repo / "stryker-cxx.yml"
        config.write_text(
            "schemaVersion: stryker-cxx.config.v1\n"
            "execution:\n"
            "  executionMode: speculative\n"
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
            "true",
            "--report",
            str(self.repo / "bad-execution-mode.json"),
            "--max-mutants",
            "1",
            "--quiet",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("--execution-mode must be one of", result.stdout)

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

    def test_run_mutant_can_activate_mutant_switch_selector(self) -> None:
        self.source.write_text("bool flag() { return true; }\nint main() { return flag() ? 0 : 1; }\n")
        self._git("add", "sample.cpp")
        self._git(
            "-c",
            "user.name=stryker-cxx",
            "-c",
            "user.email=stryker-cxx@example.invalid",
            "commit",
            "-q",
            "-m",
            "run mutant switch fixture",
        )
        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--mutators",
            "ReturnValue",
            "--max-mutants",
            "1",
        )
        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        listed_mutant = json.loads(listed.stdout)[0]
        report = self.repo / "one-mutant-switch.json"

        result = self._cli(
            "run-mutant",
            "--repo",
            str(self.repo),
            "--id",
            listed_mutant["id"],
            "--build-command",
            "printf build >> build-count.txt && c++ -std=c++17 sample.cpp -o sample",
            "--test-command",
            "printf test >> test-count.txt",
            "--report",
            str(report),
            "--mutators",
            "ReturnValue",
            "--execution-mode",
            "mutant-switch",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual((self.repo / "build-count.txt").read_text(), "build")
        self.assertEqual((self.repo / "test-count.txt").read_text(), "test")
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["executionMode"], "mutant-switch")
        self.assertEqual(payload["execution"]["singleCompile"]["builds"], 1)
        self.assertEqual(payload["totalMutants"], 1)
        self.assertEqual(payload["mutants"][0]["id"], listed_mutant["id"])
        self.assertEqual(payload["mutants"][0]["run"]["mutantSwitchGuardId"], listed_mutant["mutantSwitchGuardId"])
        self.assertEqual(
            payload["mutants"][0]["run"]["mutantSwitchActiveEnvironment"],
            "STRYKER_CXX_ACTIVE_MUTANT",
        )
        repro = payload["mutants"][0]["run"]["reproCommand"]
        self.assertIn("--mode token", repro)
        self.assertIn("--execution-mode mutant-switch", repro)

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
        payload = json.loads(report.read_text())
        self.assertEqual(len(payload["execution"]["reporterRuns"]), 1)
        self.assertEqual(payload["execution"]["reporterRuns"][0]["plugin"], "hook-plugin")
        self.assertEqual(payload["execution"]["reporterRuns"][0]["reporter"], "copy-json")
        self.assertEqual(payload["execution"]["reporterRuns"][0]["status"], "passed")
        self.assertEqual(payload["execution"]["reporterRuns"][0]["exitCode"], 0)

    def test_plugin_lifecycle_events_are_recorded_and_redacted(self) -> None:
        plugin = self.repo / "lifecycle-plugin.json"

        def hook_command(name: str, secret_prefix: bool = False) -> str:
            prefix = "API_KEY=supersecret " if secret_prefix else ""
            return f"{prefix}printf '{name}\\n' >> plugin-lifecycle.txt"

        plugin.write_text(json.dumps({
            "name": "lifecycle-plugin",
            "version": "0.1.0",
            "capabilities": {
                "hooks": True,
            },
            "hooks": {
                "initialization": hook_command("initialization", secret_prefix=True),
                "projectAnalysis": hook_command("projectAnalysis"),
                "mutationDiscovery": hook_command("mutationDiscovery"),
                "artifactCreation": hook_command("artifactCreation"),
                "coverageAnalysis": hook_command("coverageAnalysis"),
                "scheduling": hook_command("scheduling"),
                "execution": hook_command("execution"),
                "reporting": hook_command("reporting"),
                "cleanup": hook_command("cleanup"),
                "postRun": hook_command("postRun"),
            },
        }))
        report = self.repo / "lifecycle.json"

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
            "--plugin",
            str(plugin),
            "--max-mutants",
            "1",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(
            (self.repo / "plugin-lifecycle.txt").read_text().splitlines(),
            [
                "initialization",
                "projectAnalysis",
                "mutationDiscovery",
                "artifactCreation",
                "coverageAnalysis",
                "scheduling",
                "execution",
                "reporting",
                "cleanup",
                "postRun",
            ],
        )
        payload = json.loads(report.read_text())
        serialized = json.dumps(payload)
        self.assertNotIn("supersecret", serialized)
        self.assertIn("[REDACTED]", serialized)
        lifecycle = payload["execution"]["pluginLifecycle"]
        self.assertEqual(lifecycle["schemaVersion"], "stryker-cxx.plugin-lifecycle.v1")
        self.assertTrue(lifecycle["localOnly"])
        self.assertFalse(lifecycle["networkInstall"])
        self.assertEqual(lifecycle["loadOrder"], ["lifecycle-plugin"])
        self.assertIn("artifactCreation", lifecycle["supportedEvents"])
        registered = lifecycle["registeredHooks"]
        self.assertTrue(
            any(item["event"] == "cleanup" and item["hook"] == "postRun" for item in registered)
        )
        self.assertTrue(
            any("[REDACTED]" in " ".join(item.get("commands", [])) for item in registered)
        )
        run_phases = [item["phase"] for item in lifecycle["runs"]]
        self.assertEqual(
            run_phases,
            [
                "initialization",
                "projectAnalysis",
                "mutationDiscovery",
                "artifactCreation",
                "coverageAnalysis",
                "scheduling",
                "execution",
                "reporting",
                "cleanup",
                "cleanup",
            ],
        )
        self.assertTrue(
            all("STRYKER_CXX_PHASE" in item["environment"]["provided"] for item in lifecycle["runs"])
        )

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
        reporter_runs = payload["execution"].get("reporterRuns")
        self.assertEqual(len(reporter_runs), 1)
        self.assertEqual(reporter_runs[0]["reporter"], "copy-json")
        self.assertEqual(reporter_runs[0]["phase"], "reporting")
        self.assertEqual(reporter_runs[0]["status"], "passed")

    def test_missing_plugin_reporter_request_is_recorded_in_execution_payload(self) -> None:
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
        report = self.repo / "missing-reporter.json"

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
            "--reporter",
            "missing-json",
            "--dry-run-only",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["reporters"], ["copy-json", "missing-json"])
        reporter_runs = payload["execution"].get("reporterRuns")
        self.assertEqual(len(reporter_runs), 2)
        self.assertEqual(reporter_runs[0]["reporter"], "copy-json")
        self.assertEqual(reporter_runs[0]["status"], "passed")
        missing = reporter_runs[1]
        self.assertIsNone(missing["plugin"])
        self.assertEqual(missing["reporter"], "missing-json")
        self.assertEqual(missing["phase"], "reporting")
        self.assertIsNone(missing["command"])
        self.assertEqual(missing["status"], "notFound")
        self.assertIsNone(missing["exitCode"])
        self.assertEqual(missing["durationMs"], 0)
        self.assertEqual(missing["availableReporters"], ["copy-json"])
        self.assertIn("not provided", missing["reason"])

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

    def test_plugin_capability_version_is_validated_during_initialization(self) -> None:
        plugin = self.repo / "future-plugin.json"
        plugin.write_text(json.dumps({
            "name": "future-plugin",
            "version": "0.1.0",
            "capabilities": {
                "runner": {
                    "version": "2.0",
                    "name": "future-runner",
                    "buildCommand": "true",
                }
            },
        }))
        report = self.repo / "future-plugin.json.report"

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
            "--dry-run-only",
            "--quiet",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("unsupported capability version for runner: 2.0", result.stderr + result.stdout)

    def test_project_analysis_records_compile_database_source_ownership(self) -> None:
        self.source.write_text("int main() { return 1 == 1 ? 0 : 1; }\n")
        compile_db = [
            {
                "directory": str(self.repo),
                "command": "clang++ -std=c++17 -c sample.cpp -o sample.o",
                "file": str(self.source),
                "output": str(self.repo / "sample.o"),
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
            "compile database ownership",
        )
        report = self.repo / "project-analysis-ownership.json"

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
        compile_database = payload["projectAnalysis"]["compileDatabase"]
        self.assertTrue(compile_database["present"])
        self.assertEqual(compile_database["fileEntries"][0]["file"], "sample.cpp")
        self.assertEqual(compile_database["fileEntries"][0]["output"], "sample.o")
        source_target = payload["projectAnalysis"]["sourceTargets"][0]
        self.assertTrue(source_target["compileDatabaseMatched"])
        self.assertEqual(source_target["compileDirectory"], ".")
        self.assertEqual(source_target["compileCommand"], "clang++ -std=c++17 -c sample.cpp -o sample.o")
        self.assertEqual(source_target["compileOutput"], "sample.o")
        self.assertEqual(source_target["ownership"]["kind"], "compile-database-unit")
        self.assertEqual(source_target["ownership"]["confidence"], "high")

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
        reporter_runs = payload["execution"].get("reporterRuns")
        self.assertEqual(len(reporter_runs), 1)
        self.assertEqual(reporter_runs[0]["plugin"], "reporter-hook-fixture")
        self.assertEqual(reporter_runs[0]["reporter"], "copy-json")
        self.assertEqual(reporter_runs[0]["status"], "passed")

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
        plan = payload["execution"]["batching"]["plan"]
        self.assertEqual(len(plan), 2)
        self.assertEqual([item["batchIndex"] for item in plan], [1, 2])
        self.assertEqual(sorted(sorted(loc["line"] for loc in item["locations"]) for item in plan), [[1, 3], [2, 4]])
        self.assertTrue(all(item["heuristic"] == "first-fit non-overlap" for item in plan))
        placements = [placement for item in plan for placement in item["placement"]]
        self.assertIn({"placement": "seed", "placementReasons": []}, placements)
        self.assertIn(
            {"placement": "new-batch", "placementReasons": ["same-file adjacent-line isolation"]},
            placements,
        )
        self.assertEqual(
            sum(1 for placement in placements if placement["placement"] == "joined-existing-batch"),
            2,
        )
        self.assertEqual([m["resultSource"] for m in payload["mutants"]], ["batch", "batch", "batch", "batch"])
        lines_by_batch: dict[str, list[int]] = {}
        for mut in payload["mutants"]:
            lines_by_batch.setdefault(mut["run"]["batchId"], []).append(mut["line"])
        self.assertEqual(sorted(sorted(lines) for lines in lines_by_batch.values()), [[1, 3], [2, 4]])
        self.assertEqual({item["batchId"] for item in plan}, set(lines_by_batch))

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

    def test_compiled_executable_backend_supports_make_root_executable(self) -> None:
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
        (self.repo / "Makefile").write_text(
            "CXX ?= c++\n"
            "CXXFLAGS ?= -std=c++17 -Wall -Wextra\n"
            "sample: sample.cpp\n"
            "\t$(CXX) $(CXXFLAGS) $< -o $@\n"
        )
        self._git("add", "sample.cpp", "Makefile")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "compiled-make-backend")
        subprocess.run(["make", "-C", str(self.repo), "sample"], cwd=self.repo, check=True, text=True, capture_output=True)
        original_source = self.source.read_text()
        executable = self.repo / "sample"
        original_executable_hash = _sha256(executable)
        report = self.repo / "compiled-make-executable.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-system",
            "make",
            "--build-target",
            "sample",
            "--test-binary",
            "sample",
            "--test-command",
            "./sample",
            "--artifact-backend",
            "compiled-executable",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "EqualityOperator",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original_source)
        self.assertEqual(_sha256(executable), original_executable_hash)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["killed"], 1)
        self.assertEqual(payload["mutationArtifact"]["backend"], "compiled-executable")
        compiled = payload["compiledArtifacts"][0]
        self.assertEqual(compiled["backend"], "compiled-executable")
        self.assertEqual(compiled["target"], "sample")
        self.assertEqual(compiled["scratchBuildDir"], compiled["scratchRepo"])
        self.assertTrue(compiled["originalRestored"])
        run = payload["mutants"][0]["run"]
        self.assertEqual(run["configureCommand"], None)
        self.assertIn("make -C", run["buildCommand"])
        self.assertEqual(run["artifactBackend"], "compiled-executable")

    @unittest.skipIf(shutil.which("meson") is None, "meson not installed")
    def test_compiled_executable_backend_supports_meson_executable(self) -> None:
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
        (self.repo / "meson.build").write_text(
            "project('stryker-cxx-compiled-meson-fixture', 'cpp', default_options: ['cpp_std=c++17'])\n"
            "sample = executable('sample', 'sample.cpp')\n"
            "test('sample', sample)\n"
        )
        self._git("add", "sample.cpp", "meson.build")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "compiled-meson-backend")
        subprocess.run(["meson", "setup", "build", "."], cwd=self.repo, check=True, text=True, capture_output=True)
        subprocess.run(["meson", "compile", "-C", "build", "sample"], cwd=self.repo, check=True, text=True, capture_output=True)
        original_source = self.source.read_text()
        executable = self.repo / "build" / "sample"
        original_executable_hash = _sha256(executable)
        report = self.repo / "compiled-meson-executable.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-system",
            "meson",
            "--build-dir",
            "build",
            "--build-target",
            "sample",
            "--test-binary",
            "build/sample",
            "--test-command",
            "meson test -C build",
            "--artifact-backend",
            "compiled-executable",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "EqualityOperator",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original_source)
        self.assertEqual(_sha256(executable), original_executable_hash)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["killed"], 1)
        self.assertEqual(payload["mutationArtifact"]["backend"], "compiled-executable")
        compiled = payload["compiledArtifacts"][0]
        self.assertEqual(compiled["backend"], "compiled-executable")
        self.assertEqual(compiled["target"], "sample")
        self.assertTrue(compiled["originalRestored"])
        run = payload["mutants"][0]["run"]
        self.assertIn("meson setup", run["configureCommand"])
        self.assertIn("meson compile -C", run["buildCommand"])
        self.assertEqual(run["artifactBackend"], "compiled-executable")

    @unittest.skipIf(shutil.which("bazel") is None, "bazel not installed")
    def test_compiled_executable_backend_supports_bazel_executable(self) -> None:
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
        (self.repo / "WORKSPACE.bazel").write_text("")
        (self.repo / "BUILD.bazel").write_text(
            "cc_binary(\n"
            "    name = 'sample',\n"
            "    srcs = ['sample.cpp'],\n"
            ")\n"
        )
        self._git("add", "sample.cpp", "WORKSPACE.bazel", "BUILD.bazel")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "compiled-bazel-backend")
        subprocess.run(["bazel", "build", "//:sample"], cwd=self.repo, check=True, text=True, capture_output=True)
        original_source = self.source.read_text()
        executable = self.repo / "bazel-bin" / "sample"
        original_executable_hash = _sha256(executable)
        report = self.repo / "compiled-bazel-executable.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-system",
            "bazel",
            "--build-target",
            "//:sample",
            "--test-binary",
            "bazel-bin/sample",
            "--test-command",
            "./bazel-bin/sample",
            "--artifact-backend",
            "compiled-executable",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "EqualityOperator",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original_source)
        self.assertEqual(_sha256(executable), original_executable_hash)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["killed"], 1)
        self.assertEqual(payload["mutationArtifact"]["backend"], "compiled-executable")
        compiled = payload["compiledArtifacts"][0]
        self.assertEqual(compiled["backend"], "compiled-executable")
        self.assertEqual(compiled["target"], "//:sample")
        self.assertEqual(compiled["scratchBuildDir"], compiled["scratchRepo"])
        self.assertTrue(compiled["originalRestored"])
        run = payload["mutants"][0]["run"]
        self.assertEqual(run["configureCommand"], None)
        self.assertIn("bazel build", run["buildCommand"])
        self.assertEqual(run["artifactBackend"], "compiled-executable")

    def test_compiled_executable_backend_supports_xcodebuild_executable_with_explicit_artifact(self) -> None:
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
        fake_bin = self.repo / "fake-bin"
        fake_bin.mkdir()
        fake_xcodebuild = fake_bin / "xcodebuild"
        fake_xcodebuild.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "out=\"\"\n"
            "for arg in \"$@\"; do\n"
            "  case \"$arg\" in\n"
            "    CONFIGURATION_BUILD_DIR=*) out=\"${arg#CONFIGURATION_BUILD_DIR=}\" ;;\n"
            "  esac\n"
            "done\n"
            "test -n \"$out\"\n"
            "mkdir -p \"$out\"\n"
            "c++ -std=c++17 sample.cpp -o \"$out/sample\"\n"
        )
        fake_xcodebuild.chmod(0o755)
        (self.repo / "build").mkdir()
        subprocess.run(["c++", "-std=c++17", "sample.cpp", "-o", "build/sample"], cwd=self.repo, check=True, text=True, capture_output=True)
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "compiled-xcodebuild-backend")
        original_source = self.source.read_text()
        executable = self.repo / "build" / "sample"
        original_executable_hash = _sha256(executable)
        report = self.repo / "compiled-xcodebuild-executable.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-system",
            "xcodebuild",
            "--build-target",
            "sample",
            "--xcode-configuration",
            "Debug",
            "--test-binary",
            "build/sample",
            "--test-command",
            "./build/sample",
            "--artifact-backend",
            "compiled-executable",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--mutators",
            "EqualityOperator",
            "--env",
            f"PATH={fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original_source)
        self.assertEqual(_sha256(executable), original_executable_hash)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["killed"], 1)
        self.assertEqual(payload["mutationArtifact"]["backend"], "compiled-executable")
        compiled = payload["compiledArtifacts"][0]
        self.assertEqual(compiled["backend"], "compiled-executable")
        self.assertEqual(compiled["target"], "sample")
        self.assertTrue(compiled["originalRestored"])
        run = payload["mutants"][0]["run"]
        self.assertEqual(run["configureCommand"], None)
        self.assertIn("xcodebuild build", run["buildCommand"])
        self.assertIn("CONFIGURATION_BUILD_DIR=", run["buildCommand"])
        self.assertEqual(run["artifactBackend"], "compiled-executable")

    def test_compiled_backend_rejects_unsupported_adapter_in_preflight(self) -> None:
        report = self.repo / "compiled-unsupported.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-system",
            "xcodebuild",
            "--build-target",
            "sample",
            "--build-command",
            "true",
            "--test-command",
            "true",
            "--artifact-backend",
            "compiled-library",
            "--report",
            str(report),
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertIn("requires --build-system cmake/ctest/make/ninja/meson", result.stderr)

    def test_compiled_backend_rejects_missing_build_system_in_preflight(self) -> None:
        report = self.repo / "compiled-missing-build-system.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-target",
            "sample",
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
        self.assertIn(
            "requires explicit --build-system cmake/ctest/make/ninja/meson/bazel/xcodebuild",
            result.stderr,
        )

    def test_compiled_backend_falls_back_to_source_overlay_when_requested(self) -> None:
        self.source.write_text("int value() { return 1 == 1; }\n")
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "compiled-fallback")
        original_source = self.source.read_text()
        report = self.repo / "compiled-fallback.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-system",
            "xcodebuild",
            "--build-target",
            "sample",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--artifact-backend",
            "compiled-library",
            "--artifact-fallback",
            "source-overlay",
            "--mutators",
            "EqualityOperator",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--threshold-break",
            "0",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original_source)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["requestedArtifactBackend"], "compiled-library")
        self.assertEqual(payload["execution"]["artifactBackend"], "source-overlay")
        self.assertIn("--build-system xcodebuild", payload["execution"]["artifactFallbackReason"])
        self.assertEqual(payload["mutationArtifact"]["mode"], "source-overlay")
        self.assertEqual(payload["killed"], 1)

    def test_compiled_backend_with_missing_build_system_can_explicitly_fallback(self) -> None:
        self.source.write_text("int value() { return 1 == 1; }\n")
        self._git("add", "sample.cpp")
        self._git(
            "-c",
            "user.name=stryker-cxx",
            "-c",
            "user.email=stryker-cxx@example.invalid",
            "commit",
            "-q",
            "-m",
            "compiled-missing-build-system-fallback",
        )
        original_source = self.source.read_text()
        report = self.repo / "compiled-missing-build-system-fallback.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-target",
            "sample",
            "--build-command",
            "true",
            "--test-command",
            "false",
            "--artifact-backend",
            "compiled-executable",
            "--artifact-fallback",
            "source-overlay",
            "--mutators",
            "EqualityOperator",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--threshold-break",
            "0",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original_source)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["execution"]["requestedArtifactBackend"], "compiled-executable")
        self.assertEqual(payload["execution"]["artifactBackend"], "source-overlay")
        self.assertIn("requires explicit --build-system", payload["execution"]["artifactFallbackReason"])
        self.assertEqual(payload["mutationArtifact"]["mode"], "source-overlay")
        self.assertEqual(payload["killed"], 1)

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

    def test_compiled_library_backend_supports_make_with_explicit_library_artifact(self) -> None:
        if os.name == "nt":
            self.skipTest("fake make executable fixture uses POSIX shebang lookup")
        self.source.write_text(
            "int value(int input) {\n"
            "  if (input == 1) {\n"
            "    return 2;\n"
            "  }\n"
            "  return 0;\n"
            "}\n"
        )
        fake_bin = self.repo / "fake-bin"
        fake_bin.mkdir()
        fake_make = fake_bin / "make"
        fake_make.write_text(
            f"#!{sys.executable}\n"
            "from pathlib import Path\n"
            "import sys\n"
            "cwd = Path.cwd()\n"
            "args = sys.argv[1:]\n"
            "if '-C' in args:\n"
            "    cwd = Path(args[args.index('-C') + 1])\n"
            "source = cwd / 'sample.cpp'\n"
            "artifact = cwd / 'libmathlib.a'\n"
            "artifact.write_text(source.read_text(), encoding='utf-8')\n"
        )
        fake_make.chmod(0o755)
        test_script = self.repo / "check_library.py"
        test_script.write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "text = Path('libmathlib.a').read_text(encoding='utf-8')\n"
            "sys.exit(1 if '!=' in text else 0)\n"
        )
        (self.repo / "libmathlib.a").write_text(self.source.read_text(), encoding="utf-8")
        self._git("add", "sample.cpp", "libmathlib.a")
        self._git(
            "-c",
            "user.name=stryker-cxx",
            "-c",
            "user.email=stryker-cxx@example.invalid",
            "commit",
            "-q",
            "-m",
            "compiled-library-make-backend",
        )
        original_source = self.source.read_text()
        library = self.repo / "libmathlib.a"
        original_library_hash = _sha256(library)
        report = self.repo / "compiled-library-make.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-system",
            "make",
            "--build-dir",
            ".",
            "--build-target",
            "mathlib",
            "--test-command",
            f"{sys.executable} {test_script}",
            "--artifact-backend",
            "compiled-library",
            "--mutators",
            "EqualityOperator",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--skip-initial-test",
            "--env",
            f"PATH={fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original_source)
        self.assertEqual(_sha256(library), original_library_hash)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["killed"], 1)
        self.assertEqual(payload["mutationArtifact"]["backend"], "compiled-library")
        self.assertEqual(payload["mutationArtifact"]["compiledArtifacts"]["kinds"], ["library"])
        compiled = payload["compiledArtifacts"][0]
        self.assertEqual(compiled["backend"], "compiled-library")
        self.assertEqual(compiled["kind"], "library")
        self.assertEqual(compiled["target"], "mathlib")
        self.assertEqual(compiled["originalHashBefore"], original_library_hash)
        self.assertEqual(compiled["originalHashAfter"], original_library_hash)
        run = payload["mutants"][0]["run"]
        self.assertIn("make -C", run["buildCommand"])
        self.assertEqual(run["artifactBackend"], "compiled-library")
        self.assertTrue(run["artifactPlacement"]["originalArtifactsRestored"])

    def test_compiled_library_backend_supports_bazel_with_explicit_artifact_path(self) -> None:
        if os.name == "nt":
            self.skipTest("fake bazel executable fixture uses POSIX shebang lookup")
        self.source.write_text(
            "int value(int input) {\n"
            "  if (input == 1) {\n"
            "    return 2;\n"
            "  }\n"
            "  return 0;\n"
            "}\n"
        )
        fake_bin = self.repo / "fake-bin"
        fake_bin.mkdir()
        fake_bazel = fake_bin / "bazel"
        fake_bazel.write_text(
            f"#!{sys.executable}\n"
            "from pathlib import Path\n"
            "import sys\n"
            "if sys.argv[1:3] != ['build', '//lib:mathlib']:\n"
            "    sys.exit(2)\n"
            "artifact = Path('bazel-bin/lib/libmathlib.a')\n"
            "artifact.parent.mkdir(parents=True, exist_ok=True)\n"
            "artifact.write_text(Path('sample.cpp').read_text(encoding='utf-8'), encoding='utf-8')\n"
        )
        fake_bazel.chmod(0o755)
        artifact = self.repo / "bazel-bin" / "lib" / "libmathlib.a"
        artifact.parent.mkdir(parents=True)
        artifact.write_text(self.source.read_text(), encoding="utf-8")
        test_script = self.repo / "check_bazel_library.py"
        test_script.write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "text = Path('bazel-bin/lib/libmathlib.a').read_text(encoding='utf-8')\n"
            "sys.exit(1 if '!=' in text else 0)\n"
        )
        self._git("add", "sample.cpp", "bazel-bin/lib/libmathlib.a")
        self._git(
            "-c",
            "user.name=stryker-cxx",
            "-c",
            "user.email=stryker-cxx@example.invalid",
            "commit",
            "-q",
            "-m",
            "compiled-library-bazel-backend",
        )
        original_source = self.source.read_text()
        original_library_hash = _sha256(artifact)
        report = self.repo / "compiled-library-bazel.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-system",
            "bazel",
            "--build-target",
            "//lib:mathlib",
            "--artifact-path",
            "bazel-bin/lib/libmathlib.a",
            "--test-command",
            f"{sys.executable} {test_script}",
            "--artifact-backend",
            "compiled-library",
            "--mutators",
            "EqualityOperator",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--skip-initial-test",
            "--env",
            f"PATH={fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original_source)
        self.assertEqual(_sha256(artifact), original_library_hash)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["killed"], 1)
        self.assertEqual(payload["execution"]["artifactPath"], "bazel-bin/lib/libmathlib.a")
        compiled = payload["compiledArtifacts"][0]
        self.assertEqual(compiled["backend"], "compiled-library")
        self.assertEqual(compiled["kind"], "library")
        self.assertEqual(compiled["target"], "//lib:mathlib")
        self.assertEqual(compiled["originalHashBefore"], original_library_hash)
        self.assertEqual(compiled["originalHashAfter"], original_library_hash)
        run = payload["mutants"][0]["run"]
        self.assertIn("bazel build //lib:mathlib", run["buildCommand"])
        self.assertEqual(run["artifactBackend"], "compiled-library")
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

    def test_compiled_object_backend_supports_make_with_explicit_artifact_path_and_compile_database(self) -> None:
        if os.name == "nt":
            self.skipTest("fake make executable fixture uses POSIX shebang lookup")
        self.source.write_text(
            "int value(int input) {\n"
            "  if (input == 1) {\n"
            "    return 2;\n"
            "  }\n"
            "  return 0;\n"
            "}\n"
        )
        fake_bin = self.repo / "fake-bin"
        fake_bin.mkdir()
        fake_make = fake_bin / "make"
        fake_make.write_text(
            f"#!{sys.executable}\n"
            "from pathlib import Path\n"
            "import json\n"
            "import sys\n"
            "cwd = Path.cwd()\n"
            "args = sys.argv[1:]\n"
            "if '-C' in args:\n"
            "    cwd = Path(args[args.index('-C') + 1])\n"
            "if args and args[-1] != 'sample_test':\n"
            "    sys.exit(2)\n"
            "source = cwd / 'sample.cpp'\n"
            "object_file = cwd / 'sample.o'\n"
            "linked = cwd / 'sample_test'\n"
            "object_file.write_text(source.read_text(encoding='utf-8'), encoding='utf-8')\n"
            "linked.write_text(source.read_text(encoding='utf-8'), encoding='utf-8')\n"
            "compile_db = [{\n"
            "    'directory': str(cwd),\n"
            "    'file': str(source),\n"
            "    'command': 'c++ -c sample.cpp -o sample.o',\n"
            "}]\n"
            "(cwd / 'compile_commands.json').write_text(json.dumps(compile_db), encoding='utf-8')\n"
        )
        fake_make.chmod(0o755)
        linked = self.repo / "sample_test"
        linked.write_text(self.source.read_text(), encoding="utf-8")
        test_script = self.repo / "check_object.py"
        test_script.write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "text = Path('sample_test').read_text(encoding='utf-8')\n"
            "sys.exit(1 if '!=' in text else 0)\n"
        )
        self._git("add", "sample.cpp", "sample_test")
        self._git(
            "-c",
            "user.name=stryker-cxx",
            "-c",
            "user.email=stryker-cxx@example.invalid",
            "commit",
            "-q",
            "-m",
            "compiled-object-make-backend",
        )
        original_source = self.source.read_text()
        original_linked_hash = _sha256(linked)
        report = self.repo / "compiled-object-make.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-system",
            "make",
            "--build-dir",
            ".",
            "--build-target",
            "sample_test",
            "--artifact-path",
            "sample_test",
            "--test-command",
            f"{sys.executable} {test_script}",
            "--artifact-backend",
            "compiled-object",
            "--mutators",
            "EqualityOperator",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--skip-initial-test",
            "--env",
            f"PATH={fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original_source)
        self.assertEqual(_sha256(linked), original_linked_hash)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["killed"], 1)
        self.assertEqual(payload["execution"]["artifactPath"], "sample_test")
        compiled = payload["compiledArtifacts"][0]
        self.assertEqual(compiled["backend"], "compiled-object")
        self.assertEqual(compiled["kind"], "object")
        self.assertEqual(compiled["target"], "sample_test")
        self.assertEqual(compiled["originalHashBefore"], original_linked_hash)
        self.assertEqual(compiled["originalHashAfter"], original_linked_hash)
        obj = compiled["objectArtifacts"][0]
        self.assertTrue(obj["compileCommandFound"])
        self.assertTrue(obj["objectProduced"])
        self.assertTrue(obj["objectArtifact"].endswith("sample.o"))
        self.assertIsInstance(obj["objectHash"], str)
        run = payload["mutants"][0]["run"]
        self.assertIn("make -C", run["buildCommand"])
        self.assertEqual(run["artifactBackend"], "compiled-object")
        self.assertTrue(run["artifactPlacement"]["originalArtifactsRestored"])

    def test_compiled_object_backend_supports_bazel_with_explicit_artifact_path_and_compile_database(self) -> None:
        if os.name == "nt":
            self.skipTest("fake bazel executable fixture uses POSIX shebang lookup")
        self.source.write_text(
            "int value(int input) {\n"
            "  if (input == 1) {\n"
            "    return 2;\n"
            "  }\n"
            "  return 0;\n"
            "}\n"
        )
        fake_bin = self.repo / "fake-bin"
        fake_bin.mkdir()
        fake_bazel = fake_bin / "bazel"
        fake_bazel.write_text(
            f"#!{sys.executable}\n"
            "from pathlib import Path\n"
            "import json\n"
            "import sys\n"
            "cwd = Path.cwd()\n"
            "args = sys.argv[1:]\n"
            "if args != ['build', '//:sample_test']:\n"
            "    sys.exit(2)\n"
            "source = cwd / 'sample.cpp'\n"
            "object_file = cwd / 'sample.o'\n"
            "linked = cwd / 'bazel-bin' / 'sample_test'\n"
            "linked.parent.mkdir(parents=True, exist_ok=True)\n"
            "object_file.write_text(source.read_text(encoding='utf-8'), encoding='utf-8')\n"
            "linked.write_text(source.read_text(encoding='utf-8'), encoding='utf-8')\n"
            "compile_db = [{\n"
            "    'directory': str(cwd),\n"
            "    'file': str(source),\n"
            "    'command': 'c++ -c sample.cpp -o sample.o',\n"
            "}]\n"
            "(cwd / 'compile_commands.json').write_text(json.dumps(compile_db), encoding='utf-8')\n"
        )
        fake_bazel.chmod(0o755)
        linked_dir = self.repo / "bazel-bin"
        linked_dir.mkdir()
        linked = linked_dir / "sample_test"
        linked.write_text(self.source.read_text(), encoding="utf-8")
        test_script = self.repo / "check_bazel_object.py"
        test_script.write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "text = Path('bazel-bin/sample_test').read_text(encoding='utf-8')\n"
            "sys.exit(1 if '!=' in text else 0)\n"
        )
        (self.repo / "WORKSPACE.bazel").write_text("workspace(name = 'compiled_object_bazel_fixture')\n")
        (self.repo / "BUILD.bazel").write_text(
            "cc_binary(\n"
            "    name = 'sample_test',\n"
            "    srcs = ['sample.cpp'],\n"
            ")\n"
        )
        self._git("add", "sample.cpp", "bazel-bin/sample_test", "WORKSPACE.bazel", "BUILD.bazel")
        self._git(
            "-c",
            "user.name=stryker-cxx",
            "-c",
            "user.email=stryker-cxx@example.invalid",
            "commit",
            "-q",
            "-m",
            "compiled-object-bazel-backend",
        )
        original_source = self.source.read_text()
        original_linked_hash = _sha256(linked)
        report = self.repo / "compiled-object-bazel.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-system",
            "bazel",
            "--build-target",
            "//:sample_test",
            "--artifact-path",
            "bazel-bin/sample_test",
            "--test-command",
            f"{sys.executable} {test_script}",
            "--artifact-backend",
            "compiled-object",
            "--mutators",
            "EqualityOperator",
            "--report",
            str(report),
            "--max-mutants",
            "1",
            "--skip-initial-test",
            "--env",
            f"PATH={fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original_source)
        self.assertEqual(_sha256(linked), original_linked_hash)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["killed"], 1)
        self.assertEqual(payload["execution"]["artifactPath"], "bazel-bin/sample_test")
        compiled = payload["compiledArtifacts"][0]
        self.assertEqual(compiled["backend"], "compiled-object")
        self.assertEqual(compiled["kind"], "object")
        self.assertEqual(compiled["target"], "//:sample_test")
        self.assertEqual(compiled["originalHashBefore"], original_linked_hash)
        self.assertEqual(compiled["originalHashAfter"], original_linked_hash)
        obj = compiled["objectArtifacts"][0]
        self.assertTrue(obj["compileCommandFound"])
        self.assertTrue(obj["objectProduced"])
        self.assertTrue(obj["objectArtifact"].endswith("sample.o"))
        self.assertIsInstance(obj["objectHash"], str)
        run = payload["mutants"][0]["run"]
        self.assertIn("bazel build //:sample_test", run["buildCommand"])
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
            "bool feature_c() {\n"
            "  return true;\n"
            "}\n"
            "\n"
            "bool feature_d() {\n"
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
            "--jobs",
            "2",
            "--mutators",
            "BooleanLiteral",
            "--report",
            str(report),
            "--max-mutants",
            "4",
            "--threshold-break",
            "0",
            "--skip-initial-test",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.source.read_text(), original_source)
        self.assertEqual(_sha256(executable), original_executable_hash)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["survived"], 4)
        self.assertEqual(payload["execution"]["batching"]["batches"], 2)
        self.assertEqual(payload["execution"]["batching"]["batchedMutants"], 4)
        self.assertEqual(payload["execution"]["batching"]["parallelWorkers"], 2)
        self.assertEqual(payload["execution"]["batching"]["splitBatches"], 0)
        self.assertEqual([mut["resultSource"] for mut in payload["mutants"]], ["batch", "batch", "batch", "batch"])
        for mut in payload["mutants"]:
            run = mut["run"]
            self.assertEqual(run["artifactBackend"], "compiled-executable")
            self.assertEqual(run["worktreeMode"], "compiled-artifact")
            self.assertEqual(run["scheduler"]["sessionType"], "batch")
            self.assertEqual(run["compiledArtifact"]["backend"], "compiled-executable")
            self.assertIn("artifactPlacementLock", run)
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
            "Iter redundant_range(Iter first, int value) { return std::lower_bound(first, first, value); }\n"
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
        self.assertEqual(payload["totalMutants"], 6)
        self.assertEqual(payload["ignored"], 6)
        self.assertEqual(payload["killed"], 0)
        reasons = {mut["ignoreReason"] for mut in payload["mutants"]}
        self.assertIn("equivalent duplicate logical operand", reasons)
        self.assertIn("equivalent arithmetic identity", reasons)
        self.assertIn("equivalent duplicate bitwise operand", reasons)
        self.assertIn("equivalent duplicate standard-library operands", reasons)
        self.assertIn("equivalent duplicate standard-library range", reasons)
        self.assertIn("equivalent duplicate conditional branches", reasons)
        rule_ids = {mut["run"]["suppressionRule"] for mut in payload["mutants"]}
        self.assertEqual(
            rule_ids,
            {
                "duplicate-logical-operand",
                "arithmetic-identity",
                "duplicate-bitwise-operand",
                "duplicate-standard-library-operands",
                "duplicate-standard-library-range",
                "duplicate-conditional-branches",
            },
        )
        suppression = payload["execution"]["analysis"]["equivalentSuppression"]
        self.assertEqual(suppression["mode"], "conservative")
        self.assertEqual(suppression["suppressedMutants"], 6)
        self.assertEqual({item["ruleId"] for item in suppression["suppressions"]}, rule_ids)

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
        for mut in payload:
            self.assertEqual(mut["rewriteStrategy"], "token-statement-removal")
            self.assertEqual(mut["sourceRange"]["kind"], "TOKEN_STATEMENT")
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
        self.assertEqual(payload[0]["rewriteStrategy"], "token-block-removal")
        self.assertEqual(payload[0]["sourceRange"]["kind"], "TOKEN_BLOCK")

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
            "  a <<= 1;\n"
            "  b >>= 2;\n"
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
        self.assertIn(("<<=", ">>="), pairs)
        self.assertIn((">>=", "<<="), pairs)

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
        self.assertIn(("++i", "--i"), pairs)
        self.assertIn(("i++", "i--"), pairs)
        self.assertIn(("--i", "++i"), pairs)
        self.assertIn(("--x", "++x"), pairs)
        for mut in payload:
            self.assertEqual(mut["rewriteStrategy"], "token-update-expression")
            self.assertEqual(mut["sourceRange"]["kind"], "TOKEN_UPDATE_EXPRESSION")

    def test_modulo_and_bitwise_assignment_mutators_discover_candidates(self) -> None:
        self.source.write_text(
            "int modulo(int x, int y) {\n"
            "  int r = x % y;\n"
            "  r %= 3;\n"
            "  r &= y;\n"
            "  r |= 1;\n"
            "  r ^= 2;\n"
            "  return r;\n"
            "}\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "modulo-mutator-fixture")

        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--mutators",
            "ArithmeticOperator,AssignmentOperator",
        )

        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        payload = json.loads(listed.stdout)
        pairs = {(mut["mutator"], mut["original"], mut["mutated"]) for mut in payload}
        self.assertIn(("ArithmeticOperator", "%", "*"), pairs)
        self.assertIn(("AssignmentOperator", "%=", "*="), pairs)
        self.assertIn(("AssignmentOperator", "&=", "|="), pairs)
        self.assertIn(("AssignmentOperator", "|=", "&="), pairs)
        self.assertIn(("AssignmentOperator", "^=", "|="), pairs)
        bitwise_pairs = {(mut["original"], mut["mutated"]) for mut in payload if mut["mutator"] == "BitwiseOperator"}
        self.assertNotIn(("&", "|"), bitwise_pairs)
        self.assertNotIn(("|", "&"), bitwise_pairs)
        self.assertNotIn(("^", "|"), bitwise_pairs)

    def test_unary_operator_discovers_sign_candidates_in_conservative_contexts(self) -> None:
        self.source.write_text(
            "int sign(int x, int y) {\n"
            "  int a = -x;\n"
            "  int b = (+y);\n"
            "  int c = x - y;\n"
            "  return -a;\n"
            "}\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "unary-sign-fixture")

        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--mutators",
            "UnaryOperator",
        )

        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        payload = json.loads(listed.stdout)
        pairs = {(mut["original"], mut["mutated"], mut["rewriteStrategy"]) for mut in payload}
        self.assertIn(("-", "+", "token-unary-sign"), pairs)
        self.assertIn(("+", "-", "token-unary-sign"), pairs)
        ranged_signs = [mut for mut in payload if mut["rewriteStrategy"] == "token-unary-sign"]
        self.assertTrue(ranged_signs)
        for mut in ranged_signs:
            self.assertEqual(mut["sourceRange"]["kind"], "TOKEN_UNARY_EXPRESSION")
        self.assertFalse(any(mut["line"] == 4 for mut in payload), payload)

    def test_unary_operator_discovers_logical_not_expression_spans(self) -> None:
        self.source.write_text(
            "bool ready(bool flag) {\n"
            "  return !flag;\n"
            "}\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "logical-not-fixture")

        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--mutators",
            "UnaryOperator",
        )

        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        payload = json.loads(listed.stdout)
        logical = [mut for mut in payload if mut["original"] == "!"]
        self.assertEqual(len(logical), 2)
        self.assertEqual({mut["mutated"] for mut in logical}, {"", "!!"})
        for mut in logical:
            self.assertEqual(mut["rewriteStrategy"], "token-unary-expression")
            self.assertEqual(mut["sourceRange"]["kind"], "TOKEN_UNARY_EXPRESSION")

    def test_bitwise_operator_discovers_xor_alternatives(self) -> None:
        self.source.write_text(
            "int mask(int x, int y) {\n"
            "  return x ^ y;\n"
            "}\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "bitwise-xor-fixture")

        listed = self._cli(
            "list-mutants",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--mutators",
            "BitwiseOperator",
        )

        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        payload = json.loads(listed.stdout)
        pairs = {(mut["original"], mut["mutated"]) for mut in payload}
        self.assertIn(("^", "|"), pairs)
        self.assertIn(("^", "&"), pairs)

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
        for mut in payload:
            self.assertEqual(mut["rewriteStrategy"], "token-loop-boundary")
            self.assertEqual(mut["sourceRange"]["kind"], "TOKEN_LOOP_CONDITION")

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
        for mut in payload:
            self.assertEqual(mut["rewriteStrategy"], "token-loop-condition")
            self.assertEqual(mut["sourceRange"]["kind"], "TOKEN_LOOP_CONDITION")

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
            "  auto moved = std::move(node);\n"
            "  auto forwarded = std::forward<Node>(node);\n"
            "  auto first_value = values.front();\n"
            "  auto last_value = values.back();\n"
            "  bool none = values.empty();\n"
            "  auto count = values.size();\n"
            "  auto cap = values.capacity();\n"
            "  auto pos = label.find(\"x\");\n"
            "  auto rpos = label.rfind(\"x\");\n"
            "  bool prefix = label.starts_with(\"a\");\n"
            "  bool suffix = label.ends_with(\"z\");\n"
            "  double up = std::ceil(input);\n"
            "  double down = std::floor(input);\n"
            "  double rounded = std::round(input);\n"
            "  double truncated = std::trunc(input);\n"
            "  auto next_it = std::next(values.begin());\n"
            "  auto prev_it = std::prev(values.end());\n"
            "  auto floor_time = std::chrono::floor<std::chrono::seconds>(duration);\n"
            "  auto ceil_time = std::chrono::ceil<std::chrono::seconds>(duration);\n"
            "  bool full_match = std::regex_match(text, match, re);\n"
            "  bool partial_match = std::regex_search(text, match, re);\n"
            "  bool path_exists = std::filesystem::exists(path);\n"
            "  bool path_empty = std::filesystem::is_empty(path);\n"
            "  bool regular = std::filesystem::is_regular_file(path);\n"
            "  bool directory = std::filesystem::is_directory(path);\n"
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
            "  int updates = 0;\n"
            "  updates++;\n"
            "  ++updates;\n"
            "  updates--;\n"
            "  --updates;\n"
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
                "MoveSemantics",
                "ContainerCall",
                "ContainerStateCall",
                "StringCall",
                "MathCall",
                "IteratorCall",
                "ChronoCall",
                "RegexCall",
                "FilesystemCall",
                "MemoryOrder",
                "UpdateOperator",
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
                "MoveSemantics",
                "ContainerCall",
                "ContainerStateCall",
                "StringCall",
                "MathCall",
                "IteratorCall",
                "ChronoCall",
                "RegexCall",
                "FilesystemCall",
                "MemoryOrder",
                "UpdateOperator",
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
        self.assertIn(("StandardLibraryCall", "std::min(1, 2)", "std::max(1, 2)"), pairs)
        self.assertIn(("StandardLibraryCall", "std::max(1, 2)", "std::min(1, 2)"), pairs)
        self.assertIn(("StandardLibraryCall", "std::lower_bound(values.begin(), values.end(), 2)", "std::upper_bound(values.begin(), values.end(), 2)"), pairs)
        self.assertIn(("StandardLibraryCall", "std::upper_bound(values.begin(), values.end(), 2)", "std::lower_bound(values.begin(), values.end(), 2)"), pairs)
        self.assertIn(("StandardLibraryCall", "std::begin(values)", "std::end(values)"), pairs)
        self.assertIn(("StandardLibraryCall", "std::end(values)", "std::begin(values)"), pairs)
        self.assertIn(("StandardLibraryCall", "std::sort(values.begin(), values.end())", "std::stable_sort(values.begin(), values.end())"), pairs)
        self.assertIn(("StandardLibraryCall", "std::stable_sort(values.begin(), values.end())", "std::sort(values.begin(), values.end())"), pairs)
        self.assertIn(("StandardLibraryCall", "std::partition(values.begin(), values.end(), pred)", "std::stable_partition(values.begin(), values.end(), pred)"), pairs)
        self.assertIn(("StandardLibraryCall", "std::stable_partition(values.begin(), values.end(), pred)", "std::partition(values.begin(), values.end(), pred)"), pairs)
        self.assertIn(("StandardLibraryCall", "std::is_sorted(values.begin(), values.end())", "std::is_heap(values.begin(), values.end())"), pairs)
        self.assertIn(("StandardLibraryCall", "std::is_heap(values.begin(), values.end())", "std::is_sorted(values.begin(), values.end())"), pairs)
        self.assertIn(("UpdateOperator", "updates++", "updates--"), pairs)
        self.assertIn(("UpdateOperator", "++updates", "--updates"), pairs)
        self.assertIn(("UpdateOperator", "updates--", "updates++"), pairs)
        self.assertIn(("UpdateOperator", "--updates", "++updates"), pairs)
        self.assertIn(("MoveSemantics", "std::move(node)", "node"), pairs)
        self.assertIn(("MoveSemantics", "std::forward<Node>(node)", "node"), pairs)
        self.assertIn(("ContainerCall", "values.front()", "values.back()"), pairs)
        self.assertIn(("ContainerCall", "values.back()", "values.front()"), pairs)
        self.assertIn(("ContainerStateCall", "values.empty()", "values.size()"), pairs)
        self.assertIn(("ContainerStateCall", "values.size()", "values.empty()"), pairs)
        self.assertIn(("ContainerStateCall", "values.capacity()", "values.size()"), pairs)
        self.assertIn(("StringCall", 'label.find("x")', 'label.rfind("x")'), pairs)
        self.assertIn(("StringCall", 'label.rfind("x")', 'label.find("x")'), pairs)
        self.assertIn(("StringCall", 'label.starts_with("a")', 'label.ends_with("a")'), pairs)
        self.assertIn(("StringCall", 'label.ends_with("z")', 'label.starts_with("z")'), pairs)
        self.assertIn(("MathCall", "std::ceil(input)", "std::floor(input)"), pairs)
        self.assertIn(("MathCall", "std::floor(input)", "std::ceil(input)"), pairs)
        self.assertIn(("MathCall", "std::round(input)", "std::trunc(input)"), pairs)
        self.assertIn(("MathCall", "std::trunc(input)", "std::round(input)"), pairs)
        self.assertIn(("IteratorCall", "std::next(values.begin())", "std::prev(values.begin())"), pairs)
        self.assertIn(("IteratorCall", "std::prev(values.end())", "std::next(values.end())"), pairs)
        self.assertIn(("ChronoCall", "std::chrono::floor<std::chrono::seconds>(duration)", "std::chrono::ceil<std::chrono::seconds>(duration)"), pairs)
        self.assertIn(("ChronoCall", "std::chrono::ceil<std::chrono::seconds>(duration)", "std::chrono::floor<std::chrono::seconds>(duration)"), pairs)
        self.assertIn(("RegexCall", "std::regex_match(text, match, re)", "std::regex_search(text, match, re)"), pairs)
        self.assertIn(("RegexCall", "std::regex_search(text, match, re)", "std::regex_match(text, match, re)"), pairs)
        self.assertIn(("FilesystemCall", "std::filesystem::exists(path)", "std::filesystem::is_empty(path)"), pairs)
        self.assertIn(("FilesystemCall", "std::filesystem::is_empty(path)", "std::filesystem::exists(path)"), pairs)
        self.assertIn(("FilesystemCall", "std::filesystem::is_regular_file(path)", "std::filesystem::is_directory(path)"), pairs)
        self.assertIn(("FilesystemCall", "std::filesystem::is_directory(path)", "std::filesystem::is_regular_file(path)"), pairs)
        self.assertIn(("MemoryOrder", "std::memory_order_relaxed", "std::memory_order_seq_cst"), pairs)
        self.assertIn(("MemberAccessOperator", ".", "->"), pairs)
        self.assertIn(("MemberAccessOperator", "->", "."), pairs)
        self.assertIn(("PreprocessorGuard", "ifdef", "ifndef"), pairs)
        self.assertIn(("PreprocessorGuard", "1", "0"), pairs)
        self.assertIn(("ObjCBoolLiteral", "YES", "NO"), pairs)
        self.assertIn(("MetalThreadPosition", "thread_position_in_grid", "thread_position_in_threadgroup"), pairs)
        self.assertTrue(any(mut["mutator"] == "ExceptionHandling" and mut["mutated"] == "(void)0;" for mut in payload))
        self.assertTrue(any(mut["mutator"] == "ObjCMessageSend" and mut["mutated"] == "(void)0" for mut in payload))
        metadata = {
            (mut["mutator"], mut["original"]): (mut.get("rewriteStrategy"), mut.get("sourceRange", {}).get("kind"))
            for mut in payload
        }
        self.assertEqual(metadata[("MemberAccessOperator", ".")], ("token-member-access-operator", "TOKEN_MEMBER_ACCESS_OPERATOR"))
        self.assertEqual(metadata[("PreprocessorGuard", "ifdef")], ("token-preprocessor-guard", "TOKEN_PREPROCESSOR_GUARD"))
        self.assertEqual(metadata[("PreprocessorGuard", "1")], ("token-preprocessor-guard", "TOKEN_PREPROCESSOR_GUARD"))
        self.assertEqual(
            metadata[("MetalThreadPosition", "thread_position_in_grid")],
            ("token-metal-thread-position", "TOKEN_METAL_THREAD_POSITION"),
        )

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
        for mut in payload:
            self.assertEqual(mut["rewriteStrategy"], "token-metal-address-space")
            self.assertEqual(mut["sourceRange"]["kind"], "TOKEN_METAL_ADDRESS_SPACE")

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
        self.assertIn(("x ? 1 : 0", "x ? 0 : 1"), pairs)

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

    def test_clang_ast_mode_prefers_direct_binary_operator_range_when_available(self) -> None:
        try:
            from clang import cindex  # type: ignore
            cindex.Index.create()
        except Exception as exc:
            self.skipTest(f"optional libclang binding is unavailable: {exc}")

        self.source.write_text(
            "bool eq(int lhs, int rhs) { return lhs == rhs; }\n"
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
            "clang-ast-binary-fixture",
        )
        report = self.repo / "clang-ast-binary.json"

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
            "clang-ast",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["totalMutants"], 1)
        self.assertEqual(payload["mutants"][0]["original"], "==")
        self.assertEqual(payload["mutants"][0]["mutated"], "!=")
        self.assertEqual(payload["mutants"][0]["nodeKind"], "BINARY_OPERATOR")
        self.assertEqual(payload["mutants"][0]["rewriteStrategy"], "clang-ast-direct-binary")
        self.assertEqual(payload["mutants"][0]["sourceRange"]["kind"], "BINARY_OPERATOR")

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
        self.assertEqual(payload["mutants"][0]["original"].strip(), "x ? 1 : 0")
        self.assertEqual(payload["mutants"][0]["mutated"].strip(), "x ? 0 : 1")
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

    def test_clang_ast_mode_generates_direct_unary_operator_mutants_when_available(self) -> None:
        try:
            from clang import cindex  # type: ignore
            cindex.Index.create()
        except Exception as exc:
            self.skipTest(f"optional libclang binding is unavailable: {exc}")

        self.source.write_text("bool neg(bool flag) { return !flag; }\n")
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
            "clang-ast-unary-fixture",
        )
        report = self.repo / "clang-ast-unary.json"

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
            "UnaryOperator",
            "--mode",
            "clang-ast",
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        self.assertEqual(payload["totalMutants"], 2)
        self.assertEqual({mut["original"] for mut in payload["mutants"]}, {"!"})
        self.assertEqual({mut["mutated"] for mut in payload["mutants"]}, {"", "!!"})
        for mut in payload["mutants"]:
            self.assertEqual(mut["mutator"], "UnaryOperator")
            self.assertEqual(mut["nodeKind"], "UNARY_OPERATOR")
            self.assertEqual(mut["rewriteStrategy"], "clang-ast-direct-unary")
            self.assertEqual(mut["sourceRange"]["kind"], "UNARY_OPERATOR")

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

    def test_distribution_manifest_records_selected_shard(self) -> None:
        self.source.write_text(
            "int main() { if (1 == 1) return 0; if (2 == 2) return 0; return 1; }\n"
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
            "two-mutants-for-distribution",
        )
        report = self.repo / "distribution-report.json"
        manifest = self.repo / "distribution.json"

        result = self._cli(
            "run",
            "--repo",
            str(self.repo),
            "--files",
            "sample.cpp",
            "--build-command",
            "SECRET_TOKEN=topsecret true",
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
            "--worker-label",
            "dist-worker",
            "--distribution-manifest",
            str(manifest),
            "--quiet",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        manifest_payload = json.loads(manifest.read_text())
        self.assertEqual(manifest_payload["schemaVersion"], "stryker-cxx.distribution.v1")
        self.assertEqual(manifest_payload["toolVersion"], payload["toolVersion"])
        self.assertEqual(manifest_payload["shard"]["index"], 2)
        self.assertEqual(manifest_payload["shard"]["total"], 2)
        self.assertEqual(manifest_payload["shard"]["selectedMutants"], 1)
        self.assertEqual(manifest_payload["worker"]["label"], "dist-worker")
        self.assertEqual(manifest_payload["commands"]["build"], "SECRET_TOKEN=[REDACTED] true")
        self.assertEqual(manifest_payload["mutants"][0]["id"], payload["mutants"][0]["id"])
        self.assertEqual(payload["execution"]["distribution"]["selectedMutants"], 1)
        self.assertEqual(payload["execution"]["distribution"]["manifestPath"], str(manifest))

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
        native = json.loads(report.read_text())
        export = native["execution"]["dashboard"]["export"]
        self.assertTrue(export["enabled"])
        self.assertEqual(export["path"], str(dashboard))
        self.assertEqual(export["status"], "succeeded")
        self.assertGreater(export["bytes"], 0)
        self.assertIsNotNone(export["writtenAt"])

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
                        "1": ["MathTest.A", "MathTest.Shared"],
                        "2": ["MathTest.B", "MathTest.Shared"],
                        "3": ["MathTest.A", "MathTest.Shared"],
                        "4": ["MathTest.B", "MathTest.Shared"],
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
        placements = [placement for batch in payload["execution"]["batching"]["plan"] for placement in batch["placement"]]
        self.assertEqual(
            sum(
                1
                for placement in placements
                if "coverage-selected affinity" in placement["placementReasons"]
            ),
            2,
        )
        for group in payload["execution"]["testScheduler"]["groups"]:
            self.assertTrue(group["coverageSelected"])
            self.assertTrue(group["testCommand"].startswith("true "))
            self.assertEqual(len(group["selectedTests"]), 2)
        selected_groups = {
            tuple(group["selectedTests"])
            for group in payload["execution"]["testScheduler"]["groups"]
        }
        self.assertEqual(
            selected_groups,
            {
                ("MathTest.A", "MathTest.Shared"),
                ("MathTest.B", "MathTest.Shared"),
            },
        )
        for mut in payload["mutants"]:
            self.assertEqual(mut["resultSource"], "batch")
            self.assertEqual(mut["run"]["coverageStatus"], "covered")
            self.assertTrue(mut["run"]["scheduler"]["coverageSelected"])

    def test_batched_coverage_selection_minimizes_selected_test_union(self) -> None:
        self.source.write_text(
            "int a() { return 1 == 1; }\n"
            "int b() { return 2 == 2; }\n"
            "int spacer() { return 0; }\n"
            "int c() { return 3 == 3; }\n"
        )
        self._git("add", "sample.cpp")
        self._git("-c", "user.name=stryker-cxx", "-c", "user.email=stryker-cxx@example.invalid", "commit", "-q", "-m", "batch-coverage-union")
        report = self.repo / "batch-coverage-union.json"
        coverage = self.repo / "batch-coverage-union-input.json"
        coverage.write_text(json.dumps({
            "files": {
                "sample.cpp": {
                    "coveredLines": [1, 2, 4],
                    "coveredTests": {
                        "1": ["MathTest.B", "MathTest.Shared"],
                        "2": ["MathTest.A", "MathTest.Shared"],
                        "4": ["MathTest.A", "MathTest.C", "MathTest.Shared"],
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
        plan = payload["execution"]["batching"]["plan"]
        self.assertEqual(sorted(sorted(loc["line"] for loc in item["locations"]) for item in plan), [[1], [2, 4]])
        placements = [placement for batch in plan for placement in batch["placement"]]
        self.assertTrue(any("coverage-union minimized" in placement["placementReasons"] for placement in placements))
        batch_groups = [
            group
            for group in payload["execution"]["testScheduler"]["groups"]
            if group["sessionType"] == "batch"
        ]
        self.assertEqual(len(batch_groups), 1)
        self.assertEqual(set(batch_groups[0]["selectedTests"]), {"MathTest.A", "MathTest.C", "MathTest.Shared"})

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
