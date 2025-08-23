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
from .code_extractors import summarize_repo_code
from .github_fetcher import GitHubFetcher

class GitHubAnalyzer:
    def __init__(self, llm_api_key: str, fetcher: GitHubFetcher, batch_size: int = 5):
        self.gemini = Gemini(api_key=llm_api_key)
        self.fetcher = fetcher
        self.batch_size = batch_size
    

    async def pre_filter_repos(self, repos: list[dict], jd_text: str) -> list[dict]:
        """
        Lightweight pre-filter:
        - Extracts skills from JD (LLM or fallback simple keywords).
        - Fetches repo languages + dependencies.
        - Keeps only repos that overlap with JD skills.
        """
        jd_skills = self._extract_skills_from_jd(jd_text)
        print(f"[JD Skills] {jd_skills}")

        filtered = []
        for repo in repos:
            owner = (repo.get("owner") or {}).get("login", "")
            name = repo.get("name")

            try:

                if not name or not owner:
                    raise ValueError(f"Invalid repo metadata: {repo}")

                # lightweight signals (no zipball, no deep code scan yet)
                languages = await self.fetcher.fetch_repo_languages(owner, name)
                dependencies = await self.fetcher.fetch_repo_dependencies(owner, name)

                repo_skills = set(languages.keys()) | set(dependencies.keys())
                if any(skill.lower() in [s.lower() for s in repo_skills] for skill in jd_skills):
                    filtered.append(repo)

            except Exception as e:
                print(f"Pre-filter error for {name}: {e}")
                continue

        print(f"[Pre-filter] Reduced repos: {len(filtered)} / {len(repos)}")
        return filtered


    async def build_repo_fingerprint(self, repo: Dict) -> Dict:
        """
        Stage 1: Build a JD-independent fingerprint. Cached by pushed_at.
        """
        name = repo.get("name")
        owner = (repo.get("owner") or {}).get("login", "")

        if not name or not owner:
            return {}

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

    async def analyze_repos(self, repos: List[Dict], jd_text: str) -> List[Dict]:
        
        # stage 1: fingerprints(JD-independent,persisted)
        fingerprints:List[Dict] = []
        for repo in repos:
            fp = await self.build_repo_fingerprint(repo)
            if len(fp) > 0:
                fingerprints.append(fp)

        # stage 2: batch score against JD (LLM batching + internal caching)
        scored = self.gemini.batch_score_repos(jd_text,fingerprints,batch_size=self.batch_size)

        # sort by score desc
        scored.sort(key=lambda x: x.get("relevance_score", 0.0), reverse=True)
        
        return scored

    def _extract_skills_from_jd(self,jd_text:str)->List[str]:
        """
        Extract skills from the JD.
        - Uses LLM if available, else falls back to simple regex-based keywords.
        """
        if not self.gemini:
            # fallback simple keyword extraction
            keywords = re.findall(r"\b[A-Za-z][A-Za-z0-9\.\+#-]+\b", jd_text)
            return list(set(keywords))

        prompt = f"""
        Extract all technical skills, frameworks, libraries, and tools from this Job Description.
        Return as a JSON list of strings. No explanation.

        JD:
        {jd_text}
        """
        try:
            resp = self.gemini.generate(prompt)
            skills = json.loads(resp)
            if isinstance(skills, list):
                return skills
        except Exception as e:
            print(f"LLM JD skill extraction failed: {e}")

        # fallback if LLM fails
        keywords = re.findall(r"\b[A-Za-z][A-Za-z0-9\.\+#-]+\b", jd_text)
        return list(set(keywords))