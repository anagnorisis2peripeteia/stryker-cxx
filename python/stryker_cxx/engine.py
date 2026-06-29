#!/usr/bin/env python3
"""marmorkrebs-cxx: a source-level C++/ObjC++ mutation tester.

This module is the execution engine used by the standalone `stryker-cxx` package.
The behavior intentionally mirrors the original embedded Marmorkrebs script with
added report modes and run-time metadata to support Stryker-level workflows.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import html as html_lib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .schema import (
    REPORT_SCHEMA_VERSION,
    MTE_SCHEMA_VERSION,
    require_mte,
    require_report,
)

# Token-level mutators.
MUTATORS: dict[str, list[tuple[str, str]]] = {
    "ConditionalBoundary": [("<=", "<"), (">=", ">"), ("<", "<="), (">", ">=")],
    "EqualityOperator": [("==", "!="), ("!=", "==")],
    "LogicalOperator": [("&&", "||"), ("||", "&&")],
    "BooleanLiteral": [("true", "false"), ("false", "true")],
    "ArithmeticOperator": [("+", "-"), ("-", "+"), ("*", "/"), ("/", "*")],
    "AssignmentOperator": [("+=", "-="), ("-=", "+="), ("*=", "/="), ("/=", "*=")],
    "BitwiseOperator": [("&", "|"), ("|", "&"), ("^", "|")],
    "UnaryOperator": [("!", ""), ("!", "!!")],
    "ReturnValue": [("return true", "return false"), ("return false", "return true")],
    "IntegerLiteral": [("0", "1"), ("1", "0")],
    "NullLiteral": [("nullptr", "NULL"), ("NULL", "nullptr")],
    "CallRemoval": [],
}

MUTATOR_DESCRIPTIONS: dict[str, str] = {
    "ConditionalBoundary": "replaced conditional boundary operator",
    "EqualityOperator": "replaced equality operator",
    "LogicalOperator": "replaced boolean short-circuit operator",
    "BooleanLiteral": "swapped boolean literal",
    "ArithmeticOperator": "replaced arithmetic operator",
    "AssignmentOperator": "replaced compound assignment operator",
    "BitwiseOperator": "replaced bitwise operator",
    "UnaryOperator": "modified unary operator",
    "ReturnValue": "reversed returned boolean result",
    "IntegerLiteral": "changed a basic integer literal",
    "NullLiteral": "changed a null pointer literal",
    "CallRemoval": "removed a statement-level function call",
}

_TOKEN_PATTERNS: dict[str, str] = {
    "<=": r"<=",
    ">=": r">=",
    "==": r"==",
    "!=": r"!=",
    "&&": r"&&",
    "||": r"\|\|",
    # Bare `<`/`>` require surrounding whitespace so we avoid touching templates.
    "<": r"(?<=\s)<(?=\s)",
    ">": r"(?<=\s)>(?=\s)",
    "true": r"\btrue\b",
    "false": r"\bfalse\b",
    "+": r"(?<![+])\+(?![+=])",
    "-": r"(?<![-])-(?![->=])",
    "*": r"(?<![*/])\*(?![*/=])",
    "/": r"(?<!/)/(?!/)",
    "+=": r"\+=",
    "-=": r"-=",
    "*=": r"\*=",
    "/=": r"/=",
    "&": r"(?<![&|])&(?!(?:[&=]))",
    "|": r"(?<!\|)\|(?!(?:\||=))",
    "^": r"\^",
    "!": r"(?<![!])!(?![=])",
    "return true": r"\breturn\s+true\b",
    "return false": r"\breturn\s+false\b",
    "0": r"(?<![\w.])0(?![\w.])",
    "1": r"(?<![\w.])1(?![\w.])",
    "nullptr": r"\bnullptr\b",
    "NULL": r"\bNULL\b",
}

_CALL_REMOVAL_RE = re.compile(
    r"\b(?!if\b|for\b|while\b|switch\b|return\b|sizeof\b|catch\b)"
    r"([A-Za-z_]\w*(?:(?:\s*::\s*|\s*->\s*|\s*\.\s*)[A-Za-z_]\w*)*\s*\([^;{}]*\))(?=\s*;)"
)

SOURCE_EXTENSIONS = {
    ".cpp",
    ".cc",
    ".cxx",
    ".c",
    ".mm",
    ".m",
    ".h",
    ".hpp",
    ".hh",
    ".hxx",
}
DEFAULT_MUTATORS = ["ConditionalBoundary", "EqualityOperator", "LogicalOperator", "BooleanLiteral"]

PLUGIN_MANIFEST = "stryker-cxx-plugin.json"
REDACTED_VALUE = "[REDACTED]"
SENSITIVE_ENV_KEY_RE = re.compile(
    r"(^|_)(TOKEN|SECRET|PASSWORD|PASSWD|PWD|CREDENTIAL|API_?KEY|"
    r"ACCESS_?KEY|PRIVATE_?KEY|AUTH|BEARER)($|_)",
    re.IGNORECASE,
)
SHELL_ASSIGNMENT_RE = re.compile(
    r"(?P<prefix>(^|[\s;])(?:export\s+)?)"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)="
    r"(?P<value>'[^']*'|\"[^\"]*\"|[^\s;]+)"
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_supported_source_path(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in SOURCE_EXTENSIONS


@dataclass
class Mutant:
    mutator: str
    file: str
    line: int
    col: int
    original: str
    mutated: str
    id: str = ""
    nodeKind: str = ""
    status: str = "PENDING"  # KILLED | SURVIVED | BUILD_ERROR | CHECK_ERROR | NO_COVERAGE | TIMEOUT | IGNORED | PENDING
    detail: str = ""
    ignoreReason: str = ""
    sourceRange: dict[str, Any] = field(default_factory=dict)
    rewriteStrategy: str = ""
    durationMs: int = 0
    buildLog: str = ""
    checkLog: str = ""
    testLog: str = ""
    run: dict[str, Any] = field(default_factory=dict)


@dataclass
class Report:
    target_files: list[str]
    tool: str = "stryker-cxx"
    repo: str | None = None
    base: str | None = None
    threshold: float | None = None
    thresholds: dict[str, Any] = field(default_factory=dict)
    timeoutSeconds: int | None = None
    buildCommand: str | None = None
    checkCommand: str | None = None
    testCommand: str | None = None
    total: int = 0
    killed: int = 0
    survived: int = 0
    buildError: int = 0
    checkErrors: int = 0
    timeouts: int = 0
    ignored: int = 0
    noCoverage: int = 0
    execution: dict[str, Any] = field(default_factory=dict)
    dryRun: dict[str, Any] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)
    baseline: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    mutants: list[dict] = field(default_factory=list)
    startedAt: str = field(default_factory=_utc_now_iso)
    completedAt: str | None = None

    @property
    def totalMutants(self) -> int:
        return self.total

    @property
    def score(self) -> float:
        scored = self.killed + self.survived
        return self.killed / scored if scored else 1.0

    @property
    def scorePercent(self) -> float:
        return 100.0 * self.score

    def finalize(self) -> None:
        self.completedAt = _utc_now_iso()


FATAL_STATUSES = {"KILLED", "SURVIVED", "BUILD_ERROR", "CHECK_ERROR", "NO_COVERAGE", "TIMEOUT", "IGNORED"}
RETAINABLE_STATUSES = FATAL_STATUSES | {"RUNTIME_ERROR", "PENDING"}


@dataclass(frozen=True)
class IgnoreDirective:
    action: str
    targets: frozenset[str] | None
    reason: str
    next_line: bool = False


IGNORE_MUTATOR_ALIASES: dict[str, str] = {
    "boolean": "BooleanLiteral",
    "booleanliteral": "BooleanLiteral",
    "equality": "EqualityOperator",
    "equalityoperator": "EqualityOperator",
    "logical": "LogicalOperator",
    "logicaloperator": "LogicalOperator",
    "arithmetic": "ArithmeticOperator",
    "arithmeticoperator": "ArithmeticOperator",
    "assignment": "AssignmentOperator",
    "assignmentoperator": "AssignmentOperator",
    "bitwise": "BitwiseOperator",
    "bitwiseoperator": "BitwiseOperator",
    "unary": "UnaryOperator",
    "unaryoperator": "UnaryOperator",
    "return": "ReturnValue",
    "returnvalue": "ReturnValue",
    "call": "CallRemoval",
    "callremoval": "CallRemoval",
    "conditionalboundary": "ConditionalBoundary",
}

AST_MUTATOR_CURSOR_KINDS: dict[str, set[str]] = {
    "ConditionalBoundary": {"BINARY_OPERATOR"},
    "EqualityOperator": {"BINARY_OPERATOR"},
    "LogicalOperator": {"BINARY_OPERATOR"},
    "ArithmeticOperator": {"BINARY_OPERATOR"},
    "AssignmentOperator": {"COMPOUND_ASSIGNMENT_OPERATOR", "BINARY_OPERATOR"},
    "BitwiseOperator": {"BINARY_OPERATOR"},
    "UnaryOperator": {"UNARY_OPERATOR"},
    "BooleanLiteral": {"CXX_BOOL_LITERAL_EXPR", "OBJC_BOOL_LITERAL_EXPR"},
    "ReturnValue": {"RETURN_STMT"},
    "IntegerLiteral": {"INTEGER_LITERAL"},
    "NullLiteral": {"CXX_NULL_PTR_LITERAL_EXPR", "GNU_NULL_EXPR", "DECL_REF_EXPR"},
    "CallRemoval": {"CALL_EXPR", "CXX_MEMBER_CALL_EXPR", "OBJC_MESSAGE_EXPR"},
}
MACRO_CURSOR_KINDS = {"MACRO_INSTANTIATION", "MACRO_EXPANSION"}


def _load_plugin_manifest(path: str) -> dict[str, Any]:
    with open(path) as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"plugin manifest must be an object: {path}")
    return payload


def load_plugins(
    plugin_paths: list[str] | None = None,
    plugin_dirs: list[str] | None = None,
) -> list[dict[str, Any]]:
    manifests: list[str] = []
    for path in plugin_paths or []:
        if path:
            manifests.append(path)
    for directory in plugin_dirs or []:
        if not directory:
            continue
        manifest = os.path.join(directory, PLUGIN_MANIFEST)
        if not os.path.exists(manifest):
            raise ValueError(f"plugin directory missing {PLUGIN_MANIFEST}: {directory}")
        manifests.append(manifest)

    loaded: list[dict[str, Any]] = []
    for manifest in manifests:
        payload = _load_plugin_manifest(manifest)
        name = str(payload.get("name", os.path.basename(manifest)))
        for mutator in payload.get("mutators", []):
            if not isinstance(mutator, dict):
                raise ValueError(f"plugin {name}: mutator entries must be objects")
            mutator_name = str(mutator.get("name", "")).strip()
            replacements = mutator.get("replacements", [])
            if not mutator_name:
                raise ValueError(f"plugin {name}: mutator missing name")
            if mutator_name in MUTATORS:
                raise ValueError(f"plugin {name}: mutator already exists: {mutator_name}")
            pairs: list[tuple[str, str]] = []
            for item in replacements:
                if not isinstance(item, list) or len(item) != 2:
                    raise ValueError(
                        f"plugin {name}: replacements for {mutator_name} "
                        "must be [from, to] pairs"
                    )
                original, mutated = str(item[0]), str(item[1])
                pairs.append((original, mutated))
                _TOKEN_PATTERNS.setdefault(original, re.escape(original))
            MUTATORS[mutator_name] = pairs
            MUTATOR_DESCRIPTIONS[mutator_name] = str(mutator.get("description", f"plugin mutator {mutator_name}"))
        loaded.append(
            {
                "name": name,
                "version": str(payload.get("version", "")),
                "path": manifest,
                "capabilities": payload.get("capabilities", {}),
                "mutators": [m.get("name") for m in payload.get("mutators", []) if isinstance(m, dict)],
                "reporters": [r.get("name") for r in payload.get("reporters", []) if isinstance(r, dict)],
                "reporterCommands": [
                    {"name": r.get("name"), "command": r.get("command")}
                    for r in payload.get("reporters", [])
                    if isinstance(r, dict) and r.get("command")
                ],
                "hooks": payload.get("hooks", {}) if isinstance(payload.get("hooks", {}), dict) else {},
            }
        )
    return loaded


def _plugin_commands(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _run_plugin_command(
    command: str,
    repo: str,
    artifact_root: str,
    report_path: str,
    plugin: dict[str, Any],
    hook: str,
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
) -> None:
    os.makedirs(artifact_root, exist_ok=True)
    safe_plugin = _safe_basename(str(plugin.get("name", "plugin")))
    safe_hook = _safe_basename(hook)
    log_path = os.path.join(artifact_root, f"plugin_{safe_plugin}_{safe_hook}.log")
    env = _build_subprocess_env(
        env_overrides,
        env_inherit,
        env_block,
        {
            "STRYKER_CXX_PLUGIN": str(plugin.get("name", "")),
            "STRYKER_CXX_HOOK": hook,
            "STRYKER_CXX_REPORT": report_path,
            "STRYKER_CXX_ARTIFACT_DIR": artifact_root,
        },
    )
    with open(log_path, "a") as log:
        proc = subprocess.run(command, cwd=repo, shell=True, stdout=log, stderr=subprocess.STDOUT, env=env)
    if proc.returncode != 0:
        raise ValueError(f"plugin hook failed ({plugin.get('name')}:{hook}); see {log_path}")


def _run_plugin_hooks(
    plugins: list[dict[str, Any]],
    hook: str,
    repo: str,
    artifact_root: str,
    report_path: str,
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
) -> None:
    for plugin in plugins:
        hooks = plugin.get("hooks", {})
        if not isinstance(hooks, dict):
            continue
        for command in _plugin_commands(hooks.get(hook)):
            _run_plugin_command(
                command,
                repo,
                artifact_root,
                report_path,
                plugin,
                hook,
                env_overrides,
                env_inherit,
                env_block,
            )


def _run_reporter_plugins(
    plugins: list[dict[str, Any]],
    requested_reporters: list[str],
    repo: str,
    artifact_root: str,
    report_path: str,
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
) -> None:
    requested = set(requested_reporters)
    if not requested:
        return
    for plugin in plugins:
        for reporter in plugin.get("reporterCommands", []):
            if not isinstance(reporter, dict):
                continue
            name = str(reporter.get("name", ""))
            command = str(reporter.get("command", ""))
            if name in requested and command:
                _run_plugin_command(
                    command,
                    repo,
                    artifact_root,
                    report_path,
                    plugin,
                    f"reporter_{name}",
                    env_overrides,
                    env_inherit,
                    env_block,
                )


def _parse_env_overrides(items: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"--env must be KEY=VALUE, got: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"--env must use a non-empty key, got: {item}")
        out[key] = value
    return out


def _parse_env_names(items: list[str] | None, flag: str) -> list[str]:
    out: list[str] = []
    for item in items or []:
        for raw in item.split(","):
            name = raw.strip()
            if not name:
                continue
            if "=" in name:
                raise ValueError(f"{flag} expects environment variable names, got: {name}")
            if name not in out:
                out.append(name)
    return out


def _parse_csv_items(items: list[str] | None) -> list[str]:
    out: list[str] = []
    for item in items or []:
        for raw in item.split(","):
            value = raw.strip()
            if value and value not in out:
                out.append(value)
    return out


def _build_subprocess_env(
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    if env_inherit is None:
        env = os.environ.copy()
    else:
        env = {
            key: os.environ[key]
            for key in env_inherit
            if key in os.environ
        }
    for key in env_block or []:
        env.pop(key, None)
    env.update(env_overrides or {})
    env.update(extra or {})
    return env


def _record_environment_policy(
    target: dict[str, Any],
    env_overrides: dict[str, str] | None,
    env_inherit: list[str] | None,
    env_block: list[str] | None,
) -> None:
    target["environmentKeys"] = sorted(env_overrides or {})
    target["environmentInheritedKeys"] = (
        sorted(env_inherit) if env_inherit is not None else ["*"]
    )
    target["environmentBlockedKeys"] = sorted(env_block or [])


def _parse_retain_statuses(spec: str | None) -> set[str] | None:
    if not spec:
        return None
    statuses: set[str] = set()
    for raw in spec.split(","):
        token = raw.strip()
        if not token:
            continue
        normalized = token.replace("-", "_").upper()
        if normalized == "ALL":
            return None
        if normalized == "NONE":
            return set()
        if normalized not in RETAINABLE_STATUSES:
            allowed = ", ".join(sorted(RETAINABLE_STATUSES))
            raise ValueError(f"--retain-worktrees-for must use known statuses: {allowed}")
        statuses.add(normalized)
    return statuses


def _retain_status_names(statuses: set[str] | None) -> list[str]:
    return ["ALL"] if statuses is None else sorted(statuses)


def _should_retain_worktree(
    retain_worktrees: bool,
    retain_worktrees_for: set[str] | None,
    status: str,
    work_repo: str,
    repo: str,
) -> bool:
    if not retain_worktrees or work_repo == repo:
        return False
    if retain_worktrees_for is None:
        return True
    return status in retain_worktrees_for


def _retained_worktree_candidates(worker_tmp_dir: str | None) -> list[str]:
    if not worker_tmp_dir or not os.path.isdir(worker_tmp_dir):
        return []
    out: list[str] = []
    for name in os.listdir(worker_tmp_dir):
        if name.startswith(("stryker-cxx-copy-", "stryker-cxx-worktree-")):
            out.append(os.path.join(worker_tmp_dir, name))
    return out


def _cleanup_retained_worktrees(
    repo: str,
    worker_tmp_dir: str | None,
    ttl_hours: float | None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "enabled": ttl_hours is not None,
        "ttlHours": ttl_hours,
        "removed": 0,
        "errors": [],
    }
    if ttl_hours is None:
        return meta
    if not worker_tmp_dir:
        meta["skippedReason"] = "workerTmpDir required"
        return meta

    cutoff = time.time() - (ttl_hours * 3600)
    for root in _retained_worktree_candidates(worker_tmp_dir):
        try:
            if os.path.getmtime(root) > cutoff:
                continue
            if os.path.basename(root).startswith("stryker-cxx-worktree-"):
                workdir = os.path.join(root, "worktree")
                subprocess.run(
                    ["git", "-C", repo, "worktree", "remove", "--force", workdir],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                subprocess.run(
                    ["git", "-C", repo, "worktree", "prune"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            shutil.rmtree(root, ignore_errors=True)
            meta["removed"] += 1
        except Exception as exc:
            meta["errors"].append(f"{root}: {exc}")
    return meta


_PHASE_CAPABILITY_KEYS: dict[str, tuple[str, ...]] = {
    "build": ("buildRunner", "builder", "runner"),
    "check": ("checker", "checkRunner", "runner"),
    "test": ("testRunner", "runner"),
}


def _plugin_capability_provider(plugins: list[dict[str, Any]] | None, phase: str) -> dict[str, str] | None:
    for plugin in plugins or []:
        capabilities = plugin.get("capabilities", {})
        if not isinstance(capabilities, dict):
            continue
        for key in _PHASE_CAPABILITY_KEYS.get(phase, ()):
            capability = capabilities.get(key)
            if not isinstance(capability, dict):
                continue
            command = capability.get(f"{phase}Command") or capability.get(phase)
            if key != "runner":
                command = command or capability.get("command")
            if not command:
                continue
            return {
                "plugin": str(plugin.get("name", "")),
                "capability": key,
                "name": str(capability.get("name") or plugin.get("name") or key),
                "command": str(command),
            }
    return None


def _phase_provider_name(plugins: list[dict[str, Any]] | None, phase: str) -> str:
    provider = _plugin_capability_provider(plugins, phase)
    return provider["name"] if provider else "builtin"


def _execution_provider_summary(plugins: list[dict[str, Any]] | None) -> dict[str, Any]:
    phases: dict[str, str] = {}
    for phase in ("build", "check", "test"):
        provider = _plugin_capability_provider(plugins, phase)
        phases[phase] = provider["name"] if provider else "builtin"
    return {"phases": phases}


def _plugin_coverage_source(
    repo: str,
    artifact_root: str,
    plugins: list[dict[str, Any]] | None,
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
) -> tuple[str | None, str | None, dict[str, Any]]:
    for plugin in plugins or []:
        capabilities = plugin.get("capabilities", {})
        if not isinstance(capabilities, dict):
            continue
        capability = capabilities.get("coverageProvider") or capabilities.get("coverage")
        if not isinstance(capability, dict):
            continue

        provider_name = str(capability.get("name") or plugin.get("name") or "plugin")
        coverage_file = capability.get("coverageFile") or capability.get("file")
        command = capability.get("command")
        log_path = os.path.join(artifact_root, f"coverage_{_safe_basename(provider_name)}.log")
        if command:
            os.makedirs(artifact_root, exist_ok=True)
            output_file = str(
                coverage_file
                or capability.get("outputFile")
                or os.path.join(artifact_root, f"coverage_{_safe_basename(provider_name)}.json")
            )
            env = _build_subprocess_env(
                env_overrides,
                env_inherit,
                env_block,
                {
                    "STRYKER_CXX_COVERAGE_FILE": output_file,
                    "STRYKER_CXX_COVERAGE_PROVIDER": provider_name,
                    "STRYKER_CXX_ARTIFACT_DIR": artifact_root,
                    "STRYKER_CXX_REPO": repo,
                },
            )
            with open(log_path, "w") as log:
                proc = subprocess.run(str(command), cwd=repo, shell=True, stdout=log, stderr=subprocess.STDOUT, env=env)
            if proc.returncode != 0:
                raise ValueError(f"coverage provider failed ({provider_name}); see {log_path}")
            coverage_file = output_file

        if coverage_file:
            return str(coverage_file), provider_name, {
                "plugin": str(plugin.get("name", "")),
                "capability": "coverageProvider",
                "log": log_path if command else "",
            }
    return None, None, {}


def normalize_mutator_list(raw: str) -> list[str]:
    vals = [v.strip() for v in (raw or "").split(",") if v.strip()]
    unknown = [v for v in vals if v not in MUTATORS]
    if unknown:
        raise ValueError(f"unknown mutators: {unknown}")
    return vals


def _normalize_ignore_target(raw: str) -> str:
    key = re.sub(r"[^A-Za-z0-9]", "", raw).lower()
    return IGNORE_MUTATOR_ALIASES.get(key, raw.strip())


def _extract_stryker_comment(raw: str) -> str | None:
    candidates: list[str] = []
    line_idx = raw.find("//")
    if line_idx >= 0:
        candidates.append(raw[line_idx + 2 :])
    block_idx = raw.find("/*")
    if block_idx >= 0:
        end_idx = raw.find("*/", block_idx + 2)
        candidates.append(raw[block_idx + 2 : end_idx if end_idx >= 0 else len(raw)])
    for candidate in candidates:
        if "Stryker" in candidate:
            return candidate.strip()
    return None


def _parse_ignore_directive(raw: str) -> IgnoreDirective | None:
    comment = _extract_stryker_comment(raw)
    if not comment:
        return None

    match = re.search(r"\bStryker\s+(disable|restore)\b(.*)$", comment, re.IGNORECASE)
    if not match:
        return None

    action = match.group(1).lower()
    rest = match.group(2).strip()
    next_line = False
    lowered = rest.lower()
    for marker in ("next-line", "once"):
        if lowered.startswith(marker):
            next_line = True
            rest = rest[len(marker) :].strip()
            lowered = rest.lower()
            break

    if ":" in rest:
        target_text, reason = rest.split(":", 1)
    else:
        target_text, reason = rest, ""

    target_text = target_text.strip()
    reason = reason.strip()
    if not target_text or target_text.lower() == "all":
        targets = None
    else:
        targets = frozenset(
            _normalize_ignore_target(target)
            for target in target_text.split(",")
            if target.strip()
        )
        if not targets:
            targets = None

    return IgnoreDirective(action=action, targets=targets, reason=reason, next_line=next_line)


def _directive_matches(directive: IgnoreDirective, mutator: str) -> bool:
    if directive.targets is None:
        return True
    return mutator in directive.targets or mutator.lower() in {target.lower() for target in directive.targets}


def _ignore_reason(mutator: str, directives: list[IgnoreDirective]) -> str | None:
    reason: str | None = None
    for directive in directives:
        if not _directive_matches(directive, mutator):
            continue
        if directive.action == "disable":
            reason = directive.reason or "ignored by Stryker disable comment"
        elif directive.action == "restore":
            reason = None
    return reason


def _clang_file_name(location: Any) -> str:
    file_obj = getattr(location, "file", None)
    return str(getattr(file_obj, "name", "") or "")


def _collect_clang_cursor_ranges(cursor: Any, full_path: str) -> list[dict[str, int | str]]:
    out: list[dict[str, int | str]] = []

    def visit(node: Any) -> None:
        try:
            children = list(node.get_children())
        except Exception:
            children = []

        extent = getattr(node, "extent", None)
        start = getattr(extent, "start", None)
        end = getattr(extent, "end", None)
        kind = getattr(getattr(node, "kind", None), "name", "")
        try:
            start_file = os.path.abspath(_clang_file_name(start))
        except Exception:
            start_file = ""
        if kind and start and end and start_file == full_path:
            out.append(
                {
                    "kind": kind,
                    "startLine": int(getattr(start, "line", 0) or 0),
                    "startColumn": int(getattr(start, "column", 0) or 0),
                    "endLine": int(getattr(end, "line", 0) or 0),
                    "endColumn": int(getattr(end, "column", 0) or 0),
                }
            )

        for child in children:
            visit(child)

    visit(cursor)
    return out


def _clang_range_contains(
    item: dict[str, int | str],
    line: int,
    start_col0: int,
    end_col0: int,
) -> bool:
    start_line = int(item.get("startLine", 0) or 0)
    end_line = int(item.get("endLine", 0) or 0)
    start_col = int(item.get("startColumn", 0) or 0)
    end_col = int(item.get("endColumn", 0) or 0)
    start_col1 = max(1, start_col0 + 1)
    end_col1 = max(start_col1, end_col0 + 1)

    if start_line <= 0 or end_line <= 0:
        return False
    if line < start_line or line > end_line:
        return False
    if line == start_line and start_col and start_col1 < start_col:
        return False
    if line == end_line and end_col and end_col1 > end_col:
        return False
    return True


def _clang_matching_kinds(
    ranges: list[dict[str, int | str]],
    line: int,
    col: int,
    original: str,
) -> list[str]:
    end_col = col + max(len(original), 1)
    matches = [
        item
        for item in ranges
        if _clang_range_contains(item, line, col, end_col)
    ]
    matches.sort(
        key=lambda item: (
            int(item.get("endLine", 0) or 0) - int(item.get("startLine", 0) or 0),
            int(item.get("endColumn", 0) or 0) - int(item.get("startColumn", 0) or 0),
        )
    )
    return [str(item["kind"]) for item in matches if item.get("kind")]


def _clang_mutation_is_ast_confirmed(mutator: str, kinds: list[str]) -> bool:
    allowed = AST_MUTATOR_CURSOR_KINDS.get(mutator, set())
    return any(kind in allowed for kind in kinds)


def _clang_primary_node_kind(mutator: str, kinds: list[str]) -> str:
    allowed = AST_MUTATOR_CURSOR_KINDS.get(mutator, set())
    for kind in kinds:
        if kind in allowed:
            return kind
    return kinds[0] if kinds else ""


def _clang_macro_ranges(ranges: list[dict[str, int | str]]) -> list[dict[str, int | str]]:
    return [
        item
        for item in ranges
        if str(item.get("kind", "")) in MACRO_CURSOR_KINDS
    ]


def _candidate_macro_range(
    macro_ranges: list[dict[str, int | str]],
    mut: Mutant,
) -> dict[str, int | str] | None:
    end_col = mut.col + max(len(mut.original), 1)
    for item in macro_ranges:
        if _clang_range_contains(item, mut.line, mut.col, end_col):
            return item
    return None


def _record_macro_rejection(
    analysis: dict[str, Any] | None,
    path: str,
    mut: Mutant,
    macro_range: dict[str, int | str],
) -> None:
    if analysis is None:
        return
    analysis["macroRejectedMutants"] = int(analysis.get("macroRejectedMutants", 0)) + 1
    rejections = analysis.setdefault("macroRejections", [])
    if isinstance(rejections, list):
        rejections.append(
            {
                "file": path,
                "line": mut.line,
                "column": mut.col + 1,
                "mutator": mut.mutator,
                "nodeKind": mut.nodeKind,
                "reason": "candidate overlaps a macro expansion range",
                "macroRange": dict(macro_range),
            }
        )


def _rejects_macro_candidate(
    analysis: dict[str, Any] | None,
    path: str,
    macro_ranges: list[dict[str, int | str]],
    mut: Mutant,
) -> bool:
    macro_range = _candidate_macro_range(macro_ranges, mut)
    if macro_range is None:
        return False
    _record_macro_rejection(analysis, path, mut, macro_range)
    return True


def _strip_noncode(line: str, in_block_comment: bool = False) -> tuple[str, bool]:
    """Blank out // comments and "string"/'c' literals so we never mutate them."""
    if line.lstrip().startswith("#"):
        return " " * len(line), in_block_comment

    i = 0
    out: list[str] = []
    n = len(line)
    while i < n:
        if in_block_comment:
            end = line.find("*/", i)
            if end == -1:
                out.append(" " * (n - i))
                return "".join(out), True
            out.append(" " * (end + 2 - i))
            i = end + 2
            in_block_comment = False
            continue

        if line[i : i + 2] == "/*":
            end = line.find("*/", i + 2)
            if end == -1:
                out.append(" " * (n - i))
                return "".join(out), True
            out.append(" " * (end + 2 - i))
            i = end + 2
            continue

        if line[i : i + 2] == "//":
            out.append(" " * (n - i))
            break

        if line[i] == '"':
            end = i + 1
            while end < n:
                if line[end] == "\\":
                    end += 2
                    continue
                if line[end] == '"':
                    end += 1
                    break
                end += 1
            out.append(" " * (end - i))
            i = end
            continue

        if line[i] == "'":
            end = i + 1
            while end < n:
                if line[end] == "\\":
                    end += 2
                    continue
                if line[end] == "'":
                    end += 1
                    break
                end += 1
            out.append(" " * (end - i))
            i = end
            continue

        out.append(line[i])
        i += 1

    return "".join(out), in_block_comment


