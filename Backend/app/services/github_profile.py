from .github_fetcher import GitHubFetcher
from .github_analyzer import GitHubAnalyzer
from app.resume.builder import ResumeBuilder

class GitHubProfileService:
    """
    Complete service to fetch + analyze GitHub profile for a user.
    Uses 2-stage pipeline with code-level analysis cached per repo.
    """
    def __init__(self, token = None, llm_api_key = None):
        self.fetcher = GitHubFetcher(token)
        self.analyzer = GitHubAnalyzer(llm_api_key, self.fetcher)
        self.resume_builder = ResumeBuilder(llm_api_key)

    async def build_profile(self, username: str, jd_text: str) -> dict:
        repos = await self.fetcher.fetch_user_repos(username)

        # Optionally filter forks/archived to reduce noise
        repos = [r for r in repos if not r.get("fork") and not r.get("archived")]

        # 2 calls for each repo(readme + score. AT JD) = 2n calls
        projects = await self.analyzer.analyze_repos(repos, jd_text)

        print(projects)

        # Aggregate skills
        skills_set = set()
        for p in projects:
            for s in p.get("skills", []):
                if isinstance(s, str) and s:
                    skills_set.add(s)

        profile = {
            "user_info": {"github_username": username},
            "skills": sorted(list(skills_set)),
            "projects": projects,
            "stats": {
                "public_repos": len(repos),
            },
        }

        # Attach resume-ready data
        # 1 call for each project + 1 for user summary = (n+1)calls
        resume_data = self.resume_builder.build_resume_sections(profile, jd_text)
        profile["resume_ready"] = resume_data

        return profile

# total calls = 2n + n + 1 = 3n +1