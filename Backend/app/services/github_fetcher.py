import os
import httpx
import json
import tempfile
import time
import os
import re
import zipfile
import shutil
from typing import List, Dict, Optional, Tuple, Any
from google.genai import Client
from .code_extractors import _dedupe

class GitHubFetcher:
    BASE_URL = "https://api.github.com"
    CACHE_FILE = "output/github_cache.json"

    def __init__(self, token = None):
        self.token = token
        self.headers = {"Authorization": f"token {self.token}"} if token else {}
        # Load cache if exists
        if os.path.exists(self.CACHE_FILE):
            with open(self.CACHE_FILE, "r") as f:
                self.cache = json.load(f)
        else:
            self.cache: Dict[str, Any] = {}

    def _save_cache(self):
        with open(self.CACHE_FILE, "w") as f:
            json.dump(self.cache, f, indent=2)

    async def fetch_user_repos(self, username: str) -> list[dict]:
        cache_key = f"user_repos:{username}"
        if cache_key in self.cache:
            print(f"Cache hit: repos for {username}")
            return self.cache[cache_key]

        url = f"{self.BASE_URL}/users/{username}/repos?per_page=100&type=owner&sort=updated"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            repos = resp.json()

        self.cache[cache_key] = repos
        self._save_cache()
        print(f"Fetched {len(repos)} repos for {username}")
        return repos

    async def fetch_repo_readme(self, owner: str, repo: str) -> str:
        cache_key = f"readme:{owner}/{repo}"
        if cache_key in self.cache:
            print(f"Cache hit: readme for {owner}/{repo}")
            return self.cache[cache_key]

        url = f"{self.BASE_URL}/repos/{owner}/{repo}/readme"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                url,
                headers={**self.headers, "Accept": "application/vnd.github.v3.raw"},
            )
            if resp.status_code == 200:
                readme = resp.text
                self.cache[cache_key] = readme
                self._save_cache()
                return readme
            return ""

    async def fetch_repo_languages(self, owner: str, repo: str) -> list[str]:
        cache_key = f"languages:{owner}/{repo}"
        if cache_key in self.cache:
            print(f"Cache hit: languages for {owner}/{repo}")
            return self.cache[cache_key]

        url = f"{self.BASE_URL}/repos/{owner}/{repo}/languages"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code == 200:
                langs = list(resp.json().keys())
                self.cache[cache_key] = langs
                self._save_cache()
                return langs
            return []

    async def fetch_repo_structure(self, owner: str, repo: str) -> list[str]:
        cache_key = f"structure:{owner}/{repo}"
        if cache_key in self.cache:
            print(f"Cache hit: structure for {owner}/{repo}")
            return self.cache[cache_key]

        url = f"{self.BASE_URL}/repos/{owner}/{repo}/contents"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code != 200:
                return []

            items = resp.json()
            structure = []
            for item in items:
                if item["type"] == "dir":
                    structure.append(item["name"] + "/")
                else:
                    if item["name"].lower() in [
                        "dockerfile", "docker-compose.yml",
                        "requirements.txt", "package.json",
                        "pom.xml", "build.gradle",
                        "readme.md"
                    ] or item["name"].startswith(".github"):
                        structure.append(item["name"])

        self.cache[cache_key] = structure
        self._save_cache()
        return structure

    async def fetch_repo_dependencies(self, owner: str, repo: str) -> list[str]:
        cache_key = f"dependencies:{owner}/{repo}"
        if cache_key in self.cache:
            print(f"Cache hit: dependencies for {owner}/{repo}")
            return self.cache[cache_key]

        dependencies = []
        files_to_check = [
            "package.json", "requirements.txt", "pyproject.toml",
            "Pipfile", "pom.xml", "build.gradle", "Cargo.toml", "go.mod"
        ]

        async with httpx.AsyncClient(timeout=20) as client:
            for file in files_to_check:
                url = f"{self.BASE_URL}/repos/{owner}/{repo}/contents/{file}"
                resp = await client.get(
                    url, headers={**self.headers, "Accept": "application/vnd.github.v3.raw"}
                )
                if resp.status_code == 200:
                    content = resp.text
                    if file == "package.json":
                        try:
                            data = json.loads(content)
                            deps = list(data.get("dependencies", {}).keys()) + \
                                   list(data.get("devDependencies", {}).keys())
                            dependencies.extend(deps)
                        except Exception:
                            pass
                    elif file in ("requirements.txt", "Pipfile"):
                        deps = [
                            re.split(r"[=<>!~ ]+", line.strip())[0]
                            for line in content.splitlines()
                            if line.strip() and not line.strip().startswith("#")
                        ]
                        dependencies.extend(deps)
                    elif file == "pyproject.toml":
                        # Extremely light parse: collect keys under dependencies blocks
                        for line in content.splitlines():
                            if "=" in line and "[" not in line and not line.strip().startswith("#"):
                                dependencies.append(line.split("=")[0].strip().strip('"').strip("'"))
                    elif file in ("pom.xml", "build.gradle"):
                        # Simplified: grab artifactId or implementation coords
                        for line in content.splitlines():
                            if "<artifactId>" in line:
                                m = re.search(r"<artifactId>(.*?)</artifactId>", line)
                                if m:
                                    dependencies.append(m.group(1).strip())
                            elif "implementation" in line or "api " in line:
                                # e.g., implementation "org.springframework.boot:spring-boot-starter-web"
                                g = re.search(r"['\"]([A-Za-z0-9_.\-]+:[A-Za-z0-9_.\-]+)['\"]", line)
                                if g:
                                    dependencies.append(g.group(1))
                    else:
                        dependencies.append(f"{file} present")

        dependencies = _dedupe(dependencies)
        self.cache[cache_key] = dependencies
        self._save_cache()
        return dependencies

    async def download_repo_zip(self, owner: str, repo: str, ref = None) -> str:
        """
        Downloads repo zipball to a temp directory and returns the extracted path.
        Uses GitHub zipball API (no git needed).
        """
        ref_part = f"/{ref}" if ref else ""
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/zipball{ref_part}"
        async with httpx.AsyncClient(follow_redirects=True,timeout=60) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            tmp_dir = tempfile.mkdtemp(prefix=f"{owner}_{repo}_")
            zip_path = os.path.join(tmp_dir, f"{repo}.zip")
            with open(zip_path, "wb") as f:
                f.write(resp.content)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmp_dir)
            # GitHub wraps contents in a top folder; find it
            entries = [os.path.join(tmp_dir, d) for d in os.listdir(tmp_dir)]
            top = next((e for e in entries if os.path.isdir(e)), tmp_dir)
            return top
