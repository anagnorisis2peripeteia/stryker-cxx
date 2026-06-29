"""Schema contracts for stryker-cxx machine-readable outputs."""

from __future__ import annotations

from typing import Any, Iterable

REPORT_SCHEMA_VERSION = "stryker-cxx.report.v1"
MTE_SCHEMA_VERSION = "2.0"


def _expect(obj: Any, key: str, kind: type | tuple[type, ...] | None = None, *, require: bool = True) -> bool:
    if not isinstance(obj, dict):
        return False
    if key not in obj:
        return not require
    if kind is None:
        return True
    return isinstance(obj[key], kind)


def _collect(path: str, message: str) -> str:
    return f"{path}: {message}"


def validate_report(payload: dict[str, Any]) -> list[str]:
    """Validate top-level stryker-cxx.report.v1 payload.

    Returns a list of human-readable schema violations.
    """
    errors: list[str] = []

    if not isinstance(payload, dict):
        return ["payload must be an object"]

    if payload.get("schemaVersion") != REPORT_SCHEMA_VERSION:
        errors.append(
            _collect(
                "schemaVersion",
                f"expected '{REPORT_SCHEMA_VERSION}', got {payload.get('schemaVersion')!r}",
            )
        )

    required_scalar = {
        "tool": str,
        "repo": str,
        "base": (str, type(None)),
        "startedAt": str,
        "completedAt": (str, type(None)),
        "threshold": (float, int, type(None)),
    }
    for key, expected in required_scalar.items():
        if not _expect(payload, key, expected, require=False):
            errors.append(_collect(key, f"expected type {expected}"))

    required_ints = {
        "totalMutants": int,
        "killed": int,
        "survived": int,
        "buildErrors": int,
        "timeouts": int,
        "ignored": int,
    }
    for key, expected in required_ints.items():
        if not _expect(payload, key, expected):
            errors.append(_collect(key, f"expected {expected.__name__}"))

    if not _expect(payload, "score", (int, float)):
        errors.append(_collect("score", "expected numeric score"))
    else:
        score = payload.get("score")
        if score is not None and not (0.0 <= float(score) <= 1.0):
            errors.append(_collect("score", "expected score in [0.0, 1.0]"))

    exec_ctx = payload.get("execution")
    if not isinstance(exec_ctx, dict):
        errors.append(_collect("execution", "expected object"))
    else:
        for key in ("mode", "worktreeMode", "jobs"):
            if key == "jobs":
                if not isinstance(exec_ctx.get(key), int):
                    errors.append(_collect("execution.jobs", "expected integer"))
            else:
                if key in exec_ctx and not isinstance(exec_ctx.get(key), str):
                    errors.append(_collect(f"execution.{key}", "expected string"))

    cmds = payload.get("commands")
    if not isinstance(cmds, dict):
        errors.append(_collect("commands", "expected object with build/test"))
    else:
        for key in ("build", "test"):
            if key not in cmds:
                errors.append(_collect(f"commands.{key}", "missing"))

    mutants = payload.get("mutants")
    if not isinstance(mutants, list):
        errors.append(_collect("mutants", "expected array"))
    else:
        for idx, mut in enumerate(mutants):
            _validate_mutant(mut, errors, prefix=f"mutants[{idx}]")

    mte = payload.get("mutationTestingElements")
    if not isinstance(mte, dict):
        errors.append(_collect("mutationTestingElements", "expected object"))
    else:
        errors.extend(validate_mte(mte))

    return errors


