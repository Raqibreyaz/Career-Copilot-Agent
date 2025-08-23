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

CACHE_FILE = os.path.join(".cache", "github_cache.json")
os.makedirs(".cache", exist_ok=True)

class GitHubFetcher:
    BASE_URL = "https://api.github.com"

    def __init__(self, token: Optional[str] = None):
        self.token = token
        self.headers = {"Authorization": f"token {self.token}"} if token else {}
        self.cache: Dict[str, Any] = {}
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r") as f:
                    self.cache = json.load(f)
            except Exception:
                self.cache = {}

    def _save_cache(self):
        with open(CACHE_FILE, "w") as f:
            json.dump(self.cache, f, indent=2)

    async def fetch_user_repos(self, username: str) -> List[Dict]:
        key = f"user_repos:{username}"
        if key in self.cache:
            print(f"Cache hit: repos for {username}")
            return self.cache[key]
        url = f"{self.BASE_URL}/users/{username}/repos?per_page=100&type=owner&sort=updated"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            repos = resp.json()
        self.cache[key] = repos
        self._save_cache()
        return repos

    async def fetch_repo_readme(self, owner: str, repo: str) -> str:
        key = f"readme:{owner}/{repo}"
        if key in self.cache:
            print(f"Cache hit: readme for {owner}/{repo}")
            return self.cache[key]
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/readme"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers={**self.headers, "Accept": "application/vnd.github.v3.raw"})
            if resp.status_code == 200:
                txt = resp.text
                self.cache[key] = txt
                self._save_cache()
                return txt
        return ""

    async def fetch_repo_languages(self, owner: str, repo: str) -> List[str]:
        key = f"languages:{owner}/{repo}"
        if key in self.cache:
            print(f"Cache hit: languages for {owner}/{repo}")
            return self.cache[key]
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/languages"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code == 200:
                langs = list(resp.json().keys())
                self.cache[key] = langs
                self._save_cache()
                return langs
        return []

    async def fetch_repo_structure(self, owner: str, repo: str) -> List[str]:
        key = f"structure:{owner}/{repo}"
        if key in self.cache:
            print(f"Cache hit: structure for {owner}/{repo}")
            return self.cache[key]
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
                        "dockerfile", "docker-compose.yml", "requirements.txt",
                        "package.json", "pom.xml", "build.gradle", "readme.md"
                    ] or item["name"].startswith(".github"):
                        structure.append(item["name"])
        self.cache[key] = structure
        self._save_cache()
        return structure

    async def fetch_repo_dependencies(self, owner: str, repo: str) -> List[str]:
            key = f"dependencies:{owner}/{repo}"
            if key in self.cache:
                print(f"Cache hit: dependencies for {owner}/{repo}")
                return self.cache[key]
            dependencies = []
            files = ["package.json", "requirements.txt", "pyproject.toml", "Pipfile", "pom.xml", "build.gradle", "Cargo.toml", "go.mod"]
            async with httpx.AsyncClient(timeout=20) as client:
                for file in files:
                    url = f"{self.BASE_URL}/repos/{owner}/{repo}/contents/{file}"
                    resp = await client.get(url, headers={**self.headers, "Accept": "application/vnd.github.v3.raw"})
                    if resp.status_code != 200:
                        continue
                    content = resp.text
                    if file == "package.json":
                        try:
                            data = json.loads(content)
                            deps = list(data.get("dependencies", {}).keys()) + list(data.get("devDependencies", {}).keys())
                            dependencies.extend(deps)
                        except Exception:
                            pass
                    elif file in ("requirements.txt", "Pipfile"):
                        for line in content.splitlines():
                            line = line.strip()
                            if not line or line.startswith("#"): continue
                            dependencies.append(re.split(r"[=<>!~ ]+", line)[0])
                    elif file == "pyproject.toml":
                        for line in content.splitlines():
                            s = line.strip()
                            if "=" in s and "[" not in s and not s.startswith("#"):
                                dependencies.append(s.split("=")[0].strip().strip('"').strip("'"))
                    elif file in ("pom.xml", "build.gradle"):
                        for line in content.splitlines():
                            if "<artifactId>" in line:
                                m = re.search(r"<artifactId>(.*?)</artifactId>", line)
                                if m: dependencies.append(m.group(1).strip())
                            elif "implementation" in line or "api " in line:
                                g = re.search(r"['\"]([A-Za-z0-9_.\-]+:[A-Za-z0-9_.\-]+)['\"]", line)
                                if g: dependencies.append(g.group(1))
                    else:
                        dependencies.append(f"{file} present")
            dependencies = _dedupe(dependencies)
            self.cache[key] = dependencies
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


    def put_cache(self, key: str, value: Any):
        self.cache[key] = value
        self._save_cache()

    def get_cache(self, key: str, default=None):
        return self.cache.get(key, default)