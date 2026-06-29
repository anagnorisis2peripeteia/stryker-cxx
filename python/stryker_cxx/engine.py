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
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
import shlex
import subprocess
import sys
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import time
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
    "EqualityOperator": [("==", "!="), ("!=","==")],
    "LogicalOperator": [("&&", "||"), ("||", "&&")],
    "BooleanLiteral": [("true", "false"), ("false", "true")],
    "ArithmeticOperator": [("+", "-"), ("-", "+"), ("*", "/"), ("/", "*")],
    "AssignmentOperator": [("+=", "-="), ("-=", "+="), ("*=", "/="), ("/=", "*=")],
    "BitwiseOperator": [("&", "|"), ("|", "&"), ("^", "|")],
    "UnaryOperator": [("!", ""), ("!", "!!")],
    "ReturnValue": [("return true", "return false"), ("return false", "return true")],
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
}

_CALL_REMOVAL_RE = re.compile(
    r"\b(?!if\b|for\b|while\b|switch\b|return\b|sizeof\b|catch\b)"
    r"([A-Za-z_]\w*(?:(?:\s*::\s*|\s*->\s*|\s*\.\s*)[A-Za-z_]\w*)*\s*\([^;{}]*\))(?=\s*;)"
)

SOURCE_EXTENSIONS = {".cpp", ".cc", ".cxx", ".c", ".mm", ".m", ".h", ".hpp", ".hh", ".hxx"}
DEFAULT_MUTATORS = ["ConditionalBoundary", "EqualityOperator", "LogicalOperator", "BooleanLiteral"]


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
    status: str = "PENDING"  # KILLED | SURVIVED | BUILD_ERROR | TIMEOUT | IGNORED | PENDING
    detail: str = ""
    ignoreReason: str = ""
    durationMs: int = 0
    buildLog: str = ""
    testLog: str = ""
    run: dict[str, Any] = field(default_factory=dict)


@dataclass
class Report:
    target_files: list[str]
    tool: str = "stryker-cxx"
    repo: str | None = None
    base: str | None = None
    threshold: float | None = None
    timeoutSeconds: int | None = None
    buildCommand: str | None = None
    testCommand: str | None = None
    total: int = 0
    killed: int = 0
    survived: int = 0
    buildError: int = 0
    timeouts: int = 0
    ignored: int = 0
    execution: dict[str, Any] = field(default_factory=dict)
    mutants: list[dict] = field(default_factory=list)
    startedAt: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
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
        self.completedAt = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


FATAL_STATUSES = {"KILLED", "SURVIVED", "BUILD_ERROR", "TIMEOUT", "IGNORED"}


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
    "CallRemoval": {"CALL_EXPR", "CXX_MEMBER_CALL_EXPR", "OBJC_MESSAGE_EXPR"},
}


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


def run_cmd(cmd: str, repo: str, log: str, timeout: int | None = None) -> tuple[int, int]:
    start = time.perf_counter()
    try:
        with open(log, "w") as f:
            proc = subprocess.run(cmd, cwd=repo, shell=True, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
        status = proc.returncode
    except subprocess.TimeoutExpired:
        status = 124
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return status, elapsed_ms


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


def _discover_mode(repo: str, path: str, only: set[int] | None, enabled: list[str], mode: str) -> list[Mutant]:
    if not _ensure_supported_source_path(path):
        return []
    if mode == "token":
        return discover(repo, path, only, enabled)
    if mode == "clang":
        try:
            from clang import cindex  # type: ignore
        except ModuleNotFoundError as exc:
            raise ValueError(
                "--mode clang requires the optional 'clang' package and libclang bindings. "
                "Install with `pip install libclang` or use --mode token."
            ) from exc

        compile_entry = _resolve_compile_entry(repo, path)
        tu = cindex.Index.create().parse(
            os.path.join(repo, path),
            args=compile_entry,
            options=cindex.TranslationUnit.PARSE_SKIP_FUNCTION_BODIES,
        )
        errors = [
            d for d in tu.diagnostics
            if int(getattr(d, "severity", 0)) >= getattr(cindex.Diagnostic, "Error", 3)
        ]
        if errors:
            raise ValueError(f"clang parse failed for {path}: {errors[0].spelling}")

        full = os.path.abspath(os.path.join(repo, path))
        token_mutants = discover(repo, path, only, enabled)
        ranges = _collect_clang_cursor_ranges(tu.cursor, full)
        out: list[Mutant] = []
        for mut in token_mutants:
            kinds = _clang_matching_kinds(ranges, mut.line, mut.col, mut.original)
            if not _clang_mutation_is_ast_confirmed(mut.mutator, kinds):
                continue
            mut.nodeKind = _clang_primary_node_kind(mut.mutator, kinds)
            out.append(mut)
        return _apply_stryker_ignore_comments(repo, path, out)
    return discover(repo, path, only, enabled)


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
        "ignored": rep.ignored,
        "mutants": rep.mutants,
        "score": rep.scorePercent,
    }


