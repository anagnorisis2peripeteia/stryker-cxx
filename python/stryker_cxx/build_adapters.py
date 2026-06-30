"""Build, check, and test command adapters for stryker-cxx."""

from __future__ import annotations

import os
import shlex
from typing import Any


def adapter_commands(
    build_system: str | None,
    build_dir: str | None,
    build_target: str | None,
    test_target: str | None,
    test_filter: str | None,
    test_framework: str | None = None,
    test_binary: str | None = None,
    xctest_bundle: str | None = None,
    repo: str | None = None,
    xctest_destination: str | None = None,
    xctest_only_testing: list[str] | None = None,
    xctest_skip_testing: list[str] | None = None,
    xcode_workspace: str | None = None,
    xcode_project: str | None = None,
    xcode_scheme: str | None = None,
    xcode_configuration: str | None = None,
    xcode_sdk: str | None = None,
    xcode_destination: str | None = None,
) -> dict[str, str]:
    out: dict[str, str] = {}
    framework_test = framework_test_command(
        test_framework,
        test_binary,
        test_filter,
        xctest_bundle,
        repo,
        build_dir,
        xctest_destination,
        xctest_only_testing,
        xctest_skip_testing,
    )
    if not build_system:
        if framework_test:
            out["test"] = framework_test
        return out
    system = build_system.lower()
    build_dir = build_dir or "build"
    if system == "cmake":
        out["build"] = " ".join(["cmake", "--build", _shell_quote(build_dir)] + (["--target", _shell_quote(build_target)] if build_target else []))
        test = ["ctest", "--test-dir", _shell_quote(build_dir), "--output-on-failure"]
        if test_filter:
            test.extend(["--tests-regex", _shell_quote(test_filter)])
        out["test"] = framework_test or " ".join(test)
    elif system == "ctest":
        out["build"] = " ".join(["cmake", "--build", _shell_quote(build_dir)] + (["--target", _shell_quote(build_target)] if build_target else []))
        test = ["ctest", "--test-dir", _shell_quote(build_dir), "--output-on-failure"]
        if test_filter:
            test.extend(["--tests-regex", _shell_quote(test_filter)])
        out["test"] = framework_test or " ".join(test)
    elif system == "ninja":
        out["build"] = " ".join(["ninja", "-C", _shell_quote(build_dir)] + ([_shell_quote(build_target)] if build_target else []))
        out["test"] = framework_test or " ".join(["ninja", "-C", _shell_quote(build_dir), _shell_quote(test_target or "test")])
    elif system == "make":
        out["build"] = " ".join(["make", "-C", _shell_quote(build_dir)] + ([_shell_quote(build_target)] if build_target else []))
        out["test"] = framework_test or " ".join(["make", "-C", _shell_quote(build_dir), _shell_quote(test_target or "test")])
    elif system == "meson":
        out["build"] = " ".join(["meson", "compile", "-C", _shell_quote(build_dir)] + ([_shell_quote(build_target)] if build_target else []))
        test = ["meson", "test", "-C", _shell_quote(build_dir)]
        if test_filter:
            test.extend(["--suite", _shell_quote(test_filter)])
        elif test_target:
            test.append(_shell_quote(test_target))
        out["test"] = framework_test or " ".join(test)
    elif system == "bazel":
        out["build"] = " ".join(["bazel", "build", _shell_quote(build_target or "//...")])
        out["test"] = framework_test or " ".join(["bazel", "test", _shell_quote(test_target or build_target or "//...")])
    elif system == "xcodebuild":
        common = _xcodebuild_args(
            xcode_workspace,
            xcode_project,
            xcode_scheme,
            build_target,
            xcode_configuration,
            xcode_sdk,
            xcode_destination,
        )
        out["build"] = " ".join(["xcodebuild", "build", *common])
        if framework_test:
            out["test"] = framework_test
        else:
            test = ["xcodebuild", "test", *common]
            for item in xctest_only_testing or ([test_filter] if test_filter else []):
                test.append(_shell_quote(f"-only-testing:{item}"))
            for item in xctest_skip_testing or []:
                test.append(_shell_quote(f"-skip-testing:{item}"))
            out["test"] = " ".join(test)
    else:
        raise ValueError(f"unknown --build-system: {build_system}")
    return out


def checker_command(
    check_system: str | None,
    check_args: str | None,
    files: str | None,
) -> str | None:
    if not check_system:
        return None
    file_list = _coerce_list(files)
    if not file_list:
        raise ValueError(f"--check-system {check_system} requires --files")
    system = check_system.lower()
    quoted_files = [_shell_quote(file_name) for file_name in file_list]
    args = shlex.split(check_args or "")
    quoted_args = [_shell_quote(arg) for arg in args]
    if system in {"clang", "clang++"}:
        return " ".join([system, "-fsyntax-only", *quoted_args, *quoted_files])
    if system == "clang-tidy":
        return " ".join(["clang-tidy", *quoted_args, *quoted_files])
    if system == "cppcheck":
        return " ".join(["cppcheck", *quoted_args, *quoted_files])
    raise ValueError(f"unknown --check-system: {check_system}")


