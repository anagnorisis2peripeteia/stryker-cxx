"""Project analysis for stryker-cxx lifecycle reports."""

from __future__ import annotations

import json
import os
import re
from typing import Any


def analyze_project(
    repo: str,
    target_files: list[str],
    *,
    build_system: str | None = None,
    build_dir: str | None = None,
    build_target: str | None = None,
    test_target: str | None = None,
    test_framework: str | None = None,
    test_binary: str | None = None,
    build_command: str | None = None,
    check_command: str | None = None,
    test_command: str | None = None,
) -> dict[str, Any]:
    repo_root = os.path.abspath(repo)
    compile_database = _compile_database(repo_root)
    build_systems = _build_systems(
        repo_root,
        build_system=build_system,
        build_command=build_command,
        compile_database=compile_database,
    )
    source_targets = _source_targets(repo_root, target_files, compile_database)
    build_targets = _build_targets(
        repo_root,
        build_system=build_system,
        build_target=build_target,
        build_dir=build_dir,
    )
    test_targets = _test_targets(
        repo_root,
        test_target=test_target,
        test_framework=test_framework,
        test_binary=test_binary,
        test_command=test_command,
    )
    confidence = _confidence(build_systems, compile_database, build_targets, test_targets)
    return {
        "schemaVersion": "stryker-cxx.project-analysis.v1",
        "confidence": confidence,
        "repo": repo_root,
        "targetFiles": [_repo_relative(repo_root, path) for path in target_files],
        "buildSystems": build_systems,
        "compileDatabase": compile_database,
        "sourceTargets": source_targets,
        "buildTargets": build_targets,
        "testTargets": test_targets,
        "commands": {
            "build": build_command,
            "check": check_command,
            "test": test_command,
        },
    }


def _compile_database(repo: str) -> dict[str, Any]:
    path = os.path.join(repo, "compile_commands.json")
    if not os.path.exists(path):
        return {"present": False}
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "present": True,
            "path": "compile_commands.json",
            "status": "unreadable",
            "error": str(exc),
        }
    if not isinstance(entries, list):
        return {"present": True, "path": "compile_commands.json", "status": "invalid"}
    files = sorted(
        {
            _repo_relative(repo, str(entry.get("file")))
            for entry in entries
            if isinstance(entry, dict) and entry.get("file")
        }
    )
    directories = sorted(
        {
            _repo_relative(repo, str(entry.get("directory")))
            for entry in entries
            if isinstance(entry, dict) and entry.get("directory")
        }
    )
    return {
        "present": True,
        "path": "compile_commands.json",
        "status": "loaded",
        "entries": len(entries),
        "files": files,
        "directories": directories,
    }