def _report_dict(rep: Report, repo: str | None = None, base: str | None = None,
                 threshold: float | None = None, startedAt: str | None = None) -> dict:
    normalized_mutants = [_normalize_mutant_record(mut) for mut in rep.mutants]
    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "tool": rep.tool,
        "repo": repo or rep.repo,
        "base": base or rep.base,
        "startedAt": startedAt or rep.startedAt,
        "completedAt": rep.completedAt,
        "threshold": threshold if threshold is not None else rep.threshold,
        "totalMutants": rep.total,
        "killed": rep.killed,
        "survived": rep.survived,
        "buildErrors": rep.buildError,
        "timeouts": rep.timeouts,
        "ignored": rep.ignored,
        "score": rep.score,
        "execution": {
            "mode": rep.execution.get("mode", "token"),
            "worktreeMode": rep.execution.get("worktreeMode", "inplace"),
            "jobs": rep.execution.get("jobs", 1),
        },
        "commands": {
            "build": rep.buildCommand,
            "test": rep.testCommand,
        },
        "mutationTestingElements": _mutation_testing_elements(rep),
        "mutants": normalized_mutants,
        "targetFiles": rep.target_files,
        # Legacy compatibility fields for transition period.
        "scorePercent": rep.scorePercent,
        "target_files": rep.target_files,
        "build_error": rep.buildError,
        "total": rep.total,
        "ignored_count": rep.ignored,
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
        files[file]["mutants"].append({
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
        })

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
    rows = [
        "<tr><th>File</th><th>Line</th><th>Mutator</th><th>Original</th><th>Mutated</th><th>Status</th><th>DurationMs</th></tr>"
    ]
    for mut in rep.mutants:
        rows.append(
            "<tr>"
            f"<td>{mut['file']}</td><td>{mut['line']}</td><td>{mut['mutator']}</td>"
            f"<td>{mut['original']}</td><td>{mut['mutated']}</td><td>{mut['status']}</td>"
            f"<td>{mut.get('durationMs', 0)}</td>"
            "</tr>"
        )
    return (
        "<!doctype html><html><body>"
        f"<h1>stryker-cxx report</h1><p>score={rep.score:.2f} "
        f"killed={rep.killed} survived={rep.survived} "
        f"build_errors={rep.buildError} timeouts={rep.timeouts} ignored={rep.ignored}</p>"
        f"<table>{''.join(rows)}</table></body></html>"
    )