def _discover_call_removals(path: str, line: int, code: str, raw: str) -> list[Mutant]:
    out: list[Mutant] = []
    for match in re.finditer(_CALL_REMOVAL_RE, code):
        if code[: match.start()].strip():
            continue
        original = raw[match.start(1) : match.end(1)]
        if not original.strip():
            continue
        mut = Mutant("CallRemoval", path, line, match.start(1), original, "(void)0")
        mut.id = stable_id(mut)
        out.append(mut)
    return out


def _apply_stryker_ignore_comments(repo: str, path: str, mutants: list[Mutant]) -> list[Mutant]:
    if not mutants:
        return mutants

    try:
        with open(os.path.join(repo, path)) as f:
            src = f.readlines()
    except OSError:
        return mutants

    directives_by_line: dict[int, list[IgnoreDirective]] = {}
    active: list[IgnoreDirective] = []
    next_line: dict[int, list[IgnoreDirective]] = {}
    for line_no, raw in enumerate(src, start=1):
        directive = _parse_ignore_directive(raw)
        if directive:
            if directive.next_line:
                next_line.setdefault(line_no + 1, []).append(directive)
            else:
                active.append(directive)
        directives_by_line[line_no] = active[:] + next_line.get(line_no, [])

    for mut in mutants:
        reason = _ignore_reason(mut.mutator, directives_by_line.get(mut.line, []))
        if reason is not None:
            mut.status = "IGNORED"
            mut.detail = reason
            mut.ignoreReason = reason
    return mutants


def parse_lines(spec: str) -> set[int]:
    """Parse '409-545,1493-1540' into a set of line numbers."""
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def _quote_for_shell(value: str) -> str:
    return shlex.quote(value)


def changed_lines(repo: str, diff_base: str, path: str) -> set[int]:
    """Line numbers added/changed in `path` vs diff_base (the new-file side)."""
    out = subprocess.run(
        ["git", "-C", repo, "diff", "--unified=0", diff_base, "--", path],
        capture_output=True,
        text=True,
    ).stdout
    lines, cur = set(), 0
    for ln in out.splitlines():
        m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", ln)
        if m:
            cur = int(m.group(1))
            continue
        if ln.startswith("+") and not ln.startswith("+++"):
            lines.add(cur)
            cur += 1
        elif not ln.startswith("-"):
            cur += 1
    return lines


def mutation_repro_command(mut: Mutant, repo: str, build_cmd: str, test_cmd: str, report: str | None = None) -> str:
    return (
        f"stryker-cxx run-mutant --repo {_quote_for_shell(repo)} "
        f"--id {_quote_for_shell(mut.id)} "
        f"--build-command {_quote_for_shell(build_cmd)} "
        f"--test-command {_quote_for_shell(test_cmd)} "
        f"--report {_quote_for_shell(report or os.path.join(repo, 'mutation.json'))} "
        "--output-format stryker-cxx"
    )


def discover(repo: str, path: str, only: set[int] | None, enabled: list[str]) -> list[Mutant]:
    if not _ensure_supported_source_path(path):
        return []

    full = os.path.join(repo, path)
    with open(full) as f:
        src = f.readlines()
    in_block_comment = False
    muts: list[Mutant] = []
    for i, raw in enumerate(src, start=1):
        if only is not None and i not in only:
            continue
        code, in_block_comment = _strip_noncode(raw, in_block_comment=in_block_comment)
        for mutator in enabled:
            for orig, new in MUTATORS[mutator]:
                pattern = _TOKEN_PATTERNS.get(orig)
                if pattern is None:
                    continue
                for m in re.finditer(pattern, code):
                    mut = Mutant(mutator, path, i, m.start(), orig, new)
                    mut.id = stable_id(mut)
                    muts.append(mut)
        if "CallRemoval" in enabled:
            muts.extend(_discover_call_removals(path, i, code, raw))
    return _apply_stryker_ignore_comments(repo, path, muts)


