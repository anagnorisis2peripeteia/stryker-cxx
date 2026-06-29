#!/usr/bin/env python3
"""CLI for the standalone stryker-cxx tool."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
from typing import Any

from . import engine

DEFAULT_CONFIG_FILES = ["stryker-cxx.yml", ".stryker-cxx.yml"]


def _load_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    if not os.path.exists(path):
        return {}

    ext = os.path.splitext(path)[1].lower()
    if ext in {".json", ".js"}:
        with open(path) as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}

    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        raise ValueError(
            "yaml config requested but pyyaml is not installed; install PyYAML or use JSON"
        )

    with open(path) as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


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
    parser.add_argument("--config", default=None, help="Optional YAML/JSON config file")

    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="discover, mutate, build, and run tests")
    run.add_argument("--repo", required=True)
    run.add_argument("--files", required=False)
    run.add_argument("--base", default=None)
    run.add_argument("--lines", default=None)
    run.add_argument("--build-command", required=False, dest="build_command")
    run.add_argument("--test-command", required=False, dest="test_command")
    run.add_argument("--report", required=True)
    run.add_argument("--max-mutants", type=int, default=None)
    run.add_argument("--include-metal", action="store_true")
    run.add_argument("--include", default=None)
    run.add_argument("--exclude", default=None)
    run.add_argument("--mutators", default=None)
    run.add_argument("--output-format", choices=["legacy", "stryker-cxx"], default="stryker-cxx")
    run.add_argument("--format", choices=["json", "markdown", "html", "sarif", "mutation-testing-elements"], default="json")
    run.add_argument("--mode", choices=["token", "clang"], default=None)
    run.add_argument("--jobs", type=int, default=None, help="Parallel mutant execution with isolated worktrees.")
    run.add_argument("--worktree-mode", dest="worktree_mode", choices=["inplace", "git-worktree", "copy"], default=None)
    run.add_argument("--allow-dirty", action="store_true")
    run.add_argument("--threshold", type=float, default=None)
    run.add_argument("--fail-on-empty", action="store_true", dest="fail_on_empty")
    run.add_argument("--timeout", type=int, default=None, dest="timeout", help="Per-mutant timeout in seconds")
    run.add_argument("--artifact-dir", default=None)
    run.add_argument("--resume", default=None)
    run.add_argument("--quiet", action="store_true")
    run.add_argument("--shard-index", type=int, default=None)
    run.add_argument("--shard-total", type=int, default=None)

    list_mutants = subparsers.add_parser("list-mutants", help="list mutants without running build/tests")
    list_mutants.add_argument("--repo", required=True)
    list_mutants.add_argument("--files", required=False)
    list_mutants.add_argument("--base", default=None)
    list_mutants.add_argument("--lines", default=None)
    list_mutants.add_argument("--max-mutants", type=int, default=None)
    list_mutants.add_argument("--include-metal", action="store_true")
    list_mutants.add_argument("--include", default=None)
    list_mutants.add_argument("--exclude", default=None)
    list_mutants.add_argument("--mutators", default=None)
    list_mutants.add_argument("--mode", choices=["token", "clang"], default=None)
    list_mutants.add_argument("--format", choices=["json"], default="json")

    run_mutant = subparsers.add_parser("run-mutant", help="run a single mutant by stable ID")
    run_mutant.add_argument("--repo", required=True)
    run_mutant.add_argument("--id", required=True)
    run_mutant.add_argument("--build-command", required=False, dest="build_command")
    run_mutant.add_argument("--test-command", required=False, dest="test_command")
    run_mutant.add_argument("--report", required=True)
    run_mutant.add_argument("--base", default=None)
    run_mutant.add_argument("--lines", default=None)
    run_mutant.add_argument("--output-format", choices=["legacy", "stryker-cxx"], default="stryker-cxx")
    run_mutant.add_argument("--format", choices=["json", "markdown", "html", "sarif", "mutation-testing-elements"], default="json")
    run_mutant.add_argument("--timeout", type=int, default=None, dest="timeout")
    run_mutant.add_argument("--artifact-dir", default=None)
    run_mutant.add_argument("--mutators", default=None)
    run_mutant.add_argument("--fail-on-empty", action="store_true", dest="fail_on_empty")
    run_mutant.add_argument("--threshold", type=float, default=None)
    run_mutant.add_argument("--mode", choices=["token", "clang"], default=None)
    run_mutant.add_argument("--worktree-mode", dest="worktree_mode", choices=["inplace", "git-worktree", "copy"], default=None)
    run_mutant.add_argument("--allow-dirty", action="store_true")
    run_mutant.add_argument("--quiet", action="store_true")
    run_mutant.add_argument("--shard-index", type=int, default=None)
    run_mutant.add_argument("--shard-total", type=int, default=None)

    return parser


def _resolve_defaults(args: argparse.Namespace) -> dict[str, Any]:
    config_path = _pick_config_path(args.config, getattr(args, "repo", None))
    cfg = _load_config(config_path) if config_path else {}

    execution = cfg.get("execution", {}) if isinstance(cfg.get("execution"), dict) else {}
    report_cfg = cfg.get("report", {}) if isinstance(cfg.get("report"), dict) else {}
    files_cfg = cfg.get("files") if isinstance(cfg.get("files"), dict) else {}
    cfg_mutators = _coerce_mutator_list(cfg.get("mutators"))
    exec_mutators = _coerce_mutator_list(execution.get("mutators"))

    defaults = {
        "repo": getattr(args, "repo", None),
        "files": getattr(args, "files", None) or (cfg.get("files") if isinstance(cfg.get("files"), str) else None),
        "base": getattr(args, "base", None) if getattr(args, "base", None) is not None else cfg.get("base"),
        "build_command": getattr(args, "build_command", None) or execution.get("buildCommand"),
        "test_command": getattr(args, "test_command", None) or execution.get("testCommand"),
        "max_mutants": getattr(args, "max_mutants", None) if getattr(args, "max_mutants", None) is not None else execution.get("maxMutants"),
        "include_metal": bool(
            getattr(args, "include_metal", False)
            or execution.get("includeMetal", False)
        ),
        "mutators": getattr(args, "mutators", None) or execution.get("mutators") or execution.get("mutationMutators") or cfg.get("mutators"),
        "threshold": getattr(args, "threshold", None) if getattr(args, "threshold", None) is not None else execution.get("threshold"),
        "timeout": getattr(args, "timeout", None) if getattr(args, "timeout", None) is not None else execution.get("timeoutSeconds"),
        "artifact_dir": getattr(args, "artifact_dir", None) if hasattr(args, "artifact_dir") else execution.get("artifactDir"),
        "mode": getattr(args, "mode", None) if getattr(args, "mode", None) is not None else execution.get("mode", "token"),
        "jobs": getattr(args, "jobs", None) if getattr(args, "jobs", None) is not None else execution.get("jobs", 1),
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
        defaults["mutators"] = ",".join(engine.DEFAULT_MUTATORS)

    if defaults["threshold"] is None:
        defaults["threshold"] = defaults.get("report_threshold")

    if defaults["jobs"] is not None and defaults["jobs"] < 1:
        raise ValueError("--jobs must be >= 1")

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

    if defaults["build_command"] is None or defaults["test_command"] is None:
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

    return defaults


def _run(args: argparse.Namespace) -> int:
    cfg = _resolve_defaults(args)

    files = cfg["files"]
    if not files:
        raise ValueError("run requires --files or stryker-cxx config files.include")

    if not cfg["build_command"] or not cfg["test_command"]:
        raise ValueError("run requires --build-command/--test-command or config equivalents")

    mutators = cfg["mutators"]
    legacy_args = [
        "--repo-dir",
        cfg["repo"],
        "--files",
        ",".join(files),
        "--build-cmd",
        cfg["build_command"],
        "--test-cmd",
        cfg["test_command"],
        "--report",
        cfg["report"],
        "--output-format",
        cfg["output_format"],
        "--format",
        cfg["format"],
        "--mutators",
        mutators,
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
    if cfg["timeout"] is not None:
        legacy_args.extend(["--timeout", str(cfg["timeout"])])
    if cfg["mode"] is not None:
        legacy_args.extend(["--mode", str(cfg["mode"])])
    if cfg["jobs"] is not None:
        legacy_args.extend(["--jobs", str(cfg["jobs"])])
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
    if cfg["resume"]:
        legacy_args.extend(["--resume", cfg["resume"]])
    if cfg["fail_on_empty"]:
        legacy_args.append("--fail-on-empty")
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
        pending += engine._discover_mode(repo_root, path, only, [m for m in enabled if m], cfg["mode"])

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
            "nodeKind": mut.nodeKind,
            "mode": cfg["mode"],
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

    if not cfg["build_command"] or not cfg["test_command"]:
        raise ValueError("run-mutant requires --build-command/--test-command or config equivalents")

    file_hint = _parse_mutant_id(args.id)
    legacy_args = [
        "--repo-dir",
        cfg["repo"],
        "--files",
        file_hint,
        "--build-cmd",
        cfg["build_command"],
        "--test-cmd",
        cfg["test_command"],
        "--report",
        cfg["report"],
        "--output-format",
        cfg["output_format"],
        "--format",
        cfg["format"],
        "--run-mutant-id",
        args.id,
        "--mutators",
        cfg["mutators"],
    ]
    if cfg["base"]:
        legacy_args.extend(["--diff-base", cfg["base"]])
    if args.lines:
        legacy_args.extend(["--lines", args.lines])
    if cfg["timeout"] is not None:
        legacy_args.extend(["--timeout", str(cfg["timeout"])])
    if cfg["mode"] is not None:
        legacy_args.extend(["--mode", str(cfg["mode"])])
    if cfg["jobs"] is not None:
        legacy_args.extend(["--jobs", str(cfg["jobs"])])
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
    if cfg["fail_on_empty"]:
        legacy_args.append("--fail-on-empty")
    if cfg["quiet"]:
        legacy_args.append("--quiet")
    if cfg["threshold"] is not None:
        legacy_args.extend(["--threshold", str(cfg["threshold"])])

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
        parser.error(f"unsupported command: {args.command}")
    except Exception as exc:
        print(f"error: {exc}")
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
