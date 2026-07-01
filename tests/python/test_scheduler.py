import unittest

from python.stryker_cxx.scheduler import (
    batch_scheduler_record,
    build_test_scheduler_metadata,
    per_mutant_scheduler_record,
)


class SchedulerMetadataTests(unittest.TestCase):
    def test_per_mutant_scheduler_record_uses_canonical_shape(self) -> None:
        record = per_mutant_scheduler_record(
            coverage_selected=True,
            selected_tests=["A.test", 7],
            split_from_batch_id="batch-a",
            artifact_backend="compiled-executable",
            active_mutant="msw-123",
        )

        self.assertEqual(
            record,
            {
                "sessionType": "per-mutant",
                "coverageSelected": True,
                "selectedTests": ["A.test", "7"],
                "splitFromBatchId": "batch-a",
                "artifactBackend": "compiled-executable",
                "activeMutant": "msw-123",
            },
        )

    def test_batch_scheduler_record_uses_canonical_shape(self) -> None:
        record = batch_scheduler_record(
            coverage_selected=True,
            selected_tests=["A.test"],
            mutant_ids=["m1", 2],
            artifact_backend="compiled-object",
        )

        self.assertEqual(
            record,
            {
                "sessionType": "batch",
                "coverageSelected": True,
                "selectedTests": ["A.test"],
                "mutantIds": ["m1", "2"],
                "artifactBackend": "compiled-object",
            },
        )

    def test_groups_batches_and_per_mutant_sessions_deterministically(self) -> None:
        payload = build_test_scheduler_metadata(
            [
                {
                    "id": "m1",
                    "status": "KILLED",
                    "resultSource": "batch",
                    "run": {
                        "batchId": "b1",
                        "scheduler": {
                            "sessionType": "batch",
                            "coverageSelected": True,
                            "selectedTests": ["MathTests.add"],
                            "mutantIds": ["m1", "m2"],
                        },
                        "selectedTestCommand": "ctest -R MathTests.add",
                    },
                },
                {
                    "id": "m2",
                    "status": "SURVIVED",
                    "resultSource": "batch",
                    "run": {
                        "batchId": "b1",
                        "scheduler": {
                            "sessionType": "batch",
                            "coverageSelected": True,
                            "selectedTests": ["MathTests.add"],
                            "mutantIds": ["m1", "m2"],
                        },
                        "selectedTestCommand": "ctest -R MathTests.add",
                    },
                },
                {
                    "id": "m3",
                    "status": "TIMEOUT",
                    "resultSource": "executed",
                    "run": {
                        "scheduler": {
                            "sessionType": "per-mutant",
                            "splitFromBatchId": "b2",
                        },
                        "testCommand": "./math_test",
                    },
                },
            ],
            {"enabled": True},
        )

        self.assertEqual(payload["schemaVersion"], "stryker-cxx.test-scheduler.v1")
        self.assertEqual(payload["strategy"], "batched")
        self.assertEqual(payload["sessions"], 2)
        self.assertEqual(payload["batchSessions"], 1)
        self.assertEqual(payload["perMutantSessions"], 1)
        self.assertEqual(payload["splitSessions"], 1)
        self.assertEqual(payload["coverageSelectedSessions"], 1)
        self.assertEqual(payload["groups"][0]["sessionId"], "session-0001")
        self.assertEqual(payload["groups"][0]["mutantIds"], ["m1", "m2"])
        self.assertEqual(payload["groups"][0]["status"], "MIXED")
        self.assertEqual(payload["groups"][0]["selectedTests"], ["MathTests.add"])
        self.assertEqual(payload["groups"][1]["sessionId"], "session-0002")
        self.assertEqual(payload["groups"][1]["mutantIds"], ["m3"])
        self.assertEqual(payload["groups"][1]["status"], "TIMEOUT")

    def test_scheduler_summary_records_active_mutant_guards(self) -> None:
        payload = build_test_scheduler_metadata(
            [
                {
                    "id": "m1",
                    "status": "SURVIVED",
                    "resultSource": "mutant-switch",
                    "run": {
                        "scheduler": per_mutant_scheduler_record(active_mutant="msw-one"),
                        "testCommand": "./test",
                    },
                }
            ],
            {"enabled": False},
        )

        self.assertEqual(payload["sessions"], 1)
        self.assertEqual(payload["groups"][0]["sessionId"], "session-0001")
        self.assertEqual(payload["groups"][0]["activeMutants"], ["msw-one"])

    def test_excludes_non_executed_results_from_sessions(self) -> None:
        payload = build_test_scheduler_metadata(
            [
                {"id": "cached", "status": "KILLED", "resultSource": "baseline", "run": {}},
                {"id": "uncovered", "status": "NO_COVERAGE", "resultSource": "coverage", "run": {}},
                {"id": "ignored", "status": "IGNORED", "resultSource": "ignored", "run": {}},
                {
                    "id": "compiled-out",
                    "status": "BUILD_ERROR",
                    "resultSource": "compile-pruning",
                    "run": {},
                },
            ],
            {"enabled": False},
        )

        self.assertEqual(payload["strategy"], "per-mutant")
        self.assertEqual(payload["sessions"], 0)
        self.assertEqual(payload["groups"], [])


if __name__ == "__main__":
    unittest.main()