def stable_id(mut: Mutant) -> str:
    raw = f"{mut.file}:{mut.line}:{mut.col}:{mut.mutator}:{mut.original}:{mut.mutated}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{mut.file}:{mut.line}:{mut.col}:{mut.mutator}:{digest}"


def apply_mutant(repo: str, mut: Mutant) -> str:
    full = os.path.join(repo, mut.file)
    with open(full) as f:
        src = f.readlines()
    original = src[mut.line - 1]
    span = len(mut.original)
    src[mut.line - 1] = original[:mut.col] + mut.mutated + original[mut.col + span :]
    with open(full, "w") as f:
        f.writelines(src)
    return original


def restore(repo: str, path: str, line: int, original: str) -> None:
    full = os.path.join(repo, path)
    with open(full) as f:
        src = f.readlines()
    src[line - 1] = original
    with open(full, "w") as f:
        f.writelines(src)


def run_cmd(
    cmd: str,
    repo: str,
    log: str,
    timeout: int | None = None,
    phase: str | None = None,
    plugins: list[dict[str, Any]] | None = None,
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
) -> tuple[int, int]:
    provider = _plugin_capability_provider(plugins, phase) if phase else None
    actual_cmd = provider["command"] if provider else cmd
    start = time.perf_counter()
    try:
        with open(log, "w") as f:
            extra_env: dict[str, str] = {}
            if provider:
                extra_env = {
                    "STRYKER_CXX_PHASE": phase or "",
                    "STRYKER_CXX_PROVIDER": provider["name"],
                    "STRYKER_CXX_ORIGINAL_COMMAND": cmd,
                    "STRYKER_CXX_COMMAND": cmd,
                    "STRYKER_CXX_LOG": log,
                    "STRYKER_CXX_REPO": repo,
                }
            env = _build_subprocess_env(env_overrides, env_inherit, env_block, extra_env)
            proc = subprocess.run(actual_cmd, cwd=repo, shell=True, stdout=f, stderr=subprocess.STDOUT, timeout=timeout, env=env)
        status = proc.returncode
    except subprocess.TimeoutExpired:
        status = 124
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return status, elapsed_ms


def _dry_run(
    build_cmd: str,
    check_cmd: str | None,
    test_cmd: str | None,
    repo: str,
    artifact_root: str,
    timeout_seconds: int | None,
    skip_tests: bool,
    plugins: list[dict[str, Any]] | None = None,
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
) -> dict[str, Any]:
    os.makedirs(artifact_root, exist_ok=True)
    build_log = os.path.join(artifact_root, "dry_run_build.log")
    check_log = os.path.join(artifact_root, "dry_run_check.log")
    test_log = os.path.join(artifact_root, "dry_run_test.log")
    result: dict[str, Any] = {
        "status": "PASSED",
        "artifacts": {"buildLog": build_log, "checkLog": check_log, "testLog": test_log},
    }

    build_rc, build_ms = run_cmd(
        build_cmd,
        repo,
        build_log,
        timeout_seconds,
        "build",
        plugins,
        env_overrides,
        env_inherit,
        env_block,
    )
    result["build"] = {"exitCode": build_rc, "durationMs": build_ms, "log": build_log, "provider": _phase_provider_name(plugins, "build")}
    if build_rc == 124:
        result["status"] = "FAILED"
        result["failureReason"] = "initial build timed out"
        return result
    if build_rc != 0:
        result["status"] = "FAILED"
        result["failureReason"] = "initial build failed"
        return result

    if check_cmd:
        check_rc, check_ms = run_cmd(
            check_cmd,
            repo,
            check_log,
            timeout_seconds,
            "check",
            plugins,
            env_overrides,
            env_inherit,
            env_block,
        )
        result["check"] = {"exitCode": check_rc, "durationMs": check_ms, "log": check_log, "provider": _phase_provider_name(plugins, "check")}
        if check_rc == 124:
            result["status"] = "FAILED"
            result["failureReason"] = "initial check timed out"
            return result
        if check_rc != 0:
            result["status"] = "FAILED"
            result["failureReason"] = "initial check failed"
            return result

    if not skip_tests:
        if not test_cmd:
            result["status"] = "FAILED"
            result["failureReason"] = "initial tests missing"
            return result
        test_rc, test_ms = run_cmd(
            test_cmd,
            repo,
            test_log,
            timeout_seconds,
            "test",
            plugins,
            env_overrides,
            env_inherit,
            env_block,
        )
        result["test"] = {"exitCode": test_rc, "durationMs": test_ms, "log": test_log, "provider": _phase_provider_name(plugins, "test")}
        if test_rc == 124:
            result["status"] = "FAILED"
            result["failureReason"] = "initial tests timed out"
        elif test_rc != 0:
            result["status"] = "FAILED"
            result["failureReason"] = "initial tests failed"
    return result


def _effective_timeout_ms(
    fixed_timeout_seconds: int | None,
    dry_run: dict[str, Any],
    timeout_factor: float,
    timeout_constant_ms: int,
) -> int | None:
    if fixed_timeout_seconds is not None:
        return max(0, fixed_timeout_seconds * 1000)
    if dry_run.get("status") != "PASSED":
        return None
    for phase in ("test", "check", "build"):
        item = dry_run.get(phase)
        duration_ms = int(item.get("durationMs", 0) or 0) if isinstance(item, dict) else 0
        if duration_ms > 0:
            return int(math.ceil((duration_ms * timeout_factor) + timeout_constant_ms))
    return int(timeout_constant_ms)


def _timeout_seconds_from_ms(timeout_ms: int | None) -> int | None:
    if timeout_ms is None:
        return None
    if timeout_ms <= 0:
        return None
    return max(1, int(math.ceil(timeout_ms / 1000)))


def _validate_threshold_value(name: str, value: float | None) -> None:
    if value is None:
        return
    if not (0.0 <= float(value) <= 1.0):
        raise ValueError(f"--{name} must be in [0.0, 1.0]")


def _resolve_thresholds(
    legacy_threshold: float | None,
    threshold_high: float | None,
    threshold_low: float | None,
    threshold_break: float | None,
    score: float,
) -> dict[str, Any]:
    break_score = threshold_break if threshold_break is not None else legacy_threshold
    if break_score is None:
        break_score = 1.0
    low_score = threshold_low if threshold_low is not None else break_score
    high_score = threshold_high if threshold_high is not None else low_score
    if high_score < low_score or low_score < break_score:
        raise ValueError("thresholds must satisfy high >= low >= break")
    if score < break_score:
        status = "failed"
    elif score < low_score:
        status = "low"
    elif score < high_score:
        status = "acceptable"
    else:
        status = "high"
    return {
        "high": float(high_score),
        "low": float(low_score),
        "break": float(break_score),
        "status": status,
    }


def _repo_relative(repo: str, path: str) -> str:
    normalized = os.path.normpath(path)
    if os.path.isabs(normalized):
        try:
            return os.path.relpath(normalized, repo)
        except ValueError:
            return normalized
    return normalized


def _load_coverage(
    repo: str,
    coverage_file: str | None,
    provider: str | None = None,
    plugins: list[dict[str, Any]] | None = None,
    artifact_root: str | None = None,
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
    helper_command_template: str | None = None,
    helper_tests: list[str] | None = None,
) -> tuple[dict[str, set[int]], dict[str, dict[int, list[str]]], dict[str, Any]]:
    plugin_meta: dict[str, Any] = {}
    if not coverage_file and artifact_root:
        coverage_file, plugin_provider, plugin_meta = _plugin_coverage_source(
            repo,
            artifact_root,
            plugins,
            env_overrides,
            env_inherit,
            env_block,
        )
        if plugin_provider and not provider:
            provider = plugin_provider
    helper_tests = helper_tests or []
    if helper_command_template and not helper_tests:
        raise ValueError("--coverage-helper-command-template requires --coverage-helper-tests")
    if not coverage_file and not helper_command_template:
        return {}, {}, {"provider": provider or "none", "enabled": False}

    coverage: dict[str, set[int]] = {}
    coverage_tests: dict[str, dict[int, list[str]]] = {}
    if coverage_file:
        path = coverage_file if os.path.isabs(coverage_file) else os.path.join(repo, coverage_file)
        if not os.path.exists(path):
            raise ValueError(f"coverage file not found: {coverage_file}")
        with open(path) as f:
            raw = f.read()

        stripped = raw.lstrip()
        if stripped.startswith("{"):
            payload = json.loads(raw)
            _merge_json_coverage(repo, payload, coverage, coverage_tests)
            provider_name = provider or str(payload.get("provider", "json") if isinstance(payload, dict) else "json")
        else:
            _merge_lcov(repo, raw, coverage)
            provider_name = provider or "lcov"
        path_meta: str | None = path
    else:
        provider_name = provider or "coverage-helper"
        path_meta = None

    helper_meta: dict[str, Any] = {}
    if helper_command_template:
        helper_meta = _run_coverage_helpers(
            repo,
            artifact_root or os.path.join(repo, "agent_space", "stryker-cxx"),
            helper_command_template,
            helper_tests,
            coverage,
            coverage_tests,
            env_overrides,
            env_inherit,
            env_block,
        )

    meta = {
        "provider": provider_name,
        "enabled": True,
        "path": path_meta,
        "files": len(coverage),
    }
    if plugin_meta:
        meta["plugin"] = plugin_meta
    if helper_meta:
        meta["helper"] = helper_meta
    if coverage_tests:
        meta["testLevel"] = True
        meta["testMappedFiles"] = len(coverage_tests)
    return coverage, coverage_tests, meta


def _format_coverage_helper_command(
    template: str,
    test_name: str,
    coverage_file: str,
) -> str:
    quoted_test = shlex.quote(test_name)
    quoted_file = shlex.quote(coverage_file)
    return (
        template
        .replace("{test}", quoted_test)
        .replace("{test_shell}", quoted_test)
        .replace("{coverage_file}", quoted_file)
        .replace("{coverageFile}", quoted_file)
    )


def _merge_helper_coverage(
    repo: str,
    raw: str,
    test_name: str,
    coverage: dict[str, set[int]],
    coverage_tests: dict[str, dict[int, list[str]]],
) -> None:
    helper_coverage: dict[str, set[int]] = {}
    helper_tests: dict[str, dict[int, list[str]]] = {}
    stripped = raw.lstrip()
    if stripped.startswith("{"):
        _merge_json_coverage(repo, json.loads(raw), helper_coverage, helper_tests)
    else:
        _merge_lcov(repo, raw, helper_coverage)

    for file_name, lines in helper_coverage.items():
        coverage.setdefault(file_name, set()).update(lines)
        for line in lines:
            _add_covered_tests(coverage_tests, repo, file_name, line, [test_name])
    for file_name, by_line in helper_tests.items():
        for line, tests in by_line.items():
            _add_covered_tests(coverage_tests, repo, file_name, line, tests or [test_name])


def _run_coverage_helpers(
    repo: str,
    artifact_root: str,
    command_template: str,
    tests: list[str],
    coverage: dict[str, set[int]],
    coverage_tests: dict[str, dict[int, list[str]]],
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
) -> dict[str, Any]:
    os.makedirs(artifact_root, exist_ok=True)
    generated_files: list[str] = []
    logs: list[str] = []
    for index, test_name in enumerate(tests, start=1):
        safe_name = _safe_basename(test_name)
        coverage_file = os.path.join(artifact_root, f"coverage_helper_{index}_{safe_name}.json")
        log_path = os.path.join(artifact_root, f"coverage_helper_{index}_{safe_name}.log")
        command = _format_coverage_helper_command(command_template, test_name, coverage_file)
        env = _build_subprocess_env(
            env_overrides,
            env_inherit,
            env_block,
            {
                "STRYKER_CXX_COVERAGE_TEST": test_name,
                "STRYKER_CXX_COVERAGE_FILE": coverage_file,
                "STRYKER_CXX_ARTIFACT_DIR": artifact_root,
                "STRYKER_CXX_REPO": repo,
            },
        )
        with open(log_path, "w") as log:
            proc = subprocess.run(command, cwd=repo, shell=True, stdout=log, stderr=subprocess.STDOUT, env=env)
        if proc.returncode != 0:
            raise ValueError(f"coverage helper failed for {test_name}; see {log_path}")
        if not os.path.exists(coverage_file):
            raise ValueError(f"coverage helper did not write coverage file for {test_name}: {coverage_file}")
        with open(coverage_file) as f:
            _merge_helper_coverage(repo, f.read(), test_name, coverage, coverage_tests)
        generated_files.append(coverage_file)
        logs.append(log_path)

    return {
        "enabled": True,
        "template": command_template,
        "tests": tests,
        "testCount": len(tests),
        "generatedFiles": generated_files,
        "logs": logs,
    }


def _add_covered_line(coverage: dict[str, set[int]], repo: str, file_name: str, line: int) -> None:
    if line <= 0:
        return
    key = _repo_relative(repo, file_name)
    coverage.setdefault(key, set()).add(line)


def _add_covered_tests(
    coverage_tests: dict[str, dict[int, list[str]]],
    repo: str,
    file_name: str,
    line: int,
    tests: Any,
) -> None:
    if line <= 0:
        return
    if isinstance(tests, str):
        values = [tests]
    elif isinstance(tests, list):
        values = [str(item) for item in tests if str(item).strip()]
    else:
        return
    if not values:
        return
    key = _repo_relative(repo, file_name)
    coverage_tests.setdefault(key, {}).setdefault(line, [])
    existing = coverage_tests[key][line]
    for value in values:
        if value not in existing:
            existing.append(value)


def _merge_json_coverage(
    repo: str,
    payload: Any,
    coverage: dict[str, set[int]],
    coverage_tests: dict[str, dict[int, list[str]]],
) -> None:
    if not isinstance(payload, dict):
        return

    files = payload.get("files")
    if isinstance(files, dict):
        for file_name, entry in files.items():
            coverage.setdefault(_repo_relative(repo, str(file_name)), set())
            if isinstance(entry, dict):
                lines = entry.get("coveredLines", entry.get("lines", []))
            else:
                lines = entry
            if isinstance(lines, list):
                for line in lines:
                    if isinstance(line, int):
                        _add_covered_line(coverage, repo, str(file_name), line)
                    elif isinstance(line, dict):
                        line_no = int(line.get("line", 0) or 0)
                        count = int(line.get("count", 1) or 0)
                        if count > 0:
                            _add_covered_line(coverage, repo, str(file_name), line_no)
                            _add_covered_tests(
                                coverage_tests,
                                repo,
                                str(file_name),
                                line_no,
                                line.get("coveredBy", line.get("tests")),
                            )
            if isinstance(lines, dict):
                for raw_line, line_entry in lines.items():
                    try:
                        line_no = int(raw_line)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(line_entry, dict):
                        count = int(line_entry.get("count", 1) or 0)
                        if count > 0:
                            _add_covered_line(coverage, repo, str(file_name), line_no)
                            _add_covered_tests(
                                coverage_tests,
                                repo,
                                str(file_name),
                                line_no,
                                line_entry.get("coveredBy", line_entry.get("tests")),
                            )
                    elif isinstance(line_entry, int) and line_entry > 0:
                        _add_covered_line(coverage, repo, str(file_name), line_no)
            for key in ("coveredTests", "testsByLine"):
                tests_by_line = entry.get(key) if isinstance(entry, dict) else None
                if isinstance(tests_by_line, dict):
                    for raw_line, tests in tests_by_line.items():
                        try:
                            line_no = int(raw_line)
                        except (TypeError, ValueError):
                            continue
                        _add_covered_line(coverage, repo, str(file_name), line_no)
                        _add_covered_tests(coverage_tests, repo, str(file_name), line_no, tests)

    # llvm-cov export JSON shape: data[].files[].segments = [line, col, count, ...]
    data = payload.get("data")
    if isinstance(data, list):
        for module in data:
            if not isinstance(module, dict):
                continue
            for file_entry in module.get("files", []):
                if not isinstance(file_entry, dict):
                    continue
                file_name = str(file_entry.get("filename", ""))
                for segment in file_entry.get("segments", []):
                    if isinstance(segment, list) and len(segment) >= 3:
                        line = int(segment[0] or 0)
                        count = int(segment[2] or 0)
                        if count > 0:
                            _add_covered_line(coverage, repo, file_name, line)


def _merge_lcov(repo: str, raw: str, coverage: dict[str, set[int]]) -> None:
    current = ""
    for line in raw.splitlines():
        if line.startswith("SF:"):
            current = line[3:].strip()
            continue
        if line.startswith("DA:") and current:
            fields = line[3:].split(",", 1)
            if len(fields) != 2:
                continue
            line_no = int(fields[0] or 0)
            count = int(fields[1].split(",", 1)[0] or 0)
            if count > 0:
                _add_covered_line(coverage, repo, current, line_no)
        elif line == "end_of_record":
            current = ""


def _covered_lines_for(coverage: dict[str, set[int]], repo: str, file_name: str) -> set[int] | None:
    candidates = [
        _repo_relative(repo, file_name),
        os.path.normpath(file_name),
        os.path.abspath(os.path.join(repo, file_name)),
    ]
    for candidate in candidates:
        if candidate in coverage:
            return coverage[candidate]
    return None


