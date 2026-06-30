"""Schema contracts for stryker-cxx machine-readable outputs."""

from __future__ import annotations

from typing import Any

from .payload_contract import (
    MTE_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    TOOL_VERSION,
    is_mte_status,
    is_native_status,
    supported_mte_statuses,
    supported_native_statuses,
)


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
        "toolVersion": str,
        "repo": str,
        "base": (str, type(None)),
        "startedAt": str,
        "completedAt": (str, type(None)),
        "threshold": (float, int, type(None)),
        "timeoutSeconds": (int, type(None)),
    }
    for key, expected in required_scalar.items():
        if not _expect(payload, key, expected):
            errors.append(_collect(key, f"expected type {expected}"))

    required_ints = {
        "totalMutants": int,
        "killed": int,
        "survived": int,
        "buildErrors": int,
        "checkErrors": int,
        "noCoverage": int,
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

    thresholds = payload.get("thresholds")
    if not isinstance(thresholds, dict):
        errors.append(_collect("thresholds", "expected object"))
    else:
        for key in ("high", "low", "break"):
            if not isinstance(thresholds.get(key), (int, float)):
                errors.append(_collect(f"thresholds.{key}", "expected numeric threshold"))
        if not isinstance(thresholds.get("status"), str):
            errors.append(_collect("thresholds.status", "expected string"))
        elif thresholds.get("status") not in {"failed", "low", "acceptable", "high"}:
            errors.append(_collect("thresholds.status", f"unexpected status {thresholds.get('status')!r}"))

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
        optional_exec_types = {
            "initialTest": bool,
            "dryRunOnly": bool,
            "skipTests": bool,
            "timeoutFactor": (int, float),
            "timeoutConstantMs": int,
            "effectiveTimeoutMs": (int, type(None)),
        }
        for key, expected in optional_exec_types.items():
            if key in exec_ctx and not isinstance(exec_ctx.get(key), expected):
                errors.append(_collect(f"execution.{key}", f"expected type {expected}"))
        analysis = exec_ctx.get("analysis")
        if analysis is not None:
            if not isinstance(analysis, dict):
                errors.append(_collect("execution.analysis", "expected object"))
            else:
                if "engine" in analysis and not isinstance(analysis.get("engine"), str):
                    errors.append(_collect("execution.analysis.engine", "expected string"))
                if "macroRejectedMutants" in analysis and not isinstance(analysis.get("macroRejectedMutants"), int):
                    errors.append(_collect("execution.analysis.macroRejectedMutants", "expected integer"))
                if "macroRejections" in analysis and not isinstance(analysis.get("macroRejections"), list):
                    errors.append(_collect("execution.analysis.macroRejections", "expected array"))
                suppression = analysis.get("equivalentSuppression")
                if suppression is not None:
                    if not isinstance(suppression, dict):
                        errors.append(_collect("execution.analysis.equivalentSuppression", "expected object"))
                    else:
                        mode = suppression.get("mode")
                        if not isinstance(mode, str):
                            errors.append(_collect("execution.analysis.equivalentSuppression.mode", "expected string"))
                        elif mode not in {"off", "conservative", "aggressive"}:
                            errors.append(_collect("execution.analysis.equivalentSuppression.mode", f"unexpected mode {mode!r}"))
                        if "suppressedMutants" in suppression and not isinstance(suppression.get("suppressedMutants"), int):
                            errors.append(_collect("execution.analysis.equivalentSuppression.suppressedMutants", "expected integer"))
                        if "suppressions" in suppression and not isinstance(suppression.get("suppressions"), list):
                            errors.append(_collect("execution.analysis.equivalentSuppression.suppressions", "expected array"))
        batching = exec_ctx.get("batching")
        if batching is not None:
            if not isinstance(batching, dict):
                errors.append(_collect("execution.batching", "expected object"))
            else:
                if "enabled" in batching and not isinstance(batching.get("enabled"), bool):
                    errors.append(_collect("execution.batching.enabled", "expected boolean"))
                for key in ("batchSize", "batches", "splitBatches", "batchedMutants"):
                    if key in batching and not isinstance(batching.get(key), int):
                        errors.append(_collect(f"execution.batching.{key}", "expected integer"))
        compile_pruning = exec_ctx.get("compilePruning")
        if compile_pruning is not None:
            if not isinstance(compile_pruning, dict):
                errors.append(_collect("execution.compilePruning", "expected object"))
            else:
                if "enabled" in compile_pruning and not isinstance(compile_pruning.get("enabled"), bool):
                    errors.append(_collect("execution.compilePruning.enabled", "expected boolean"))
                if "strategy" in compile_pruning and not isinstance(compile_pruning.get("strategy"), str):
                    errors.append(_collect("execution.compilePruning.strategy", "expected string"))
                for key in (
                    "attempts",
                    "candidateMutants",
                    "failedBatches",
                    "retryBatches",
                    "prunedMutants",
                    "buildErrors",
                    "checkErrors",
                ):
                    if key in compile_pruning and not isinstance(compile_pruning.get(key), int):
                        errors.append(_collect(f"execution.compilePruning.{key}", "expected integer"))
                for key in ("records", "attemptRecords", "retryRecords"):
                    if key in compile_pruning and not isinstance(compile_pruning.get(key), list):
                        errors.append(_collect(f"execution.compilePruning.{key}", "expected array"))
        test_scheduler = exec_ctx.get("testScheduler")
        if test_scheduler is not None:
            if not isinstance(test_scheduler, dict):
                errors.append(_collect("execution.testScheduler", "expected object"))
            else:
                if "schemaVersion" in test_scheduler and not isinstance(test_scheduler.get("schemaVersion"), str):
                    errors.append(_collect("execution.testScheduler.schemaVersion", "expected string"))
                if "strategy" in test_scheduler and not isinstance(test_scheduler.get("strategy"), str):
                    errors.append(_collect("execution.testScheduler.strategy", "expected string"))
                for key in (
                    "sessions",
                    "batchSessions",
                    "perMutantSessions",
                    "splitSessions",
                    "coverageSelectedSessions",
                ):
                    if key in test_scheduler and not isinstance(test_scheduler.get(key), int):
                        errors.append(_collect(f"execution.testScheduler.{key}", "expected integer"))
                if "groups" in test_scheduler and not isinstance(test_scheduler.get("groups"), list):
                    errors.append(_collect("execution.testScheduler.groups", "expected array"))
        dashboard = exec_ctx.get("dashboard")
        if dashboard is not None:
            if not isinstance(dashboard, dict):
                errors.append(_collect("execution.dashboard", "expected object"))
            else:
                for key in ("version", "exportPath", "project", "branch", "commit", "buildUrl"):
                    if key in dashboard and not isinstance(dashboard.get(key), (str, type(None))):
                        errors.append(
                            _collect(f"execution.dashboard.{key}", "expected string or null")
                        )
                if "retentionDays" in dashboard and not isinstance(
                    dashboard.get("retentionDays"),
                    (int, type(None)),
                ):
                    errors.append(
                        _collect("execution.dashboard.retentionDays", "expected integer or null")
                    )
                upload = dashboard.get("upload")
                if upload is not None:
                    if not isinstance(upload, dict):
                        errors.append(_collect("execution.dashboard.upload", "expected object"))
                    else:
                        for key in ("enabled", "urlConfigured"):
                            if key in upload and not isinstance(upload.get(key), bool):
                                errors.append(
                                    _collect(
                                        f"execution.dashboard.upload.{key}",
                                        "expected boolean",
                                    )
                                )
                        for key in ("authTokenEnv", "authHeader"):
                            if key in upload and not isinstance(upload.get(key), (str, type(None))):
                                errors.append(
                                    _collect(
                                        f"execution.dashboard.upload.{key}",
                                        "expected string or null",
                                    )
                                )
        resource = exec_ctx.get("resourceIsolation")
        if resource is not None:
            if not isinstance(resource, dict):
                errors.append(_collect("execution.resourceIsolation", "expected object"))
            else:
                for key in ("workspacePerMutant", "parallelSafe", "retainWorktrees"):
                    if key in resource and not isinstance(resource.get(key), bool):
                        errors.append(_collect(f"execution.resourceIsolation.{key}", "expected boolean"))
                if "workerCount" in resource and not isinstance(resource.get("workerCount"), int):
                    errors.append(_collect("execution.resourceIsolation.workerCount", "expected integer"))
                if "retainedWorktreeTtlHours" in resource and not isinstance(
                    resource.get("retainedWorktreeTtlHours"),
                    (int, float, type(None)),
                ):
                    errors.append(
                        _collect(
                            "execution.resourceIsolation.retainedWorktreeTtlHours",
                            "expected number or null",
                        )
                    )
                if "retainedWorktreeCleanup" in resource and not isinstance(
                    resource.get("retainedWorktreeCleanup"),
                    dict,
                ):
                    errors.append(
                        _collect(
                            "execution.resourceIsolation.retainedWorktreeCleanup",
                            "expected object",
                        )
                    )
                for key in ("worktreeMode", "artifactDir", "workerTmpDir", "workerLabel", "network"):
                    if key in resource and not isinstance(resource.get(key), (str, type(None))):
                        errors.append(_collect(f"execution.resourceIsolation.{key}", "expected string or null"))
                if "environmentKeys" in resource:
                    keys = resource.get("environmentKeys")
                    if not isinstance(keys, list) or not all(isinstance(item, str) for item in keys):
                        errors.append(_collect("execution.resourceIsolation.environmentKeys", "expected string array"))
                for key in ("environmentInheritedKeys", "environmentBlockedKeys"):
                    values = resource.get(key)
                    if values is not None and (
                        not isinstance(values, list)
                        or not all(isinstance(item, str) for item in values)
                    ):
                        errors.append(_collect(f"execution.resourceIsolation.{key}", "expected string array"))
                if "redaction" in resource:
                    redaction = resource.get("redaction")
                    if not isinstance(redaction, dict):
                        errors.append(_collect("execution.resourceIsolation.redaction", "expected object"))
                    else:
                        for key in ("enabled", "environmentValues", "secretAssignmentPatterns"):
                            if key in redaction and not isinstance(redaction.get(key), bool):
                                errors.append(
                                    _collect(
                                        f"execution.resourceIsolation.redaction.{key}",
                                        "expected boolean",
                                    )
                                )
                        if "replacement" in redaction and not isinstance(redaction.get("replacement"), str):
                            errors.append(
                                _collect(
                                    "execution.resourceIsolation.redaction.replacement",
                                    "expected string",
                                )
                            )
                if "retainWorktreesFor" in resource:
                    statuses = resource.get("retainWorktreesFor")
                    if not isinstance(statuses, list) or not all(isinstance(item, str) for item in statuses):
                        errors.append(
                            _collect(
                                "execution.resourceIsolation.retainWorktreesFor",
                                "expected string array",
                            )
                        )

    dry_run = payload.get("dryRun")
    if not isinstance(dry_run, dict):
        errors.append(_collect("dryRun", "expected object"))
    else:
        status = dry_run.get("status")
        if not isinstance(status, str):
            errors.append(_collect("dryRun.status", "expected string"))
        elif status not in {"PASSED", "FAILED", "SKIPPED", "NOT_RUN"}:
            errors.append(_collect("dryRun.status", f"unexpected status {status!r}"))
        for phase in ("build", "check", "test"):
            item = dry_run.get(phase)
            if item is None:
                continue
            if not isinstance(item, dict):
                errors.append(_collect(f"dryRun.{phase}", "expected object"))
                continue
            if not isinstance(item.get("exitCode"), int):
                errors.append(_collect(f"dryRun.{phase}.exitCode", "expected integer"))
            if not isinstance(item.get("durationMs"), int):
                errors.append(_collect(f"dryRun.{phase}.durationMs", "expected integer"))
            if "log" in item and not isinstance(item.get("log"), str):
                errors.append(_collect(f"dryRun.{phase}.log", "expected string"))

    cmds = payload.get("commands")
    if not isinstance(cmds, dict):
        errors.append(_collect("commands", "expected object with build/test"))
    else:
        for key in ("build", "check", "test"):
            if key not in cmds:
                errors.append(_collect(f"commands.{key}", "missing"))

    coverage = payload.get("coverage")
    if not isinstance(coverage, dict):
        errors.append(_collect("coverage", "expected object"))
    else:
        if "enabled" in coverage and not isinstance(coverage.get("enabled"), bool):
            errors.append(_collect("coverage.enabled", "expected boolean"))
        if "coveredMutants" in coverage and not isinstance(coverage.get("coveredMutants"), int):
            errors.append(_collect("coverage.coveredMutants", "expected integer"))
        if "noCoverageMutants" in coverage and not isinstance(coverage.get("noCoverageMutants"), int):
            errors.append(_collect("coverage.noCoverageMutants", "expected integer"))
        if "testLevel" in coverage and not isinstance(coverage.get("testLevel"), bool):
            errors.append(_collect("coverage.testLevel", "expected boolean"))
        if "testMappedFiles" in coverage and not isinstance(coverage.get("testMappedFiles"), int):
            errors.append(_collect("coverage.testMappedFiles", "expected integer"))
        if "testSelectionTemplate" in coverage and not isinstance(coverage.get("testSelectionTemplate"), (str, type(None))):
            errors.append(_collect("coverage.testSelectionTemplate", "expected string or null"))
        for key in ("testSelectedMutants", "testSelectionMisses"):
            if key in coverage and not isinstance(coverage.get(key), int):
                errors.append(_collect(f"coverage.{key}", "expected integer"))

    baseline = payload.get("baseline")
    if not isinstance(baseline, dict):
        errors.append(_collect("baseline", "expected object"))
    else:
        if "enabled" in baseline and not isinstance(baseline.get("enabled"), bool):
            errors.append(_collect("baseline.enabled", "expected boolean"))
        for key in ("cacheHits", "cacheMisses", "cacheWrites"):
            if key in baseline and not isinstance(baseline.get(key), int):
                errors.append(_collect(f"baseline.{key}", "expected integer"))
        if "maxAgeDays" in baseline and not isinstance(baseline.get("maxAgeDays"), (int, type(None))):
            errors.append(_collect("baseline.maxAgeDays", "expected integer or null"))
        if "branch" in baseline and not isinstance(baseline.get("branch"), (str, type(None))):
            errors.append(_collect("baseline.branch", "expected string or null"))
        if "missReasons" in baseline:
            reasons = baseline.get("missReasons")
            if not isinstance(reasons, dict) or not all(isinstance(k, str) and isinstance(v, int) for k, v in reasons.items()):
                errors.append(_collect("baseline.missReasons", "expected string-to-integer map"))

    project_analysis = payload.get("projectAnalysis")
    if project_analysis is not None:
        if not isinstance(project_analysis, dict):
            errors.append(_collect("projectAnalysis", "expected object"))
        else:
            if "schemaVersion" in project_analysis and not isinstance(project_analysis.get("schemaVersion"), str):
                errors.append(_collect("projectAnalysis.schemaVersion", "expected string"))
            if "confidence" in project_analysis and not isinstance(project_analysis.get("confidence"), str):
                errors.append(_collect("projectAnalysis.confidence", "expected string"))
            for key in ("targetFiles", "buildSystems", "sourceTargets", "buildTargets", "testTargets"):
                if key in project_analysis and not isinstance(project_analysis.get(key), list):
                    errors.append(_collect(f"projectAnalysis.{key}", "expected array"))
            compile_db = project_analysis.get("compileDatabase")
            if compile_db is not None and not isinstance(compile_db, dict):
                errors.append(_collect("projectAnalysis.compileDatabase", "expected object"))
            commands = project_analysis.get("commands")
            if commands is not None and not isinstance(commands, dict):
                errors.append(_collect("projectAnalysis.commands", "expected object"))

    mutation_artifact = payload.get("mutationArtifact")
    if mutation_artifact is not None:
        if not isinstance(mutation_artifact, dict):
            errors.append(_collect("mutationArtifact", "expected object"))
        else:
            if "schemaVersion" in mutation_artifact and not isinstance(mutation_artifact.get("schemaVersion"), str):
                errors.append(_collect("mutationArtifact.schemaVersion", "expected string"))
            for key in ("mode", "implementation"):
                if key in mutation_artifact and not isinstance(mutation_artifact.get(key), str):
                    errors.append(_collect(f"mutationArtifact.{key}", "expected string"))
            for key in ("workspacePerMutant", "parallelSafe", "supportsCompiledReplacement", "retainArtifacts"):
                if key in mutation_artifact and not isinstance(mutation_artifact.get(key), bool):
                    errors.append(_collect(f"mutationArtifact.{key}", "expected boolean"))
            if "retainArtifactsFor" in mutation_artifact and not isinstance(mutation_artifact.get("retainArtifactsFor"), list):
                errors.append(_collect("mutationArtifact.retainArtifactsFor", "expected array"))
            source_overlay = mutation_artifact.get("sourceOverlay")
            if source_overlay is not None and not isinstance(source_overlay, dict):
                errors.append(_collect("mutationArtifact.sourceOverlay", "expected object"))

    artifact_placement = payload.get("artifactPlacement")
    if artifact_placement is not None:
        if not isinstance(artifact_placement, dict):
            errors.append(_collect("artifactPlacement", "expected object"))
        else:
            if "schemaVersion" in artifact_placement and not isinstance(artifact_placement.get("schemaVersion"), str):
                errors.append(_collect("artifactPlacement.schemaVersion", "expected string"))
            for key in ("mode", "implementation"):
                if key in artifact_placement and not isinstance(artifact_placement.get(key), str):
                    errors.append(_collect(f"artifactPlacement.{key}", "expected string"))
            for key in ("restoreOriginals", "retainArtifacts"):
                if key in artifact_placement and not isinstance(artifact_placement.get(key), bool):
                    errors.append(_collect(f"artifactPlacement.{key}", "expected boolean"))
            if "retainArtifactsFor" in artifact_placement and not isinstance(artifact_placement.get("retainArtifactsFor"), list):
                errors.append(_collect("artifactPlacement.retainArtifactsFor", "expected array"))
            for key in ("sourceOverlay", "compiledArtifacts"):
                if key in artifact_placement and not isinstance(artifact_placement.get(key), dict):
                    errors.append(_collect(f"artifactPlacement.{key}", "expected object"))

    compiled_artifacts = payload.get("compiledArtifacts")
    if compiled_artifacts is not None:
        if not isinstance(compiled_artifacts, list):
            errors.append(_collect("compiledArtifacts", "expected array"))
        else:
            for idx, item in enumerate(compiled_artifacts):
                if not isinstance(item, dict):
                    errors.append(_collect(f"compiledArtifacts[{idx}]", "expected object"))
                    continue
                if "schemaVersion" in item and not isinstance(item.get("schemaVersion"), str):
                    errors.append(_collect(f"compiledArtifacts[{idx}].schemaVersion", "expected string"))
                for key in ("backend", "kind", "target"):
                    if key in item and not isinstance(item.get(key), str):
                        errors.append(_collect(f"compiledArtifacts[{idx}].{key}", "expected string"))
                if "originalRestored" in item and not isinstance(item.get("originalRestored"), bool):
                    errors.append(_collect(f"compiledArtifacts[{idx}].originalRestored", "expected boolean"))

    lifecycle = payload.get("lifecycle")
    if lifecycle is not None:
        if not isinstance(lifecycle, dict):
            errors.append(_collect("lifecycle", "expected object"))
        else:
            if "schemaVersion" in lifecycle and not isinstance(lifecycle.get("schemaVersion"), str):
                errors.append(_collect("lifecycle.schemaVersion", "expected string"))
            if "artifactModel" in lifecycle and not isinstance(lifecycle.get("artifactModel"), str):
                errors.append(_collect("lifecycle.artifactModel", "expected string"))
            phase_order = lifecycle.get("phaseOrder")
            if phase_order is not None and (
                not isinstance(phase_order, list)
                or not all(isinstance(item, str) for item in phase_order)
            ):
                errors.append(_collect("lifecycle.phaseOrder", "expected string array"))
            phases = lifecycle.get("phases")
            if phases is not None:
                if not isinstance(phases, list):
                    errors.append(_collect("lifecycle.phases", "expected array"))
                else:
                    for idx, phase in enumerate(phases):
                        if not isinstance(phase, dict):
                            errors.append(_collect(f"lifecycle.phases[{idx}]", "expected object"))
                            continue
                        if not isinstance(phase.get("name"), str):
                            errors.append(_collect(f"lifecycle.phases[{idx}].name", "expected string"))
                        if not isinstance(phase.get("status"), str):
                            errors.append(_collect(f"lifecycle.phases[{idx}].status", "expected string"))
                        if "detail" in phase and not isinstance(phase.get("detail"), dict):
                            errors.append(_collect(f"lifecycle.phases[{idx}].detail", "expected object"))

    config = payload.get("config")
    if not isinstance(config, dict):
        errors.append(_collect("config", "expected object"))
    else:
        if "path" in config and not isinstance(config.get("path"), (str, type(None))):
            errors.append(_collect("config.path", "expected string or null"))
        if "hash" in config and not isinstance(config.get("hash"), (str, type(None))):
            errors.append(_collect("config.hash", "expected string or null"))
        if "effective" in config and not isinstance(config.get("effective"), dict):
            errors.append(_collect("config.effective", "expected object"))

    summary = payload.get("summary")
    if not isinstance(summary, dict):
        errors.append(_collect("summary", "expected object"))
    else:
        for key in ("byStatus", "byFile", "byMutator"):
            if not isinstance(summary.get(key), dict):
                errors.append(_collect(f"summary.{key}", "expected object"))

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
    return is_native_status(status)


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

    if mut.get("status") and not is_native_status(str(mut.get("status")).upper()):
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
    if status and not is_mte_status(status):
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
