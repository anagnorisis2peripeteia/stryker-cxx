#!/usr/bin/env python3
"""CLI for the standalone stryker-cxx tool."""

from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

from . import engine
from .build_adapters import (
    adapter_commands as _adapter_commands,
    checker_command as _checker_command,
    discover_test_binary as _discover_test_binary,
)
from .schema import TOOL_VERSION

DEFAULT_CONFIG_FILES = ["stryker-cxx.yml", ".stryker-cxx.yml"]
VERSION = TOOL_VERSION
REDACTED_VALUE = "[REDACTED]"
PLUGIN_MANIFEST = "stryker-cxx-plugin.json"
SENSITIVE_KEY_RE = re.compile(
    r"(^|_)(TOKEN|SECRET|PASSWORD|PASSWD|PWD|CREDENTIAL|API_?KEY|"
    r"ACCESS_?KEY|PRIVATE_?KEY|AUTH|BEARER)($|_)",
    re.IGNORECASE,
)
EXECUTION_MODES = {"source-overlay", "mutant-switch"}
EXECUTION_BACKENDS = {"auto", "source-overlay", "mutant-switch", "compiled-artifact", "llvm-switch"}
SHELL_ASSIGNMENT_RE = re.compile(
    r"(?P<prefix>(^|[\s;])(?:export\s+)?)"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)="
    r"(?P<value>'[^']*'|\"[^\"]*\"|[^\s;]+)"
)


DEFAULT_CONFIG = """schemaVersion: stryker-cxx.config.v1
files:
  include:
    - "src/**/*.cpp"
  exclude:
    - "**/test/**"
mutators:
  - ConditionalBoundary
  - EqualityOperator
  - LogicalOperator
  - BooleanLiteral
thresholds:
  high: 0.9
  low: 0.7
  break: 0.6
execution:
  mode: token
  mutationLevel: Standard
  executionMode: source-overlay
  executionBackend: auto
  equivalentSuppression: conservative
  buildSystem: cmake
  buildDir: build
  buildTarget: ""
  artifactPath: ""
  xcodeWorkspace: ""
  xcodeProject: ""
  xcodeScheme: ""
  xcodeConfiguration: ""
  xcodeSdk: ""
  xcodeDestination: ""
  checkSystem: ""
  checkArgs: ""
  testFilter: ""
  testFramework: ""
  testBinary: ""
  xctestBundle: ""
  xctestDestination: ""
  xctestOnlyTesting: []
  xctestSkipTesting: []
  worktreeMode: copy
  artifactBackend: source-overlay
  artifactFallback: none
  jobs: 1
  batchMutants: false
  batchSize: 4
  retainWorktrees: false
  retainWorktreesFor: []
  retainedWorktreeTtlHours: null
  workerTmpDir: ""
  workerLabel: ""
  env: {}
  envInherit: []
  envBlock: []
  buildCommand: "ninja -C build"
  checkCommand: ""
  testCommand: "./build/tests"
  coverageHelperCommandTemplate: ""
  coverageHelperTests: []
  timeoutFactor: 1.5
  timeoutConstantMs: 5000
  incremental: false
  baselineFile: ".stryker-cxx-baseline.json"
  plugins: []
  pluginDirs: []
  reporters: []
  dashboardVersion: "1"
  dashboardRetentionDays: null
  dashboardProject: ""
  dashboardBranch: ""
  dashboardCommit: ""
  dashboardBuildUrl: ""
  dashboardAuthTokenEnv: ""
  dashboardAuthHeader: "Authorization"
  dashboardUploadRetries: 0
  dashboardUploadRetryDelayMs: 1000
  distributionManifest: ""
report:
  failOnEmpty: true
"""


CONFIG_PRESETS: dict[str, dict[str, str]] = {
    "cmake": {
        "buildSystem": "cmake",
        "buildDir": "build",
        "testFilter": "",
    },
    "cmake-gtest": {
        "buildSystem": "cmake",
        "buildDir": "build",
        "testFramework": "gtest",
        "testFilter": "",
    },
    "cmake-catch2": {
        "buildSystem": "cmake",
        "buildDir": "build",
        "testFramework": "catch2",
        "testFilter": "",
    },
    "cmake-doctest": {
        "buildSystem": "cmake",
        "buildDir": "build",
        "testFramework": "doctest",
        "testFilter": "",
    },
    "ctest": {
        "buildSystem": "ctest",
        "buildDir": "build",
        "testFilter": "",
    },
    "ninja": {
        "buildSystem": "ninja",
        "buildDir": "build",
        "testTarget": "test",
    },
    "ninja-gtest": {
        "buildSystem": "ninja",
        "buildDir": "build",
        "testTarget": "test",
        "testFramework": "gtest",
        "testFilter": "",
    },
    "make": {
        "buildSystem": "make",
        "buildDir": ".",
        "testTarget": "test",
    },
    "meson": {
        "buildSystem": "meson",
        "buildDir": "build",
        "testTarget": "",
    },
    "meson-catch2": {
        "buildSystem": "meson",
        "buildDir": "build",
        "testTarget": "",
        "testFramework": "catch2",
        "testFilter": "",
    },
    "bazel": {
        "buildSystem": "bazel",
        "buildTarget": "//...",
        "testTarget": "//...",
    },
    "bazel-gtest": {
        "buildSystem": "bazel",
        "buildTarget": "//...",
        "testTarget": "//...",
        "testFramework": "gtest",
        "testFilter": "",
    },
    "xcodebuild": {
        "buildSystem": "xcodebuild",
        "xcodeScheme": "",
        "xcodeConfiguration": "Debug",
        "xcodeDestination": "",
        "testFramework": "xctest",
    },
}


def _config_for_preset(preset: str | None) -> str:
    if not preset:
        return DEFAULT_CONFIG
    values = dict(CONFIG_PRESETS[preset])
    values["buildCommand"] = ""
    values["testCommand"] = ""
    inserted: set[str] = set()
    out: list[str] = []
    in_execution = False

    def scalar(value: str) -> str:
        return json.dumps(value)

    for line in DEFAULT_CONFIG.splitlines():
        if line == "execution:":
            in_execution = True
            out.append(line)
            continue
        if in_execution and line and not line.startswith("  "):
            for key, value in values.items():
                if key not in inserted:
                    out.append(f"  {key}: {scalar(value)}")
                    inserted.add(key)
            in_execution = False
        if in_execution and line.startswith("  "):
            key = line.strip().split(":", 1)[0]
            if key in values:
                out.append(f"  {key}: {scalar(values[key])}")
                inserted.add(key)
                continue
        out.append(line)

    if in_execution:
        for key, value in values.items():
            if key not in inserted:
                out.append(f"  {key}: {scalar(value)}")
                inserted.add(key)
    return "\n".join(out) + "\n"


CONFIG_ALLOWED_TOP_LEVEL = {
    "schemaVersion",
    "base",
    "since",
    "files",
    "mutators",
    "mutationLevel",
    "thresholds",
    "execution",
    "mutationArtifact",
    "report",
    "coverageFile",
    "coverageAnalysis",
    "executionBackend",
    "coverageProvider",
    "coverageHelperCommandTemplate",
    "coverageHelperTests",
    "baselineFile",
    "writeBaseline",
    "plugins",
    "pluginDirs",
}
CONFIG_ALLOWED_NESTED = {
    "files": {"include", "exclude"},
    "thresholds": {"high", "low", "break"},
    "report": {"threshold", "thresholdBreak", "thresholds", "failOnEmpty", "fail_on_empty"},
    "execution": {
        "buildCommand",
        "checkCommand",
        "checkSystem",
        "checkArgs",
        "testCommand",
        "maxMutants",
        "includeMetal",
        "mutators",
        "mutationLevel",
        "mutationMutators",
        "threshold",
        "thresholdHigh",
        "thresholdLow",
        "thresholdBreak",
        "timeoutSeconds",
        "timeoutFactor",
        "timeoutConstantMs",
        "artifactDir",
        "artifactBackend",
        "artifactFallback",
        "artifactPath",
        "retainWorktrees",
        "retainWorktreesFor",
        "retainedWorktreeTtlHours",
        "workerTmpDir",
        "workerLabel",
        "env",
        "envInherit",
        "envBlock",
        "mode",
        "executionMode",
        "executionBackend",
        "jobs",
        "worktreeMode",
        "workTreeMode",
        "format",
        "outputFormat",
        "skipInitialTest",
        "initialTest",
        "dryRunOnly",
        "skipTests",
        "coverageFile",
        "coverageAnalysis",
        "coverageProvider",
        "coverageTestCommandTemplate",
        "coverageHelperCommandTemplate",
        "coverageHelperTests",
        "equivalentSuppression",
        "buildSystem",
        "buildDir",
        "buildTarget",
        "xcodeWorkspace",
        "xcodeProject",
        "xcodeScheme",
        "xcodeConfiguration",
        "xcodeSdk",
        "xcodeDestination",
        "testTarget",
        "testFilter",
        "testFramework",
        "testBinary",
        "xctestBundle",
        "xctestDestination",
        "xctestOnlyTesting",
        "xctestSkipTesting",
        "plugins",
        "pluginDirs",
        "reporters",
        "dashboardExport",
        "dashboardUploadUrl",
        "dashboardVersion",
        "dashboardRetentionDays",
        "dashboardProject",
        "dashboardBranch",
        "dashboardCommit",
        "dashboardBuildUrl",
        "dashboardAuthTokenEnv",
        "dashboardAuthHeader",
        "dashboardUploadRetries",
        "dashboardUploadRetryDelayMs",
        "distributionManifest",
        "batchMutants",
        "batchSize",
        "incremental",
        "baselineFile",
        "baselineMaxAgeDays",
        "baselineBranch",
        "writeBaseline",
        "clearBaseline",
    },
    "mutationArtifact": {"backend", "fallback", "retainArtifactsFor"},
}


def _validate_config_shape(cfg: dict[str, Any], path: str) -> None:
    unknown_top = sorted(set(cfg) - CONFIG_ALLOWED_TOP_LEVEL)
    if unknown_top:
        raise ValueError(f"{path}: unknown config keys: {', '.join(unknown_top)}")
    for key, allowed in CONFIG_ALLOWED_NESTED.items():
        section = cfg.get(key)
        if section is None:
            continue
        if not isinstance(section, dict):
            if key in {"files", "thresholds", "report", "execution"}:
                if key == "files" and isinstance(section, (str, list)):
                    continue
                if key == "thresholds":
                    raise ValueError(f"{path}: config section '{key}' must be an object")
            continue
        unknown = sorted(set(section) - allowed)
        if unknown:
            raise ValueError(f"{path}: unknown config keys in {key}: {', '.join(unknown)}")