def _covered_tests_for(coverage_tests: dict[str, dict[int, list[str]]], repo: str, file_name: str, line: int) -> list[str]:
    candidates = [
        _repo_relative(repo, file_name),
        os.path.normpath(file_name),
        os.path.abspath(os.path.join(repo, file_name)),
    ]
    for candidate in candidates:
        by_line = coverage_tests.get(candidate)
        if by_line and line in by_line:
            return list(by_line[line])
    return []


def _coverage_selected_test_command(template: str, tests: list[str]) -> str:
    csv = ",".join(tests)
    space = " ".join(shlex.quote(test) for test in tests)
    return (
        template
        .replace("{tests}", shlex.quote(csv))
        .replace("{tests_csv}", shlex.quote(csv))
        .replace("{tests_space}", space)
        .replace("{first_test}", shlex.quote(tests[0] if tests else ""))
    )


def _file_hash(repo: str, file_name: str) -> str:
    path = os.path.join(repo, file_name)
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def _stable_json_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _baseline_config_hash(args: argparse.Namespace, enabled: list[str]) -> str:
    return _stable_json_hash(
        {
            "version": REPORT_SCHEMA_VERSION,
            "mode": args.mode,
            "mutators": enabled,
            "buildCommand": args.build_cmd,
            "checkCommand": args.check_cmd,
            "testCommand": args.test_cmd,
            "skipTests": args.skip_tests,
            "coverageFile": args.coverage_file,
            "coverageProvider": args.coverage_provider,
            "coverageTestCommandTemplate": args.coverage_test_command_template,
            "coverageHelperCommandTemplate": args.coverage_helper_command_template,
            "coverageHelperTests": _parse_csv_items(args.coverage_helper_tests),
            "timeoutSeconds": args.timeout_seconds,
            "timeoutFactor": args.timeout_factor,
            "timeoutConstantMs": args.timeout_constant_ms,
        }
    )


def _baseline_key(mut: Mutant, repo: str, config_hash: str) -> str:
    return _stable_json_hash(
        {
            "id": mut.id,
            "file": mut.file,
            "line": mut.line,
            "column": mut.col,
            "mutator": mut.mutator,
            "original": mut.original,
            "mutated": mut.mutated,
            "sourceHash": _file_hash(repo, mut.file),
            "configHash": config_hash,
        }
    )


def _load_baseline(path: str | None) -> dict[str, dict[str, Any]]:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            payload = json.load(f)
    except Exception:
        return {}
    entries = payload.get("entries", {}) if isinstance(payload, dict) else {}
    if not isinstance(entries, dict):
        return {}
    return {str(k): v for k, v in entries.items() if isinstance(v, dict)}


