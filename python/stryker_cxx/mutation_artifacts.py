"""Mutation artifact materialization for stryker-cxx.

The current engine mutates source text, but the lifecycle contract needs a
single seam where source overlays, object replacements, and library swaps can
eventually share execution/reporting metadata.  This module owns the source
overlay implementation used today.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any

MUTATION_ARTIFACT_SCHEMA_VERSION = "stryker-cxx.mutation-artifact.v1"
ARTIFACT_PLACEMENT_SCHEMA_VERSION = "stryker-cxx.artifact-placement.v1"
SOURCE_OVERLAY_MODE = "source-overlay"
COMPILED_ARTIFACT_MODE = "compiled-artifact"
MUTANT_SWITCH_MODE = "mutant-switch"
MUTANT_SWITCH_ACTIVE_ENV = "STRYKER_CXX_ACTIVE_MUTANT"


def _workspace_is_retained(retain: bool, retain_state: dict[str, bool] | None) -> bool:
    return retain or bool(retain_state and retain_state.get("retain"))


def _safe_worker_label(label: str | None) -> str:
    if not label:
        return ""
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in label)[:48]


def _source_overlay_strategy(implementation: str) -> str:
    return {
        "inplace": "tracked-source-restore",
        "copy": "isolated-copy",
        "git-worktree": "isolated-git-worktree",
    }.get(implementation, implementation)


def _source_overlay_restoration(implementation: str) -> str:
    if implementation == "inplace":
        return "restore-mutated-source"
    if implementation == "git-worktree":
        return "remove-git-worktree-or-retain"
    return "discard-copy-or-retain"


def mutation_artifact_metadata(
    implementation: str,
    *,
    worker_tmp_dir: str | None = None,
    retain_worktrees: bool = False,
    retain_worktrees_for: list[str] | None = None,
    worker_label: str | None = None,
) -> dict[str, Any]:
    """Return report-level metadata for the active mutation artifact mode."""
    payload: dict[str, Any] = {
        "schemaVersion": MUTATION_ARTIFACT_SCHEMA_VERSION,
        "mode": SOURCE_OVERLAY_MODE,
        "implementation": implementation,
        "workspacePerMutant": implementation in {"copy", "git-worktree"},
        "parallelSafe": implementation != "inplace",
        "supportsCompiledReplacement": False,
        "sourceOverlay": {
            "strategy": _source_overlay_strategy(implementation),
            "restoration": _source_overlay_restoration(implementation),
        },
    }
    if worker_tmp_dir:
        payload["workerTmpDir"] = worker_tmp_dir
    if worker_label:
        payload["workerLabel"] = worker_label
    if retain_worktrees:
        payload["retainArtifacts"] = True
        payload["retainArtifactsFor"] = list(retain_worktrees_for or [])
    else:
        payload["retainArtifacts"] = False
        payload["retainArtifactsFor"] = []
    return payload


def artifact_placement_policy(
    implementation: str,
    *,
    artifact_root: str | None = None,
    worker_tmp_dir: str | None = None,
    retain_worktrees: bool = False,
    retain_worktrees_for: list[str] | None = None,
    worker_label: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schemaVersion": ARTIFACT_PLACEMENT_SCHEMA_VERSION,
        "mode": SOURCE_OVERLAY_MODE,
        "implementation": implementation,
        "artifactRoot": artifact_root,
        "workerTmpDir": worker_tmp_dir,
        "workerLabel": worker_label,
        "restoreOriginals": True,
        "retainArtifacts": retain_worktrees,
        "retainArtifactsFor": list(retain_worktrees_for or []),
        "sourceOverlay": {
            "restorePolicy": _source_overlay_restoration(implementation),
            "placement": _source_overlay_strategy(implementation),
        },
        "compiledArtifacts": {
            "supported": False,
            "placement": "not-supported",
            "restorePolicy": "not-supported",
        },
    }
    return payload


def mutant_switch_guard_id(mutant: Any) -> str:
    data = dict(mutant.__dict__ if hasattr(mutant, "__dict__") else mutant)
    identity = {
        "id": data.get("id"),
        "file": data.get("file"),
        "line": data.get("line"),
        "column": data.get("column", data.get("col")),
        "mutator": data.get("mutator"),
        "original": data.get("original"),
        "mutated": data.get("mutated"),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"msw-{digest[:16]}"


def mutant_switch_artifact_metadata(
    *,
    enabled: bool,
    guard_count: int,
    guards: list[dict[str, Any]] | None = None,
    fallback_reason: str | None = None,
    activation_environment: str = MUTANT_SWITCH_ACTIVE_ENV,
    implementation: str = "guarded-source-overlay",
) -> dict[str, Any]:
    return {
        "schemaVersion": MUTATION_ARTIFACT_SCHEMA_VERSION,
        "mode": MUTANT_SWITCH_MODE,
        "implementation": implementation,
        "workspacePerMutant": False,
        "parallelSafe": True,
        "supportsCompiledReplacement": True,
        "enabled": enabled,
        "activationEnvironment": activation_environment,
        "runtimeGuardCount": guard_count if enabled else 0,
        "candidateGuardCount": guard_count,
        "fallbackReason": fallback_reason,
        "guards": list(guards or []),
        "mutantSwitch": {
            "guardStrategy": "environment-selected-conditional",
            "activationEnvironment": activation_environment,
        },
    }


def compiled_mutation_artifact_metadata(
    backend: str,
    *,
    artifact_kind: str = "executable",
    implementation: str = "scratch-build-swap",
    worker_tmp_dir: str | None = None,
    retain_artifacts: bool = False,
    retain_artifacts_for: list[str] | None = None,
    worker_label: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schemaVersion": MUTATION_ARTIFACT_SCHEMA_VERSION,
        "mode": COMPILED_ARTIFACT_MODE,
        "backend": backend,
        "implementation": implementation,
        "workspacePerMutant": True,
        "parallelSafe": True,
        "supportsCompiledReplacement": True,
        "sourceOverlay": {
            "strategy": "scratch-source-materialization",
            "restoration": "discard-scratch-source",
        },
        "compiledArtifacts": {
            "supported": True,
            "kinds": [artifact_kind],
            "placementPolicy": "swap-file",
            "sourceCheckoutMutation": False,
        },
        "retainArtifacts": retain_artifacts,
        "retainArtifactsFor": list(retain_artifacts_for or []),
    }
    if worker_tmp_dir:
        payload["workerTmpDir"] = worker_tmp_dir
    if worker_label:
        payload["workerLabel"] = worker_label
    return payload


def compiled_artifact_placement_policy(
    backend: str,
    *,
    artifact_kind: str = "executable",
    artifact_root: str | None = None,
    worker_tmp_dir: str | None = None,
    retain_artifacts: bool = False,
    retain_artifacts_for: list[str] | None = None,
    worker_label: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": ARTIFACT_PLACEMENT_SCHEMA_VERSION,
        "mode": COMPILED_ARTIFACT_MODE,
        "implementation": backend,
        "artifactRoot": artifact_root,
        "workerTmpDir": worker_tmp_dir,
        "workerLabel": worker_label,
        "restoreOriginals": True,
        "retainArtifacts": retain_artifacts,
        "retainArtifactsFor": list(retain_artifacts_for or []),
        "sourceOverlay": {
            "restorePolicy": "discard-scratch-source",
            "placement": "scratch-source-materialization",
        },
        "compiledArtifacts": {
            "supported": True,
            "kind": artifact_kind,
            "placement": "swap-file",
            "restorePolicy": "restore-original-file",
        },
    }


@dataclass
class MutationArtifact:
    """A materialized artifact workspace for one mutation execution."""

    repo: str
    work_repo: str
    implementation: str
    workspace_root: str | None = None
    worker_tmp_dir: str | None = None
    worker_label: str | None = None
    retained: bool = False
    retained_reason: str | None = None

    @property
    def mode(self) -> str:
        return SOURCE_OVERLAY_MODE

    def mark_retained(self, reason: str | None = None) -> None:
        self.retained = True
        self.retained_reason = reason

    def run_metadata(self) -> dict[str, Any]:
        payload = mutation_artifact_metadata(
            self.implementation,
            worker_tmp_dir=self.worker_tmp_dir,
            retain_worktrees=self.retained,
            retain_worktrees_for=[self.retained_reason] if self.retained_reason else [],
            worker_label=self.worker_label,
        )
        payload["workRepo"] = self.work_repo
        if self.workspace_root:
            payload["workspaceRoot"] = self.workspace_root
        if self.retained:
            payload["retained"] = True
            payload["retainedPath"] = self.work_repo
            if self.retained_reason:
                payload["retainedReason"] = self.retained_reason
        else:
            payload["retained"] = False
        return payload

    def placement_metadata(self) -> dict[str, Any]:
        payload = artifact_placement_policy(
            self.implementation,
            worker_tmp_dir=self.worker_tmp_dir,
            retain_worktrees=self.retained,
            retain_worktrees_for=[self.retained_reason] if self.retained_reason else [],
            worker_label=self.worker_label,
        )
        payload["workRepo"] = self.work_repo
        if self.workspace_root:
            payload["workspaceRoot"] = self.workspace_root
        payload["originalArtifactsRestored"] = True
        payload["materializedArtifactRetained"] = self.retained
        payload["materializedArtifactRestored"] = not self.retained
        if self.retained:
            payload["retainedPath"] = self.work_repo
            if self.retained_reason:
                payload["retainedReason"] = self.retained_reason
            payload["cleanupGuidance"] = "remove retainedPath when proof capture is complete"
        return payload


@contextlib.contextmanager
def materialize_mutation_artifact(
    repo: str,
    implementation: str,
    *,
    worker_tmp_dir: str | None = None,
    retain: bool = False,
    retain_state: dict[str, bool] | None = None,
    worker_label: str | None = None,
):
    """Materialize the current source-overlay artifact implementation."""
    if implementation == "inplace":
        yield MutationArtifact(
            repo=repo,
            work_repo=repo,
            implementation=implementation,
            workspace_root=repo,
            worker_tmp_dir=worker_tmp_dir,
            worker_label=worker_label,
        )
        return

    if worker_tmp_dir:
        os.makedirs(worker_tmp_dir, exist_ok=True)
    label_prefix = f"{_safe_worker_label(worker_label)}-" if _safe_worker_label(worker_label) else ""

    if implementation == "git-worktree":
        if not os.path.isdir(os.path.join(repo, ".git")):
            raise ValueError("git-worktree mode requires --repo to point at a git work tree")

        workspace_root = tempfile.mkdtemp(prefix=f"stryker-cxx-worktree-{label_prefix}", dir=worker_tmp_dir)
        workdir = os.path.join(workspace_root, "worktree")
        artifact = MutationArtifact(
            repo=repo,
            work_repo=workdir,
            implementation=implementation,
            workspace_root=workspace_root,
            worker_tmp_dir=worker_tmp_dir,
            worker_label=worker_label,
        )
        try:
            subprocess.run(
                ["git", "-C", repo, "worktree", "add", "--detach", workdir, "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            )
            yield artifact
            if not _workspace_is_retained(retain, retain_state):
                subprocess.run(
                    ["git", "-C", repo, "worktree", "remove", "--force", workdir],
                    capture_output=True,
                    text=True,
                    check=False,
                )
        finally:
            if not _workspace_is_retained(retain, retain_state):
                shutil.rmtree(workspace_root, ignore_errors=True)
        return

    if implementation == "copy":
        workdir = tempfile.mkdtemp(prefix=f"stryker-cxx-copy-{label_prefix}", dir=worker_tmp_dir)
        artifact = MutationArtifact(
            repo=repo,
            work_repo=workdir,
            implementation=implementation,
            workspace_root=workdir,
            worker_tmp_dir=worker_tmp_dir,
            worker_label=worker_label,
        )
        try:
            shutil.copytree(repo, workdir, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
            yield artifact
        finally:
            if not _workspace_is_retained(retain, retain_state):
                shutil.rmtree(workdir, ignore_errors=True)
        return

    raise ValueError(f"unsupported mutation artifact implementation: {implementation}")
