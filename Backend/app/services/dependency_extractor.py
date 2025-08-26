import os
import re
import json
import toml
import yaml
import configparser
from typing import Dict, List, Callable, Any


class DependencyExtractor:
    """
    Extract dependencies from common manifest/lock files across ecosystems.
    """

    # 1. Registry of dependency files → language
    DEP_FILES: Dict[str, str] = {
        # --- JavaScript / TypeScript ---
        "package.json": "javascript",
        # "package-lock.json": "javascript",
        # "yarn.lock": "javascript",
        # "pnpm-lock.yaml": "javascript",
        "bower.json": "javascript",

        # --- Python ---
        "requirements.txt": "python",
        "pyproject.toml": "python",
        # "poetry.lock": "python",
        "Pipfile": "python",
        # "Pipfile.lock": "python",
        "environment.yml": "python",
        "conda.yml": "python",
        "setup.py": "python",
        "tox.ini": "python",

        # --- C / C++ ---
        "CMakeLists.txt": "cpp",
        "Makefile": "cpp",
        "configure.ac": "cpp",
        "configure.in": "cpp",
        "config.m4": "cpp",
        "vcpkg.json": "cpp",
        "conanfile.txt": "cpp",
        "conanfile.py": "cpp",
        "meson.build": "cpp",
        "xmake.lua": "cpp",
        "Brewfile": "cpp",

        # --- Rust ---
        "Cargo.toml": "rust",
        # "Cargo.lock": "rust",

        # --- Go ---
        "go.mod": "go",
        "go.sum": "go",
        "Gopkg.toml": "go",
        # "Gopkg.lock": "go",
        "glide.yaml": "go",
        # "glide.lock": "go",

        # --- Ruby ---
        "Gemfile": "ruby",
        # "Gemfile.lock": "ruby",
        ".gemspec": "ruby",

        # --- Java ---
        "pom.xml": "java",
        "build.gradle": "java",
        "build.gradle.kts": "java",
        "settings.gradle": "java",
        "ivy.xml": "java",
    }

    # 2. Parsers for different file types
    @staticmethod
    def _parse_json(text: str) -> List[str]:
        try:
            data = json.loads(text)
            deps = []
            for section in ("dependencies", "devDependencies", "peerDependencies"):
                deps.extend(data.get(section, {}).keys())
            return deps
        except Exception:
            return []

    @staticmethod
    def _parse_toml(text: str) -> List[str]:
        try:
            data = toml.loads(text)
            deps = []
            if "tool" in data and "poetry" in data["tool"]:
                deps.extend(data["tool"]["poetry"].get("dependencies", {}).keys())
            if "project" in data:  # PEP 621
                deps.extend(data["project"].get("dependencies", []))
            return [d if isinstance(d, str) else d.split()[0] for d in deps]
        except Exception:
            return []

    @staticmethod
    def _parse_yaml(text: str) -> List[str]:
        try:
            data = yaml.safe_load(text) or {}
            deps = []
            if isinstance(data, dict):
                for key in ("dependencies", "packages", "requirements"):
                    if key in data:
                        val = data[key]
                        if isinstance(val, list):
                            deps.extend(val)
                        elif isinstance(val, dict):
                            deps.extend(val.keys())
            return deps
        except Exception:
            return []

    @staticmethod
    def _parse_requirements(text: str) -> List[str]:
        deps = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pkg = re.split(r"[<=>~!]", line)[0].strip()
            deps.append(pkg)
        return deps

    @staticmethod
    def _parse_ini(text: str) -> List[str]:
        parser = configparser.ConfigParser()
        try:
            parser.read_string(text)
            deps = []
            if parser.has_section("tox"):
                for key, val in parser.items("testenv"):
                    if key == "deps":
                        deps.extend(val.splitlines())
            return deps
        except Exception:
            return []

    @staticmethod
    def _parse_plain(text: str) -> List[str]:
        """For Makefile, CMakeLists, etc. — just extract words that look like deps."""
        return re.findall(r"[A-Za-z0-9_\-\.]+", text)

    # 3. File-specific dispatch map
    PARSERS: Dict[str, Callable[[str], List[str]]] = {
        # JS/TS
        "package.json": _parse_json.__func__,
        # "package-lock.json": _parse_json.__func__,
        # "yarn.lock": _parse_plain.__func__,
        # "pnpm-lock.yaml": _parse_yaml.__func__,
        "bower.json": _parse_json.__func__,

        # Python
        "requirements.txt": _parse_requirements.__func__,
        "pyproject.toml": _parse_toml.__func__,
        # "poetry.lock": _parse_toml.__func__,
        "Pipfile": _parse_toml.__func__,
        "Pipfile.lock": _parse_toml.__func__,
        "environment.yml": _parse_yaml.__func__,
        "conda.yml": _parse_yaml.__func__,
        "setup.py": _parse_plain.__func__,
        "tox.ini": _parse_ini.__func__,

        # C / C++
        "CMakeLists.txt": _parse_plain.__func__,
        "Makefile": _parse_plain.__func__,
        "configure.ac": _parse_plain.__func__,
        "configure.in": _parse_plain.__func__,
        "config.m4": _parse_plain.__func__,
        "vcpkg.json": _parse_json.__func__,
        "conanfile.txt": _parse_plain.__func__,
        "conanfile.py": _parse_plain.__func__,
        "meson.build": _parse_plain.__func__,
        "xmake.lua": _parse_plain.__func__,
        "Brewfile": _parse_plain.__func__,

        # Rust
        "Cargo.toml": _parse_toml.__func__,
        # "Cargo.lock": _parse_toml.__func__,

        # Go
        "go.mod": _parse_plain.__func__,
        "go.sum": _parse_plain.__func__,
        "Gopkg.toml": _parse_toml.__func__,
        # "Gopkg.lock": _parse_toml.__func__,
        "glide.yaml": _parse_yaml.__func__,
        # "glide.lock": _parse_yaml.__func__,

        # Ruby
        "Gemfile": _parse_plain.__func__,
        # "Gemfile.lock": _parse_plain.__func__,
        ".gemspec": _parse_plain.__func__,

        # Java
        "pom.xml": _parse_plain.__func__,
        "build.gradle": _parse_plain.__func__,
        "build.gradle.kts": _parse_plain.__func__,
        "settings.gradle": _parse_plain.__func__,
        "ivy.xml": _parse_plain.__func__,
    }

    def extract_from_file(self, filename: str, text: str) -> List[str]:
        base = os.path.basename(filename)
        parser = self.PARSERS.get(base)
        if not parser:
            return []
        return parser(text)