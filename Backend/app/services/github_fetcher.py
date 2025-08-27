import os
import re
import json
import toml
import yaml
import base64
import logging
import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional
from collections import defaultdict, Counter
from dataclasses import dataclass
from .dependency_extractor import DependencyExtractor
from .cache import Cache,cached,cache_key,DEFAULT_CACHE_TTL

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

    def _is_skippable_folder(self, path: str) -> bool:
        folder_name = os.path.basename(path)
        return folder_name in (".git", "node_modules",".venv","venv","__pycache__","dist","build","public","assets","cache",".cache")

    @cached("blob")
    def _get_file_text(self, repo: Repository.Repository, path: str) -> Optional[str]:

        try:
            cf = repo.get_contents(path)
            # large files: skip
            size = getattr(cf, "size", None)
            if size is not None and size > MAX_FETCH_BYTES:
                return None
            data = cf.decoded_content
            return data.decode("utf-8", errors="ignore")
        except GithubException as e:
            if e.status != 404:
                logger.debug(f"get_file_text error {repo.full_name}:{path} -> {e}")
            return None
        except Exception as e:
            logger.debug(f"get_file_text error {repo.full_name}:{path} -> {e}")            
            return None

    # @cached("repos")
    def get_all_repositories(self) -> List[Repository.Repository]:
        try:
            repos = list(self.user.get_repos())
            logger.info(f"User repos: {len(repos)}")
            return repos
        except GithubException as e:
            logger.error(f"Error fetching repos: {e}")
            return []

    # @cached("repo")
    def get_repository_by_full_name(self, full_name: str) -> Optional[Repository.Repository]:
        try:
            return self.github.get_repo(full_name)
        except GithubException as e:
            logger.error(f"Error fetching {full_name}: {e}")
            return None

    # TODO:
    def get_repo_created_date():pass

    # TODO:
    def get_repo_last_updated_date():pass

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
            contents  = repo.get_contents("")
            while contents:
                file_content = contents.pop(0)

                # enqueue its children
                if file_content.type == "dir":
                    if not self._is_skippable_folder(file_content.path):
                        contents.extend(repo.get_contents(file_content.path))
                    continue
                
                # extracting only the filename
                fname = os.path.basename(file_content.path)

                # filtering for known dependency files
                if fname not in self.dependency_extractor.DEP_FILES:
                    continue
                
                # encoding in normal string
                text = file_content.decoded_content.decode("utf-8", errors="ignore")
                if not text:
                    continue
                
                # extract dependencies from the dependency file
                extracted = self.dependency_extractor.extract_from_file(fname, text)
                
                # filtering unique dependencies
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
        for name in ["README.md", "Readme.md", "README.MD", "readme.md", "README", "readme"]:
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
        orgs: Optional[List[str]] = None,
        limit: Optional[int] = None,
        out_json: str = "github_repo_fetch.json",
    ) -> List[Dict[str, Any]]:
        repos: List[Repository.Repository] = []

        # return if already fetched all the repos
        if os.path.exists(out_json):
            with open(out_json, "r", encoding="utf-8") as f:
                repos_result = json.load(f)
            logger.info(f"Loaded existing report with {len(repos)} entries from {out_json}")
            if repos_result.get("repos", []) and repos_result.get("fetched_all"):
                return repos_result.get("repos", [])

        # fetch specified repos
        if repo_full_names and len(repo_full_names) > 0:
            for f in repo_full_names:
                repo = self.get_repository_by_full_name(f)
                if repo:
                    repos.append(repo)

        # otherwise fetch all the repos
        else:
            fetched_repos = self.get_all_repositories()
            if fetched_repos:
                repos.extend(fetched_repos)
            else:
                raise RuntimeError("received None from get_all_repositories")

        # filtering unique repos
        seen = set()
        uniq: List[Repository.Repository] = []
        for r in repos:
            if r.full_name not in seen:
                uniq.append(r)
                seen.add(r.full_name)

        # sort by created_at (newest first) then limit
        uniq.sort(key=lambda r: r.pushed_at or r.created_at, reverse=True)
        if limit:
            uniq = uniq[:limit]

        # extract required repository information
        results: List[Dict[str, Any]] = []
        for r in uniq:
            try:
                results.append(self.extract_repository(r))
                logger.info(f"✓ {r.full_name}")
            except Exception as e:
                logger.error(f"Fetch failed for {r.full_name}: {e}")

        # write JSON report
        try:
            # {len,
            #  fetched_all,
            #  repos }
            fetched_all = True if not repo_full_names or len(repo_full_names) == 0 else False
            with open(out_json, "w", encoding="utf-8") as f:
                # TODO: add timestamsps to know when had fetched a repo
                json.dump({time:datetime.now(),"len": len(repos), "all": fetched_all, "repos": results}, f, indent=2)
            logger.info(f"Wrote report -> {out_json}")
        except Exception as e:
            logger.error(f"Failed writing report: {e}")

        return results