def _format_sarif(rep: Report) -> dict:
    results = []
    for mut in rep.mutants:
        status = mut.get("status", "PENDING")
        level = {
            "KILLED": "none",
            "SURVIVED": "warning",
            "BUILD_ERROR": "warning",
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


def _write_human_artifact(path: str, report: str, payload: Any) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    out_path = path
    ext = os.path.splitext(path)[1].lower()
    if ext not in {".md", ".html", ".sarif"}:
        if report == "markdown":
            out_path = path + ".md"
        elif report == "html":
            out_path = path + ".html"
        elif report == "sarif":
            out_path = path + ".sarif"
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
        else:
            json.dump(payload, f, indent=2)
    return out_path


def _run_mutant_once(
    mut: Mutant,
    repo: str,
    build_cmd: str,
    test_cmd: str,
    timeout_seconds: int | None,
    worktree_mode: str,
    artifact_root: str,
    execution_mode: str,
) -> Mutant:
    mut.run = {
        "mode": execution_mode,
        "worktreeMode": worktree_mode,
    }
    start_ms = time.perf_counter()
    with _workspace(repo, worktree_mode) as work_repo:
        original = apply_mutant(work_repo, mut)
        build_log = os.path.join(artifact_root, f"build_{_safe_basename(mut.id)}.log")
        test_log = os.path.join(artifact_root, f"test_{_safe_basename(mut.id)}.log")
        mut.buildLog = build_log
        mut.testLog = test_log

        try:
            build_rc, build_ms = run_cmd(build_cmd, work_repo, build_log, timeout_seconds)
            mut.run["buildReturnCode"] = build_rc
            mut.run["buildMs"] = build_ms
            mut.durationMs += build_ms
            if build_rc == 124:
                mut.status = "TIMEOUT"
                mut.detail = "build timed out"
            elif build_rc != 0:
                mut.status = "BUILD_ERROR"
                mut.detail = "did not compile"
            else:
                test_rc, test_ms = run_cmd(test_cmd, work_repo, test_log, timeout_seconds)
                mut.run["testReturnCode"] = test_rc
                mut.run["testMs"] = test_ms
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
            restore(work_repo, mut.file, mut.line, original)
            mut.durationMs = int((time.perf_counter() - start_ms) * 1000)
    return mut


def _run_mutant_task(payload: tuple[Mutant, str, str, str, int | None, str, str, str]) -> Mutant:
    (mut, repo, build_cmd, test_cmd, timeout_seconds, worktree_mode, artifact_root, execution_mode) = payload
    return _run_mutant_once(
        mut=mut,
        repo=repo,
        build_cmd=build_cmd,
        test_cmd=test_cmd,
        timeout_seconds=timeout_seconds,
        worktree_mode=worktree_mode,
        artifact_root=artifact_root,
        execution_mode=execution_mode,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stryker-cxx")
    ap.add_argument("--repo-dir", dest="repo", required=True)
    ap.add_argument("--files", required=True)
    ap.add_argument("--diff-base", default=None, dest="diff_base")
    ap.add_argument("--lines", default=None)
    ap.add_argument("--build-cmd", required=True, dest="build_cmd")
    ap.add_argument("--test-cmd", required=True, dest="test_cmd")
    ap.add_argument("--report", required=True)
    ap.add_argument("--max-mutants", type=int, default=0)
    ap.add_argument("--include-metal", action="store_true", dest="include_metal")
    ap.add_argument("--mutators", default=",".join(DEFAULT_MUTATORS))
    ap.add_argument("--timeout", type=int, default=None, dest="timeout_seconds",
                    help="Per-mutant timeout in seconds")
    ap.add_argument("--mode", default="token", choices=["token", "clang"])
    ap.add_argument("--jobs", type=int, default=1, help="Parallel mutation workers")
    ap.add_argument("--shard-index", type=int, default=None)
    ap.add_argument("--shard-total", type=int, default=None, help="Split work into N shards")
    ap.add_argument("--worktree-mode", dest="worktree_mode", choices=["inplace", "git-worktree", "copy"], default="inplace")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--output-format", default="legacy", choices=["legacy", "stryker-cxx"],
                    dest="output_format",
                    help="Compatibility report format; legacy keeps old engine fields")
    ap.add_argument("--format", default="json", choices=["json", "markdown", "html", "sarif", "mutation-testing-elements"],
                    help="Report artifact format")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--fail-on-empty", action="store_true", dest="fail_on_empty")
    ap.add_argument("--artifact-dir", default=None)
    ap.add_argument("--resume", default=None, dest="resume")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--run-mutant-id", default=None)
    args = ap.parse_args(argv)

    if args.jobs < 1:
        ap.error("--jobs must be >= 1")
    if args.shard_total is not None and args.shard_total < 1:
        ap.error("--shard-total must be >= 1")
    if args.shard_index is not None and args.shard_total is None:
        ap.error("--shard-index requires --shard-total")
    if args.shard_total is not None and args.shard_index is None:
        ap.error("--shard-total requires --shard-index")
    if args.shard_index is not None and args.shard_index > (args.shard_total or 0):
        ap.error("--shard-index must be <= --shard-total")

    if args.format == "json" and args.output_format == "legacy":
        output_mode = "legacy"
    else:
        output_mode = args.output_format

    try:
        enabled = normalize_mutator_list(args.mutators)
    except ValueError as exc:
        ap.error(str(exc))

    repo = _ensure_target_root(args.repo)
    files = [p.strip() for p in args.files.split(",") if p.strip()]

    if args.worktree_mode == "inplace" and not args.allow_dirty:
        dirty = _git_dirty_files(repo, files)
        if dirty:
            raise ValueError(f"refusing to mutate dirty files in inplace mode: {', '.join(dirty)} (use --allow-dirty to override)")

    if args.jobs > 1 and args.worktree_mode == "inplace":
        if not args.quiet:
            print("[error] --jobs > 1 requires --worktree-mode copy or git-worktree to avoid workspace races")
        raise ValueError("cannot run parallel in-place mutation")

    rep = Report(
        target_files=files,
        repo=repo,
        base=args.diff_base,
        threshold=args.threshold,
        timeoutSeconds=args.timeout_seconds,
        buildCommand=args.build_cmd,
        testCommand=args.test_cmd,
        execution={"mode": args.mode, "worktreeMode": args.worktree_mode, "jobs": args.jobs},
    )
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
        discovered.extend(_discover_mode(repo, path, only, enabled, args.mode))

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

    artifact_root = args.artifact_dir or os.path.join(repo, "agent_space", "stryker-cxx")
    os.makedirs(artifact_root, exist_ok=True)

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
            elif status == "TIMEOUT":
                rep.timeouts += 1
            elif status == "IGNORED":
                rep.ignored += 1
            continue
        if m.status == "IGNORED":
            rep.ignored += 1
            rep.mutants.append(_normalize_mutant_record(asdict(m)))
            continue
        pending.append(m)

    rep.total = len(discovered)

    try:
        if pending:
            if args.jobs > 1:
                if not args.quiet:
                    print(f"[stryker-cxx] running {len(pending)} mutants with {args.jobs} workers")
                payloads = [
                    (
                        mut,
                        repo,
                        args.build_cmd,
                        args.test_cmd,
                        args.timeout_seconds,
                        args.worktree_mode,
                        artifact_root,
                        args.mode,
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
                        elif executed.status == "TIMEOUT":
                            rep.timeouts += 1

                        if not args.quiet:
                            tag = (
                                f"{executed.file.split('/')[-1]}:{executed.line} "
                                f"{executed.original}->{executed.mutated} [{executed.mutator}]"
                            )
                            print(f"[{idx}/{len(pending)}] {tag} ... {executed.status} ({executed.durationMs}ms)")
                        executed.run["reproCommand"] = mutation_repro_command(executed, repo, args.build_cmd, args.test_cmd, args.report)
                        rep.mutants.append(_normalize_mutant_record(asdict(executed)))
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
                        test_cmd=args.test_cmd,
                        timeout_seconds=args.timeout_seconds,
                        worktree_mode=args.worktree_mode,
                        artifact_root=artifact_root,
                        execution_mode=args.mode,
                    )
                    if not args.quiet:
                        print(f"{executed.status} ({executed.durationMs}ms)")
                    if executed.status == "KILLED":
                        rep.killed += 1
                    elif executed.status == "SURVIVED":
                        rep.survived += 1
                    elif executed.status == "BUILD_ERROR":
                        rep.buildError += 1
                    elif executed.status == "TIMEOUT":
                        rep.timeouts += 1
                    executed.run["reproCommand"] = mutation_repro_command(executed, repo, args.build_cmd, args.test_cmd, args.report)
                    rep.mutants.append(_normalize_mutant_record(asdict(executed)))
                    _write_report(args.report, rep, output_mode=output_mode)
    finally:
        if args.worktree_mode == "inplace":
            for path in files:
                if _is_tracked_file(repo, path):
                    subprocess.run(["git", "-C", repo, "checkout", "--", path], check=False)

    rep.finalize()
    _write_report(args.report, rep, output_mode=output_mode)

    if args.format == "markdown":
        _write_human_artifact(args.report, "markdown", _format_markdown(rep))
    elif args.format == "html":
        _write_human_artifact(args.report, "html", _format_html(rep))
    elif args.format == "sarif":
        _write_human_artifact(args.report, "sarif", _format_sarif(rep))
    elif args.format == "mutation-testing-elements":
        _write_human_artifact(args.report, "json", _mutation_testing_elements(rep))

    if not args.quiet:
        print(
            f"[stryker-cxx] score={rep.score:.2f} killed={rep.killed} "
            f"survived={rep.survived} build_error={rep.buildError} "
            f"timeouts={rep.timeouts} ignored={rep.ignored}",
        )
    for m in rep.mutants:
        if m["status"] == "SURVIVED":
            print(f"  SURVIVOR {m['file']}:{m['line']} {m['original']}->{m['mutated']} ({m['mutator']})")

    if rep.total == 0:
        if args.fail_on_empty:
            return 3
        return 0

    effective_threshold = 1.0 if args.threshold is None else args.threshold
    if rep.score < effective_threshold:
        return 2

    return 0


@contextlib.contextmanager
def _workspace(repo: str, mode: str):
    if mode == "inplace":
        yield repo
        return

    if mode == "git-worktree":
        if not os.path.isdir(os.path.join(repo, ".git")):
            raise ValueError("git-worktree mode requires --repo to point at a git work tree")

        workspace_root = tempfile.mkdtemp(prefix="stryker-cxx-worktree-")
        workdir = os.path.join(workspace_root, "worktree")
        try:
            subprocess.run(
                ["git", "-C", repo, "worktree", "add", "--detach", workdir, "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            )
            yield workdir
            subprocess.run(
                ["git", "-C", repo, "worktree", "remove", "--force", workdir],
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            shutil.rmtree(workspace_root, ignore_errors=True)
        return

    if mode == "copy":
        workdir = tempfile.mkdtemp(prefix="stryker-cxx-copy-")
        try:
            shutil.copytree(repo, workdir, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
            yield workdir
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        return

    raise ValueError(f"unsupported worktree mode: {mode}")


if __name__ == "__main__":
    sys.exit(main())
