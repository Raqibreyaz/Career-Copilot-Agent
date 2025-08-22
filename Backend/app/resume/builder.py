from app.services.gemini_client import Gemini

class ResumeBuilder:
    """
    Converts GitHub profile analysis into resume-ready structured data.
    Uses Google Generative AI to generate ATS-friendly summaries & filtering.
    Includes caching so repo enhancement is reused across JDs.
    """
    def __init__(self,api_key=str|None):  
        self.gemini = Gemini(api_key)
        self._cache = {}  # JD-independent cache: repo_name -> enhanced project JSON

    def build_resume_sections(self, profile: dict, jd_text: str) -> dict:
        """
        Main pipeline:
        1. Select top projects
        2. Generate ATS-friendly summary
        3. Enhance project descriptions (cached if already done)
        """
        skills = profile.get("skills", [])
        projects = profile.get("projects", [])

        # Filter top projects by relevance
        top_projects = sorted(
            projects,
            key=lambda p: p.get("relevance_score", 0),
            reverse=True
        )[:3]

        # Generate ATS-friendly professional summary
        summary = self._generate_summary(skills, top_projects, jd_text)

        print(summary)
        time.sleep(2)

        # Enhance projects (cache + LLM)
        enhanced_projects = self._enhance_projects(top_projects, jd_text)

        print(enhanced_projects)

        return {
            "summary": summary,
            "skills": skills,
            "projects": enhanced_projects
        }

    def _generate_summary(self, skills: list[str], projects: list[dict], jd_text: str) -> str:
        """
        Uses LLM to generate a professional summary tailored to the JD.
        """
        if not self.gemini:
            # Fallback if no API key
            return f"Developer experienced in {', '.join(skills[:5])}, with hands-on project experience."

        project_names = ", ".join([p.get("name") for p in projects if p.get("name")])

        prompt = f"""
        You are an ATS-friendly resume writer.
        Job Description: {jd_text}

        Candidate Skills: {', '.join(skills)}
        Candidate Projects: {project_names}

        Task:
        - Write a crisp 3-4 line professional summary tailored to the JD.
        - Highlight key skills matching the JD.
        - Mention 1-2 projects if highly relevant.
        """

        try:
            resp = self.gemini.generate(prompt)
        except Exception:
            return f"Developer skilled in {', '.join(skills[:5])}, with relevant GitHub project experience."

    def _enhance_projects(self, projects: list[dict], jd_text: str) -> list[dict]:
        """
        Uses LLM to rewrite project descriptions to be resume-ready.
        Caches JD-independent enhancement (so raw project is only analyzed once).
        """
        enhanced = []
        for p in projects:
            repo_name = p.get("name")
            if not repo_name:
                continue

            # If cached, reuse JD-independent enhancement
            if repo_name in self._cache:
                cached_proj = self._cache[repo_name].copy()
                cached_proj["relevance_score"] = p.get("relevance_score", 0)  # update dynamic score
                enhanced.append(cached_proj)
                continue

            desc = p.get("description", "")
            if not self.gemini:
                enhanced.append(p)
                continue

            prompt = f"""
            You are rewriting project descriptions for a resume.

            Job Description: {jd_text}
            Project Name: {p.get('name')}
            Raw Description: {desc}
            Project Skills: {', '.join(p.get('skills', []))}

            Task:
            - Rewrite description in 2-3 impactful bullet points.
            - Use action verbs, quantify improvements if possible.
            - Tailor emphasis to match the JD.
            - Output JSON with fields:
              {{
                "name": "string",
                "description": ["bullet point 1", "bullet point 2"],
                "skills": ["list", "of", "skills"],
                "relevance_score": float
              }}
            """

            try:
                resp = self.gemini.generate(prompt)

                # Ensure valid JSON
                parsed = json.loads(content)
                # Cache JD-independent enhancement
                self._cache[repo_name] = {
                    "name": parsed.get("name", repo_name),
                    "description": parsed.get("description", []),
                    "skills": parsed.get("skills", []),
                }
                # Add JD-specific relevance
                parsed["relevance_score"] = p.get("relevance_score", 0)
                enhanced.append(parsed)

                print(parsed)

                time.sleep(2);
            except Exception:
                enhanced.append(p)

        return enhanced