def _parse_yaml_scalar(value: str) -> Any:
    raw = value.strip()
    if raw == "null":
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    try:
        if raw == "":
            return raw
        if raw[0] in {"'", '"'} and raw[-1] == raw[0]:
            return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        pass
    if raw == "{}":
        return {}
    if raw == "[]":
        return []
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        values: list[str] = []
        current = []
        in_single = False
        in_double = False
        escaped = False
        for char in inner:
            if escaped:
                current.append(char)
                escaped = False
                continue
            if char == "\\" and in_double:
                escaped = True
                current.append(char)
                continue
            if char == "'" and not in_double:
                in_single = not in_single
                current.append(char)
                continue
            if char == '"' and not in_single:
                in_double = not in_double
                current.append(char)
                continue
            if char == "," and not in_single and not in_double:
                part = "".join(current).strip()
                if part:
                    values.append(part)
                current = []
                continue
            current.append(char)
        if current:
            values.append("".join(current).strip())
        return [_parse_yaml_scalar(item) for item in values]
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def _parse_yaml_text(path: str) -> dict[str, Any]:
    with open(path) as f:
        lines = f.readlines()
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]

    def next_significant(index: int) -> tuple[int, str] | None:
        idx = index
        while idx < len(lines):
            candidate = lines[idx]
            if candidate.strip() and not candidate.lstrip().startswith("#"):
                return idx, candidate
            idx += 1
        return None

    index = 0
    while index < len(lines):
        raw_line = lines[index]
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            index += 1
            continue

        if "\t" in raw_line:
            raise ValueError(f"{path}: YAML indentation cannot contain tabs")
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()

        while indent < stack[-1][0]:
            stack.pop()
        container = stack[-1][1]

        if line.startswith("- "):
            if not isinstance(container, list):
                raise ValueError(f"{path}: list item outside list at line {index + 1}: {line}")
            item = line[2:].strip()
            container.append(_parse_yaml_scalar(item))
            index += 1
            continue

        if not isinstance(container, dict):
            raise ValueError(f"{path}: invalid block at line {index + 1}: {line}")

        key, _, remainder = line.partition(":")
        if not key:
            raise ValueError(f"{path}: invalid YAML line at {index + 1}: {line}")
        value = remainder.strip()
        if value:
            container[key.strip()] = _parse_yaml_scalar(value)
            index += 1
            continue

        next_line = next_significant(index + 1)
        child: Any
        child_indent = indent + 2
        if next_line is None:
            child = {}
        else:
            child_indent = len(next_line[1]) - len(next_line[1].lstrip(" "))
            if child_indent <= indent:
                child = {}
            elif next_line[1].strip().startswith("- "):
                child = []
            else:
                child = {}
        container[key.strip()] = child
        stack.append((child_indent, child))
        index += 1

    return root if isinstance(root, dict) else {}


def _load_config(path: str | None, validate: bool = True) -> dict[str, Any]:
    if not path:
        return {}
    if not os.path.exists(path):
        return {}

    ext = os.path.splitext(path)[1].lower()
    if ext in {".json", ".js"}:
        with open(path) as f:
            raw = json.load(f)
        cfg = raw if isinstance(raw, dict) else {}
        if validate:
            _validate_config_shape(cfg, path)
        return cfg

    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        data = _parse_yaml_text(path)
    else:
        with open(path) as f:
            data = yaml.safe_load(f)

    cfg = data if isinstance(data, dict) else {}
    if validate:
        _validate_config_shape(cfg, path)
    return cfg


def _collect_manifest_payloads(
    plugin_paths: list[str],
    plugin_dirs: list[str],
) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for path in plugin_paths:
        if not path:
            continue
        with open(path) as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            raise ValueError(f"plugin manifest must be an object: {path}")
        manifests.append(payload)

    for directory in plugin_dirs:
        if not directory:
            continue
        manifest = os.path.join(directory, PLUGIN_MANIFEST)
        if not os.path.exists(manifest):
            raise ValueError(f"plugin directory missing {PLUGIN_MANIFEST}: {directory}")
        with open(manifest) as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            raise ValueError(f"plugin manifest must be an object: {manifest}")
        manifests.append(payload)
    return manifests


def _run_config_loader(
    command: str,
    plugin_name: str,
    repo: str | None,
) -> dict[str, Any]:
    env = os.environ.copy()
    if repo:
        env["STRYKER_CXX_REPO"] = repo
    env["STRYKER_CXX_PLUGIN"] = plugin_name
    proc = subprocess.run(
        command,
        cwd=repo,
        shell=True,
        text=True,
        capture_output=True,
        env=env,
    )
    if proc.returncode != 0:
        detail = proc.stderr or proc.stdout
        raise ValueError(
            f"plugin {plugin_name} configLoader command failed: "
            f"exit {proc.returncode}: {detail}".strip()
        )
    raw = proc.stdout.strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"plugin {plugin_name} configLoader command did not emit JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(f"plugin {plugin_name} configLoader output must be a JSON object")
    return payload


