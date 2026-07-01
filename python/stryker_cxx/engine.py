#!/usr/bin/env python3
"""marmorkrebs-cxx: a source-level C++/ObjC++ mutation tester.

This module is the execution engine used by the standalone `stryker-cxx` package.
The behavior intentionally mirrors the original embedded Marmorkrebs script with
added report modes and run-time metadata to support Stryker-level workflows.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import copy
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
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .schema import (
    REPORT_SCHEMA_VERSION,
    MTE_SCHEMA_VERSION,
    TOOL_VERSION,
    require_mte,
    require_report,
)
from .payload_contract import native_to_mte_status
from .project_analysis import analyze_project
from .scheduler import (
    batch_scheduler_record,
    build_test_scheduler_metadata,
    per_mutant_scheduler_record,
)
from .mutation_artifacts import (
    artifact_placement_policy,
    compiled_artifact_placement_policy,
    compiled_mutation_artifact_metadata,
    materialize_mutation_artifact,
    mutation_artifact_metadata,
    mutant_switch_artifact_metadata,
    mutant_switch_guard_id,
)

# Token-level mutators.
MUTATORS: dict[str, list[tuple[str, str]]] = {
    "ConditionalBoundary": [("<=", "<"), (">=", ">"), ("<", "<="), (">", ">=")],
    "ConditionalExpression": [],
    "EqualityOperator": [("==", "!="), ("!=", "==")],
    "LogicalOperator": [("&&", "||"), ("||", "&&")],
    "ShiftOperator": [("<<=", ">>="), (">>=", "<<="), ("<<", ">>"), (">>", "<<")],
    "BooleanLiteral": [("true", "false"), ("false", "true")],
    "ObjCBoolLiteral": [("YES", "NO"), ("NO", "YES")],
    "UpdateOperator": [("++", "--"), ("--", "++")],
    "ArithmeticOperator": [("+", "-"), ("-", "+"), ("*", "/"), ("/", "*"), ("%", "*")],
    "AssignmentOperator": [
        ("+=", "-="),
        ("-=", "+="),
        ("*=", "/="),
        ("/=", "*="),
        ("%=", "*="),
        ("&=", "|="),
        ("|=", "&="),
        ("^=", "|="),
    ],
    "BitwiseOperator": [("&", "|"), ("|", "&"), ("^", "|"), ("^", "&")],
    "UnaryOperator": [("!", ""), ("!", "!!")],
    "ReturnValue": [("return true", "return false"), ("return false", "return true")],
    "IntegerLiteral": [("0", "1"), ("1", "0")],
    "NullLiteral": [("nullptr", "NULL"), ("NULL", "nullptr")],
    "CharacterLiteral": [],
    "FloatingPointLiteral": [],
    "StringLiteral": [],
    "StatementRemoval": [],
    "BlockRemoval": [],
    "CallRemoval": [],
    "LoopBoundary": [("<=", "<"), (">=", ">"), ("<", "<="), (">", ">=")],
    "LoopCondition": [],
    "StandardLibraryCall": [
        ("std::min", "std::max"),
        ("std::max", "std::min"),
        ("std::all_of", "std::any_of"),
        ("std::any_of", "std::all_of"),
        ("std::none_of", "std::any_of"),
        ("std::equal", "std::mismatch"),
        ("std::mismatch", "std::equal"),
        ("std::lower_bound", "std::upper_bound"),
        ("std::upper_bound", "std::lower_bound"),
        ("std::begin", "std::end"),
        ("std::end", "std::begin"),
        ("std::cbegin", "std::cend"),
        ("std::cend", "std::cbegin"),
        ("std::sort", "std::stable_sort"),
        ("std::stable_sort", "std::sort"),
        ("std::partition", "std::stable_partition"),
        ("std::stable_partition", "std::partition"),
        ("std::is_sorted", "std::is_heap"),
        ("std::is_heap", "std::is_sorted"),
    ],
    "MoveSemantics": [],
    "ContainerCall": [],
    "ContainerStateCall": [],
    "StringCall": [],
    "MathCall": [],
    "IteratorCall": [],
    "ChronoCall": [],
    "RegexCall": [],
    "FilesystemCall": [],
    "MemoryOrder": [
        ("std::memory_order_relaxed", "std::memory_order_seq_cst"),
        ("std::memory_order_seq_cst", "std::memory_order_relaxed"),
        ("std::memory_order_acquire", "std::memory_order_relaxed"),
        ("std::memory_order_release", "std::memory_order_relaxed"),
        ("std::memory_order_acq_rel", "std::memory_order_seq_cst"),
        ("std::memory_order_consume", "std::memory_order_acquire"),
        ("std::memory_order::relaxed", "std::memory_order::seq_cst"),
        ("std::memory_order::seq_cst", "std::memory_order::relaxed"),
        ("std::memory_order::acquire", "std::memory_order::relaxed"),
        ("std::memory_order::release", "std::memory_order::relaxed"),
        ("std::memory_order::acq_rel", "std::memory_order::seq_cst"),
        ("std::memory_order::consume", "std::memory_order::acquire"),
    ],
    "MemberAccessOperator": [],
    "ExceptionHandling": [],
    "PreprocessorGuard": [],
    "ObjCMessageSend": [],
    "MetalThreadPosition": [
        ("thread_position_in_grid", "thread_position_in_threadgroup"),
        ("thread_position_in_threadgroup", "thread_position_in_grid"),
        ("thread_index_in_threadgroup", "threads_per_threadgroup"),
        ("threads_per_threadgroup", "thread_index_in_threadgroup"),
    ],
    "MetalAddressSpace": [],
}

MUTATOR_DESCRIPTIONS: dict[str, str] = {
    "ConditionalBoundary": "replaced conditional boundary operator",
    "ConditionalExpression": "replaced ternary branch expressions",
    "EqualityOperator": "replaced equality operator",
    "LogicalOperator": "replaced boolean short-circuit operator",
    "ShiftOperator": "replaced bit-shift operator",
    "UpdateOperator": "replaced increment/decrement operator",
    "BooleanLiteral": "swapped boolean literal",
    "ObjCBoolLiteral": "swapped Objective-C boolean literal",
    "ArithmeticOperator": "replaced arithmetic operator",
    "AssignmentOperator": "replaced compound assignment operator",
    "BitwiseOperator": "replaced bitwise operator",
    "UnaryOperator": "modified unary operator",
    "ReturnValue": "reversed returned boolean result",
    "IntegerLiteral": "changed a basic integer literal",
    "NullLiteral": "changed a null pointer literal",
    "CharacterLiteral": "replaced a character literal",
    "FloatingPointLiteral": "replaced a floating-point literal",
    "StringLiteral": "replaced a string literal",
    "StatementRemoval": "removed a statement and preserved control flow shape",
    "BlockRemoval": "removed a single-line compound statement",
    "CallRemoval": "removed a statement-level function call",
    "LoopBoundary": "replaced loop boundary operator",
    "LoopCondition": "replaced loop condition",
    "StandardLibraryCall": "replaced a standard-library call target",
    "MoveSemantics": "removed a std::move/std::forward value-category wrapper",
    "ContainerCall": "replaced a no-argument C++ container member call",
    "ContainerStateCall": "replaced a C++ container state/capacity member call",
    "StringCall": "replaced a C++ string/search member call",
    "MathCall": "replaced a C/C++ math call target",
    "IteratorCall": "replaced a C++ iterator movement call target",
    "ChronoCall": "replaced a C++ chrono rounding call target",
    "RegexCall": "replaced a C++ regex predicate call target",
    "FilesystemCall": "replaced a C++ filesystem predicate call target",
    "MemoryOrder": "replaced a C++ atomic memory-order constant",
    "MemberAccessOperator": "replaced a member-access operator",
    "ExceptionHandling": "removed a throw statement",
    "PreprocessorGuard": "replaced a simple preprocessor guard",
    "ObjCMessageSend": "removed a statement-level Objective-C message send",
    "MetalThreadPosition": "replaced a Metal thread-position attribute",
    "MetalAddressSpace": "replaced a Metal address-space qualifier",
}

_TOKEN_PATTERNS: dict[str, str] = {
    "<=": r"<=",
    ">=": r">=",
    "<<=": r"<<=",
    ">>=": r">>=",
    "++": r"(?<![+\-])\+\+(?![+=])",
    "--": r"(?<![-+])--(?![=-])",
    "<<": r"<<(?![=<])",
    ">>": r">>(?![=>])",
    "==": r"==",
    "!=": r"!=",
    "&&": r"&&",
    "||": r"\|\|",
    # Bare `<`/`>` require surrounding whitespace so we avoid touching templates.
    "<": r"(?<=\s)<(?=\s)",
    ">": r"(?<=\s)>(?=\s)",
    "true": r"\btrue\b",
    "false": r"\bfalse\b",
    "YES": r"\bYES\b",
    "NO": r"\bNO\b",
    "+": r"(?<![+])\+(?![+=])",
    "-": r"(?<![-])-(?![->=])",
    "*": r"(?<![*/])\*(?![*/=])",
    "/": r"(?<!/)/(?![=/])",
    "%": r"%(?!=)",
    "+=": r"\+=",
    "-=": r"-=",
    "*=": r"\*=",
    "/=": r"/=",
    "%=": r"%=",
    "&=": r"&=",
    "|=": r"\|=",
    "^=": r"\^=",
    "&": r"(?<![&|])&(?!(?:[&=]))",
    "|": r"(?<!\|)\|(?!(?:\||=))",
    "^": r"\^(?!=)",
    "!": r"(?<![!])!(?![=])",
    "return true": r"\breturn\s+true\b",
    "return false": r"\breturn\s+false\b",
    "0": r"(?<![\w.])0(?![\w.])",
    "1": r"(?<![\w.])1(?![\w.])",
    "nullptr": r"\bnullptr\b",
    "NULL": r"\bNULL\b",
    "STRING_LITERAL": r'"(?:[^"\\\\]|\\\\.)*"',
    "std::min": r"\bstd::min\b",
    "std::max": r"\bstd::max\b",
    "std::all_of": r"\bstd::all_of\b",
    "std::any_of": r"\bstd::any_of\b",
    "std::none_of": r"\bstd::none_of\b",
    "std::equal": r"\bstd::equal\b",
    "std::mismatch": r"\bstd::mismatch\b",
    "std::lower_bound": r"\bstd::lower_bound\b",
    "std::upper_bound": r"\bstd::upper_bound\b",
    "std::sort": r"\bstd::sort\b",
    "std::stable_sort": r"\bstd::stable_sort\b",
    "std::partition": r"\bstd::partition\b",
    "std::stable_partition": r"\bstd::stable_partition\b",
    "std::is_sorted": r"\bstd::is_sorted\b",
    "std::is_heap": r"\bstd::is_heap\b",
    "std::begin": r"\bstd::begin\b",
    "std::end": r"\bstd::end\b",
    "std::cbegin": r"\bstd::cbegin\b",
    "std::cend": r"\bstd::cend\b",
    "std::memory_order_relaxed": r"\bstd::memory_order_relaxed\b",
    "std::memory_order_seq_cst": r"\bstd::memory_order_seq_cst\b",
    "std::memory_order_acquire": r"\bstd::memory_order_acquire\b",
    "std::memory_order_release": r"\bstd::memory_order_release\b",
    "std::memory_order_acq_rel": r"\bstd::memory_order_acq_rel\b",
    "std::memory_order_consume": r"\bstd::memory_order_consume\b",
    "std::memory_order::relaxed": r"\bstd::memory_order::relaxed\b",
    "std::memory_order::seq_cst": r"\bstd::memory_order::seq_cst\b",
    "std::memory_order::acquire": r"\bstd::memory_order::acquire\b",
    "std::memory_order::release": r"\bstd::memory_order::release\b",
    "std::memory_order::acq_rel": r"\bstd::memory_order::acq_rel\b",
    "std::memory_order::consume": r"\bstd::memory_order::consume\b",
    "thread_position_in_grid": r"\bthread_position_in_grid\b",
    "thread_position_in_threadgroup": r"\bthread_position_in_threadgroup\b",
    "thread_index_in_threadgroup": r"\bthread_index_in_threadgroup\b",
    "threads_per_threadgroup": r"\bthreads_per_threadgroup\b",
}
_TOKEN_BINARY_OPERAND_RE = re.compile(
    r"(?:[A-Za-z_][A-Za-z0-9_:]*|\d+(?:\.\d+)?|true|false|nullptr|NULL)"
)
_TOKEN_BINARY_EXPRESSION_PREFIXES = ("return", "case")


def _skip_left_space(text: str, index: int) -> int:
    while index >= 0 and text[index].isspace():
        index -= 1
    return index


def _skip_right_space(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _matching_left_delimiter(text: str, close_index: int) -> int | None:
    pairs = {")": "(", "]": "[", "}": "{"}
    close_char = text[close_index]
    open_char = pairs.get(close_char)
    if open_char is None:
        return None
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(close_index, -1, -1):
        char = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == close_char:
            depth += 1
        elif char == open_char:
            depth -= 1
            if depth == 0:
                return index
    return None


def _matching_right_delimiter(text: str, open_index: int) -> int | None:
    pairs = {"(": ")", "[": "]", "{": "}"}
    open_char = text[open_index]
    close_char = pairs.get(open_char)
    if close_char is None:
        return None
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(open_index, len(text)):
        char = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return index
    return None


def _identifier_start_left(text: str, end_exclusive: int) -> int:
    index = end_exclusive - 1
    while index >= 0 and re.match(r"[A-Za-z0-9_:~.]", text[index]):
        index -= 1
    return index + 1


def _token_binary_left_span_start(code: str, operator_start: int) -> int | None:
    index = _skip_left_space(code, operator_start - 1)
    if index < 0:
        return None
    expression_prefix_end: int | None = None
    if code[index] in ")]}":
        open_index = _matching_left_delimiter(code, index)
        if open_index is None:
            return None
        start = open_index
        prefix_end = _skip_left_space(code, open_index - 1)
        if prefix_end >= 0 and re.match(r"[A-Za-z0-9_:~]", code[prefix_end]):
            start = _identifier_start_left(code, prefix_end + 1)
            token = code[start : prefix_end + 1]
            if token in _TOKEN_BINARY_EXPRESSION_PREFIXES:
                expression_prefix_end = prefix_end + 1
            if code[start] == "." or code[:start].rstrip().endswith("->"):
                return None
    else:
        start = _identifier_start_left(code, index + 1)
        token = code[start : index + 1]
        if token in _TOKEN_BINARY_EXPRESSION_PREFIXES:
            expression_prefix_end = index + 1
    if expression_prefix_end is not None:
        start = _skip_right_space(code, expression_prefix_end)
    prefix = code[:start].rstrip()
    for keyword in _TOKEN_BINARY_EXPRESSION_PREFIXES:
        if prefix.endswith(keyword):
            before = prefix[: -len(keyword)]
            if not before or not re.match(r"[A-Za-z0-9_]", before[-1]):
                start = _skip_right_space(code, len(prefix))
                break
    return start


def _token_binary_right_span_end(code: str, operator_end: int) -> int | None:
    start = _skip_right_space(code, operator_end)
    if start >= len(code):
        return None
    if code[start] in "([{":
        close_index = _matching_right_delimiter(code, start)
        if close_index is None:
            return None
        end = close_index + 1
    else:
        end = start
        while end < len(code) and re.match(r"[A-Za-z0-9_:~.]", code[end]):
            end += 1
        if end == start:
            return None
        call_start = _skip_right_space(code, end)
        if call_start < len(code) and code[call_start] == "(":
            close_index = _matching_right_delimiter(code, call_start)
            if close_index is None:
                return None
            end = close_index + 1
    return end


def _token_binary_expression_source_range(
    code: str,
    line_no: int,
    operator_start: int,
    operator_end: int,
) -> dict[str, int | str] | None:
    balanced_start = _token_binary_left_span_start(code, operator_start)
    balanced_end = _token_binary_right_span_end(code, operator_end)
    if balanced_start is not None and balanced_end is not None:
        if balanced_start < operator_start and balanced_end > operator_end:
            return {
                "kind": "TOKEN_BINARY_EXPRESSION",
                "startLine": line_no,
                "startColumn": balanced_start + 1,
                "endLine": line_no,
                "endColumn": balanced_end + 1,
            }
    left_part = code[:operator_start].rstrip()
    right_padding = len(code[operator_end:]) - len(code[operator_end:].lstrip())
    right_part = code[operator_end + right_padding :]
    left_matches = list(_TOKEN_BINARY_OPERAND_RE.finditer(left_part))
    left_match = left_matches[-1] if left_matches else None
    right_match = _TOKEN_BINARY_OPERAND_RE.match(right_part)
    if left_match is None or right_match is None:
        return None
    if left_match.end() != len(left_part):
        return None
    start_col0 = left_match.start()
    end_col0 = operator_end + right_padding + right_match.end()
    if start_col0 >= operator_start or end_col0 <= operator_end:
        return None
    return {
        "kind": "TOKEN_BINARY_EXPRESSION",
        "startLine": line_no,
        "startColumn": start_col0 + 1,
        "endLine": line_no,
        "endColumn": end_col0 + 1,
    }

_STRING_LITERAL_RE = re.compile(r'"(?:[^"\\]|\\.)*"')
_CHARACTER_LITERAL_RE = re.compile(r"(?:L|u8|u|U)?'(?:[^'\\]|\\.)*'")
_FLOATING_LITERAL_RE = re.compile(
    r"(?<![\w.])(?:[0-9]+\.[0-9]*|\.[0-9]+|[0-9]+\.[0-9]+[eE][+-]?[0-9]+|[0-9]+[eE][+-]?[0-9]+)(?:[fFlL])?(?![\w.])"
)
_INTEGER_LITERAL_RE = re.compile(r"(?<![\w.])(?:0|1)(?![\w.])")
_NULL_LITERAL_RE = re.compile(r"\b(?:nullptr|NULL)\b")

_CALL_REMOVAL_RE = re.compile(
    r"\b(?!if\b|for\b|while\b|switch\b|return\b|sizeof\b|catch\b)"
    r"([A-Za-z_]\w*(?:(?:\s*::\s*|\s*->\s*|\s*\.\s*)[A-Za-z_]\w*)*\s*\([^;{}]*\))(?=\s*;)"
)
_MEMBER_ACCESS_RE = re.compile(
    r"\b[A-Za-z_]\w*(?:\s*(?:\)|\]))?\s*(?P<op>->\*|->|\.\*|\.)(?=\s*[A-Za-z_]\w*)"
)
_THROW_STATEMENT_RE = re.compile(r"\bthrow\b[^;]*;")
_OBJC_MESSAGE_SEND_RE = re.compile(r"^\s*(\[[^;{}]+\])\s*;")
_METAL_ADDRESS_SPACE_RE = re.compile(r"\b(device|constant|threadgroup)\b(?=\s+)")

_BLOCK_REMOVAL_RE = re.compile(r"^\s*\{[^{}]*\}\s*$")
_STATEMENT_REMOVAL_FORBIDDEN_PREFIXES = (
    "if",
    "for",
    "while",
    "switch",
    "do",
    "return",
    "case",
    "default",
    "goto",
    "break",
    "continue",
    "catch",
    "try",
    "throw",
    "asm",
    "constexpr",
    "class",
    "struct",
    "enum",
    "namespace",
    "template",
    "typename",
    "operator",
    "public",
    "private",
    "protected",
)
_STATEMENT_REMOVAL_DECLARATION_PREFIXES = {
    "alignas",
    "auto",
    "bool",
    "char",
    "char16_t",
    "char32_t",
    "const",
    "consteval",
    "constinit",
    "double",
    "extern",
    "float",
    "friend",
    "inline",
    "int",
    "long",
    "mutable",
    "register",
    "short",
    "signed",
    "static",
    "thread_local",
    "typedef",
    "unsigned",
    "using",
    "virtual",
    "void",
    "volatile",
}

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
    ".metal",
}
DEFAULT_MUTATORS = ["ConditionalBoundary", "EqualityOperator", "LogicalOperator", "BooleanLiteral"]
EQUIVALENT_SUPPRESSION_MODES = {"off", "conservative", "aggressive"}
GENERATED_CODE_MARKERS = (
    "auto-generated",
    "autogenerated",
    "automatically generated",
    "generated by",
    "do not edit",
    "do not modify",
)
GENERATED_PATH_PATTERNS = (
    "/generated/",
    "/gen/",
    ".pb.",
    ".grpc.",
    "moc_",
    "ui_",
    "qrc_",
)
EQUIVALENT_SUPPRESSION_RULE_IDS = {
    "generated code auto-suppression": "generated-code",
    "equivalent duplicate logical operand": "duplicate-logical-operand",
    "equivalent arithmetic identity": "arithmetic-identity",
    "equivalent duplicate bitwise operand": "duplicate-bitwise-operand",
    "equivalent duplicate standard-library operands": "duplicate-standard-library-operands",
    "equivalent duplicate standard-library range": "duplicate-standard-library-range",
    "equivalent duplicate conditional branches": "duplicate-conditional-branches",
    "style-equivalent null literal suppression": "style-equivalent-null-literal",
}

PLUGIN_MANIFEST = "stryker-cxx-plugin.json"
SUPPORTED_PLUGIN_CAPABILITY_VERSIONS = {"1", "1.0", "v1"}
PLUGIN_LIFECYCLE_SCHEMA_VERSION = "stryker-cxx.plugin-lifecycle.v1"
PLUGIN_LIFECYCLE_EVENTS = (
    "initialization",
    "projectAnalysis",
    "mutationDiscovery",
    "artifactCreation",
    "coverageAnalysis",
    "scheduling",
    "execution",
    "reporting",
    "cleanup",
)
PLUGIN_LIFECYCLE_ALIASES = {
    "initialization": ("preRun",),
    "cleanup": ("postRun",),
}
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


def _plugin_capability_versions(name: str, payload: dict[str, Any]) -> dict[str, str]:
    capabilities = payload.get("capabilities", {})
    if capabilities in (None, False):
        return {}
    if not isinstance(capabilities, dict):
        raise ValueError(f"plugin {name}: capabilities must be an object")
    default_version = str(
        payload.get("capabilityVersion")
        or payload.get("capabilitiesVersion")
        or payload.get("apiVersion")
        or "1"
    )
    versions: dict[str, str] = {}
    for capability_name in sorted(capabilities):
        capability = capabilities[capability_name]
        version = default_version
        if isinstance(capability, dict):
            version = str(
                capability.get("capabilityVersion")
                or capability.get("version")
                or capability.get("apiVersion")
                or default_version
            )
        elif not isinstance(capability, bool):
            raise ValueError(f"plugin {name}: capability {capability_name} must be an object or boolean")
        if version not in SUPPORTED_PLUGIN_CAPABILITY_VERSIONS:
            raise ValueError(
                f"plugin {name}: unsupported capability version for {capability_name}: {version}"
            )
        versions[str(capability_name)] = version
    return versions


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
COMPILE_PRUNED_STATUSES = {"BUILD_ERROR", "CHECK_ERROR"}
ARTIFACT_BACKENDS = {"source-overlay", "compiled-executable", "compiled-library", "compiled-object"}
EXECUTION_MODES = {"source-overlay", "mutant-switch"}
EXECUTION_BACKENDS = {"auto", "source-overlay", "mutant-switch", "compiled-artifact", "llvm-switch"}
MUTANT_SWITCH_ACTIVE_ENV = "STRYKER_CXX_ACTIVE_MUTANT"
MUTANT_SWITCH_EXPRESSION_MUTATORS = {
    "BooleanLiteral",
    "CallRemoval",
    "ChronoCall",
    "ContainerCall",
    "ContainerStateCall",
    "ConditionalExpression",
    "FilesystemCall",
    "IntegerLiteral",
    "FloatingPointLiteral",
    "CharacterLiteral",
    "StringLiteral",
    "IteratorCall",
    "MathCall",
    "MemoryOrder",
    "MoveSemantics",
    "NullLiteral",
    "ObjCBoolLiteral",
    "RegexCall",
    "StandardLibraryCall",
    "StringCall",
}
MUTANT_SWITCH_EXPRESSION_SPAN_MUTATORS = {
    "ArithmeticOperator",
    "AssignmentOperator",
    "BitwiseOperator",
    "ConditionalBoundary",
    "EqualityOperator",
    "LogicalOperator",
    "LoopBoundary",
    "LoopCondition",
    "ShiftOperator",
    "UnaryOperator",
    "UpdateOperator",
}
BATCH_ISOLATED_MUTATORS = {
    "BlockRemoval",
    "CallRemoval",
    "ExceptionHandling",
    "ObjCMessageSend",
    "PreprocessorGuard",
    "StatementRemoval",
}


@dataclass(frozen=True)
class IgnoreDirective:
    action: str
    targets: frozenset[str] | None
    reason: str
    next_line: bool = False


IGNORE_MUTATOR_ALIASES: dict[str, str] = {
    "boolean": "BooleanLiteral",
    "booleanliteral": "BooleanLiteral",
    "objcbool": "ObjCBoolLiteral",
    "objcboolean": "ObjCBoolLiteral",
    "objcboolliteral": "ObjCBoolLiteral",
    "conditional": "ConditionalExpression",
    "conditionalexpression": "ConditionalExpression",
    "statement": "StatementRemoval",
    "statementremoval": "StatementRemoval",
    "block": "BlockRemoval",
    "blockremoval": "BlockRemoval",
    "equality": "EqualityOperator",
    "equalityoperator": "EqualityOperator",
    "logical": "LogicalOperator",
    "logicaloperator": "LogicalOperator",
    "shift": "ShiftOperator",
    "shiftoperator": "ShiftOperator",
    "update": "UpdateOperator",
    "updateoperator": "UpdateOperator",
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
    "char": "CharacterLiteral",
    "character": "CharacterLiteral",
    "characterliteral": "CharacterLiteral",
    "float": "FloatingPointLiteral",
    "floating": "FloatingPointLiteral",
    "floatingliteral": "FloatingPointLiteral",
    "floatingpointliteral": "FloatingPointLiteral",
    "string": "StringLiteral",
    "stringliteral": "StringLiteral",
    "loopboundary": "LoopBoundary",
    "loopcondition": "LoopCondition",
    "stdlib": "StandardLibraryCall",
    "stdlibcall": "StandardLibraryCall",
    "standardlibrary": "StandardLibraryCall",
    "standardlibrarycall": "StandardLibraryCall",
    "move": "MoveSemantics",
    "stdmove": "MoveSemantics",
    "forward": "MoveSemantics",
    "stdforward": "MoveSemantics",
    "movesemantics": "MoveSemantics",
    "move-semantics": "MoveSemantics",
    "container": "ContainerCall",
    "containercall": "ContainerCall",
    "container-call": "ContainerCall",
    "iterator": "ContainerCall",
    "iteratorboundary": "ContainerCall",
    "containerstate": "ContainerStateCall",
    "container-state": "ContainerStateCall",
    "containerstatecall": "ContainerStateCall",
    "container-state-call": "ContainerStateCall",
    "capacity": "ContainerStateCall",
    "empty": "ContainerStateCall",
    "size": "ContainerStateCall",
    "stringcall": "StringCall",
    "string-call": "StringCall",
    "stringsearch": "StringCall",
    "string-search": "StringCall",
    "find": "StringCall",
    "rfind": "StringCall",
    "math": "MathCall",
    "mathcall": "MathCall",
    "math-call": "MathCall",
    "ceil": "MathCall",
    "floor": "MathCall",
    "round": "MathCall",
    "trunc": "MathCall",
    "iteratorcall": "IteratorCall",
    "iterator-call": "IteratorCall",
    "next": "IteratorCall",
    "prev": "IteratorCall",
    "chrono": "ChronoCall",
    "chronocall": "ChronoCall",
    "chrono-call": "ChronoCall",
    "chronofloor": "ChronoCall",
    "chronoceil": "ChronoCall",
    "regex": "RegexCall",
    "regexcall": "RegexCall",
    "regex-call": "RegexCall",
    "regexmatch": "RegexCall",
    "regexsearch": "RegexCall",
    "filesystem": "FilesystemCall",
    "filesystemcall": "FilesystemCall",
    "filesystem-call": "FilesystemCall",
    "fsexists": "FilesystemCall",
    "fsfile": "FilesystemCall",
    "fsdirectory": "FilesystemCall",
    "memoryorder": "MemoryOrder",
    "memory-order": "MemoryOrder",
    "memberaccess": "MemberAccessOperator",
    "memberaccessoperator": "MemberAccessOperator",
    "exception": "ExceptionHandling",
    "exceptionhandling": "ExceptionHandling",
    "preprocessor": "PreprocessorGuard",
    "preprocessorguard": "PreprocessorGuard",
    "objcmessage": "ObjCMessageSend",
    "objcmessagesend": "ObjCMessageSend",
    "metal": "MetalThreadPosition",
    "metalthread": "MetalThreadPosition",
    "metalthreadposition": "MetalThreadPosition",
    "metaladdress": "MetalAddressSpace",
    "metaladdressspace": "MetalAddressSpace",
}

AST_MUTATOR_CURSOR_KINDS: dict[str, set[str]] = {
    "ConditionalBoundary": {"BINARY_OPERATOR"},
    "ConditionalExpression": {"CONDITIONAL_OPERATOR"},
    "EqualityOperator": {"BINARY_OPERATOR"},
    "LogicalOperator": {"BINARY_OPERATOR"},
    "ShiftOperator": {"BINARY_OPERATOR"},
    "UpdateOperator": {"UNARY_OPERATOR"},
    "ArithmeticOperator": {"BINARY_OPERATOR"},
    "AssignmentOperator": {"COMPOUND_ASSIGNMENT_OPERATOR", "BINARY_OPERATOR"},
    "BitwiseOperator": {"BINARY_OPERATOR"},
    "UnaryOperator": {"UNARY_OPERATOR"},
    "BooleanLiteral": {"CXX_BOOL_LITERAL_EXPR", "OBJC_BOOL_LITERAL_EXPR"},
    "ObjCBoolLiteral": {"OBJC_BOOL_LITERAL_EXPR"},
    "ReturnValue": {"RETURN_STMT"},
    "IntegerLiteral": {"INTEGER_LITERAL"},
    "NullLiteral": {"CXX_NULL_PTR_LITERAL_EXPR", "GNU_NULL_EXPR", "DECL_REF_EXPR"},
    "CharacterLiteral": {"CHARACTER_LITERAL", "CXX_CHAR_LITERAL", "OBJC_CHAR_LITERAL"},
    "FloatingPointLiteral": {"FLOATING_LITERAL", "CXX_FLOATING_LITERAL"},
    "StringLiteral": {"STRING_LITERAL", "CXX_STRING_LITERAL", "OBJC_STRING_LITERAL"},
    "CallRemoval": {"CALL_EXPR", "CXX_MEMBER_CALL_EXPR", "OBJC_MESSAGE_EXPR"},
    "StatementRemoval": {"EXPR_STMT", "DECL_STMT", "ASM_STMT"},
    "BlockRemoval": {"COMPOUND_STMT"},
    "LoopBoundary": {"FOR_STMT", "WHILE_STMT", "DO_STMT"},
    "LoopCondition": {"FOR_STMT", "WHILE_STMT", "DO_STMT"},
    "StandardLibraryCall": {"CALL_EXPR", "DECL_REF_EXPR", "NAMESPACE_REF", "OVERLOADED_DECL_REF"},
    "MoveSemantics": {"CALL_EXPR", "DECL_REF_EXPR", "UNEXPOSED_EXPR"},
    "ContainerCall": {"CALL_EXPR", "CXX_MEMBER_CALL_EXPR", "MEMBER_REF_EXPR"},
    "ContainerStateCall": {"CALL_EXPR", "CXX_MEMBER_CALL_EXPR", "MEMBER_REF_EXPR"},
    "StringCall": {"CALL_EXPR", "CXX_MEMBER_CALL_EXPR", "MEMBER_REF_EXPR"},
    "MathCall": {"CALL_EXPR", "DECL_REF_EXPR", "NAMESPACE_REF", "OVERLOADED_DECL_REF", "UNEXPOSED_EXPR"},
    "IteratorCall": {"CALL_EXPR", "DECL_REF_EXPR", "NAMESPACE_REF", "OVERLOADED_DECL_REF", "UNEXPOSED_EXPR"},
    "ChronoCall": {"CALL_EXPR", "DECL_REF_EXPR", "NAMESPACE_REF", "OVERLOADED_DECL_REF", "UNEXPOSED_EXPR"},
    "RegexCall": {"CALL_EXPR", "DECL_REF_EXPR", "NAMESPACE_REF", "OVERLOADED_DECL_REF", "UNEXPOSED_EXPR"},
    "FilesystemCall": {"CALL_EXPR", "DECL_REF_EXPR", "NAMESPACE_REF", "OVERLOADED_DECL_REF", "UNEXPOSED_EXPR"},
    "MemoryOrder": {"DECL_REF_EXPR", "MEMBER_REF_EXPR", "UNEXPOSED_EXPR"},
    "MemberAccessOperator": {"MEMBER_REF_EXPR", "CXX_DEPENDENT_SCOPE_MEMBER_EXPR", "OBJC_PROPERTY_REF_EXPR"},
    "ExceptionHandling": {"CXX_THROW_EXPR"},
    "PreprocessorGuard": set(),
    "ObjCMessageSend": {"OBJC_MESSAGE_EXPR"},
    "MetalThreadPosition": {"PARM_DECL", "VAR_DECL", "UNEXPOSED_ATTR", "ANNOTATE_ATTR"},
    "MetalAddressSpace": {"PARM_DECL", "VAR_DECL", "TYPE_REF", "UNEXPOSED_ATTR", "ANNOTATE_ATTR"},
}
MACRO_CURSOR_KINDS = {"MACRO_INSTANTIATION", "MACRO_EXPANSION"}
PREPROCESSOR_CURSOR_KINDS = MACRO_CURSOR_KINDS | {"MACRO_DEFINITION", "INCLUSION_DIRECTIVE"}


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
        capability_versions = _plugin_capability_versions(name, payload)
        reporter_defs: list[dict[str, Any]] = []
        reporter_metadata: list[dict[str, Any]] = []
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
        for reporter in payload.get("reporters", []):
            if not isinstance(reporter, dict):
                continue
            reporter_name = str(reporter.get("name", ""))
            if not reporter_name:
                continue
            if reporter.get("metadata") is not None:
                if not isinstance(reporter.get("metadata"), dict):
                    raise ValueError(f"plugin {name}: reporter metadata for {reporter_name} must be an object")
                reporter_metadata.append(
                    {
                        "name": reporter_name,
                        "metadata": reporter.get("metadata"),
                    }
                )
            command = str(reporter.get("command", ""))
            if not command:
                continue
            entry = {
                "name": reporter_name,
                "command": command,
            }
            if reporter.get("metadata") is not None:
                entry["metadata"] = reporter.get("metadata")
            reporter_defs.append(entry)
        loaded.append(
            {
                "name": name,
                "version": str(payload.get("version", "")),
                "path": manifest,
                "capabilities": payload.get("capabilities", {}),
                "capabilityVersions": capability_versions,
                "mutators": [m.get("name") for m in payload.get("mutators", []) if isinstance(m, dict)],
                "reporters": [r.get("name") for r in payload.get("reporters", []) if isinstance(r, dict)],
                "reporterCommands": reporter_defs,
                "reporterMetadata": reporter_metadata,
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


def _plugin_lifecycle_hook_names(event: str) -> list[str]:
    out: list[str] = []
    for name in (event, *PLUGIN_LIFECYCLE_ALIASES.get(event, ())):
        if name not in out:
            out.append(name)
    return out


def _plugin_lifecycle_event_for_hook(hook: str) -> str:
    for event, aliases in PLUGIN_LIFECYCLE_ALIASES.items():
        if hook in aliases:
            return event
    return hook


def _plugin_redacted_environment_summary(
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "overrides": {
            key: REDACTED_VALUE
            for key in sorted(env_overrides or {})
        },
        "inherited": sorted(env_inherit) if env_inherit is not None else ["*"],
        "blocked": sorted(env_block or []),
        "provided": sorted(extra or {}),
        "redacted": True,
    }


def _plugin_lifecycle_metadata(plugins: list[dict[str, Any]]) -> dict[str, Any]:
    registered: list[dict[str, Any]] = []
    load_order: list[str] = []
    event_order = {event: idx for idx, event in enumerate(PLUGIN_LIFECYCLE_EVENTS)}
    for plugin_index, plugin in enumerate(plugins):
        plugin_name = str(plugin.get("name", ""))
        load_order.append(plugin_name)
        hooks = plugin.get("hooks", {})
        if not isinstance(hooks, dict):
            continue
        hook_names = sorted(
            (name for name in hooks if _plugin_commands(hooks.get(name))),
            key=lambda name: (
                event_order.get(_plugin_lifecycle_event_for_hook(str(name)), len(event_order)),
                str(name),
            ),
        )
        for hook in hook_names:
            commands = _plugin_commands(hooks.get(hook))
            registered.append(
                {
                    "plugin": plugin_name,
                    "pluginIndex": plugin_index,
                    "event": _plugin_lifecycle_event_for_hook(str(hook)),
                    "hook": str(hook),
                    "commandCount": len(commands),
                    "commands": [_redact_sensitive_assignment_text(command) for command in commands],
                    "legacyAlias": str(hook) in {"preRun", "postRun"},
                }
            )
    return {
        "schemaVersion": PLUGIN_LIFECYCLE_SCHEMA_VERSION,
        "supportedEvents": list(PLUGIN_LIFECYCLE_EVENTS),
        "legacyAliases": {
            event: list(aliases)
            for event, aliases in PLUGIN_LIFECYCLE_ALIASES.items()
        },
        "loadOrder": load_order,
        "registeredHooks": registered,
        "runs": [],
        "localOnly": True,
        "networkInstall": False,
    }


def _record_plugin_lifecycle_runs(rep: Report, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    lifecycle = rep.execution.setdefault("pluginLifecycle", _plugin_lifecycle_metadata([]))
    runs = lifecycle.setdefault("runs", [])
    if isinstance(runs, list):
        runs.extend(records)


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
    phase: str | None = None,
) -> dict[str, Any]:
    os.makedirs(artifact_root, exist_ok=True)
    safe_plugin = _safe_basename(str(plugin.get("name", "plugin")))
    safe_hook = _safe_basename(hook)
    log_path = os.path.join(artifact_root, f"plugin_{safe_plugin}_{safe_hook}.log")
    event = phase or _plugin_lifecycle_event_for_hook(hook)
    extra_env = {
        "STRYKER_CXX_PLUGIN": str(plugin.get("name", "")),
        "STRYKER_CXX_HOOK": hook,
        "STRYKER_CXX_PHASE": event,
        "STRYKER_CXX_REPORT": report_path,
        "STRYKER_CXX_ARTIFACT_DIR": artifact_root,
    }
    env = _build_subprocess_env(
        env_overrides,
        env_inherit,
        env_block,
        extra_env,
    )
    started = _utc_now_iso()
    started_monotonic = time.monotonic()
    with open(log_path, "a") as log:
        proc = subprocess.run(command, cwd=repo, shell=True, stdout=log, stderr=subprocess.STDOUT, env=env)
    completed = _utc_now_iso()
    record = {
        "phase": event,
        "hook": hook,
        "plugin": str(plugin.get("name", "")),
        "command": _redact_sensitive_assignment_text(command),
        "status": "passed" if proc.returncode == 0 else "failed",
        "exitCode": proc.returncode,
        "startedAt": started,
        "completedAt": completed,
        "durationMs": int((time.monotonic() - started_monotonic) * 1000),
        "log": log_path,
        "environment": _plugin_redacted_environment_summary(
            env_overrides,
            env_inherit,
            env_block,
            extra_env,
        ),
    }
    if proc.returncode != 0:
        env_summary = json.dumps(record["environment"], sort_keys=True)
        raise ValueError(
            "plugin hook failed "
            f"(phase={event}, plugin={plugin.get('name')}, hook={hook}, "
            f"command={record['command']}, env={env_summary}); see {log_path}"
        )
    return record


def _run_plugin_hooks(
    plugins: list[dict[str, Any]],
    hook: str,
    repo: str,
    artifact_root: str,
    report_path: str,
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
    phase: str | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for plugin in plugins:
        hooks = plugin.get("hooks", {})
        if not isinstance(hooks, dict):
            continue
        for command in _plugin_commands(hooks.get(hook)):
            records.append(
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
                    phase,
                )
            )
    return records


def _run_plugin_lifecycle_hooks(
    plugins: list[dict[str, Any]],
    event: str,
    repo: str,
    artifact_root: str,
    report_path: str,
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for hook in _plugin_lifecycle_hook_names(event):
        records.extend(
            _run_plugin_hooks(
                plugins,
                hook,
                repo,
                artifact_root,
                report_path,
                env_overrides,
                env_inherit,
                env_block,
                phase=event,
            )
        )
    return records


def _run_reporter_plugins(
    plugins: list[dict[str, Any]],
    requested_reporters: list[str],
    repo: str,
    artifact_root: str,
    report_path: str,
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    requested = set(requested_reporters)
    if not requested:
        return records
    for plugin in plugins:
        for reporter in plugin.get("reporterCommands", []):
            if not isinstance(reporter, dict):
                continue
            name = str(reporter.get("name", ""))
            command = str(reporter.get("command", ""))
            if name in requested and command:
                records.append(
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
                        phase="reporting",
                    )
                )
    return records


def _reporter_name_from_hook(hook: Any) -> str:
    hook_name = str(hook)
    prefix = "reporter_"
    return hook_name[len(prefix) :] if hook_name.startswith(prefix) else hook_name


def _reporter_command_names(plugins: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for plugin in plugins:
        for reporter in plugin.get("reporterCommands", []):
            if not isinstance(reporter, dict):
                continue
            name = str(reporter.get("name", ""))
            command = str(reporter.get("command", ""))
            if name and command:
                names.add(name)
    return names


def _missing_reporter_request_records(
    plugins: list[dict[str, Any]],
    requested_reporters: list[str],
) -> list[dict[str, Any]]:
    available = sorted(_reporter_command_names(plugins))
    available_set = set(available)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for reporter in requested_reporters:
        name = str(reporter)
        if not name or name in seen:
            continue
        seen.add(name)
        if name in available_set:
            continue
        out.append(
            {
                "plugin": None,
                "reporter": name,
                "hook": None,
                "phase": "reporting",
                "command": None,
                "status": "notFound",
                "exitCode": None,
                "durationMs": 0,
                "log": None,
                "environment": None,
                "reason": "requested reporter was not provided by loaded plugin reporter commands",
                "availableReporters": available,
            }
        )
    return out


def _record_reporter_plugin_runs(
    rep: Report,
    records: list[dict[str, Any]],
    plugins: list[dict[str, Any]] | None = None,
    requested_reporters: list[str] | None = None,
) -> None:
    _record_plugin_lifecycle_runs(rep, records)
    diagnostic_records = _missing_reporter_request_records(
        plugins or [],
        requested_reporters or [],
    )
    if not records and not diagnostic_records:
        return
    reporter_runs = rep.execution.setdefault("reporterRuns", [])
    if not isinstance(reporter_runs, list):
        return
    for record in records:
        if not isinstance(record, dict):
            continue
        reporter_runs.append(
            {
                "plugin": record.get("plugin"),
                "reporter": _reporter_name_from_hook(record.get("hook")),
                "hook": record.get("hook"),
                "phase": record.get("phase"),
                "command": record.get("command"),
                "status": record.get("status"),
                "exitCode": record.get("exitCode"),
                "durationMs": record.get("durationMs"),
                "log": record.get("log"),
                "environment": record.get("environment"),
            }
        )
    reporter_runs.extend(diagnostic_records)


def _reporter_metadata(
    plugins: list[dict[str, Any]],
    requested_reporters: list[str],
) -> list[dict[str, Any]]:
    requested = set(requested_reporters)
    if not requested:
        return []
    out: list[dict[str, Any]] = []
    for plugin in plugins:
        for reporter in plugin.get("reporterMetadata", []):
            if not isinstance(reporter, dict):
                continue
            name = str(reporter.get("name", ""))
            metadata = reporter.get("metadata")
            if not name or name not in requested or not isinstance(metadata, dict):
                continue
            out.append(
                {
                    "plugin": str(plugin.get("name", "")),
                    "reporter": name,
                    "metadata": metadata,
                }
            )
    return out


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


def _safe_worker_label(label: str | None) -> str:
    if not label:
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", label.strip())
    cleaned = cleaned.strip("-._")
    return cleaned[:48]


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


def _record_macro_range_rejection(
    analysis: dict[str, Any] | None,
    path: str,
    item: dict[str, int | str],
    reason: str,
) -> None:
    if analysis is None:
        return
    analysis["macroRejectedRanges"] = int(analysis.get("macroRejectedRanges", 0)) + 1
    rejections = analysis.setdefault("macroRangeRejections", [])
    if isinstance(rejections, list):
        rejections.append(
            {
                "file": path,
                "startLine": int(item.get("startLine", 0) or 0),
                "startColumn": int(item.get("startColumn", 0) or 0),
                "endLine": int(item.get("endLine", 0) or 0),
                "endColumn": int(item.get("endColumn", 0) or 0),
                "nodeKind": str(item.get("kind", "")),
                "reason": reason,
            }
        )


def _range_touches_preprocessor_directive(src: list[str], item: dict[str, int | str]) -> bool:
    start_line = int(item.get("startLine", 0) or 0)
    end_line = int(item.get("endLine", 0) or 0)
    if start_line <= 0 or end_line <= 0:
        return False
    for line_no in range(start_line, min(end_line, len(src)) + 1):
        if src[line_no - 1].lstrip().startswith("#"):
            return True
    return False


def _rejects_macro_range(
    analysis: dict[str, Any] | None,
    path: str,
    src: list[str],
    item: dict[str, int | str],
) -> bool:
    kind = str(item.get("kind", ""))
    if kind in PREPROCESSOR_CURSOR_KINDS:
        _record_macro_range_rejection(analysis, path, item, "source range is a macro or preprocessor cursor")
        return True
    if _range_touches_preprocessor_directive(src, item):
        _record_macro_range_rejection(analysis, path, item, "source range touches a preprocessor directive")
        return True
    return False


def _strip_noncode(
    line: str,
    in_block_comment: bool = False,
    mask_string_literals: bool = True,
    mask_character_literals: bool = True,
) -> tuple[str, bool]:
    """Blank out comments and optionally preserve quoted literals."""
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
            if mask_string_literals:
                out.append(" " * (end - i))
            else:
                out.append(line[i:end])
            i = end
            continue

        if line[i] == "'":
            start = i
            end = i + 1
            while end < n:
                if line[end] == "\\":
                    end += 2
                    continue
                if line[end] == "'":
                    end += 1
                    break
                end += 1
            if mask_character_literals:
                out.append(" " * (end - start))
            else:
                out.append(line[start:end])
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
        mut.sourceRange = {
            "kind": "TOKEN_CALL_EXPRESSION",
            "startLine": line,
            "startColumn": match.start(1) + 1,
            "endLine": line,
            "endColumn": match.start(1) + len(original) + 1,
        }
        mut.rewriteStrategy = "token-call-removal"
        out.append(mut)
    return out


def _statement_should_skip(prefix: str) -> bool:
    return any(prefix == token or prefix.startswith(f"{token} ") for token in _STATEMENT_REMOVAL_FORBIDDEN_PREFIXES)


def _discover_statement_removals(path: str, line: int, code: str, raw: str) -> list[Mutant]:
    out: list[Mutant] = []
    segments: list[tuple[int, int]] = []
    paren = 0
    bracket = 0
    start = 0
    line_len = len(code)

    for i, char in enumerate(code):
        if char == "(":
            paren += 1
        elif char == ")":
            paren = max(0, paren - 1)
        elif char == "[":
            bracket += 1
        elif char == "]":
            bracket = max(0, bracket - 1)
        elif char == ";" and paren == 0 and bracket == 0:
            segments.append((start, min(i + 1, line_len)))
            start = i + 1

    if start < line_len and code[start:].strip():
        segments.append((start, line_len))

    for seg_start, seg_end in segments:
        seg = code[seg_start:seg_end]
        if not seg:
            continue
        segment = seg.rstrip()
        if not segment:
            continue
        semi_pos = segment.rfind(";")
        if semi_pos < 0:
            continue
        statement = segment[: semi_pos + 1]
        trimmed = statement.strip()
        if not trimmed or trimmed == ";":
            continue
        if trimmed.startswith("{") or trimmed.startswith("}"):
            continue
        if ":" in statement:
            # Avoid confusing ternaries and label-style constructs in token mode.
            continue
        prefix = trimmed.split(None, 1)[0]
        if _statement_should_skip(prefix):
            continue
        leading = len(statement) - len(statement.lstrip())
        original = raw[seg_start + leading : seg_start + len(statement)]
        if not original.strip():
            continue
        mut = Mutant("StatementRemoval", path, line, seg_start + leading, original, ";")
        mut.id = stable_id(mut)
        mut.sourceRange = {
            "kind": "TOKEN_STATEMENT",
            "startLine": line,
            "startColumn": seg_start + leading + 1,
            "endLine": line,
            "endColumn": seg_start + leading + len(original) + 1,
        }
        mut.rewriteStrategy = "token-statement-removal"
        out.append(mut)
    return out


def _discover_block_removals(path: str, line: int, code: str, raw: str) -> list[Mutant]:
    normalized = code.rstrip("\r\n")
    raw_line = raw.rstrip("\r\n")
    if not _BLOCK_REMOVAL_RE.fullmatch(normalized):
        return []
    if "{" not in normalized or "}" not in normalized:
        return []
    start = normalized.find("{")
    end = normalized.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return []
    original = raw_line[start : end + 1]
    mut = Mutant("BlockRemoval", path, line, start, original, "{}")
    mut.id = stable_id(mut)
    mut.sourceRange = {
        "kind": "TOKEN_BLOCK",
        "startLine": line,
        "startColumn": start + 1,
        "endLine": line,
        "endColumn": end + 2,
    }
    mut.rewriteStrategy = "token-block-removal"
    return [mut]


def _find_matching_paren(text: str, start: int) -> int | None:
    if start < 0 or start >= len(text) or text[start] != "(":
        return None
    depth = 0
    for index, char in enumerate(text[start:], start=start):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _discover_loop_conditions(code: str) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []

    for match in re.finditer(r"\bfor\s*\(", code):
        open_paren = match.end() - 1
        close_paren = _find_matching_paren(code, open_paren)
        if close_paren is None:
            continue
        inside = code[open_paren + 1 : close_paren]
        semicolons: list[int] = []
        paren = 0
        bracket = 0
        brace = 0
        for offset, char in enumerate(inside):
            if char == "(":
                paren += 1
            elif char == ")":
                paren = max(0, paren - 1)
            elif char == "[":
                bracket += 1
            elif char == "]":
                bracket = max(0, bracket - 1)
            elif char == "{":
                brace += 1
            elif char == "}":
                brace = max(0, brace - 1)
            elif (
                char == ";"
                and paren == 0
                and bracket == 0
                and brace == 0
            ):
                semicolons.append(offset)

        if len(semicolons) < 2:
            continue
        cond_start = open_paren + 1 + semicolons[0] + 1
        cond_end = open_paren + 1 + semicolons[1]
        condition = code[cond_start:cond_end]
        if not condition.strip():
            continue
        out.append((cond_start, cond_end, condition))

    for match in re.finditer(r"\bwhile\s*\(", code):
        open_paren = match.end() - 1
        close_paren = _find_matching_paren(code, open_paren)
        if close_paren is None:
            continue
        cond_start = open_paren + 1
        cond_end = close_paren
        condition = code[cond_start:cond_end]
        if not condition.strip():
            continue
        out.append((cond_start, cond_end, condition))

    return out


def _discover_loop_boundary_mutations(path: str, line: int, code: str) -> list[Mutant]:
    out: list[Mutant] = []
    for cond_start, cond_end, _ in _discover_loop_conditions(code):
        condition = code[cond_start:cond_end]
        if not condition.strip():
            continue
        leading = len(condition) - len(condition.lstrip())
        trailing = len(condition) - len(condition.rstrip())
        for orig, new in MUTATORS["LoopBoundary"]:
            pattern = _TOKEN_PATTERNS.get(orig)
            if pattern is None:
                continue
            for match in re.finditer(pattern, condition):
                mut = Mutant(
                    "LoopBoundary",
                    path,
                    line,
                    cond_start + match.start(),
                    match.group(0),
                    new,
                )
                mut.id = stable_id(mut)
                mut.sourceRange = {
                    "kind": "TOKEN_LOOP_CONDITION",
                    "startLine": line,
                    "startColumn": cond_start + leading + 1,
                    "endLine": line,
                    "endColumn": cond_end - trailing + 1,
                }
                mut.rewriteStrategy = "token-loop-boundary"
                out.append(mut)
    return out


def _discover_loop_condition_mutations(path: str, line: int, code: str) -> list[Mutant]:
    out: list[Mutant] = []
    for cond_start, cond_end, condition in _discover_loop_conditions(code):
        if not condition.strip():
            continue
        leading = len(condition) - len(condition.lstrip())
        trailing = len(condition) - len(condition.rstrip())
        core = condition.strip()
        mut = Mutant(
            "LoopCondition",
            path,
            line,
            cond_start + leading,
            core,
            f"!({core})",
        )
        mut.id = stable_id(mut)
        mut.sourceRange = {
            "kind": "TOKEN_LOOP_CONDITION",
            "startLine": line,
            "startColumn": cond_start + leading + 1,
            "endLine": line,
            "endColumn": cond_end - trailing + 1,
        }
        mut.rewriteStrategy = "token-loop-condition"
        out.append(mut)
    return out


def _discover_member_access_mutations(path: str, line: int, code: str) -> list[Mutant]:
    replacements = {
        ".": "->",
        "->": ".",
        ".*": "->*",
        "->*": ".*",
    }
    out: list[Mutant] = []
    for match in re.finditer(_MEMBER_ACCESS_RE, code):
        op = match.group("op")
        replacement = replacements.get(op)
        if replacement is None:
            continue
        mut = Mutant(
            "MemberAccessOperator",
            path,
            line,
            match.start("op"),
            op,
            replacement,
        )
        mut.id = stable_id(mut)
        mut.sourceRange = {
            "kind": "TOKEN_MEMBER_ACCESS_OPERATOR",
            "startLine": line,
            "startColumn": match.start("op") + 1,
            "endLine": line,
            "endColumn": match.start("op") + len(op) + 1,
        }
        mut.rewriteStrategy = "token-member-access-operator"
        out.append(mut)
    return out


def _discover_exception_handling_mutations(path: str, line: int, code: str, raw: str) -> list[Mutant]:
    out: list[Mutant] = []
    for match in re.finditer(_THROW_STATEMENT_RE, code):
        original = raw[match.start() : match.end()]
        if not original.strip():
            continue
        mut = Mutant(
            "ExceptionHandling",
            path,
            line,
            match.start(),
            original,
            "(void)0;",
        )
        mut.id = stable_id(mut)
        mut.sourceRange = {
            "kind": "TOKEN_THROW_STATEMENT",
            "startLine": line,
            "startColumn": match.start() + 1,
            "endLine": line,
            "endColumn": match.start() + len(original) + 1,
        }
        mut.rewriteStrategy = "token-throw-statement"
        out.append(mut)
    return out


def _discover_preprocessor_guard_mutations(path: str, line: int, raw: str) -> list[Mutant]:
    out: list[Mutant] = []
    ifdef_match = re.match(r"(\s*#\s*)(ifdef|ifndef)\b", raw)
    if ifdef_match:
        original = ifdef_match.group(2)
        mutated = "ifndef" if original == "ifdef" else "ifdef"
        mut = Mutant(
            "PreprocessorGuard",
            path,
            line,
            ifdef_match.start(2),
            original,
            mutated,
        )
        mut.id = stable_id(mut)
        mut.sourceRange = {
            "kind": "TOKEN_PREPROCESSOR_GUARD",
            "startLine": line,
            "startColumn": ifdef_match.start(2) + 1,
            "endLine": line,
            "endColumn": ifdef_match.start(2) + len(original) + 1,
        }
        mut.rewriteStrategy = "token-preprocessor-guard"
        out.append(mut)
        return out

    if_match = re.match(r"(\s*#\s*if\s+)(0|1)\b", raw)
    if if_match:
        original = if_match.group(2)
        mut = Mutant(
            "PreprocessorGuard",
            path,
            line,
            if_match.start(2),
            original,
            "1" if original == "0" else "0",
        )
        mut.id = stable_id(mut)
        mut.sourceRange = {
            "kind": "TOKEN_PREPROCESSOR_GUARD",
            "startLine": line,
            "startColumn": if_match.start(2) + 1,
            "endLine": line,
            "endColumn": if_match.start(2) + len(original) + 1,
        }
        mut.rewriteStrategy = "token-preprocessor-guard"
        out.append(mut)
    return out


def _discover_objc_message_send_mutations(path: str, line: int, code: str, raw: str) -> list[Mutant]:
    match = _OBJC_MESSAGE_SEND_RE.match(code)
    if match is None:
        return []
    original = raw[match.start(1) : match.end(1)]
    if not original.strip():
        return []
    mut = Mutant(
        "ObjCMessageSend",
        path,
        line,
        match.start(1),
        original,
        "(void)0",
    )
    mut.id = stable_id(mut)
    mut.sourceRange = {
        "kind": "TOKEN_OBJC_MESSAGE_EXPR",
        "startLine": line,
        "startColumn": match.start(1) + 1,
        "endLine": line,
        "endColumn": match.start(1) + len(original) + 1,
    }
    mut.rewriteStrategy = "token-objc-message-send"
    return [mut]


def _discover_metal_address_space_mutations(path: str, line: int, code: str) -> list[Mutant]:
    if not path.lower().endswith(".metal"):
        return []
    replacements = {
        "device": "constant",
        "constant": "device",
        "threadgroup": "device",
    }
    out: list[Mutant] = []
    for match in re.finditer(_METAL_ADDRESS_SPACE_RE, code):
        original = match.group(1)
        replacement = replacements.get(original)
        if replacement is None:
            continue
        mut = Mutant(
            "MetalAddressSpace",
            path,
            line,
            match.start(1),
            original,
            replacement,
        )
        mut.id = stable_id(mut)
        mut.sourceRange = {
            "kind": "TOKEN_METAL_ADDRESS_SPACE",
            "startLine": line,
            "startColumn": match.start(1) + 1,
            "endLine": line,
            "endColumn": match.start(1) + len(original) + 1,
        }
        mut.rewriteStrategy = "token-metal-address-space"
        out.append(mut)
    return out


def _mutate_string_literal(token: str) -> str | None:
    if _STRING_LITERAL_RE.fullmatch(token) is None:
        return None
    return "\"\"" if token != "\"\"" else "\"x\""


def _mutate_character_literal(token: str) -> str | None:
    if _CHARACTER_LITERAL_RE.fullmatch(token) is None:
        return None
    body = token
    if token.startswith("u8'"):
        prefix, quote = "u8", "'"
    elif token.startswith("L'"):
        prefix, quote = "L", "'"
    elif token.startswith("u'"):
        prefix, quote = "u", "'"
    elif token.startswith("U'"):
        prefix, quote = "U", "'"
    else:
        prefix, quote = "", "'"
    body = token[len(prefix) + 1 : -1]
    if not body:
        return None
    return f"{prefix}{quote}x{quote}"


def _mutate_floating_literal(token: str) -> str | None:
    # Keep a trailing C/C++ floating suffix when present.
    # The full token capture excludes signs and most integer-like values.
    m = re.fullmatch(r"(?P<body>(?:[0-9]+\.[0-9]*|\.[0-9]+|[0-9]+\.[0-9]+[eE][+-]?[0-9]+|[0-9]+[eE][+-]?[0-9]+))(?P<suffix>[fFlL]?)", token)
    if not m:
        return None
    body = m.group("body")
    suffix = m.group("suffix")
    # Toggle zero-like forms to 1.0, everything else to 0.0.
    is_zero_like = re.fullmatch(r"0(?:\.0*)?(?:[eE][+-]?[0-9]+)?", body, re.IGNORECASE) is not None
    return f"{'1.0' if is_zero_like else '0.0'}{suffix}"


def _integer_literal_range_replacement(text: str) -> tuple[int, str, str] | None:
    match = _INTEGER_LITERAL_RE.search(text)
    if not match:
        return None
    original = match.group(0)
    replacement = "1" if original == "0" else "0"
    return match.start(), original, replacement


def _null_literal_range_replacement(text: str) -> tuple[int, str, str] | None:
    match = _NULL_LITERAL_RE.search(text)
    if not match:
        return None
    original = match.group(0)
    replacement = "NULL" if original == "nullptr" else "nullptr"
    return match.start(), original, replacement


def _move_semantics_range_replacement(text: str) -> tuple[int, str, str] | None:
    match = _MOVE_SEMANTICS_CALL_RE.search(text)
    if not match:
        return None
    open_paren = match.end()
    while open_paren < len(text) and text[open_paren].isspace():
        open_paren += 1
    if open_paren >= len(text) or text[open_paren] != "(":
        return None
    close_paren = _find_matching_paren(text, open_paren)
    if close_paren is None:
        return None
    args = _split_top_level_arguments(text[open_paren + 1 : close_paren])
    if len(args) != 1:
        return None
    original = text[match.start() : close_paren + 1]
    mutated = args[0].strip()
    if not mutated:
        return None
    return match.start(), original, mutated


def _member_call_range_replacement(text: str, match: re.Match[str], replacement: str) -> tuple[int, str, str] | None:
    target_start = match.start("target")
    receiver_end = _skip_left_space(text, target_start - 1)
    if receiver_end < 0:
        return None
    if text[receiver_end] in ")]}":
        receiver_start = _matching_left_delimiter(text, receiver_end)
        if receiver_start is None:
            return None
        prefix_end = _skip_left_space(text, receiver_start - 1)
        if prefix_end >= 0 and re.match(r"[A-Za-z0-9_:~]", text[prefix_end]):
            receiver_start = _identifier_start_left(text, prefix_end + 1)
    else:
        receiver_start = _identifier_start_left(text, receiver_end + 1)
    if receiver_start >= target_start:
        return None
    open_paren = match.end("target")
    while open_paren < len(text) and text[open_paren].isspace():
        open_paren += 1
    if open_paren >= len(text) or text[open_paren] != "(":
        return None
    close_paren = _find_matching_paren(text, open_paren)
    if close_paren is None:
        return None
    original = text[receiver_start : close_paren + 1]
    mutated = text[receiver_start:target_start] + f".{replacement}" + text[open_paren : close_paren + 1]
    return receiver_start, original, mutated


def _qualified_call_range_replacement(
    text: str,
    match: re.Match[str],
    replacement: str,
) -> tuple[int, str, str] | None:
    target_start = match.start("target")
    open_paren = match.end("target")
    while open_paren < len(text) and text[open_paren].isspace():
        open_paren += 1
    if open_paren >= len(text) or text[open_paren] != "(":
        return None
    close_paren = _find_matching_paren(text, open_paren)
    if close_paren is None:
        return None
    original = text[target_start : close_paren + 1]
    mutated = replacement + text[open_paren : close_paren + 1]
    return target_start, original, mutated


def _standard_library_call_range_replacement(text: str) -> tuple[int, str, str] | None:
    match = _STANDARD_LIBRARY_CALL_RE.search(text)
    if not match:
        return None
    original_name = f"std::{match.group('name')}"
    replacement = _STANDARD_LIBRARY_CALL_REPLACEMENTS.get(original_name)
    if replacement is None:
        return None
    return _qualified_call_range_replacement(text, match, replacement)


def _container_call_range_replacement(text: str) -> tuple[int, str, str] | None:
    match = _CONTAINER_CALL_RE.search(text)
    if not match:
        return None
    name = match.group("name")
    replacement = _CONTAINER_CALL_REPLACEMENTS.get(name)
    if replacement is None:
        return None
    return _member_call_range_replacement(text, match, replacement)


def _container_state_call_range_replacement(text: str) -> tuple[int, str, str] | None:
    match = _CONTAINER_STATE_CALL_RE.search(text)
    if not match:
        return None
    name = match.group("name")
    replacement = _CONTAINER_STATE_CALL_REPLACEMENTS.get(name)
    if replacement is None:
        return None
    return _member_call_range_replacement(text, match, replacement)


def _string_call_range_replacement(text: str) -> tuple[int, str, str] | None:
    match = _STRING_CALL_RE.search(text)
    if not match:
        return None
    name = match.group("name")
    replacement = _STRING_CALL_REPLACEMENTS.get(name)
    if replacement is None:
        return None
    return _member_call_range_replacement(text, match, replacement)


def _math_call_range_replacement(text: str) -> tuple[int, str, str] | None:
    match = _MATH_CALL_RE.search(text)
    if not match:
        return None
    name = match.group("name")
    replacement = _MATH_CALL_REPLACEMENTS.get(name)
    if replacement is None:
        return None
    prefix = match.group("prefix") or ""
    open_paren = match.end()
    while open_paren < len(text) and text[open_paren].isspace():
        open_paren += 1
    if open_paren >= len(text) or text[open_paren] != "(":
        return None
    close_paren = _find_matching_paren(text, open_paren)
    if close_paren is None:
        return None
    original = text[match.start("target") : close_paren + 1]
    mutated = f"{prefix}{replacement}{text[open_paren : close_paren + 1]}"
    return match.start("target"), original, mutated


def _iterator_call_range_replacement(text: str) -> tuple[int, str, str] | None:
    match = _ITERATOR_CALL_RE.search(text)
    if not match:
        return None
    name = match.group("name")
    replacement = _ITERATOR_CALL_REPLACEMENTS.get(name)
    if replacement is None:
        return None
    prefix = match.group("prefix") or ""
    open_paren = match.end()
    while open_paren < len(text) and text[open_paren].isspace():
        open_paren += 1
    if open_paren >= len(text) or text[open_paren] != "(":
        return None
    close_paren = _find_matching_paren(text, open_paren)
    if close_paren is None:
        return None
    original = text[match.start("target") : close_paren + 1]
    mutated = f"{prefix}{replacement}{text[open_paren : close_paren + 1]}"
    return match.start("target"), original, mutated


def _chrono_call_range_replacement(text: str) -> tuple[int, str, str] | None:
    match = _CHRONO_CALL_RE.search(text)
    if not match:
        return None
    name = match.group("name")
    replacement = _CHRONO_CALL_REPLACEMENTS.get(name)
    if replacement is None:
        return None
    prefix = match.group("prefix") or ""
    open_paren = match.end()
    while open_paren < len(text) and text[open_paren].isspace():
        open_paren += 1
    if open_paren >= len(text) or text[open_paren] != "(":
        return None
    close_paren = _find_matching_paren(text, open_paren)
    if close_paren is None:
        return None
    original = text[match.start("target") : close_paren + 1]
    mutated = f"{prefix}{replacement}{text[match.end('target') : close_paren + 1]}"
    return match.start("target"), original, mutated


def _regex_call_range_replacement(text: str) -> tuple[int, str, str] | None:
    match = _REGEX_CALL_RE.search(text)
    if not match:
        return None
    name = match.group("name")
    replacement = _REGEX_CALL_REPLACEMENTS.get(name)
    if replacement is None:
        return None
    prefix = match.group("prefix") or ""
    open_paren = match.end()
    while open_paren < len(text) and text[open_paren].isspace():
        open_paren += 1
    if open_paren >= len(text) or text[open_paren] != "(":
        return None
    close_paren = _find_matching_paren(text, open_paren)
    if close_paren is None:
        return None
    original = text[match.start("target") : close_paren + 1]
    mutated = f"{prefix}{replacement}{text[open_paren : close_paren + 1]}"
    return match.start("target"), original, mutated


def _filesystem_call_range_replacement(text: str) -> tuple[int, str, str] | None:
    match = _FILESYSTEM_CALL_RE.search(text)
    if not match:
        return None
    name = match.group("name")
    replacement = _FILESYSTEM_CALL_REPLACEMENTS.get(name)
    if replacement is None:
        return None
    prefix = match.group("prefix") or ""
    open_paren = match.end()
    while open_paren < len(text) and text[open_paren].isspace():
        open_paren += 1
    if open_paren >= len(text) or text[open_paren] != "(":
        return None
    close_paren = _find_matching_paren(text, open_paren)
    if close_paren is None:
        return None
    original = text[match.start("target") : close_paren + 1]
    mutated = f"{prefix}{replacement}{text[open_paren : close_paren + 1]}"
    return match.start("target"), original, mutated


def _string_literal_range_replacement(text: str) -> tuple[int, str, str] | None:
    match = _STRING_LITERAL_RE.search(text)
    if not match:
        return None
    original = match.group(0)
    replacement = _mutate_string_literal(original)
    if replacement is None:
        return None
    return match.start(), original, replacement


def _character_literal_range_replacement(text: str) -> tuple[int, str, str] | None:
    match = _CHARACTER_LITERAL_RE.search(text)
    if not match:
        return None
    original = match.group(0)
    replacement = _mutate_character_literal(original)
    if replacement is None:
        return None
    return match.start(), original, replacement


def _floating_literal_range_replacement(text: str) -> tuple[int, str, str] | None:
    match = _FLOATING_LITERAL_RE.search(text)
    if not match:
        return None
    original = match.group(0)
    replacement = _mutate_floating_literal(original)
    if replacement is None:
        return None
    return match.start(), original, replacement


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


def _source_has_generated_marker(src: list[str]) -> bool:
    header = "\n".join(src[:40]).lower()
    return any(marker in header for marker in GENERATED_CODE_MARKERS)


def _path_looks_generated(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    basename = os.path.basename(normalized)
    return any(pattern in normalized or basename.startswith(pattern) for pattern in GENERATED_PATH_PATTERNS)


def _strip_outer_parens(expr: str) -> str:
    out = expr.strip()
    while out.startswith("(") and out.endswith(")"):
        close = _find_matching_paren(out, 0)
        if close != len(out) - 1:
            break
        out = out[1:-1].strip()
    return out


def _normalize_equivalent_operand(expr: str) -> str:
    out = expr.strip()
    if out.startswith("return "):
        out = out[len("return ") :].strip()
    out = _strip_outer_parens(out)
    return re.sub(r"\s+", "", out)


def _operand_left(text: str, end: int) -> str:
    i = end - 1
    depth = 0
    while i >= 0:
        char = text[i]
        if char in ")]}":
            depth += 1
        elif char in "([{":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0:
            if char in ";,{}?:":
                break
            if char == "=" and not (
                (i > 0 and text[i - 1] in {"=", "!", "<", ">"})
                or (i + 1 < len(text) and text[i + 1] == "=")
            ):
                break
            if i > 0 and text[i - 1 : i + 1] in {"&&", "||"}:
                break
        i -= 1
    return text[i + 1 : end]


def _operand_right(text: str, start: int) -> str:
    i = start
    depth = 0
    while i < len(text):
        char = text[i]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0:
            if char in ";,{}?:":
                break
            if char == "=" and not (
                (i > 0 and text[i - 1] in {"=", "!", "<", ">"})
                or (i + 1 < len(text) and text[i + 1] == "=")
            ):
                break
            if i + 1 < len(text) and text[i : i + 2] in {"&&", "||"}:
                break
        i += 1
    return text[start:i]


def _operand_is_pureish(expr: str) -> bool:
    compact = _normalize_equivalent_operand(expr)
    if not compact:
        return False
    if "++" in compact or "--" in compact:
        return False
    if re.search(r"(?<![=!<>])=(?!=)", compact):
        return False
    no_outer = _strip_outer_parens(expr.strip())
    return "(" not in no_outer and ")" not in no_outer


def _numeric_identity_token(expr: str) -> str | None:
    normalized = _normalize_equivalent_operand(expr)
    if re.fullmatch(r"0(?:\.0*)?(?:[fFlL])?", normalized):
        return "0"
    if re.fullmatch(r"1(?:\.0*)?(?:[fFlL])?", normalized):
        return "1"
    return None


def _split_top_level_arguments(text: str) -> list[str]:
    args: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for i, char in enumerate(text):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            args.append(text[start:i].strip())
            start = i + 1
    tail = text[start:].strip()
    if tail:
        args.append(tail)
    return args


def _call_arguments_after(text: str, offset: int) -> list[str]:
    i = offset
    while i < len(text) and text[i].isspace():
        i += 1
    if i >= len(text) or text[i] != "(":
        return []
    close = _find_matching_paren(text, i)
    if close < 0:
        return []
    return _split_top_level_arguments(text[i + 1 : close])


def _conditional_expression_branches(text: str) -> tuple[str, str] | None:
    question = text.find("?")
    if question < 0:
        depth = 0
        quote: str | None = None
        escaped = False
        for i, char in enumerate(text):
            if quote is not None:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if char in {"'", '"'}:
                quote = char
                continue
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth = max(0, depth - 1)
            elif char == ":" and depth == 0:
                left = text[:i].strip()
                right = text[i + 1 :].strip()
                left = left.rstrip(";")
                right = right.rstrip(";")
                if left and right:
                    return left, right
        return None
    depth = 0
    quote: str | None = None
    escaped = False
    for i in range(question + 1, len(text)):
        char = text[i]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == ":" and depth == 0:
            return text[question + 1 : i].strip(), text[i + 1 :].strip()
    return None


def _standard_library_call_name_and_args(mut: Mutant, line: str) -> tuple[str, list[str]]:
    match = _STANDARD_LIBRARY_CALL_RE.match(mut.original)
    if match:
        return f"std::{match.group('name')}", _call_arguments_after(mut.original, match.end("target"))
    return mut.original, _call_arguments_after(line, mut.col + len(mut.original))


def _equivalent_suppression_reason(mut: Mutant, src: list[str], generated: bool, mode: str) -> str | None:
    if generated:
        return "generated code auto-suppression"
    if mut.line < 1 or mut.line > len(src):
        return None
    line = src[mut.line - 1].rstrip("\r\n")
    if mut.mutator == "LogicalOperator" and mut.original in {"&&", "||"}:
        left = _operand_left(line, mut.col)
        right = _operand_right(line, mut.col + len(mut.original))
        if (
            _operand_is_pureish(left)
            and _operand_is_pureish(right)
            and _normalize_equivalent_operand(left) == _normalize_equivalent_operand(right)
        ):
            return "equivalent duplicate logical operand"
    if mut.mutator == "ArithmeticOperator" and mut.original in {"+", "-", "*", "/"}:
        right = _operand_right(line, mut.col + len(mut.original))
        identity = _numeric_identity_token(right)
        if mut.original in {"+", "-"} and identity == "0":
            return "equivalent arithmetic identity"
        if mut.original in {"*", "/"} and identity == "1":
            return "equivalent arithmetic identity"
    if mut.mutator == "BitwiseOperator" and mut.original in {"&", "|"}:
        left = _operand_left(line, mut.col)
        right = _operand_right(line, mut.col + len(mut.original))
        if (
            _operand_is_pureish(left)
            and _operand_is_pureish(right)
            and _normalize_equivalent_operand(left) == _normalize_equivalent_operand(right)
        ):
            return "equivalent duplicate bitwise operand"
    if mut.mutator == "StandardLibraryCall":
        original_name, args = _standard_library_call_name_and_args(mut, line)
        if original_name in {"std::min", "std::max"} and (
            len(args) == 2
            and _operand_is_pureish(args[0])
            and _operand_is_pureish(args[1])
            and _normalize_equivalent_operand(args[0]) == _normalize_equivalent_operand(args[1])
        ):
            return "equivalent duplicate standard-library operands"
        if original_name in {"std::lower_bound", "std::upper_bound"} and (
            len(args) >= 2
            and _operand_is_pureish(args[0])
            and _operand_is_pureish(args[1])
            and _normalize_equivalent_operand(args[0]) == _normalize_equivalent_operand(args[1])
        ):
            return "equivalent duplicate standard-library range"
    if mut.mutator == "ConditionalExpression":
        branches = _conditional_expression_branches(mut.original)
        if branches is not None:
            true_branch, false_branch = branches
            if true_branch and _normalize_equivalent_operand(true_branch) == _normalize_equivalent_operand(false_branch):
                return "equivalent duplicate conditional branches"
    if mode == "aggressive" and mut.mutator == "NullLiteral":
        return "style-equivalent null literal suppression"
    return None


def _equivalent_suppression_rule_id(reason: str) -> str:
    return EQUIVALENT_SUPPRESSION_RULE_IDS.get(
        reason,
        re.sub(r"[^a-z0-9]+", "-", reason.lower()).strip("-") or "equivalent-suppression",
    )


def _record_equivalent_suppression(
    analysis: dict[str, Any] | None,
    mode: str,
    mut: Mutant,
    reason: str,
) -> None:
    if analysis is None:
        return
    payload = analysis.setdefault(
        "equivalentSuppression",
        {
            "mode": mode,
            "suppressedMutants": 0,
            "suppressions": [],
        },
    )
    if isinstance(payload, dict):
        payload["mode"] = mode
        payload["suppressedMutants"] = int(payload.get("suppressedMutants", 0)) + 1
        suppressions = payload.setdefault("suppressions", [])
        if isinstance(suppressions, list):
            suppressions.append(
                {
                    "id": mut.id,
                    "file": mut.file,
                    "line": mut.line,
                    "column": mut.col + 1,
                    "mutator": mut.mutator,
                    "ruleId": _equivalent_suppression_rule_id(reason),
                    "reason": reason,
                }
            )


def _apply_equivalent_suppression(
    repo: str,
    path: str,
    mutants: list[Mutant],
    mode: str,
    analysis: dict[str, Any] | None = None,
) -> list[Mutant]:
    if not mutants or mode == "off":
        return mutants
    if mode not in EQUIVALENT_SUPPRESSION_MODES:
        raise ValueError(f"unknown equivalent suppression mode: {mode}")
    try:
        with open(os.path.join(repo, path)) as f:
            src = f.readlines()
    except OSError:
        src = []
    generated = _source_has_generated_marker(src) or (mode == "aggressive" and _path_looks_generated(path))
    for mut in mutants:
        if mut.status == "IGNORED":
            continue
        reason = _equivalent_suppression_reason(mut, src, generated, mode)
        if reason is None:
            continue
        mut.status = "IGNORED"
        mut.detail = reason
        mut.ignoreReason = reason
        mut.run["suppression"] = "equivalent"
        mut.run["suppressionRule"] = _equivalent_suppression_rule_id(reason)
        mut.run["suppressionReason"] = reason
        _record_equivalent_suppression(analysis, mode, mut, reason)
    return mutants


def _finalize_discovered_mutants(
    repo: str,
    path: str,
    mutants: list[Mutant],
    equivalent_suppression: str,
    analysis: dict[str, Any] | None = None,
) -> list[Mutant]:
    ignored = _apply_stryker_ignore_comments(repo, path, mutants)
    return _apply_equivalent_suppression(repo, path, ignored, equivalent_suppression, analysis)


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
    parts = [
        "stryker-cxx",
        "run-mutant",
        "--repo",
        _quote_for_shell(repo),
        "--id",
        _quote_for_shell(mut.id),
        "--build-command",
        _quote_for_shell(build_cmd),
        "--test-command",
        _quote_for_shell(test_cmd),
        "--report",
        _quote_for_shell(report or os.path.join(repo, "mutation.json")),
        "--output-format",
        "stryker-cxx",
    ]
    run = mut.run if isinstance(mut.run, dict) else {}
    mode = run.get("mode")
    if isinstance(mode, str) and mode:
        parts.extend(["--mode", _quote_for_shell(mode)])
    execution_mode = run.get("executionMode")
    if isinstance(execution_mode, str) and execution_mode:
        parts.extend(["--execution-mode", _quote_for_shell(execution_mode)])
    artifact = run.get("compiledArtifact")
    if isinstance(artifact, dict):
        backend = artifact.get("backend")
        if isinstance(backend, str) and backend:
            parts.extend(["--artifact-backend", _quote_for_shell(backend)])
        target = artifact.get("target")
        if isinstance(target, str) and target:
            parts.extend(["--build-target", _quote_for_shell(target)])
    worktree_mode = run.get("worktreeMode")
    if isinstance(worktree_mode, str) and worktree_mode and worktree_mode != "inplace":
        parts.extend(["--worktree-mode", _quote_for_shell(worktree_mode)])
    return " ".join(parts)


_UNARY_SIGN_RE = re.compile(
    r"(?P<prefix>(?:^|[=(,{?:]\s*|\breturn\s+))"
    r"(?P<op>[+-])(?![+=-])"
    r"(?P<operand>[A-Za-z_][A-Za-z0-9_:]*|\d+(?:\.\d+)?)"
)
_UPDATE_OPERATOR_RE = re.compile(
    r"(?<![+\-])(?P<prefix>(?P<prefix_op>\+\+|--)\s*(?P<prefix_operand>[A-Za-z_][A-Za-z0-9_]*))"
    r"|(?P<suffix>(?P<suffix_operand>[A-Za-z_][A-Za-z0-9_]*)(?P<suffix_op>\+\+|--))(?![+\-=])"
)
_MOVE_SEMANTICS_CALL_RE = re.compile(
    r"\bstd\s*::\s*(?:move|forward)(?:\s*<[^;\n()]+>)?\s*(?=\()"
)
_STANDARD_LIBRARY_CALL_REPLACEMENTS = {
    original: replacement for original, replacement in MUTATORS["StandardLibraryCall"]
}
_STANDARD_LIBRARY_CALL_RE = re.compile(
    r"\b(?P<target>std\s*::\s*(?P<name>"
    r"min|max|all_of|any_of|none_of|equal|mismatch|lower_bound|upper_bound|"
    r"c?begin|c?end|stable_sort|sort|stable_partition|partition|is_sorted|is_heap"
    r"))\s*(?=\()"
)
_CONTAINER_CALL_REPLACEMENTS = {
    "front": "back",
    "back": "front",
    "begin": "end",
    "end": "begin",
    "cbegin": "cend",
    "cend": "cbegin",
    "rbegin": "rend",
    "rend": "rbegin",
}
_CONTAINER_CALL_RE = re.compile(
    r"(?P<target>\.(?P<name>front|back|c?begin|c?end|rbegin|rend))\s*(?=\()"
)
_CONTAINER_STATE_CALL_REPLACEMENTS = {
    "empty": "size",
    "size": "empty",
    "capacity": "size",
    "max_size": "size",
}
_CONTAINER_STATE_CALL_RE = re.compile(
    r"(?P<target>\.(?P<name>empty|size|capacity|max_size))\s*(?=\()"
)
_STRING_CALL_REPLACEMENTS = {
    "find": "rfind",
    "rfind": "find",
    "starts_with": "ends_with",
    "ends_with": "starts_with",
}
_STRING_CALL_RE = re.compile(
    r"(?P<target>\.(?P<name>find|rfind|starts_with|ends_with))\s*(?=\()"
)
_MATH_CALL_REPLACEMENTS = {
    "ceil": "floor",
    "floor": "ceil",
    "round": "trunc",
    "trunc": "round",
}
_MATH_CALL_RE = re.compile(
    r"(?<![\w:.])(?P<target>(?P<prefix>std\s*::\s*)?(?P<name>ceil|floor|round|trunc))\s*(?=\()"
)
_ITERATOR_CALL_REPLACEMENTS = {
    "next": "prev",
    "prev": "next",
}
_ITERATOR_CALL_RE = re.compile(
    r"(?<![\w:.])(?P<target>(?P<prefix>std\s*::\s*)?(?P<name>next|prev))\s*(?=\()"
)
_CHRONO_CALL_REPLACEMENTS = {
    "floor": "ceil",
    "ceil": "floor",
}
_CHRONO_CALL_RE = re.compile(
    r"(?<![\w:.])(?P<target>(?P<prefix>(?:std\s*::\s*)?chrono\s*::\s*)"
    r"(?P<name>floor|ceil))(?:\s*<[^;\n()]+>)?\s*(?=\()"
)
_REGEX_CALL_REPLACEMENTS = {
    "regex_match": "regex_search",
    "regex_search": "regex_match",
}
_REGEX_CALL_RE = re.compile(
    r"(?<![\w:.])(?P<target>(?P<prefix>std\s*::\s*)?(?P<name>regex_match|regex_search))\s*(?=\()"
)
_FILESYSTEM_CALL_REPLACEMENTS = {
    "exists": "is_empty",
    "is_empty": "exists",
    "is_regular_file": "is_directory",
    "is_directory": "is_regular_file",
}
_FILESYSTEM_CALL_RE = re.compile(
    r"(?<![\w:.])(?P<target>(?P<prefix>(?:std\s*::\s*)?filesystem\s*::\s*)"
    r"(?P<name>exists|is_empty|is_regular_file|is_directory))\s*(?=\()"
)


def _discover_unary_operator_mutations(path: str, line_no: int, code: str) -> list[Mutant]:
    out: list[Mutant] = []
    for match in re.finditer(_TOKEN_PATTERNS["!"], code):
        span_end = _token_binary_right_span_end(code, match.end())
        if span_end is None:
            continue
        source_range = {
            "kind": "TOKEN_UNARY_EXPRESSION",
            "startLine": line_no,
            "startColumn": match.start() + 1,
            "endLine": line_no,
            "endColumn": span_end + 1,
        }
        for mutated in ("", "!!"):
            mut = Mutant("UnaryOperator", path, line_no, match.start(), "!", mutated)
            mut.id = stable_id(mut)
            mut.sourceRange = source_range
            mut.rewriteStrategy = "token-unary-expression"
            out.append(mut)
    for match in _UNARY_SIGN_RE.finditer(code):
        op = match.group("op")
        mutated = "-" if op == "+" else "+"
        mut = Mutant("UnaryOperator", path, line_no, match.start("op"), op, mutated)
        mut.id = stable_id(mut)
        span_end = _token_binary_right_span_end(code, match.end("op"))
        if span_end is not None:
            mut.sourceRange = {
                "kind": "TOKEN_UNARY_EXPRESSION",
                "startLine": line_no,
                "startColumn": match.start("op") + 1,
                "endLine": line_no,
                "endColumn": span_end + 1,
            }
        mut.rewriteStrategy = "token-unary-sign"
        out.append(mut)
    return out


def _update_operator_range_replacements(text: str) -> list[tuple[int, str, str]]:
    out: list[tuple[int, str, str]] = []
    for match in _UPDATE_OPERATOR_RE.finditer(text):
        if match.group("prefix") is not None:
            start = match.start("prefix")
            original = match.group("prefix")
            op_start = match.start("prefix_op") - start
            op = match.group("prefix_op")
        else:
            start = match.start("suffix")
            original = match.group("suffix")
            op_start = match.start("suffix_op") - start
            op = match.group("suffix_op")
        mutated_op = "--" if op == "++" else "++"
        mutated = original[:op_start] + mutated_op + original[op_start + len(op) :]
        out.append((start, original, mutated))
    return out


def _discover_update_operator_mutations(path: str, line_no: int, code: str) -> list[Mutant]:
    out: list[Mutant] = []
    for col, original, mutated in _update_operator_range_replacements(code):
        mut = Mutant("UpdateOperator", path, line_no, col, original, mutated)
        mut.id = stable_id(mut)
        mut.sourceRange = {
            "kind": "TOKEN_UPDATE_EXPRESSION",
            "startLine": line_no,
            "startColumn": col + 1,
            "endLine": line_no,
            "endColumn": col + len(original) + 1,
        }
        mut.rewriteStrategy = "token-update-expression"
        out.append(mut)
    return out


def _discover_move_semantics_mutations(path: str, line_no: int, code: str) -> list[Mutant]:
    out: list[Mutant] = []
    for match in _MOVE_SEMANTICS_CALL_RE.finditer(code):
        replacement = _move_semantics_range_replacement(code[match.start() :])
        if replacement is None:
            continue
        offset, original, mutated = replacement
        mut = Mutant("MoveSemantics", path, line_no, match.start() + offset, original, mutated)
        mut.id = stable_id(mut)
        mut.sourceRange = {
            "kind": "TOKEN_CALL_EXPRESSION",
            "startLine": line_no,
            "startColumn": match.start() + offset + 1,
            "endLine": line_no,
            "endColumn": match.start() + offset + len(original) + 1,
        }
        mut.rewriteStrategy = "token-call-wrapper-removal"
        out.append(mut)
    return out


def _discover_standard_library_call_mutations(path: str, line_no: int, code: str) -> list[Mutant]:
    out: list[Mutant] = []
    for match in _STANDARD_LIBRARY_CALL_RE.finditer(code):
        original_name = f"std::{match.group('name')}"
        replacement_name = _STANDARD_LIBRARY_CALL_REPLACEMENTS.get(original_name)
        if replacement_name is None:
            continue
        replacement = _qualified_call_range_replacement(code, match, replacement_name)
        if replacement is None:
            continue
        col, original, mutated = replacement
        mut = Mutant("StandardLibraryCall", path, line_no, col, original, mutated)
        mut.id = stable_id(mut)
        mut.sourceRange = {
            "kind": "TOKEN_CALL_EXPRESSION",
            "startLine": line_no,
            "startColumn": col + 1,
            "endLine": line_no,
            "endColumn": col + len(original) + 1,
        }
        mut.rewriteStrategy = "token-standard-library-call"
        out.append(mut)
    return out


def _discover_container_call_mutations(path: str, line_no: int, code: str) -> list[Mutant]:
    out: list[Mutant] = []
    for match in _CONTAINER_CALL_RE.finditer(code):
        name = match.group("name")
        replacement_name = _CONTAINER_CALL_REPLACEMENTS.get(name)
        if replacement_name is None:
            continue
        replacement = _member_call_range_replacement(code, match, replacement_name)
        if replacement is None:
            continue
        col, original, mutated = replacement
        mut = Mutant("ContainerCall", path, line_no, col, original, mutated)
        mut.id = stable_id(mut)
        mut.sourceRange = {
            "kind": "TOKEN_CALL_EXPRESSION",
            "startLine": line_no,
            "startColumn": col + 1,
            "endLine": line_no,
            "endColumn": col + len(original) + 1,
        }
        mut.rewriteStrategy = "token-container-call"
        out.append(mut)
    return out


def _discover_container_state_call_mutations(path: str, line_no: int, code: str) -> list[Mutant]:
    out: list[Mutant] = []
    for match in _CONTAINER_STATE_CALL_RE.finditer(code):
        name = match.group("name")
        replacement_name = _CONTAINER_STATE_CALL_REPLACEMENTS.get(name)
        if replacement_name is None:
            continue
        replacement = _member_call_range_replacement(code, match, replacement_name)
        if replacement is None:
            continue
        col, original, mutated = replacement
        mut = Mutant("ContainerStateCall", path, line_no, col, original, mutated)
        mut.id = stable_id(mut)
        mut.sourceRange = {
            "kind": "TOKEN_CALL_EXPRESSION",
            "startLine": line_no,
            "startColumn": col + 1,
            "endLine": line_no,
            "endColumn": col + len(original) + 1,
        }
        mut.rewriteStrategy = "token-container-state-call"
        out.append(mut)
    return out


def _discover_string_call_mutations(path: str, line_no: int, code: str) -> list[Mutant]:
    out: list[Mutant] = []
    for match in _STRING_CALL_RE.finditer(code):
        name = match.group("name")
        replacement_name = _STRING_CALL_REPLACEMENTS.get(name)
        if replacement_name is None:
            continue
        replacement = _member_call_range_replacement(code, match, replacement_name)
        if replacement is None:
            continue
        col, original, mutated = replacement
        mut = Mutant("StringCall", path, line_no, col, original, mutated)
        mut.id = stable_id(mut)
        mut.sourceRange = {
            "kind": "TOKEN_CALL_EXPRESSION",
            "startLine": line_no,
            "startColumn": col + 1,
            "endLine": line_no,
            "endColumn": col + len(original) + 1,
        }
        mut.rewriteStrategy = "token-string-call"
        out.append(mut)
    return out


def _discover_math_call_mutations(path: str, line_no: int, code: str) -> list[Mutant]:
    out: list[Mutant] = []
    for match in _MATH_CALL_RE.finditer(code):
        replacement = _math_call_range_replacement(code[match.start("target") :])
        if replacement is None:
            continue
        offset, original, mutated = replacement
        col = match.start("target") + offset
        mut = Mutant("MathCall", path, line_no, col, original, mutated)
        mut.id = stable_id(mut)
        mut.sourceRange = {
            "kind": "TOKEN_CALL_EXPRESSION",
            "startLine": line_no,
            "startColumn": col + 1,
            "endLine": line_no,
            "endColumn": col + len(original) + 1,
        }
        mut.rewriteStrategy = "token-math-call"
        out.append(mut)
    return out


def _discover_iterator_call_mutations(path: str, line_no: int, code: str) -> list[Mutant]:
    out: list[Mutant] = []
    for match in _ITERATOR_CALL_RE.finditer(code):
        replacement = _iterator_call_range_replacement(code[match.start("target") :])
        if replacement is None:
            continue
        offset, original, mutated = replacement
        col = match.start("target") + offset
        mut = Mutant("IteratorCall", path, line_no, col, original, mutated)
        mut.id = stable_id(mut)
        mut.sourceRange = {
            "kind": "TOKEN_CALL_EXPRESSION",
            "startLine": line_no,
            "startColumn": col + 1,
            "endLine": line_no,
            "endColumn": col + len(original) + 1,
        }
        mut.rewriteStrategy = "token-iterator-call"
        out.append(mut)
    return out


def _discover_chrono_call_mutations(path: str, line_no: int, code: str) -> list[Mutant]:
    out: list[Mutant] = []
    for match in _CHRONO_CALL_RE.finditer(code):
        replacement = _chrono_call_range_replacement(code[match.start("target") :])
        if replacement is None:
            continue
        offset, original, mutated = replacement
        col = match.start("target") + offset
        mut = Mutant("ChronoCall", path, line_no, col, original, mutated)
        mut.id = stable_id(mut)
        mut.sourceRange = {
            "kind": "TOKEN_CALL_EXPRESSION",
            "startLine": line_no,
            "startColumn": col + 1,
            "endLine": line_no,
            "endColumn": col + len(original) + 1,
        }
        mut.rewriteStrategy = "token-chrono-call"
        out.append(mut)
    return out


def _discover_regex_call_mutations(path: str, line_no: int, code: str) -> list[Mutant]:
    out: list[Mutant] = []
    for match in _REGEX_CALL_RE.finditer(code):
        replacement = _regex_call_range_replacement(code[match.start("target") :])
        if replacement is None:
            continue
        offset, original, mutated = replacement
        col = match.start("target") + offset
        mut = Mutant("RegexCall", path, line_no, col, original, mutated)
        mut.id = stable_id(mut)
        mut.sourceRange = {
            "kind": "TOKEN_CALL_EXPRESSION",
            "startLine": line_no,
            "startColumn": col + 1,
            "endLine": line_no,
            "endColumn": col + len(original) + 1,
        }
        mut.rewriteStrategy = "token-regex-call"
        out.append(mut)
    return out


def _discover_filesystem_call_mutations(path: str, line_no: int, code: str) -> list[Mutant]:
    out: list[Mutant] = []
    for match in _FILESYSTEM_CALL_RE.finditer(code):
        replacement = _filesystem_call_range_replacement(code[match.start("target") :])
        if replacement is None:
            continue
        offset, original, mutated = replacement
        col = match.start("target") + offset
        mut = Mutant("FilesystemCall", path, line_no, col, original, mutated)
        mut.id = stable_id(mut)
        mut.sourceRange = {
            "kind": "TOKEN_CALL_EXPRESSION",
            "startLine": line_no,
            "startColumn": col + 1,
            "endLine": line_no,
            "endColumn": col + len(original) + 1,
        }
        mut.rewriteStrategy = "token-filesystem-call"
        out.append(mut)
    return out


def discover(
    repo: str,
    path: str,
    only: set[int] | None,
    enabled: list[str],
    equivalent_suppression: str = "conservative",
    analysis: dict[str, Any] | None = None,
) -> list[Mutant]:
    if not _ensure_supported_source_path(path):
        return []

    full = os.path.join(repo, path)
    with open(full) as f:
        src = f.readlines()
    in_block_comment = False
    muts: list[Mutant] = []
    preserve_string_literals = "StringLiteral" in enabled
    preserve_character_literals = "CharacterLiteral" in enabled
    for i, raw in enumerate(src, start=1):
        if only is not None and i not in only:
            continue
        if "PreprocessorGuard" in enabled:
            muts.extend(_discover_preprocessor_guard_mutations(path, i, raw))
        line_in_block_comment = in_block_comment
        code, in_block_comment = _strip_noncode(
            raw,
            in_block_comment=in_block_comment,
            mask_string_literals=not preserve_string_literals,
            mask_character_literals=not preserve_character_literals,
        )
        string_call_code = code
        if "StringCall" in enabled and not preserve_string_literals:
            string_call_code, _ = _strip_noncode(
                raw,
                in_block_comment=line_in_block_comment,
                mask_string_literals=False,
                mask_character_literals=not preserve_character_literals,
            )
        if "StringLiteral" in enabled:
            for match in re.finditer(_STRING_LITERAL_RE, code):
                original = match.group(0)
                replacement = _mutate_string_literal(original)
                if replacement is None:
                    continue
                mut = Mutant(
                    "StringLiteral",
                    path,
                    i,
                    match.start(),
                    original,
                    replacement,
                )
                mut.id = stable_id(mut)
                muts.append(mut)
        if "CharacterLiteral" in enabled:
            for match in re.finditer(_CHARACTER_LITERAL_RE, code):
                original = match.group(0)
                replacement = _mutate_character_literal(original)
                if replacement is None:
                    continue
                mut = Mutant(
                    "CharacterLiteral",
                    path,
                    i,
                    match.start(),
                    original,
                    replacement,
                )
                mut.id = stable_id(mut)
                muts.append(mut)
        if "FloatingPointLiteral" in enabled:
            for match in re.finditer(_FLOATING_LITERAL_RE, code):
                original = match.group(0)
                replacement = _mutate_floating_literal(original)
                if replacement is None:
                    continue
                mut = Mutant(
                    "FloatingPointLiteral",
                    path,
                    i,
                    match.start(),
                    original,
                    replacement,
                )
                mut.id = stable_id(mut)
                muts.append(mut)
        for mutator in enabled:
            if mutator == "ConditionalExpression":
                for start, original, mutated in _conditional_expression_range_replacements(code):
                    mut = Mutant(
                        "ConditionalExpression",
                        path,
                        i,
                        start,
                        original,
                        mutated,
                    )
                    mut.id = stable_id(mut)
                    muts.append(mut)
                continue
            if mutator == "LoopBoundary":
                muts.extend(_discover_loop_boundary_mutations(path, i, code))
                continue
            if mutator == "LoopCondition":
                muts.extend(_discover_loop_condition_mutations(path, i, code))
                continue
            if mutator == "MemberAccessOperator":
                muts.extend(_discover_member_access_mutations(path, i, code))
                continue
            if mutator == "ExceptionHandling":
                muts.extend(_discover_exception_handling_mutations(path, i, code, raw))
                continue
            if mutator == "ObjCMessageSend":
                muts.extend(_discover_objc_message_send_mutations(path, i, code, raw))
                continue
            if mutator == "MetalAddressSpace":
                muts.extend(_discover_metal_address_space_mutations(path, i, code))
                continue
            if mutator == "UnaryOperator":
                muts.extend(_discover_unary_operator_mutations(path, i, code))
                continue
            if mutator == "UpdateOperator":
                muts.extend(_discover_update_operator_mutations(path, i, code))
                continue
            if mutator == "MoveSemantics":
                muts.extend(_discover_move_semantics_mutations(path, i, code))
                continue
            if mutator == "StandardLibraryCall":
                muts.extend(_discover_standard_library_call_mutations(path, i, code))
                continue
            if mutator == "ContainerCall":
                muts.extend(_discover_container_call_mutations(path, i, code))
                continue
            if mutator == "ContainerStateCall":
                muts.extend(_discover_container_state_call_mutations(path, i, code))
                continue
            if mutator == "StringCall":
                muts.extend(_discover_string_call_mutations(path, i, string_call_code))
                continue
            if mutator == "MathCall":
                muts.extend(_discover_math_call_mutations(path, i, code))
                continue
            if mutator == "IteratorCall":
                muts.extend(_discover_iterator_call_mutations(path, i, code))
                continue
            if mutator == "ChronoCall":
                muts.extend(_discover_chrono_call_mutations(path, i, code))
                continue
            if mutator == "RegexCall":
                muts.extend(_discover_regex_call_mutations(path, i, code))
                continue
            if mutator == "FilesystemCall":
                muts.extend(_discover_filesystem_call_mutations(path, i, code))
                continue
            if mutator == "PreprocessorGuard":
                continue
            if mutator in {"StringLiteral", "CharacterLiteral", "FloatingPointLiteral"}:
                continue
            for orig, new in MUTATORS[mutator]:
                pattern = _TOKEN_PATTERNS.get(orig)
                if pattern is None:
                    continue
                for m in re.finditer(pattern, code):
                    mut = Mutant(mutator, path, i, m.start(), orig, new)
                    mut.id = stable_id(mut)
                    if mutator in MUTANT_SWITCH_EXPRESSION_SPAN_MUTATORS:
                        source_range = _token_binary_expression_source_range(code, i, m.start(), m.end())
                        if source_range is not None:
                            mut.sourceRange = source_range
                            mut.rewriteStrategy = "token-binary-expression"
                    elif mutator == "MetalThreadPosition":
                        mut.sourceRange = {
                            "kind": "TOKEN_METAL_THREAD_POSITION",
                            "startLine": i,
                            "startColumn": m.start() + 1,
                            "endLine": i,
                            "endColumn": m.end() + 1,
                        }
                        mut.rewriteStrategy = "token-metal-thread-position"
                    muts.append(mut)
        if "CallRemoval" in enabled:
            muts.extend(_discover_call_removals(path, i, code, raw))
        if "StatementRemoval" in enabled:
            muts.extend(_discover_statement_removals(path, i, code, raw))
        if "BlockRemoval" in enabled:
            muts.extend(_discover_block_removals(path, i, code, raw))
    return _finalize_discovered_mutants(repo, path, muts, equivalent_suppression, analysis)


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


def _mutant_switch_safe_expression(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and "\n" not in stripped and "\r" not in stripped and ";" not in stripped


def _return_expression(text: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("return "):
        return None
    expression = stripped[len("return ") :].strip()
    return expression if _mutant_switch_safe_expression(expression) else None


def _mutant_switch_safe_statement(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and "\n" not in stripped and "\r" not in stripped and stripped.endswith(";")


def _mutant_switch_safe_statement_removal_original(text: str) -> bool:
    if not _mutant_switch_safe_statement(text):
        return False
    stripped = text.strip()
    prefix = stripped.split(None, 1)[0].strip("*&")
    if prefix in _STATEMENT_REMOVAL_DECLARATION_PREFIXES:
        return False
    if re.match(r"^(?:[A-Za-z_]\w*(?:::\w+)*)\s+[A-Za-z_]\w*\b", stripped):
        return False
    return any(token in stripped for token in ("=", "++", "--", "(", "+", "-", "*", "/", "%", "<<", ">>", "["))


def _mutant_switch_safe_block(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and "\n" not in stripped and "\r" not in stripped and stripped.startswith("{") and stripped.endswith("}")


def _mutant_switch_safe_objc_message(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and "\n" not in stripped and "\r" not in stripped and stripped.startswith("[") and stripped.endswith("]")


def _mutant_switch_direct_guarded_replacement(mut: Mutant) -> str | None:
    guard_id = mutant_switch_guard_id(mut)
    if mut.mutator in MUTANT_SWITCH_EXPRESSION_MUTATORS:
        if not _mutant_switch_safe_expression(mut.original) or not _mutant_switch_safe_expression(mut.mutated):
            return None
        return f'(__stryker_cxx_active("{guard_id}") ? ({mut.mutated}) : ({mut.original}))'
    if mut.mutator == "ReturnValue":
        original_expression = _return_expression(mut.original)
        mutated_expression = _return_expression(mut.mutated)
        if original_expression is None or mutated_expression is None:
            return None
        return (
            f'return (__stryker_cxx_active("{guard_id}") '
            f'? ({mutated_expression}) : ({original_expression}))'
        )
    if mut.mutator == "ExceptionHandling":
        if not _mutant_switch_safe_statement(mut.original) or not _mutant_switch_safe_statement(mut.mutated):
            return None
        return (
            f'if (__stryker_cxx_active("{guard_id}")) '
            f"{{ {mut.mutated.strip()} }} else {{ {mut.original.strip()} }}"
        )
    if mut.mutator == "StatementRemoval":
        if mut.mutated.strip() != ";" or not _mutant_switch_safe_statement_removal_original(mut.original):
            return None
        return (
            f'if (__stryker_cxx_active("{guard_id}")) '
            f"{{ ; }} else {{ {mut.original.strip()} }}"
        )
    if mut.mutator == "BlockRemoval":
        if mut.mutated.strip() != "{}" or not _mutant_switch_safe_block(mut.original):
            return None
        return (
            f'if (__stryker_cxx_active("{guard_id}")) '
            f"{{ }} else {{ {mut.original.strip()} }}"
        )
    if mut.mutator == "ObjCMessageSend":
        if mut.mutated.strip() != "(void)0" or not _mutant_switch_safe_objc_message(mut.original):
            return None
        return (
            f'if (__stryker_cxx_active("{guard_id}")) '
            f"{{ {mut.mutated.strip()}; }} else {{ {mut.original.strip()}; }}"
        )
    return None


def _mutant_switch_expression_span_guarded_edit(
    mut: Mutant,
    lines: list[str],
) -> tuple[int, int, str, str] | None:
    if mut.mutator not in MUTANT_SWITCH_EXPRESSION_SPAN_MUTATORS:
        return None
    source_range = mut.sourceRange
    if not isinstance(source_range, dict):
        return None
    ranged = _single_line_source_range(lines, source_range)
    if ranged is None:
        return None
    line_no, col0, original_expression = ranged
    if line_no != mut.line:
        return None
    rel = mut.col - col0
    if rel < 0 or rel + len(mut.original) > len(original_expression):
        return None
    if original_expression[rel : rel + len(mut.original)] != mut.original:
        return None
    mutated_expression = (
        original_expression[:rel]
        + mut.mutated
        + original_expression[rel + len(mut.original) :]
    )
    if not _mutant_switch_safe_expression(original_expression):
        return None
    if not _mutant_switch_safe_expression(mutated_expression):
        return None
    guard_id = mutant_switch_guard_id(mut)
    replacement = (
        f'(__stryker_cxx_active("{guard_id}") '
        f'? ({mutated_expression}) : ({original_expression}))'
    )
    return line_no - 1, col0, original_expression, replacement


def _mutant_switch_guarded_edit(
    mut: Mutant,
    lines: list[str],
) -> tuple[int, int, str, str] | None:
    direct = _mutant_switch_direct_guarded_replacement(mut)
    if direct is not None:
        return mut.line - 1, mut.col, mut.original, direct
    return _mutant_switch_expression_span_guarded_edit(mut, lines)


def _mutant_switch_is_guardable(mut: Mutant) -> bool:
    if _mutant_switch_direct_guarded_replacement(mut) is not None:
        return True
    return mut.mutator in MUTANT_SWITCH_EXPRESSION_SPAN_MUTATORS and bool(mut.sourceRange)


def _mutant_switch_preamble() -> list[str]:
    return [
        "#ifndef STRYKER_CXX_MUTANT_SWITCH_RUNTIME\n",
        "#define STRYKER_CXX_MUTANT_SWITCH_RUNTIME\n",
        "#include <cstdlib>\n",
        "#include <cstring>\n",
        "static inline bool __stryker_cxx_active(const char* id) {\n",
        f"  const char* active = std::getenv(\"{MUTANT_SWITCH_ACTIVE_ENV}\");\n",
        "  return active != nullptr && std::strcmp(active, id) == 0;\n",
        "}\n",
        "#endif\n",
    ]


def _mutant_switch_edit_fallback_reason(repo: str, mutants: list[Mutant]) -> str | None:
    by_file: dict[str, list[Mutant]] = {}
    unsupported: set[str] = set()
    for mut in mutants:
        if mut.file.endswith(".metal") or not _mutant_switch_is_guardable(mut):
            unsupported.add(mut.mutator)
            continue
        by_file.setdefault(mut.file, []).append(mut)
    if unsupported:
        return "unsupported mutant-switch mutators or files: " + ", ".join(sorted(unsupported))
    for file_name, file_mutants in by_file.items():
        full = os.path.join(repo, file_name)
        try:
            with open(full) as f:
                lines = f.readlines()
        except OSError:
            return f"unsupported mutant-switch source file: {file_name}"
        spans_by_line: dict[int, list[tuple[int, int, str]]] = {}
        for mut in file_mutants:
            edit = _mutant_switch_guarded_edit(mut, lines)
            if edit is None:
                unsupported.add(mut.mutator)
                continue
            line_index, col, original_text, _replacement = edit
            spans_by_line.setdefault(line_index, []).append((col, col + len(original_text), mut.id))
        if unsupported:
            return "unsupported mutant-switch mutators or files: " + ", ".join(sorted(unsupported))
        for line_index, spans in spans_by_line.items():
            ordered = sorted(spans)
            previous_start = -1
            previous_end = -1
            previous_id = ""
            for start, end, mutant_id in ordered:
                if start < previous_end and not (start == previous_start and end == previous_end):
                    return (
                        "overlapping mutant-switch expression spans: "
                        f"{file_name}:{line_index + 1} ({previous_id}, {mutant_id})"
                    )
                previous_start = start
                previous_end = end
                previous_id = mutant_id
    return None


def _mutant_switch_fallback_reason(repo: str, mutants: list[Mutant], args: argparse.Namespace) -> str | None:
    if args.artifact_backend != "source-overlay":
        return "mutant-switch currently requires --artifact-backend source-overlay"
    if args.jobs != 1:
        return "mutant-switch currently requires --jobs 1"
    if args.batch_mutants:
        return "mutant-switch currently owns session batching; disable --batch-mutants"
    if not mutants:
        return None
    return _mutant_switch_edit_fallback_reason(repo, mutants)


def _implied_execution_backend(execution_mode: str, artifact_backend: str) -> str:
    if artifact_backend != "source-overlay":
        return "compiled-artifact"
    if execution_mode == "mutant-switch":
        return "mutant-switch"
    return "source-overlay"


def _resolve_execution_backend(
    requested_backend: str,
    execution_mode: str,
    artifact_backend: str,
    mutant_switch_fallback_reason: str | None = None,
) -> tuple[str, str | None]:
    implied = _implied_execution_backend(execution_mode, artifact_backend)
    if requested_backend == "auto":
        return implied, None
    if requested_backend == "llvm-switch":
        if execution_mode == "mutant-switch" and mutant_switch_fallback_reason is None:
            return "llvm-switch", None
        return (
            implied,
            mutant_switch_fallback_reason
            or f"llvm-switch backend is experimental and unavailable; using {implied}",
        )
    if requested_backend == "compiled-artifact":
        if artifact_backend == "source-overlay":
            return (
                implied,
                "compiled-artifact backend requires --artifact-backend compiled-executable, "
                "compiled-library, or compiled-object; using source-overlay",
            )
        return implied, None
    if requested_backend == "mutant-switch":
        if implied == "mutant-switch":
            return implied, None
        reason = mutant_switch_fallback_reason or "mutant-switch backend could not create a guarded artifact"
        return implied, reason
    if requested_backend == "source-overlay":
        if implied == "source-overlay":
            return implied, None
        return implied, f"source-overlay backend requested but {implied} backend is active"
    return implied, None


def _llvm_switch_project_fallback_reason(project_analysis: dict[str, Any] | None) -> str | None:
    if not isinstance(project_analysis, dict):
        return "llvm-switch requires project analysis evidence"
    build_graph = project_analysis.get("buildGraph")
    compile_database = project_analysis.get("compileDatabase")
    build_systems = project_analysis.get("buildSystems")
    if isinstance(build_graph, dict):
        graph_compile_database = build_graph.get("compileDatabase")
        if isinstance(graph_compile_database, dict) and graph_compile_database.get("present"):
            return None
        ownership_model = str(build_graph.get("ownershipModel", ""))
        if ownership_model in {"compile-database", "build-system-targets", "partial-compile-database"}:
            return None
    if isinstance(compile_database, dict) and compile_database.get("present"):
        return None
    if isinstance(build_systems, list):
        for item in build_systems:
            if not isinstance(item, dict):
                continue
            if item.get("name") in {"cmake", "ctest", "compile-database"}:
                return None
    return "llvm-switch requires compile_commands.json or CMake/CTest ownership evidence"


def _mutant_switch_span_mutated_expression(mut: Mutant, col: int, original_text: str) -> str | None:
    if mut.mutator in MUTANT_SWITCH_EXPRESSION_MUTATORS:
        return mut.mutated
    if mut.mutator in MUTANT_SWITCH_EXPRESSION_SPAN_MUTATORS:
        rel = mut.col - col
        if rel < 0 or rel + len(mut.original) > len(original_text):
            return None
        if original_text[rel : rel + len(mut.original)] != mut.original:
            return None
        mutated = original_text[:rel] + mut.mutated + original_text[rel + len(mut.original) :]
        return mutated if _mutant_switch_safe_expression(mutated) else None
    return None


def _mutant_switch_chained_guarded_replacement(
    original_text: str,
    alternatives: list[tuple[Mutant, str]],
) -> str | None:
    if not _mutant_switch_safe_expression(original_text):
        return None
    expression = f"({original_text})"
    for mut, mutated_expression in reversed(alternatives):
        if not _mutant_switch_safe_expression(mutated_expression):
            return None
        guard_id = mutant_switch_guard_id(mut)
        expression = (
            f'(__stryker_cxx_active("{guard_id}") '
            f"? ({mutated_expression}) : {expression})"
        )
    return expression


def _apply_mutant_switch_overlay(repo: str, mutants: list[Mutant]) -> dict[str, list[str]]:
    originals: dict[str, list[str]] = {}
    by_file: dict[str, list[Mutant]] = {}
    for mut in mutants:
        by_file.setdefault(mut.file, []).append(mut)

    for file_name, file_mutants in by_file.items():
        full = os.path.join(repo, file_name)
        with open(full) as f:
            lines = f.readlines()
        originals[file_name] = list(lines)
        edits: list[tuple[int, int, str, str, Mutant]] = []
        for mut in file_mutants:
            edit = _mutant_switch_guarded_edit(mut, lines)
            if edit is None:
                raise ValueError(f"mutant is not guardable in mutant-switch mode: {mut.id}")
            line_index, col, original_text, replacement = edit
            edits.append((line_index, col, original_text, replacement, mut))
        grouped: dict[tuple[int, int, str], list[tuple[str, Mutant]]] = {}
        for line_index, col, original_text, replacement, mut in edits:
            grouped.setdefault((line_index, col, original_text), []).append((replacement, mut))
        grouped_edits: list[tuple[int, int, str, str, Mutant]] = []
        for (line_index, col, original_text), replacements in grouped.items():
            first_mut = replacements[0][1]
            if len(replacements) == 1:
                grouped_edits.append((line_index, col, original_text, replacements[0][0], first_mut))
                continue
            alternatives: list[tuple[Mutant, str]] = []
            for _replacement, mut in replacements:
                mutated_expression = _mutant_switch_span_mutated_expression(mut, col, original_text)
                if mutated_expression is None:
                    raise ValueError(f"mutant is not chainable in mutant-switch mode: {mut.id}")
                alternatives.append((mut, mutated_expression))
            chained = _mutant_switch_chained_guarded_replacement(original_text, alternatives)
            if chained is None:
                raise ValueError(f"mutant span is not chainable in mutant-switch mode: {first_mut.id}")
            grouped_edits.append((line_index, col, original_text, chained, first_mut))
        for line_index, col, original_text, replacement, mut in sorted(
            grouped_edits,
            key=lambda item: (item[0], item[1], len(item[2])),
            reverse=True,
        ):
            line = lines[line_index]
            actual = line[col : col + len(original_text)]
            if actual != original_text:
                raise ValueError(f"source span mismatch for mutant-switch guard: {mut.id}")
            lines[line_index] = line[:col] + replacement + line[col + len(original_text) :]
        if not any("STRYKER_CXX_MUTANT_SWITCH_RUNTIME" in line for line in lines):
            lines = _mutant_switch_preamble() + lines
        with open(full, "w") as f:
            f.writelines(lines)
    return originals


def _restore_mutant_switch_overlay(repo: str, originals: dict[str, list[str]]) -> None:
    for file_name, lines in originals.items():
        with open(os.path.join(repo, file_name), "w") as f:
            f.writelines(lines)


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


def _unique_ordered(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _batch_selected_test_command(
    batch: list[Mutant],
    fallback_test_cmd: str,
    coverage_test_command_template: str | None,
) -> tuple[str, list[str]]:
    selected_tests = _unique_ordered(
        [
            str(test)
            for mut in batch
            for test in mut.run.get("coveredBy", [])
            if isinstance(mut.run.get("coveredBy"), list)
        ]
    )
    if selected_tests and coverage_test_command_template:
        return _coverage_selected_test_command(coverage_test_command_template, selected_tests), selected_tests

    selected_commands = _unique_ordered(
        [
            str(mut.run.get("selectedTestCommand"))
            for mut in batch
            if mut.run.get("selectedTestCommand")
        ]
    )
    if len(selected_commands) == 1:
        return selected_commands[0], selected_tests
    return fallback_test_cmd, selected_tests


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
            "coverageAnalysis": args.coverage_analysis,
            "executionBackend": getattr(args, "execution_backend", "auto"),
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


def _compile_pruning_state(rep: Report) -> dict[str, Any]:
    state = rep.execution.setdefault("compilePruning", {})
    state.setdefault("enabled", True)
    state.setdefault("strategy", "source-overlay-prune-and-retry")
    state.setdefault("attempts", 0)
    state.setdefault("candidateMutants", 0)
    state.setdefault("failedBatches", 0)
    state.setdefault("retryBatches", 0)
    state.setdefault("prunedMutants", 0)
    state.setdefault("buildErrors", 0)
    state.setdefault("checkErrors", 0)
    state.setdefault("records", [])
    return state


def _record_compile_pruning_attempt(
    rep: Report,
    batch: list[Mutant],
    status: str,
    run: dict[str, Any],
) -> None:
    state = _compile_pruning_state(rep)
    state["attempts"] = int(state.get("attempts", 0)) + 1
    state["candidateMutants"] = int(state.get("candidateMutants", 0)) + len(batch)
    state["failedBatches"] = int(state.get("failedBatches", 0)) + 1
    attempts = state.setdefault("attemptRecords", [])
    if isinstance(attempts, list):
        attempts.append(
            {
                "batchId": run.get("batchId"),
                "status": status,
                "mutantIds": [mut.id for mut in batch],
            }
        )


def _record_compile_pruning_retry(rep: Report, batch: list[Mutant]) -> None:
    state = _compile_pruning_state(rep)
    state["retryBatches"] = int(state.get("retryBatches", 0)) + 1
    retries = state.setdefault("retryRecords", [])
    if isinstance(retries, list):
        retries.append({"mutantIds": [mut.id for mut in batch]})


def _record_compile_pruned_mutant(
    rep: Report,
    mut: Mutant,
    *,
    source: str,
    batch_id: str | None = None,
) -> None:
    if mut.status not in COMPILE_PRUNED_STATUSES:
        return
    state = _compile_pruning_state(rep)
    phase = "build" if mut.status == "BUILD_ERROR" else "check"
    mut.run["compilePruning"] = {
        "pruned": True,
        "phase": phase,
        "source": source,
        "batchId": batch_id,
        "reason": mut.detail,
    }
    mut.run["testSkippedReason"] = "compile-pruned"
    records = state.setdefault("records", [])
    if not isinstance(records, list):
        return
    if any(isinstance(item, dict) and item.get("id") == mut.id for item in records):
        return
    records.append(
        {
            "id": mut.id,
            "file": mut.file,
            "line": mut.line,
            "mutator": mut.mutator,
            "status": mut.status,
            "phase": phase,
            "source": source,
            "batchId": batch_id,
            "reason": mut.detail,
        }
    )
    state["prunedMutants"] = int(state.get("prunedMutants", 0)) + 1
    if mut.status == "BUILD_ERROR":
        state["buildErrors"] = int(state.get("buildErrors", 0)) + 1
    else:
        state["checkErrors"] = int(state.get("checkErrors", 0)) + 1


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


def _find_matching_ternary_colon(text: str, question_pos: int) -> int | None:
    if question_pos < 0 or question_pos >= len(text) or text[question_pos] != "?":
        return None
    ternary_depth = 0
    paren = 0
    bracket = 0
    brace = 0
    for i in range(question_pos + 1, len(text)):
        char = text[i]
        if char == "(":
            paren += 1
        elif char == ")":
            paren = max(0, paren - 1)
        elif char == "[":
            bracket += 1
        elif char == "]":
            bracket = max(0, bracket - 1)
        elif char == "{":
            brace += 1
        elif char == "}":
            brace = max(0, brace - 1)
        elif char == "?":
            ternary_depth += 1
        elif char == ":":
            if paren or bracket or brace:
                continue
            if ternary_depth == 0:
                return i
            ternary_depth -= 1
    return None


def _container_depths_before(text: str, index: int) -> tuple[int, int, int]:
    paren = 0
    bracket = 0
    brace = 0
    for char in text[:index]:
        if char == "(":
            paren += 1
        elif char == ")":
            paren = max(0, paren - 1)
        elif char == "[":
            bracket += 1
        elif char == "]":
            bracket = max(0, bracket - 1)
        elif char == "{":
            brace += 1
        elif char == "}":
            brace = max(0, brace - 1)
    return paren, bracket, brace


def _conditional_expression_false_end(text: str, start: int, *, paren: int = 0, bracket: int = 0, brace: int = 0) -> int:
    ternary_depth = 0
    for i in range(start, len(text)):
        char = text[i]
        if char == "(":
            paren += 1
        elif char == ")":
            paren = max(0, paren - 1)
            if paren == 0 and bracket == 0 and brace == 0:
                return i
        elif char == "[":
            bracket += 1
        elif char == "]":
            bracket = max(0, bracket - 1)
            if paren == 0 and bracket == 0 and brace == 0:
                return i
        elif char == "{":
            brace += 1
        elif char == "}":
            brace = max(0, brace - 1)
            if paren == 0 and bracket == 0 and brace == 0:
                return i
        elif char == "?":
            ternary_depth += 1
        elif char == ":":
            if paren == 0 and bracket == 0 and brace == 0 and ternary_depth > 0:
                ternary_depth -= 1
        elif char in ",;":
            if paren == 0 and bracket == 0:
                return i
    return len(text)


def _conditional_expression_start(
    text: str,
    question: int,
) -> int:
    paren, bracket, brace = _container_depths_before(text, question)
    for i in range(question - 1, -1, -1):
        char = text[i]
        if char == "(":
            paren = max(0, paren - 1)
            if paren == 0 and bracket == 0 and brace == 0:
                return i + 1
            continue
        if char == ")":
            paren += 1
        elif char == "[":
            bracket = max(0, bracket - 1)
            if bracket == 0 and paren == 0 and brace == 0:
                return i + 1
            continue
        elif char == "]":
            bracket += 1
        elif char == "{":
            brace = max(0, brace - 1)
            if brace == 0 and paren == 0 and bracket == 0:
                return i + 1
            continue
        elif char == "}":
            brace += 1
        elif char in ",;" and paren == 0 and bracket == 0 and brace == 0:
            return i + 1
    return 0


def _conditional_expression_range_replacements(
    text: str,
    *,
    include_condition: bool = True,
) -> list[tuple[int, str, str]]:
    out: list[tuple[int, str, str]] = []
    for i, char in enumerate(text):
        if char != "?":
            continue
        colon = _find_matching_ternary_colon(text, i)
        if colon is None:
            continue
        paren, bracket, brace = _container_depths_before(text, i)
        end = _conditional_expression_false_end(
            text,
            colon + 1,
            paren=paren,
            bracket=bracket,
            brace=brace,
        )
        true_expr = text[i + 1 : colon].strip().rstrip(";")
        false_expr = text[colon + 1 : end].strip().rstrip(";")
        if not true_expr.strip() or not false_expr.strip():
            continue
        start = i + 1
        if include_condition:
            start = _conditional_expression_start(text, i)
            condition_span = text[start:i]
            return_prefix = re.match(r"^\s*return\s+", condition_span)
            if return_prefix is not None:
                start += return_prefix.end()
                condition = condition_span[return_prefix.end() :].strip()
            else:
                condition = condition_span.strip()
            if not condition:
                continue
            true_expr = true_expr.strip()
            false_expr = false_expr.strip()
            original = f"{condition} ? {true_expr} : {false_expr}"
            mutated = f"{condition} ? {false_expr} : {true_expr}"
            out.append((start, original, mutated))
        else:
            out.append((i + 1, text[i + 1 : end].strip().rstrip(";"), f"{false_expr} : {true_expr}"))
    return out


_AST_DIRECT_BINARY_MUTATORS = {
    "ConditionalBoundary",
    "EqualityOperator",
    "LogicalOperator",
    "ShiftOperator",
    "ArithmeticOperator",
    "AssignmentOperator",
    "BitwiseOperator",
}
_AST_DIRECT_BINARY_OPERATOR_RE = re.compile(
    r"(?<![!<>=&|+\-*/%^])"
    r"(<<=|>>=|==|!=|&&|\|\||<<|>>|\+=|-=|\*=|/=|%=|&=|\|=|\^=|<=|>=|<|>|\+|-|\*|/|%|&|\||\^)"
    r"(?![!<>=&|+\-*/%^])"
)


def _direct_ast_binary_mutants(
    path: str,
    item: dict[str, int | str],
    line_no: int,
    col0: int,
    text: str,
    enabled: list[str],
) -> list[Mutant]:
    kind = str(item.get("kind", ""))
    enabled_binary = [
        mutator
        for mutator in enabled
        if mutator in _AST_DIRECT_BINARY_MUTATORS
        and kind in AST_MUTATOR_CURSOR_KINDS.get(mutator, set())
    ]
    if not enabled_binary:
        return []
    operator_matches = list(_AST_DIRECT_BINARY_OPERATOR_RE.finditer(text))
    operator_positions = {(match.start(), match.end(), match.group(1)) for match in operator_matches}
    if len(operator_positions) != 1:
        return []
    op_start, _op_end, operator_token = next(iter(operator_positions))
    out: list[Mutant] = []
    for mutator in enabled_binary:
        for original, mutated in MUTATORS[mutator]:
            if original != operator_token:
                continue
            mut = Mutant(mutator, path, line_no, col0 + op_start, original, mutated)
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-binary"
            out.append(mut)
    return out


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

    out.extend(_direct_ast_binary_mutants(path, item, line_no, col0, text, enabled))

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

    if "StatementRemoval" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["StatementRemoval"]:
        if stripped.endswith(";"):
            original = stripped
            mut = Mutant("StatementRemoval", path, line_no, col0 + leading, original, ";")
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-statement"
            out.append(mut)

    if "BlockRemoval" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["BlockRemoval"] and stripped.startswith("{") and stripped.endswith("}"):
        mut = Mutant("BlockRemoval", path, line_no, col0 + leading, stripped, "{}")
        mut.id = stable_id(mut)
        mut.nodeKind = kind
        mut.sourceRange = dict(item)
        mut.rewriteStrategy = "clang-ast-direct-block"
        out.append(mut)

    if "UnaryOperator" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["UnaryOperator"]:
        if stripped.startswith("!") and not stripped.startswith("!="):
            for mutated in ("", "!!"):
                mut = Mutant("UnaryOperator", path, line_no, col0 + leading, "!", mutated)
                mut.id = stable_id(mut)
                mut.nodeKind = kind
                mut.sourceRange = dict(item)
                mut.rewriteStrategy = "clang-ast-direct-unary"
                out.append(mut)

    if (
        "ConditionalExpression" in enabled
        and kind in AST_MUTATOR_CURSOR_KINDS["ConditionalExpression"]
        and ":" in text
    ):
        for start, original, mutated in _conditional_expression_range_replacements(text, include_condition=True):
            mut = Mutant(
                "ConditionalExpression",
                path,
                line_no,
                col0 + leading + start,
                original,
                mutated,
            )
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-conditional"
            out.append(mut)

    if "StandardLibraryCall" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["StandardLibraryCall"]:
        replacement = _standard_library_call_range_replacement(text)
        if replacement is not None:
            token_offset, original, mutated = replacement
            mut = Mutant(
                "StandardLibraryCall",
                path,
                line_no,
                col0 + leading + token_offset,
                original,
                mutated,
            )
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-standard-library-call"
            out.append(mut)

    if "MoveSemantics" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["MoveSemantics"]:
        replacement = _move_semantics_range_replacement(text)
        if replacement is not None:
            token_offset, original, mutated = replacement
            mut = Mutant(
                "MoveSemantics",
                path,
                line_no,
                col0 + leading + token_offset,
                original,
                mutated,
            )
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-call-wrapper"
            out.append(mut)

    if "ContainerCall" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["ContainerCall"]:
        replacement = _container_call_range_replacement(text)
        if replacement is not None:
            token_offset, original, mutated = replacement
            mut = Mutant(
                "ContainerCall",
                path,
                line_no,
                col0 + leading + token_offset,
                original,
                mutated,
            )
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-container-call"
            out.append(mut)

    if "ContainerStateCall" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["ContainerStateCall"]:
        replacement = _container_state_call_range_replacement(text)
        if replacement is not None:
            token_offset, original, mutated = replacement
            mut = Mutant(
                "ContainerStateCall",
                path,
                line_no,
                col0 + leading + token_offset,
                original,
                mutated,
            )
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-container-state-call"
            out.append(mut)

    if "StringCall" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["StringCall"]:
        replacement = _string_call_range_replacement(text)
        if replacement is not None:
            token_offset, original, mutated = replacement
            mut = Mutant(
                "StringCall",
                path,
                line_no,
                col0 + leading + token_offset,
                original,
                mutated,
            )
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-string-call"
            out.append(mut)

    if "MathCall" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["MathCall"]:
        replacement = _math_call_range_replacement(text)
        if replacement is not None:
            token_offset, original, mutated = replacement
            mut = Mutant(
                "MathCall",
                path,
                line_no,
                col0 + leading + token_offset,
                original,
                mutated,
            )
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-math-call"
            out.append(mut)

    if "IteratorCall" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["IteratorCall"]:
        replacement = _iterator_call_range_replacement(text)
        if replacement is not None:
            token_offset, original, mutated = replacement
            mut = Mutant(
                "IteratorCall",
                path,
                line_no,
                col0 + leading + token_offset,
                original,
                mutated,
            )
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-iterator-call"
            out.append(mut)

    if "ChronoCall" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["ChronoCall"]:
        replacement = _chrono_call_range_replacement(text)
        if replacement is not None:
            token_offset, original, mutated = replacement
            mut = Mutant(
                "ChronoCall",
                path,
                line_no,
                col0 + leading + token_offset,
                original,
                mutated,
            )
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-chrono-call"
            out.append(mut)

    if "RegexCall" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["RegexCall"]:
        replacement = _regex_call_range_replacement(text)
        if replacement is not None:
            token_offset, original, mutated = replacement
            mut = Mutant(
                "RegexCall",
                path,
                line_no,
                col0 + leading + token_offset,
                original,
                mutated,
            )
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-regex-call"
            out.append(mut)

    if "FilesystemCall" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["FilesystemCall"]:
        replacement = _filesystem_call_range_replacement(text)
        if replacement is not None:
            token_offset, original, mutated = replacement
            mut = Mutant(
                "FilesystemCall",
                path,
                line_no,
                col0 + leading + token_offset,
                original,
                mutated,
            )
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-filesystem-call"
            out.append(mut)

    if "StringLiteral" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["StringLiteral"]:
        replacement = _string_literal_range_replacement(text)
        if replacement is not None:
            token_offset, original, mutated = replacement
            mut = Mutant(
                "StringLiteral",
                path,
                line_no,
                col0 + leading + token_offset,
                original,
                mutated,
            )
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-literal"
            out.append(mut)

    if "CharacterLiteral" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["CharacterLiteral"]:
        replacement = _character_literal_range_replacement(text)
        if replacement is not None:
            token_offset, original, mutated = replacement
            mut = Mutant(
                "CharacterLiteral",
                path,
                line_no,
                col0 + leading + token_offset,
                original,
                mutated,
            )
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-literal"
            out.append(mut)

    if "IntegerLiteral" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["IntegerLiteral"]:
        replacement = _integer_literal_range_replacement(text)
        if replacement is not None:
            token_offset, original, mutated = replacement
            mut = Mutant(
                "IntegerLiteral",
                path,
                line_no,
                col0 + leading + token_offset,
                original,
                mutated,
            )
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-literal"
            out.append(mut)

    if "NullLiteral" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["NullLiteral"]:
        replacement = _null_literal_range_replacement(text)
        if replacement is not None:
            token_offset, original, mutated = replacement
            mut = Mutant(
                "NullLiteral",
                path,
                line_no,
                col0 + leading + token_offset,
                original,
                mutated,
            )
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-literal"
            out.append(mut)

    if "FloatingPointLiteral" in enabled and kind in AST_MUTATOR_CURSOR_KINDS["FloatingPointLiteral"]:
        replacement = _floating_literal_range_replacement(text)
        if replacement is not None:
            token_offset, original, mutated = replacement
            mut = Mutant(
                "FloatingPointLiteral",
                path,
                line_no,
                col0 + leading + token_offset,
                original,
                mutated,
            )
            mut.id = stable_id(mut)
            mut.nodeKind = kind
            mut.sourceRange = dict(item)
            mut.rewriteStrategy = "clang-ast-direct-literal"
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
    equivalent_suppression: str = "conservative",
) -> list[Mutant]:
    if not _ensure_supported_source_path(path):
        return []
    if mode == "token":
        return discover(repo, path, only, enabled, equivalent_suppression, analysis)
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
                equivalent_suppression,
            )

        token_mutants = discover(repo, path, only, enabled, "off")
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
        return _finalize_discovered_mutants(repo, path, out, equivalent_suppression, analysis)
    return discover(repo, path, only, enabled, equivalent_suppression, analysis)


def _discover_clang_ast_first(
    repo: str,
    path: str,
    only: set[int] | None,
    enabled: list[str],
    ranges: list[dict[str, int | str]],
    macro_ranges: list[dict[str, int | str]] | None = None,
    analysis: dict[str, Any] | None = None,
    equivalent_suppression: str = "conservative",
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
        if _rejects_macro_range(analysis, path, src, item):
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
            if mutator in {"LoopBoundary", "LoopCondition"}:
                for line_no in range(start_line, min(end_line, len(src)) + 1):
                    if only is not None and line_no not in only:
                        continue
                    code = stripped.get(line_no, "")
                    if mutator == "LoopBoundary":
                        loop_mutants = _discover_loop_boundary_mutations(path, line_no, code)
                    else:
                        loop_mutants = _discover_loop_condition_mutations(path, line_no, code)
                    for mut in loop_mutants:
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
            if mutator in {"MemberAccessOperator", "ExceptionHandling", "ObjCMessageSend", "MetalAddressSpace"}:
                for line_no in range(start_line, min(end_line, len(src)) + 1):
                    if only is not None and line_no not in only:
                        continue
                    code = stripped.get(line_no, "")
                    if mutator == "MemberAccessOperator":
                        custom_mutants = _discover_member_access_mutations(path, line_no, code)
                    elif mutator == "ExceptionHandling":
                        custom_mutants = _discover_exception_handling_mutations(path, line_no, code, src[line_no - 1])
                    elif mutator == "ObjCMessageSend":
                        custom_mutants = _discover_objc_message_send_mutations(path, line_no, code, src[line_no - 1])
                    else:
                        custom_mutants = _discover_metal_address_space_mutations(path, line_no, code)
                    for mut in custom_mutants:
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

    return _finalize_discovered_mutants(repo, path, out, equivalent_suppression, analysis)


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
    rec.setdefault("sourcePrecision", _source_precision_for_record(rec))
    return rec


def _source_precision_for_record(mut: dict[str, Any]) -> dict[str, Any]:
    strategy = str(mut.get("rewriteStrategy", "") or "")
    node_kind = str(mut.get("nodeKind", "") or "")
    source_range = mut.get("sourceRange")
    has_range = isinstance(source_range, dict) and bool(source_range)
    range_kind = str(source_range.get("kind", "") or "") if isinstance(source_range, dict) else ""
    if strategy.startswith("clang-ast-direct"):
        kind = "ast-direct"
        confidence = "high"
    elif strategy == "clang-confirmed-token" or node_kind:
        kind = "ast-confirmed-token"
        confidence = "medium"
    elif has_range and strategy.startswith("token-"):
        kind = "token-range"
        confidence = "medium"
    elif has_range:
        kind = "source-range"
        confidence = "medium"
    else:
        kind = "token-only"
        confidence = "low"
    return {
        "kind": kind,
        "confidence": confidence,
        "rewriteStrategy": strategy,
        "nodeKind": node_kind,
        "hasSourceRange": has_range,
        "sourceRangeKind": range_kind,
    }


def _source_precision_summary(mutants: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, int] = {}
    by_strategy: dict[str, int] = {}
    by_range_kind: dict[str, int] = {}
    ranged = 0
    ast_direct = 0
    token_only = 0
    for mut in mutants:
        precision = mut.get("sourcePrecision") if isinstance(mut.get("sourcePrecision"), dict) else _source_precision_for_record(mut)
        kind = str(precision.get("kind", "token-only"))
        strategy = str(precision.get("rewriteStrategy", "") or "")
        range_kind = str(precision.get("sourceRangeKind", "") or "")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if strategy:
            by_strategy[strategy] = by_strategy.get(strategy, 0) + 1
        if range_kind:
            by_range_kind[range_kind] = by_range_kind.get(range_kind, 0) + 1
        if precision.get("hasSourceRange"):
            ranged += 1
        if kind == "ast-direct":
            ast_direct += 1
        if kind == "token-only":
            token_only += 1
    diagnostics = []
    if token_only:
        diagnostics.append(
            {
                "level": "info",
                "code": "token-only-mutants-present",
                "message": "some mutants do not have a sourceRange; use clang-ast where source-range proof is required",
                "count": token_only,
            }
        )
    return {
        "schemaVersion": "stryker-cxx.source-precision.v1",
        "totalMutants": len(mutants),
        "withSourceRange": ranged,
        "astDirectMutants": ast_direct,
        "tokenOnlyMutants": token_only,
        "byKind": dict(sorted(by_kind.items())),
        "byRewriteStrategy": dict(sorted(by_strategy.items())),
        "bySourceRangeKind": dict(sorted(by_range_kind.items())),
        "diagnostics": diagnostics,
    }


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


DISTRIBUTION_MANIFEST_SCHEMA_VERSION = "stryker-cxx.distribution.v1"


def _distribution_manifest_payload(
    rep: Report,
    mutants: list[Mutant],
    args: argparse.Namespace,
    worker_label: str | None,
) -> dict[str, Any]:
    shard_index = args.shard_index or 1
    shard_total = args.shard_total or 1
    artifact_root = args.artifact_dir or os.path.join(rep.repo, "agent_space", "stryker-cxx")

    def mutant_record(mut: Mutant) -> dict[str, Any]:
        rec = _normalize_mutant_record(mut)
        return {
            "id": rec.get("id"),
            "file": rec.get("file"),
            "line": rec.get("line"),
            "column": rec.get("column", rec.get("col", 0)),
            "mutator": rec.get("mutator"),
            "original": rec.get("original"),
            "mutated": rec.get("mutated"),
            "status": rec.get("status", "PENDING"),
        }

    return {
        "schemaVersion": DISTRIBUTION_MANIFEST_SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "tool": rep.tool,
        "toolVersion": TOOL_VERSION,
        "repo": rep.repo,
        "base": rep.base,
        "shard": {
            "index": shard_index,
            "total": shard_total,
            "selectedMutants": len(mutants),
        },
        "worker": {
            "label": worker_label,
            "jobs": args.jobs,
            "worktreeMode": args.worktree_mode,
            "workerTmpDir": args.worker_tmp_dir,
            "artifactDir": artifact_root,
        },
        "execution": {
            "mode": args.mode,
            "executionMode": rep.execution.get("executionMode"),
            "requestedExecutionMode": rep.execution.get("requestedExecutionMode"),
            "artifactBackend": rep.execution.get("artifactBackend"),
            "requestedArtifactBackend": rep.execution.get("requestedArtifactBackend"),
            "artifactFallback": rep.execution.get("artifactFallback"),
            "batchMutants": bool(args.batch_mutants),
            "batchSize": args.batch_size,
        },
        "commands": {
            "build": rep.buildCommand,
            "check": rep.checkCommand,
            "test": rep.testCommand,
        },
        "redaction": _redaction_metadata(),
        "mutants": [mutant_record(mut) for mut in mutants],
    }


def _write_distribution_manifest(
    path: str | None,
    rep: Report,
    mutants: list[Mutant],
    args: argparse.Namespace,
    worker_label: str | None,
) -> None:
    if not path:
        return
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    payload = _redact_report_artifact(
        _distribution_manifest_payload(rep, mutants, args, worker_label)
    )
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


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


def _test_scheduler_metadata(rep: Report, mutants: list[dict[str, Any]]) -> dict[str, Any]:
    return build_test_scheduler_metadata(mutants, rep.execution.get("batching", {}))


def _report_dict(rep: Report, repo: str | None = None, base: str | None = None,
                 threshold: float | None = None, startedAt: str | None = None) -> dict:
    normalized_mutants = [_normalize_mutant_record(mut) for mut in rep.mutants]
    analysis = rep.execution.get("analysis") if isinstance(rep.execution.get("analysis"), dict) else {}
    analysis_engine = analysis.get("engine") if isinstance(analysis, dict) else None
    execution = {
        "mode": rep.execution.get("mode", analysis_engine or "token"),
        "executionMode": rep.execution.get("executionMode", "source-overlay"),
        "worktreeMode": rep.execution.get("worktreeMode", "inplace"),
        "jobs": rep.execution.get("jobs", 1),
    }
    for key, value in rep.execution.items():
        if key not in execution:
            execution[key] = value
    if not isinstance(execution.get("analysis"), dict):
        execution["analysis"] = {"engine": execution.get("mode", "token")}
    elif "engine" not in execution["analysis"]:
        execution["analysis"]["engine"] = execution.get("mode", "token")
    existing_source_precision = execution["analysis"].get("sourcePrecision")
    execution["analysis"]["sourcePrecision"] = (
        _source_precision_summary(normalized_mutants)
        if normalized_mutants or not isinstance(existing_source_precision, dict)
        else existing_source_precision
    )
    execution["testScheduler"] = _test_scheduler_metadata(rep, normalized_mutants)
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
        "toolVersion": TOOL_VERSION,
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
        "projectAnalysis": execution.get("projectAnalysis", {}),
        "mutationArtifact": execution.get("mutationArtifact", {}),
        "artifactPlacement": execution.get("artifactPlacement", {}),
        "compiledArtifacts": [
            mut.get("run", {}).get("compiledArtifact")
            for mut in normalized_mutants
            if isinstance(mut.get("run"), dict) and isinstance(mut.get("run", {}).get("compiledArtifact"), dict)
        ],
        "lifecycle": _lifecycle_metadata(rep, normalized_mutants),
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


def _phase(name: str, status: str, **detail: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, "status": status}
    if detail:
        payload["detail"] = detail
    return payload


def _lifecycle_metadata(rep: Report, mutants: list[dict[str, Any]]) -> dict[str, Any]:
    coverage = rep.coverage or {"enabled": False, "provider": "none"}
    batching = rep.execution.get("batching", {})
    test_scheduler = _test_scheduler_metadata(rep, mutants)
    resource = rep.execution.get("resourceIsolation", {})
    compile_pruning = rep.execution.get("compilePruning", {})
    lifecycle = rep.execution.get("lifecycle", {})
    if isinstance(lifecycle, dict) and lifecycle:
        return lifecycle

    coverage_status = "completed" if coverage.get("enabled") else "notConfigured"
    scheduler = test_scheduler.get("strategy", "batched" if batching.get("enabled") else "per-mutant")
    retained_paths = [
        (
            mut.get("run", {}).get("worktree")
            or mut.get("run", {}).get("retainedWorktree")
            or mut.get("run", {}).get("retainedArtifact")
        )
        for mut in mutants
        if isinstance(mut.get("run"), dict)
        and (
            mut.get("run", {}).get("worktree")
            or mut.get("run", {}).get("retainedWorktree")
            or mut.get("run", {}).get("retainedArtifact")
        )
    ]
    project_analysis = rep.execution.get("projectAnalysis")
    mutation_artifact = rep.execution.get("mutationArtifact")
    artifact_placement = rep.execution.get("artifactPlacement")
    project_analysis_detail = (
        {
            "confidence": project_analysis.get("confidence"),
            "targetFiles": len(project_analysis.get("targetFiles") or []),
            "buildSystems": [
                item.get("name")
                for item in project_analysis.get("buildSystems") or []
                if isinstance(item, dict)
            ],
            "testTargets": len(project_analysis.get("testTargets") or []),
        }
        if isinstance(project_analysis, dict) and project_analysis
        else {
            "targetFiles": len(rep.target_files),
            "source": "explicit-or-file-discovery",
        }
    )
    mutation_artifact_detail = (
        {
            "artifactMode": mutation_artifact.get("mode"),
            "backend": mutation_artifact.get("backend"),
            "implementation": mutation_artifact.get("implementation"),
            "workspacePerMutant": mutation_artifact.get("workspacePerMutant"),
            "parallelSafe": mutation_artifact.get("parallelSafe"),
            "supportsCompiledReplacement": mutation_artifact.get("supportsCompiledReplacement"),
            "runtimeGuardCount": mutation_artifact.get("runtimeGuardCount"),
            "candidateGuardCount": mutation_artifact.get("candidateGuardCount"),
            "activationEnvironment": mutation_artifact.get("activationEnvironment"),
        }
        if isinstance(mutation_artifact, dict) and mutation_artifact
        else {
            "artifactMode": "source-overlay",
            "implementation": rep.execution.get("worktreeMode", "inplace"),
        }
    )
    artifact_mode = mutation_artifact.get("mode") if isinstance(mutation_artifact, dict) else None
    if artifact_mode == "compiled-artifact":
        artifact_model = "compiled-artifact"
        mutation_artifact_status = "compiledArtifact"
    elif artifact_mode == "mutant-switch":
        artifact_model = "mutant-switch"
        mutation_artifact_status = "mutantSwitch"
    else:
        artifact_model = "source-level"
        mutation_artifact_status = "sourceLevel"
    phases = [
        _phase(
            "initialization",
            "completed",
            mode=rep.execution.get("mode", "token"),
            executionMode=rep.execution.get("executionMode", "source-overlay"),
            requestedExecutionMode=rep.execution.get(
                "requestedExecutionMode",
                rep.execution.get("executionMode", "source-overlay"),
            ),
            worktreeMode=rep.execution.get("worktreeMode", "inplace"),
        ),
        _phase(
            "projectAnalysis",
            "partial",
            **project_analysis_detail,
        ),
        _phase(
            "mutationDiscovery",
            "completed",
            discoveredMutants=rep.total,
            engine=rep.execution.get("analysis", {}).get("engine", rep.execution.get("mode", "token"))
            if isinstance(rep.execution.get("analysis"), dict)
            else rep.execution.get("mode", "token"),
        ),
        _phase(
            "mutationArtifact",
            mutation_artifact_status,
            **mutation_artifact_detail,
        ),
        _phase(
            "compilePruning",
            "completed" if isinstance(compile_pruning, dict) and compile_pruning.get("enabled") else "notSupported",
            strategy=compile_pruning.get("strategy") if isinstance(compile_pruning, dict) else None,
            buildErrors=rep.buildError,
            checkErrors=rep.checkErrors,
            prunedMutants=compile_pruning.get("prunedMutants", 0)
            if isinstance(compile_pruning, dict)
            else 0,
            attempts=compile_pruning.get("attempts", 0)
            if isinstance(compile_pruning, dict)
            else 0,
            retryBatches=compile_pruning.get("retryBatches", 0)
            if isinstance(compile_pruning, dict)
            else 0,
        ),
        _phase(
            "coverageAnalysis",
            coverage_status,
            provider=coverage.get("provider", "none"),
            coveredMutants=coverage.get("coveredMutants", 0),
            noCoverageMutants=coverage.get("noCoverageMutants", 0),
            unknownCoverageMutants=coverage.get("unknownCoverageMutants", 0),
            testSelectedMutants=coverage.get("testSelectedMutants", 0),
            testLevel=bool(coverage.get("testLevel")),
        ),
        _phase(
            "testScheduling",
            "completed",
            scheduler=scheduler,
            sessions=test_scheduler.get("sessions", 0),
            batchSessions=test_scheduler.get("batchSessions", 0),
            perMutantSessions=test_scheduler.get("perMutantSessions", 0),
            splitSessions=test_scheduler.get("splitSessions", 0),
            coverageSelectedSessions=test_scheduler.get("coverageSelectedSessions", 0),
            batches=batching.get("batches", 0) if isinstance(batching, dict) else 0,
            splitBatches=batching.get("splitBatches", 0) if isinstance(batching, dict) else 0,
        ),
        _phase(
            "artifactRestoration",
            (
                "mutantSwitch"
                if artifact_model == "mutant-switch"
                else ("compiledArtifact" if artifact_model == "compiled-artifact" else "sourceLevel")
            ),
            restoreOriginals=artifact_placement.get("restoreOriginals")
            if isinstance(artifact_placement, dict)
            else True,
            sourceOverlayRestorePolicy=artifact_placement.get("sourceOverlay", {}).get("restorePolicy")
            if isinstance(artifact_placement, dict) and isinstance(artifact_placement.get("sourceOverlay"), dict)
            else None,
            compiledArtifactsSupported=artifact_placement.get("compiledArtifacts", {}).get("supported")
            if isinstance(artifact_placement, dict) and isinstance(artifact_placement.get("compiledArtifacts"), dict)
            else False,
            retainedWorktrees=bool(resource.get("retainWorktrees")) if isinstance(resource, dict) else False,
            retainedPaths=[str(path) for path in retained_paths if path],
        ),
        _phase("reporting", "completed", nativeReport=True, mutationTestingElements=True),
    ]
    return {
        "schemaVersion": "stryker-cxx.lifecycle.v1",
        "artifactModel": artifact_model,
        "phaseOrder": [phase["name"] for phase in phases],
        "phases": phases,
    }


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
            "rewriteStrategy": mut.get("rewriteStrategy", ""),
            "sourcePrecision": mut.get("sourcePrecision", {}),
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
        "executionMode": rep.execution.get("executionMode", "source-overlay"),
        "strykerCxx": {
            "executionMode": rep.execution.get("executionMode", "source-overlay"),
            "requestedExecutionMode": rep.execution.get(
                "requestedExecutionMode",
                rep.execution.get("executionMode", "source-overlay"),
            ),
            "analysisEngine": (
                rep.execution.get("analysis", {}).get("engine")
                if isinstance(rep.execution.get("analysis"), dict)
                else rep.execution.get("mode", "token")
            ),
            "sourcePrecision": (
                _source_precision_summary([_normalize_mutant_record(mut) for mut in rep.mutants])
                if rep.mutants
                else (
                    rep.execution.get("analysis", {}).get("sourcePrecision")
                    if isinstance(rep.execution.get("analysis"), dict)
                    and isinstance(rep.execution.get("analysis", {}).get("sourcePrecision"), dict)
                    else _source_precision_summary([])
                )
            ),
        },
    }


def _mte_status(status: str) -> str:
    return native_to_mte_status(status)


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
        "## Mutator summary",
        "",
        "| mutator | total | killed | survived | build errors | check errors | no coverage | timeouts | ignored | score |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    summary = _summary([_normalize_mutant_record(mut) for mut in rep.mutants])
    for mutator, bucket in sorted(summary["byMutator"].items()):
        lines.append(
            f"| {mutator} | {bucket.get('total', 0)} | {bucket.get('killed', 0)} | "
            f"{bucket.get('survived', 0)} | {bucket.get('buildErrors', 0)} | "
            f"{bucket.get('checkErrors', 0)} | {bucket.get('noCoverage', 0)} | "
            f"{bucket.get('timeouts', 0)} | {bucket.get('ignored', 0)} | "
            f"{float(bucket.get('score', 0.0)):.2f} |"
        )
    lines.extend([
        "",
        "## Surviving mutants",
    ])
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
        if status == "NO_COVERAGE":
            level = "notice"
        elif status in {"BUILD_ERROR", "CHECK_ERROR", "TIMEOUT"}:
            level = "error"
        else:
            level = "warning"
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
    analysis = rep.execution.get("analysis") if isinstance(rep.execution.get("analysis"), dict) else {}
    source_precision = (
        _source_precision_summary(mutants)
        if mutants or not isinstance(analysis.get("sourcePrecision"), dict)
        else analysis["sourcePrecision"]
    )
    project_analysis = rep.execution.get("projectAnalysis") if isinstance(rep.execution.get("projectAnalysis"), dict) else {}
    build_graph = project_analysis.get("buildGraph") if isinstance(project_analysis.get("buildGraph"), dict) else {}
    dashboard = rep.execution.get("dashboard", {})
    if not isinstance(dashboard, dict):
        dashboard = {}
    retention_days = dashboard.get("retentionDays")
    commit = dashboard.get("commit") or os.environ.get("GITHUB_SHA") or os.environ.get("CI_COMMIT_SHA")
    branch = dashboard.get("branch") or os.environ.get("GITHUB_REF_NAME") or os.environ.get("CI_COMMIT_REF_NAME")
    project = dashboard.get("project") or os.environ.get("GITHUB_REPOSITORY") or os.environ.get("CI_PROJECT_PATH") or rep.repo
    run_id = os.environ.get("GITHUB_RUN_ID") or os.environ.get("CI_PIPELINE_ID")
    build_url = dashboard.get("buildUrl") or os.environ.get("CI_PIPELINE_URL")
    upload = dict(dashboard.get("upload") or {"enabled": False})
    upload.setdefault("enabled", False)
    upload.setdefault("status", "notAttempted" if upload.get("enabled") else "disabled")
    if (
        not build_url
        and os.environ.get("GITHUB_SERVER_URL")
        and os.environ.get("GITHUB_REPOSITORY")
        and os.environ.get("GITHUB_RUN_ID")
    ):
        build_url = (
            f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
            f"/actions/runs/{os.environ['GITHUB_RUN_ID']}"
        )
    return {
        "schemaVersion": "stryker-cxx.dashboard.v1",
        "dashboardVersion": str(dashboard.get("version") or "1"),
        "generatedAt": _utc_now_iso(),
        "tool": rep.tool,
        "toolVersion": TOOL_VERSION,
        "repo": rep.repo,
        "base": rep.base,
        "project": project,
        "branch": branch,
        "commit": commit,
        "runId": run_id,
        "buildUrl": build_url,
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
        "privacy": {
            "sourceFilesIncluded": False,
            "mutantSourceSnippetsIncluded": True,
            "secretValuesRedacted": True,
            "environmentValuesRedacted": True,
        },
        "provenance": {
            "reportSchemaVersion": REPORT_SCHEMA_VERSION,
            "toolVersion": TOOL_VERSION,
            "configHash": (rep.config or {}).get("hash"),
            "configPath": (rep.config or {}).get("path"),
            "ci": {
                "project": project,
                "branch": branch,
                "commit": commit,
                "runId": run_id,
                "buildUrl": build_url,
            },
            "upload": upload,
        },
        "score": rep.score,
        "thresholds": rep.thresholds,
        "thresholdStatus": (rep.thresholds or {}).get("status") if isinstance(rep.thresholds, dict) else None,
        "analysis": {
            "engine": analysis.get("engine", rep.execution.get("mode", "token")),
            "sourcePrecision": source_precision,
        },
        "projectAnalysis": {
            "schemaVersion": project_analysis.get("schemaVersion"),
            "confidence": project_analysis.get("confidence"),
            "buildSystems": [
                item.get("name")
                for item in project_analysis.get("buildSystems") or []
                if isinstance(item, dict) and item.get("name")
            ],
            "buildGraph": {
                "schemaVersion": build_graph.get("schemaVersion"),
                "confidence": build_graph.get("confidence"),
                "ownershipModel": build_graph.get("ownershipModel"),
                "compileDatabase": build_graph.get("compileDatabase"),
                "diagnostics": build_graph.get("diagnostics", []),
            },
        },
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
                "rewriteStrategy": mut.get("rewriteStrategy"),
                "nodeKind": mut.get("nodeKind"),
                "sourcePrecision": mut.get("sourcePrecision"),
            }
            for mut in mutants
        ],
    }


def _write_dashboard_export(path: str, rep: Report) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    dashboard = rep.execution.setdefault("dashboard", {})
    export = dashboard.setdefault("export", {})
    export["enabled"] = True
    export["path"] = path
    export["status"] = "writing"
    export["writtenAt"] = None
    export.pop("bytes", None)
    export.pop("error", None)
    try:
        with open(path, "w") as f:
            json.dump(_redact_report_artifact(_dashboard_payload(rep)), f, indent=2)
        export["status"] = "succeeded"
        export["bytes"] = os.path.getsize(path)
        export["writtenAt"] = _utc_now_iso()
    except Exception as exc:
        export["status"] = "failed"
        export["error"] = str(exc)
        raise


def _upload_dashboard(
    url: str,
    rep: Report,
    auth_token_env: str | None = None,
    auth_header: str | None = None,
) -> int:
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
        return int(response.getcode())


def _record_dashboard_upload_status(
    rep: Report,
    status: str,
    *,
    status_code: int | None = None,
    error: Exception | None = None,
    attempt: int | None = None,
    max_attempts: int | None = None,
    retry_delay_ms: int | None = None,
) -> None:
    dashboard = rep.execution.setdefault("dashboard", {})
    upload = dashboard.setdefault("upload", {})
    upload["status"] = status
    if max_attempts is not None:
        upload["maxAttempts"] = max_attempts
    if retry_delay_ms is not None:
        upload["retryDelayMs"] = retry_delay_ms
    if attempt is not None:
        attempts = upload.setdefault("attempts", [])
        if isinstance(attempts, list):
            attempt_record: dict[str, Any] = {
                "attempt": attempt,
                "status": status,
            }
            if status_code is not None:
                attempt_record["statusCode"] = status_code
            if error is not None:
                attempt_record["error"] = str(error)
            attempts.append(attempt_record)
    if status_code is not None:
        upload["statusCode"] = status_code
    if error is not None:
        upload["error"] = str(error)


def _attempt_dashboard_upload(args: argparse.Namespace, rep: Report) -> Exception | None:
    if not args.dashboard_upload_url:
        _record_dashboard_upload_status(rep, "disabled")
        return None
    retries = max(0, int(getattr(args, "dashboard_upload_retries", 0) or 0))
    retry_delay_ms = max(0, int(getattr(args, "dashboard_upload_retry_delay_ms", 0) or 0))
    max_attempts = retries + 1
    _record_dashboard_upload_status(
        rep,
        "attempting",
        max_attempts=max_attempts,
        retry_delay_ms=retry_delay_ms,
    )
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            status_code = _upload_dashboard(
                args.dashboard_upload_url,
                rep,
                args.dashboard_auth_token_env,
                args.dashboard_auth_header,
            )
        except Exception as exc:
            last_error = exc
            _record_dashboard_upload_status(
                rep,
                "failed",
                error=exc,
                attempt=attempt,
                max_attempts=max_attempts,
                retry_delay_ms=retry_delay_ms,
            )
            if attempt < max_attempts and retry_delay_ms > 0:
                time.sleep(retry_delay_ms / 1000)
            continue
        _record_dashboard_upload_status(
            rep,
            "succeeded",
            status_code=status_code,
            attempt=attempt,
            max_attempts=max_attempts,
            retry_delay_ms=retry_delay_ms,
        )
        return None
    return last_error


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


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ignore_for_compiled_scratch(build_dir: str | None, artifact_root: str | None):
    ignored = {".git", "__pycache__", ".pytest_cache", "node_modules"}
    if build_dir and not os.path.isabs(build_dir):
        ignored.add(os.path.normpath(build_dir).split(os.sep)[0])
    if artifact_root and not os.path.isabs(artifact_root):
        ignored.add(os.path.normpath(artifact_root).split(os.sep)[0])

    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {name for name in names if name in ignored or name.startswith("bazel-")}

    return ignore


def _infer_cmake_executable_target(repo: str) -> str | None:
    path = os.path.join(repo, "CMakeLists.txt")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    match = re.search(r"\badd_executable\s*\(\s*([A-Za-z0-9_.:+-]+)", text, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _find_library_artifact(build_root: str, target: str) -> str | None:
    names = [
        f"lib{target}.dylib",
        f"lib{target}.so",
        f"lib{target}.a",
        f"{target}.dll",
        f"{target}.lib",
    ]
    for current, dirs, files in os.walk(build_root):
        dirs[:] = sorted(d for d in dirs if d not in {"CMakeFiles", "__pycache__"})
        for file_name in sorted(files):
            if file_name in names:
                return os.path.join(current, file_name)
    return None


def _compiled_artifact_target(
    repo: str,
    backend: str,
    build_system: str | None,
    build_dir: str | None,
    build_target: str | None,
    test_binary: str | None,
    artifact_path: str | None = None,
    xcode_scheme: str | None = None,
) -> tuple[str, str, str]:
    build_root = build_dir or ("." if build_system in {"make", "ninja"} else "build")
    build_root_abs = build_root if os.path.isabs(build_root) else os.path.join(repo, build_root)
    if artifact_path:
        original = artifact_path if os.path.isabs(artifact_path) else os.path.join(repo, artifact_path)
        if backend == "compiled-library":
            target = build_target or xcode_scheme
            if not target:
                raise ValueError("compiled-library backend requires --build-target or --xcode-scheme when --artifact-path is used")
            return target, os.path.abspath(original), "library"
        if backend == "compiled-object":
            target = build_target or os.path.splitext(os.path.basename(original))[0]
            return target, os.path.abspath(original), "object"
        if build_system == "bazel" and not build_target:
            raise ValueError("compiled-executable bazel backend requires --build-target and --test-binary or --artifact-path")
        if build_system == "xcodebuild":
            target = build_target or xcode_scheme
            if not target:
                raise ValueError("compiled-executable xcodebuild backend requires --build-target or --xcode-scheme, plus --test-binary or --artifact-path")
            return target, os.path.abspath(original), "executable"
        target = build_target or os.path.splitext(os.path.basename(original))[0]
        return target, os.path.abspath(original), "executable"
    if backend == "compiled-library":
        if not build_target:
            raise ValueError("compiled-library backend requires --build-target for the library target")
        original = _find_library_artifact(build_root_abs, build_target)
        if not original:
            raise ValueError(f"compiled-library backend could not find built library artifact for target {build_target!r}")
        return build_target, os.path.abspath(original), "library"
    if backend == "compiled-object":
        if test_binary:
            original = test_binary if os.path.isabs(test_binary) else os.path.join(repo, test_binary)
            return build_target or os.path.splitext(os.path.basename(original))[0], os.path.abspath(original), "object"
        target = build_target or _infer_cmake_executable_target(repo)
        if not target:
            raise ValueError("compiled-object backend requires --build-target, --test-binary, or a discoverable add_executable target")
        original = os.path.join(repo, build_root, target) if not os.path.isabs(build_root) else os.path.join(build_root, target)
        return target, os.path.abspath(original), "object"
    if build_system == "bazel":
        if not build_target or not test_binary:
            raise ValueError("compiled-executable bazel backend requires --build-target and --test-binary")
        original = test_binary if os.path.isabs(test_binary) else os.path.join(repo, test_binary)
        return build_target, os.path.abspath(original), "executable"
    if build_system == "xcodebuild":
        target = build_target or xcode_scheme
        if not target or not test_binary:
            raise ValueError("compiled-executable xcodebuild backend requires --build-target or --xcode-scheme, plus --test-binary")
        original = test_binary if os.path.isabs(test_binary) else os.path.join(repo, test_binary)
        return target, os.path.abspath(original), "executable"
    if test_binary:
        original = test_binary if os.path.isabs(test_binary) else os.path.join(repo, test_binary)
        return os.path.splitext(os.path.basename(original))[0], os.path.abspath(original), "executable"
    target = build_target or _infer_cmake_executable_target(repo)
    if not target:
        raise ValueError("compiled-executable backend requires --build-target, --test-binary, or a discoverable add_executable target")
    original = os.path.join(repo, build_root, target) if not os.path.isabs(build_root) else os.path.join(build_root, target)
    return target, os.path.abspath(original), "executable"


def _compiled_artifact_scratch_build_dir(
    scratch_root: str,
    scratch_repo: str,
    build_system: str | None,
    build_dir: str | None,
) -> str:
    if build_system == "bazel":
        return scratch_repo
    if build_system in {"make", "ninja"}:
        if not build_dir or build_dir == ".":
            return scratch_repo
        return build_dir if os.path.isabs(build_dir) else os.path.join(scratch_repo, build_dir)
    return os.path.join(scratch_root, "build")


def _compiled_artifact_configure_command(
    scratch_repo: str,
    scratch_build_dir: str,
    build_system: str | None,
) -> str | None:
    if build_system in {"make", "ninja", "bazel", "xcodebuild"}:
        return None
    if build_system == "meson":
        return f"meson setup {shlex.quote(scratch_build_dir)} {shlex.quote(scratch_repo)}"
    return (
        "cmake -S "
        f"{shlex.quote(scratch_repo)} -B {shlex.quote(scratch_build_dir)} "
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"
    )


def _compiled_artifact_build_command(
    scratch_build_dir: str,
    build_system: str | None,
    target: str,
    xcode_options: dict[str, str | None] | None = None,
) -> str:
    if build_system == "make":
        return f"make -C {shlex.quote(scratch_build_dir)} {shlex.quote(target)}"
    if build_system == "ninja":
        return f"ninja -C {shlex.quote(scratch_build_dir)} {shlex.quote(target)}"
    if build_system == "meson":
        return f"meson compile -C {shlex.quote(scratch_build_dir)} {shlex.quote(target)}"
    if build_system == "bazel":
        return f"cd {shlex.quote(scratch_build_dir)} && bazel build {shlex.quote(target)}"
    if build_system == "xcodebuild":
        return _compiled_xcodebuild_command(scratch_build_dir, target, xcode_options or {})
    return f"cmake --build {shlex.quote(scratch_build_dir)} --target {shlex.quote(target)}"


def _compiled_xcodebuild_command(
    scratch_build_dir: str,
    target: str,
    options: dict[str, str | None],
) -> str:
    scratch_repo = str(options.get("scratchRepo") or ".")
    workspace = options.get("workspace")
    project = options.get("project")
    scheme = options.get("scheme")
    configuration = options.get("configuration")
    sdk = options.get("sdk")
    destination = options.get("destination")
    cmd = ["cd", shlex.quote(scratch_repo), "&&", "xcodebuild", "build"]
    if workspace:
        cmd.extend(["-workspace", shlex.quote(str(workspace))])
    if project:
        cmd.extend(["-project", shlex.quote(str(project))])
    if scheme:
        cmd.extend(["-scheme", shlex.quote(str(scheme))])
    else:
        cmd.extend(["-target", shlex.quote(target)])
    if configuration:
        cmd.extend(["-configuration", shlex.quote(str(configuration))])
    if sdk:
        cmd.extend(["-sdk", shlex.quote(str(sdk))])
    if destination:
        cmd.extend(["-destination", shlex.quote(str(destination))])
    cmd.extend([
        f"CONFIGURATION_BUILD_DIR={shlex.quote(scratch_build_dir)}",
        f"SYMROOT={shlex.quote(scratch_build_dir)}",
        f"OBJROOT={shlex.quote(os.path.join(scratch_build_dir, 'obj'))}",
    ])
    return " ".join(cmd)


def _remove_stale_scratch_artifact(
    scratch_build_dir: str,
    target: str,
    original_artifact: str,
    build_system: str | None,
) -> None:
    if build_system not in {"make", "ninja"}:
        return
    for candidate in {
        os.path.join(scratch_build_dir, target),
        os.path.join(scratch_build_dir, os.path.basename(original_artifact)),
    }:
        if os.path.isfile(candidate):
            os.remove(candidate)


_COMPILED_ARTIFACT_PLACEMENT_LOCKS: dict[str, threading.Lock] = {}
_COMPILED_ARTIFACT_PLACEMENT_LOCKS_GUARD = threading.Lock()


def _compiled_artifact_placement_lock(original_artifact: str) -> threading.Lock:
    key = os.path.abspath(original_artifact)
    with _COMPILED_ARTIFACT_PLACEMENT_LOCKS_GUARD:
        lock = _COMPILED_ARTIFACT_PLACEMENT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _COMPILED_ARTIFACT_PLACEMENT_LOCKS[key] = lock
        return lock


def _find_built_artifact(build_dir: str, target: str, original_artifact: str) -> str:
    bazel_relative = _bazel_artifact_relative(target)
    candidates = [
        os.path.join(build_dir, os.path.basename(original_artifact)),
        os.path.join(build_dir, "bazel-bin", os.path.basename(original_artifact)),
    ]
    if not os.path.isabs(target) and not target.startswith("//"):
        candidates.append(os.path.join(build_dir, target))
    if bazel_relative:
        candidates.append(os.path.join(build_dir, "bazel-bin", bazel_relative))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    for current, dirs, files in os.walk(build_dir):
        dirs[:] = sorted(d for d in dirs if d not in {"CMakeFiles", "__pycache__"})
        for file_name in sorted(files):
            if file_name in {os.path.basename(original_artifact), target}:
                return os.path.join(current, file_name)
    raise ValueError(f"compiled artifact backend did not produce artifact for target {target!r}")


def _bazel_artifact_relative(target: str) -> str | None:
    if not target.startswith("//"):
        return None
    label = target[2:]
    if ":" in label:
        package, name = label.split(":", 1)
    else:
        package = label
        name = os.path.basename(label.rstrip("/"))
    if not name:
        return None
    return os.path.join(package, name) if package else name


def _compiled_backend_retain_statuses(retain_worktrees_for: set[str] | None) -> list[str]:
    if retain_worktrees_for is None:
        return ["ALL"]
    if not retain_worktrees_for:
        return []
    return _retain_status_names(retain_worktrees_for)


def _compiled_artifact_kind_for_backend(backend: str) -> str:
    if backend == "compiled-library":
        return "library"
    if backend == "compiled-object":
        return "object"
    return "executable"


def _compile_database_entries(build_dir: str) -> tuple[list[dict[str, Any]], str | None]:
    path = os.path.join(build_dir, "compile_commands.json")
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, json.JSONDecodeError):
        return [], path
    if not isinstance(entries, list):
        return [], path
    return [entry for entry in entries if isinstance(entry, dict)], path


def _compile_entry_source(entry: dict[str, Any]) -> str | None:
    file_name = entry.get("file")
    if not isinstance(file_name, str) or not file_name:
        return None
    directory = entry.get("directory")
    directory = directory if isinstance(directory, str) and directory else os.getcwd()
    return os.path.abspath(file_name if os.path.isabs(file_name) else os.path.join(directory, file_name))


def _compile_entry_object(entry: dict[str, Any]) -> str | None:
    directory = entry.get("directory")
    directory = directory if isinstance(directory, str) and directory else os.getcwd()
    argv: list[str]
    if isinstance(entry.get("arguments"), list):
        argv = [str(item) for item in entry["arguments"]]
    elif isinstance(entry.get("command"), str):
        try:
            argv = shlex.split(str(entry["command"]))
        except ValueError:
            argv = []
    else:
        argv = []
    for index, item in enumerate(argv):
        if item == "-o" and index + 1 < len(argv):
            value = argv[index + 1]
            return os.path.abspath(value if os.path.isabs(value) else os.path.join(directory, value))
        if item.startswith("-o") and len(item) > 2:
            value = item[2:]
            return os.path.abspath(value if os.path.isabs(value) else os.path.join(directory, value))
    return None


def _compiled_object_records(build_dir: str, scratch_repo: str, mutants: list[Mutant]) -> list[dict[str, Any]]:
    entries, database_path = _compile_database_entries(build_dir)
    out: list[dict[str, Any]] = []
    for mut in mutants:
        source = os.path.abspath(os.path.join(scratch_repo, mut.file))
        source_key = os.path.normcase(os.path.realpath(source))
        matched_entry: dict[str, Any] | None = None
        for entry in entries:
            entry_source = _compile_entry_source(entry)
            if entry_source and os.path.normcase(os.path.realpath(entry_source)) == source_key:
                matched_entry = entry
                break
        object_path = _compile_entry_object(matched_entry) if matched_entry else None
        command = matched_entry.get("command") if matched_entry else None
        if command is None and matched_entry and isinstance(matched_entry.get("arguments"), list):
            command = " ".join(shlex.quote(str(item)) for item in matched_entry["arguments"])
        object_hash = _sha256_file(object_path) if object_path and os.path.isfile(object_path) else None
        out.append(
            {
                "mutantId": mut.id,
                "source": mut.file,
                "compileDatabase": database_path,
                "compileCommandFound": matched_entry is not None,
                "compileCommand": command,
                "objectArtifact": object_path,
                "objectHash": object_hash,
                "objectProduced": bool(object_path and os.path.isfile(object_path)),
            }
        )
    return out


def _compiled_artifact_run_metadata(
    *,
    backend: str,
    artifact_kind: str,
    target: str,
    scratch_root: str,
    scratch_repo: str,
    scratch_build_dir: str,
    mutated_artifact: str | None,
    original_artifact: str,
    backup_artifact: str,
    retained: bool,
    original_hash_before: str | None,
    original_hash_after: str | None,
    object_artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schemaVersion": "stryker-cxx.compiled-artifact.v1",
        "backend": backend,
        "kind": artifact_kind,
        "target": target,
        "scratchRoot": scratch_root,
        "scratchRepo": scratch_repo,
        "scratchBuildDir": scratch_build_dir,
        "mutatedArtifact": mutated_artifact,
        "originalArtifact": original_artifact,
        "backupArtifact": backup_artifact,
        "placementPolicy": "swap-file",
        "restorationPolicy": "restore-original-file",
        "sourceCheckoutMutation": False,
        "originalHashBefore": original_hash_before,
        "originalHashAfter": original_hash_after,
        "originalRestored": bool(original_hash_before and original_hash_after and original_hash_before == original_hash_after),
        "retained": retained,
    }
    if retained:
        payload["retainedPath"] = scratch_root
        payload["cleanupGuidance"] = "remove retainedPath when proof capture is complete"
    if object_artifacts is not None:
        payload["objectArtifacts"] = object_artifacts
    return payload


def _run_mutant_switch_session(
    mutants: list[Mutant],
    repo: str,
    build_cmd: str,
    check_cmd: str | None,
    test_cmd: str,
    timeout_seconds: int | None,
    worktree_mode: str,
    artifact_root: str,
    analysis_mode: str,
    skip_tests: bool,
    plugins: list[dict[str, Any]] | None = None,
    worker_tmp_dir: str | None = None,
    retain_worktrees: bool = False,
    retain_worktrees_for: set[str] | None = None,
    worker_label: str | None = None,
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
) -> list[Mutant]:
    if not mutants:
        return []
    os.makedirs(artifact_root, exist_ok=True)
    start_ms = time.perf_counter()
    retain_state = {"retain": False}
    build_log = os.path.join(artifact_root, "mutant_switch_build.log")
    check_log = os.path.join(artifact_root, "mutant_switch_check.log")
    guards = [
        {"mutantId": mut.id, "guardId": mutant_switch_guard_id(mut)}
        for mut in mutants
    ]
    with materialize_mutation_artifact(
        repo,
        worktree_mode,
        worker_tmp_dir=worker_tmp_dir,
        retain_state=retain_state,
        worker_label=worker_label,
    ) as artifact:
        work_repo = artifact.work_repo
        originals: dict[str, list[str]] = {}
        try:
            originals = _apply_mutant_switch_overlay(work_repo, mutants)
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
            check_rc: int | None = None
            check_ms = 0
            if build_rc == 0 and check_cmd:
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

            compile_status = "PENDING"
            compile_detail = ""
            if build_rc == 124:
                compile_status = "TIMEOUT"
                compile_detail = "mutant-switch build timed out"
            elif build_rc != 0:
                compile_status = "BUILD_ERROR"
                compile_detail = "mutant-switch artifact did not compile"
            elif check_rc == 124:
                compile_status = "TIMEOUT"
                compile_detail = "mutant-switch check timed out"
            elif check_rc not in {None, 0}:
                compile_status = "CHECK_ERROR"
                compile_detail = "mutant-switch checker rejected artifact"

            for mut in mutants:
                guard_id = mutant_switch_guard_id(mut)
                mut.run = {
                    **dict(mut.run),
                    "mode": analysis_mode,
                    "executionMode": "mutant-switch",
                    "worktreeMode": worktree_mode,
                    "mutantSwitchGuardId": guard_id,
                    "mutantSwitchActiveEnvironment": MUTANT_SWITCH_ACTIVE_ENV,
                    "mutationArtifact": mutant_switch_artifact_metadata(
                        enabled=True,
                        guard_count=len(guards),
                        guards=guards,
                        activation_environment=MUTANT_SWITCH_ACTIVE_ENV,
                    ),
                    "artifactPlacement": artifact.placement_metadata(),
                    "scheduler": per_mutant_scheduler_record(
                        coverage_selected=bool(mut.run.get("selectedTestCommand")),
                        selected_tests=mut.run.get("coveredBy", []),
                        split_from_batch_id=mut.run.get("splitFromBatchId"),
                        active_mutant=guard_id,
                    ),
                    "buildReturnCode": build_rc,
                    "buildMs": build_ms,
                    "buildLog": build_log,
                }
                if check_rc is not None:
                    mut.run["checkReturnCode"] = check_rc
                    mut.run["checkMs"] = check_ms
                    mut.run["checkLog"] = check_log
                mut.buildLog = build_log
                mut.checkLog = check_log if check_cmd else ""

                if compile_status != "PENDING":
                    mut.status = compile_status
                    mut.detail = compile_detail
                    mut.durationMs = int((time.perf_counter() - start_ms) * 1000)
                    continue

                if skip_tests:
                    mut.status = "SURVIVED"
                    mut.detail = "tests skipped after successful mutant-switch build/check"
                    mut.durationMs = build_ms + check_ms
                    continue

                effective_test_cmd = str(mut.run.get("selectedTestCommand") or test_cmd)
                test_log = os.path.join(artifact_root, f"mutant_switch_test_{_safe_basename(mut.id)}.log")
                active_env = dict(env_overrides or {})
                active_env[MUTANT_SWITCH_ACTIVE_ENV] = guard_id
                test_rc, test_ms = run_cmd(
                    effective_test_cmd,
                    work_repo,
                    test_log,
                    timeout_seconds,
                    "test",
                    plugins,
                    active_env,
                    env_inherit,
                    env_block,
                )
                mut.testLog = test_log
                mut.run["testReturnCode"] = test_rc
                mut.run["testMs"] = test_ms
                mut.run["testCommand"] = effective_test_cmd
                mut.run["testLog"] = test_log
                mut.durationMs = build_ms + check_ms + test_ms
                if test_rc == 124:
                    mut.status = "TIMEOUT"
                    mut.detail = "mutant-switch tests timed out"
                elif test_rc != 0:
                    mut.status = "KILLED"
                    mut.detail = "mutant-switch active mutant killed tests"
                else:
                    mut.status = "SURVIVED"
                    mut.detail = "all targeted tests passed"
        finally:
            retain_current = any(
                _should_retain_worktree(
                    retain_worktrees,
                    retain_worktrees_for,
                    mut.status,
                    work_repo,
                    repo,
                )
                for mut in mutants
            )
            if retain_current:
                retain_state["retain"] = True
                artifact.mark_retained("mutant-switch")
            elif worktree_mode == "inplace" and originals:
                _restore_mutant_switch_overlay(work_repo, originals)
            placement = artifact.placement_metadata()
            placement["mode"] = "mutant-switch"
            placement["mutantSwitch"] = {
                "activationEnvironment": MUTANT_SWITCH_ACTIVE_ENV,
                "guardedSourceOverlay": True,
            }
            for mut in mutants:
                mut.run["artifactPlacement"] = placement
                if retain_current:
                    mut.run["retainedWorktree"] = work_repo
                    mut.run["retainedWorktreeReason"] = "mutant-switch"
                    if worker_label:
                        mut.run["retainedWorktreeLabel"] = worker_label
    return mutants


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
    worker_label: str | None = None,
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
) -> Mutant:
    existing_run = dict(mut.run)
    run_record = dict(existing_run)
    run_record["mode"] = execution_mode
    run_record["worktreeMode"] = worktree_mode
    if worker_label:
        run_record["workerLabel"] = worker_label
    mut.run = run_record
    mut.status = "PENDING"
    mut.detail = ""
    mut.durationMs = 0
    _record_environment_policy(mut.run, env_overrides, env_inherit, env_block)
    start_ms = time.perf_counter()
    retain_state = {"retain": False}
    with materialize_mutation_artifact(
        repo,
        worktree_mode,
        worker_tmp_dir=worker_tmp_dir,
        retain_state=retain_state,
        worker_label=worker_label,
    ) as artifact:
        work_repo = artifact.work_repo
        mut.run["mutationArtifact"] = artifact.run_metadata()
        original = apply_mutant(work_repo, mut)
        build_log = os.path.join(artifact_root, f"build_{_safe_basename(mut.id)}.log")
        check_log = os.path.join(artifact_root, f"check_{_safe_basename(mut.id)}.log")
        test_log = os.path.join(artifact_root, f"test_{_safe_basename(mut.id)}.log")
        mut.buildLog = build_log
        mut.checkLog = check_log
        mut.testLog = test_log
        effective_test_cmd = str(mut.run.get("selectedTestCommand") or test_cmd)
        mut.run["scheduler"] = per_mutant_scheduler_record(
            coverage_selected=bool(mut.run.get("selectedTestCommand")),
            selected_tests=mut.run.get("coveredBy", []),
            split_from_batch_id=mut.run.get("splitFromBatchId"),
        )

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
                artifact.mark_retained(mut.status)
                mut.run["retainedWorktree"] = work_repo
                mut.run["retainedWorktreeReason"] = mut.status
                if worker_label:
                    mut.run["retainedWorktreeLabel"] = worker_label
            else:
                restore(work_repo, mut.file, mut.line, original)
            mut.run["mutationArtifact"] = artifact.run_metadata()
            mut.run["artifactPlacement"] = artifact.placement_metadata()
            mut.durationMs = int((time.perf_counter() - start_ms) * 1000)
    return mut


def _run_mutant_once_compiled_artifact(
    mut: Mutant,
    repo: str,
    build_cmd: str,
    check_cmd: str | None,
    test_cmd: str,
    timeout_seconds: int | None,
    artifact_root: str,
    execution_mode: str,
    skip_tests: bool,
    build_system: str | None,
    artifact_backend: str,
    build_dir: str | None,
    build_target: str | None,
    test_binary: str | None,
    artifact_path: str | None,
    xcode_workspace: str | None = None,
    xcode_project: str | None = None,
    xcode_scheme: str | None = None,
    xcode_configuration: str | None = None,
    xcode_sdk: str | None = None,
    xcode_destination: str | None = None,
    plugins: list[dict[str, Any]] | None = None,
    worker_tmp_dir: str | None = None,
    retain_worktrees: bool = False,
    retain_worktrees_for: set[str] | None = None,
    worker_label: str | None = None,
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
) -> Mutant:
    existing_run = dict(mut.run)
    run_record = dict(existing_run)
    run_record["mode"] = execution_mode
    run_record["artifactBackend"] = artifact_backend
    run_record["worktreeMode"] = "compiled-artifact"
    if worker_label:
        run_record["workerLabel"] = worker_label
    mut.run = run_record
    mut.status = "PENDING"
    mut.detail = ""
    mut.durationMs = 0
    _record_environment_policy(mut.run, env_overrides, env_inherit, env_block)

    start_ms = time.perf_counter()
    target, original_artifact, artifact_kind = _compiled_artifact_target(
        repo,
        artifact_backend,
        build_system,
        build_dir,
        build_target,
        test_binary,
        artifact_path=artifact_path,
        xcode_scheme=xcode_scheme,
    )
    if not os.path.isfile(original_artifact):
        raise ValueError(f"{artifact_backend} backend requires existing built artifact: {original_artifact}")

    scratch_root = tempfile.mkdtemp(prefix="stryker-cxx-compiled-", dir=worker_tmp_dir)
    scratch_repo = os.path.join(scratch_root, "source")
    scratch_build_dir = _compiled_artifact_scratch_build_dir(
        scratch_root,
        scratch_repo,
        build_system,
        build_dir,
    )
    xcode_options = {
        "scratchRepo": scratch_repo,
        "workspace": xcode_workspace,
        "project": xcode_project,
        "scheme": xcode_scheme,
        "configuration": xcode_configuration,
        "sdk": xcode_sdk,
        "destination": xcode_destination,
    }
    backup_artifact = os.path.join(artifact_root, f"original_{_safe_basename(mut.id)}_{os.path.basename(original_artifact)}")
    configure_log = os.path.join(artifact_root, f"compiled_configure_{_safe_basename(mut.id)}.log")
    build_log = os.path.join(artifact_root, f"compiled_build_{_safe_basename(mut.id)}.log")
    check_log = os.path.join(artifact_root, f"compiled_check_{_safe_basename(mut.id)}.log")
    test_log = os.path.join(artifact_root, f"compiled_test_{_safe_basename(mut.id)}.log")
    mut.buildLog = build_log
    mut.checkLog = check_log
    mut.testLog = test_log
    original_hash_before = _sha256_file(original_artifact)
    original_hash_after: str | None = None
    mutated_artifact: str | None = None
    object_artifacts: list[dict[str, Any]] | None = None
    retained = False
    placement_lock: threading.Lock | None = None
    placement_lock_acquired = False

    try:
        shutil.copytree(
            repo,
            scratch_repo,
            ignore=_ignore_for_compiled_scratch(build_dir, artifact_root),
        )
        apply_mutant(scratch_repo, mut)
        _remove_stale_scratch_artifact(
            scratch_build_dir,
            target,
            original_artifact,
            build_system,
        )

        configure_cmd = _compiled_artifact_configure_command(
            scratch_repo,
            scratch_build_dir,
            build_system,
        )
        mut.run["configureCommand"] = configure_cmd
        if configure_cmd:
            configure_rc, configure_ms = run_cmd(
                configure_cmd,
                repo,
                configure_log,
                timeout_seconds,
                "build",
                plugins,
                env_overrides,
                env_inherit,
                env_block,
            )
            mut.run["configureReturnCode"] = configure_rc
            mut.run["configureMs"] = configure_ms
            if configure_rc == 124:
                mut.status = "TIMEOUT"
                mut.detail = f"{artifact_backend} configure timed out"
                return mut
            if configure_rc != 0:
                mut.status = "BUILD_ERROR"
                mut.detail = f"{artifact_backend} configure failed"
                return mut
        else:
            mut.run["configureReturnCode"] = None
            mut.run["configureMs"] = 0

        compiled_build_cmd = _compiled_artifact_build_command(
            scratch_build_dir,
            build_system,
            target,
            xcode_options=xcode_options,
        )
        build_rc, build_ms = run_cmd(
            compiled_build_cmd,
            repo,
            build_log,
            timeout_seconds,
            "build",
            plugins,
            env_overrides,
            env_inherit,
            env_block,
        )
        mut.run["buildCommand"] = compiled_build_cmd
        mut.run["buildReturnCode"] = build_rc
        mut.run["buildMs"] = build_ms
        mut.run["buildProvider"] = _phase_provider_name(plugins, "build")
        if build_rc == 124:
            mut.status = "TIMEOUT"
            mut.detail = f"{artifact_backend} build timed out"
            return mut
        if build_rc != 0:
            mut.status = "BUILD_ERROR"
            mut.detail = f"{artifact_backend} mutant did not compile"
            return mut

        if artifact_backend == "compiled-object":
            object_artifacts = _compiled_object_records(scratch_build_dir, scratch_repo, [mut])
            mut.run["compiledObjects"] = object_artifacts
            if not object_artifacts or not object_artifacts[0].get("objectProduced"):
                mut.status = "BUILD_ERROR"
                mut.detail = "compiled-object backend did not produce a mutated object artifact"
                return mut

        mutated_artifact = _find_built_artifact(scratch_build_dir, target, original_artifact)
        if check_cmd:
            check_rc, check_ms = run_cmd(
                check_cmd,
                scratch_repo,
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
            if check_rc == 124:
                mut.status = "TIMEOUT"
                mut.detail = f"{artifact_backend} check timed out"
                return mut
            if check_rc != 0:
                mut.status = "CHECK_ERROR"
                mut.detail = f"{artifact_backend} checker rejected mutant"
                return mut

        placement_lock = _compiled_artifact_placement_lock(original_artifact)
        lock_wait_start = time.perf_counter()
        placement_lock.acquire()
        placement_lock_acquired = True
        mut.run["artifactPlacementLock"] = {
            "key": os.path.abspath(original_artifact),
            "waitMs": int((time.perf_counter() - lock_wait_start) * 1000),
        }
        shutil.copy2(original_artifact, backup_artifact)
        shutil.copy2(mutated_artifact, original_artifact)
        mut.run["artifactPlaced"] = True
        mut.run["scheduler"] = per_mutant_scheduler_record(
            coverage_selected=bool(mut.run.get("selectedTestCommand")),
            selected_tests=mut.run.get("coveredBy", []),
            split_from_batch_id=mut.run.get("splitFromBatchId"),
            artifact_backend=artifact_backend,
        )
        effective_test_cmd = str(mut.run.get("selectedTestCommand") or test_cmd)
        if skip_tests:
            mut.status = "SURVIVED"
            mut.detail = "tests skipped after successful compiled artifact build/check"
        else:
            test_rc, test_ms = run_cmd(
                effective_test_cmd,
                repo,
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
            if test_rc == 124:
                mut.status = "TIMEOUT"
                mut.detail = f"{artifact_backend} tests timed out"
            elif test_rc != 0:
                mut.status = "KILLED"
            else:
                mut.status = "SURVIVED"
                mut.detail = "all targeted tests passed against compiled artifact"
    finally:
        try:
            if os.path.isfile(backup_artifact):
                shutil.copy2(backup_artifact, original_artifact)
                original_hash_after = _sha256_file(original_artifact)
            elif os.path.isfile(original_artifact):
                original_hash_after = _sha256_file(original_artifact)
        finally:
            if placement_lock_acquired and placement_lock is not None:
                placement_lock.release()
        retain_current = _should_retain_worktree(
            retain_worktrees,
            retain_worktrees_for,
            mut.status,
            scratch_root,
            repo,
        )
        if retain_current:
            retained = True
            mut.run["retainedArtifact"] = scratch_root
            mut.run["retainedArtifactReason"] = mut.status
            if worker_label:
                mut.run["retainedArtifactLabel"] = worker_label
        else:
            shutil.rmtree(scratch_root, ignore_errors=True)
        mut.run["mutationArtifact"] = compiled_mutation_artifact_metadata(
            artifact_backend,
            artifact_kind=artifact_kind,
            worker_tmp_dir=worker_tmp_dir,
            retain_artifacts=retained,
            retain_artifacts_for=[mut.status] if retained else [],
            worker_label=worker_label,
        )
        mut.run["compiledArtifact"] = _compiled_artifact_run_metadata(
            backend=artifact_backend,
            artifact_kind=artifact_kind,
            target=target,
            scratch_root=scratch_root,
            scratch_repo=scratch_repo,
            scratch_build_dir=scratch_build_dir,
            mutated_artifact=mutated_artifact,
            original_artifact=original_artifact,
            backup_artifact=backup_artifact,
            retained=retained,
            original_hash_before=original_hash_before,
            original_hash_after=original_hash_after,
            object_artifacts=object_artifacts,
        )
        mut.run["artifactPlacement"] = {
            **compiled_artifact_placement_policy(
                artifact_backend,
                artifact_kind=artifact_kind,
                artifact_root=artifact_root,
                worker_tmp_dir=worker_tmp_dir,
                retain_artifacts=retained,
                retain_artifacts_for=[mut.status] if retained else [],
                worker_label=worker_label,
            ),
            "workRepo": scratch_repo,
            "workspaceRoot": scratch_root,
            "originalArtifactsRestored": bool(original_hash_after and original_hash_after == original_hash_before),
            "materializedArtifactRetained": retained,
            "materializedArtifactRestored": not retained,
            "retainedPath": scratch_root if retained else None,
        }
        mut.durationMs = int((time.perf_counter() - start_ms) * 1000)
    return mut


def _run_batch_probe_compiled_artifact(
    batch: list[Mutant],
    repo: str,
    check_cmd: str | None,
    test_cmd: str,
    coverage_test_command_template: str | None,
    timeout_seconds: int | None,
    artifact_root: str,
    execution_mode: str,
    skip_tests: bool,
    build_system: str | None,
    artifact_backend: str,
    build_dir: str | None,
    build_target: str | None,
    test_binary: str | None,
    artifact_path: str | None,
    xcode_workspace: str | None = None,
    xcode_project: str | None = None,
    xcode_scheme: str | None = None,
    xcode_configuration: str | None = None,
    xcode_sdk: str | None = None,
    xcode_destination: str | None = None,
    plugins: list[dict[str, Any]] | None = None,
    worker_tmp_dir: str | None = None,
    retain_worktrees: bool = False,
    retain_worktrees_for: set[str] | None = None,
    worker_label: str | None = None,
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
) -> tuple[str, str, int, dict[str, Any]]:
    batch_id = _batch_id(batch)
    effective_test_cmd, selected_tests = _batch_selected_test_command(
        batch,
        test_cmd,
        coverage_test_command_template,
    )
    run: dict[str, Any] = {
        "mode": execution_mode,
        "artifactBackend": artifact_backend,
        "worktreeMode": "compiled-artifact",
        "batchId": batch_id,
        "batchSize": len(batch),
        "scheduler": batch_scheduler_record(
            coverage_selected=bool(selected_tests and coverage_test_command_template),
            selected_tests=selected_tests,
            mutant_ids=[mut.id for mut in batch],
            artifact_backend=artifact_backend,
        ),
    }
    if selected_tests:
        run["coveredBy"] = selected_tests
        run["selectedTestCommand"] = effective_test_cmd
    if worker_label:
        run["workerLabel"] = worker_label
    _record_environment_policy(run, env_overrides, env_inherit, env_block)

    start_ms = time.perf_counter()
    target, original_artifact, artifact_kind = _compiled_artifact_target(
        repo,
        artifact_backend,
        build_system,
        build_dir,
        build_target,
        test_binary,
        artifact_path=artifact_path,
        xcode_scheme=xcode_scheme,
    )
    if not os.path.isfile(original_artifact):
        raise ValueError(f"{artifact_backend} backend requires existing built artifact: {original_artifact}")

    scratch_root = tempfile.mkdtemp(prefix="stryker-cxx-compiled-batch-", dir=worker_tmp_dir)
    scratch_repo = os.path.join(scratch_root, "source")
    scratch_build_dir = _compiled_artifact_scratch_build_dir(
        scratch_root,
        scratch_repo,
        build_system,
        build_dir,
    )
    xcode_options = {
        "scratchRepo": scratch_repo,
        "workspace": xcode_workspace,
        "project": xcode_project,
        "scheme": xcode_scheme,
        "configuration": xcode_configuration,
        "sdk": xcode_sdk,
        "destination": xcode_destination,
    }
    backup_artifact = os.path.join(artifact_root, f"original_batch_{batch_id}_{os.path.basename(original_artifact)}")
    configure_log = os.path.join(artifact_root, f"compiled_batch_configure_{batch_id}.log")
    build_log = os.path.join(artifact_root, f"compiled_batch_build_{batch_id}.log")
    check_log = os.path.join(artifact_root, f"compiled_batch_check_{batch_id}.log")
    test_log = os.path.join(artifact_root, f"compiled_batch_test_{batch_id}.log")
    original_hash_before = _sha256_file(original_artifact)
    original_hash_after: str | None = None
    mutated_artifact: str | None = None
    object_artifacts: list[dict[str, Any]] | None = None
    retained = False
    result: tuple[str, str, int, dict[str, Any]] | None = None
    placement_lock: threading.Lock | None = None
    placement_lock_acquired = False

    try:
        shutil.copytree(
            repo,
            scratch_repo,
            ignore=_ignore_for_compiled_scratch(build_dir, artifact_root),
        )
        for mut in batch:
            apply_mutant(scratch_repo, mut)
        _remove_stale_scratch_artifact(
            scratch_build_dir,
            target,
            original_artifact,
            build_system,
        )

        configure_cmd = _compiled_artifact_configure_command(
            scratch_repo,
            scratch_build_dir,
            build_system,
        )
        run["configureCommand"] = configure_cmd
        run["configureLog"] = configure_log
        if configure_cmd:
            configure_rc, configure_ms = run_cmd(
                configure_cmd,
                repo,
                configure_log,
                timeout_seconds,
                "build",
                plugins,
                env_overrides,
                env_inherit,
                env_block,
            )
            run["configureReturnCode"] = configure_rc
            run["configureMs"] = configure_ms
            if configure_rc == 124:
                result = ("TIMEOUT", f"{artifact_backend} batch configure timed out", int((time.perf_counter() - start_ms) * 1000), run)
                return result
            if configure_rc != 0:
                result = ("BUILD_ERROR", f"{artifact_backend} batch configure failed", int((time.perf_counter() - start_ms) * 1000), run)
                return result
        else:
            run["configureReturnCode"] = None
            run["configureMs"] = 0

        compiled_build_cmd = _compiled_artifact_build_command(
            scratch_build_dir,
            build_system,
            target,
            xcode_options=xcode_options,
        )
        build_rc, build_ms = run_cmd(
            compiled_build_cmd,
            repo,
            build_log,
            timeout_seconds,
            "build",
            plugins,
            env_overrides,
            env_inherit,
            env_block,
        )
        run["buildCommand"] = compiled_build_cmd
        run["buildReturnCode"] = build_rc
        run["buildMs"] = build_ms
        run["buildLog"] = build_log
        run["buildProvider"] = _phase_provider_name(plugins, "build")
        if build_rc == 124:
            result = ("TIMEOUT", f"{artifact_backend} batch build timed out", int((time.perf_counter() - start_ms) * 1000), run)
            return result
        if build_rc != 0:
            result = ("BUILD_ERROR", f"{artifact_backend} batch mutant set did not compile", int((time.perf_counter() - start_ms) * 1000), run)
            return result

        if artifact_backend == "compiled-object":
            object_artifacts = _compiled_object_records(scratch_build_dir, scratch_repo, batch)
            run["compiledObjects"] = object_artifacts
            if not object_artifacts or any(not item.get("objectProduced") for item in object_artifacts):
                result = (
                    "BUILD_ERROR",
                    "compiled-object backend did not produce every mutated object artifact",
                    int((time.perf_counter() - start_ms) * 1000),
                    run,
                )
                return result

        mutated_artifact = _find_built_artifact(scratch_build_dir, target, original_artifact)
        if check_cmd:
            check_rc, check_ms = run_cmd(
                check_cmd,
                scratch_repo,
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
                result = ("TIMEOUT", f"{artifact_backend} batch check timed out", int((time.perf_counter() - start_ms) * 1000), run)
                return result
            if check_rc != 0:
                result = ("CHECK_ERROR", f"{artifact_backend} batch checker rejected mutant set", int((time.perf_counter() - start_ms) * 1000), run)
                return result

        placement_lock = _compiled_artifact_placement_lock(original_artifact)
        lock_wait_start = time.perf_counter()
        placement_lock.acquire()
        placement_lock_acquired = True
        run["artifactPlacementLock"] = {
            "key": os.path.abspath(original_artifact),
            "waitMs": int((time.perf_counter() - lock_wait_start) * 1000),
        }
        shutil.copy2(original_artifact, backup_artifact)
        shutil.copy2(mutated_artifact, original_artifact)
        run["artifactPlaced"] = True
        if skip_tests:
            result = (
                "SURVIVED",
                "tests skipped after successful compiled artifact batch build/check",
                int((time.perf_counter() - start_ms) * 1000),
                run,
            )
            return result

        test_rc, test_ms = run_cmd(
            effective_test_cmd,
            repo,
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
        run["testCommand"] = effective_test_cmd
        if test_rc == 124:
            result = ("TIMEOUT", f"{artifact_backend} batch tests timed out", int((time.perf_counter() - start_ms) * 1000), run)
            return result
        if test_rc != 0:
            result = (
                "KILLED",
                "compiled artifact batch killed by tests; split for attribution",
                int((time.perf_counter() - start_ms) * 1000),
                run,
            )
            return result
        result = (
            "SURVIVED",
            "all targeted tests passed for compiled artifact batch",
            int((time.perf_counter() - start_ms) * 1000),
            run,
        )
        return result
    finally:
        status = result[0] if result else "PENDING"
        try:
            if os.path.isfile(backup_artifact):
                shutil.copy2(backup_artifact, original_artifact)
                original_hash_after = _sha256_file(original_artifact)
            elif os.path.isfile(original_artifact):
                original_hash_after = _sha256_file(original_artifact)
        finally:
            if placement_lock_acquired and placement_lock is not None:
                placement_lock.release()
        retain_current = _should_retain_worktree(
            retain_worktrees,
            retain_worktrees_for,
            status,
            scratch_root,
            repo,
        )
        if retain_current:
            retained = True
            run["retainedArtifact"] = scratch_root
            run["retainedArtifactReason"] = status
            if worker_label:
                run["retainedArtifactLabel"] = worker_label
        else:
            shutil.rmtree(scratch_root, ignore_errors=True)
        run["mutationArtifact"] = compiled_mutation_artifact_metadata(
            artifact_backend,
            artifact_kind=artifact_kind,
            worker_tmp_dir=worker_tmp_dir,
            retain_artifacts=retained,
            retain_artifacts_for=[status] if retained else [],
            worker_label=worker_label,
        )
        run["compiledArtifact"] = _compiled_artifact_run_metadata(
            backend=artifact_backend,
            artifact_kind=artifact_kind,
            target=target,
            scratch_root=scratch_root,
            scratch_repo=scratch_repo,
            scratch_build_dir=scratch_build_dir,
            mutated_artifact=mutated_artifact,
            original_artifact=original_artifact,
            backup_artifact=backup_artifact,
            retained=retained,
            original_hash_before=original_hash_before,
            original_hash_after=original_hash_after,
            object_artifacts=object_artifacts,
        )
        run["artifactPlacement"] = {
            **compiled_artifact_placement_policy(
                artifact_backend,
                artifact_kind=artifact_kind,
                artifact_root=artifact_root,
                worker_tmp_dir=worker_tmp_dir,
                retain_artifacts=retained,
                retain_artifacts_for=[status] if retained else [],
                worker_label=worker_label,
            ),
            "workRepo": scratch_repo,
            "workspaceRoot": scratch_root,
            "originalArtifactsRestored": bool(original_hash_after and original_hash_after == original_hash_before),
            "materializedArtifactRetained": retained,
            "materializedArtifactRestored": not retained,
            "retainedPath": scratch_root if retained else None,
        }


def _compile_probe_mutant(
    mut: Mutant,
    repo: str,
    build_cmd: str,
    check_cmd: str | None,
    timeout_seconds: int | None,
    worktree_mode: str,
    artifact_root: str,
    execution_mode: str,
    plugins: list[dict[str, Any]] | None = None,
    worker_tmp_dir: str | None = None,
    retain_worktrees: bool = False,
    retain_worktrees_for: set[str] | None = None,
    worker_label: str | None = None,
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
    batch_id: str | None = None,
) -> Mutant:
    existing_run = dict(mut.run)
    run_record = dict(existing_run)
    run_record["mode"] = execution_mode
    run_record["worktreeMode"] = worktree_mode
    run_record["compilePruning"] = {
        "candidate": True,
        "batchId": batch_id,
        "testSession": "notStarted",
    }
    if worker_label:
        run_record["workerLabel"] = worker_label
    mut.run = run_record
    mut.status = "PENDING"
    mut.detail = ""
    mut.durationMs = 0
    _record_environment_policy(mut.run, env_overrides, env_inherit, env_block)
    start_ms = time.perf_counter()
    retain_state = {"retain": False}
    with materialize_mutation_artifact(
        repo,
        worktree_mode,
        worker_tmp_dir=worker_tmp_dir,
        retain_state=retain_state,
        worker_label=worker_label,
    ) as artifact:
        work_repo = artifact.work_repo
        mut.run["mutationArtifact"] = artifact.run_metadata()
        original = apply_mutant(work_repo, mut)
        build_log = os.path.join(artifact_root, f"prune_build_{_safe_basename(mut.id)}.log")
        check_log = os.path.join(artifact_root, f"prune_check_{_safe_basename(mut.id)}.log")
        mut.buildLog = build_log
        mut.checkLog = check_log
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
            if build_rc == 124:
                mut.status = "TIMEOUT"
                mut.detail = "build timed out during compile pruning"
            elif build_rc != 0:
                mut.status = "BUILD_ERROR"
                mut.detail = "did not compile during compile pruning"
            elif check_cmd:
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
                if check_rc == 124:
                    mut.status = "TIMEOUT"
                    mut.detail = "check timed out during compile pruning"
                elif check_rc != 0:
                    mut.status = "CHECK_ERROR"
                    mut.detail = "checker rejected mutant during compile pruning"
            if mut.status == "PENDING":
                mut.detail = "passed compile pruning probe"
                mut.run["compilePruning"]["passed"] = True
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
                artifact.mark_retained(mut.status)
                mut.run["retainedWorktree"] = work_repo
                mut.run["retainedWorktreeReason"] = mut.status
                if worker_label:
                    mut.run["retainedWorktreeLabel"] = worker_label
            else:
                restore(work_repo, mut.file, mut.line, original)
            mut.run["mutationArtifact"] = artifact.run_metadata()
            mut.run["artifactPlacement"] = artifact.placement_metadata()
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
        worker_label,
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
        worker_label=worker_label,
        env_overrides=env_overrides,
        env_inherit=env_inherit,
        env_block=env_block,
    )


def _mutants_overlap(a: Mutant, b: Mutant) -> bool:
    return _batch_placement_conflict_reason(a, b) is not None


def _batch_placement_conflict_reason(a: Mutant, b: Mutant) -> str | None:
    if a.file != b.file:
        return None
    if a.mutator in BATCH_ISOLATED_MUTATORS or b.mutator in BATCH_ISOLATED_MUTATORS:
        return "source-structure mutator isolation"
    # Keep batching conservative: nearby edits can shift columns, interact with
    # statement-level replacements, or change neighboring branch/loop behavior.
    if abs(a.line - b.line) <= 1:
        return "same-file adjacent-line isolation"
    return None


def _batch_coverage_key(mut: Mutant) -> tuple[str, ...]:
    covered_by = mut.run.get("coveredBy", [])
    if not isinstance(covered_by, list):
        return ()
    return tuple(sorted(str(test) for test in covered_by))


def _batch_shared_coverage_key(batch: list[Mutant]) -> tuple[str, ...]:
    if not batch:
        return ()
    first = _batch_coverage_key(batch[0])
    if not first:
        return ()
    if all(_batch_coverage_key(mut) == first for mut in batch[1:]):
        return first
    return ()


def _batch_coverage_union(batch: list[Mutant]) -> tuple[str, ...]:
    tests: set[str] = set()
    for mut in batch:
        tests.update(_batch_coverage_key(mut))
    return tuple(sorted(tests))


def _coverage_union_growth(mut: Mutant, batch: list[Mutant]) -> int:
    coverage_key = set(_batch_coverage_key(mut))
    if not coverage_key:
        return 0
    existing = set(_batch_coverage_union(batch))
    return len(existing | coverage_key) - len(existing)


def _batch_candidate_order(mut: Mutant, batches: list[list[Mutant]]) -> list[list[Mutant]]:
    coverage_key = _batch_coverage_key(mut)
    if not coverage_key:
        return batches
    matching = [batch for batch in batches if _batch_shared_coverage_key(batch) == coverage_key]
    non_matching = [batch for batch in batches if _batch_shared_coverage_key(batch) != coverage_key]
    non_matching.sort(key=lambda batch: (_coverage_union_growth(mut, batch), batches.index(batch)))
    return matching + non_matching


def _batch_mutants(mutants: list[Mutant], batch_size: int) -> list[list[Mutant]]:
    if batch_size <= 1:
        for mut in mutants:
            mut.run["batchPlanning"] = {
                "placement": "new-batch",
                "placementReasons": ["batch-size limit"],
            }
        return [[mut] for mut in mutants]
    batches: list[list[Mutant]] = []
    for mut in mutants:
        placed = False
        placement_reasons: set[str] = set()
        for batch in _batch_candidate_order(mut, batches):
            if len(batch) >= batch_size:
                placement_reasons.add("batch-size limit")
                continue
            conflict_reasons = [
                reason
                for existing in batch
                for reason in [_batch_placement_conflict_reason(mut, existing)]
                if reason is not None
            ]
            if conflict_reasons:
                placement_reasons.update(conflict_reasons)
                continue
            coverage_key = _batch_coverage_key(mut)
            shared_coverage_key = _batch_shared_coverage_key(batch)
            coverage_union = _batch_coverage_union(batch)
            coverage_reason = None
            if coverage_key and shared_coverage_key == coverage_key:
                coverage_reason = "coverage-selected affinity"
            elif coverage_key and coverage_union:
                coverage_reason = "coverage-union minimized"
            batch.append(mut)
            mut.run["batchPlanning"] = {
                "placement": "joined-existing-batch",
                "placementReasons": [coverage_reason] if coverage_reason else [],
            }
            placed = True
            break
        if not placed:
            mut.run["batchPlanning"] = {
                "placement": "new-batch" if batches else "seed",
                "placementReasons": sorted(placement_reasons),
            }
            batches.append([mut])
    return batches


def _batch_id(batch: list[Mutant]) -> str:
    return hashlib.sha256(",".join(mut.id for mut in batch).encode("utf-8")).hexdigest()[:12]


def _batch_plan_record(batch_index: int, batch: list[Mutant]) -> dict[str, Any]:
    return {
        "batchIndex": batch_index,
        "batchId": _batch_id(batch),
        "batchSize": len(batch),
        "sessionType": "batch" if len(batch) > 1 else "per-mutant",
        "heuristic": "first-fit non-overlap",
        "placement": [dict(mut.run.get("batchPlanning", {})) for mut in batch],
        "mutantIds": [mut.id for mut in batch],
        "locations": [
            {
                "file": mut.file,
                "line": mut.line,
                "column": mut.col + 1,
                "mutator": mut.mutator,
            }
            for mut in batch
        ],
    }


def _batch_plan(batches: list[list[Mutant]]) -> list[dict[str, Any]]:
    return [_batch_plan_record(index, batch) for index, batch in enumerate(batches, 1)]


def _run_batch_probe(
    batch: list[Mutant],
    repo: str,
    build_cmd: str,
    check_cmd: str | None,
    test_cmd: str,
    coverage_test_command_template: str | None,
    timeout_seconds: int | None,
    worktree_mode: str,
    artifact_root: str,
    execution_mode: str,
    skip_tests: bool,
    plugins: list[dict[str, Any]] | None = None,
    worker_tmp_dir: str | None = None,
    retain_worktrees: bool = False,
    retain_worktrees_for: set[str] | None = None,
    worker_label: str | None = None,
    env_overrides: dict[str, str] | None = None,
    env_inherit: list[str] | None = None,
    env_block: list[str] | None = None,
) -> tuple[str, str, int, dict[str, Any]]:
    batch_id = _batch_id(batch)
    run: dict[str, Any] = {
        "mode": execution_mode,
        "worktreeMode": worktree_mode,
        "batchId": batch_id,
        "batchSize": len(batch),
    }
    effective_test_cmd, selected_tests = _batch_selected_test_command(
        batch,
        test_cmd,
        coverage_test_command_template,
    )
    run["scheduler"] = batch_scheduler_record(
        coverage_selected=bool(selected_tests and coverage_test_command_template),
        selected_tests=selected_tests,
        mutant_ids=[mut.id for mut in batch],
    )
    if selected_tests:
        run["coveredBy"] = selected_tests
        run["selectedTestCommand"] = effective_test_cmd
    if worker_label:
        run["workerLabel"] = worker_label
    _record_environment_policy(run, env_overrides, env_inherit, env_block)
    start_ms = time.perf_counter()
    result: tuple[str, str, int, dict[str, Any]] | None = None
    retain_state = {"retain": False}
    with materialize_mutation_artifact(
        repo,
        worktree_mode,
        worker_tmp_dir=worker_tmp_dir,
        retain_state=retain_state,
        worker_label=worker_label,
    ) as artifact:
        work_repo = artifact.work_repo
        run["mutationArtifact"] = artifact.run_metadata()
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
            run["testReturnCode"] = test_rc
            run["testMs"] = test_ms
            run["testLog"] = test_log
            run["testProvider"] = _phase_provider_name(plugins, "test")
            run["testCommand"] = effective_test_cmd
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
                artifact.mark_retained(status)
                run["retainedWorktree"] = work_repo
                run["retainedWorktreeReason"] = status
                if worker_label:
                    run["retainedWorktreeLabel"] = worker_label
            else:
                for mut, original in reversed(originals):
                    restore(work_repo, mut.file, mut.line, original)
            run["mutationArtifact"] = artifact.run_metadata()
            run["artifactPlacement"] = artifact.placement_metadata()


def _run_batch_task(payload: tuple[Any, ...]) -> tuple[int, list[Mutant], str, str, int, dict[str, Any]]:
    (
        batch_index,
        batch,
        repo,
        build_cmd,
        check_cmd,
        test_cmd,
        coverage_test_command_template,
        timeout_seconds,
        worktree_mode,
        artifact_root,
        execution_mode,
        skip_tests,
        plugins,
        worker_tmp_dir,
        retain_worktrees,
        retain_worktrees_for,
        worker_label,
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
        coverage_test_command_template=coverage_test_command_template,
        timeout_seconds=timeout_seconds,
        worktree_mode=worktree_mode,
        artifact_root=artifact_root,
        execution_mode=execution_mode,
        skip_tests=skip_tests,
        plugins=plugins,
        worker_tmp_dir=worker_tmp_dir,
        retain_worktrees=retain_worktrees,
        retain_worktrees_for=retain_worktrees_for,
        worker_label=worker_label,
        env_overrides=env_overrides,
        env_inherit=env_inherit,
        env_block=env_block,
    )
    return batch_index, batch, status, detail, duration_ms, run


def _run_compiled_batch_task(payload: tuple[Any, ...]) -> tuple[int, list[Mutant], str, str, int, dict[str, Any]]:
    (
        batch_index,
        batch,
        repo,
        check_cmd,
        test_cmd,
        coverage_test_command_template,
        timeout_seconds,
        artifact_root,
        execution_mode,
        skip_tests,
        build_system,
        artifact_backend,
        build_dir,
        build_target,
        test_binary,
        artifact_path,
        xcode_workspace,
        xcode_project,
        xcode_scheme,
        xcode_configuration,
        xcode_sdk,
        xcode_destination,
        plugins,
        worker_tmp_dir,
        retain_worktrees,
        retain_worktrees_for,
        worker_label,
        env_overrides,
        env_inherit,
        env_block,
    ) = payload
    status, detail, duration_ms, run = _run_batch_probe_compiled_artifact(
        batch,
        repo=repo,
        check_cmd=check_cmd,
        test_cmd=test_cmd,
        coverage_test_command_template=coverage_test_command_template,
        timeout_seconds=timeout_seconds,
        artifact_root=artifact_root,
        execution_mode=execution_mode,
        skip_tests=skip_tests,
        build_system=build_system,
        artifact_backend=artifact_backend,
        build_dir=build_dir,
        build_target=build_target,
        test_binary=test_binary,
        artifact_path=artifact_path,
        xcode_workspace=xcode_workspace,
        xcode_project=xcode_project,
        xcode_scheme=xcode_scheme,
        xcode_configuration=xcode_configuration,
        xcode_sdk=xcode_sdk,
        xcode_destination=xcode_destination,
        plugins=plugins,
        worker_tmp_dir=worker_tmp_dir,
        retain_worktrees=retain_worktrees,
        retain_worktrees_for=retain_worktrees_for,
        worker_label=worker_label,
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
    ap.add_argument("--build-system", default=None, choices=["cmake", "ctest", "ninja", "make", "meson", "bazel", "xcodebuild"])
    ap.add_argument("--build-dir", default=None)
    ap.add_argument("--build-target", default=None)
    ap.add_argument("--test-binary", default=None)
    ap.add_argument("--artifact-path", default=None)
    ap.add_argument("--xcode-workspace", default=None, dest="xcode_workspace")
    ap.add_argument("--xcode-project", default=None, dest="xcode_project")
    ap.add_argument("--xcode-scheme", default=None, dest="xcode_scheme")
    ap.add_argument("--xcode-configuration", default=None, dest="xcode_configuration")
    ap.add_argument("--xcode-sdk", default=None, dest="xcode_sdk")
    ap.add_argument("--xcode-destination", default=None, dest="xcode_destination")
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
    ap.add_argument("--coverage-analysis", default="perTest", choices=["off", "all", "perTest", "perTestInIsolation"],
                    help="Stryker-style coverage mode: off, all, perTest, or perTestInIsolation")
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
    ap.add_argument("--dashboard-project", default=None)
    ap.add_argument("--dashboard-branch", default=None)
    ap.add_argument("--dashboard-commit", default=None)
    ap.add_argument("--dashboard-build-url", default=None)
    ap.add_argument("--dashboard-auth-token-env", default=None)
    ap.add_argument("--dashboard-auth-header", default="Authorization")
    ap.add_argument("--dashboard-upload-retries", type=int, default=0)
    ap.add_argument("--dashboard-upload-retry-delay-ms", type=int, default=1000)
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
    ap.add_argument("--equivalent-suppression", default="conservative",
                    choices=sorted(EQUIVALENT_SUPPRESSION_MODES),
                    help="Automatically mark high-confidence equivalent/noisy mutants as IGNORED")
    ap.add_argument("--jobs", type=int, default=1, help="Parallel mutation workers")
    ap.add_argument("--batch-mutants", action="store_true",
                    help="Batch compatible mutants in isolated worktrees and split failed batches for attribution")
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--shard-index", type=int, default=None)
    ap.add_argument("--shard-total", type=int, default=None, help="Split work into N shards")
    ap.add_argument("--distribution-manifest", default=None,
                    help="Write a deterministic shard/work distribution manifest")
    ap.add_argument("--artifact-backend", default="source-overlay", choices=sorted(ARTIFACT_BACKENDS))
    ap.add_argument("--artifact-fallback", default="none", choices=["none", "source-overlay"])
    ap.add_argument("--execution-mode", default="source-overlay", choices=sorted(EXECUTION_MODES))
    ap.add_argument("--execution-backend", default="auto", choices=sorted(EXECUTION_BACKENDS))
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
    ap.add_argument("--worker-label", default=None, dest="worker_label",
                    help="Label retained worker/worktree artifacts for proof or CI grouping")
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--env-inherit", action="append", default=[],
                    help="Comma-separated inherited environment variable allowlist")
    ap.add_argument("--env-block", action="append", default=[],
                    help="Comma-separated inherited environment variable denylist")
    ap.add_argument("--resume", default=None, dest="resume")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--run-mutant-id", default=None)
    args = ap.parse_args(argv)
    requested_artifact_backend = args.artifact_backend
    artifact_fallback_reason: str | None = None

    if args.jobs < 1:
        ap.error("--jobs must be >= 1")
    if args.batch_size < 1:
        ap.error("--batch-size must be >= 1")
    if args.artifact_backend != "source-overlay":
        if args.artifact_backend not in {"compiled-executable", "compiled-library", "compiled-object"}:
            ap.error(f"{args.artifact_backend} backend is specified but not implemented yet; use compiled-executable, compiled-library, or compiled-object")
        allowed_build_systems = {"cmake", "ctest"}
        supported_build_systems = "cmake/ctest"
        if args.artifact_backend == "compiled-executable":
            allowed_build_systems = {"cmake", "ctest", "make", "ninja", "meson", "bazel", "xcodebuild"}
            supported_build_systems = "cmake/ctest/make/ninja/meson/bazel/xcodebuild"
        elif args.artifact_backend == "compiled-library":
            allowed_build_systems = {"cmake", "ctest", "make", "ninja", "meson"}
            supported_build_systems = "cmake/ctest/make/ninja/meson"
            if args.artifact_path:
                allowed_build_systems = {"cmake", "ctest", "make", "ninja", "meson", "bazel", "xcodebuild"}
                supported_build_systems = "cmake/ctest/make/ninja/meson/bazel/xcodebuild"
        elif args.artifact_backend == "compiled-object" and args.artifact_path:
            allowed_build_systems = {"cmake", "ctest", "make", "ninja", "meson", "bazel"}
            supported_build_systems = "cmake/ctest/make/ninja/meson/bazel"
        if args.build_system is None:
            if args.artifact_fallback == "source-overlay":
                artifact_fallback_reason = (
                    f"--artifact-backend {args.artifact_backend} requires explicit "
                    f"--build-system {supported_build_systems}; falling back to source-overlay"
                )
                args.artifact_backend = "source-overlay"
            else:
                ap.error(
                    f"--artifact-backend {args.artifact_backend} requires explicit "
                    f"--build-system {supported_build_systems}"
                )
        elif args.build_system not in allowed_build_systems:
            if args.artifact_fallback == "source-overlay":
                if args.artifact_backend == "compiled-executable":
                    supported = "cmake/ctest/make/ninja/meson/bazel/xcodebuild"
                elif args.artifact_backend == "compiled-library":
                    supported = "cmake/ctest/make/ninja/meson"
                    if args.artifact_path:
                        supported = f"{supported}/bazel/xcodebuild"
                elif args.artifact_backend == "compiled-object" and args.artifact_path:
                    supported = "cmake/ctest/make/ninja/meson/bazel"
                else:
                    supported = "cmake/ctest"
                artifact_fallback_reason = (
                    f"--artifact-backend {args.artifact_backend} does not support "
                    f"--build-system {args.build_system}; falling back to source-overlay "
                    f"(supported: {supported})"
                )
                args.artifact_backend = "source-overlay"
            else:
                if args.artifact_backend == "compiled-executable":
                    ap.error(
                        "--artifact-backend compiled-executable currently requires "
                        "--build-system cmake/ctest/make/ninja/meson/bazel/xcodebuild"
                    )
                if args.artifact_backend == "compiled-library":
                    ap.error(
                        "--artifact-backend compiled-library currently requires "
                        "--build-system cmake/ctest/make/ninja/meson "
                        "(or bazel/xcodebuild with --artifact-path)"
                    )
                if args.artifact_backend == "compiled-object" and args.artifact_path:
                    ap.error(
                        "--artifact-backend compiled-object with --artifact-path currently requires "
                        "--build-system cmake/ctest/make/ninja/meson/bazel"
                    )
                ap.error(f"--artifact-backend {args.artifact_backend} currently requires --build-system cmake/ctest")
        if args.jobs > 1 and not args.batch_mutants:
            if args.artifact_fallback == "source-overlay":
                artifact_fallback_reason = (
                    f"--artifact-backend {args.artifact_backend} currently requires "
                    "--batch-mutants for --jobs > 1; falling back to source-overlay"
                )
                args.artifact_backend = "source-overlay"
            else:
                ap.error(f"--artifact-backend {args.artifact_backend} currently requires --batch-mutants for --jobs > 1")
    if args.batch_mutants and args.worktree_mode == "inplace" and args.artifact_backend == "source-overlay":
        ap.error("--batch-mutants requires --worktree-mode copy or git-worktree")
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
    if args.dashboard_upload_retries < 0:
        ap.error("--dashboard-upload-retries must be >= 0")
    if args.dashboard_upload_retry_delay_ms < 0:
        ap.error("--dashboard-upload-retry-delay-ms must be >= 0")
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
    worker_label = _safe_worker_label(args.worker_label) or None

    if args.format == "json" and args.output_format == "legacy":
        output_mode = "legacy"
    else:
        output_mode = args.output_format

    try:
        plugins = load_plugins(args.plugin, args.plugin_dir)
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

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

    if args.jobs > 1 and args.worktree_mode == "inplace" and args.artifact_backend == "source-overlay":
        if not args.quiet:
            print("[error] --jobs > 1 requires --worktree-mode copy or git-worktree to avoid workspace races")
        raise ValueError("cannot run parallel in-place mutation")

    artifact_root = args.artifact_dir or os.path.join(repo, "agent_space", "stryker-cxx")
    requested_execution_backend = args.execution_backend
    requested_execution_mode = "mutant-switch" if requested_execution_backend in {"mutant-switch", "llvm-switch"} else args.execution_mode
    actual_execution_mode = requested_execution_mode
    mutant_switch_fallback_reason: str | None = None
    actual_execution_backend, execution_backend_fallback_reason = _resolve_execution_backend(
        requested_execution_backend,
        actual_execution_mode,
        args.artifact_backend,
        mutant_switch_fallback_reason,
    )
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
            "executionMode": actual_execution_mode,
            "requestedExecutionMode": requested_execution_mode,
            "executionBackend": actual_execution_backend,
            "requestedExecutionBackend": requested_execution_backend,
            "executionBackendFallbackReason": execution_backend_fallback_reason,
            "artifactBackend": args.artifact_backend,
            "requestedArtifactBackend": requested_artifact_backend,
            "artifactPath": args.artifact_path,
            "artifactFallback": args.artifact_fallback,
            "artifactFallbackReason": artifact_fallback_reason,
            "worktreeMode": args.worktree_mode,
            "jobs": args.jobs,
            "initialTest": not args.skip_initial_test,
            "dryRunOnly": args.dry_run_only,
            "skipTests": args.skip_tests,
            "analysis": {
                "engine": args.mode,
                "macroRejectedMutants": 0,
                "macroRejections": [],
                "macroRejectedRanges": 0,
                "macroRangeRejections": [],
                "equivalentSuppression": {
                    "mode": args.equivalent_suppression,
                    "suppressedMutants": 0,
                    "suppressions": [],
                },
            },
            "mutantSwitch": {
                "enabled": actual_execution_mode == "mutant-switch",
                "requested": requested_execution_mode == "mutant-switch",
                "fallbackReason": mutant_switch_fallback_reason,
                "activationEnvironment": MUTANT_SWITCH_ACTIVE_ENV,
                "runtimeGuardCount": 0,
            },
            "llvmSwitch": {
                "enabled": False,
                "requested": requested_execution_backend == "llvm-switch",
                "implementation": "guarded-source-switch",
                "fallbackReason": execution_backend_fallback_reason,
                "requires": [
                    "compile_commands.json or CMake/CTest ownership evidence",
                    "mutant-switch guardable source ranges",
                ],
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
                "plan": [],
                "heuristics": [
                    "same-file adjacent-line isolation",
                    "source-structure mutator isolation",
                    "failed batch split attribution",
                ],
            },
            "distribution": {
                "schemaVersion": DISTRIBUTION_MANIFEST_SCHEMA_VERSION,
                "manifestPath": args.distribution_manifest,
                "shardIndex": args.shard_index or 1,
                "shardTotal": args.shard_total or 1,
                "selectedMutants": 0,
                "workerLabel": worker_label,
            },
            "compilePruning": {
                "enabled": True,
                "strategy": "source-overlay-prune-and-retry"
                if args.artifact_backend == "source-overlay"
                else "compiled-artifact-prune-and-retry",
                "candidateArtifactMode": args.artifact_backend,
                "attempts": 0,
                "candidateMutants": 0,
                "failedBatches": 0,
                "retryBatches": 0,
                "prunedMutants": 0,
                "buildErrors": 0,
                "checkErrors": 0,
                "records": [],
            },
            "dashboard": {
                "version": args.dashboard_version,
                "retentionDays": args.dashboard_retention_days,
                "project": args.dashboard_project,
                "branch": args.dashboard_branch,
                "commit": args.dashboard_commit,
                "buildUrl": args.dashboard_build_url,
                "exportPath": args.dashboard_export,
                "export": {
                    "enabled": bool(args.dashboard_export),
                    "path": args.dashboard_export,
                    "status": "notAttempted" if args.dashboard_export else "disabled",
                    "bytes": None,
                    "writtenAt": None,
                },
                "upload": {
                    "enabled": bool(args.dashboard_upload_url),
                    "urlConfigured": bool(args.dashboard_upload_url),
                    "status": "notAttempted" if args.dashboard_upload_url else "disabled",
                    "maxAttempts": args.dashboard_upload_retries + 1 if args.dashboard_upload_url else 0,
                    "retryDelayMs": args.dashboard_upload_retry_delay_ms,
                    "attempts": [],
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
                "workspacePerMutant": (
                    args.worktree_mode in {"copy", "git-worktree"}
                    or args.artifact_backend != "source-overlay"
                ),
                "parallelSafe": args.worktree_mode != "inplace" or args.artifact_backend != "source-overlay",
                "workerCount": args.jobs,
                "artifactDir": artifact_root,
                "retainWorktrees": retain_worktrees,
                "retainWorktreesFor": _retain_status_names(retain_worktrees_for)
                if retain_worktrees
                else [],
                "retainedWorktreeTtlHours": args.retained_worktree_ttl_hours,
                "retainedWorktreeCleanup": retained_worktree_cleanup,
                "workerTmpDir": args.worker_tmp_dir,
                "workerLabel": worker_label,
                "environmentKeys": sorted(env_overrides),
                "environmentInheritedKeys": sorted(env_inherit) if env_inherit is not None else ["*"],
                "environmentBlockedKeys": sorted(env_block),
                "redaction": _redaction_metadata(),
                "network": "core-offline; explicit dashboard URLs and plugin commands may use network",
            },
            "pluginLifecycle": _plugin_lifecycle_metadata(plugins),
            "mutationArtifact": (
                compiled_mutation_artifact_metadata(
                    args.artifact_backend,
                    artifact_kind=_compiled_artifact_kind_for_backend(args.artifact_backend),
                    worker_tmp_dir=args.worker_tmp_dir,
                    retain_artifacts=retain_worktrees,
                    retain_artifacts_for=_compiled_backend_retain_statuses(retain_worktrees_for)
                    if retain_worktrees
                    else [],
                    worker_label=worker_label,
                )
                if args.artifact_backend != "source-overlay"
                else mutation_artifact_metadata(
                    args.worktree_mode,
                    worker_tmp_dir=args.worker_tmp_dir,
                    retain_worktrees=retain_worktrees,
                    retain_worktrees_for=_retain_status_names(retain_worktrees_for)
                    if retain_worktrees
                    else [],
                    worker_label=worker_label,
                )
            ),
            "artifactPlacement": (
                compiled_artifact_placement_policy(
                    args.artifact_backend,
                    artifact_kind=_compiled_artifact_kind_for_backend(args.artifact_backend),
                    artifact_root=artifact_root,
                    worker_tmp_dir=args.worker_tmp_dir,
                    retain_artifacts=retain_worktrees,
                    retain_artifacts_for=_compiled_backend_retain_statuses(retain_worktrees_for)
                    if retain_worktrees
                    else [],
                    worker_label=worker_label,
                )
                if args.artifact_backend != "source-overlay"
                else artifact_placement_policy(
                    args.worktree_mode,
                    artifact_root=artifact_root,
                    worker_tmp_dir=args.worker_tmp_dir,
                    retain_worktrees=retain_worktrees,
                    retain_worktrees_for=_retain_status_names(retain_worktrees_for)
                    if retain_worktrees
                    else [],
                    worker_label=worker_label,
                )
            ),
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
    rep.execution["reporterMetadata"] = _reporter_metadata(plugins, args.reporter)
    rep.execution["reporterRuns"] = []
    rep.execution["providers"] = _execution_provider_summary(plugins)
    rep.execution["projectAnalysis"] = analyze_project(
        repo,
        files,
        build_system=getattr(args, "build_system", None),
        build_dir=getattr(args, "build_dir", None),
        build_target=getattr(args, "build_target", None),
        test_target=getattr(args, "test_target", None),
        test_framework=getattr(args, "test_framework", None),
        test_binary=getattr(args, "test_binary", None),
        build_command=getattr(args, "build_cmd", None),
        check_command=getattr(args, "check_cmd", None),
        test_command=getattr(args, "test_cmd", None),
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
        discovered.extend(
            _discover_mode(
                repo,
                path,
                only,
                enabled,
                args.mode,
                rep.execution.get("analysis"),
                args.equivalent_suppression,
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
    if requested_execution_mode == "mutant-switch":
        mutant_switch_fallback_reason = _mutant_switch_fallback_reason(repo, discovered, args)
        if requested_execution_backend == "llvm-switch":
            llvm_switch_fallback_reason = _llvm_switch_project_fallback_reason(
                rep.execution.get("projectAnalysis")
            )
            if llvm_switch_fallback_reason:
                mutant_switch_fallback_reason = llvm_switch_fallback_reason
            elif mutant_switch_fallback_reason:
                mutant_switch_fallback_reason = (
                    "llvm-switch guarded backend unavailable: "
                    + mutant_switch_fallback_reason
                )
        if mutant_switch_fallback_reason:
            actual_execution_mode = "source-overlay"
        else:
            actual_execution_mode = "mutant-switch"
        rep.execution["executionMode"] = actual_execution_mode
        rep.execution["requestedExecutionMode"] = requested_execution_mode
        actual_execution_backend, execution_backend_fallback_reason = _resolve_execution_backend(
            requested_execution_backend,
            actual_execution_mode,
            args.artifact_backend,
            mutant_switch_fallback_reason,
        )
        rep.execution["executionBackend"] = actual_execution_backend
        rep.execution["requestedExecutionBackend"] = requested_execution_backend
        rep.execution["executionBackendFallbackReason"] = execution_backend_fallback_reason
        rep.execution["mutantSwitch"]["enabled"] = actual_execution_mode == "mutant-switch"
        rep.execution["mutantSwitch"]["fallbackReason"] = mutant_switch_fallback_reason
        rep.execution["llvmSwitch"] = {
            "enabled": actual_execution_backend == "llvm-switch",
            "requested": requested_execution_backend == "llvm-switch",
            "implementation": "guarded-source-switch",
            "fallbackReason": execution_backend_fallback_reason,
            "activationEnvironment": MUTANT_SWITCH_ACTIVE_ENV
            if actual_execution_backend == "llvm-switch"
            else None,
            "requires": [
                "compile_commands.json or CMake/CTest ownership evidence",
                "mutant-switch guardable source ranges",
            ],
        }
        guards = []
        for mut in discovered:
            guard_id = mutant_switch_guard_id(mut)
            mut.run["mutantSwitchGuardId"] = guard_id
            mut.run["mutantSwitchActiveEnvironment"] = MUTANT_SWITCH_ACTIVE_ENV
            guards.append({"mutantId": mut.id, "guardId": guard_id})
        rep.execution["mutantSwitch"]["candidateGuardCount"] = len(guards)
        rep.execution["mutantSwitch"]["guards"] = guards
        rep.execution["mutantSwitch"]["artifactCandidate"] = mutant_switch_artifact_metadata(
            enabled=actual_execution_mode == "mutant-switch",
            guard_count=len(guards),
            guards=guards,
            fallback_reason=mutant_switch_fallback_reason,
            activation_environment=MUTANT_SWITCH_ACTIVE_ENV,
        )
        if actual_execution_mode == "mutant-switch":
            rep.execution["mutationArtifact"] = rep.execution["mutantSwitch"]["artifactCandidate"]
            rep.execution["artifactPlacement"] = {
                **artifact_placement_policy(
                    args.worktree_mode,
                    artifact_root=artifact_root,
                    worker_tmp_dir=args.worker_tmp_dir,
                    retain_worktrees=retain_worktrees,
                    retain_worktrees_for=_retain_status_names(retain_worktrees_for)
                    if retain_worktrees
                    else [],
                    worker_label=worker_label,
                ),
                "mode": "mutant-switch",
                "mutantSwitch": {
                    "activationEnvironment": MUTANT_SWITCH_ACTIVE_ENV,
                    "guardedSourceOverlay": True,
                },
            }
            rep.execution["singleCompile"] = {
                "enabled": True,
                "activationMethod": "environment",
                "activationEnvironment": MUTANT_SWITCH_ACTIVE_ENV,
                "runtimeGuardCount": len(guards),
                "builds": 0,
                "checks": 0,
            }
            rep.execution["compilePruning"]["strategy"] = "mutant-switch-prune-and-retry"
            rep.execution["compilePruning"]["candidateArtifactMode"] = "mutant-switch"

    rep.execution["distribution"]["selectedMutants"] = len(discovered)
    _write_distribution_manifest(args.distribution_manifest, rep, discovered, args, worker_label)
    rep.total = len(discovered)
    rep.execution["analysis"]["sourcePrecision"] = _source_precision_summary(
        [_normalize_mutant_record(mut) for mut in discovered]
    )
    if not args.quiet:
        print(f"[stryker-cxx] {rep.total} mutants across {len(files)} file(s)\n")

    os.makedirs(artifact_root, exist_ok=True)
    for event in ("initialization", "projectAnalysis", "mutationDiscovery", "artifactCreation"):
        _record_plugin_lifecycle_runs(
            rep,
            _run_plugin_lifecycle_hooks(
                plugins,
                event,
                repo,
                artifact_root,
                args.report,
                env_overrides,
                env_inherit,
                env_block,
            ),
        )
    _record_plugin_lifecycle_runs(
        rep,
        _run_plugin_lifecycle_hooks(
            plugins,
            "coverageAnalysis",
            repo,
            artifact_root,
            args.report,
            env_overrides,
            env_inherit,
            env_block,
        ),
    )

    if args.coverage_analysis == "off":
        coverage_map, coverage_tests, coverage_meta = {}, {}, {
            "provider": None,
            "analysis": "off",
            "enabled": False,
        }
    else:
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
        "analysis": args.coverage_analysis,
        "coveredMutants": 0,
        "noCoverageMutants": 0,
        "unknownCoverageMutants": 0,
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
            dashboard_upload_error = _attempt_dashboard_upload(args, rep)
            _write_report(args.report, rep, output_mode=output_mode)
            _write_output_artifacts(args.report, args.format, rep)
            if args.dashboard_export:
                _write_dashboard_export(args.dashboard_export, rep)
            if dashboard_upload_error is not None:
                raise dashboard_upload_error
            _record_plugin_lifecycle_runs(
                rep,
                _run_plugin_lifecycle_hooks(
                    plugins,
                    "reporting",
                    repo,
                    artifact_root,
                    args.report,
                    env_overrides,
                    env_inherit,
                    env_block,
                ),
            )
            _record_reporter_plugin_runs(
                rep,
                _run_reporter_plugins(
                    plugins,
                    args.reporter,
                    repo,
                    artifact_root,
                    args.report,
                    env_overrides,
                    env_inherit,
                    env_block,
                ),
                plugins,
                args.reporter,
            )
            _record_plugin_lifecycle_runs(
                rep,
                _run_plugin_lifecycle_hooks(
                    plugins,
                    "cleanup",
                    repo,
                    artifact_root,
                    args.report,
                    env_overrides,
                    env_inherit,
                    env_block,
                ),
            )
            _write_report(args.report, rep, output_mode=output_mode)
            _write_output_artifacts(args.report, args.format, rep)
            if args.dashboard_export:
                _write_dashboard_export(args.dashboard_export, rep)
            return 1

    if args.dry_run_only:
        rep.finalize()
        dashboard_upload_error = _attempt_dashboard_upload(args, rep)
        _write_report(args.report, rep, output_mode=output_mode)
        _write_output_artifacts(args.report, args.format, rep)
        if args.dashboard_export:
            _write_dashboard_export(args.dashboard_export, rep)
        if dashboard_upload_error is not None:
            raise dashboard_upload_error
        _record_plugin_lifecycle_runs(
            rep,
            _run_plugin_lifecycle_hooks(
                plugins,
                "reporting",
                repo,
                artifact_root,
                args.report,
                env_overrides,
                env_inherit,
                env_block,
            ),
        )
        _record_reporter_plugin_runs(
            rep,
            _run_reporter_plugins(
                plugins,
                args.reporter,
                repo,
                artifact_root,
                args.report,
                env_overrides,
                env_inherit,
                env_block,
            ),
            plugins,
            args.reporter,
        )
        _record_plugin_lifecycle_runs(
            rep,
            _run_plugin_lifecycle_hooks(
                plugins,
                "cleanup",
                repo,
                artifact_root,
                args.report,
                env_overrides,
                env_inherit,
                env_block,
            ),
        )
        _write_report(args.report, rep, output_mode=output_mode)
        _write_output_artifacts(args.report, args.format, rep)
        if args.dashboard_export:
            _write_dashboard_export(args.dashboard_export, rep)
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
        if args.coverage_analysis == "off":
            m.run["coverageStatus"] = "disabled"
        elif coverage_map:
            covered = _covered_lines_for(coverage_map, repo, m.file)
            if covered is None:
                m.run["coverageStatus"] = "unknown"
                rep.coverage["unknownCoverageMutants"] = int(rep.coverage.get("unknownCoverageMutants", 0)) + 1
            elif m.line not in covered:
                m.run["coverageStatus"] = "not-covered"
                m.status = "NO_COVERAGE"
                m.detail = "mutant line was not covered by supplied coverage data"
                rep.noCoverage += 1
                rep.coverage["noCoverageMutants"] = int(rep.coverage.get("noCoverageMutants", 0)) + 1
                rec = _normalize_mutant_record(asdict(m))
                rec["resultSource"] = "coverage"
                rec["baselineKey"] = _baseline_key(m, repo, baseline_config_hash)
                rep.mutants.append(rec)
                continue
            else:
                m.run["coverageStatus"] = "covered"
                rep.coverage["coveredMutants"] = int(rep.coverage.get("coveredMutants", 0)) + 1
            if args.coverage_analysis in {"perTest", "perTestInIsolation"} and args.coverage_test_command_template:
                tests = _covered_tests_for(coverage_tests, repo, m.file, m.line)
                if tests:
                    m.run["coveredBy"] = tests
                    m.run["selectedTestCommand"] = _coverage_selected_test_command(args.coverage_test_command_template, tests)
                    rep.coverage["testSelectedMutants"] = int(rep.coverage.get("testSelectedMutants", 0)) + 1
                else:
                    m.run["coverageSelectionMissReason"] = "no covering tests for mutant line"
                    rep.coverage["testSelectionMisses"] = int(rep.coverage.get("testSelectionMisses", 0)) + 1
        else:
            m.run["coverageStatus"] = "unknown"
            rep.coverage["unknownCoverageMutants"] = int(rep.coverage.get("unknownCoverageMutants", 0)) + 1
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
    _record_plugin_lifecycle_runs(
        rep,
        _run_plugin_lifecycle_hooks(
            plugins,
            "scheduling",
            repo,
            artifact_root,
            args.report,
            env_overrides,
            env_inherit,
            env_block,
        ),
    )

    def run_single_mutant(mut: Mutant) -> Mutant:
        if args.artifact_backend in {"compiled-executable", "compiled-library", "compiled-object"}:
            return _run_mutant_once_compiled_artifact(
                mut,
                repo=repo,
                build_cmd=args.build_cmd,
                check_cmd=args.check_cmd,
                test_cmd=args.test_cmd,
                timeout_seconds=effective_timeout_seconds,
                artifact_root=artifact_root,
                execution_mode=args.mode,
                skip_tests=args.skip_tests,
                build_system=args.build_system,
                artifact_backend=args.artifact_backend,
                build_dir=args.build_dir,
                build_target=args.build_target,
                test_binary=args.test_binary,
                artifact_path=args.artifact_path,
                xcode_workspace=args.xcode_workspace,
                xcode_project=args.xcode_project,
                xcode_scheme=args.xcode_scheme,
                xcode_configuration=args.xcode_configuration,
                xcode_sdk=args.xcode_sdk,
                xcode_destination=args.xcode_destination,
                plugins=plugins,
                worker_tmp_dir=args.worker_tmp_dir,
                retain_worktrees=retain_worktrees,
                retain_worktrees_for=retain_worktrees_for,
                worker_label=worker_label,
                env_overrides=env_overrides,
                env_inherit=env_inherit,
                env_block=env_block,
            )
        return _run_mutant_once(
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
            worker_label=worker_label,
            env_overrides=env_overrides,
            env_inherit=env_inherit,
            env_block=env_block,
        )

    _record_plugin_lifecycle_runs(
        rep,
        _run_plugin_lifecycle_hooks(
            plugins,
            "execution",
            repo,
            artifact_root,
            args.report,
            env_overrides,
            env_inherit,
            env_block,
        ),
    )

    try:
        if pending:
            if actual_execution_mode == "mutant-switch":
                switch_builds = 1
                executed_mutants = _run_mutant_switch_session(
                    pending,
                    repo=repo,
                    build_cmd=args.build_cmd,
                    check_cmd=args.check_cmd,
                    test_cmd=args.test_cmd,
                    timeout_seconds=effective_timeout_seconds,
                    worktree_mode=args.worktree_mode,
                    artifact_root=artifact_root,
                    analysis_mode=args.mode,
                    skip_tests=args.skip_tests,
                    plugins=plugins,
                    worker_tmp_dir=args.worker_tmp_dir,
                    retain_worktrees=retain_worktrees,
                    retain_worktrees_for=retain_worktrees_for,
                    worker_label=worker_label,
                    env_overrides=env_overrides,
                    env_inherit=env_inherit,
                    env_block=env_block,
                )
                if (
                    len(pending) > 1
                    and executed_mutants
                    and all(mut.status in COMPILE_PRUNED_STATUSES for mut in executed_mutants)
                ):
                    original_batch_id = "mutant-switch-compile-prune-1"
                    first_status = executed_mutants[0].status
                    _record_compile_pruning_attempt(
                        rep,
                        pending,
                        first_status,
                        {
                            "batchId": original_batch_id,
                            "executionMode": "mutant-switch",
                            "guardCount": len(pending),
                        },
                    )
                    retry_candidates: list[Mutant] = []
                    for original in pending:
                        probe_input = copy.deepcopy(original)
                        probe_result = _run_mutant_switch_session(
                            [probe_input],
                            repo=repo,
                            build_cmd=args.build_cmd,
                            check_cmd=args.check_cmd,
                            test_cmd=args.test_cmd,
                            timeout_seconds=effective_timeout_seconds,
                            worktree_mode=args.worktree_mode,
                            artifact_root=artifact_root,
                            analysis_mode=args.mode,
                            skip_tests=True,
                            plugins=plugins,
                            worker_tmp_dir=args.worker_tmp_dir,
                            retain_worktrees=retain_worktrees,
                            retain_worktrees_for=retain_worktrees_for,
                            worker_label=worker_label,
                            env_overrides=env_overrides,
                            env_inherit=env_inherit,
                            env_block=env_block,
                        )[0]
                        switch_builds += 1
                        if probe_result.status in COMPILE_PRUNED_STATUSES:
                            _record_compile_pruned_mutant(
                                rep,
                                probe_result,
                                source="mutant-switch",
                                batch_id=original_batch_id,
                            )
                            _count_status(rep, probe_result.status)
                            probe_result.run["reproCommand"] = mutation_repro_command(
                                probe_result,
                                repo,
                                args.build_cmd,
                                str(probe_result.run.get("selectedTestCommand") or args.test_cmd or ""),
                                args.report,
                            )
                            rep.mutants.append(_executed_record(probe_result, "compile-pruning"))
                            continue
                        original.run["compilePruning"] = {
                            **dict(original.run.get("compilePruning", {})),
                            "retriedAfterPruning": True,
                            "sourceBatchId": original_batch_id,
                        }
                        retry_candidates.append(original)

                    if retry_candidates:
                        _record_compile_pruning_retry(rep, retry_candidates)
                        executed_mutants = _run_mutant_switch_session(
                            retry_candidates,
                            repo=repo,
                            build_cmd=args.build_cmd,
                            check_cmd=args.check_cmd,
                            test_cmd=args.test_cmd,
                            timeout_seconds=effective_timeout_seconds,
                            worktree_mode=args.worktree_mode,
                            artifact_root=artifact_root,
                            analysis_mode=args.mode,
                            skip_tests=args.skip_tests,
                            plugins=plugins,
                            worker_tmp_dir=args.worker_tmp_dir,
                            retain_worktrees=retain_worktrees,
                            retain_worktrees_for=retain_worktrees_for,
                            worker_label=worker_label,
                            env_overrides=env_overrides,
                            env_inherit=env_inherit,
                            env_block=env_block,
                        )
                        switch_builds += 1
                    else:
                        executed_mutants = []
                rep.execution.setdefault("singleCompile", {})["builds"] = switch_builds
                rep.execution.setdefault("singleCompile", {})["checks"] = switch_builds if args.check_cmd else 0
                for idx, executed in enumerate(executed_mutants, 1):
                    _count_status(rep, executed.status)
                    _record_compile_pruned_mutant(rep, executed, source="mutant-switch")
                    executed.run["reproCommand"] = mutation_repro_command(
                        executed,
                        repo,
                        args.build_cmd,
                        str(executed.run.get("selectedTestCommand") or args.test_cmd or ""),
                        args.report,
                    )
                    rep.mutants.append(_executed_record(executed, "mutant-switch"))
                    _write_report(args.report, rep, output_mode=output_mode)
                    if not args.quiet:
                        tag = f"{executed.file}:{executed.line}:{executed.col}:{executed.mutator}"
                        print(f"[{idx}/{len(executed_mutants)}] {tag} ... {executed.status} ({executed.durationMs}ms)")
            elif args.batch_mutants:
                batches = _batch_mutants(pending, args.batch_size)
                rep.execution["batching"]["batches"] = len(batches)
                rep.execution["batching"]["batchedMutants"] = sum(len(batch) for batch in batches if len(batch) > 1)
                rep.execution["batching"]["plan"] = _batch_plan(batches)
                if args.artifact_backend == "source-overlay":
                    probe_payloads = [
                        (
                            batch_index,
                            batch,
                            repo,
                            args.build_cmd,
                            args.check_cmd,
                            args.test_cmd,
                            args.coverage_test_command_template,
                            effective_timeout_seconds,
                            args.worktree_mode,
                            artifact_root,
                            args.mode,
                            args.skip_tests,
                            plugins,
                            args.worker_tmp_dir,
                            retain_worktrees,
                            retain_worktrees_for,
                            worker_label,
                            env_overrides,
                            env_inherit,
                            env_block,
                        )
                        for batch_index, batch in enumerate(batches, 1)
                        if len(batch) > 1
                    ]
                    batch_task = _run_batch_task
                else:
                    probe_payloads = [
                        (
                            batch_index,
                            batch,
                            repo,
                            args.check_cmd,
                            args.test_cmd,
                            args.coverage_test_command_template,
                            effective_timeout_seconds,
                            artifact_root,
                            args.mode,
                            args.skip_tests,
                            args.build_system,
                            args.artifact_backend,
                            args.build_dir,
                            args.build_target,
                            args.test_binary,
                            args.artifact_path,
                            args.xcode_workspace,
                            args.xcode_project,
                            args.xcode_scheme,
                            args.xcode_configuration,
                            args.xcode_sdk,
                            args.xcode_destination,
                            plugins,
                            args.worker_tmp_dir,
                            retain_worktrees,
                            retain_worktrees_for,
                            worker_label,
                            env_overrides,
                            env_inherit,
                            env_block,
                        )
                        for batch_index, batch in enumerate(batches, 1)
                        if len(batch) > 1
                    ]
                    batch_task = _run_compiled_batch_task
                rep.execution["batching"]["parallelWorkers"] = min(args.jobs, max(1, len(probe_payloads)))
                if args.jobs > 1 and len(probe_payloads) > 1:
                    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
                        probe_results = list(executor.map(batch_task, probe_payloads))
                else:
                    probe_results = [batch_task(payload) for payload in probe_payloads]
                probe_results_by_index = {
                    batch_index: (batch, status, detail, duration_ms, run)
                    for batch_index, batch, status, detail, duration_ms, run in probe_results
                }
                for batch_index, batch in enumerate(batches, 1):
                    if len(batch) == 1:
                        executed = run_single_mutant(batch[0])
                        _record_compile_pruned_mutant(rep, executed, source="executed")
                        _count_status(rep, executed.status)
                        executed.run["reproCommand"] = mutation_repro_command(executed, repo, args.build_cmd, str(executed.run.get("selectedTestCommand") or args.test_cmd or ""), args.report)
                        result_source = "compile-pruning" if executed.status in COMPILE_PRUNED_STATUSES else "executed"
                        rep.mutants.append(_executed_record(executed, result_source))
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
                    if status in COMPILE_PRUNED_STATUSES and args.artifact_backend != "source-overlay":
                        _record_compile_pruning_attempt(rep, batch, status, run)
                    if status in COMPILE_PRUNED_STATUSES and args.artifact_backend == "source-overlay":
                        original_batch_id = str(run.get("batchId") or "")
                        _record_compile_pruning_attempt(rep, batch, status, run)
                        retry_candidates: list[Mutant] = []
                        for mut in batch:
                            probed = _compile_probe_mutant(
                                mut,
                                repo=repo,
                                build_cmd=args.build_cmd,
                                check_cmd=args.check_cmd,
                                timeout_seconds=effective_timeout_seconds,
                                worktree_mode=args.worktree_mode,
                                artifact_root=artifact_root,
                                execution_mode=args.mode,
                                plugins=plugins,
                                worker_tmp_dir=args.worker_tmp_dir,
                                retain_worktrees=retain_worktrees,
                                retain_worktrees_for=retain_worktrees_for,
                                worker_label=worker_label,
                                env_overrides=env_overrides,
                                env_inherit=env_inherit,
                                env_block=env_block,
                                batch_id=original_batch_id,
                            )
                            if probed.status in COMPILE_PRUNED_STATUSES:
                                _record_compile_pruned_mutant(
                                    rep,
                                    probed,
                                    source="batch",
                                    batch_id=original_batch_id,
                                )
                                _count_status(rep, probed.status)
                                probed.run["reproCommand"] = mutation_repro_command(
                                    probed,
                                    repo,
                                    args.build_cmd,
                                    str(probed.run.get("selectedTestCommand") or args.test_cmd or ""),
                                    args.report,
                                )
                                rep.mutants.append(_executed_record(probed, "compile-pruning"))
                                continue
                            if probed.status == "PENDING":
                                retry_candidates.append(probed)
                                continue
                            _count_status(rep, probed.status)
                            probed.run["reproCommand"] = mutation_repro_command(
                                probed,
                                repo,
                                args.build_cmd,
                                str(probed.run.get("selectedTestCommand") or args.test_cmd or ""),
                                args.report,
                            )
                            rep.mutants.append(_executed_record(probed, "compile-pruning"))

                        if not retry_candidates:
                            _write_report(args.report, rep, output_mode=output_mode)
                            continue

                        _record_compile_pruning_retry(rep, retry_candidates)
                        if len(retry_candidates) > 1:
                            retry_status, retry_detail, retry_duration_ms, retry_run = _run_batch_probe(
                                retry_candidates,
                                repo=repo,
                                build_cmd=args.build_cmd,
                                check_cmd=args.check_cmd,
                                test_cmd=args.test_cmd,
                                coverage_test_command_template=args.coverage_test_command_template,
                                timeout_seconds=effective_timeout_seconds,
                                worktree_mode=args.worktree_mode,
                                artifact_root=artifact_root,
                                execution_mode=args.mode,
                                skip_tests=args.skip_tests,
                                plugins=plugins,
                                worker_tmp_dir=args.worker_tmp_dir,
                                retain_worktrees=retain_worktrees,
                                retain_worktrees_for=retain_worktrees_for,
                                worker_label=worker_label,
                                env_overrides=env_overrides,
                                env_inherit=env_inherit,
                                env_block=env_block,
                            )
                            if retry_status == "SURVIVED":
                                for mut in retry_candidates:
                                    mut.status = "SURVIVED"
                                    mut.detail = retry_detail
                                    mut.durationMs = retry_duration_ms
                                    mut.run.update(retry_run)
                                    mut.run["compilePruning"] = {
                                        **dict(mut.run.get("compilePruning", {})),
                                        "retriedAfterPruning": True,
                                        "sourceBatchId": original_batch_id,
                                    }
                                    mut.run["reproCommand"] = mutation_repro_command(mut, repo, args.build_cmd, args.test_cmd or "", args.report)
                                    _count_status(rep, mut.status)
                                    rep.mutants.append(_executed_record(mut, "batch"))
                                _write_report(args.report, rep, output_mode=output_mode)
                                if not args.quiet:
                                    print(
                                        f"[batch {batch_index}/{len(batches)}] "
                                        f"{len(retry_candidates)} mutants ... SURVIVED after pruning "
                                        f"({retry_duration_ms}ms)"
                                    )
                                continue
                            if retry_status in COMPILE_PRUNED_STATUSES:
                                _record_compile_pruning_attempt(rep, retry_candidates, retry_status, retry_run)
                            batch, status, detail, duration_ms, run = (
                                retry_candidates,
                                retry_status,
                                retry_detail,
                                retry_duration_ms,
                                retry_run,
                            )
                        else:
                            batch = retry_candidates

                    if not args.quiet:
                        print(f"[batch {batch_index}/{len(batches)}] {len(batch)} mutants ... {status}; splitting")
                    for mut in batch:
                        if run.get("batchId"):
                            mut.run["splitFromBatchId"] = run.get("batchId")
                        executed = run_single_mutant(mut)
                        _record_compile_pruned_mutant(
                            rep,
                            executed,
                            source="executed",
                            batch_id=str(run.get("batchId") or "") or None,
                        )
                        _count_status(rep, executed.status)
                        executed.run["reproCommand"] = mutation_repro_command(executed, repo, args.build_cmd, str(executed.run.get("selectedTestCommand") or args.test_cmd or ""), args.report)
                        result_source = "compile-pruning" if executed.status in COMPILE_PRUNED_STATUSES else "executed"
                        rep.mutants.append(_executed_record(executed, result_source))
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
                        worker_label,
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
                        _record_compile_pruned_mutant(rep, executed, source="executed")

                        if not args.quiet:
                            tag = (
                                f"{executed.file.split('/')[-1]}:{executed.line} "
                                f"{executed.original}->{executed.mutated} [{executed.mutator}]"
                            )
                            print(f"[{idx}/{len(pending)}] {tag} ... {executed.status} ({executed.durationMs}ms)")
                        executed.run["reproCommand"] = mutation_repro_command(executed, repo, args.build_cmd, str(executed.run.get("selectedTestCommand") or args.test_cmd or ""), args.report)
                        rec = _normalize_mutant_record(asdict(executed))
                        rec["resultSource"] = "compile-pruning" if executed.status in COMPILE_PRUNED_STATUSES else "executed"
                        if isinstance(executed.run, dict) and executed.run.get("baselineKey"):
                            rec["baselineKey"] = executed.run["baselineKey"]
                        rep.mutants.append(rec)
                        _write_report(args.report, rep, output_mode=output_mode)
            else:
                for idx, mut in enumerate(pending, 1):
                    if not args.quiet:
                        tag = f"{mut.file.split('/')[-1]}:{mut.line} {mut.original}->{mut.mutated} [{mut.mutator}]"
                        print(f"[{idx}/{len(pending)}] {tag} ... ", end="", flush=True)
                    executed = run_single_mutant(mut)
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
                    _record_compile_pruned_mutant(rep, executed, source="executed")
                    executed.run["reproCommand"] = mutation_repro_command(executed, repo, args.build_cmd, str(executed.run.get("selectedTestCommand") or args.test_cmd or ""), args.report)
                    rec = _normalize_mutant_record(asdict(executed))
                    rec["resultSource"] = "compile-pruning" if executed.status in COMPILE_PRUNED_STATUSES else "executed"
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
    dashboard_upload_error = _attempt_dashboard_upload(args, rep)
    _write_report(args.report, rep, output_mode=output_mode)

    _write_output_artifacts(args.report, args.format, rep)
    if args.dashboard_export:
        _write_dashboard_export(args.dashboard_export, rep)
    if dashboard_upload_error is not None:
        raise dashboard_upload_error
    _record_plugin_lifecycle_runs(
        rep,
        _run_plugin_lifecycle_hooks(
            plugins,
            "reporting",
            repo,
            artifact_root,
            args.report,
            env_overrides,
            env_inherit,
            env_block,
        ),
    )
    _record_reporter_plugin_runs(
        rep,
        _run_reporter_plugins(
            plugins,
            args.reporter,
            repo,
            artifact_root,
            args.report,
            env_overrides,
            env_inherit,
            env_block,
        ),
        plugins,
        args.reporter,
    )
    _record_plugin_lifecycle_runs(
        rep,
        _run_plugin_lifecycle_hooks(
            plugins,
            "cleanup",
            repo,
            artifact_root,
            args.report,
            env_overrides,
            env_inherit,
            env_block,
        ),
    )
    _write_report(args.report, rep, output_mode=output_mode)
    _write_output_artifacts(args.report, args.format, rep)
    if args.dashboard_export:
        _write_dashboard_export(args.dashboard_export, rep)

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

    if rep.buildError:
        return 2

    effective_threshold = rep.thresholds["break"]
    if rep.score < effective_threshold:
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
