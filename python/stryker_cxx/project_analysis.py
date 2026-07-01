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
    cmake_targets = _cmake_target_sources(repo_root)
    build_target_sources = _build_target_sources(repo_root, cmake_targets)
    build_systems = _build_systems(
        repo_root,
        build_system=build_system,
        build_command=build_command,
        compile_database=compile_database,
    )
    source_targets = _source_targets(repo_root, target_files, compile_database, build_target_sources)
    build_targets = _build_targets(
        repo_root,
        build_system=build_system,
        build_target=build_target,
        build_dir=build_dir,
        build_target_sources=build_target_sources,
    )
    test_targets = _test_targets(
        repo_root,
        test_target=test_target,
        test_framework=test_framework,
        test_binary=test_binary,
        test_command=test_command,
        cmake_targets=cmake_targets,
    )
    confidence = _confidence(build_systems, compile_database, build_targets, test_targets)
    build_graph = _build_graph(
        repo_root,
        target_files,
        compile_database,
        source_targets,
        build_targets,
        test_targets,
        confidence,
    )
    return {
        "schemaVersion": "stryker-cxx.project-analysis.v1",
        "confidence": confidence,
        "repo": repo_root,
        "targetFiles": [_repo_relative(repo_root, path) for path in target_files],
        "buildSystems": build_systems,
        "compileDatabase": compile_database,
        "buildGraph": build_graph,
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
    file_entries = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("file"):
            continue
        command = entry.get("command")
        arguments = entry.get("arguments")
        file_entries.append(
            {
                "file": _repo_relative(repo, str(entry.get("file"))),
                "directory": _repo_relative(repo, str(entry.get("directory", ""))),
                "command": str(command) if command is not None else None,
                "arguments": [str(arg) for arg in arguments] if isinstance(arguments, list) else None,
                "output": _repo_relative(repo, str(entry.get("output", ""))) if entry.get("output") else None,
            }
        )
    files = sorted({str(entry["file"]) for entry in file_entries})
    directories = sorted({str(entry["directory"]) for entry in file_entries if entry.get("directory")})
    return {
        "present": True,
        "path": "compile_commands.json",
        "status": "loaded",
        "entries": len(entries),
        "files": files,
        "directories": directories,
        "fileEntries": file_entries,
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


def _source_targets(
    repo: str,
    target_files: list[str],
    compile_database: dict[str, Any],
    build_target_sources: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    compile_db_files = set(compile_database.get("files") or [])
    compile_db_entries = {
        str(entry.get("file")): entry
        for entry in compile_database.get("fileEntries") or []
        if isinstance(entry, dict) and entry.get("file")
    }
    source_owners: dict[str, list[dict[str, Any]]] = {}
    for target in (build_target_sources or {}).values():
        target_name = str(target.get("name", ""))
        for source in target.get("sourceFiles") or []:
            source_owners.setdefault(str(source), []).append(target)
    out = []
    for file_name in target_files:
        relative = _repo_relative(repo, file_name)
        compile_entry = compile_db_entries.get(relative)
        matched = relative in compile_db_files
        owning_records = sorted(
            source_owners.get(relative, []),
            key=lambda item: (str(item.get("source", "")), str(item.get("name", ""))),
        )
        owning_build_targets = sorted({str(item.get("name", "")) for item in owning_records if item.get("name")})
        owning_sources = sorted({str(item.get("source", "")) for item in owning_records if item.get("source")})
        confidence = "high" if matched else ("medium" if owning_build_targets else "medium")
        source_owned_kind = (
            "cmake-target-source"
            if owning_sources == ["CMakeLists.txt"]
            else "build-target-source"
        )
        ownership_kind = (
            "compile-database-unit"
            if matched
            else (source_owned_kind if owning_build_targets else "explicit-source")
        )
        ownership_source = (
            "compile_commands.json"
            if matched
            else (",".join(owning_sources) if owning_sources else "cli-or-config")
        )
        record = {
            "file": relative,
            "analysisKey": _stable_analysis_key(
                "source",
                relative,
                ownership_kind,
                ownership_source,
                ",".join(owning_build_targets),
            ),
            "source": "cli-or-config",
            "compileDatabaseMatched": matched,
            "confidence": confidence,
            "ownership": {
                "kind": ownership_kind,
                "source": ownership_source,
                "confidence": confidence,
                "buildTargets": owning_build_targets,
                "key": _stable_analysis_key(
                    ownership_kind,
                    relative,
                    ownership_source,
                    ",".join(owning_build_targets),
                ),
            },
            "owningBuildTargets": owning_build_targets,
            "owningBuildTargetSources": owning_sources,
        }
        if compile_entry:
            record.update(
                {
                    "compileDirectory": compile_entry.get("directory"),
                    "compileCommand": compile_entry.get("command"),
                    "compileArguments": compile_entry.get("arguments"),
                    "compileOutput": compile_entry.get("output"),
                }
            )
        out.append(
            record
        )
    return out


def _build_graph(
    repo: str,
    target_files: list[str],
    compile_database: dict[str, Any],
    source_targets: list[dict[str, Any]],
    build_targets: list[dict[str, Any]],
    test_targets: list[dict[str, Any]],
    confidence: str,
) -> dict[str, Any]:
    relative_targets = [_repo_relative(repo, path) for path in target_files]
    matched_files = [
        str(item.get("file"))
        for item in source_targets
        if item.get("compileDatabaseMatched") and item.get("file")
    ]
    missing_files = sorted(set(relative_targets) - set(matched_files))
    source_nodes = [_build_graph_source_node(item) for item in source_targets]
    build_target_nodes = [_build_graph_target_node(item) for item in build_targets]
    test_target_nodes = [_build_graph_target_node(item) for item in test_targets]
    diagnostics: list[dict[str, Any]] = []
    if not compile_database.get("present"):
        diagnostics.append(
            {
                "level": "info",
                "code": "compile-database-missing",
                "message": "compile_commands.json not found; source ownership uses explicit file scope and build-system metadata",
            }
        )
    elif missing_files:
        diagnostics.append(
            {
                "level": "warning",
                "code": "compile-database-target-miss",
                "message": "compile_commands.json does not cover every target file",
                "files": missing_files,
            }
        )
    if not any(node.get("buildTargets") for node in source_nodes):
        diagnostics.append(
            {
                "level": "info",
                "code": "build-target-ownership-missing",
                "message": "no build target owns the selected source files; compiled-object ownership may require explicit artifact paths",
            }
        )
    ownership_model = "explicit-source"
    if matched_files and not missing_files:
        ownership_model = "compile-database"
    elif any(node.get("buildTargets") for node in source_nodes):
        ownership_model = "build-system-targets"
    elif compile_database.get("present"):
        ownership_model = "partial-compile-database"
    return {
        "schemaVersion": "stryker-cxx.build-graph.v1",
        "confidence": confidence,
        "ownershipModel": ownership_model,
        "compileDatabase": {
            "present": bool(compile_database.get("present")),
            "path": compile_database.get("path"),
            "status": compile_database.get("status"),
            "entries": compile_database.get("entries", 0),
            "matchedTargetFiles": sorted(matched_files),
            "missingTargetFiles": missing_files,
        },
        "sourceNodes": source_nodes,
        "buildTargetNodes": build_target_nodes,
        "testTargetNodes": test_target_nodes,
        "diagnostics": diagnostics,
    }


def _build_graph_source_node(item: dict[str, Any]) -> dict[str, Any]:
    ownership = item.get("ownership") if isinstance(item.get("ownership"), dict) else {}
    node = {
        "file": item.get("file"),
        "analysisKey": item.get("analysisKey"),
        "ownershipKey": ownership.get("key"),
        "ownershipKind": ownership.get("kind"),
        "ownershipSource": ownership.get("source"),
        "confidence": ownership.get("confidence") or item.get("confidence"),
        "buildTargets": list(ownership.get("buildTargets") or item.get("owningBuildTargets") or []),
        "compileDatabaseMatched": bool(item.get("compileDatabaseMatched")),
    }
    if item.get("compileDirectory") is not None:
        node["compileDirectory"] = item.get("compileDirectory")
    if item.get("compileOutput") is not None:
        node["compileOutput"] = item.get("compileOutput")
    if item.get("compileCommand") is not None:
        node["compileCommand"] = item.get("compileCommand")
    if item.get("compileArguments") is not None:
        node["compileArguments"] = item.get("compileArguments")
    return node


def _build_graph_target_node(item: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": item.get("name"),
        "kind": item.get("kind"),
        "source": item.get("source"),
        "confidence": item.get("confidence"),
        "analysisKey": item.get("analysisKey"),
    }
    for key in ("sourceFiles", "dependencies", "relatedBuildTarget", "command"):
        if item.get(key) is not None:
            out[key] = item.get(key)
    return out


def _build_targets(
    repo: str,
    *,
    build_system: str | None,
    build_target: str | None,
    build_dir: str | None,
    build_target_sources: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if build_target:
        out.append({"name": build_target, "kind": "build", "source": "explicit", "confidence": "high"})
    out.extend((build_target_sources or _build_target_sources(repo)).values())
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
    cmake_targets: dict[str, dict[str, Any]] | None = None,
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
    out.extend(_cmake_tests(repo, cmake_targets))
    out.extend(_meson_tests(repo))
    out.extend(_bazel_tests(repo))
    out.extend(_ninja_test_targets(repo))
    out.extend(_make_test_targets(repo))
    out.extend(_xcode_tests(repo))
    return _dedupe_named(out)


def _build_target_sources(
    repo: str,
    cmake_targets: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for target in list((cmake_targets or _cmake_target_sources(repo)).values()):
        _record_target_source(out, target)
    for target in _meson_targets(repo):
        _record_target_source(out, target)
    for target in _bazel_targets(repo):
        _record_target_source(out, target)
    for target in _ninja_targets(repo):
        _record_target_source(out, target)
    for target in _make_targets(repo):
        _record_target_source(out, target)
    for target in _xcode_targets(repo):
        _record_target_source(out, target)
    return out


def _record_target_source(targets: dict[str, dict[str, Any]], target: dict[str, Any]) -> None:
    target.setdefault(
        "analysisKey",
        _stable_analysis_key(
            "build-target",
            str(target.get("source", "")),
            str(target.get("kind", "")),
            str(target.get("name", "")),
        ),
    )
    key = f"{target.get('source')}:{target.get('kind')}:{target.get('name')}"
    targets[key] = target


def _cmake_targets(
    repo: str,
    cmake_targets: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if cmake_targets is not None:
        return list(cmake_targets.values())
    text = _read_optional(os.path.join(repo, "CMakeLists.txt"))
    if text is None:
        return []
    out = []
    for kind, name in re.findall(r"\badd_(executable|library)\s*\(\s*([A-Za-z0-9_.:+-]+)", text, flags=re.IGNORECASE):
        out.append({"name": name, "kind": kind.lower(), "source": "CMakeLists.txt", "confidence": "medium"})
    return out


def _cmake_tests(
    repo: str,
    cmake_targets: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    text = _read_optional(os.path.join(repo, "CMakeLists.txt"))
    if text is None:
        return []
    out = []
    target_names = set((cmake_targets or _cmake_target_sources(repo)).keys())
    for block in re.findall(r"\badd_test\s*\((.*?)\)", text, flags=re.IGNORECASE | re.DOTALL):
        tokens = _cmake_tokens(block)
        if not tokens:
            continue
        name = ""
        command = ""
        if tokens[0].upper() == "NAME":
            for index, token in enumerate(tokens):
                upper = token.upper()
                if upper == "NAME" and index + 1 < len(tokens):
                    name = tokens[index + 1]
                elif upper == "COMMAND" and index + 1 < len(tokens):
                    command = tokens[index + 1]
                    break
        elif len(tokens) >= 2:
            name = tokens[0]
            command = tokens[1]
        if not name or not command:
            continue
        record = {
            "name": name,
            "kind": "ctest",
            "source": "CMakeLists.txt",
            "confidence": "medium",
            "command": command,
        }
        if command in target_names:
            record["relatedBuildTarget"] = command
        out.append(record)
    return out


def _cmake_target_sources(repo: str) -> dict[str, dict[str, Any]]:
    text = _read_optional(os.path.join(repo, "CMakeLists.txt"))
    if text is None:
        return {}
    targets: dict[str, dict[str, Any]] = {}
    for kind, block in re.findall(
        r"\badd_(executable|library)\s*\((.*?)\)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        tokens = _cmake_tokens(block)
        if not tokens:
            continue
        name = tokens[0]
        source_files = [
            _repo_relative(repo, token)
            for token in tokens[1:]
            if _looks_like_source_file(token)
        ]
        targets[name] = {
            "name": name,
            "kind": kind.lower(),
            "source": "CMakeLists.txt",
            "confidence": "medium",
            "sourceFiles": source_files,
        }
    for block in re.findall(
        r"\btarget_sources\s*\((.*?)\)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        tokens = _cmake_tokens(block)
        if not tokens:
            continue
        name = tokens[0]
        source_files = [
            _repo_relative(repo, token)
            for token in tokens[1:]
            if _looks_like_source_file(token)
        ]
        if not source_files:
            continue
        target = targets.setdefault(
            name,
            {
                "name": name,
                "kind": "target",
                "source": "CMakeLists.txt",
                "confidence": "medium",
                "sourceFiles": [],
            },
        )
        existing = list(target.get("sourceFiles") or [])
        target["sourceFiles"] = _stable_unique(existing + source_files)
    return targets


def _cmake_tokens(block: str) -> list[str]:
    out: list[str] = []
    for match in re.finditer(r'"([^"]+)"|\'([^\']+)\'|([^\s()]+)', block):
        token = match.group(1) or match.group(2) or match.group(3) or ""
        if token:
            out.append(token)
    return out


def _looks_like_source_file(token: str) -> bool:
    if token.startswith("$") or token.upper() in {
        "STATIC",
        "SHARED",
        "MODULE",
        "OBJECT",
        "INTERFACE",
        "EXCLUDE_FROM_ALL",
        "WIN32",
        "MACOSX_BUNDLE",
    }:
        return False
    return os.path.splitext(token)[1].lower() in {
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".m",
        ".mm",
        ".h",
        ".hh",
        ".hpp",
        ".hxx",
        ".metal",
    }


def _meson_targets(repo: str) -> list[dict[str, Any]]:
    text = _read_optional(os.path.join(repo, "meson.build"))
    if text is None:
        return []
    out: list[dict[str, Any]] = []
    for callee, kind in (
        ("executable", "executable"),
        ("library", "library"),
        ("static_library", "static-library"),
        ("shared_library", "shared-library"),
    ):
        for block in re.findall(rf"\b{re.escape(callee)}\s*\((.*?)\)", text, flags=re.DOTALL):
            tokens = re.findall(r"['\"]([^'\"]+)['\"]", block)
            if not tokens:
                continue
            name = tokens[0]
            source_files = _stable_unique(
                [
                    _repo_relative(repo, token)
                    for token in tokens[1:]
                    if _looks_like_source_file(token)
                ]
            )
            out.append(
                {
                    "name": name,
                    "kind": kind,
                    "source": "meson.build",
                    "confidence": "medium",
                    "sourceFiles": source_files,
                }
            )
    return out


def _meson_tests(repo: str) -> list[dict[str, Any]]:
    text = _read_optional(os.path.join(repo, "meson.build"))
    if text is None:
        return []
    out: list[dict[str, Any]] = []
    for block in re.findall(r"\btest\s*\((.*?)\)", text, flags=re.DOTALL):
        tokens = re.findall(r"['\"]([^'\"]+)['\"]|([A-Za-z_][A-Za-z0-9_]*)", block)
        flat = [quoted or bare for quoted, bare in tokens if quoted or bare]
        if not flat:
            continue
        record = {"name": flat[0], "kind": "meson-test", "source": "meson.build", "confidence": "medium"}
        if len(flat) > 1:
            record["relatedBuildTarget"] = flat[1]
        out.append(record)
    return out


def _bazel_targets(repo: str) -> list[dict[str, Any]]:
    return _bazel_named_rules(repo, {"cc_binary", "cc_library", "cc_test"})


def _bazel_tests(repo: str) -> list[dict[str, Any]]:
    tests = _bazel_named_rules(repo, {"cc_test"}, kind="bazel-test")
    for test in tests:
        dependencies = test.get("dependencies")
        if isinstance(dependencies, list) and dependencies:
            test["relatedBuildTarget"] = dependencies[0]
        else:
            test.setdefault("relatedBuildTarget", test.get("name"))
    return tests


def _bazel_named_rules(repo: str, rules: set[str], kind: str | None = None) -> list[dict[str, Any]]:
    out = []
    for package_dir, source, text in _bazel_build_files(repo):
        for rule in rules:
            for block in re.findall(rf"\b{rule}\s*\((.*?)\)", text, flags=re.DOTALL):
                match = re.search(r"name\s*=\s*['\"]([^'\"]+)['\"]", block)
                if match:
                    target_name = _bazel_target_name(package_dir, match.group(1))
                    source_files = _stable_unique(
                        [
                            _bazel_source_path(repo, package_dir, source)
                            for source in re.findall(r"['\"]([^'\"]+)['\"]", block)
                            if _looks_like_source_file(_bazel_source_token_path(source))
                        ]
                    )
                    record = {
                        "name": target_name,
                        "kind": kind or rule,
                        "source": source,
                        "confidence": "medium",
                    }
                    if source_files:
                        record["sourceFiles"] = source_files
                    dependencies = _bazel_dependencies(package_dir, block)
                    if dependencies:
                        record["dependencies"] = dependencies
                    out.append(record)
    return out


def _bazel_build_files(repo: str) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    ignored_dirs = {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        "agent_space",
        "bazel-bin",
        "bazel-out",
        "bazel-testlogs",
        "build",
        "cmake-build-debug",
        "cmake-build-release",
        "node_modules",
    }
    for current, dirs, files in os.walk(repo):
        dirs[:] = sorted(
            name
            for name in dirs
            if name not in ignored_dirs
            and not name.startswith("bazel-")
            and not name.startswith(".")
        )
        for file_name in ("BUILD.bazel", "BUILD"):
            if file_name not in files:
                continue
            path = os.path.join(current, file_name)
            text = _read_optional(path)
            if text is None:
                continue
            package_dir = os.path.relpath(current, repo)
            if package_dir == ".":
                package_dir = ""
            out.append((package_dir, _repo_relative(repo, path), text))
            break
    return out


def _bazel_target_name(package_dir: str, name: str) -> str:
    return name if not package_dir else f"//{package_dir}:{name}"


def _bazel_source_token_path(token: str) -> str:
    if token.startswith("//") and ":" in token:
        package, name = token[2:].split(":", 1)
        return os.path.join(package, name)
    if token.startswith(":"):
        return token[1:]
    return token


def _bazel_source_path(repo: str, package_dir: str, token: str) -> str:
    source = _bazel_source_token_path(token)
    if os.path.isabs(source):
        return _repo_relative(repo, source)
    if token.startswith("//"):
        return _repo_relative(repo, source)
    return _repo_relative(repo, os.path.join(package_dir, source))


def _bazel_dependencies(package_dir: str, block: str) -> list[str]:
    match = re.search(r"\bdeps\s*=\s*\[(.*?)\]", block, flags=re.DOTALL)
    if not match:
        return []
    return _stable_unique(
        [
            _bazel_label_to_target(package_dir, dep)
            for dep in re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))
            if dep and not _looks_like_source_file(_bazel_source_token_path(dep))
        ]
    )


def _bazel_label_to_target(package_dir: str, label: str) -> str:
    if label.startswith("//"):
        return label
    if label.startswith(":"):
        return _bazel_target_name(package_dir, label[1:])
    return _bazel_target_name(package_dir, label)


def _ninja_targets(repo: str) -> list[dict[str, Any]]:
    text = _read_optional(os.path.join(repo, "build.ninja"))
    if text is None:
        return []
    out = []
    for name, inputs in re.findall(r"^build\s+([^:\s]+)\s*:\s*[^\s:]+(?:\s+(.+))?$", text, flags=re.MULTILINE):
        if name != "test":
            source_files = [
                _repo_relative(repo, token)
                for token in (inputs or "").split()
                if _looks_like_source_file(token)
            ]
            out.append(
                {
                    "name": name,
                    "kind": "ninja",
                    "source": "build.ninja",
                    "confidence": "medium",
                    "sourceFiles": source_files,
                }
            )
    return out


def _ninja_test_targets(repo: str) -> list[dict[str, Any]]:
    text = _read_optional(os.path.join(repo, "build.ninja"))
    if text is None or not re.search(r"^build\s+test\s*:", text, flags=re.MULTILINE):
        return []
    match = re.search(r"^build\s+test\s*:\s*[^\s:]+(?:\s+(.+))?$", text, flags=re.MULTILINE)
    record = {"name": "test", "kind": "ninja", "source": "build.ninja", "confidence": "medium"}
    if match:
        related = _first_build_target_token(match.group(1) or "")
        if related:
            record["relatedBuildTarget"] = related
    return [record]


def _make_targets(repo: str) -> list[dict[str, Any]]:
    text = _read_optional(os.path.join(repo, "Makefile"))
    if text is None:
        return []
    out = []
    for name, prerequisites in re.findall(r"^([A-Za-z0-9_.+-]+)\s*:\s*(.*)$", text, flags=re.MULTILINE):
        if name not in {"test", "clean", "all"}:
            source_files = [
                _repo_relative(repo, token)
                for token in prerequisites.split()
                if _looks_like_source_file(token)
            ]
            out.append(
                {
                    "name": name,
                    "kind": "make",
                    "source": "Makefile",
                    "confidence": "medium",
                    "sourceFiles": source_files,
                }
            )
    return out


def _make_test_targets(repo: str) -> list[dict[str, Any]]:
    text = _read_optional(os.path.join(repo, "Makefile"))
    if text is None:
        return []
    match = re.search(r"^test\s*:\s*(.*)$", text, flags=re.MULTILINE)
    if not match:
        return []
    record = {"name": "test", "kind": "make", "source": "Makefile", "confidence": "medium"}
    related = _first_build_target_token(match.group(1) or "")
    if related:
        record["relatedBuildTarget"] = related
    return [record]


def _xcode_targets(repo: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for project_source, text in _xcode_project_texts(repo):
        file_refs = _xcode_file_refs(repo, text)
        build_files = _xcode_build_files(text, file_refs)
        source_phases = _xcode_source_phases(text, build_files)
        native_targets = _xcode_native_target_blocks(text)
        target_names = {target_id: name for target_id, name, _block in native_targets}
        dependency_targets = _xcode_target_dependency_targets(text, target_names)
        for target_id, name, block in native_targets:
            phase_ids = _xcode_list_property(block, "buildPhases")
            dependencies = [
                dependency_targets[dependency_id]
                for dependency_id in _xcode_list_property(block, "dependencies")
                if dependency_id in dependency_targets
            ]
            source_files = sorted(
                {
                    source
                    for phase_id in phase_ids
                    for source in source_phases.get(phase_id, [])
                }
            )
            product_type = _xcode_scalar_property(block, "productType")
            out.append(
                {
                    "name": name,
                    "kind": "xcode-target",
                    "source": project_source,
                    "confidence": "medium" if source_files else "low",
                    "sourceFiles": source_files,
                    "productType": product_type,
                    "xcodeTargetId": target_id,
                }
            )
            if dependencies:
                out[-1]["dependencies"] = dependencies
                out[-1]["relatedBuildTarget"] = dependencies[0]
    return out


def _xcode_tests(repo: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for target in _xcode_targets(repo):
        product_type = str(target.get("productType") or "")
        if "unit-test" not in product_type and "ui-testing" not in product_type:
            continue
        record = {
            "name": str(target.get("name")),
            "kind": "xcode-test",
            "source": str(target.get("source")),
            "confidence": "medium",
            "relatedBuildTarget": str(target.get("relatedBuildTarget") or target.get("name")),
            "productType": product_type,
        }
        dependencies = target.get("dependencies")
        if isinstance(dependencies, list) and dependencies:
            record["dependencies"] = dependencies
        out.append(record)
    return out


def _xcode_project_texts(repo: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    try:
        entries = sorted(os.listdir(repo))
    except OSError:
        return out
    for entry in entries:
        if not entry.endswith(".xcodeproj"):
            continue
        rel = os.path.join(entry, "project.pbxproj")
        text = _read_optional(os.path.join(repo, rel))
        if text is not None:
            out.append((rel, text))
    return out


def _xcode_file_refs(repo: str, text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in re.finditer(
        r"(?P<id>[A-Za-z0-9]+)\s*/\*\s*(?P<comment>[^*]+?)\s*\*/\s*=\s*\{(?P<body>.*?)\};",
        text,
        flags=re.DOTALL,
    ):
        body = match.group("body")
        if "isa = PBXFileReference" not in body:
            continue
        path = _xcode_scalar_property(body, "path") or match.group("comment").strip()
        if _looks_like_source_file(path):
            out[match.group("id")] = _repo_relative(repo, path)
    return out


def _xcode_build_files(text: str, file_refs: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in re.finditer(
        r"(?P<id>[A-Za-z0-9]+)\s*/\*\s*[^*]+?\s*\*/\s*=\s*\{(?P<body>.*?)\};",
        text,
        flags=re.DOTALL,
    ):
        body = match.group("body")
        if "isa = PBXBuildFile" not in body:
            continue
        file_ref = re.search(r"fileRef\s*=\s*([A-Za-z0-9]+)\s*/\*", body)
        if file_ref and file_ref.group(1) in file_refs:
            out[match.group("id")] = file_refs[file_ref.group(1)]
    return out


def _xcode_source_phases(text: str, build_files: dict[str, str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for match in re.finditer(
        r"(?P<id>[A-Za-z0-9]+)\s*/\*\s*Sources\s*\*/\s*=\s*\{(?P<body>.*?)\};",
        text,
        flags=re.DOTALL,
    ):
        body = match.group("body")
        if "isa = PBXSourcesBuildPhase" not in body:
            continue
        file_ids = _xcode_list_property(body, "files")
        out[match.group("id")] = [
            build_files[file_id]
            for file_id in file_ids
            if file_id in build_files
        ]
    return out


def _xcode_native_target_blocks(text: str) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for match in re.finditer(
        r"(?P<id>[A-Za-z0-9]+)\s*/\*\s*(?P<comment>[^*]+?)\s*\*/\s*=\s*\{(?P<body>.*?)\};",
        text,
        flags=re.DOTALL,
    ):
        body = match.group("body")
        if "isa = PBXNativeTarget" not in body:
            continue
        name = _xcode_scalar_property(body, "name") or match.group("comment").strip()
        out.append((match.group("id"), name, body))
    return out


def _xcode_target_dependency_targets(text: str, target_names: dict[str, str]) -> dict[str, str]:
    proxy_targets = _xcode_container_proxy_targets(text)
    out: dict[str, str] = {}
    for match in re.finditer(
        r"(?P<id>[A-Za-z0-9]+)\s*/\*\s*[^*]+?\s*\*/\s*=\s*\{(?P<body>.*?)\};",
        text,
        flags=re.DOTALL,
    ):
        body = match.group("body")
        if "isa = PBXTargetDependency" not in body:
            continue
        target_id = _xcode_object_reference(body, "target")
        if not target_id:
            proxy_id = _xcode_object_reference(body, "targetProxy")
            target_id = proxy_targets.get(proxy_id or "")
        if target_id and target_id in target_names:
            out[match.group("id")] = target_names[target_id]
    return out


def _xcode_container_proxy_targets(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in re.finditer(
        r"(?P<id>[A-Za-z0-9]+)\s*/\*\s*[^*]+?\s*\*/\s*=\s*\{(?P<body>.*?)\};",
        text,
        flags=re.DOTALL,
    ):
        body = match.group("body")
        if "isa = PBXContainerItemProxy" not in body:
            continue
        target_id = _xcode_scalar_property(body, "remoteGlobalIDString")
        if target_id:
            out[match.group("id")] = target_id
    return out


def _xcode_object_reference(block: str, key: str) -> str | None:
    match = re.search(rf"\b{re.escape(key)}\s*=\s*([A-Za-z0-9]+)\s*(?:/\*[^*]*\*/)?;", block)
    return match.group(1) if match else None


def _xcode_list_property(block: str, key: str) -> list[str]:
    match = re.search(rf"\b{re.escape(key)}\s*=\s*\((.*?)\);", block, flags=re.DOTALL)
    if not match:
        return []
    return re.findall(r"\b([A-Za-z0-9]+)\b\s*(?:/\*[^*]*\*/)?\s*,", match.group(1))


def _xcode_scalar_property(block: str, key: str) -> str | None:
    match = re.search(rf"\b{re.escape(key)}\s*=\s*(?:\"([^\"]+)\"|([^;\n]+));", block)
    if not match:
        return None
    return (match.group(1) or match.group(2) or "").strip()


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
        item.setdefault(
            "analysisKey",
            _stable_analysis_key(
                str(item.get("source", "")),
                str(item.get("kind", "")),
                str(item.get("name", "")),
            ),
        )
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


def _first_build_target_token(value: str) -> str | None:
    for token in value.split():
        if not token or token.startswith("$") or _looks_like_source_file(token):
            continue
        return token
    return None


def _stable_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _stable_analysis_key(*parts: str) -> str:
    cleaned = [
        re.sub(r"[^A-Za-z0-9_.:/@+-]+", "-", str(part).strip()).strip("-")
        for part in parts
        if str(part).strip()
    ]
    return "|".join(cleaned)
