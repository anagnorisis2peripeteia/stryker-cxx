"""Deterministic test-session scheduler metadata for stryker-cxx reports."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


TEST_SCHEDULER_SCHEMA_VERSION = "stryker-cxx.test-scheduler.v1"
_NON_EXECUTED_SOURCES = {"baseline", "coverage", "ignored", "compile-pruning"}


def _coerce_tests(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(test) for test in value]


def per_mutant_scheduler_record(
    *,
    coverage_selected: bool = False,
    selected_tests: list[Any] | None = None,
    split_from_batch_id: Any = None,
    artifact_backend: str | None = None,
    active_mutant: str | None = None,
) -> dict[str, Any]:
    """Create the canonical scheduler record for one per-mutant test session."""

    record: dict[str, Any] = {
        "sessionType": "per-mutant",
        "coverageSelected": bool(coverage_selected),
        "selectedTests": _coerce_tests(selected_tests or []),
        "splitFromBatchId": split_from_batch_id,
    }
    if artifact_backend:
        record["artifactBackend"] = artifact_backend
    if active_mutant:
        record["activeMutant"] = active_mutant
    return record


def batch_scheduler_record(
    *,
    mutant_ids: list[Any],
    coverage_selected: bool = False,
    selected_tests: list[Any] | None = None,
    artifact_backend: str | None = None,
) -> dict[str, Any]:
    """Create the canonical scheduler record for one batched test session."""

    record: dict[str, Any] = {
        "sessionType": "batch",
        "coverageSelected": bool(coverage_selected),
        "selectedTests": _coerce_tests(selected_tests or []),
        "mutantIds": [str(mutant_id) for mutant_id in mutant_ids],
    }
    if artifact_backend:
        record["artifactBackend"] = artifact_backend
    return record


def _selected_tests_from(run: Mapping[str, Any], scheduler: Mapping[str, Any]) -> list[str]:
    selected_tests = scheduler.get("selectedTests", run.get("coveredBy", []))
    return _coerce_tests(selected_tests)


def _session_status(statuses: Sequence[str]) -> str:
    if not statuses:
        return "PENDING"
    first = statuses[0]
    return first if all(status == first for status in statuses) else "MIXED"


def build_test_scheduler_metadata(
    mutants: Sequence[Mapping[str, Any]],
    batching: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the native test scheduler summary from normalized mutant records.

    The scheduler model is intentionally independent of mutation materialization:
    source-overlay, compiled-artifact, and mutant-switch runners can all attach
    the same `run.scheduler` shape, then reporting folds those records into
    deterministic sessions here.
    """

    groups: dict[str, dict[str, Any]] = {}
    order = 0
    for mut in mutants:
        source = str(mut.get("resultSource", "executed"))
        if source in _NON_EXECUTED_SOURCES:
            continue
        run = mut.get("run", {})
        if not isinstance(run, Mapping):
            continue
        scheduler = run.get("scheduler", {})
        if not isinstance(scheduler, Mapping):
            scheduler = {}
        batch_id = run.get("batchId")
        session_type = str(
            scheduler.get("sessionType")
            or ("batch" if source == "batch" and batch_id else "per-mutant")
        )
        key = f"batch:{batch_id}" if session_type == "batch" and batch_id else f"mutant:{mut.get('id')}"
        group = groups.get(key)
        if group is None:
            order += 1
            group = {
                "order": order,
                "sessionId": f"session-{order:04d}",
                "sessionType": session_type,
                "batchId": batch_id,
                "splitFromBatchId": scheduler.get("splitFromBatchId") or run.get("splitFromBatchId"),
                "coverageSelected": bool(scheduler.get("coverageSelected")),
                "selectedTests": _selected_tests_from(run, scheduler),
                "testCommand": run.get("testCommand") or run.get("selectedTestCommand"),
                "mutantIds": [],
                "activeMutants": [],
                "statuses": [],
            }
            groups[key] = group
        group["mutantIds"].append(mut.get("id"))
        active_mutant = scheduler.get("activeMutant")
        if active_mutant:
            group["activeMutants"].append(str(active_mutant))
        group["statuses"].append(str(mut.get("status", "PENDING")).upper())

    sessions: list[dict[str, Any]] = []
    for group in sorted(groups.values(), key=lambda item: int(item.get("order", 0))):
        statuses = [str(status) for status in group.pop("statuses", [])]
        if not group.get("activeMutants"):
            group.pop("activeMutants", None)
        group["status"] = _session_status(statuses)
        sessions.append(group)

    batching_enabled = bool(batching.get("enabled")) if isinstance(batching, Mapping) else False
    return {
        "schemaVersion": TEST_SCHEDULER_SCHEMA_VERSION,
        "strategy": "batched" if batching_enabled else "per-mutant",
        "sessions": len(sessions),
        "batchSessions": len([item for item in sessions if item.get("sessionType") == "batch"]),
        "perMutantSessions": len([item for item in sessions if item.get("sessionType") == "per-mutant"]),
        "splitSessions": len([item for item in sessions if item.get("splitFromBatchId")]),
        "coverageSelectedSessions": len([item for item in sessions if item.get("coverageSelected")]),
        "groups": sessions,
    }
