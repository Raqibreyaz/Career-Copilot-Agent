import os
import re
import json
import toml
import yaml
import base64
import logging
import hashlib
from typing import Any, Dict, List, Optional
from collections import defaultdict, Counter
from dataclasses import dataclass
from .dependency_extractor import DependencyExtractor
from .cache import Cache,cached,DEFAULT_CACHE_TTL

import redis
from github import Github, GithubException, Repository

MAX_FETCH_BYTES = 2_000_000  # 2MB safety limit

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

class GitHubFetcher:
    def __init__(
        self,
        github_token: str,
        redis_url: Optional[str] = "redis://localhost:6379/0",
        cache_ttl: int = DEFAULT_CACHE_TTL,
    ):
        self.github = Github(github_token, per_page=100)
        self.user = self.github.get_user()
        self.dependency_extractor = DependencyExtractor()
        logger.info(f"Authenticated as: {self.user.login}")

        client = None
        if redis_url:
            try:
                client = redis.from_url(redis_url, decode_responses=True)
                client.ping()
                logger.info("Connected to Redis.")
            except Exception as e:
                logger.warning(f"Redis unavailable ({e}); using in-memory cache.")
                client = None

        self.cache = Cache(client=client, ttl=cache_ttl)

    def _get_file_text(self, repo: Repository.Repository, path: str) -> Optional[str]:
        key = self._cache_key("blob", repo.full_name, path)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        try:
            cf = repo.get_contents(path)
            # large files: skip
            size = getattr(cf, "size", None)
            if size is not None and size > MAX_FETCH_BYTES:
                self.cache.set(key, None)
                return None
            data = cf.decoded_content
            text = data.decode("utf-8", errors="ignore")
            self.cache.set(key, text)
            return text
        except GithubException as e:
            if e.status != 404:
                logger.debug(f"get_file_text error {repo.full_name}:{path} -> {e}")
            self.cache.set(key, None)
            return None
        except Exception as e:
            logger.debug(f"get_file_text error {repo.full_name}:{path} -> {e}")
            self.cache.set(key, None)
            return None

    @cached("repos")
    def get_all_repositories(self) -> List[Repository.Repository]:
        try:
            repos = list(self.user.get_repos())
            logger.info(f"User repos: {len(repos)}")
            return repos
        except GithubException as e:
            logger.error(f"Error fetching repos: {e}")
            return []

    def get_repository_by_full_name(self, full_name: str) -> Optional[Repository.Repository]:
        try:
            return self.github.get_repo(full_name)
        except GithubException as e:
            logger.error(f"Error fetching {full_name}: {e}")
            return None

    @cached("topics")
    def get_topics(self, repo: Repository.Repository) -> List[str]:
        try:
            topics = repo.get_topics() or []
            return topics
        except GithubException as e:
            logger.debug(f"get_topics error {repo.full_name}: {e}")
            return []

    @cached("dependencies")
    def get_dependencies(self, repo: Repository.Repository) -> List[str]:
        deps: List[str] = []
        seen = set()
        try:
            tree = self._get_tree(repo)
            for node in tree:
                if node["type"] != "blob":
                    continue
                fname = os.path.basename(node["path"])
                if fname not in self.dependency_extractor.DEP_FILES:
                    continue

                text = self._get_file_text(repo, node["path"])
                if not text:
                    continue

                extracted = self.dependency_extractor.extract_from_file(fname, text)
                for dep in extracted:
                    if dep and dep not in seen:
                        deps.append(dep)
                        seen.add(dep)

            return deps

        except Exception as e:
            logger.error(f"Dependency extraction failed for {repo.full_name}: {e}")
            return []

    def get_description(self, repo: Repository.Repository)->str:
        return repo.description or ""

    @cached("languages")
    def detect_languages(self, repo: Repository.Repository) -> Dict[str, int]:
        try:
            langs = repo.get_languages() or {}
            return langs
        except GithubException as e:
            logger.debug(f"get_languages error: {e}")
            return {}

    @cached("readme")
    def get_readme(self, repo: Repository.Repository) -> Optional[str]:
      
        # Try common names quickly; fall back to API get_readme()
        for name in ["README.md", "README.MD", "Readme.md", "readme.md", "README", "readme"]:
            txt = self._get_file_text(repo, name)
            if txt:
                return txt

        try:
            rd = repo.get_readme()
            txt = rd.decoded_content.decode("utf-8", errors="ignore")
            return txt
        except Exception:
            return None

    # ---------- High-level API ----------
    def extract_repository(self, repo: Repository.Repository) -> Dict[str, Any]:
        if not repo:
            raise RuntimeError(f"Repository not found")

        return {
                "name": repo.full_name,
                "topics": self.get_topics(repo),
                "description": self.get_description(repo),
                "readme": self.get_readme(repo),
                "dependencies": self.get_dependencies(repo),
                "languages": self.detect_languages(repo)
            }

    # works for both :
        # specified repos (repo_full_names)
        # all repos (when no specific repos are given)
    def extract_many(
        self,
        repo_full_names: Optional[List[str]] = None,
        include_user_repos: bool = False,
        orgs: Optional[List[str]] = None,
        limit: Optional[int] = None,
        out_json: str = "github_repo_fetch.json",
    ) -> List[Dict[str, Any]]:
        repos: List[Repository.Repository] = []

        if repo_full_names:
            for f in repo_full_names:
                repo = self.get_repository_by_full_name(f)
                if repo:
                    repos.append(repo)

        if include_user_repos:
            repos.extend(self.get_all_repositories())

        # de-dup by full_name
        # seen = set()
        uniq: List[Repository.Repository] = []
        for r in repos:
            if r.full_name not in seen:
                uniq.append(r)
                # seen.add(r.full_name)

        # sort by created_at (newest first) then limit
        uniq.sort(key=lambda r: r.pushed_at or r.created_at, reverse=True)
        if limit:
            uniq = uniq[:limit]

        results: List[Dict[str, Any]] = []
        for r in uniq:
            try:
                results.append(self.extract_repository(r))
                logger.info(f"✓ {r.full_name}")
            except Exception as e:
                logger.error(f"Fetch failed for {r.full_name}: {e}")

        # write JSON report
        try:
            with open(out_json, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2)
            logger.info(f"Wrote report -> {out_json}")
        except Exception as e:
            logger.error(f"Failed writing report: {e}")

        return results

