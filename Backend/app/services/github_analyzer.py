import httpx
import time
import json
import os
import re
import zipfile
import tempfile
import shutil
from typing import List, Dict, Optional, Tuple, Any
from .gemini_client import Gemini
from .code_extractors import summarize_repo_code,_safe_json_loads
from .github_fetcher import GitHubFetcher

class GitHubAnalyzer:
    def __init__(self, llm_api_key: str, fetcher: GitHubFetcher, batch_size: int = 5):
        self.gemini = Gemini(api_key=llm_api_key)
        self.fetcher = fetcher
        self.batch_size = batch_size

    async def _summarize_readme(self, readme: str) -> str:
        prompt = f"Summarize the following README in <=5 sentences focusing on tech stack and functionality:\n\n{readme}"
        return self.gemini.generate(prompt)
    
    async def build_repo_fingerprint(self, repo: Dict) -> Dict:
        """
        Stage 1: Build a JD-independent fingerprint. Cached by pushed_at.
        """
        name = repo.get("name")
        owner = (repo.get("owner") or {}).get("login", "")
        description = repo.get("description") or ""
        pushed_at = repo.get("pushed_at") or ""  # cache invalidation key
        default_branch = repo.get("default_branch") or "main"

        cache_key = f"fingerprint:{owner}/{name}:{pushed_at}"
        cached = self.fetcher.get_cache(cache_key)

        if cached:
            print(f"Cache hit: fingerprint for {owner}/{name}")
            return cached

        # Lightweight API signals
        readme = await self.fetcher.fetch_repo_readme(owner, name)
        languages = await self.fetcher.fetch_repo_languages(owner, name)
        dependencies = await self.fetcher.fetch_repo_dependencies(owner, name)
        structure = await self.fetcher.fetch_repo_structure(owner, name)
        
        maturity = []
        sl = [s.lower() for s in structure]

        if any(s.startswith("tests") or s == "tests/" for s in structure):
            maturity.append("Has tests")

        if ("dockerfile" in sl) or ("dockerfile" in structure) or any(s.endswith("docker-compose.yml") for s in structure):
            maturity.append("Dockerized")

        if any(s.startswith(".github") for s in structure):
            maturity.append("CI/CD enabled")

        # # TODO: to be removed
        # readme_summary = await self._summarize_readme(readme) if readme and len(readme) > 0 else ""

        # Code-level extraction via zipball (fast, no git)
        repo_dir = await self.fetcher.download_repo_zip(owner, name, ref=default_branch)

        try:
            code_summary = summarize_repo_code(repo_dir)
        finally:
            # cleanup temp dir root
            try:
                top = os.path.dirname(repo_dir)
                shutil.rmtree(top, ignore_errors=True)
            except Exception:
                pass

        fingerprint = {
            "name": name,
            "owner": owner,
            "description": description,
            "pushed_at": pushed_at,
            "languages": languages,
            "dependencies": dependencies,
            "structure": structure,
            "maturity": maturity,
            "readme_excerpt": (readme[:4000] if readme else ""),
            "code_summary": code_summary,
        }

        self.fetcher.put_cache(cache_key, fingerprint)
        return fingerprint

    async def score_repo_for_jd(self, fingerprint: Dict, jd_text: str) -> Dict:
        """
        Stage 2: LLM scoring using cached fingerprint.
        """
        name = fingerprint.get("name", "repo")
        fingerprint_text = json.dumps(fingerprint, indent=2)

        prompt = f"""
            You are an expert technical recruiter and senior software engineer.
            Decide how relevant this GitHub repository is to the Job Description.

            Job Description:
            {jd_text}

            Repository Fingerprint (JSON):
            {fingerprint_text}

            Instructions:
            1) Derive skills/technologies from dependencies, languages, code_summary (imports/functions/routes), and maturity signals.
            2) Detect patterns like: REST API, SQL usage, authentication, microservices, queues, tests, CI/CD, containerization.
            3) Score relevance strictly from 0.0 to 1.0 (float).
            4) Output ONLY valid JSON in this exact schema:
            {{
            "name": "{name}",
            "skills": ["list", "key", "skills"],
            "relevance_score": 0.0,
            "reasoning": "short concise explanation grounded in the fingerprint vs JD"
            }}
            """

        try:
            content = self.gemini.generate(prompt)
            parsed = _safe_json_loads(content, fallback=None)
            if isinstance(parsed, dict) and "name" in parsed:
                return parsed
            return {"name": name, "skills": [], "relevance_score": 0.0, "reasoning": "LLM parse failed"}
        except Exception as e:
            print(f"Scoring error for {name}: {e}")
            return {"name": name, "skills": [], "relevance_score": 0.0, "reasoning": "LLM error"}

    async def analyze_repos(self, repos: List[Dict], jd_text: str) -> List[Dict]:
        """
        Full pipeline: Fingerprint → Score against JD.
        Fingerprints are cached (keyed by pushed_at) and reused across JDs.
        """
        results = []
        for repo in repos:
            fingerprint = await self.build_repo_fingerprint(repo)
            project_info = await self.score_repo_for_jd(fingerprint, jd_text)
            results.append(project_info)
            time.sleep(1.2)  # gentle rate-limiting for LLM/API
        results.sort(key=lambda x: x.get("relevance_score", 0.0), reverse=True)
        return results

