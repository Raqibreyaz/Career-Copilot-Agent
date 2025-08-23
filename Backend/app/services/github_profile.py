from typing import Dict, List, Optional

from .github_analyzer import GitHubAnalyzer
from .github_fetcher import GitHubFetcher
from app.resume.builder import ResumeBuilder

class GitHubProfileService:
    """
    Complete service: fetch → fingerprint (cache) → batch-score (LLM) → resume sections.
    Automatically re-fingerprints if repo was updated (cache key uses pushed_at).
    """
    def __init__(self, token: Optional[str] = None, llm_api_key: Optional[str] = None, batch_size: int = 5):
        self.fetcher = GitHubFetcher(token)
        self.analyzer = GitHubAnalyzer(llm_api_key, self.fetcher, batch_size=batch_size)
        self.resume_builder = ResumeBuilder(llm_api_key)

    async def build_profile(self, username: str, jd_text: str) -> Dict:
        repos = await self.fetcher.fetch_user_repos(username)
        repos = [r for r in repos if not r.get("fork") and not r.get("archived")]

        # analyze (n repos → ~n/batch_size LLM calls)
        projects = await self.analyzer.analyze_repos(repos, jd_text)

        # aggregate skills from scored projects
        skills_set = set()
        for p in projects:
            for s in p.get("skills", []):
                if isinstance(s, str) and s:
                    skills_set.add(s)

        profile = {
            "user_info": {"github_username": username},
            "skills": sorted(list(skills_set)),
            "projects": projects,
            "stats": {"public_repos": len(repos)},
        }

        profile["resume_ready"] = self.resume_builder.build_resume_sections(profile, jd_text)
        return profile
