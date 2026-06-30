"""Payload contract helpers shared by schema validation and report projection."""

from __future__ import annotations

from typing import Any

REPORT_SCHEMA_VERSION = "stryker-cxx.report.v1"
MTE_SCHEMA_VERSION = "2.0"
TOOL_VERSION = "0.1.0"

NATIVE_STATUSES = (
    "KILLED",
    "SURVIVED",
    "BUILD_ERROR",
    "CHECK_ERROR",
    "NO_COVERAGE",
    "TIMEOUT",
    "IGNORED",
    "PENDING",
    "RUNTIME_ERROR",
)

MTE_STATUSES = (
    "Killed",
    "Survived",
    "NoCoverage",
    "Timeout",
    "Ignored",
    "Pending",
    "RuntimeError",
)

NATIVE_TO_MTE_STATUS = {
    "KILLED": "Killed",
    "SURVIVED": "Survived",
    "BUILD_ERROR": "NoCoverage",
    "CHECK_ERROR": "RuntimeError",
    "NO_COVERAGE": "NoCoverage",
    "TIMEOUT": "Timeout",
    "IGNORED": "Ignored",
    "PENDING": "Pending",
    "RUNTIME_ERROR": "RuntimeError",
}

_NATIVE_STATUS_SET = frozenset(NATIVE_STATUSES)
_MTE_STATUS_SET = frozenset(MTE_STATUSES)


def is_native_status(status: Any) -> bool:
    return str(status) in _NATIVE_STATUS_SET


def is_mte_status(status: Any) -> bool:
    return str(status) in _MTE_STATUS_SET


def native_to_mte_status(status: Any) -> str:
    return NATIVE_TO_MTE_STATUS.get(str(status).upper(), "RuntimeError")


def extract_mte_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("expected mutation-testing-elements payload object")

    if payload.get("schemaVersion") == MTE_SCHEMA_VERSION and isinstance(payload.get("files"), dict):
        return payload

    nested = payload.get("mutationTestingElements")
    if isinstance(nested, dict) and isinstance(nested.get("files"), dict):
        nested_schema = nested.get("schemaVersion")
        if nested_schema is None:
            return {**nested, "schemaVersion": MTE_SCHEMA_VERSION}
        if nested_schema == MTE_SCHEMA_VERSION:
            return nested

    raise ValueError(f"unexpected schemaVersion; expected '{MTE_SCHEMA_VERSION}'")


def supported_mte_statuses() -> tuple[str, ...]:
    return MTE_STATUSES


def supported_native_statuses() -> tuple[str, ...]:
    return NATIVE_STATUSES