def framework_test_command(
    test_framework: str | None,
    test_binary: str | None,
    test_filter: str | None,
    xctest_bundle: str | None,
    repo: str | None = None,
    build_dir: str | None = None,
    xctest_destination: str | None = None,
    xctest_only_testing: list[str] | None = None,
    xctest_skip_testing: list[str] | None = None,
) -> str | None:
    if not test_framework:
        return None
    framework = test_framework.lower()
    if framework in {"gtest", "googletest"}:
        test_binary = test_binary or discover_test_binary(repo, build_dir, framework)
        if not test_binary:
            raise ValueError("--test-framework gtest requires --test-binary or a single discoverable test binary under --build-dir/build")
        cmd = [_shell_quote(test_binary)]
        if test_filter:
            cmd.append(f"--gtest_filter={_shell_quote(test_filter)}")
        return " ".join(cmd)
    if framework == "catch2":
        test_binary = test_binary or discover_test_binary(repo, build_dir, framework)
        if not test_binary:
            raise ValueError("--test-framework catch2 requires --test-binary or a single discoverable test binary under --build-dir/build")
        cmd = [_shell_quote(test_binary), "--reporter", "compact"]
        if test_filter:
            cmd.append(_shell_quote(test_filter))
        return " ".join(cmd)
    if framework == "doctest":
        test_binary = test_binary or discover_test_binary(repo, build_dir, framework)
        if not test_binary:
            raise ValueError("--test-framework doctest requires --test-binary or a single discoverable test binary under --build-dir/build")
        cmd = [_shell_quote(test_binary)]
        if test_filter:
            cmd.append(f"--test-case={_shell_quote(test_filter)}")
        return " ".join(cmd)
    if framework == "xctest":
        bundle = xctest_bundle or test_binary
        if not bundle:
            raise ValueError("--test-framework xctest requires --xctest-bundle or --test-binary")
        only_testing = xctest_only_testing or []
        skip_testing = xctest_skip_testing or []
        if xctest_destination or only_testing or skip_testing:
            cmd = ["xcodebuild", "test-without-building", "-xctestrun", _shell_quote(bundle)]
            if xctest_destination:
                cmd.extend(["-destination", _shell_quote(xctest_destination)])
            for item in only_testing or ([test_filter] if test_filter else []):
                cmd.append(_shell_quote(f"-only-testing:{item}"))
            for item in skip_testing:
                cmd.append(_shell_quote(f"-skip-testing:{item}"))
            return " ".join(cmd)
        cmd = ["xcrun", "xctest", _shell_quote(bundle)]
        if test_filter:
            cmd.extend(["-XCTest", _shell_quote(test_filter)])
        return " ".join(cmd)
    raise ValueError(f"unknown --test-framework: {test_framework}")


def discover_test_binary(repo: str | None, build_dir: str | None, test_framework: str | None) -> str | None:
    if not repo or not test_framework:
        return None
    framework = test_framework.lower()
    if framework not in {"gtest", "googletest", "catch2", "doctest"}:
        return None

    repo_root = os.path.abspath(repo)
    search_roots: list[str] = []
    for candidate in [build_dir, "build", "cmake-build-debug", "cmake-build-release", "out", "bin"]:
        if not candidate:
            continue
        path = candidate if os.path.isabs(candidate) else os.path.join(repo_root, candidate)
        if os.path.isdir(path) and path not in search_roots:
            search_roots.append(path)

    matches: list[str] = []
    for root in search_roots:
        for current, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in {".git", "CMakeFiles", "__pycache__"})
            for file_name in sorted(files):
                path = os.path.join(current, file_name)
                if _is_test_binary_candidate(path):
                    matches.append(path)

    unique = sorted(dict.fromkeys(matches))
    if not unique:
        return None
    if len(unique) > 1:
        rel = ", ".join(os.path.relpath(path, repo_root) for path in unique[:5])
        more = "" if len(unique) <= 5 else f", ... ({len(unique)} total)"
        raise ValueError(f"multiple test binaries discovered for {test_framework}: {rel}{more}; pass --test-binary")
    return unique[0]


def _xcodebuild_args(
    workspace: str | None,
    project: str | None,
    scheme: str | None,
    target: str | None,
    configuration: str | None,
    sdk: str | None,
    destination: str | None,
) -> list[str]:
    if workspace and project:
        raise ValueError("--build-system xcodebuild accepts --xcode-workspace or --xcode-project, not both")
    if not scheme and not target:
        raise ValueError("--build-system xcodebuild requires --xcode-scheme or --build-target")
    out: list[str] = []
    if workspace:
        out.extend(["-workspace", _shell_quote(workspace)])
    if project:
        out.extend(["-project", _shell_quote(project)])
    if scheme:
        out.extend(["-scheme", _shell_quote(scheme)])
    else:
        out.extend(["-target", _shell_quote(target or "")])
    if configuration:
        out.extend(["-configuration", _shell_quote(configuration)])
    if sdk:
        out.extend(["-sdk", _shell_quote(sdk)])
    if destination:
        out.extend(["-destination", _shell_quote(destination)])
    return out


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _is_test_binary_candidate(path: str) -> bool:
    name = os.path.basename(path).lower()
    if name.startswith(".") or os.path.isdir(path):
        return False
    if not any(token in name for token in ("test", "spec")):
        return False
    if os.name == "nt":
        return name.endswith((".exe", ".bat", ".cmd"))
    return os.access(path, os.X_OK)
