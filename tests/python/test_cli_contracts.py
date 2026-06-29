from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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
        self.assertEqual(payload["totalMutants"], 1)
        self.assertEqual(payload["survived"], 1)
        self.assertEqual(payload["score"], 0)
        self.assertEqual(payload["mutationTestingElements"]["schemaVersion"], "2.0")
        first = payload["mutationTestingElements"]["files"]["sample.cpp"]["mutants"][0]
        self.assertEqual(first["status"], "Survived")
        self.assertIn("stryker-cxx run-mutant", first["runCommand"])

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
        self.assertEqual(payload["killed"], 1)

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
        self.assertIn("# stryker-cxx report", markdown_artifact.read_text())

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


if __name__ == "__main__":
    raise SystemExit(unittest.main())