def _merge_configs(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge_configs(merged[key], value)
        else:
            merged[key] = value
    return merged


def _pick_config_path(explicit: str | None, repo_root: str | None = None) -> str:
    if explicit:
        return explicit
    search_roots = [os.getcwd()]
    if repo_root:
        search_roots.insert(0, repo_root)
    for root in search_roots:
        for candidate in DEFAULT_CONFIG_FILES:
            path = os.path.join(root, candidate)
            if os.path.exists(path):
                return path
    return explicit or ""


def _coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _apply_config_loader_plugins(
    base_config: dict[str, Any],
    cli_plugins: list[str],
    cli_plugin_dirs: list[str],
    repo: str | None,
) -> dict[str, Any]:
    execution = base_config.get("execution", {}) if isinstance(base_config.get("execution"), dict) else {}
    config_plugin_paths = list(
        dict.fromkeys(_coerce_list(base_config.get("plugins")) + _coerce_list(execution.get("plugins")))
    )
    config_plugin_dirs = list(
        dict.fromkeys(_coerce_list(base_config.get("pluginDirs")) + _coerce_list(execution.get("pluginDirs")))
    )
    manifests = _collect_manifest_payloads(
        list(dict.fromkeys(cli_plugins + config_plugin_paths)),
        list(dict.fromkeys(cli_plugin_dirs + config_plugin_dirs)),
    )
    merged = base_config
    for manifest in manifests:
        capabilities = manifest.get("capabilities")
        if not isinstance(capabilities, dict):
            continue
        loader = capabilities.get("configLoader")
        if not isinstance(loader, dict):
            continue
        command = loader.get("command")
        if not isinstance(command, str) or not command.strip():
            continue
        merged = _merge_configs(
            merged,
            _run_config_loader(command, str(manifest.get("name", "plugin")), repo),
        )
    return merged


def _coerce_env_args(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [f"{key}={val}" for key, val in value.items()]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    return []


def _coerce_mutator_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, dict):
        return _coerce_mutator_list(value.get("enabled"))
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _looks_sensitive_key(key: str) -> bool:
    return bool(SENSITIVE_KEY_RE.search(key))


def _redact_shell_assignments(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group("key")
        raw_value = match.group("value")
        if not _looks_sensitive_key(key):
            return match.group(0)
        if raw_value.startswith("'") and raw_value.endswith("'"):
            redacted = f"'{REDACTED_VALUE}'"
        elif raw_value.startswith('"') and raw_value.endswith('"'):
            redacted = f'"{REDACTED_VALUE}"'
        else:
            redacted = REDACTED_VALUE
        return f"{match.group('prefix')}{key}={redacted}"

    return SHELL_ASSIGNMENT_RE.sub(replace, value)


def _redact_env_entry(value: str) -> str:
    key, separator, _ = value.partition("=")
    if not separator:
        return value
    return f"{key}{separator}{REDACTED_VALUE}"


def _redact_config_value(key: str, value: Any) -> Any:
    if key == "dashboardUploadUrl":
        return REDACTED_VALUE if value else value
    if key == "env":
        if isinstance(value, dict):
            return {str(env_key): REDACTED_VALUE for env_key in value}
        if isinstance(value, list):
            return [
                _redact_env_entry(str(item))
                for item in value
            ]
        if isinstance(value, str):
            return _redact_env_entry(value)
    if _looks_sensitive_key(key):
        return REDACTED_VALUE
    if isinstance(value, dict):
        return {
            str(child_key): _redact_config_value(str(child_key), child_value)
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_config_value(key, item) for item in value]
    if isinstance(value, str):
        return _redact_shell_assignments(value)
    return value


def _redact_effective_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _redact_config_value(key, value)
        for key, value in payload.items()
    }


def _stable_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")).hexdigest()


def _effective_config_payload(cfg: dict[str, Any]) -> dict[str, Any]:
    redacted = {
        key: value
        for key, value in cfg.items()
        if key not in {"effective_config_json", "config_hash"}
    }
    return redacted


def _apply_file_filters(paths: list[str], includes: list[str], excludes: list[str]) -> list[str]:
    out = list(dict.fromkeys(paths))
    if includes:
        out = [p for p in out if any(fnmatch.fnmatch(p, pat) for pat in includes)]
    if excludes:
        out = [p for p in out if not any(fnmatch.fnmatch(p, pat) for pat in excludes)]
    return out


def _to_linespec(base_files: list[str] | None, fallback: str | None = None) -> str | None:
    if base_files:
        return ",".join(base_files)
    return fallback


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stryker-cxx")
    parser.add_argument("--version", action="version", version=f"stryker-cxx {VERSION}")
    parser.add_argument("--config", default=None, help="Optional YAML/JSON config file")

    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="write a starter stryker-cxx.yml config")
    init.add_argument("--path", default="stryker-cxx.yml")
    init.add_argument("--preset", choices=sorted(CONFIG_PRESETS), default=None)
    init.add_argument("--force", action="store_true")

    baseline_merge = subparsers.add_parser("baseline-merge", help="merge baseline cache files")
    baseline_merge.add_argument("--output", required=True)
    baseline_merge.add_argument("inputs", nargs="+")

    baseline_prune = subparsers.add_parser("baseline-prune", help="prune baseline cache entries")
    baseline_prune.add_argument("--baseline-file", required=True)
    baseline_prune.add_argument("--repo", default=None)
    baseline_prune.add_argument("--max-entries", type=int, default=None)

    baseline_info = subparsers.add_parser("baseline-info", help="summarize baseline cache entries")
    baseline_info.add_argument("--baseline-file", required=True)
    baseline_info.add_argument("--repo", default=None)

    baseline_history = subparsers.add_parser("baseline-history", help="show baseline cache update history")
    baseline_history.add_argument("--baseline-file", required=True)
    baseline_history.add_argument("--repo", default=None)
    baseline_history.add_argument("--limit", type=int, default=20)
    baseline_history.add_argument("--status", default=None)
    baseline_history.add_argument("--branch", default=None)

    parity_audit = subparsers.add_parser("parity-audit", help="summarize Mull/Stryker.NET parity coverage from a report")
    parity_audit.add_argument("--report", required=True)
    parity_audit.add_argument("--format", choices=["json", "markdown"], default="json")
    parity_audit.add_argument(
        "--profile",
        choices=["summary", "review", "strict"],
        default="summary",
        help="summary prints only; review fails missing items; strict fails missing or partial items",
    )

    run = subparsers.add_parser("run", help="discover, mutate, build, and run tests")
    run.add_argument("--config", default=None, help="Optional YAML/JSON config file")
    run.add_argument("--repo", required=True)
    run.add_argument("--files", required=False)
    run.add_argument("--base", default=None)
    run.add_argument("--since", default=None, help="Stryker.NET-style alias for --base")
    run.add_argument("--lines", default=None)
    run.add_argument("--build-command", required=False, dest="build_command")
    run.add_argument("--check-command", required=False, dest="check_command")
    run.add_argument("--test-command", required=False, dest="test_command")
    run.add_argument("--build-system", choices=["cmake", "ctest", "ninja", "make", "meson", "bazel", "xcodebuild"], default=None)
    run.add_argument("--build-dir", default=None)
    run.add_argument("--build-target", default=None)
    run.add_argument("--artifact-path", default=None, dest="artifact_path")
    run.add_argument("--xcode-workspace", default=None, dest="xcode_workspace")
    run.add_argument("--xcode-project", default=None, dest="xcode_project")
    run.add_argument("--xcode-scheme", default=None, dest="xcode_scheme")
    run.add_argument("--xcode-configuration", default=None, dest="xcode_configuration")
    run.add_argument("--xcode-sdk", default=None, dest="xcode_sdk")
    run.add_argument("--xcode-destination", default=None, dest="xcode_destination")
    run.add_argument("--check-system", choices=["clang", "clang++", "clang-tidy", "cppcheck"], default=None, dest="check_system")
    run.add_argument("--check-args", default=None, dest="check_args")
    run.add_argument("--test-target", default=None)
    run.add_argument("--test-filter", default=None)
    run.add_argument("--test-framework", choices=["gtest", "googletest", "catch2", "doctest", "xctest"], default=None)
    run.add_argument("--test-binary", default=None)
    run.add_argument("--xctest-bundle", default=None)
    run.add_argument("--xctest-destination", default=None, dest="xctest_destination")
    run.add_argument(
        "--xctest-only-testing",
        action="append",
        default=[],
        dest="xctest_only_testing",
    )
    run.add_argument(
        "--xctest-skip-testing",
        action="append",
        default=[],
        dest="xctest_skip_testing",
    )
    run.add_argument("--report", required=True)
    run.add_argument("--max-mutants", type=int, default=None)
    run.add_argument("--include-metal", action="store_true")
    run.add_argument("--include", default=None)
    run.add_argument("--exclude", default=None)
    run.add_argument("--mutators", default=None)
    run.add_argument("--mutation-level", choices=sorted(engine.MUTATION_LEVEL_NAMES), default=None, dest="mutation_level")
    run.add_argument("--output-format", choices=["legacy", "stryker-cxx"], default="stryker-cxx")
    run.add_argument("--format", choices=["json", "markdown", "html", "sarif", "github-annotations", "mutation-testing-elements"], default="json")
    run.add_argument("--mode", choices=["token", "clang", "clang-ast"], default=None)
    run.add_argument("--execution-mode", choices=sorted(EXECUTION_MODES), default=None, dest="execution_mode")
    run.add_argument("--execution-backend", choices=sorted(EXECUTION_BACKENDS), default=None, dest="execution_backend")
    run.add_argument("--equivalent-suppression", choices=["off", "conservative", "aggressive"], default=None, dest="equivalent_suppression")
    run.add_argument("--jobs", type=int, default=None, help="Parallel mutant execution with isolated worktrees.")
    run.add_argument("--batch-mutants", action="store_true", dest="batch_mutants")
    run.add_argument("--batch-size", type=int, default=None, dest="batch_size")
    run.add_argument("--artifact-backend", choices=["source-overlay", "compiled-executable", "compiled-library", "compiled-object"], default=None, dest="artifact_backend")
    run.add_argument("--artifact-fallback", choices=["none", "source-overlay"], default=None, dest="artifact_fallback")
    run.add_argument("--worktree-mode", dest="worktree_mode", choices=["inplace", "git-worktree", "copy"], default=None)
    run.add_argument("--allow-dirty", action="store_true")
    run.add_argument("--threshold", type=float, default=None)
    run.add_argument("--threshold-high", type=float, default=None, dest="threshold_high")
    run.add_argument("--threshold-low", type=float, default=None, dest="threshold_low")
    run.add_argument("--threshold-break", type=float, default=None, dest="threshold_break")
    run.add_argument("--fail-on-empty", action="store_true", dest="fail_on_empty")
    run.add_argument("--timeout", type=int, default=None, dest="timeout", help="Per-mutant timeout in seconds")
    run.add_argument("--timeout-factor", type=float, default=None, dest="timeout_factor")
    run.add_argument("--timeout-constant-ms", type=int, default=None, dest="timeout_constant_ms")
    run.add_argument("--skip-initial-test", action="store_true", dest="skip_initial_test")
    run.add_argument("--dry-run-only", action="store_true", dest="dry_run_only")
    run.add_argument("--skip-tests", action="store_true", dest="skip_tests")
    run.add_argument("--coverage-file", default=None, dest="coverage_file")
    run.add_argument("--coverage-analysis", default=None, choices=["off", "all", "perTest", "perTestInIsolation"], dest="coverage_analysis")
    run.add_argument("--coverage-provider", default=None, dest="coverage_provider")
    run.add_argument("--coverage-test-command-template", default=None, dest="coverage_test_command_template")
    run.add_argument("--coverage-helper-command-template", default=None, dest="coverage_helper_command_template")
    run.add_argument("--coverage-helper-tests", action="append", default=[], dest="coverage_helper_tests")
    run.add_argument("--plugin", action="append", default=[])
    run.add_argument("--plugin-dir", action="append", default=[])
    run.add_argument("--reporter", action="append", default=[])
    run.add_argument("--dashboard-export", default=None, dest="dashboard_export")
    run.add_argument("--dashboard-upload-url", default=None, dest="dashboard_upload_url")
    run.add_argument("--dashboard-version", default=None, dest="dashboard_version")
    run.add_argument("--dashboard-project", default=None, dest="dashboard_project")
    run.add_argument("--dashboard-branch", default=None, dest="dashboard_branch")
    run.add_argument("--dashboard-commit", default=None, dest="dashboard_commit")
    run.add_argument("--dashboard-build-url", default=None, dest="dashboard_build_url")
    run.add_argument(
        "--dashboard-retention-days",
        type=int,
        default=None,
        dest="dashboard_retention_days",
    )
    run.add_argument(
        "--dashboard-auth-token-env",
        default=None,
        dest="dashboard_auth_token_env",
    )
    run.add_argument("--dashboard-auth-header", default=None, dest="dashboard_auth_header")
    run.add_argument("--dashboard-upload-retries", type=int, default=None, dest="dashboard_upload_retries")
    run.add_argument("--dashboard-upload-retry-delay-ms", type=int, default=None, dest="dashboard_upload_retry_delay_ms")
    run.add_argument("--distribution-manifest", default=None, dest="distribution_manifest")
    run.add_argument("--incremental", action="store_true")
    run.add_argument("--baseline-file", default=None, dest="baseline_file")
    run.add_argument("--baseline-max-age-days", type=int, default=None, dest="baseline_max_age_days")
    run.add_argument("--baseline-branch", default=None, dest="baseline_branch")
    run.add_argument("--write-baseline", default=None, dest="write_baseline")
    run.add_argument("--clear-baseline", action="store_true", dest="clear_baseline")
    run.add_argument("--artifact-dir", default=None)
    run.add_argument("--retain-worktrees", action="store_true", dest="retain_worktrees")
    run.add_argument("--retain-worktrees-for", default=None, dest="retain_worktrees_for")
    run.add_argument(
        "--retained-worktree-ttl-hours",
        type=float,
        default=None,
        dest="retained_worktree_ttl_hours",
    )
    run.add_argument("--worker-tmp-dir", default=None, dest="worker_tmp_dir")
    run.add_argument("--worker-label", default=None, dest="worker_label")
    run.add_argument("--env", action="append", default=[], dest="env")
    run.add_argument("--env-inherit", action="append", default=[], dest="env_inherit")
    run.add_argument("--env-block", action="append", default=[], dest="env_block")
    run.add_argument("--resume", default=None)
    run.add_argument("--quiet", action="store_true")
    run.add_argument("--shard-index", type=int, default=None)
    run.add_argument("--shard-total", type=int, default=None)

    list_mutants = subparsers.add_parser("list-mutants", help="list mutants without running build/tests")
    list_mutants.add_argument("--config", default=None, help="Optional YAML/JSON config file")
    list_mutants.add_argument("--repo", required=True)
    list_mutants.add_argument("--files", required=False)
    list_mutants.add_argument("--base", default=None)
    list_mutants.add_argument("--since", default=None, help="Stryker.NET-style alias for --base")
    list_mutants.add_argument("--lines", default=None)
    list_mutants.add_argument("--max-mutants", type=int, default=None)
    list_mutants.add_argument("--include-metal", action="store_true")
    list_mutants.add_argument("--include", default=None)
    list_mutants.add_argument("--exclude", default=None)
    list_mutants.add_argument("--mutators", default=None)
    list_mutants.add_argument("--mutation-level", choices=sorted(engine.MUTATION_LEVEL_NAMES), default=None, dest="mutation_level")
    list_mutants.add_argument("--mode", choices=["token", "clang", "clang-ast"], default=None)
    list_mutants.add_argument("--equivalent-suppression", choices=["off", "conservative", "aggressive"], default=None, dest="equivalent_suppression")
    list_mutants.add_argument("--plugin", action="append", default=[])
    list_mutants.add_argument("--plugin-dir", action="append", default=[])
    list_mutants.add_argument("--format", choices=["json"], default="json")

    run_mutant = subparsers.add_parser("run-mutant", help="run a single mutant by stable ID")
    run_mutant.add_argument("--config", default=None, help="Optional YAML/JSON config file")
    run_mutant.add_argument("--repo", required=True)
    run_mutant.add_argument("--id", required=True)
    run_mutant.add_argument("--build-command", required=False, dest="build_command")
    run_mutant.add_argument("--check-command", required=False, dest="check_command")
    run_mutant.add_argument("--test-command", required=False, dest="test_command")
    run_mutant.add_argument("--build-system", choices=["cmake", "ctest", "ninja", "make", "meson", "bazel", "xcodebuild"], default=None)
    run_mutant.add_argument("--build-dir", default=None)
    run_mutant.add_argument("--build-target", default=None)
    run_mutant.add_argument("--artifact-path", default=None, dest="artifact_path")
    run_mutant.add_argument("--xcode-workspace", default=None, dest="xcode_workspace")
    run_mutant.add_argument("--xcode-project", default=None, dest="xcode_project")
    run_mutant.add_argument("--xcode-scheme", default=None, dest="xcode_scheme")
    run_mutant.add_argument("--xcode-configuration", default=None, dest="xcode_configuration")
    run_mutant.add_argument("--xcode-sdk", default=None, dest="xcode_sdk")
    run_mutant.add_argument("--xcode-destination", default=None, dest="xcode_destination")
    run_mutant.add_argument("--check-system", choices=["clang", "clang++", "clang-tidy", "cppcheck"], default=None, dest="check_system")
    run_mutant.add_argument("--check-args", default=None, dest="check_args")
    run_mutant.add_argument("--test-target", default=None)
    run_mutant.add_argument("--test-filter", default=None)
    run_mutant.add_argument("--test-framework", choices=["gtest", "googletest", "catch2", "doctest", "xctest"], default=None)
    run_mutant.add_argument("--test-binary", default=None)
    run_mutant.add_argument("--xctest-bundle", default=None)
    run_mutant.add_argument("--xctest-destination", default=None, dest="xctest_destination")
    run_mutant.add_argument(
        "--xctest-only-testing",
        action="append",
        default=[],
        dest="xctest_only_testing",
    )
    run_mutant.add_argument(
        "--xctest-skip-testing",
        action="append",
        default=[],
        dest="xctest_skip_testing",
    )
    run_mutant.add_argument("--report", required=True)
    run_mutant.add_argument("--base", default=None)
    run_mutant.add_argument("--since", default=None, help="Stryker.NET-style alias for --base")
    run_mutant.add_argument("--lines", default=None)
    run_mutant.add_argument("--output-format", choices=["legacy", "stryker-cxx"], default="stryker-cxx")
    run_mutant.add_argument("--format", choices=["json", "markdown", "html", "sarif", "github-annotations", "mutation-testing-elements"], default="json")
    run_mutant.add_argument("--timeout", type=int, default=None, dest="timeout")
    run_mutant.add_argument("--timeout-factor", type=float, default=None, dest="timeout_factor")
    run_mutant.add_argument("--timeout-constant-ms", type=int, default=None, dest="timeout_constant_ms")
    run_mutant.add_argument("--skip-initial-test", action="store_true", dest="skip_initial_test")
    run_mutant.add_argument("--dry-run-only", action="store_true", dest="dry_run_only")
    run_mutant.add_argument("--skip-tests", action="store_true", dest="skip_tests")
    run_mutant.add_argument("--coverage-file", default=None, dest="coverage_file")
    run_mutant.add_argument("--coverage-analysis", default=None, choices=["off", "all", "perTest", "perTestInIsolation"], dest="coverage_analysis")
    run_mutant.add_argument("--coverage-provider", default=None, dest="coverage_provider")
    run_mutant.add_argument("--coverage-test-command-template", default=None, dest="coverage_test_command_template")
    run_mutant.add_argument("--coverage-helper-command-template", default=None, dest="coverage_helper_command_template")
    run_mutant.add_argument("--coverage-helper-tests", action="append", default=[], dest="coverage_helper_tests")
    run_mutant.add_argument("--plugin", action="append", default=[])
    run_mutant.add_argument("--plugin-dir", action="append", default=[])
    run_mutant.add_argument("--reporter", action="append", default=[])
    run_mutant.add_argument("--dashboard-export", default=None, dest="dashboard_export")
    run_mutant.add_argument("--dashboard-upload-url", default=None, dest="dashboard_upload_url")
    run_mutant.add_argument("--dashboard-version", default=None, dest="dashboard_version")
    run_mutant.add_argument("--dashboard-project", default=None, dest="dashboard_project")
    run_mutant.add_argument("--dashboard-branch", default=None, dest="dashboard_branch")
    run_mutant.add_argument("--dashboard-commit", default=None, dest="dashboard_commit")
    run_mutant.add_argument("--dashboard-build-url", default=None, dest="dashboard_build_url")
    run_mutant.add_argument(
        "--dashboard-retention-days",
        type=int,
        default=None,
        dest="dashboard_retention_days",
    )
    run_mutant.add_argument(
        "--dashboard-auth-token-env",
        default=None,
        dest="dashboard_auth_token_env",
    )
    run_mutant.add_argument("--dashboard-auth-header", default=None, dest="dashboard_auth_header")
    run_mutant.add_argument("--dashboard-upload-retries", type=int, default=None, dest="dashboard_upload_retries")
    run_mutant.add_argument("--dashboard-upload-retry-delay-ms", type=int, default=None, dest="dashboard_upload_retry_delay_ms")
    run_mutant.add_argument("--distribution-manifest", default=None, dest="distribution_manifest")
    run_mutant.add_argument("--incremental", action="store_true")
    run_mutant.add_argument("--baseline-file", default=None, dest="baseline_file")
    run_mutant.add_argument("--baseline-max-age-days", type=int, default=None, dest="baseline_max_age_days")
    run_mutant.add_argument("--baseline-branch", default=None, dest="baseline_branch")
    run_mutant.add_argument("--write-baseline", default=None, dest="write_baseline")
    run_mutant.add_argument("--clear-baseline", action="store_true", dest="clear_baseline")
    run_mutant.add_argument("--artifact-dir", default=None)
    run_mutant.add_argument("--retain-worktrees", action="store_true", dest="retain_worktrees")
    run_mutant.add_argument("--retain-worktrees-for", default=None, dest="retain_worktrees_for")
    run_mutant.add_argument(
        "--retained-worktree-ttl-hours",
        type=float,
        default=None,
        dest="retained_worktree_ttl_hours",
    )
    run_mutant.add_argument("--worker-tmp-dir", default=None, dest="worker_tmp_dir")
    run_mutant.add_argument("--worker-label", default=None, dest="worker_label")
    run_mutant.add_argument("--env", action="append", default=[], dest="env")
    run_mutant.add_argument("--env-inherit", action="append", default=[], dest="env_inherit")
    run_mutant.add_argument("--env-block", action="append", default=[], dest="env_block")
    run_mutant.add_argument("--mutators", default=None)
    run_mutant.add_argument("--mutation-level", choices=sorted(engine.MUTATION_LEVEL_NAMES), default=None, dest="mutation_level")
    run_mutant.add_argument("--fail-on-empty", action="store_true", dest="fail_on_empty")
    run_mutant.add_argument("--threshold", type=float, default=None)
    run_mutant.add_argument("--threshold-high", type=float, default=None, dest="threshold_high")
    run_mutant.add_argument("--threshold-low", type=float, default=None, dest="threshold_low")
    run_mutant.add_argument("--threshold-break", type=float, default=None, dest="threshold_break")
    run_mutant.add_argument("--mode", choices=["token", "clang", "clang-ast"], default=None)
    run_mutant.add_argument("--execution-mode", choices=sorted(EXECUTION_MODES), default=None, dest="execution_mode")
    run_mutant.add_argument("--execution-backend", choices=sorted(EXECUTION_BACKENDS), default=None, dest="execution_backend")
    run_mutant.add_argument("--equivalent-suppression", choices=["off", "conservative", "aggressive"], default=None, dest="equivalent_suppression")
    run_mutant.add_argument("--artifact-backend", choices=["source-overlay", "compiled-executable", "compiled-library", "compiled-object"], default=None, dest="artifact_backend")
    run_mutant.add_argument("--artifact-fallback", choices=["none", "source-overlay"], default=None, dest="artifact_fallback")
    run_mutant.add_argument("--worktree-mode", dest="worktree_mode", choices=["inplace", "git-worktree", "copy"], default=None)
    run_mutant.add_argument("--allow-dirty", action="store_true")
    run_mutant.add_argument("--quiet", action="store_true")
    run_mutant.add_argument("--shard-index", type=int, default=None)
    run_mutant.add_argument("--shard-total", type=int, default=None)

    return parser


