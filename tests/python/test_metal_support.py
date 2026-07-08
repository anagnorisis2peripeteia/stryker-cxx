from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from stryker_cxx import engine
from stryker_cxx.cli import _config_for_preset

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "fixtures" / "metal"


def _cli(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    package_root = REPO_ROOT / "python"
    env["PYTHONPATH"] = (
        f"{package_root}{os.pathsep}{env['PYTHONPATH']}"
        if env.get("PYTHONPATH")
        else str(package_root)
    )
    return subprocess.run(
        [sys.executable, "-m", "stryker_cxx.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
    )


class StarDisambiguationTests(unittest.TestCase):
    """Unit coverage for the token-mode `*` pointer-vs-multiply classifier."""

    def test_pointer_declarators_are_not_multiplication(self) -> None:
        for code in (
            "device const float* a",
            "float* out",
            "threadgroup float *shared",
            "int *p",
            "uint2* grid",
            "packed_float3* v",
            "unsigned long* q",
            "static float* s",
            "device MyStruct* s",       # user type reached via the address-space qualifier
            "constant half4 *weights",
        ):
            star = code.index("*")
            self.assertFalse(
                engine._star_is_multiplication(code, star),
                f"expected pointer declarator, not multiply: {code!r}",
            )

    def test_dereferences_are_not_multiplication(self) -> None:
        for code in ("= *ptr;", "*ptr = 3;", "return *p;", "sizeof *p", "co_return *ptr;"):
            star = code.index("*")
            self.assertFalse(engine._star_is_multiplication(code, star), code)

    def test_user_type_pointer_declarators_at_statement_boundary(self) -> None:
        # User-defined types aren't in the keyword table; a `<Type>* <name><terminator>` that
        # STARTS a statement (line-start, after `;`, or inside `{ ... }`) is a declaration.
        for code in (
            "MyType* p;",
            "Widget *w;",
            "Node* n = nullptr;",
            "{ Matrix* m; }",
            "obj.field; Sprite* s;",
            "Tensor* t[4];",
        ):
            star = code.index("*")
            self.assertFalse(
                engine._star_is_multiplication(code, star),
                f"expected user-type pointer declarator, not multiply: {code!r}",
            )

    def test_real_multiplications_are_kept(self) -> None:
        for code in (
            "out[i] = a[i] * k;",
            "float x = y * z;",
            "scale * value",
            "2 * n",
            "foo() * bar()",
            "arr[i]*arr[j]",
            "(a + b) * c",
            "x = alpha * beta;",   # `<ident> * <ident>;` after `=` is a multiply, not a decl
            "foo(a * b)",          # call arg after `(` stays a multiply (not a param decl)
            "return width * height;",
        ):
            star = code.index("*")
            self.assertTrue(engine._star_is_multiplication(code, star), code)

    def test_chained_multiplication_keeps_both_operators(self) -> None:
        code = "z = a * b * c;"
        stars = [i for i, ch in enumerate(code) if ch == "*"]
        self.assertEqual(len(stars), 2)
        for star in stars:
            self.assertTrue(engine._star_is_multiplication(code, star), code)


class MetalDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.kernel = self.repo / "kernel.metal"
        self.kernel.write_text(
            "#include <metal_stdlib>\n"
            "using namespace metal;\n"
            "kernel void k(device const float* a, device float* out,\n"
            "              uint gid [[thread_position_in_grid]]) {\n"
            "    out[gid] = a[gid] * 2.0f;\n"
            "}\n"
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _list(self, *args: str) -> list[dict]:
        result = _cli(self.repo, "list-mutants", "--repo", str(self.repo), *args)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return json.loads(result.stdout)

    def test_metal_skipped_without_flag(self) -> None:
        mutants = self._list("--files", "kernel.metal", "--mutation-level", "Complete")
        self.assertEqual(mutants, [], "metal must be excluded unless --include-metal is set")

    def test_include_metal_auto_enables_metal_mutators(self) -> None:
        # --include-metal alone (default Standard level, no --mutators) must yield the
        # Metal-specific mutators; that regressed to zero before the auto-enable fix.
        mutants = self._list("--files", "kernel.metal", "--include-metal")
        names = {m["mutator"] for m in mutants}
        self.assertIn("MetalAddressSpace", names)
        self.assertIn("MetalThreadPosition", names)

    def test_explicit_mutators_surface_is_not_augmented(self) -> None:
        # An explicit --mutators list is the exact surface and must not gain Metal mutators.
        mutants = self._list(
            "--files", "kernel.metal", "--include-metal", "--mutators", "ArithmeticOperator"
        )
        self.assertEqual({m["mutator"] for m in mutants}, {"ArithmeticOperator"})

    def test_pointer_star_is_not_mutated_as_arithmetic(self) -> None:
        mutants = self._list(
            "--files", "kernel.metal", "--include-metal", "--mutators", "ArithmeticOperator"
        )
        star_lines = sorted(m["line"] for m in mutants if m["original"] == "*")
        # Only the genuine `a[gid] * 2.0f` on line 5 -- never the pointer declarators.
        self.assertEqual(star_lines, [5], f"pointer declarators leaked as multiplies: {mutants}")


class MetalPresetTests(unittest.TestCase):
    def test_metal_preset_config_is_metal_ready(self) -> None:
        cfg = _config_for_preset("metal")
        self.assertIn('- "**/*.metal"', cfg)
        self.assertIn("includeMetal: true", cfg)
        self.assertIn("MetalAddressSpace", cfg)
        self.assertIn("MetalThreadPosition", cfg)
        self.assertIn("xcrun metal", cfg)
        # The build command must create build/ before xcrun/swiftc write into it, or a fresh
        # `init --preset metal` project fails its very first build.
        self.assertRegex(cfg, r"mkdir -p build\s*&&\s*xcrun metal")
        self.assertIn("schemaVersion: stryker-cxx.config.v1", cfg)

    def test_init_writes_metal_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "stryker-cxx.yml"
            result = _cli(Path(tmp), "init", "--path", str(config), "--preset", "metal")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            text = config.read_text()
            self.assertIn("includeMetal: true", text)
            self.assertIn('- "**/*.metal"', text)


class CppArithmeticRegressionTests(unittest.TestCase):
    """The pointer-* fix is general C++; guard that it neither over- nor under-fires."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.src = self.repo / "scale.cpp"
        self.src.write_text(
            "float scale(const float* a, float* out, int n) {\n"
            "    float k = 3.0f;\n"
            "    for (int i = 0; i < n; i++) {\n"
            "        out[i] = a[i] * k;\n"
            "    }\n"
            "    return k * static_cast<float>(n);\n"
            "}\n"
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_pointer_declarators_excluded_but_multiplies_kept(self) -> None:
        result = _cli(
            self.repo, "list-mutants", "--repo", str(self.repo),
            "--files", "scale.cpp", "--mutators", "ArithmeticOperator",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        mutants = json.loads(result.stdout)
        star_lines = sorted(m["line"] for m in mutants if m["original"] == "*")
        # Genuine multiplies on lines 4 (a[i] * k) and 6 (k * n); never the line-1 params.
        self.assertEqual(star_lines, [4, 6], f"unexpected `*` mutants: {mutants}")


class ComplexKernelDiscoveryTests(unittest.TestCase):
    """Guard real-world robustness on a production-shaped kernel (templates, simdgroup
    reduction, device/constant/threadgroup address spaces, thread positions, pointer-heavy
    strided indexing) — the patterns that show up in the real PyTorch MPS kernels."""

    KERNEL = "fixtures/metal/src/reduce.metal"

    def _list(self, *args: str) -> list[dict]:
        result = _cli(REPO_ROOT, "list-mutants", "--repo", str(REPO_ROOT), "--files", self.KERNEL, *args)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return json.loads(result.stdout)

    def test_discovery_is_robust_and_finds_metal_mutators(self) -> None:
        mutants = self._list("--include-metal", "--mutation-level", "Complete")
        self.assertGreater(len(mutants), 0)
        names = {m["mutator"] for m in mutants}
        self.assertIn("MetalAddressSpace", names)
        self.assertIn("MetalThreadPosition", names)

    def test_pointer_params_never_leak_as_arithmetic_stars(self) -> None:
        # This kernel has 4+ pointer declarators (`device const T* in`, `device T* out`,
        # `threadgroup T* scratch`, `device const T* row_ptr`). Only two `*` are genuine
        # multiplies: `row * row_stride` (L19) and `row_ptr[c] * T(2)` (L22). Note L19 holds
        # BOTH a declarator and a multiply, so this is asserted at column precision.
        mutants = self._list("--include-metal", "--mutators", "ArithmeticOperator")
        src = (REPO_ROOT / self.KERNEL).read_text().splitlines()
        star_hits = sorted((m["line"], m["column"]) for m in mutants if m["original"] == "*")
        # Each reported `*` must land on an actual `*` that is the genuine multiply, never a
        # pointer declarator. Asserted at column precision: L19 carries both `T* row_ptr` and
        # `row * row_stride`, so only the multiply column (39) may appear, not the declarator.
        for line_no, col in star_hits:
            self.assertEqual(src[line_no - 1][col], "*", f"L{line_no}:{col} is not `*`")
        self.assertEqual(star_hits, [(19, 39), (22, 26)], f"unexpected `*` mutants: {star_hits}")


def _metal_toolchain() -> bool:
    if sys.platform != "darwin":
        return False
    for tool in ("metal", "swiftc"):
        if subprocess.run(["xcrun", "--find", tool], capture_output=True).returncode != 0:
            return False
    return True


BUILD_CMD = (
    "xcrun metal -o build/kernels.metallib src/add.metal "
    "&& swiftc -O host/main.swift -o build/hosttest"
)
TEST_CMD = "./build/hosttest"


@unittest.skipUnless(_metal_toolchain(), "requires macOS Metal toolchain (xcrun metal/swiftc)")
class MetalEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "metal"
        shutil.copytree(FIXTURE, self.repo)
        (self.repo / "build").mkdir(exist_ok=True)
        # Only exercise mutation behaviour when the host can actually run on a GPU.
        for phase in BUILD_CMD.split("&&"):
            if subprocess.run(phase.strip(), shell=True, cwd=self.repo, capture_output=True).returncode != 0:
                self.skipTest("Metal toolchain present but kernel failed to build")
        baseline = subprocess.run(TEST_CMD, shell=True, cwd=self.repo, capture_output=True)
        if baseline.returncode != 0:
            self.skipTest("no usable Metal GPU in this environment")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_full_mutation_loop_classifies_metal_mutants(self) -> None:
        report = self.repo / "mutation.json"
        result = _cli(
            self.repo,
            "run",
            "--repo", str(self.repo),
            "--files", "src/add.metal",
            "--include-metal",
            "--mutation-level", "Complete",
            "--build-command", BUILD_CMD,
            "--test-command", TEST_CMD,
            "--report", str(report),
        )
        # A completed run exits 0, or 2 when survivors/build-errors are present -- both
        # are healthy here (device->constant on the writable buffer is an expected build
        # error). Exit 1/3 would be a real failure. Classification is asserted below.
        self.assertIn(result.returncode, (0, 2), result.stderr + result.stdout)
        payload = json.loads(report.read_text())
        mutants = payload.get("mutants") or []
        self.assertTrue(mutants, f"no mutants in report: {sorted(payload)}")

        statuses = {m["status"] for m in mutants}

        # The thread-position swap changes indexing -> wrong output -> killed.
        thread_pos = [m for m in mutants if m["mutator"] == "MetalThreadPosition"]
        self.assertTrue(thread_pos, "expected a MetalThreadPosition mutant")
        self.assertTrue(
            all(m["status"] == "KILLED" for m in thread_pos),
            f"MetalThreadPosition should be killed: {thread_pos}",
        )
        self.assertIn("KILLED", statuses, f"expected at least one killed mutant: {statuses}")
        # Writing through a constant-address-space pointer cannot compile.
        self.assertTrue(
            any(m["status"] == "BUILD_ERROR" for m in mutants),
            f"expected a build error from device->constant on the output buffer: {statuses}",
        )
        # The pointer declarators must not have produced arithmetic `*` mutants.
        self.assertFalse(
            any(m["mutator"] == "ArithmeticOperator" and m["original"] == "*" for m in mutants),
            "pointer declarator leaked as an arithmetic multiply mutant",
        )


def _metal_compiler() -> bool:
    """The Metal *compiler* only (no swiftc, no GPU) — available on far more machines
    than a usable Metal device, so the compile-detection loop runs where the GPU e2e can't."""
    return (
        sys.platform == "darwin"
        and subprocess.run(["xcrun", "--find", "metal"], capture_output=True).returncode == 0
    )


@unittest.skipUnless(_metal_compiler(), "requires the macOS Metal compiler (xcrun metal)")
class MetalCompileDetectionTests(unittest.TestCase):
    """The mutate -> recompile-with-real-`xcrun metal` -> classify loop, using the compiler
    as the oracle (no GPU): a `device*`->`constant*` write must be a BUILD_ERROR, a benign
    arithmetic mutant must compile (SURVIVED under a trivial test). This is the always-on
    guarantee for CI runners that have the Metal toolchain but no usable GPU."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "metal"
        shutil.copytree(FIXTURE, self.repo)
        # Deliberately do NOT pre-create build/ — the commands must create it themselves
        # (mirrors a fresh `init --preset metal` project), so the fix isn't hidden by setup.
        baseline = subprocess.run(
            "mkdir -p build && xcrun metal -o build/kernels.metallib src/add.metal",
            shell=True, cwd=self.repo, capture_output=True,
        )
        if baseline.returncode != 0:
            self.skipTest(f"real kernel failed to compile: {baseline.stderr.decode()[:200]}")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_real_compiler_distinguishes_breaking_from_valid_mutants(self) -> None:
        report = self.repo / "mutation.json"
        result = _cli(
            self.repo, "run",
            "--repo", str(self.repo),
            "--files", "src/add.metal",
            "--include-metal",
            "--mutators", "MetalAddressSpace,ArithmeticOperator",
            # Compile only (no GPU); a trivial passing test means valid mutants SURVIVE.
            # Build command creates build/ itself (no setUp mkdir) — proves the preset/doc fix.
            "--build-command", "mkdir -p build && xcrun metal -o build/kernels.metallib src/add.metal",
            "--test-command", "true",
            "--report", str(report),
        )
        # 0 = clean, 2 = survivors/build-errors present; both mean the run completed.
        self.assertIn(result.returncode, (0, 2), result.stderr + result.stdout)
        mutants = json.loads(report.read_text()).get("mutants") or []
        self.assertTrue(mutants, "no mutants in report")
        # device* -> constant* on the writable `out` buffer cannot compile.
        self.assertTrue(
            any(m["mutator"] == "MetalAddressSpace" and m["status"] == "BUILD_ERROR" for m in mutants),
            f"expected a MetalAddressSpace BUILD_ERROR: {[(m['mutator'], m['status']) for m in mutants]}",
        )
        # a benign arithmetic mutant compiles fine -> survives the trivial test.
        self.assertTrue(
            any(m["mutator"] == "ArithmeticOperator" and m["status"] == "SURVIVED" for m in mutants),
            f"expected an ArithmeticOperator SURVIVED: {[(m['mutator'], m['status']) for m in mutants]}",
        )


if __name__ == "__main__":
    unittest.main()