def _write_baseline(path: str, entries: dict[str, dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    payload = {
        "schemaVersion": "stryker-cxx.baseline.v1",
        "tool": "stryker-cxx",
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "entries": entries,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)


def _baseline_entry(mutant_record: dict[str, Any]) -> dict[str, Any]:
    entry = {
        "status": mutant_record.get("status"),
        "mutant": mutant_record,
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    branch = mutant_record.get("baselineBranch")
    if isinstance(branch, str) and branch:
        entry["branch"] = branch
    return entry


def _parse_baseline_updated_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _baseline_reuse_rejection(cached: Any, args: argparse.Namespace) -> str | None:
    if not isinstance(cached, dict):
        return "missing entry"
    cached_mutant = cached.get("mutant")
    if not isinstance(cached_mutant, dict):
        return "missing mutant result"
    status = str(cached_mutant.get("status", "")).upper()
    if status not in FATAL_STATUSES:
        return f"non-terminal status {status or 'missing'}"
    if args.baseline_branch:
        branch = cached.get("branch")
        if branch != args.baseline_branch:
            return f"branch mismatch {branch or 'none'}"
    if args.baseline_max_age_days is not None:
        updated_at = _parse_baseline_updated_at(cached.get("updatedAt"))
        if updated_at is None:
            return "missing updatedAt"
        max_age_seconds = int(args.baseline_max_age_days) * 24 * 60 * 60
        age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
        if age_seconds > max_age_seconds:
            return f"older than {args.baseline_max_age_days}d"
    return None


def _count_status(rep: Report, status: str) -> None:
    if status == "KILLED":
        rep.killed += 1
    elif status == "SURVIVED":
        rep.survived += 1
    elif status == "BUILD_ERROR":
        rep.buildError += 1
    elif status == "CHECK_ERROR":
        rep.checkErrors += 1
    elif status == "NO_COVERAGE":
        rep.noCoverage += 1
    elif status == "TIMEOUT":
        rep.timeouts += 1
    elif status == "IGNORED":
        rep.ignored += 1


def _single_line_source_range(
    src: list[str],
    item: dict[str, int | str],
) -> tuple[int, int, str] | None:
    start_line = int(item.get("startLine", 0) or 0)
    end_line = int(item.get("endLine", 0) or 0)
    start_col = int(item.get("startColumn", 0) or 0)
    end_col = int(item.get("endColumn", 0) or 0)
    if start_line <= 0 or end_line <= 0 or start_line != end_line:
        return None
    if start_line > len(src) or start_col <= 0 or end_col <= start_col:
        return None
    line = src[start_line - 1].rstrip("\n")
    col0 = max(0, start_col - 1)
    end0 = min(len(line), max(col0, end_col - 1))
    text = line[col0:end0]
    if not text.strip():
        return None
    return start_line, col0, text


def _return_bool_replacement(text: str) -> tuple[str, str] | None:
    original = text.strip()
    if original.endswith(";"):
        original = original[:-1].rstrip()
    match = re.match(
        r"^(?P<prefix>return\s*(?:\(\s*)?)"
        r"(?P<literal>true|false)"
        r"(?P<suffix>(?:\s*\))?\s*)$",
        original,
    )
    if not match:
        return None
    literal = match.group("literal")
    mutated_literal = "false" if literal == "true" else "true"
    mutated = f"{match.group('prefix')}{mutated_literal}{match.group('suffix')}"
    return original, mutated


def _direct_ast_range_mutants(
    path: str,
    item: dict[str, int | str],
    src: list[str],
    enabled: list[str],
    only: set[int] | None,
) -> list[Mutant]:
    ranged = _single_line_source_range(src, item)
    if ranged is None:
        return []
    line_no, col0, text = ranged
    if only is not None and line_no not in only:
        return []

    kind = str(item.get("kind", ""))
    out: list[Mutant] = []
    leading = len(text) - len(text.lstrip())
    stripped = text.strip()

    if "BooleanLiteral" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["BooleanLiteral"]:
        if stripped in {"true", "false"}:
            mut = Mutant(
                "BooleanLiteral",
                path,
                line_no,
                col0 + leading,
                stripped,
                "false" if stripped == "true" else "true",
            )
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-literal"
            out.append(mut)

    if "ReturnValue" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["ReturnValue"]:
        replacement = _return_bool_replacement(text)
        if replacement is not None:
            original, mutated = replacement
            mut = Mutant("ReturnValue", path, line_no, col0 + leading, original, mutated)
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-return"
            out.append(mut)

    return out


def _git_dirty_files(repo: str, paths: list[str]) -> list[str]:
    if not paths:
        return []
    try:
        result = subprocess.run(
            ["git", "-C", repo, "status", "--porcelain", "--"] + paths,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        return []
    dirty = []
    for line in result.stdout.splitlines():
        path = line[3:].strip()
        if path:
            dirty.append(path)
    return dirty


def _is_tracked_file(repo: str, path: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", repo, "ls-files", "--error-unmatch", path],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def _discover_mode(
    repo: str,
    path: str,
    only: set[int] | None,
    enabled: list[str],
    mode: str,
    analysis: dict[str, Any] | None = None,
) -> list[Mutant]:
    if not _ensure_supported_source_path(path):
        return []
    if mode == "token":
        return discover(repo, path, only, enabled)
    if mode in {"clang", "clang-ast"}:
        try:
            from clang import cindex  # type: ignore
        except ModuleNotFoundError as exc:
            raise ValueError(
                f"--mode {mode} requires the optional 'clang' package and libclang bindings. "
                "Install with `pip install libclang` or use --mode token."
            ) from exc

        compile_entry = _resolve_compile_entry(repo, path)
        parse_options = getattr(cindex.TranslationUnit, "PARSE_DETAILED_PROCESSING_RECORD", 0)
        tu = cindex.Index.create().parse(
            os.path.join(repo, path),
            args=compile_entry,
            options=parse_options,
        )
        errors = [
            d for d in tu.diagnostics
            if int(getattr(d, "severity", 0)) >= getattr(cindex.Diagnostic, "Error", 3)
        ]
        if errors:
            raise ValueError(f"clang parse failed for {path}: {errors[0].spelling}")

        full = os.path.abspath(os.path.join(repo, path))
        ranges = _collect_clang_cursor_ranges(tu.cursor, full)
        macro_ranges = _clang_macro_ranges(ranges)
        if mode == "clang-ast":
            return _discover_clang_ast_first(
                repo,
                path,
                only,
                enabled,
                ranges,
                macro_ranges,
                analysis,
            )

        token_mutants = discover(repo, path, only, enabled)
        out: list[Mutant] = []
        for mut in token_mutants:
            kinds = _clang_matching_kinds(ranges, mut.line, mut.col, mut.original)
            if not _clang_mutation_is_ast_confirmed(mut.mutator, kinds):
                continue
            mut.nodeKind = _clang_primary_node_kind(mut.mutator, kinds)
            mut.rewriteStrategy = "clang-confirmed-token"
            if _rejects_macro_candidate(analysis, path, macro_ranges, mut):
                continue
            out.append(mut)
        return _apply_stryker_ignore_comments(repo, path, out)
    return discover(repo, path, only, enabled)


def _discover_clang_ast_first(
    repo: str,
    path: str,
    only: set[int] | None,
    enabled: list[str],
    ranges: list[dict[str, int | str]],
    macro_ranges: list[dict[str, int | str]] | None = None,
    analysis: dict[str, Any] | None = None,
) -> list[Mutant]:
    full = os.path.join(repo, path)
    with open(full) as f:
        src = f.readlines()

    sorted_ranges = sorted(
        ranges,
        key=lambda item: (
            int(item.get("startLine", 0) or 0),
            int(item.get("startColumn", 0) or 0),
            int(item.get("endLine", 0) or 0),
            int(item.get("endColumn", 0) or 0),
        ),
    )
    seen: set[str] = set()
    out: list[Mutant] = []
    stripped: dict[int, str] = {}
    in_block_comment = False
    for line_no, raw in enumerate(src, start=1):
        code, in_block_comment = _strip_noncode(raw, in_block_comment=in_block_comment)
        stripped[line_no] = code

    for item in sorted_ranges:
        kind = str(item.get("kind", ""))
        start_line = int(item.get("startLine", 0) or 0)
        end_line = int(item.get("endLine", 0) or 0)
        if start_line <= 0 or end_line <= 0:
            continue
        for mut in _direct_ast_range_mutants(path, item, src, enabled, only):
            if _rejects_macro_candidate(analysis, path, macro_ranges or [], mut):
                continue
            if mut.id in seen:
                continue
            seen.add(mut.id)
            out.append(mut)
        for mutator in enabled:
            if kind not in AST_MUTATOR_CURSOR_KINDS.get(mutator, set()):
                continue
            if mutator == "CallRemoval":
                for line_no in range(start_line, min(end_line, len(src)) + 1):
                    if only is not None and line_no not in only:
                        continue
                    for mut in _discover_call_removals(path, line_no, stripped.get(line_no, ""), src[line_no - 1]):
                        if not _clang_range_contains(item, mut.line, mut.col, mut.col + len(mut.original)):
                            continue
                        mut.nodeKind = kind
                        mut.sourceRange = dict(item)
                        mut.rewriteStrategy = "clang-ast-source-range"
                        if _rejects_macro_candidate(analysis, path, macro_ranges or [], mut):
                            continue
                        if mut.id not in seen:
                            seen.add(mut.id)
                            out.append(mut)
                continue
            for orig, new in MUTATORS[mutator]:
                pattern = _TOKEN_PATTERNS.get(orig)
                if pattern is None:
                    continue
                for line_no in range(start_line, min(end_line, len(src)) + 1):
                    if only is not None and line_no not in only:
                        continue
                    code = stripped.get(line_no, "")
                    for match in re.finditer(pattern, code):
                        if not _clang_range_contains(item, line_no, match.start(), match.end()):
                            continue
                        mut = Mutant(mutator, path, line_no, match.start(), orig, new)
                        mut.id = stable_id(mut)
                        mut.nodeKind = kind
                        mut.sourceRange = dict(item)
                        mut.rewriteStrategy = "clang-ast-source-range"
                        if _rejects_macro_candidate(analysis, path, macro_ranges or [], mut):
                            continue
                        if mut.id in seen:
                            continue
                        seen.add(mut.id)
                        out.append(mut)

    return _apply_stryker_ignore_comments(repo, path, out)


def _resolve_compile_entry(repo: str, path: str) -> list[str] | None:
    compile_db = os.path.join(repo, "compile_commands.json")
    if not os.path.exists(compile_db):
        return ["-fsyntax-only"]

    try:
        with open(compile_db) as f:
            entries = json.load(f)
    except Exception:
        return ["-fsyntax-only"]

    if not isinstance(entries, list):
        return ["-fsyntax-only"]

    target = os.path.normpath(os.path.join(repo, path))
    match = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        candidate = str(entry.get("file", ""))
        candidate_abs = candidate
        if candidate and not os.path.isabs(candidate):
            candidate_abs = os.path.normpath(os.path.join(repo, candidate))
        if os.path.normpath(candidate_abs) == target:
            match = entry
            break

    if match is None and entries:
        first = entries[0]
        match = first if isinstance(first, dict) else None

    if match is None:
        return ["-fsyntax-only"]

    cmd = match.get("arguments")
    if cmd is None:
        cmd = match.get("command")
    if isinstance(cmd, list):
        args = [str(v) for v in cmd]
    elif isinstance(cmd, str):
        args = shlex.split(cmd)
    else:
        args = []

    cleaned: list[str] = []
    skip_next = False
    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg == "-o":
            skip_next = True
            continue
        if not arg:
            continue
        if arg in {"clang", "clang++"}:
            continue
        if os.path.isabs(arg) and os.path.normpath(arg) == target:
            continue
        if arg == os.path.basename(target):
            continue
        cleaned.append(arg)

    if "-fsyntax-only" not in cleaned:
        cleaned.append("-fsyntax-only")
    return cleaned


def _load_resumed(report_path: str | None, target_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not report_path:
        return {}
    if not os.path.exists(report_path):
        return {}
    try:
        with open(report_path) as f:
            payload = json.load(f)
    except Exception:
        return {}

    out: dict[str, dict[str, Any]] = {}
    for mut in payload.get("mutants", []):
        mid = mut.get("id")
        status = str(mut.get("status", "")).upper()
        if not mid or status not in FATAL_STATUSES:
            continue
        if target_ids and mid not in target_ids:
            continue
        out[mid] = mut
    return out


def _ensure_target_root(path: str) -> str:
    return os.path.abspath(path)


def _safe_basename(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", text)


def _looks_sensitive_env_key(key: str) -> bool:
    return bool(SENSITIVE_ENV_KEY_RE.search(key))


def _redact_sensitive_assignment_text(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group("key")
        raw_value = match.group("value")
        if not _looks_sensitive_env_key(key):
            return match.group(0)
        if raw_value.startswith("'") and raw_value.endswith("'"):
            redacted = f"'{REDACTED_VALUE}'"
        elif raw_value.startswith('"') and raw_value.endswith('"'):
            redacted = f'"{REDACTED_VALUE}"'
        else:
            redacted = REDACTED_VALUE
        return f"{match.group('prefix')}{key}={redacted}"

    return SHELL_ASSIGNMENT_RE.sub(replace, value)


def _redact_report_artifact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_report_artifact(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_report_artifact(item) for item in value]
    if isinstance(value, str):
        return _redact_sensitive_assignment_text(value)
    return value


def _redaction_metadata() -> dict[str, Any]:
    return {
        "enabled": True,
        "environmentValues": True,
        "secretAssignmentPatterns": True,
        "replacement": REDACTED_VALUE,
    }


def _normalize_mutant_record(mut: dict[str, Any] | Mutant) -> dict[str, Any]:
    rec = dict(mut.__dict__ if isinstance(mut, Mutant) else mut)
    if "col" in rec and "column" not in rec:
        rec["column"] = rec["col"]
    if "column" in rec and "col" not in rec:
        rec["col"] = rec["column"]
    if "line" not in rec or not isinstance(rec["line"], int):
        rec["line"] = 0
    if "column" in rec and not isinstance(rec["column"], int):
        rec["column"] = 0
    if "col" in rec and not isinstance(rec["col"], int):
        rec["col"] = 0
    if rec.get("status") == "IGNORED":
        if not rec.get("ignoreReason"):
            rec["ignoreReason"] = rec.get("detail", "")
        if rec.get("ignoreReason") and not rec.get("detail"):
            rec["detail"] = rec["ignoreReason"]
    return rec


def _apply_shard(mutants: list[Mutant], shard_index: int | None, shard_total: int | None) -> list[Mutant]:
    if not mutants:
        return mutants
    if shard_total in (None, 0, 1):
        return mutants
    if shard_index is None:
        raise ValueError("--shard-total requires --shard-index")
    if not (1 <= shard_index <= shard_total):
        raise ValueError("--shard-index must be in [1, shard-total]")
    return [m for idx, m in enumerate(mutants) if idx % shard_total == shard_index - 1]


def _write_report(path: str, rep: Report, output_mode: str = "legacy") -> None:
    if output_mode == "stryker-cxx":
        payload = _report_dict(rep)
    else:
        payload = _legacy_report(rep)
    payload = _redact_report_artifact(payload)

    require_report(payload) if output_mode == "stryker-cxx" else None
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def _legacy_report(rep: Report) -> dict:
    return {
        "target_files": rep.target_files,
        "total": rep.total,
        "killed": rep.killed,
        "survived": rep.survived,
        "build_error": rep.buildError,
        "check_error": rep.checkErrors,
        "no_coverage": rep.noCoverage,
        "ignored": rep.ignored,
        "mutants": rep.mutants,
        "score": rep.scorePercent,
    }


def _report_dict(rep: Report, repo: str | None = None, base: str | None = None,
                 threshold: float | None = None, startedAt: str | None = None) -> dict:
    normalized_mutants = [_normalize_mutant_record(mut) for mut in rep.mutants]
    execution = {
        "mode": rep.execution.get("mode", "token"),
        "worktreeMode": rep.execution.get("worktreeMode", "inplace"),
        "jobs": rep.execution.get("jobs", 1),
    }
    for key, value in rep.execution.items():
        if key not in execution:
            execution[key] = value
    thresholds = dict(rep.thresholds or {})
    if "break" not in thresholds:
        thresholds = _resolve_thresholds(
            rep.threshold,
            thresholds.get("high"),
            thresholds.get("low"),
            thresholds.get("break"),
            rep.score,
        )
    else:
        thresholds["status"] = _resolve_thresholds(
            None,
            float(thresholds.get("high", thresholds.get("break"))),
            float(thresholds.get("low", thresholds.get("break"))),
            float(thresholds["break"]),
            rep.score,
        )["status"]
    payload = {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "tool": rep.tool,
        "repo": repo or rep.repo,
        "base": base or rep.base,
        "startedAt": startedAt or rep.startedAt,
        "completedAt": rep.completedAt,
        "threshold": threshold if threshold is not None else thresholds["break"],
        "thresholds": thresholds,
        "timeoutSeconds": rep.timeoutSeconds,
        "totalMutants": rep.total,
        "killed": rep.killed,
        "survived": rep.survived,
        "buildErrors": rep.buildError,
        "checkErrors": rep.checkErrors,
        "noCoverage": rep.noCoverage,
        "timeouts": rep.timeouts,
        "ignored": rep.ignored,
        "score": rep.score,
        "execution": execution,
        "dryRun": rep.dryRun or {"status": "NOT_RUN"},
        "coverage": rep.coverage or {"enabled": False, "provider": "none"},
        "baseline": rep.baseline or {"enabled": False},
        "config": rep.config or {"path": None, "hash": None, "effective": {}},
        "commands": {
            "build": rep.buildCommand,
            "check": rep.checkCommand,
            "test": rep.testCommand,
        },
        "summary": _summary(normalized_mutants),
        "mutationTestingElements": _mutation_testing_elements(rep),
        "mutants": normalized_mutants,
        "targetFiles": rep.target_files,
        # Legacy compatibility fields for transition period.
        "scorePercent": rep.scorePercent,
        "target_files": rep.target_files,
        "build_error": rep.buildError,
        "check_error": rep.checkErrors,
        "no_coverage": rep.noCoverage,
        "total": rep.total,
        "ignored_count": rep.ignored,
    }
    return _redact_report_artifact(payload)


def _new_summary_bucket() -> dict[str, Any]:
    return {
        "total": 0,
        "killed": 0,
        "survived": 0,
        "buildErrors": 0,
        "checkErrors": 0,
        "noCoverage": 0,
        "timeouts": 0,
        "ignored": 0,
        "runtimeErrors": 0,
        "pending": 0,
        "score": 1.0,
    }


def _finish_summary_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    scored = bucket["killed"] + bucket["survived"]
    bucket["score"] = (bucket["killed"] / scored) if scored else 1.0
    return bucket


def _summary(mutants: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_file: dict[str, dict[str, Any]] = {}
    by_mutator: dict[str, dict[str, Any]] = {}

    def add(bucket: dict[str, Any], status: str) -> None:
        bucket["total"] += 1
        if status == "KILLED":
            bucket["killed"] += 1
        elif status == "SURVIVED":
            bucket["survived"] += 1
        elif status == "BUILD_ERROR":
            bucket["buildErrors"] += 1
        elif status == "CHECK_ERROR":
            bucket["checkErrors"] += 1
        elif status == "NO_COVERAGE":
            bucket["noCoverage"] += 1
        elif status == "TIMEOUT":
            bucket["timeouts"] += 1
        elif status == "IGNORED":
            bucket["ignored"] += 1
        elif status == "RUNTIME_ERROR":
            bucket["runtimeErrors"] += 1
        else:
            bucket["pending"] += 1

    for mut in mutants:
        status = str(mut.get("status", "PENDING")).upper()
        file = str(mut.get("file", ""))
        mutator = str(mut.get("mutator", ""))
        by_status[status] = by_status.get(status, 0) + 1
        file_bucket = by_file.setdefault(file, _new_summary_bucket())
        mutator_bucket = by_mutator.setdefault(mutator, _new_summary_bucket())
        add(file_bucket, status)
        add(mutator_bucket, status)

    return {
        "byStatus": by_status,
        "byFile": {key: _finish_summary_bucket(value) for key, value in by_file.items()},
        "byMutator": {key: _finish_summary_bucket(value) for key, value in by_mutator.items()},
    }


def _mutation_testing_elements(rep: Report) -> dict:
    files: dict[str, dict] = {}

    # Keep a best-effort source map where possible; this is optional for the
    # consumer but preserves the Stryker-style projection contract.
    if rep.repo:
        for file in rep.target_files:
            source = ""
            try:
                with open(os.path.join(rep.repo, file)) as f:
                    source = f.read()
            except OSError:
                source = ""
            files[file] = {"source": source, "mutants": []}

    def _to_mte_column(col: int) -> int:
        return max(0, col + 1)

    def _to_mte_end(mut: dict[str, Any], start_col: int) -> int:
        return max(start_col, start_col + max(len(mut.get("original", "")), 1))

    for idx, mut in enumerate(rep.mutants):
        mut = _normalize_mutant_record(mut)
        file = mut["file"]
        files.setdefault(file, {"source": "", "mutants": []})
        start_col = _to_mte_column(int(mut["col"]))
        mte_mutant = {
            "id": mut.get("id") or str(idx),
            "mutatorName": mut["mutator"],
            "description": MUTATOR_DESCRIPTIONS.get(mut["mutator"], mut["mutator"]),
            "original": mut.get("original", ""),
            "replacement": mut["mutated"],
            "status": _mte_status(mut.get("status", "PENDING")),
            "statusReason": mut.get("detail", ""),
            "nodeKind": mut.get("nodeKind", ""),
            "runCommand": mut.get("run", {}).get("reproCommand") if isinstance(mut.get("run"), dict) else None,
            "location": {
                "start": {"line": mut["line"], "column": start_col},
                "end": {
                    "line": mut["line"],
                    "column": _to_mte_end(mut, start_col),
                },
            },
        }
        run = mut.get("run") if isinstance(mut.get("run"), dict) else {}
        if isinstance(run, dict) and isinstance(run.get("coveredBy"), list):
            mte_mutant["coveredBy"] = [str(item) for item in run["coveredBy"]]
        files[file]["mutants"].append(mte_mutant)

    return {
        "schemaVersion": MTE_SCHEMA_VERSION,
        "files": files,
        "testFiles": {},
        "projectRoot": rep.repo,
        "language": "cpp",
    }


def _mte_status(status: str) -> str:
    return {
        "KILLED": "Killed",
        "SURVIVED": "Survived",
        "BUILD_ERROR": "NoCoverage",
        "CHECK_ERROR": "RuntimeError",
        "NO_COVERAGE": "NoCoverage",
        "TIMEOUT": "Timeout",
        "IGNORED": "Ignored",
        "PENDING": "Pending",
    }.get(status.upper(), "RuntimeError")


def _format_markdown(rep: Report) -> str:
    lines = [
        "# stryker-cxx report",
        "",
        "| field | value |",
        "|---|---|",
        f"| score | {rep.score:.2f} |",
        f"| threshold | {rep.threshold} |",
        f"| mode | {rep.execution.get('mode', 'token')} |",
        f"| worktreeMode | {rep.execution.get('worktreeMode', 'inplace')} |",
        f"| jobs | {rep.execution.get('jobs', 1)} |",
        f"| killed | {rep.killed} |",
        f"| survived | {rep.survived} |",
        f"| build errors | {rep.buildError} |",
        f"| check errors | {rep.checkErrors} |",
        f"| no coverage | {rep.noCoverage} |",
        f"| timeouts | {rep.timeouts} |",
        f"| ignored | {rep.ignored} |",
        f"| total mutants | {rep.total} |",
        f"| target files | {', '.join(rep.target_files) if rep.target_files else '(none)'} |",
        f"| build command | `{rep.buildCommand or ''}` |",
        f"| test command | `{rep.testCommand or ''}` |",
        "",
        "## Surviving mutants",
    ]
    for mut in rep.mutants:
        if mut["status"] == "SURVIVED":
            lines.append(
                f"- `{mut['file']}:{mut['line']}:{mut['col']}` "
                f"{mut['mutator']} `{mut['original']} -> {mut['mutated']}` "
                f"({mut.get('durationMs', 0)}ms)"
            )
            command = mut.get("run", {}).get("reproCommand")
            if command:
                lines.append(f"  - reproduce: `{command}`")
            detail = mut.get("detail")
            if detail:
                lines.append(f"  - detail: {detail}")
    ignored = [mut for mut in rep.mutants if mut.get("status") == "IGNORED"]
    if ignored:
        lines.extend(["", "## Ignored mutants"])
        for mut in ignored:
            lines.append(
                f"- `{mut['file']}:{mut['line']}:{mut['col']}` "
                f"{mut['mutator']} `{mut['original']} -> {mut['mutated']}` "
                f"- {mut.get('ignoreReason') or mut.get('detail') or 'ignored'}"
            )
    return "\n".join(lines)


def _format_html(rep: Report) -> str:
    def esc(value: Any) -> str:
        return html_lib.escape(str(value if value is not None else ""))

    summary = _summary([_normalize_mutant_record(mut) for mut in rep.mutants])
    summary_rows = []
    for file_name, bucket in summary["byFile"].items():
        summary_rows.append(
            "<tr>"
            f"<td>{esc(file_name)}</td><td>{bucket['total']}</td><td>{bucket['killed']}</td>"
            f"<td>{bucket['survived']}</td><td>{bucket['noCoverage']}</td><td>{bucket['timeouts']}</td>"
            f"<td>{bucket['score']:.2f}</td>"
            "</tr>"
        )
    rows = [
        "<tr>"
        "<th data-sort='file'>File</th><th data-sort='line'>Line</th><th data-sort='mutator'>Mutator</th>"
        "<th>Original</th><th>Mutated</th><th data-sort='status'>Status</th><th data-sort='duration'>DurationMs</th>"
        "<th>Source</th>"
        "</tr>"
    ]
    for mut in rep.mutants:
        rows.append(
            "<tr>"
            f"<td>{esc(mut['file'])}</td><td>{esc(mut['line'])}</td><td>{esc(mut['mutator'])}</td>"
            f"<td><code>{esc(mut['original'])}</code></td><td><code>{esc(mut['mutated'])}</code></td>"
            f"<td><span class='status status-{esc(mut['status']).lower()}'>{esc(mut['status'])}</span></td>"
            f"<td>{esc(mut.get('durationMs', 0))}</td><td>{esc(mut.get('resultSource', 'executed'))}</td>"
            "</tr>"
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>stryker-cxx report</title>"
        "<style>"
        "body{font-family:ui-sans-serif,system-ui,sans-serif;margin:2rem;background:#f7f3ea;color:#1b1a17}"
        "h1{font-size:2.4rem;margin:0 0 .5rem} .cards{display:flex;gap:1rem;flex-wrap:wrap;margin:1rem 0}"
        ".card{background:#fff;border:1px solid #ded4bf;border-radius:12px;padding:1rem;min-width:8rem;box-shadow:0 2px 0 #e8ddc7}"
        ".label{font-size:.75rem;text-transform:uppercase;letter-spacing:.08em;color:#756b58}.value{font-size:1.6rem;font-weight:700}"
        "input{padding:.7rem 1rem;border:1px solid #b8aa8f;border-radius:999px;width:min(38rem,100%);margin:1rem 0;background:#fff}"
        "table{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;margin:1rem 0}"
        "th,td{padding:.65rem .75rem;border-bottom:1px solid #eee4d2;text-align:left;vertical-align:top}th{background:#2f3a2f;color:#fff;cursor:pointer;position:sticky;top:0}"
        "code{background:#f1eadb;padding:.12rem .25rem;border-radius:4px}.status{font-weight:700}.status-survived{color:#9b2c2c}.status-killed{color:#236b3b}.status-timeout,.status-check_error,.status-build_error{color:#8a5a00}.status-no_coverage{color:#5c6470}"
        "</style></head><body>"
        "<h1>stryker-cxx report</h1>"
        "<div class='cards'>"
        f"<div class='card'><div class='label'>score</div><div class='value'>{rep.score:.2f}</div></div>"
        f"<div class='card'><div class='label'>killed</div><div class='value'>{rep.killed}</div></div>"
        f"<div class='card'><div class='label'>survived</div><div class='value'>{rep.survived}</div></div>"
        f"<div class='card'><div class='label'>no coverage</div><div class='value'>{rep.noCoverage}</div></div>"
        f"<div class='card'><div class='label'>timeouts</div><div class='value'>{rep.timeouts}</div></div>"
        f"<div class='card'><div class='label'>ignored</div><div class='value'>{rep.ignored}</div></div>"
        "</div>"
        "<h2>By file</h2>"
        "<table><tr><th>File</th><th>Total</th><th>Killed</th><th>Survived</th><th>No coverage</th><th>Timeouts</th><th>Score</th></tr>"
        f"{''.join(summary_rows)}</table>"
        "<h2>Mutants</h2><input id='filter' placeholder='Filter by file, mutator, status, or source...'>"
        f"<table id='mutants'>{''.join(rows)}</table>"
        "<script>"
        "const filter=document.getElementById('filter'),table=document.getElementById('mutants');"
        "filter.addEventListener('input',()=>{const q=filter.value.toLowerCase();[...table.tBodies[0].rows].forEach(r=>{r.style.display=r.innerText.toLowerCase().includes(q)?'':'none'})});"
        "[...table.querySelectorAll('th[data-sort]')].forEach((th,i)=>th.addEventListener('click',()=>{const rows=[...table.tBodies[0].rows];const numeric=['line','duration'].includes(th.dataset.sort);rows.sort((a,b)=>{const av=a.cells[i].innerText,bv=b.cells[i].innerText;return numeric?(Number(av)-Number(bv)):av.localeCompare(bv)});rows.forEach(r=>table.tBodies[0].appendChild(r));}));"
        "</script></body></html>"
    )


def _format_sarif(rep: Report) -> dict:
    results = []
    for mut in rep.mutants:
        status = mut.get("status", "PENDING")
        level = {
            "KILLED": "none",
            "SURVIVED": "warning",
            "BUILD_ERROR": "warning",
            "CHECK_ERROR": "warning",
            "NO_COVERAGE": "note",
            "TIMEOUT": "warning",
            "IGNORED": "none",
        }.get(status, "warning")
        results.append(
            {
                "ruleId": mut["mutator"],
                "level": level,
                "message": {"text": mut.get("detail") or f"{mut['original']} -> {mut['mutated']}"},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": mut["file"]},
                            "region": {
                                "startLine": mut["line"],
                                "startColumn": mut["col"],
                                "endLine": mut["line"],
                                "endColumn": mut["col"] + len(mut["original"]),
                            },
                        },
                    }
                ],
            }
        )
    return {
        "version": "2.1.0",
        "$schema": "https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.json",
        "runs": [{"tool": {"driver": {"name": "stryker-cxx", "fullName": "Stryker C++ mutation engine"}}, "results": results}],
    }


def _github_annotation_escape(value: Any) -> str:
    return (
        str(value if value is not None else "")
        .replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(",", "%2C")
        .replace(":", "%3A")
    )


def _format_github_annotations(rep: Report) -> str:
    lines: list[str] = []
    for raw in rep.mutants:
        mut = _normalize_mutant_record(raw)
        status = str(mut.get("status", "PENDING")).upper()
        if status in {"KILLED", "IGNORED"}:
            continue
        level = "notice" if status == "NO_COVERAGE" else "warning"
        title = f"{status} {mut.get('mutator', 'mutant')}"
        message = mut.get("detail") or f"{mut.get('original', '')} -> {mut.get('mutated', '')}"
        lines.append(
            "::"
            f"{level} "
            f"file={_github_annotation_escape(mut.get('file'))},"
            f"line={int(mut.get('line', 1) or 1)},"
            f"col={max(1, int(mut.get('col', 0) or 0) + 1)},"
            f"title={_github_annotation_escape(title)}"
            f"::{_github_annotation_escape(message)}"
        )
    return "\n".join(lines) + ("\n" if lines else "")


def _dashboard_payload(rep: Report) -> dict[str, Any]:
    mutants = [_normalize_mutant_record(mut) for mut in rep.mutants]
    dashboard = rep.execution.get("dashboard", {})
    if not isinstance(dashboard, dict):
        dashboard = {}
    retention_days = dashboard.get("retentionDays")
    return {
        "schemaVersion": "stryker-cxx.dashboard.v1",
        "dashboardVersion": str(dashboard.get("version") or "1"),
        "generatedAt": _utc_now_iso(),
        "tool": rep.tool,
        "repo": rep.repo,
        "base": rep.base,
        "startedAt": rep.startedAt,
        "completedAt": rep.completedAt,
        "retention": {
            "days": retention_days,
            "policy": (
                f"delete-after-{retention_days}-days"
                if retention_days is not None
                else "caller-managed"
            ),
        },
        "provenance": {
            "reportSchemaVersion": REPORT_SCHEMA_VERSION,
            "configHash": (rep.config or {}).get("hash"),
            "configPath": (rep.config or {}).get("path"),
            "ci": {
                "commit": os.environ.get("GITHUB_SHA") or os.environ.get("CI_COMMIT_SHA"),
                "runId": os.environ.get("GITHUB_RUN_ID") or os.environ.get("CI_PIPELINE_ID"),
            },
            "upload": dashboard.get("upload", {"enabled": False}),
        },
        "score": rep.score,
        "thresholds": rep.thresholds,
        "summary": _summary(mutants),
        "counts": {
            "totalMutants": rep.total,
            "killed": rep.killed,
            "survived": rep.survived,
            "buildErrors": rep.buildError,
            "checkErrors": rep.checkErrors,
            "noCoverage": rep.noCoverage,
            "timeouts": rep.timeouts,
            "ignored": rep.ignored,
        },
        "mutants": [
            {
                "id": mut.get("id"),
                "file": mut.get("file"),
                "line": mut.get("line"),
                "mutator": mut.get("mutator"),
                "status": mut.get("status"),
                "resultSource": mut.get("resultSource", "executed"),
            }
            for mut in mutants
        ],
    }


def _write_dashboard_export(path: str, rep: Report) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    with open(path, "w") as f:
        json.dump(_redact_report_artifact(_dashboard_payload(rep)), f, indent=2)


def _upload_dashboard(
    url: str,
    rep: Report,
    auth_token_env: str | None = None,
    auth_header: str | None = None,
) -> None:
    body = json.dumps(_redact_report_artifact(_dashboard_payload(rep))).encode("utf-8")
    headers = {"content-type": "application/json", "user-agent": "stryker-cxx"}
    if auth_token_env:
        token = os.environ.get(auth_token_env)
        if not token:
            raise ValueError(f"dashboard auth token env is not set: {auth_token_env}")
        header = auth_header or "Authorization"
        headers[header] = f"Bearer {token}" if header.lower() == "authorization" else token
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()


def _write_human_artifact(path: str, report: str, payload: Any) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    out_path = path
    ext = os.path.splitext(path)[1].lower()
    if ext not in {".md", ".html", ".sarif", ".github-annotations"}:
        if report == "markdown":
            out_path = path + ".md"
        elif report == "html":
            out_path = path + ".html"
        elif report == "sarif":
            out_path = path + ".sarif"
        elif report == "github-annotations":
            out_path = path + ".github-annotations"
    payload = _redact_report_artifact(payload)
    if report == "json" and isinstance(payload, dict) and payload.get("schemaVersion") == "2.0":
        require_mte(payload)
    with open(out_path, "w") as f:
        if report == "json":
            json.dump(payload, f, indent=2)
        elif report == "markdown":
            f.write(payload)
        elif report == "html":
            f.write(payload)
        elif report == "sarif":
            json.dump(payload, f, indent=2)
        elif report == "github-annotations":
            f.write(payload)
        else:
            json.dump(payload, f, indent=2)
    return out_path


def _write_output_artifacts(report_path: str, output_format: str, rep: "Report") -> None:
    if output_format == "markdown":
        _write_human_artifact(report_path, "markdown", _format_markdown(rep))
    elif output_format == "html":
        _write_human_artifact(report_path, "html", _format_html(rep))
    elif output_format == "sarif":
        _write_human_artifact(report_path, "sarif", _format_sarif(rep))
    elif output_format == "github-annotations":
        _write_human_artifact(report_path, "github-annotations", _format_github_annotations(rep))
    elif output_format == "mutation-testing-elements":
        _write_human_artifact(report_path, "json", _mutation_testing_elements(rep))


def _run_mutant_once(
    mut: Mutant,
    repo: str,
    build_cmd: str,
    check_cmd: str | None,
    test_cmd: str,
    timeout_seconds: int | None,
    worktree_mode: str,
    artifact_root: str,
    execution_mode: str,
    skip_tests: bool,
    plugins: list[dict[str, Any]] | None = None,
    worker_tmp_dir: str | None = None,
    retain_worktrees: bool = False,
    retain_worktrees_for: set[str] | None = None,
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
) -> Mutant:
    existing_run = dict(mut.run)
    run_record = dict(existing_run)
    run_record["mode"] = execution_mode
    run_record["worktreeMode"] = worktree_mode
    mut.run = run_record
    _record_environment_policy(mut.run, env_overrides, env_inherit, env_block)
    start_ms = time.perf_counter()
    retain_state = {"retain": False}
    with _workspace(repo, worktree_mode, worker_tmp_dir=worker_tmp_dir, retain_state=retain_state) as work_repo:
        original = apply_mutant(work_repo, mut)
        build_log = os.path.join(artifact_root, f"build_{_safe_basename(mut.id)}.log")
        check_log = os.path.join(artifact_root, f"check_{_safe_basename(mut.id)}.log")
        test_log = os.path.join(artifact_root, f"test_{_safe_basename(mut.id)}.log")
        mut.buildLog = build_log
        mut.checkLog = check_log
        mut.testLog = test_log
        effective_test_cmd = str(mut.run.get("selectedTestCommand") or test_cmd)

        try:
            build_rc, build_ms = run_cmd(
                build_cmd,
                work_repo,
                build_log,
                timeout_seconds,
                "build",
                plugins,
                env_overrides,
                env_inherit,
                env_block,
            )
            mut.run["buildReturnCode"] = build_rc
            mut.run["buildMs"] = build_ms
            mut.run["buildProvider"] = _phase_provider_name(plugins, "build")
            mut.durationMs += build_ms
            if build_rc == 124:
                mut.status = "TIMEOUT"
                mut.detail = "build timed out"
            elif build_rc != 0:
                mut.status = "BUILD_ERROR"
                mut.detail = "did not compile"
            else:
                if check_cmd:
                    check_rc, check_ms = run_cmd(
                        check_cmd,
                        work_repo,
                        check_log,
                        timeout_seconds,
                        "check",
                        plugins,
                        env_overrides,
                        env_inherit,
                        env_block,
                    )
                    mut.run["checkReturnCode"] = check_rc
                    mut.run["checkMs"] = check_ms
                    mut.run["checkProvider"] = _phase_provider_name(plugins, "check")
                    mut.durationMs += check_ms
                    if check_rc == 124:
                        mut.status = "TIMEOUT"
                        mut.detail = "check timed out"
                    elif check_rc != 0:
                        mut.status = "CHECK_ERROR"
                        mut.detail = "checker rejected mutant"
                if mut.status == "PENDING":
                    if skip_tests:
                        mut.status = "SURVIVED"
                        mut.detail = "tests skipped after successful build/check"
                    else:
                        test_rc, test_ms = run_cmd(
                            effective_test_cmd,
                            work_repo,
                            test_log,
                            timeout_seconds,
                            "test",
                            plugins,
                            env_overrides,
                            env_inherit,
                            env_block,
                        )
                        mut.run["testReturnCode"] = test_rc
                        mut.run["testMs"] = test_ms
                        mut.run["testProvider"] = _phase_provider_name(plugins, "test")
                        mut.run["testCommand"] = effective_test_cmd
                        mut.durationMs += test_ms
                        if test_rc == 124:
                            mut.status = "TIMEOUT"
                            mut.detail = "tests timed out"
                        elif test_rc != 0:
                            mut.status = "KILLED"
                        else:
                            mut.status = "SURVIVED"
                            mut.detail = "all targeted tests passed"
        finally:
            retain_current = _should_retain_worktree(
                retain_worktrees,
                retain_worktrees_for,
                mut.status,
                work_repo,
                repo,
            )
            if retain_current:
                retain_state["retain"] = True
                mut.run["retainedWorktree"] = work_repo
                mut.run["retainedWorktreeReason"] = mut.status
            else:
                restore(work_repo, mut.file, mut.line, original)
            mut.durationMs = int((time.perf_counter() - start_ms) * 1000)
    return mut


def _run_mutant_task(payload: tuple[Any, ...]) -> Mutant:
    (
        mut,
        repo,
        build_cmd,
        check_cmd,
        test_cmd,
        timeout_seconds,
        worktree_mode,
        artifact_root,
        execution_mode,
        skip_tests,
        plugins,
        worker_tmp_dir,
        retain_worktrees,
        retain_worktrees_for,
        env_overrides,
        env_inherit,
        env_block,
    ) = payload
    return _run_mutant_once(
        mut=mut,
        repo=repo,
        build_cmd=build_cmd,
        check_cmd=check_cmd,
        test_cmd=test_cmd,
        timeout_seconds=timeout_seconds,
        worktree_mode=worktree_mode,
        artifact_root=artifact_root,
        execution_mode=execution_mode,
        skip_tests=skip_tests,
        plugins=plugins,
        worker_tmp_dir=worker_tmp_dir,
        retain_worktrees=retain_worktrees,
        retain_worktrees_for=retain_worktrees_for,
        env_overrides=env_overrides,
        env_inherit=env_inherit,
        env_block=env_block,
    )


def _mutants_overlap(a: Mutant, b: Mutant) -> bool:
    if a.file != b.file:
        return False
    # Keep batching conservative: same-line edits can shift columns or interact
    # with statement-level replacements, so do not batch them.
    if a.line == b.line:
        return True
    return False


def _batch_mutants(mutants: list[Mutant], batch_size: int) -> list[list[Mutant]]:
    if batch_size <= 1:
        return [[mut] for mut in mutants]
    batches: list[list[Mutant]] = []
    for mut in mutants:
        placed = False
        for batch in batches:
            if len(batch) >= batch_size:
                continue
            if any(_mutants_overlap(mut, existing) for existing in batch):
                continue
            batch.append(mut)
            placed = True
            break
        if not placed:
            batches.append([mut])
    return batches


def _run_batch_probe(
    batch: list[Mutant],
    repo: str,
    build_cmd: str,
    check_cmd: str | None,
    test_cmd: str,
    timeout_seconds: int | None,
    worktree_mode: str,
    artifact_root: str,
    execution_mode: str,
    skip_tests: bool,
    plugins: list[dict[str, Any]] | None = None,
    worker_tmp_dir: str | None = None,
    retain_worktrees: bool = False,
    retain_worktrees_for: set[str] | None = None,
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
) -> tuple[str, str, int, dict[str, Any]]:
    batch_id = hashlib.sha256(",".join(mut.id for mut in batch).encode("utf-8")).hexdigest()[:12]
    run: dict[str, Any] = {
        "mode": execution_mode,
        "worktreeMode": worktree_mode,
        "batchId": batch_id,
        "batchSize": len(batch),
    }
    _record_environment_policy(run, env_overrides, env_inherit, env_block)
    start_ms = time.perf_counter()
    result: tuple[str, str, int, dict[str, Any]] | None = None
    retain_state = {"retain": False}
    with _workspace(repo, worktree_mode, worker_tmp_dir=worker_tmp_dir, retain_state=retain_state) as work_repo:
        originals: list[tuple[Mutant, str]] = []
        build_log = os.path.join(artifact_root, f"batch_build_{batch_id}.log")
        check_log = os.path.join(artifact_root, f"batch_check_{batch_id}.log")
        test_log = os.path.join(artifact_root, f"batch_test_{batch_id}.log")
        try:
            for mut in batch:
                originals.append((mut, apply_mutant(work_repo, mut)))

            build_rc, build_ms = run_cmd(
                build_cmd,
                work_repo,
                build_log,
                timeout_seconds,
                "build",
                plugins,
                env_overrides,
                env_inherit,
                env_block,
            )
            run["buildReturnCode"] = build_rc
            run["buildMs"] = build_ms
            run["buildLog"] = build_log
            run["buildProvider"] = _phase_provider_name(plugins, "build")
            if build_rc == 124:
                result = ("TIMEOUT", "batch build timed out", int((time.perf_counter() - start_ms) * 1000), run)
                return result
            if build_rc != 0:
                result = ("BUILD_ERROR", "batch did not compile", int((time.perf_counter() - start_ms) * 1000), run)
                return result

            if check_cmd:
                check_rc, check_ms = run_cmd(
                    check_cmd,
                    work_repo,
                    check_log,
                    timeout_seconds,
                    "check",
                    plugins,
                    env_overrides,
                    env_inherit,
                    env_block,
                )
                run["checkReturnCode"] = check_rc
                run["checkMs"] = check_ms
                run["checkLog"] = check_log
                run["checkProvider"] = _phase_provider_name(plugins, "check")
                if check_rc == 124:
                    result = ("TIMEOUT", "batch check timed out", int((time.perf_counter() - start_ms) * 1000), run)
                    return result
                if check_rc != 0:
                    result = (
                        "CHECK_ERROR",
                        "batch checker rejected mutant set",
                        int((time.perf_counter() - start_ms) * 1000),
                        run,
                    )
                    return result

            if skip_tests:
                result = (
                    "SURVIVED",
                    "tests skipped after successful batch build/check",
                    int((time.perf_counter() - start_ms) * 1000),
                    run,
                )
                return result

            test_rc, test_ms = run_cmd(
                test_cmd,
                work_repo,
                test_log,
                timeout_seconds,
                "test",
                plugins,
                env_overrides,
                env_inherit,
                env_block,
            )
            run["testReturnCode"] = test_rc
            run["testMs"] = test_ms
            run["testLog"] = test_log
            run["testProvider"] = _phase_provider_name(plugins, "test")
            if test_rc == 124:
                result = ("TIMEOUT", "batch tests timed out", int((time.perf_counter() - start_ms) * 1000), run)
                return result
            if test_rc != 0:
                result = (
                    "KILLED",
                    "batch killed by tests; split for attribution",
                    int((time.perf_counter() - start_ms) * 1000),
                    run,
                )
                return result
            result = (
                "SURVIVED",
                "all targeted tests passed for batched mutants",
                int((time.perf_counter() - start_ms) * 1000),
                run,
            )
            return result
        finally:
            status = result[0] if result else "PENDING"
            retain_current = _should_retain_worktree(
                retain_worktrees,
                retain_worktrees_for,
                status,
                work_repo,
                repo,
            )
            if retain_current:
                retain_state["retain"] = True
                run["retainedWorktree"] = work_repo
                run["retainedWorktreeReason"] = status
            else:
                for mut, original in reversed(originals):
                    restore(work_repo, mut.file, mut.line, original)


def _run_batch_task(payload: tuple[Any, ...]) -> tuple[int, list[Mutant], str, str, int, dict[str, Any]]:
    (
        batch_index,
        batch,
        repo,
        build_cmd,
        check_cmd,
        test_cmd,
        timeout_seconds,
        worktree_mode,
        artifact_root,
        execution_mode,
        skip_tests,
        plugins,
        worker_tmp_dir,
        retain_worktrees,
        retain_worktrees_for,
        env_overrides,
        env_inherit,
        env_block,
    ) = payload
    status, detail, duration_ms, run = _run_batch_probe(
        batch,
        repo=repo,
        build_cmd=build_cmd,
        check_cmd=check_cmd,
        test_cmd=test_cmd,
        timeout_seconds=timeout_seconds,
        worktree_mode=worktree_mode,
        artifact_root=artifact_root,
        execution_mode=execution_mode,
        skip_tests=skip_tests,
        plugins=plugins,
        worker_tmp_dir=worker_tmp_dir,
        retain_worktrees=retain_worktrees,
        retain_worktrees_for=retain_worktrees_for,
        env_overrides=env_overrides,
        env_inherit=env_inherit,
        env_block=env_block,
    )
    return batch_index, batch, status, detail, duration_ms, run


def _executed_record(executed: Mutant, source: str = "executed") -> dict[str, Any]:
    rec = _normalize_mutant_record(asdict(executed))
    rec["resultSource"] = source
    if isinstance(executed.run, dict) and executed.run.get("baselineKey"):
        rec["baselineKey"] = executed.run["baselineKey"]
    return rec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stryker-cxx")
    ap.add_argument("--repo-dir", dest="repo", required=True)
    ap.add_argument("--files", required=True)
    ap.add_argument("--diff-base", default=None, dest="diff_base")
    ap.add_argument("--lines", default=None)
    ap.add_argument("--build-cmd", required=True, dest="build_cmd")
    ap.add_argument("--check-cmd", required=False, default=None, dest="check_cmd")
    ap.add_argument("--test-cmd", required=False, dest="test_cmd")
    ap.add_argument("--report", required=True)
    ap.add_argument("--config-path", default=None)
    ap.add_argument("--config-hash", default=None)
    ap.add_argument("--effective-config-json", default=None)
    ap.add_argument("--max-mutants", type=int, default=0)
    ap.add_argument("--include-metal", action="store_true", dest="include_metal")
    ap.add_argument("--mutators", default=",".join(DEFAULT_MUTATORS))
    ap.add_argument("--timeout", type=int, default=None, dest="timeout_seconds",
                    help="Per-mutant timeout in seconds")
    ap.add_argument("--timeout-factor", type=float, default=1.5,
                    help="Multiplier applied to initial-test duration when calibrating mutant timeouts")
    ap.add_argument("--timeout-constant-ms", type=int, default=5000,
                    help="Constant milliseconds added to calibrated mutant timeouts")
    ap.add_argument("--skip-initial-test", action="store_true",
                    help="Skip the Stryker-style unmodified build/test validation")
    ap.add_argument("--dry-run-only", action="store_true",
                    help="Run initial build/test validation and write a report without executing mutants")
    ap.add_argument("--skip-tests", action="store_true",
                    help="Run build/check phases only and mark viable mutants as survived")
    ap.add_argument("--coverage-file", default=None,
                    help="Optional llvm-cov JSON, simple JSON, or LCOV file used to mark NO_COVERAGE mutants")
    ap.add_argument("--coverage-provider", default=None)
    ap.add_argument("--coverage-test-command-template", default=None,
                    help="Optional command template for test-level coverage selection; supports {tests}, {tests_csv}, {tests_space}, {first_test}")
    ap.add_argument("--coverage-helper-command-template", default=None,
                    help="Run once per --coverage-helper-tests entry to generate per-test coverage; supports {test} and {coverage_file}")
    ap.add_argument("--coverage-helper-tests", action="append", default=[],
                    help="Comma-separated test names passed to the coverage helper command template")
    ap.add_argument("--plugin", action="append", default=[],
                    help="Path to a local stryker-cxx plugin manifest")
    ap.add_argument("--plugin-dir", action="append", default=[],
                    help=f"Directory containing {PLUGIN_MANIFEST}")
    ap.add_argument("--reporter", action="append", default=[],
                    help="Reporter name to record/request from loaded plugins")
    ap.add_argument("--dashboard-export", default=None)
    ap.add_argument("--dashboard-upload-url", default=None)
    ap.add_argument("--dashboard-version", default="1")
    ap.add_argument("--dashboard-retention-days", type=int, default=None)
    ap.add_argument("--dashboard-auth-token-env", default=None)
    ap.add_argument("--dashboard-auth-header", default="Authorization")
    ap.add_argument("--incremental", action="store_true",
                    help="Reuse compatible mutant results from a baseline cache")
    ap.add_argument("--baseline-file", default=None,
                    help="Path to a stryker-cxx baseline cache")
    ap.add_argument("--baseline-max-age-days", type=int, default=None,
                    help="Only reuse baseline entries updated within this many days")
    ap.add_argument("--baseline-branch", default=None,
                    help="Only reuse/write baseline entries for this logical branch name")
    ap.add_argument("--write-baseline", default=None,
                    help="Write/update baseline cache at this path after the run")
    ap.add_argument("--clear-baseline", action="store_true",
                    help="Delete the selected baseline cache before running")
    ap.add_argument("--mode", default="token", choices=["token", "clang", "clang-ast"])
    ap.add_argument("--jobs", type=int, default=1, help="Parallel mutation workers")
    ap.add_argument("--batch-mutants", action="store_true",
                    help="Batch compatible mutants in isolated worktrees and split failed batches for attribution")
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--shard-index", type=int, default=None)
    ap.add_argument("--shard-total", type=int, default=None, help="Split work into N shards")
    ap.add_argument("--worktree-mode", dest="worktree_mode", choices=["inplace", "git-worktree", "copy"], default="inplace")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--output-format", default="legacy", choices=["legacy", "stryker-cxx"],
                    dest="output_format",
                    help="Compatibility report format; legacy keeps old engine fields")
    ap.add_argument("--format", default="json", choices=["json", "markdown", "html", "sarif", "github-annotations", "mutation-testing-elements"],
                    help="Report artifact format")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--threshold-high", type=float, default=None)
    ap.add_argument("--threshold-low", type=float, default=None)
    ap.add_argument("--threshold-break", type=float, default=None)
    ap.add_argument("--fail-on-empty", action="store_true", dest="fail_on_empty")
    ap.add_argument("--artifact-dir", default=None)
    ap.add_argument("--retain-worktrees", action="store_true", dest="retain_worktrees")
    ap.add_argument("--retain-worktrees-for", default=None, dest="retain_worktrees_for",
                    help="Comma-separated statuses whose copy/git worktrees should be retained")
    ap.add_argument("--retained-worktree-ttl-hours", type=float, default=None,
                    dest="retained_worktree_ttl_hours",
                    help="Remove retained copy/git worktrees older than this many hours under --worker-tmp-dir")
    ap.add_argument("--worker-tmp-dir", default=None, dest="worker_tmp_dir")
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--env-inherit", action="append", default=[],
                    help="Comma-separated inherited environment variable allowlist")
    ap.add_argument("--env-block", action="append", default=[],
                    help="Comma-separated inherited environment variable denylist")
    ap.add_argument("--resume", default=None, dest="resume")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--run-mutant-id", default=None)
    args = ap.parse_args(argv)

    if args.jobs < 1:
        ap.error("--jobs must be >= 1")
    if args.batch_size < 1:
        ap.error("--batch-size must be >= 1")
    if args.batch_mutants and args.worktree_mode == "inplace":
        ap.error("--batch-mutants requires --worktree-mode copy or git-worktree")
    if args.batch_mutants and args.coverage_test_command_template:
        ap.error("--coverage-test-command-template cannot be combined with --batch-mutants")
    coverage_helper_tests = _parse_csv_items(args.coverage_helper_tests)
    if args.coverage_helper_command_template and not coverage_helper_tests:
        ap.error("--coverage-helper-command-template requires --coverage-helper-tests")
    if args.baseline_max_age_days is not None and args.baseline_max_age_days < 1:
        ap.error("--baseline-max-age-days must be >= 1")
    if args.timeout_seconds is not None and args.timeout_seconds < 1:
        ap.error("--timeout must be >= 1")
    if args.timeout_factor < 0:
        ap.error("--timeout-factor must be >= 0")
    if args.timeout_constant_ms < 0:
        ap.error("--timeout-constant-ms must be >= 0")
    if args.retained_worktree_ttl_hours is not None and args.retained_worktree_ttl_hours < 0:
        ap.error("--retained-worktree-ttl-hours must be >= 0")
    if args.dashboard_retention_days is not None and args.dashboard_retention_days < 1:
        ap.error("--dashboard-retention-days must be >= 1")
    try:
        retain_worktrees_for = _parse_retain_statuses(args.retain_worktrees_for)
    except ValueError as exc:
        ap.error(str(exc))
    retain_worktrees = bool(args.retain_worktrees or args.retain_worktrees_for)
    for name, value in (
        ("threshold", args.threshold),
        ("threshold-high", args.threshold_high),
        ("threshold-low", args.threshold_low),
        ("threshold-break", args.threshold_break),
    ):
        try:
            _validate_threshold_value(name, value)
        except ValueError as exc:
            ap.error(str(exc))
    try:
        _resolve_thresholds(args.threshold, args.threshold_high, args.threshold_low, args.threshold_break, 1.0)
    except ValueError as exc:
        ap.error(str(exc))
    if args.dry_run_only and args.skip_initial_test:
        ap.error("--dry-run-only cannot be combined with --skip-initial-test")
    if not args.skip_tests and not args.test_cmd:
        ap.error("--test-cmd is required unless --skip-tests is set")
    if args.shard_total is not None and args.shard_total < 1:
        ap.error("--shard-total must be >= 1")
    if args.shard_index is not None and args.shard_total is None:
        ap.error("--shard-index requires --shard-total")
    if args.shard_total is not None and args.shard_index is None:
        ap.error("--shard-total requires --shard-index")
    if args.shard_index is not None and args.shard_index > (args.shard_total or 0):
        ap.error("--shard-index must be <= --shard-total")
    try:
        env_overrides = _parse_env_overrides(args.env)
        env_inherit = _parse_env_names(args.env_inherit, "--env-inherit") or None
        env_block = _parse_env_names(args.env_block, "--env-block")
    except ValueError as exc:
        ap.error(str(exc))
    if args.worker_tmp_dir:
        os.makedirs(args.worker_tmp_dir, exist_ok=True)

    if args.format == "json" and args.output_format == "legacy":
        output_mode = "legacy"
    else:
        output_mode = args.output_format

    plugins = load_plugins(args.plugin, args.plugin_dir)

    try:
        enabled = normalize_mutator_list(args.mutators)
    except ValueError as exc:
        ap.error(str(exc))

    repo = _ensure_target_root(args.repo)
    files = [p.strip() for p in args.files.split(",") if p.strip()]
    retained_worktree_cleanup = _cleanup_retained_worktrees(
        repo,
        args.worker_tmp_dir,
        args.retained_worktree_ttl_hours,
    )

    if args.worktree_mode == "inplace" and not args.allow_dirty:
        dirty = _git_dirty_files(repo, files)
        if dirty:
            raise ValueError(f"refusing to mutate dirty files in inplace mode: {', '.join(dirty)} (use --allow-dirty to override)")

    if args.jobs > 1 and args.worktree_mode == "inplace":
        if not args.quiet:
            print("[error] --jobs > 1 requires --worktree-mode copy or git-worktree to avoid workspace races")
        raise ValueError("cannot run parallel in-place mutation")

    artifact_root = args.artifact_dir or os.path.join(repo, "agent_space", "stryker-cxx")
    rep = Report(
        target_files=files,
        repo=repo,
        base=args.diff_base,
        threshold=(args.threshold_break if args.threshold_break is not None else args.threshold),
        thresholds=_resolve_thresholds(args.threshold, args.threshold_high, args.threshold_low, args.threshold_break, 1.0),
        timeoutSeconds=args.timeout_seconds,
        buildCommand=args.build_cmd,
        checkCommand=args.check_cmd,
        testCommand=args.test_cmd,
        execution={
            "mode": args.mode,
            "worktreeMode": args.worktree_mode,
            "jobs": args.jobs,
            "initialTest": not args.skip_initial_test,
            "dryRunOnly": args.dry_run_only,
            "skipTests": args.skip_tests,
            "analysis": {
                "engine": args.mode,
                "macroRejectedMutants": 0,
                "macroRejections": [],
            },
            "timeoutFactor": args.timeout_factor,
            "timeoutConstantMs": args.timeout_constant_ms,
            "incremental": args.incremental,
            "batching": {
                "enabled": bool(args.batch_mutants),
                "batchSize": args.batch_size,
                "batches": 0,
                "splitBatches": 0,
                "batchedMutants": 0,
            },
            "dashboard": {
                "version": args.dashboard_version,
                "retentionDays": args.dashboard_retention_days,
                "exportPath": args.dashboard_export,
                "upload": {
                    "enabled": bool(args.dashboard_upload_url),
                    "urlConfigured": bool(args.dashboard_upload_url),
                    "authTokenEnv": args.dashboard_auth_token_env,
                    "authHeader": (
                        args.dashboard_auth_header
                        if args.dashboard_auth_token_env
                        else None
                    ),
                },
            },
            "resourceIsolation": {
                "worktreeMode": args.worktree_mode,
                "workspacePerMutant": args.worktree_mode in {"copy", "git-worktree"},
                "parallelSafe": args.worktree_mode != "inplace",
                "workerCount": args.jobs,
                "artifactDir": artifact_root,
                "retainWorktrees": retain_worktrees,
                "retainWorktreesFor": _retain_status_names(retain_worktrees_for)
                if retain_worktrees
                else [],
                "retainedWorktreeTtlHours": args.retained_worktree_ttl_hours,
                "retainedWorktreeCleanup": retained_worktree_cleanup,
                "workerTmpDir": args.worker_tmp_dir,
                "environmentKeys": sorted(env_overrides),
                "environmentInheritedKeys": sorted(env_inherit) if env_inherit is not None else ["*"],
                "environmentBlockedKeys": sorted(env_block),
                "redaction": _redaction_metadata(),
                "network": "core-offline; explicit dashboard URLs and plugin commands may use network",
            },
        },
    )
    if args.effective_config_json:
        try:
            effective_config = json.loads(args.effective_config_json)
        except Exception:
            effective_config = {}
    else:
        effective_config = {}
    rep.config = {
        "path": args.config_path,
        "hash": args.config_hash,
        "effective": effective_config,
    }
    rep.execution["plugins"] = plugins
    rep.execution["reporters"] = args.reporter
    rep.execution["providers"] = _execution_provider_summary(plugins)
    discovered: list[Mutant] = []
    for path in files:
        if path.endswith(".metal") and not args.include_metal:
            if not args.quiet:
                print(f"[skip] {path}: .metal is not C++-mutable (numeric tests cover it)")
            continue
        only = changed_lines(repo, args.diff_base, path) if args.diff_base else None
        if args.lines:
            lf = parse_lines(args.lines)
            only = lf if only is None else (only & lf)
        if only is not None and not args.quiet:
            print(f"[scope] {path}: {len(only)} lines")
        discovered.extend(
            _discover_mode(
                repo,
                path,
                only,
                enabled,
                args.mode,
                rep.execution.get("analysis"),
            )
        )

    if args.run_mutant_id:
        discovered = [m for m in discovered if m.id == args.run_mutant_id]
        if not discovered:
            print(f"[error] no mutant matched id: {args.run_mutant_id}", file=sys.stderr)
            return 1

    if args.max_mutants:
        discovered = discovered[: args.max_mutants]

    discovered = _apply_shard(discovered, args.shard_index, args.shard_total)

    rep.total = len(discovered)
    if not args.quiet:
        print(f"[stryker-cxx] {rep.total} mutants across {len(files)} file(s)\n")

    os.makedirs(artifact_root, exist_ok=True)
    _run_plugin_hooks(
        plugins,
        "preRun",
        repo,
        artifact_root,
        args.report,
        env_overrides,
        env_inherit,
        env_block,
    )

    coverage_map, coverage_tests, coverage_meta = _load_coverage(
        repo,
        args.coverage_file,
        args.coverage_provider,
        plugins,
        artifact_root,
        env_overrides,
        env_inherit,
        env_block,
        args.coverage_helper_command_template,
        coverage_helper_tests,
    )
    rep.coverage = {
        **coverage_meta,
        "coveredMutants": 0,
        "noCoverageMutants": 0,
        "testSelectionTemplate": args.coverage_test_command_template,
        "testSelectedMutants": 0,
        "testSelectionMisses": 0,
    }
    baseline_path = args.baseline_file or os.path.join(repo, ".stryker-cxx-baseline.json")
    write_baseline_path = args.write_baseline or (baseline_path if args.incremental else None)
    if args.clear_baseline and os.path.exists(baseline_path):
        os.remove(baseline_path)
    baseline_entries = _load_baseline(baseline_path) if args.incremental else {}
    baseline_key_active = bool(args.incremental or write_baseline_path)
    baseline_config_hash = _baseline_config_hash(args, enabled)
    rep.baseline = {
        "enabled": bool(args.incremental),
        "path": baseline_path if args.incremental else None,
        "writePath": write_baseline_path,
        "cacheHits": 0,
        "cacheMisses": 0,
        "cacheWrites": 0,
        "cleared": bool(args.clear_baseline),
        "maxAgeDays": args.baseline_max_age_days,
        "branch": args.baseline_branch,
        "missReasons": {},
    }

    effective_timeout_seconds = args.timeout_seconds
    if args.skip_initial_test:
        rep.dryRun = {"status": "SKIPPED", "reason": "--skip-initial-test"}
        if args.timeout_seconds is not None:
            rep.execution["effectiveTimeoutMs"] = args.timeout_seconds * 1000
    else:
        rep.dryRun = _dry_run(
            args.build_cmd,
            args.check_cmd,
            args.test_cmd,
            repo,
            artifact_root,
            args.timeout_seconds,
            args.skip_tests,
            plugins,
            env_overrides,
            env_inherit,
            env_block,
        )
        effective_timeout = _effective_timeout_ms(
            args.timeout_seconds,
            rep.dryRun,
            args.timeout_factor,
            args.timeout_constant_ms,
        )
        rep.execution["effectiveTimeoutMs"] = effective_timeout
        effective_timeout_seconds = _timeout_seconds_from_ms(effective_timeout)
        rep.timeoutSeconds = effective_timeout_seconds
        if rep.dryRun.get("status") != "PASSED":
            if not args.quiet:
                print(f"[error] dry run failed: {rep.dryRun.get('failureReason', 'unknown failure')}")
            rep.finalize()
            _write_report(args.report, rep, output_mode=output_mode)
            _write_output_artifacts(args.report, args.format, rep)
            if args.dashboard_export:
                _write_dashboard_export(args.dashboard_export, rep)
            if args.dashboard_upload_url:
                _upload_dashboard(args.dashboard_upload_url, rep)
            _run_reporter_plugins(
                plugins,
                args.reporter,
                repo,
                artifact_root,
                args.report,
                env_overrides,
                env_inherit,
                env_block,
            )
            _run_plugin_hooks(
                plugins,
                "postRun",
                repo,
                artifact_root,
                args.report,
                env_overrides,
                env_inherit,
                env_block,
            )
            return 1

    if args.dry_run_only:
        rep.finalize()
        _write_report(args.report, rep, output_mode=output_mode)
        _write_output_artifacts(args.report, args.format, rep)
        if args.dashboard_export:
            _write_dashboard_export(args.dashboard_export, rep)
        if args.dashboard_upload_url:
            _upload_dashboard(args.dashboard_upload_url, rep)
        _run_reporter_plugins(
            plugins,
            args.reporter,
            repo,
            artifact_root,
            args.report,
            env_overrides,
            env_inherit,
            env_block,
        )
        _run_plugin_hooks(
            plugins,
            "postRun",
            repo,
            artifact_root,
            args.report,
            env_overrides,
            env_inherit,
            env_block,
        )
        return 0

    resumed = _load_resumed(args.resume, {m.id for m in discovered})
    pending: list[Mutant] = []
    for m in discovered:
        if m.id in resumed:
            rep.mutants.append(_normalize_mutant_record(resumed[m.id]))
            status = str(resumed[m.id].get("status", "PENDING")).upper()
            if status == "KILLED":
                rep.killed += 1
            elif status == "SURVIVED":
                rep.survived += 1
            elif status == "BUILD_ERROR":
                rep.buildError += 1
            elif status == "CHECK_ERROR":
                rep.checkErrors += 1
            elif status == "NO_COVERAGE":
                rep.noCoverage += 1
            elif status == "TIMEOUT":
                rep.timeouts += 1
            elif status == "IGNORED":
                rep.ignored += 1
            continue
        if m.status == "IGNORED":
            rep.ignored += 1
            rec = _normalize_mutant_record(asdict(m))
            rec["resultSource"] = "ignored"
            rep.mutants.append(rec)
            continue
        if coverage_map:
            covered = _covered_lines_for(coverage_map, repo, m.file)
            if covered is not None and m.line not in covered:
                m.status = "NO_COVERAGE"
                m.detail = "mutant line was not covered by supplied coverage data"
                rep.noCoverage += 1
                rep.coverage["noCoverageMutants"] = int(rep.coverage.get("noCoverageMutants", 0)) + 1
                rec = _normalize_mutant_record(asdict(m))
                rec["resultSource"] = "coverage"
                rec["baselineKey"] = _baseline_key(m, repo, baseline_config_hash)
                rep.mutants.append(rec)
                continue
            rep.coverage["coveredMutants"] = int(rep.coverage.get("coveredMutants", 0)) + 1
            if args.coverage_test_command_template:
                tests = _covered_tests_for(coverage_tests, repo, m.file, m.line)
                if tests:
                    m.run["coveredBy"] = tests
                    m.run["selectedTestCommand"] = _coverage_selected_test_command(args.coverage_test_command_template, tests)
                    rep.coverage["testSelectedMutants"] = int(rep.coverage.get("testSelectedMutants", 0)) + 1
                else:
                    m.run["coverageSelectionMissReason"] = "no covering tests for mutant line"
                    rep.coverage["testSelectionMisses"] = int(rep.coverage.get("testSelectionMisses", 0)) + 1
        if baseline_key_active:
            key = _baseline_key(m, repo, baseline_config_hash)
            cached = baseline_entries.get(key) if args.incremental else None
            rejection = _baseline_reuse_rejection(cached, args) if args.incremental else "incremental disabled"
            if args.incremental and rejection is None:
                cached_mutant = cached["mutant"]
                status = str(cached_mutant.get("status", "")).upper()
                rec = _normalize_mutant_record(cached_mutant)
                rec["resultSource"] = "baseline"
                rec["baselineKey"] = key
                _count_status(rep, status)
                rep.baseline["cacheHits"] += 1
                rep.mutants.append(rec)
                continue
            m.run["baselineKey"] = key
            if args.incremental:
                rep.baseline["cacheMisses"] += 1
                m.run["baselineMissReason"] = rejection
                miss_reasons = rep.baseline.setdefault("missReasons", {})
                miss_reasons[rejection] = int(miss_reasons.get(rejection, 0)) + 1
        pending.append(m)

    rep.total = len(discovered)

    try:
        if pending:
            if args.batch_mutants:
                batches = _batch_mutants(pending, args.batch_size)
                rep.execution["batching"]["batches"] = len(batches)
                rep.execution["batching"]["batchedMutants"] = sum(len(batch) for batch in batches if len(batch) > 1)
                probe_payloads = [
                    (
                        batch_index,
                        batch,
                        repo,
                        args.build_cmd,
                        args.check_cmd,
                        args.test_cmd,
                        effective_timeout_seconds,
                        args.worktree_mode,
                        artifact_root,
                        args.mode,
                        args.skip_tests,
                        plugins,
                        args.worker_tmp_dir,
                        retain_worktrees,
                        retain_worktrees_for,
                        env_overrides,
                        env_inherit,
                        env_block,
                    )
                    for batch_index, batch in enumerate(batches, 1)
                    if len(batch) > 1
                ]
                rep.execution["batching"]["parallelWorkers"] = min(args.jobs, max(1, len(probe_payloads)))
                if args.jobs > 1 and len(probe_payloads) > 1:
                    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
                        probe_results = list(executor.map(_run_batch_task, probe_payloads))
                else:
                    probe_results = [_run_batch_task(payload) for payload in probe_payloads]
                probe_results_by_index = {
                    batch_index: (batch, status, detail, duration_ms, run)
                    for batch_index, batch, status, detail, duration_ms, run in probe_results
                }
                for batch_index, batch in enumerate(batches, 1):
                    if len(batch) == 1:
                        executed = _run_mutant_once(
                            batch[0],
                            repo=repo,
                            build_cmd=args.build_cmd,
                            check_cmd=args.check_cmd,
                            test_cmd=args.test_cmd,
                            timeout_seconds=effective_timeout_seconds,
                            worktree_mode=args.worktree_mode,
                            artifact_root=artifact_root,
                            execution_mode=args.mode,
                            skip_tests=args.skip_tests,
                            plugins=plugins,
                            worker_tmp_dir=args.worker_tmp_dir,
                            retain_worktrees=retain_worktrees,
                            retain_worktrees_for=retain_worktrees_for,
                            env_overrides=env_overrides,
                            env_inherit=env_inherit,
                            env_block=env_block,
                        )
                        _count_status(rep, executed.status)
                        executed.run["reproCommand"] = mutation_repro_command(executed, repo, args.build_cmd, str(executed.run.get("selectedTestCommand") or args.test_cmd or ""), args.report)
                        rep.mutants.append(_executed_record(executed))
                        _write_report(args.report, rep, output_mode=output_mode)
                        continue

                    batch, status, detail, duration_ms, run = probe_results_by_index[batch_index]
                    if status == "SURVIVED":
                        for mut in batch:
                            mut.status = "SURVIVED"
                            mut.detail = detail
                            mut.durationMs = duration_ms
                            mut.run.update(run)
                            mut.run["reproCommand"] = mutation_repro_command(mut, repo, args.build_cmd, args.test_cmd or "", args.report)
                            _count_status(rep, mut.status)
                            rep.mutants.append(_executed_record(mut, "batch"))
                        _write_report(args.report, rep, output_mode=output_mode)
                        if not args.quiet:
                            print(f"[batch {batch_index}/{len(batches)}] {len(batch)} mutants ... SURVIVED ({duration_ms}ms)")
                        continue

                    rep.execution["batching"]["splitBatches"] += 1
                    if not args.quiet:
                        print(f"[batch {batch_index}/{len(batches)}] {len(batch)} mutants ... {status}; splitting")
                    for mut in batch:
                        executed = _run_mutant_once(
                            mut,
                            repo=repo,
                            build_cmd=args.build_cmd,
                            check_cmd=args.check_cmd,
                            test_cmd=args.test_cmd,
                            timeout_seconds=effective_timeout_seconds,
                            worktree_mode=args.worktree_mode,
                            artifact_root=artifact_root,
                            execution_mode=args.mode,
                            skip_tests=args.skip_tests,
                            plugins=plugins,
                            worker_tmp_dir=args.worker_tmp_dir,
                            retain_worktrees=retain_worktrees,
                            retain_worktrees_for=retain_worktrees_for,
                            env_overrides=env_overrides,
                            env_inherit=env_inherit,
                            env_block=env_block,
                        )
                        _count_status(rep, executed.status)
                        executed.run["reproCommand"] = mutation_repro_command(executed, repo, args.build_cmd, str(executed.run.get("selectedTestCommand") or args.test_cmd or ""), args.report)
                        rep.mutants.append(_executed_record(executed))
                        _write_report(args.report, rep, output_mode=output_mode)
            elif args.jobs > 1:
                if not args.quiet:
                    print(f"[stryker-cxx] running {len(pending)} mutants with {args.jobs} workers")
                payloads = [
                    (
                        mut,
                        repo,
                        args.build_cmd,
                        args.check_cmd,
                        args.test_cmd,
                        effective_timeout_seconds,
                        args.worktree_mode,
                        artifact_root,
                        args.mode,
                        args.skip_tests,
                        plugins,
                        args.worker_tmp_dir,
                        retain_worktrees,
                        retain_worktrees_for,
                        env_overrides,
                        env_inherit,
                        env_block,
                    )
                    for mut in pending
                ]
                with ThreadPoolExecutor(max_workers=args.jobs) as executor:
                    for idx, executed in enumerate(executor.map(_run_mutant_task, payloads), 1):
                        if executed.status == "KILLED":
                            rep.killed += 1
                        elif executed.status == "SURVIVED":
                            rep.survived += 1
                        elif executed.status == "BUILD_ERROR":
                            rep.buildError += 1
                        elif executed.status == "CHECK_ERROR":
                            rep.checkErrors += 1
                        elif executed.status == "NO_COVERAGE":
                            rep.noCoverage += 1
                        elif executed.status == "TIMEOUT":
                            rep.timeouts += 1

                        if not args.quiet:
                            tag = (
                                f"{executed.file.split('/')[-1]}:{executed.line} "
                                f"{executed.original}->{executed.mutated} [{executed.mutator}]"
                            )
                            print(f"[{idx}/{len(pending)}] {tag} ... {executed.status} ({executed.durationMs}ms)")
                        executed.run["reproCommand"] = mutation_repro_command(executed, repo, args.build_cmd, str(executed.run.get("selectedTestCommand") or args.test_cmd or ""), args.report)
                        rec = _normalize_mutant_record(asdict(executed))
                        rec["resultSource"] = "executed"
                        if isinstance(executed.run, dict) and executed.run.get("baselineKey"):
                            rec["baselineKey"] = executed.run["baselineKey"]
                        rep.mutants.append(rec)
                        _write_report(args.report, rep, output_mode=output_mode)
            else:
                for idx, mut in enumerate(pending, 1):
                    if not args.quiet:
                        tag = f"{mut.file.split('/')[-1]}:{mut.line} {mut.original}->{mut.mutated} [{mut.mutator}]"
                        print(f"[{idx}/{len(pending)}] {tag} ... ", end="", flush=True)
                    executed = _run_mutant_once(
                        mut,
                        repo=repo,
                        build_cmd=args.build_cmd,
                        check_cmd=args.check_cmd,
                        test_cmd=args.test_cmd,
                        timeout_seconds=effective_timeout_seconds,
                        worktree_mode=args.worktree_mode,
                        artifact_root=artifact_root,
                        execution_mode=args.mode,
                        skip_tests=args.skip_tests,
                        plugins=plugins,
                        worker_tmp_dir=args.worker_tmp_dir,
                        retain_worktrees=retain_worktrees,
                        retain_worktrees_for=retain_worktrees_for,
                        env_overrides=env_overrides,
                        env_inherit=env_inherit,
                        env_block=env_block,
                    )
                    if not args.quiet:
                        print(f"{executed.status} ({executed.durationMs}ms)")
                    if executed.status == "KILLED":
                        rep.killed += 1
                    elif executed.status == "SURVIVED":
                        rep.survived += 1
                    elif executed.status == "BUILD_ERROR":
                        rep.buildError += 1
                    elif executed.status == "CHECK_ERROR":
                        rep.checkErrors += 1
                    elif executed.status == "NO_COVERAGE":
                        rep.noCoverage += 1
                    elif executed.status == "TIMEOUT":
                        rep.timeouts += 1
                    executed.run["reproCommand"] = mutation_repro_command(executed, repo, args.build_cmd, str(executed.run.get("selectedTestCommand") or args.test_cmd or ""), args.report)
                    rec = _normalize_mutant_record(asdict(executed))
                    rec["resultSource"] = "executed"
                    if isinstance(executed.run, dict) and executed.run.get("baselineKey"):
                        rec["baselineKey"] = executed.run["baselineKey"]
                    rep.mutants.append(rec)
                    _write_report(args.report, rep, output_mode=output_mode)
    finally:
        if args.worktree_mode == "inplace":
            for path in files:
                if _is_tracked_file(repo, path):
                    subprocess.run(["git", "-C", repo, "checkout", "--", path], check=False)

    rep.finalize()
    rep.thresholds = _resolve_thresholds(args.threshold, args.threshold_high, args.threshold_low, args.threshold_break, rep.score)
    if write_baseline_path:
        for rec in rep.mutants:
            status = str(rec.get("status", "")).upper()
            key = rec.get("baselineKey")
            if isinstance(key, str) and status in FATAL_STATUSES:
                if args.baseline_branch:
                    rec["baselineBranch"] = args.baseline_branch
                baseline_entries[key] = _baseline_entry(rec)
        _write_baseline(write_baseline_path, baseline_entries)
        rep.baseline["cacheWrites"] = len([m for m in rep.mutants if isinstance(m.get("baselineKey"), str)])
    _write_report(args.report, rep, output_mode=output_mode)

    _write_output_artifacts(args.report, args.format, rep)
    if args.dashboard_export:
        _write_dashboard_export(args.dashboard_export, rep)
    if args.dashboard_upload_url:
        _upload_dashboard(
            args.dashboard_upload_url,
            rep,
            args.dashboard_auth_token_env,
            args.dashboard_auth_header,
        )
    _run_reporter_plugins(
        plugins,
        args.reporter,
        repo,
        artifact_root,
        args.report,
        env_overrides,
        env_inherit,
        env_block,
    )
    _run_plugin_hooks(
        plugins,
        "postRun",
        repo,
        artifact_root,
        args.report,
        env_overrides,
        env_inherit,
        env_block,
    )

    if not args.quiet:
        print(
            f"[stryker-cxx] score={rep.score:.2f} killed={rep.killed} "
            f"survived={rep.survived} build_error={rep.buildError} "
            f"check_error={rep.checkErrors} no_coverage={rep.noCoverage} "
            f"timeouts={rep.timeouts} ignored={rep.ignored}",
        )
    for m in rep.mutants:
        if m["status"] == "SURVIVED":
            print(f"  SURVIVOR {m['file']}:{m['line']} {m['original']}->{m['mutated']} ({m['mutator']})")

    if rep.total == 0:
        if args.fail_on_empty:
            return 3
        return 0

    effective_threshold = rep.thresholds["break"]
    if rep.score < effective_threshold:
        return 2

    return 0


def _workspace_is_retained(retain: bool, retain_state: dict[str, bool] | None) -> bool:
    return retain or bool(retain_state and retain_state.get("retain"))


@contextlib.contextmanager
def _workspace(
    repo: str,
    mode: str,
    worker_tmp_dir: str | None = None,
    retain: bool = False,
    retain_state: dict[str, bool] | None = None,
):
    if mode == "inplace":
        yield repo
        return

    if worker_tmp_dir:
        os.makedirs(worker_tmp_dir, exist_ok=True)

    if mode == "git-worktree":
        if not os.path.isdir(os.path.join(repo, ".git")):
            raise ValueError("git-worktree mode requires --repo to point at a git work tree")

        workspace_root = tempfile.mkdtemp(prefix="stryker-cxx-worktree-", dir=worker_tmp_dir)
        workdir = os.path.join(workspace_root, "worktree")
        try:
            subprocess.run(
                ["git", "-C", repo, "worktree", "add", "--detach", workdir, "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            )
            yield workdir
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

    if mode == "copy":
        workdir = tempfile.mkdtemp(prefix="stryker-cxx-copy-", dir=worker_tmp_dir)
        try:
            shutil.copytree(repo, workdir, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
            yield workdir
        finally:
            if not _workspace_is_retained(retain, retain_state):
                shutil.rmtree(workdir, ignore_errors=True)
        return

    raise ValueError(f"unsupported worktree mode: {mode}")


if __name__ == "__main__":
    sys.exit(main())