def _build_systems(
    repo: str,
    *,
    build_system: str | None,
    build_command: str | None,
    compile_database: dict[str, Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if build_system:
        out.append({"name": build_system, "source": "explicit", "confidence": "high"})
    if build_command:
        out.append({"name": "custom-command", "source": "explicit", "confidence": "high"})
    if compile_database.get("present"):
        out.append({"name": "compile-database", "source": "compile_commands.json", "confidence": "high"})
    markers = [
        ("cmake", "CMakeLists.txt"),
        ("ninja", "build.ninja"),
        ("make", "Makefile"),
        ("meson", "meson.build"),
        ("bazel", "WORKSPACE.bazel"),
        ("bazel", "WORKSPACE"),
    ]
    for name, marker in markers:
        if os.path.exists(os.path.join(repo, marker)):
            out.append({"name": name, "source": marker, "confidence": "medium"})
    for entry in sorted(os.listdir(repo)):
        if entry.endswith(".xcodeproj") or entry.endswith(".xcworkspace"):
            out.append({"name": "xcodebuild", "source": entry, "confidence": "medium"})
    return _dedupe_named(out)


def _source_targets(repo: str, target_files: list[str], compile_database: dict[str, Any]) -> list[dict[str, Any]]:
    compile_db_files = set(compile_database.get("files") or [])
    out = []
    for file_name in target_files:
        relative = _repo_relative(repo, file_name)
        out.append(
            {
                "file": relative,
                "source": "cli-or-config",
                "compileDatabaseMatched": relative in compile_db_files,
                "confidence": "high" if relative in compile_db_files else "medium",
            }
        )
    return out


def _build_targets(
    repo: str,
    *,
    build_system: str | None,
    build_target: str | None,
    build_dir: str | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if build_target:
        out.append({"name": build_target, "kind": "build", "source": "explicit", "confidence": "high"})
    out.extend(_cmake_targets(repo))
    out.extend(_meson_targets(repo))
    out.extend(_bazel_targets(repo))
    out.extend(_ninja_targets(repo))
    out.extend(_make_targets(repo))
    if build_system and build_dir:
        out.append({"name": build_dir, "kind": "build-directory", "source": build_system, "confidence": "medium"})
    return _dedupe_named(out)


def _test_targets(
    repo: str,
    *,
    test_target: str | None,
    test_framework: str | None,
    test_binary: str | None,
    test_command: str | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if test_target:
        out.append({"name": test_target, "kind": "test", "source": "explicit", "confidence": "high"})
    if test_framework:
        out.append({"name": test_framework, "kind": "framework", "source": "explicit", "confidence": "high"})
    if test_binary:
        out.append({"name": _repo_relative(repo, test_binary), "kind": "test-binary", "source": "explicit", "confidence": "high"})
    if test_command:
        out.append({"name": test_command, "kind": "test-command", "source": "explicit", "confidence": "high"})
    out.extend(_cmake_tests(repo))
    out.extend(_meson_tests(repo))
    out.extend(_bazel_tests(repo))
    out.extend(_ninja_test_targets(repo))
    out.extend(_make_test_targets(repo))
    return _dedupe_named(out)


def _cmake_targets(repo: str) -> list[dict[str, Any]]:
    text = _read_optional(os.path.join(repo, "CMakeLists.txt"))
    if text is None:
        return []
    out = []
    for kind, name in re.findall(r"\badd_(executable|library)\s*\(\s*([A-Za-z0-9_.:+-]+)", text, flags=re.IGNORECASE):
        out.append({"name": name, "kind": kind.lower(), "source": "CMakeLists.txt", "confidence": "medium"})
    return out


def _cmake_tests(repo: str) -> list[dict[str, Any]]:
    text = _read_optional(os.path.join(repo, "CMakeLists.txt"))
    if text is None:
        return []
    out = []
    for name in re.findall(r"\badd_test\s*\(\s*NAME\s+([A-Za-z0-9_.:+-]+)", text, flags=re.IGNORECASE):
        out.append({"name": name, "kind": "ctest", "source": "CMakeLists.txt", "confidence": "medium"})
    return out


def _meson_targets(repo: str) -> list[dict[str, Any]]:
    text = _read_optional(os.path.join(repo, "meson.build"))
    if text is None:
        return []
    return [
        {"name": name, "kind": "executable", "source": "meson.build", "confidence": "medium"}
        for name in re.findall(r"\bexecutable\s*\(\s*['\"]([^'\"]+)['\"]", text)
    ]


def _meson_tests(repo: str) -> list[dict[str, Any]]:
    text = _read_optional(os.path.join(repo, "meson.build"))
    if text is None:
        return []
    return [
        {"name": name, "kind": "meson-test", "source": "meson.build", "confidence": "medium"}
        for name in re.findall(r"\btest\s*\(\s*['\"]([^'\"]+)['\"]", text)
    ]


def _bazel_targets(repo: str) -> list[dict[str, Any]]:
    return _bazel_named_rules(repo, {"cc_binary", "cc_library"})


def _bazel_tests(repo: str) -> list[dict[str, Any]]:
    return _bazel_named_rules(repo, {"cc_test"}, kind="bazel-test")


def _bazel_named_rules(repo: str, rules: set[str], kind: str | None = None) -> list[dict[str, Any]]:
    text = _read_optional(os.path.join(repo, "BUILD.bazel")) or _read_optional(os.path.join(repo, "BUILD"))
    if text is None:
        return []
    out = []
    for rule in rules:
        for block in re.findall(rf"\b{rule}\s*\((.*?)\)", text, flags=re.DOTALL):
            match = re.search(r"name\s*=\s*['\"]([^'\"]+)['\"]", block)
            if match:
                out.append({"name": match.group(1), "kind": kind or rule, "source": "BUILD.bazel", "confidence": "medium"})
    return out


def _ninja_targets(repo: str) -> list[dict[str, Any]]:
    text = _read_optional(os.path.join(repo, "build.ninja"))
    if text is None:
        return []
    out = []
    for name in re.findall(r"^build\s+([^:\s]+)\s*:", text, flags=re.MULTILINE):
        if name != "test":
            out.append({"name": name, "kind": "ninja", "source": "build.ninja", "confidence": "medium"})
    return out


def _ninja_test_targets(repo: str) -> list[dict[str, Any]]:
    text = _read_optional(os.path.join(repo, "build.ninja"))
    if text is None or not re.search(r"^build\s+test\s*:", text, flags=re.MULTILINE):
        return []
    return [{"name": "test", "kind": "ninja", "source": "build.ninja", "confidence": "medium"}]


def _make_targets(repo: str) -> list[dict[str, Any]]:
    text = _read_optional(os.path.join(repo, "Makefile"))
    if text is None:
        return []
    out = []
    for name in re.findall(r"^([A-Za-z0-9_.+-]+)\s*:", text, flags=re.MULTILINE):
        if name not in {"test", "clean", "all"}:
            out.append({"name": name, "kind": "make", "source": "Makefile", "confidence": "medium"})
    return out


def _make_test_targets(repo: str) -> list[dict[str, Any]]:
    text = _read_optional(os.path.join(repo, "Makefile"))
    if text is None or not re.search(r"^test\s*:", text, flags=re.MULTILINE):
        return []
    return [{"name": "test", "kind": "make", "source": "Makefile", "confidence": "medium"}]


def _confidence(
    build_systems: list[dict[str, Any]],
    compile_database: dict[str, Any],
    build_targets: list[dict[str, Any]],
    test_targets: list[dict[str, Any]],
) -> str:
    if any(item.get("confidence") == "high" for item in build_systems + build_targets + test_targets):
        return "high"
    if compile_database.get("present") or build_systems or build_targets or test_targets:
        return "medium"
    return "low"


def _dedupe_named(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out = []
    for item in items:
        key = (str(item.get("name")), str(item.get("kind", "")), str(item.get("source", "")))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _repo_relative(repo: str, path: str) -> str:
    if not path:
        return path
    abs_path = path if os.path.isabs(path) else os.path.join(repo, path)
    try:
        return os.path.relpath(abs_path, repo)
    except ValueError:
        return path


def _read_optional(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None