def validate_mte(payload: dict[str, Any]) -> list[str]:
    """Validate a mutation-testing-elements payload."""
    errors: list[str] = []

    if not isinstance(payload, dict):
        return ["mutationTestingElements must be an object"]

    if payload.get("schemaVersion") != MTE_SCHEMA_VERSION:
        errors.append(_collect("schemaVersion", f"expected '{MTE_SCHEMA_VERSION}'"))

    if not _expect(payload, "projectRoot", (str, type(None)), require=False):
        errors.append(_collect("projectRoot", "expected string"))

    if not _expect(payload, "language", str):
        errors.append(_collect("language", "expected string"))

    files = payload.get("files")
    if not isinstance(files, dict):
        errors.append(_collect("files", "expected map"))
    else:
        for file_name, entry in files.items():
            if not isinstance(file_name, str):
                errors.append(_collect("files", "expected string keys"))
            if not isinstance(entry, dict):
                errors.append(_collect(f"files[{file_name}]", "expected object"))
                continue
            source = entry.get("source")
            if source is not None and not isinstance(source, str):
                errors.append(_collect(f"files[{file_name}].source", "expected string if present"))
            mutants = entry.get("mutants")
            if mutants is None:
                continue
            if not isinstance(mutants, list):
                errors.append(_collect(f"files[{file_name}].mutants", "expected array if present"))
            else:
                for idx, mut in enumerate(mutants):
                    if not _validate_mte_mutant(mut, errors, prefix=f"files[{file_name}].mutants[{idx}]"):
                        pass

    test_files = payload.get("testFiles")
    if test_files is None or not isinstance(test_files, dict):
        errors.append(_collect("testFiles", "expected map"))

    return errors


def validate_mutant_status(status: str) -> bool:
    return str(status) in {"KILLED", "SURVIVED", "BUILD_ERROR", "TIMEOUT", "IGNORED", "PENDING", "RUNTIME_ERROR"}


def _validate_mutant(mut: Any, errors: list[str], *, prefix: str) -> bool:
    if not isinstance(mut, dict):
        errors.append(_collect(prefix, "expected mutant object"))
        return False

    for key in ("id", "file", "status", "original", "mutated"):
        if not _expect(mut, key, (str, int), require=False):
            errors.append(_collect(f"{prefix}.{key}", "expected string"))

    if not isinstance(mut.get("line"), int):
        errors.append(_collect(f"{prefix}.line", "expected integer"))

    col = mut.get("column", mut.get("col"))
    if not isinstance(col, int):
        errors.append(_collect(f"{prefix}.column", "expected integer"))

    if "column" not in mut and "col" in mut:
        mut["column"] = mut["col"]

    if mut.get("status") and str(mut.get("status")).upper() not in {
        "KILLED",
        "SURVIVED",
        "BUILD_ERROR",
        "TIMEOUT",
        "IGNORED",
        "PENDING",
        "RUNTIME_ERROR",
    }:
        errors.append(_collect(f"{prefix}.status", f"unexpected status {mut.get('status')!r}"))
    return True


def _validate_mte_mutant(mut: Any, errors: list[str], *, prefix: str) -> bool:
    if not isinstance(mut, dict):
        errors.append(_collect(prefix, "expected mutant object"))
        return False

    for key in ("id", "mutatorName", "original", "replacement", "status"):
        if not _expect(mut, key, str):
            errors.append(_collect(f"{prefix}.{key}", "expected string"))

    status = mut.get("status")
    if status and str(status) not in {
        "Killed",
        "Survived",
        "NoCoverage",
        "Timeout",
        "Ignored",
        "Pending",
        "RuntimeError",
    }:
        errors.append(_collect(f"{prefix}.status", f"unexpected status {status!r}"))

    for side in ("start", "end"):
        loc = mut.get("location", {}).get(side) if isinstance(mut.get("location"), dict) else None
        if not isinstance(loc, dict):
            errors.append(_collect(f"{prefix}.location.{side}", "expected location segment"))
            continue
        if not isinstance(loc.get("line"), int):
            errors.append(_collect(f"{prefix}.location.{side}.line", "expected integer"))
        if not isinstance(loc.get("column"), int):
            errors.append(_collect(f"{prefix}.location.{side}.column", "expected integer"))

    return True


def require_report(payload: dict[str, Any]) -> None:
    errors = validate_report(payload)
    if errors:
        raise ValueError("invalid stryker-cxx report:\n" + "\n".join(errors))


def require_mte(payload: dict[str, Any]) -> None:
    errors = validate_mte(payload)
    if errors:
        raise ValueError("invalid mutation-testing-elements payload:\n" + "\n".join(errors))


def supported_mte_statuses() -> Iterable[str]:
    return (
        "Killed",
        "Survived",
        "NoCoverage",
        "Timeout",
        "Ignored",
        "Pending",
        "RuntimeError",
    )


def supported_native_statuses() -> Iterable[str]:
    return ("KILLED", "SURVIVED", "BUILD_ERROR", "TIMEOUT", "IGNORED", "PENDING", "RUNTIME_ERROR")
