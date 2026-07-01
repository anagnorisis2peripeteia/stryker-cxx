from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from stryker_cxx.project_analysis import analyze_project


class ProjectAnalysisTests(unittest.TestCase):
    def test_cmake_target_sources_contribute_source_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "sample.cpp").write_text("int main() { return 0; }\n")
            (repo / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.16)\n"
                "project(target_sources_fixture LANGUAGES CXX)\n"
                "add_executable(sample)\n"
                "target_sources(sample PRIVATE sample.cpp sample.cpp)\n"
            )

            payload = analyze_project(
                str(repo),
                ["sample.cpp"],
                build_system="cmake",
                build_dir="build",
                build_target="sample",
            )

        owned = payload["sourceTargets"][0]
        self.assertEqual(owned["ownership"]["kind"], "cmake-target-source")
        self.assertEqual(owned["ownership"]["buildTargets"], ["sample"])
        discovered = [
            item
            for item in payload["buildTargets"]
            if item["name"] == "sample" and item["source"] == "CMakeLists.txt"
        ]
        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0]["sourceFiles"], ["sample.cpp"])

    def test_meson_libraries_contribute_source_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "sample.cpp").write_text("int value() { return 1; }\n")
            (repo / "meson.build").write_text(
                "project('meson-library-fixture', 'cpp')\n"
                "math_core = static_library('math_core', 'sample.cpp', 'sample.cpp')\n"
            )

            payload = analyze_project(
                str(repo),
                ["sample.cpp"],
                build_system="meson",
                build_target="math_core",
            )

        owned = payload["sourceTargets"][0]
        self.assertEqual(owned["ownership"]["kind"], "build-target-source")
        self.assertEqual(owned["ownership"]["buildTargets"], ["math_core"])
        discovered = [
            item
            for item in payload["buildTargets"]
            if item["name"] == "math_core" and item["source"] == "meson.build"
        ]
        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0]["kind"], "static-library")
        self.assertEqual(discovered[0]["sourceFiles"], ["sample.cpp"])

    def test_nested_bazel_packages_contribute_source_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "WORKSPACE.bazel").write_text("workspace(name = 'nested_bazel_fixture')\n")
            package = repo / "lib"
            package.mkdir()
            (package / "math.cpp").write_text("int value() { return 1; }\n")
            (package / "BUILD.bazel").write_text(
                "cc_library(\n"
                "    name = 'math_core',\n"
                "    srcs = ['math.cpp', 'math.cpp'],\n"
                ")\n"
            )

            payload = analyze_project(
                str(repo),
                ["lib/math.cpp"],
                build_system="bazel",
                build_target="//lib:math_core",
            )

        owned = payload["sourceTargets"][0]
        self.assertEqual(owned["ownership"]["kind"], "build-target-source")
        self.assertEqual(owned["ownership"]["buildTargets"], ["//lib:math_core"])
        discovered = [
            item
            for item in payload["buildTargets"]
            if item["name"] == "//lib:math_core" and item["source"] == "lib/BUILD.bazel"
        ]
        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0]["kind"], "cc_library")
        self.assertEqual(discovered[0]["sourceFiles"], ["lib/math.cpp"])

    def test_nested_bazel_tests_record_related_dependency_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "WORKSPACE.bazel").write_text("workspace(name = 'bazel_test_fixture')\n")
            package = repo / "lib"
            package.mkdir()
            (package / "math.cpp").write_text("int value() { return 1; }\n")
            (package / "math_test.cpp").write_text("int main() { return 0; }\n")
            (package / "BUILD.bazel").write_text(
                "cc_library(\n"
                "    name = 'math_core',\n"
                "    srcs = ['math.cpp'],\n"
                ")\n"
                "cc_test(\n"
                "    name = 'math_test',\n"
                "    srcs = ['math_test.cpp'],\n"
                "    deps = [':math_core'],\n"
                ")\n"
            )

            payload = analyze_project(
                str(repo),
                ["lib/math.cpp"],
                build_system="bazel",
                build_target="//lib:math_core",
                test_target="//lib:math_test",
            )

        tests = [
            item
            for item in payload["testTargets"]
            if item["name"] == "//lib:math_test" and item["source"] == "lib/BUILD.bazel"
        ]
        self.assertEqual(len(tests), 1)
        self.assertEqual(tests[0]["relatedBuildTarget"], "//lib:math_core")
        self.assertEqual(tests[0]["dependencies"], ["//lib:math_core"])

    def test_xcode_unit_test_target_records_related_build_target_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = repo / "App.xcodeproj"
            project.mkdir()
            (repo / "App.cpp").write_text("int value() { return 1; }\n")
            (repo / "AppTests.cpp").write_text("int main() { return 0; }\n")
            (project / "project.pbxproj").write_text(
                "SRCAPP /* App.cpp */ = { isa = PBXFileReference; path = App.cpp; };\n"
                "SRCTEST /* AppTests.cpp */ = { isa = PBXFileReference; path = AppTests.cpp; };\n"
                "BFAPP /* App.cpp in Sources */ = { isa = PBXBuildFile; fileRef = SRCAPP /* App.cpp */; };\n"
                "BFTEST /* AppTests.cpp in Sources */ = { isa = PBXBuildFile; fileRef = SRCTEST /* AppTests.cpp */; };\n"
                "PHAPP /* Sources */ = { isa = PBXSourcesBuildPhase; files = (BFAPP /* App.cpp in Sources */,); };\n"
                "PHTEST /* Sources */ = { isa = PBXSourcesBuildPhase; files = (BFTEST /* AppTests.cpp in Sources */,); };\n"
                "TAPP /* App */ = { isa = PBXNativeTarget; name = App; productType = \"com.apple.product-type.application\"; buildPhases = (PHAPP /* Sources */,); dependencies = (); };\n"
                "TTEST /* AppTests */ = { isa = PBXNativeTarget; name = AppTests; productType = \"com.apple.product-type.bundle.unit-test\"; buildPhases = (PHTEST /* Sources */,); dependencies = (DEPAPP /* PBXTargetDependency */,); };\n"
                "DEPAPP /* PBXTargetDependency */ = { isa = PBXTargetDependency; target = TAPP /* App */; };\n"
            )

            payload = analyze_project(
                str(repo),
                ["App.cpp"],
                build_system="xcodebuild",
                build_target="App",
                test_target="AppTests",
            )

        owned = payload["sourceTargets"][0]
        self.assertEqual(owned["ownership"]["kind"], "build-target-source")
        self.assertEqual(owned["ownership"]["buildTargets"], ["App"])
        tests = [
            item
            for item in payload["testTargets"]
            if item["name"] == "AppTests" and item["kind"] == "xcode-test"
        ]
        self.assertEqual(len(tests), 1)
        self.assertEqual(tests[0]["relatedBuildTarget"], "App")
        self.assertEqual(tests[0]["dependencies"], ["App"])


if __name__ == "__main__":
    unittest.main()