def _init_config(args: argparse.Namespace) -> int:
    path = args.path
    if os.path.exists(path) and not args.force:
        raise ValueError(f"config already exists: {path} (use --force to overwrite)")
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        f.write(_config_for_preset(args.preset))
    print(path)
    return 0


def _read_baseline_payload(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {"schemaVersion": "stryker-cxx.baseline.v1", "tool": "stryker-cxx", "entries": {}}
    with open(path) as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"baseline must be an object: {path}")
    payload.setdefault("schemaVersion", "stryker-cxx.baseline.v1")
    payload.setdefault("tool", "stryker-cxx")
    payload.setdefault("entries", {})
    if not isinstance(payload["entries"], dict):
        raise ValueError(f"baseline entries must be an object: {path}")
    return payload


def _write_baseline_payload(path: str, payload: dict[str, Any]) -> None:
    payload["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)


def _baseline_merge(args: argparse.Namespace) -> int:
    merged: dict[str, Any] = {
        "schemaVersion": "stryker-cxx.baseline.v1",
        "tool": "stryker-cxx",
        "entries": {},
    }
    for path in args.inputs:
        payload = _read_baseline_payload(path)
        merged["entries"].update(payload.get("entries", {}))
    _write_baseline_payload(args.output, merged)
    print(json.dumps({"output": args.output, "entries": len(merged["entries"])}, indent=2))
    return 0


def _baseline_prune(args: argparse.Namespace) -> int:
    if args.max_entries is not None and args.max_entries < 1:
        raise ValueError("--max-entries must be >= 1")
    payload = _read_baseline_payload(args.baseline_file)
    entries = payload.get("entries", {})
    original_count = len(entries)
    if args.repo:
        repo = os.path.abspath(args.repo)
        entries = {
            key: value
            for key, value in entries.items()
            if _baseline_entry_file_exists(repo, value)
        }
    if args.max_entries is not None and len(entries) > args.max_entries:
        ordered = sorted(
            entries.items(),
            key=lambda item: str(item[1].get("updatedAt", "")) if isinstance(item[1], dict) else "",
            reverse=True,
        )
        entries = dict(ordered[: args.max_entries])
    payload["entries"] = entries
    _write_baseline_payload(args.baseline_file, payload)
    print(json.dumps({"baselineFile": args.baseline_file, "before": original_count, "after": len(entries)}, indent=2))
    return 0


def _baseline_info(args: argparse.Namespace) -> int:
    payload = _read_baseline_payload(args.baseline_file)
    entries = payload.get("entries", {})
    repo = os.path.abspath(args.repo) if args.repo else None
    by_status: dict[str, int] = {}
    by_branch: dict[str, int] = {}
    updated_at: list[str] = []
    file_existence = {"present": 0, "missing": 0} if repo else None

    for value in entries.values():
        if not isinstance(value, dict):
            continue
        mutant = value.get("mutant") if isinstance(value.get("mutant"), dict) else {}
        status = str(mutant.get("status") or "UNKNOWN").upper()
        by_status[status] = by_status.get(status, 0) + 1
        branch = str(value.get("branch") or mutant.get("baselineBranch") or "none")
        by_branch[branch] = by_branch.get(branch, 0) + 1
        updated = value.get("updatedAt")
        if isinstance(updated, str) and updated:
            updated_at.append(updated)
        if file_existence is not None:
            key = "present" if _baseline_entry_file_exists(repo, value) else "missing"
            file_existence[key] += 1

    out: dict[str, Any] = {
        "baselineFile": args.baseline_file,
        "entries": len(entries),
        "byStatus": dict(sorted(by_status.items())),
        "byBranch": dict(sorted(by_branch.items())),
        "oldestUpdatedAt": min(updated_at) if updated_at else None,
        "newestUpdatedAt": max(updated_at) if updated_at else None,
    }
    if file_existence is not None:
        out["fileExistence"] = file_existence
    print(json.dumps(out, indent=2))
    return 0


def _baseline_entry_summary(key: str, value: Any, repo: str | None = None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    mutant = value.get("mutant") if isinstance(value.get("mutant"), dict) else {}
    updated = value.get("updatedAt")
    branch = str(value.get("branch") or mutant.get("baselineBranch") or "none")
    status = str(mutant.get("status") or "UNKNOWN").upper()
    out: dict[str, Any] = {
        "key": key,
        "updatedAt": updated if isinstance(updated, str) else None,
        "status": status,
        "branch": branch,
        "file": mutant.get("file"),
        "line": mutant.get("line"),
        "mutator": mutant.get("mutator"),
        "id": mutant.get("id"),
    }
    if repo is not None:
        out["fileExists"] = _baseline_entry_file_exists(repo, value)
    return out


def _baseline_history(args: argparse.Namespace) -> int:
    if args.limit < 1:
        raise ValueError("--limit must be >= 1")
    status_filter = args.status.upper() if args.status else None
    payload = _read_baseline_payload(args.baseline_file)
    entries = payload.get("entries", {})
    repo = os.path.abspath(args.repo) if args.repo else None
    rows: list[dict[str, Any]] = []
    by_day: dict[str, dict[str, int]] = {}

    for key, value in entries.items():
        row = _baseline_entry_summary(str(key), value, repo)
        if row is None:
            continue
        if status_filter and row["status"] != status_filter:
            continue
        if args.branch and row["branch"] != args.branch:
            continue
        rows.append(row)
        day = str(row.get("updatedAt") or "unknown")[:10]
        bucket = by_day.setdefault(day, {"entries": 0})
        bucket["entries"] += 1
        status = str(row["status"])
        bucket[status] = bucket.get(status, 0) + 1

    rows.sort(key=lambda row: str(row.get("updatedAt") or ""), reverse=True)
    out = {
        "baselineFile": args.baseline_file,
        "entries": len(entries),
        "matchedEntries": len(rows),
        "limit": args.limit,
        "byDay": dict(sorted(by_day.items())),
        "history": rows[: args.limit],
    }
    print(json.dumps(out, indent=2))
    return 0


def _parity_audit(args: argparse.Namespace) -> int:
    with open(args.report, encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError("--report must contain a JSON object")
    parity = payload.get("parity")
    if not isinstance(parity, dict):
        execution = payload.get("execution") if isinstance(payload.get("execution"), dict) else {}
        parity = execution.get("parity") if isinstance(execution.get("parity"), dict) else None
    if not isinstance(parity, dict):
        raise ValueError("report does not contain stryker-cxx parity metadata")
    failures = _parity_profile_failures(parity, args.profile)
    if args.format == "json":
        print(json.dumps(parity, indent=2, sort_keys=True))
        if failures:
            print(
                f"parity audit failed profile={args.profile}: {', '.join(failures)}",
                file=sys.stderr,
            )
            return 2
        return 0
    print("# stryker-cxx parity audit")
    print()
    print(f"Schema: `{parity.get('schemaVersion', 'unknown')}`")
    print(f"Profile: `{args.profile}`")
    print()
    for item in parity.get("items", []):
        if not isinstance(item, dict):
            continue
        print(f"- `{item.get('status', 'unknown')}` {item.get('id', 'unknown')}: {item.get('title', '')}")
        remaining = item.get("remaining")
        if isinstance(remaining, list) and remaining:
            print(f"  remaining: {'; '.join(str(value) for value in remaining)}")
    if failures:
        print()
        print(f"Failed profile `{args.profile}`: {', '.join(failures)}")
        return 2
    return 0


def _parity_profile_failures(parity: dict[str, Any], profile: str) -> list[str]:
    if profile == "summary":
        return []
    allowed = {"covered", "partial", "external"} if profile == "review" else {"covered", "external"}
    failures: list[str] = []
    items = parity.get("items")
    if not isinstance(items, list) or not items:
        return ["missing-parity-items"]
    for item in items:
        if not isinstance(item, dict):
            failures.append("invalid-parity-item")
            continue
        status = str(item.get("status") or "unknown")
        if status not in allowed:
            failures.append(f"{item.get('id', 'unknown')}={status}")
    return failures


def _baseline_entry_file_exists(repo: str, value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    mutant = value.get("mutant")
    if not isinstance(mutant, dict):
        return False
    file_name = mutant.get("file")
    if not isinstance(file_name, str) or not file_name:
        return False
    return os.path.exists(os.path.join(repo, file_name))


def _resolve_defaults(args: argparse.Namespace) -> dict[str, Any]:
    config_path = _pick_config_path(args.config, getattr(args, "repo", None))
    cfg = _load_config(config_path, validate=False) if config_path else {}
    cfg = _apply_config_loader_plugins(
        cfg,
        _coerce_list(getattr(args, "plugin", None)),
        _coerce_list(getattr(args, "plugin_dir", None)),
        getattr(args, "repo", None),
    )
    _validate_config_shape(cfg, config_path or "config")

    execution = cfg.get("execution", {}) if isinstance(cfg.get("execution"), dict) else {}
    report_cfg = cfg.get("report", {}) if isinstance(cfg.get("report"), dict) else {}
    mutation_artifact_cfg = cfg.get("mutationArtifact", {}) if isinstance(cfg.get("mutationArtifact"), dict) else {}
    thresholds_cfg = cfg.get("thresholds", {}) if isinstance(cfg.get("thresholds"), dict) else {}
    report_thresholds_cfg = report_cfg.get("thresholds", {}) if isinstance(report_cfg.get("thresholds"), dict) else {}
    files_cfg = cfg.get("files") if isinstance(cfg.get("files"), dict) else {}
    cfg_mutators = _coerce_mutator_list(cfg.get("mutators"))
    exec_mutators = _coerce_mutator_list(execution.get("mutators"))
    build_system = getattr(args, "build_system", None) or execution.get("buildSystem")
    build_dir = getattr(args, "build_dir", None) or execution.get("buildDir")
    build_target = getattr(args, "build_target", None) or execution.get("buildTarget")
    artifact_path = getattr(args, "artifact_path", None) or execution.get("artifactPath")
    xcode_workspace = getattr(args, "xcode_workspace", None) or execution.get("xcodeWorkspace")
    xcode_project = getattr(args, "xcode_project", None) or execution.get("xcodeProject")
    xcode_scheme = getattr(args, "xcode_scheme", None) or execution.get("xcodeScheme")
    xcode_configuration = getattr(args, "xcode_configuration", None) or execution.get("xcodeConfiguration")
    xcode_sdk = getattr(args, "xcode_sdk", None) or execution.get("xcodeSdk")
    xcode_destination = getattr(args, "xcode_destination", None) or execution.get("xcodeDestination")
    check_system = getattr(args, "check_system", None) or execution.get("checkSystem")
    check_args = getattr(args, "check_args", None) or execution.get("checkArgs")
    test_target = getattr(args, "test_target", None) or execution.get("testTarget")
    test_filter = getattr(args, "test_filter", None) or execution.get("testFilter")
    test_framework = getattr(args, "test_framework", None) or execution.get("testFramework")
    test_binary = getattr(args, "test_binary", None) or execution.get("testBinary")
    xctest_bundle = getattr(args, "xctest_bundle", None) or execution.get("xctestBundle")
    xctest_destination = (
        getattr(args, "xctest_destination", None)
        or execution.get("xctestDestination")
    )
    xctest_only_testing = _coerce_list(
        getattr(args, "xctest_only_testing", None)
    ) or _coerce_list(execution.get("xctestOnlyTesting"))
    xctest_skip_testing = _coerce_list(
        getattr(args, "xctest_skip_testing", None)
    ) or _coerce_list(execution.get("xctestSkipTesting"))
    if test_framework and not test_binary:
        test_binary = _discover_test_binary(getattr(args, "repo", None), build_dir, test_framework)
    files_value = getattr(args, "files", None) or (cfg.get("files") if isinstance(cfg.get("files"), str) else None)
    if not files_value and getattr(args, "id", None):
        files_value = str(getattr(args, "id")).split(":", 1)[0]
    checker = _checker_command(check_system, check_args, files_value)
    adapter = _adapter_commands(
        build_system,
        build_dir,
        build_target,
        test_target,
        test_filter,
        test_framework,
        test_binary,
        xctest_bundle,
        getattr(args, "repo", None),
        xctest_destination,
        xctest_only_testing,
        xctest_skip_testing,
        xcode_workspace,
        xcode_project,
        xcode_scheme,
        xcode_configuration,
        xcode_sdk,
        xcode_destination,
    )

    defaults = {
        "repo": getattr(args, "repo", None),
        "files": files_value,
        "base": (
            getattr(args, "base", None)
            or getattr(args, "since", None)
            or cfg.get("base")
            or cfg.get("since")
        ),
        "build_command": getattr(args, "build_command", None) or execution.get("buildCommand") or adapter.get("build"),
        "check_command": getattr(args, "check_command", None) or execution.get("checkCommand") or checker,
        "test_command": getattr(args, "test_command", None) or execution.get("testCommand") or adapter.get("test"),
        "build_system": build_system,
        "build_dir": build_dir,
        "build_target": build_target,
        "xcode_workspace": xcode_workspace,
        "xcode_project": xcode_project,
        "xcode_scheme": xcode_scheme,
        "xcode_configuration": xcode_configuration,
        "xcode_sdk": xcode_sdk,
        "xcode_destination": xcode_destination,
        "check_system": check_system,
        "check_args": check_args,
        "test_target": test_target,
        "test_filter": test_filter,
        "test_framework": test_framework,
        "test_binary": test_binary,
        "xctest_bundle": xctest_bundle,
        "xctest_destination": xctest_destination,
        "xctest_only_testing": xctest_only_testing,
        "xctest_skip_testing": xctest_skip_testing,
        "max_mutants": getattr(args, "max_mutants", None) if getattr(args, "max_mutants", None) is not None else execution.get("maxMutants"),
        "include_metal": bool(
            getattr(args, "include_metal", False)
            or execution.get("includeMetal", False)
        ),
        "mutators": getattr(args, "mutators", None) or execution.get("mutators") or execution.get("mutationMutators") or cfg.get("mutators"),
        "mutation_level": (
            getattr(args, "mutation_level", None)
            or execution.get("mutationLevel")
            or cfg.get("mutationLevel")
            or "Standard"
        ),
        "threshold": getattr(args, "threshold", None) if getattr(args, "threshold", None) is not None else execution.get("threshold"),
        "threshold_high": (
            getattr(args, "threshold_high", None)
            if getattr(args, "threshold_high", None) is not None
            else thresholds_cfg.get("high", report_thresholds_cfg.get("high", execution.get("thresholdHigh")))
        ),
        "threshold_low": (
            getattr(args, "threshold_low", None)
            if getattr(args, "threshold_low", None) is not None
            else thresholds_cfg.get("low", report_thresholds_cfg.get("low", execution.get("thresholdLow")))
        ),
        "threshold_break": (
            getattr(args, "threshold_break", None)
            if getattr(args, "threshold_break", None) is not None
            else thresholds_cfg.get("break", report_thresholds_cfg.get("break", execution.get("thresholdBreak")))
        ),
        "timeout": getattr(args, "timeout", None) if getattr(args, "timeout", None) is not None else execution.get("timeoutSeconds"),
        "timeout_factor": (
            getattr(args, "timeout_factor", None)
            if getattr(args, "timeout_factor", None) is not None
            else execution.get("timeoutFactor", 1.5)
        ),
        "timeout_constant_ms": (
            getattr(args, "timeout_constant_ms", None)
            if getattr(args, "timeout_constant_ms", None) is not None
            else execution.get("timeoutConstantMs", 5000)
        ),
        "skip_initial_test": bool(
            getattr(args, "skip_initial_test", False)
            or execution.get("skipInitialTest", False)
            or execution.get("initialTest") is False
        ),
        "dry_run_only": bool(getattr(args, "dry_run_only", False) or execution.get("dryRunOnly", False)),
        "skip_tests": bool(getattr(args, "skip_tests", False) or execution.get("skipTests", False)),
        "coverage_file": getattr(args, "coverage_file", None) or execution.get("coverageFile") or cfg.get("coverageFile"),
        "coverage_analysis": (
            getattr(args, "coverage_analysis", None)
            or execution.get("coverageAnalysis")
            or cfg.get("coverageAnalysis")
            or "perTest"
        ),
        "coverage_provider": getattr(args, "coverage_provider", None) or execution.get("coverageProvider") or cfg.get("coverageProvider"),
        "coverage_test_command_template": getattr(args, "coverage_test_command_template", None) or execution.get("coverageTestCommandTemplate"),
        "coverage_helper_command_template": (
            getattr(args, "coverage_helper_command_template", None)
            or execution.get("coverageHelperCommandTemplate")
            or cfg.get("coverageHelperCommandTemplate")
        ),
        "coverage_helper_tests": (
            _coerce_list(getattr(args, "coverage_helper_tests", None))
            or _coerce_list(execution.get("coverageHelperTests"))
            or _coerce_list(cfg.get("coverageHelperTests"))
        ),
        "plugins": _coerce_list(getattr(args, "plugin", None)) or _coerce_list(execution.get("plugins")) or _coerce_list(cfg.get("plugins")),
        "plugin_dirs": _coerce_list(getattr(args, "plugin_dir", None)) or _coerce_list(execution.get("pluginDirs")) or _coerce_list(cfg.get("pluginDirs")),
        "reporters": _coerce_list(getattr(args, "reporter", None)) or _coerce_list(execution.get("reporters")),
        "dashboard_export": getattr(args, "dashboard_export", None) or execution.get("dashboardExport"),
        "dashboard_upload_url": getattr(args, "dashboard_upload_url", None) or execution.get("dashboardUploadUrl"),
        "dashboard_version": (
            getattr(args, "dashboard_version", None)
            or execution.get("dashboardVersion", "1")
        ),
        "dashboard_retention_days": (
            getattr(args, "dashboard_retention_days", None)
            if getattr(args, "dashboard_retention_days", None) is not None
            else execution.get("dashboardRetentionDays")
        ),
        "dashboard_project": getattr(args, "dashboard_project", None) or execution.get("dashboardProject"),
        "dashboard_branch": getattr(args, "dashboard_branch", None) or execution.get("dashboardBranch"),
        "dashboard_commit": getattr(args, "dashboard_commit", None) or execution.get("dashboardCommit"),
        "dashboard_build_url": getattr(args, "dashboard_build_url", None) or execution.get("dashboardBuildUrl"),
        "dashboard_auth_token_env": (
            getattr(args, "dashboard_auth_token_env", None)
            or execution.get("dashboardAuthTokenEnv")
        ),
        "dashboard_auth_header": (
            getattr(args, "dashboard_auth_header", None)
            or execution.get("dashboardAuthHeader", "Authorization")
        ),
        "dashboard_upload_retries": (
            getattr(args, "dashboard_upload_retries", None)
            if getattr(args, "dashboard_upload_retries", None) is not None
            else execution.get("dashboardUploadRetries", 0)
        ),
        "dashboard_upload_retry_delay_ms": (
            getattr(args, "dashboard_upload_retry_delay_ms", None)
            if getattr(args, "dashboard_upload_retry_delay_ms", None) is not None
            else execution.get("dashboardUploadRetryDelayMs", 1000)
        ),
        "distribution_manifest": (
            getattr(args, "distribution_manifest", None)
            or execution.get("distributionManifest")
            or cfg.get("distributionManifest")
        ),
        "incremental": bool(getattr(args, "incremental", False) or execution.get("incremental", False)),
        "baseline_file": getattr(args, "baseline_file", None) or execution.get("baselineFile") or cfg.get("baselineFile"),
        "baseline_max_age_days": (
            getattr(args, "baseline_max_age_days", None)
            if getattr(args, "baseline_max_age_days", None) is not None
            else execution.get("baselineMaxAgeDays")
        ),
        "baseline_branch": getattr(args, "baseline_branch", None) or execution.get("baselineBranch"),
        "write_baseline": getattr(args, "write_baseline", None) or execution.get("writeBaseline") or cfg.get("writeBaseline"),
        "clear_baseline": bool(getattr(args, "clear_baseline", False) or execution.get("clearBaseline", False)),
        "artifact_dir": getattr(args, "artifact_dir", None) or execution.get("artifactDir"),
        "retain_worktrees": bool(getattr(args, "retain_worktrees", False) or execution.get("retainWorktrees", False)),
        "retain_worktrees_for": (
            _coerce_list(getattr(args, "retain_worktrees_for", None))
            or _coerce_list(execution.get("retainWorktreesFor"))
        ),
        "retained_worktree_ttl_hours": (
            getattr(args, "retained_worktree_ttl_hours", None)
            if getattr(args, "retained_worktree_ttl_hours", None) is not None
            else execution.get("retainedWorktreeTtlHours")
        ),
        "worker_tmp_dir": getattr(args, "worker_tmp_dir", None) or execution.get("workerTmpDir"),
        "worker_label": getattr(args, "worker_label", None) or execution.get("workerLabel"),
        "env": _coerce_env_args(getattr(args, "env", None)) or _coerce_env_args(execution.get("env")),
        "env_inherit": _coerce_list(getattr(args, "env_inherit", None))
        or _coerce_list(execution.get("envInherit")),
        "env_block": _coerce_list(getattr(args, "env_block", None))
        or _coerce_list(execution.get("envBlock")),
        "mode": getattr(args, "mode", None) if getattr(args, "mode", None) is not None else execution.get("mode", "token"),
        "execution_mode": (
            getattr(args, "execution_mode", None)
            if getattr(args, "execution_mode", None) is not None
            else execution.get("executionMode", "source-overlay")
        ),
        "execution_backend": (
            getattr(args, "execution_backend", None)
            if getattr(args, "execution_backend", None) is not None
            else execution.get("executionBackend", "auto")
        ),
        "equivalent_suppression": (
            getattr(args, "equivalent_suppression", None)
            or execution.get("equivalentSuppression", "conservative")
        ),
        "jobs": getattr(args, "jobs", None) if getattr(args, "jobs", None) is not None else execution.get("jobs", 1),
        "batch_mutants": bool(getattr(args, "batch_mutants", False) or execution.get("batchMutants", False)),
        "batch_size": (
            getattr(args, "batch_size", None)
            if getattr(args, "batch_size", None) is not None
            else execution.get("batchSize", 4)
        ),
        "artifact_backend": (
            getattr(args, "artifact_backend", None)
            or execution.get("artifactBackend")
            or mutation_artifact_cfg.get("backend")
            or "source-overlay"
        ),
        "artifact_fallback": (
            getattr(args, "artifact_fallback", None)
            or execution.get("artifactFallback")
            or mutation_artifact_cfg.get("fallback")
            or "none"
        ),
        "artifact_path": artifact_path,
        "worktree_mode": (
            getattr(args, "worktree_mode", None)
            if getattr(args, "worktree_mode", None) is not None
            else execution.get("worktreeMode", execution.get("workTreeMode", "inplace"))
        ),
        "allow_dirty": bool(getattr(args, "allow_dirty", False)),
        "resume": getattr(args, "resume", None),
        "fail_on_empty": getattr(args, "fail_on_empty", False),
        "quiet": getattr(args, "quiet", False),
        "format": getattr(args, "format", None) if getattr(args, "format", None) is not None else execution.get("format", "json"),
        "output_format": (
            getattr(args, "output_format", None)
            if getattr(args, "output_format", None) is not None
            else execution.get("outputFormat", "stryker-cxx")
        ),
        "report": getattr(args, "report", None),
        "shard_index": getattr(args, "shard_index", None),
        "shard_total": getattr(args, "shard_total", None),
        "files_include": _coerce_list(files_cfg.get("include")) if isinstance(files_cfg, dict) else [],
        "files_exclude": _coerce_list(files_cfg.get("exclude")) if isinstance(files_cfg, dict) else [],
        "report_threshold": report_cfg.get("threshold", None),
        "fail_on_empty_report": report_cfg.get("failOnEmpty", report_cfg.get("fail_on_empty")),
    }

    if cfg_mutators:
        defaults["mutators"] = ",".join(cfg_mutators)
    elif exec_mutators:
        defaults["mutators"] = ",".join(exec_mutators)

    if defaults["mutators"] is None:
        defaults["mutators"] = ",".join(engine.mutators_for_level(defaults["mutation_level"]))

    if defaults["threshold"] is None:
        defaults["threshold"] = defaults.get("report_threshold")
    if defaults["threshold_break"] is None:
        defaults["threshold_break"] = report_cfg.get("thresholdBreak", None)

    if defaults["jobs"] is not None and defaults["jobs"] < 1:
        raise ValueError("--jobs must be >= 1")
    if defaults["batch_size"] is not None and defaults["batch_size"] < 1:
        raise ValueError("--batch-size must be >= 1")
    if defaults["timeout"] is not None and defaults["timeout"] < 1:
        raise ValueError("--timeout must be >= 1")
    if defaults["timeout_factor"] is not None and defaults["timeout_factor"] < 0:
        raise ValueError("--timeout-factor must be >= 0")
    if defaults["timeout_constant_ms"] is not None and defaults["timeout_constant_ms"] < 0:
        raise ValueError("--timeout-constant-ms must be >= 0")
    if defaults["execution_mode"] not in EXECUTION_MODES:
        raise ValueError("--execution-mode must be one of: mutant-switch, source-overlay")
    if defaults["execution_backend"] not in EXECUTION_BACKENDS:
        raise ValueError(
            "--execution-backend must be one of: "
            + ", ".join(sorted(EXECUTION_BACKENDS))
        )
    if defaults["dry_run_only"] and defaults["skip_initial_test"]:
        raise ValueError("--dry-run-only cannot be combined with --skip-initial-test")

    if defaults["shard_total"] is not None and defaults["shard_total"] < 1:
        raise ValueError("--shard-total must be >= 1")
    if defaults["shard_index"] is not None and defaults["shard_total"] is None:
        raise ValueError("--shard-index requires --shard-total")
    if defaults["shard_total"] is not None and defaults["shard_index"] is None:
        raise ValueError("--shard-total requires --shard-index")
    if defaults["shard_index"] is not None and defaults["shard_total"] is not None and defaults["shard_index"] > defaults["shard_total"]:
        raise ValueError("--shard-index must be <= --shard-total")

    if defaults["fail_on_empty"] is False and defaults.get("fail_on_empty_report"):
        defaults["fail_on_empty"] = True

    if defaults["build_command"] is None or (defaults["test_command"] is None and not defaults["skip_tests"]):
        # allow parser validation to fail with concrete context in run/list context.
        pass

    requested_files = _coerce_list(args.files) if hasattr(args, "files") and args.files else []
    default_files = _coerce_list(cfg.get("files")) if not isinstance(files_cfg, dict) else []
    cfg_includes = _coerce_list(files_cfg.get("include")) if isinstance(files_cfg, dict) else []
    cfg_excludes = _coerce_list(files_cfg.get("exclude")) if isinstance(files_cfg, dict) else []
    cli_includes = _split_csv(args.include if hasattr(args, "include") else None)
    cli_excludes = _split_csv(args.exclude if hasattr(args, "exclude") else None)
    default_file_patterns = cfg_includes if cfg_includes else default_files

    defaults["files"] = _apply_file_filters(
        requested_files or default_file_patterns,
        cfg_includes + cli_includes,
        cfg_excludes + cli_excludes,
    )
    if requested_files:
        defaults["files"] = _apply_file_filters(
            requested_files,
            cfg_includes + cli_includes,
            cfg_excludes + cli_excludes,
        )
    defaults["files_include"] = cfg_includes + cli_includes
    defaults["files_exclude"] = cfg_excludes + cli_excludes
    effective = _effective_config_payload(defaults)
    redacted_effective = _redact_effective_config_payload(effective)
    defaults["config_path"] = config_path or None
    defaults["config_hash"] = _stable_hash({"source": cfg, "effective": effective})
    defaults["effective_config_json"] = json.dumps(redacted_effective, sort_keys=True, default=str)

    return defaults


def _run(args: argparse.Namespace) -> int:
    cfg = _resolve_defaults(args)

    files = cfg["files"]
    if not files:
        raise ValueError("run requires --files or stryker-cxx config files.include")

    if not cfg["build_command"] or (not cfg["test_command"] and not cfg["skip_tests"]):
        raise ValueError("run requires --build-command and --test-command unless --skip-tests is set")

    mutators = cfg["mutators"]
    legacy_args = [
        "--repo-dir",
        cfg["repo"],
        "--files",
        ",".join(files),
        "--build-cmd",
        cfg["build_command"],
        "--check-cmd",
        cfg["check_command"] or "",
        "--test-cmd",
        cfg["test_command"] or "",
        "--report",
        cfg["report"],
        "--config-path",
        cfg["config_path"] or "",
        "--config-hash",
        cfg["config_hash"],
        "--effective-config-json",
        cfg["effective_config_json"],
        "--output-format",
        cfg["output_format"],
        "--format",
        cfg["format"],
        "--mutators",
        mutators,
        "--mutation-level",
        cfg["mutation_level"],
    ]
    if cfg["base"]:
        legacy_args.extend(["--diff-base", cfg["base"]])
    if args.lines:
        legacy_args.extend(["--lines", args.lines])
    if cfg["max_mutants"]:
        legacy_args.extend(["--max-mutants", str(cfg["max_mutants"])])
    if cfg["include_metal"]:
        legacy_args.append("--include-metal")
    if cfg["threshold"] is not None:
        legacy_args.extend(["--threshold", str(cfg["threshold"])])
    if cfg["threshold_high"] is not None:
        legacy_args.extend(["--threshold-high", str(cfg["threshold_high"])])
    if cfg["threshold_low"] is not None:
        legacy_args.extend(["--threshold-low", str(cfg["threshold_low"])])
    if cfg["threshold_break"] is not None:
        legacy_args.extend(["--threshold-break", str(cfg["threshold_break"])])
    if cfg["timeout"] is not None:
        legacy_args.extend(["--timeout", str(cfg["timeout"])])
    if cfg["timeout_factor"] is not None:
        legacy_args.extend(["--timeout-factor", str(cfg["timeout_factor"])])
    if cfg["timeout_constant_ms"] is not None:
        legacy_args.extend(["--timeout-constant-ms", str(cfg["timeout_constant_ms"])])
    if cfg["mode"] is not None:
        legacy_args.extend(["--mode", str(cfg["mode"])])
    if cfg["execution_mode"] is not None:
        legacy_args.extend(["--execution-mode", str(cfg["execution_mode"])])
    if cfg["execution_backend"] is not None:
        legacy_args.extend(["--execution-backend", str(cfg["execution_backend"])])
    if cfg["equivalent_suppression"]:
        legacy_args.extend(["--equivalent-suppression", str(cfg["equivalent_suppression"])])
    if cfg["jobs"] is not None:
        legacy_args.extend(["--jobs", str(cfg["jobs"])])
    if cfg["batch_mutants"]:
        legacy_args.append("--batch-mutants")
    if cfg["batch_size"] is not None:
        legacy_args.extend(["--batch-size", str(cfg["batch_size"])])
    if cfg["artifact_backend"]:
        legacy_args.extend(["--artifact-backend", cfg["artifact_backend"]])
    if cfg["artifact_fallback"]:
        legacy_args.extend(["--artifact-fallback", cfg["artifact_fallback"]])
    if cfg["build_system"]:
        legacy_args.extend(["--build-system", cfg["build_system"]])
    if cfg["build_dir"]:
        legacy_args.extend(["--build-dir", cfg["build_dir"]])
    if cfg["build_target"]:
        legacy_args.extend(["--build-target", cfg["build_target"]])
    if cfg["artifact_path"]:
        legacy_args.extend(["--artifact-path", cfg["artifact_path"]])
    if cfg["xcode_workspace"]:
        legacy_args.extend(["--xcode-workspace", cfg["xcode_workspace"]])
    if cfg["xcode_project"]:
        legacy_args.extend(["--xcode-project", cfg["xcode_project"]])
    if cfg["xcode_scheme"]:
        legacy_args.extend(["--xcode-scheme", cfg["xcode_scheme"]])
    if cfg["xcode_configuration"]:
        legacy_args.extend(["--xcode-configuration", cfg["xcode_configuration"]])
    if cfg["xcode_sdk"]:
        legacy_args.extend(["--xcode-sdk", cfg["xcode_sdk"]])
    if cfg["xcode_destination"]:
        legacy_args.extend(["--xcode-destination", cfg["xcode_destination"]])
    if cfg["test_binary"]:
        legacy_args.extend(["--test-binary", cfg["test_binary"]])
    if cfg["shard_index"] is not None:
        legacy_args.extend(["--shard-index", str(cfg["shard_index"])])
    if cfg["shard_total"] is not None:
        legacy_args.extend(["--shard-total", str(cfg["shard_total"])])
    if cfg["worktree_mode"] is not None:
        legacy_args.extend(["--worktree-mode", cfg["worktree_mode"]])
    if cfg["allow_dirty"]:
        legacy_args.append("--allow-dirty")
    if cfg["artifact_dir"]:
        legacy_args.extend(["--artifact-dir", cfg["artifact_dir"]])
    if cfg["retain_worktrees"]:
        legacy_args.append("--retain-worktrees")
    if cfg["retain_worktrees_for"]:
        legacy_args.extend(["--retain-worktrees-for", ",".join(cfg["retain_worktrees_for"])])
    if cfg["retained_worktree_ttl_hours"] is not None:
        legacy_args.extend(["--retained-worktree-ttl-hours", str(cfg["retained_worktree_ttl_hours"])])
    if cfg["worker_tmp_dir"]:
        legacy_args.extend(["--worker-tmp-dir", cfg["worker_tmp_dir"]])
    if cfg["worker_label"]:
        legacy_args.extend(["--worker-label", cfg["worker_label"]])
    for item in cfg["env"]:
        legacy_args.extend(["--env", item])
    for item in cfg["env_inherit"]:
        legacy_args.extend(["--env-inherit", item])
    for item in cfg["env_block"]:
        legacy_args.extend(["--env-block", item])
    if cfg["resume"]:
        legacy_args.extend(["--resume", cfg["resume"]])
    if cfg["fail_on_empty"]:
        legacy_args.append("--fail-on-empty")
    if cfg["skip_initial_test"]:
        legacy_args.append("--skip-initial-test")
    if cfg["dry_run_only"]:
        legacy_args.append("--dry-run-only")
    if cfg["skip_tests"]:
        legacy_args.append("--skip-tests")
    if cfg["coverage_file"]:
        legacy_args.extend(["--coverage-file", cfg["coverage_file"]])
    if cfg["coverage_analysis"]:
        legacy_args.extend(["--coverage-analysis", cfg["coverage_analysis"]])
    if cfg["coverage_provider"]:
        legacy_args.extend(["--coverage-provider", cfg["coverage_provider"]])
    if cfg["coverage_test_command_template"]:
        legacy_args.extend(["--coverage-test-command-template", cfg["coverage_test_command_template"]])
    if cfg["coverage_helper_command_template"]:
        legacy_args.extend(["--coverage-helper-command-template", cfg["coverage_helper_command_template"]])
    for item in cfg["coverage_helper_tests"]:
        legacy_args.extend(["--coverage-helper-tests", item])
    for plugin in cfg["plugins"]:
        legacy_args.extend(["--plugin", plugin])
    for plugin_dir in cfg["plugin_dirs"]:
        legacy_args.extend(["--plugin-dir", plugin_dir])
    for reporter in cfg["reporters"]:
        legacy_args.extend(["--reporter", reporter])
    if cfg["dashboard_export"]:
        legacy_args.extend(["--dashboard-export", cfg["dashboard_export"]])
    if cfg["dashboard_upload_url"]:
        legacy_args.extend(["--dashboard-upload-url", cfg["dashboard_upload_url"]])
    if cfg["dashboard_version"]:
        legacy_args.extend(["--dashboard-version", cfg["dashboard_version"]])
    if cfg["dashboard_retention_days"] is not None:
        legacy_args.extend(["--dashboard-retention-days", str(cfg["dashboard_retention_days"])])
    if cfg["dashboard_project"]:
        legacy_args.extend(["--dashboard-project", cfg["dashboard_project"]])
    if cfg["dashboard_branch"]:
        legacy_args.extend(["--dashboard-branch", cfg["dashboard_branch"]])
    if cfg["dashboard_commit"]:
        legacy_args.extend(["--dashboard-commit", cfg["dashboard_commit"]])
    if cfg["dashboard_build_url"]:
        legacy_args.extend(["--dashboard-build-url", cfg["dashboard_build_url"]])
    if cfg["dashboard_auth_token_env"]:
        legacy_args.extend(["--dashboard-auth-token-env", cfg["dashboard_auth_token_env"]])
    if cfg["dashboard_auth_header"]:
        legacy_args.extend(["--dashboard-auth-header", cfg["dashboard_auth_header"]])
    if cfg["dashboard_upload_retries"] is not None:
        legacy_args.extend(["--dashboard-upload-retries", str(cfg["dashboard_upload_retries"])])
    if cfg["dashboard_upload_retry_delay_ms"] is not None:
        legacy_args.extend(["--dashboard-upload-retry-delay-ms", str(cfg["dashboard_upload_retry_delay_ms"])])
    if cfg["distribution_manifest"]:
        legacy_args.extend(["--distribution-manifest", cfg["distribution_manifest"]])
    if cfg["incremental"]:
        legacy_args.append("--incremental")
    if cfg["baseline_file"]:
        legacy_args.extend(["--baseline-file", cfg["baseline_file"]])
    if cfg["baseline_max_age_days"] is not None:
        legacy_args.extend(["--baseline-max-age-days", str(cfg["baseline_max_age_days"])])
    if cfg["baseline_branch"]:
        legacy_args.extend(["--baseline-branch", cfg["baseline_branch"]])
    if cfg["write_baseline"]:
        legacy_args.extend(["--write-baseline", cfg["write_baseline"]])
    if cfg["clear_baseline"]:
        legacy_args.append("--clear-baseline")
    if cfg["quiet"]:
        legacy_args.append("--quiet")

    return engine.main(legacy_args)


def _list_mutants(args: argparse.Namespace) -> int:
    cfg = _resolve_defaults(args)

    files = cfg["files"]
    if not files:
        raise ValueError("list-mutants requires --files or stryker-cxx config files.include")

    mutators = cfg["mutators"]
    repo = cfg["repo"]
    engine.load_plugins(cfg["plugins"], cfg["plugin_dirs"])

    enabled = [m.strip() for m in mutators.split(",") if m.strip()]
    bad = [m for m in enabled if m not in engine.MUTATORS]
    if bad:
        raise ValueError(f"unknown mutators: {bad}")

    repo_root = os.path.abspath(repo)
    files_list = [str(p).strip() for p in files if str(p).strip()]
    pending = []
    for path in files_list:
        if path.endswith(".metal") and not cfg["include_metal"]:
            continue
        only = engine.changed_lines(repo_root, cfg["base"], path) if cfg["base"] else None
        if args.lines:
            lf = engine.parse_lines(args.lines)
            only = lf if only is None else (only & lf)
        pending += engine._discover_mode(
            repo_root,
            path,
            only,
            [m for m in enabled if m],
            cfg["mode"],
            equivalent_suppression=cfg["equivalent_suppression"],
        )

    if cfg["max_mutants"]:
        pending = pending[: cfg["max_mutants"]]

    payload = [
        {
            "id": mut.id,
            "file": mut.file,
            "line": mut.line,
            "column": mut.col,
            "mutator": mut.mutator,
            "original": mut.original,
            "mutated": mut.mutated,
            "status": mut.status,
            "detail": mut.detail,
            "ignoreReason": mut.ignoreReason,
            "nodeKind": mut.nodeKind,
            "rewriteStrategy": mut.rewriteStrategy,
            "sourceRange": mut.sourceRange,
            "mode": cfg["mode"],
            "mutantSwitchGuardId": engine.mutant_switch_guard_id(mut),
        }
        for mut in pending
    ]
    print(json.dumps(payload, indent=2))
    return 0


def _parse_mutant_id(mut_id: str) -> str:
    parts = mut_id.split(":", 4)
    if len(parts) < 5:
        raise ValueError(f"invalid mutant id: {mut_id}")
    return parts[0]


def _run_mutant(args: argparse.Namespace) -> int:
    cfg = _resolve_defaults(args)

    if not cfg["build_command"] or (not cfg["test_command"] and not cfg["skip_tests"]):
        raise ValueError("run-mutant requires --build-command and --test-command unless --skip-tests is set")

    file_hint = _parse_mutant_id(args.id)
    legacy_args = [
        "--repo-dir",
        cfg["repo"],
        "--files",
        file_hint,
        "--build-cmd",
        cfg["build_command"],
        "--check-cmd",
        cfg["check_command"] or "",
        "--test-cmd",
        cfg["test_command"] or "",
        "--report",
        cfg["report"],
        "--config-path",
        cfg["config_path"] or "",
        "--config-hash",
        cfg["config_hash"],
        "--effective-config-json",
        cfg["effective_config_json"],
        "--output-format",
        cfg["output_format"],
        "--format",
        cfg["format"],
        "--run-mutant-id",
        args.id,
        "--mutators",
        cfg["mutators"],
        "--mutation-level",
        cfg["mutation_level"],
    ]
    if cfg["base"]:
        legacy_args.extend(["--diff-base", cfg["base"]])
    if args.lines:
        legacy_args.extend(["--lines", args.lines])
    if cfg["timeout"] is not None:
        legacy_args.extend(["--timeout", str(cfg["timeout"])])
    if cfg["timeout_factor"] is not None:
        legacy_args.extend(["--timeout-factor", str(cfg["timeout_factor"])])
    if cfg["timeout_constant_ms"] is not None:
        legacy_args.extend(["--timeout-constant-ms", str(cfg["timeout_constant_ms"])])
    if cfg["mode"] is not None:
        legacy_args.extend(["--mode", str(cfg["mode"])])
    if cfg["execution_mode"] is not None:
        legacy_args.extend(["--execution-mode", str(cfg["execution_mode"])])
    if cfg["execution_backend"] is not None:
        legacy_args.extend(["--execution-backend", str(cfg["execution_backend"])])
    if cfg["equivalent_suppression"]:
        legacy_args.extend(["--equivalent-suppression", str(cfg["equivalent_suppression"])])
    if cfg["jobs"] is not None:
        legacy_args.extend(["--jobs", str(cfg["jobs"])])
    if cfg["artifact_backend"]:
        legacy_args.extend(["--artifact-backend", cfg["artifact_backend"]])
    if cfg["artifact_fallback"]:
        legacy_args.extend(["--artifact-fallback", cfg["artifact_fallback"]])
    if cfg["build_system"]:
        legacy_args.extend(["--build-system", cfg["build_system"]])
    if cfg["build_dir"]:
        legacy_args.extend(["--build-dir", cfg["build_dir"]])
    if cfg["build_target"]:
        legacy_args.extend(["--build-target", cfg["build_target"]])
    if cfg["artifact_path"]:
        legacy_args.extend(["--artifact-path", cfg["artifact_path"]])
    if cfg["xcode_workspace"]:
        legacy_args.extend(["--xcode-workspace", cfg["xcode_workspace"]])
    if cfg["xcode_project"]:
        legacy_args.extend(["--xcode-project", cfg["xcode_project"]])
    if cfg["xcode_scheme"]:
        legacy_args.extend(["--xcode-scheme", cfg["xcode_scheme"]])
    if cfg["xcode_configuration"]:
        legacy_args.extend(["--xcode-configuration", cfg["xcode_configuration"]])
    if cfg["xcode_sdk"]:
        legacy_args.extend(["--xcode-sdk", cfg["xcode_sdk"]])
    if cfg["xcode_destination"]:
        legacy_args.extend(["--xcode-destination", cfg["xcode_destination"]])
    if cfg["test_binary"]:
        legacy_args.extend(["--test-binary", cfg["test_binary"]])
    if cfg["shard_index"] is not None:
        legacy_args.extend(["--shard-index", str(cfg["shard_index"])])
    if cfg["shard_total"] is not None:
        legacy_args.extend(["--shard-total", str(cfg["shard_total"])])
    if cfg["worktree_mode"] is not None:
        legacy_args.extend(["--worktree-mode", cfg["worktree_mode"]])
    if cfg["allow_dirty"]:
        legacy_args.append("--allow-dirty")
    if cfg["artifact_dir"]:
        legacy_args.extend(["--artifact-dir", cfg["artifact_dir"]])
    if cfg["retain_worktrees"]:
        legacy_args.append("--retain-worktrees")
    if cfg["retain_worktrees_for"]:
        legacy_args.extend(["--retain-worktrees-for", ",".join(cfg["retain_worktrees_for"])])
    if cfg["retained_worktree_ttl_hours"] is not None:
        legacy_args.extend(["--retained-worktree-ttl-hours", str(cfg["retained_worktree_ttl_hours"])])
    if cfg["worker_tmp_dir"]:
        legacy_args.extend(["--worker-tmp-dir", cfg["worker_tmp_dir"]])
    if cfg["worker_label"]:
        legacy_args.extend(["--worker-label", cfg["worker_label"]])
    for item in cfg["env"]:
        legacy_args.extend(["--env", item])
    for item in cfg["env_inherit"]:
        legacy_args.extend(["--env-inherit", item])
    for item in cfg["env_block"]:
        legacy_args.extend(["--env-block", item])
    if cfg["fail_on_empty"]:
        legacy_args.append("--fail-on-empty")
    if cfg["skip_initial_test"]:
        legacy_args.append("--skip-initial-test")
    if cfg["dry_run_only"]:
        legacy_args.append("--dry-run-only")
    if cfg["skip_tests"]:
        legacy_args.append("--skip-tests")
    if cfg["coverage_file"]:
        legacy_args.extend(["--coverage-file", cfg["coverage_file"]])
    if cfg["coverage_analysis"]:
        legacy_args.extend(["--coverage-analysis", cfg["coverage_analysis"]])
    if cfg["coverage_provider"]:
        legacy_args.extend(["--coverage-provider", cfg["coverage_provider"]])
    if cfg["coverage_test_command_template"]:
        legacy_args.extend(["--coverage-test-command-template", cfg["coverage_test_command_template"]])
    if cfg["coverage_helper_command_template"]:
        legacy_args.extend(["--coverage-helper-command-template", cfg["coverage_helper_command_template"]])
    for item in cfg["coverage_helper_tests"]:
        legacy_args.extend(["--coverage-helper-tests", item])
    for plugin in cfg["plugins"]:
        legacy_args.extend(["--plugin", plugin])
    for plugin_dir in cfg["plugin_dirs"]:
        legacy_args.extend(["--plugin-dir", plugin_dir])
    for reporter in cfg["reporters"]:
        legacy_args.extend(["--reporter", reporter])
    if cfg["dashboard_export"]:
        legacy_args.extend(["--dashboard-export", cfg["dashboard_export"]])
    if cfg["dashboard_upload_url"]:
        legacy_args.extend(["--dashboard-upload-url", cfg["dashboard_upload_url"]])
    if cfg["dashboard_version"]:
        legacy_args.extend(["--dashboard-version", cfg["dashboard_version"]])
    if cfg["dashboard_retention_days"] is not None:
        legacy_args.extend(["--dashboard-retention-days", str(cfg["dashboard_retention_days"])])
    if cfg["dashboard_project"]:
        legacy_args.extend(["--dashboard-project", cfg["dashboard_project"]])
    if cfg["dashboard_branch"]:
        legacy_args.extend(["--dashboard-branch", cfg["dashboard_branch"]])
    if cfg["dashboard_commit"]:
        legacy_args.extend(["--dashboard-commit", cfg["dashboard_commit"]])
    if cfg["dashboard_build_url"]:
        legacy_args.extend(["--dashboard-build-url", cfg["dashboard_build_url"]])
    if cfg["dashboard_auth_token_env"]:
        legacy_args.extend(["--dashboard-auth-token-env", cfg["dashboard_auth_token_env"]])
    if cfg["dashboard_auth_header"]:
        legacy_args.extend(["--dashboard-auth-header", cfg["dashboard_auth_header"]])
    if cfg["dashboard_upload_retries"] is not None:
        legacy_args.extend(["--dashboard-upload-retries", str(cfg["dashboard_upload_retries"])])
    if cfg["dashboard_upload_retry_delay_ms"] is not None:
        legacy_args.extend(["--dashboard-upload-retry-delay-ms", str(cfg["dashboard_upload_retry_delay_ms"])])
    if cfg["distribution_manifest"]:
        legacy_args.extend(["--distribution-manifest", cfg["distribution_manifest"]])
    if cfg["incremental"]:
        legacy_args.append("--incremental")
    if cfg["baseline_file"]:
        legacy_args.extend(["--baseline-file", cfg["baseline_file"]])
    if cfg["baseline_max_age_days"] is not None:
        legacy_args.extend(["--baseline-max-age-days", str(cfg["baseline_max_age_days"])])
    if cfg["baseline_branch"]:
        legacy_args.extend(["--baseline-branch", cfg["baseline_branch"]])
    if cfg["write_baseline"]:
        legacy_args.extend(["--write-baseline", cfg["write_baseline"]])
    if cfg["clear_baseline"]:
        legacy_args.append("--clear-baseline")
    if cfg["quiet"]:
        legacy_args.append("--quiet")
    if cfg["threshold"] is not None:
        legacy_args.extend(["--threshold", str(cfg["threshold"])])
    if cfg["threshold_high"] is not None:
        legacy_args.extend(["--threshold-high", str(cfg["threshold_high"])])
    if cfg["threshold_low"] is not None:
        legacy_args.extend(["--threshold-low", str(cfg["threshold_low"])])
    if cfg["threshold_break"] is not None:
        legacy_args.extend(["--threshold-break", str(cfg["threshold_break"])])

    return engine.main(legacy_args)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "run":
            return _run(args)
        if args.command == "list-mutants":
            return _list_mutants(args)
        if args.command == "run-mutant":
            return _run_mutant(args)
        if args.command == "init":
            return _init_config(args)
        if args.command == "baseline-merge":
            return _baseline_merge(args)
        if args.command == "baseline-prune":
            return _baseline_prune(args)
        if args.command == "baseline-info":
            return _baseline_info(args)
        if args.command == "baseline-history":
            return _baseline_history(args)
        if args.command == "parity-audit":
            return _parity_audit(args)
        parser.error(f"unsupported command: {args.command}")
    except Exception as exc:
        print(f"error: {exc}")
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
