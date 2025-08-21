# app/services/github_analyzer.py
import httpx
import time
import json
import os
from typing import List, Dict, Optional
from google.genai import Client


class GitHubFetcher:
    BASE_URL = "https://api.github.com"
    CACHE_FILE = "github_cache.json"

    def __init__(self, token: Optional[str] = None):
        self.token = token
        self.headers = {"Authorization": f"token {self.token}"} if token else {}
        # Load cache if exists
        if os.path.exists(self.CACHE_FILE):
            with open(self.CACHE_FILE, "r") as f:
                self.cache = json.load(f)
        else:
            self.cache = {}

    def _save_cache(self):
        with open(self.CACHE_FILE, "w") as f:
            json.dump(self.cache, f, indent=2)

    async def fetch_user_repos(self, username: str) -> List[Dict]:
        cache_key = f"user_repos:{username}"
        if cache_key in self.cache:
            print(f"Cache hit: repos for {username}")
            return self.cache[cache_key]

        url = f"{self.BASE_URL}/users/{username}/repos"
        async with httpx.AsyncClient(timeout=15) as client:
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
        async with httpx.AsyncClient(timeout=15) as client:
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

    async def fetch_repo_languages(self, owner: str, repo: str) -> List[str]:
        cache_key = f"languages:{owner}/{repo}"
        if cache_key in self.cache:
            print(f"Cache hit: languages for {owner}/{repo}")
            return self.cache[cache_key]

        url = f"{self.BASE_URL}/repos/{owner}/{repo}/languages"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code == 200:
                langs = list(resp.json().keys())
                self.cache[cache_key] = langs
                self._save_cache()
                return langs
            return []

    async def fetch_repo_structure(self, owner: str, repo: str) -> List[str]:
        cache_key = f"structure:{owner}/{repo}"
        if cache_key in self.cache:
            print(f"Cache hit: structure for {owner}/{repo}")
            return self.cache[cache_key]

        url = f"{self.BASE_URL}/repos/{owner}/{repo}/contents"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code != 200:
                return []

            items = resp.json()
            structure = []
            for item in items:
                if item["type"] == "dir":
                    structure.append(item["name"] + "/")
                else:
                    # Capture key files only
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

    async def fetch_repo_dependencies(self, owner: str, repo: str) -> List[str]:
        cache_key = f"dependencies:{owner}/{repo}"
        if cache_key in self.cache:
            print(f"Cache hit: dependencies for {owner}/{repo}")
            return self.cache[cache_key]

        dependencies = []
        files_to_check = [
            "package.json", "requirements.txt", "pyproject.toml",
            "Pipfile", "pom.xml", "build.gradle", "Cargo.toml", "go.mod"
        ]

        async with httpx.AsyncClient(timeout=15) as client:
            for file in files_to_check:
                url = f"{self.BASE_URL}/repos/{owner}/{repo}/contents/{file}"
                resp = await client.get(
                    url, headers={**self.headers, "Accept": "application/vnd.github.v3.raw"}
                )
                if resp.status_code == 200:
                    content = resp.text
                    # Parse basic dependencies
                    if file == "package.json":
                        try:
                            data = json.loads(content)
                            deps = list(data.get("dependencies", {}).keys()) + \
                                    list(data.get("devDependencies", {}).keys())
                            dependencies.extend(deps)
                        except Exception:
                            pass
                    elif file in ("requirements.txt", "Pipfile"):
                        deps = [line.split("==")[0].strip() for line in content.splitlines() if line and not line.startswith("#")]
                        dependencies.extend(deps)
                    elif file == "pyproject.toml":
                        for line in content.splitlines():
                            if "=" in line and "[" not in line:
                                dependencies.append(line.split("=")[0].strip())
                    elif file in ("pom.xml", "build.gradle"):
                        # Simplified: just grab artifactId or implementation lines
                        for line in content.splitlines():
                            if "<artifactId>" in line or "implementation" in line:
                                dependencies.append(line.strip())
                    else:
                        dependencies.append(f"{file} present")

        dependencies = list(set(dependencies))  # dedupe
        self.cache[cache_key] = dependencies
        self._save_cache()
        return dependencies


class GitHubAnalyzer:
    def __init__(self, llm_api_key: str, fetcher: GitHubFetcher):
        self.client = Client(api_key=llm_api_key)
        self.fetcher = fetcher

    async def analyze_repos(self, repos: List[Dict], jd_text: str) -> List[Dict]:
        relevant_projects = []

        for repo in repos:
            name = repo.get("name")
            owner = repo.get("owner", {}).get("login", "")
            description = repo.get("description", "")

            readme = await self.fetcher.fetch_repo_readme(owner, name)
            languages = await self.fetcher.fetch_repo_languages(owner, name)
            dependencies = await self.fetcher.fetch_repo_dependencies(owner, name)
            structure = await self.fetcher.fetch_repo_structure(owner, name)

            maturity = []
            if "tests" in structure: maturity.append("Has tests")
            if "Dockerfile" in structure: maturity.append("Dockerized")
            if ".github/workflows" in structure: maturity.append("CI/CD enabled")

            project_profile = f"""
            Repository: {name}
            Description: {description}
            Languages: {', '.join(languages)}
            Dependencies: {', '.join(dependencies)}
            Repo Structure: {', '.join(structure)}
            Maturity Signals: {', '.join(maturity)}
            README Content:
            {readme}
            """

            project_info = await self._llm_extract_skills_and_score(
                project_profile, jd_text, name
            )
            relevant_projects.append(project_info)
            print(project_info)
            time.sleep(3)

        relevant_projects.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
        return relevant_projects
        
    async def _llm_extract_skills_and_score(
        self, project_text: str, jd_text: str, repo_name: str
    ) -> Dict:
        prompt = f"""
                        You are analyzing if a GitHub repo is relevant to a Job Description.

                        Job Description:
                        {jd_text}

                        Project Profile:
                        {project_text}

                        Task:
                        1. Extract key skills/technologies from the repo.
                        2. Compare them with JD requirements.
                        3. Output a JSON with fields:
                        - name
                        - skills (list)
                        - relevance_score (0.0 - 1.0 float)
                        - reasoning (why this score was given)
                        Only return valid JSON.
                """

        try:
            response = self.client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
            )
            content = response.text.strip()
            return json.loads(content)
        except json.JSONDecodeError:
            return {"name": repo_name, "skills": [], "relevance_score": 0.0}
        except Exception as e:
            print(f"LLM error for {repo_name}: {e}")
            return {"name": repo_name, "skills": [], "relevance_score": 0.0}


class GitHubProfileService:
    """
    Complete service to fetch + analyze GitHub profile for a user.
    """

    def __init__(self, token: Optional[str] = None, llm_api_key: Optional[str] = None):
        self.fetcher = GitHubFetcher(token)
        self.analyzer = GitHubAnalyzer(llm_api_key, self.fetcher)

    async def build_profile(self, username: str, jd_text: str) -> Dict:
        repos = await self.fetcher.fetch_user_repos(username)
        projects = await self.analyzer.analyze_repos(repos, jd_text)

        # Aggregate skills
        skills_set = set()
        for p in projects:
            skills_set.update(p.get("skills", []))

        profile = {
            "user_info": {"github_username": username},
            "skills": list(skills_set),
            "projects": projects,
            "stats": {
                "public_repos": len(repos),
                "followers": 0,  # optional: fetch via GitHub user API if needed
            },
        }
        return profile
