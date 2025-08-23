# app/resume/builder.py
import hashlib
import json
import os
from typing import Any, Dict, List, Optional
from app.services.gemini_client import Gemini
from .utils import _load_cache,_save_cache

CACHE_FILE = os.path.join(".cache", "resume_cache.json")
os.makedirs(".cache", exist_ok=True)


class ResumeBuilder:
    """
    - JD-independent project bullets cached by repo fingerprint (name+pushed_at)
    - JD-specific alignment is lightweight and optionally LLM-batched later
    - Professional summary generated once per JD (and cached)
    """
    def __init__(self, llm_api_key: Optional[str] = None):
        self.gemini = Gemini(api_key=llm_api_key)
        self._cache = _load_cache(CACHE_FILE)

    def _project_base_key(self, repo_name: str, pushed_at: str):
        h = hashlib.sha1(f"{repo_name}:{pushed_at}".encode()).hexdigest()[:12]
        return f"proj_base:{repo_name}:{h}"

    def _summary_key(self, skills: List[str], jd_text: str):
        h = hashlib.sha1((json.dumps(skills, sort_keys=True) + jd_text).encode()).hexdigest()[:12]
        return f"summary:{h}"

    def build_resume_sections(self, profile: Dict, jd_text: str) -> Dict:
        skills: List[str] = profile.get("skills", [])
        projects: List[Dict] = profile.get("projects", [])

        # Pick top 3 projects
        top_projects = sorted(projects, key=lambda p: p.get("relevance_score", 0.0), reverse=True)[:3]

        # 1) Summary (cached per JD)
        sum_key = self._summary_key(skills, jd_text)
        if sum_key in self._cache:
            summary = self._cache[sum_key]
        else:
            project_names = ", ".join([p.get("name") for p in top_projects if p.get("name")])
            prompt = f"""
You are an ATS-friendly resume writer.
Job Description:
{jd_text}

Candidate Skills: {', '.join(skills)}
Candidate Projects: {project_names}

Task:
- Write a crisp 3–4 line professional summary tailored to the JD.
- Highlight the closest-matching skills and impact.
- Keep it factual and buzzword-light.
"""
            summary = self.gemini.generate(prompt)
            self._cache[sum_key] = summary
            _save_cache(CACHE_FILE, self._cache)

        # 2) Project enhancements
        enhanced_projects: List[Dict] = []
        for p in top_projects:
            name = p.get("name")
            pushed_at = p.get("pushed_at", "") or ""  # pass through from analyzer (if available)
            base_key = self._project_base_key(name, pushed_at)

            # JD-independent base bullets (cached once per repo version)
            if base_key in self._cache:
                base = self._cache[base_key]
            else:
                prompt = f"""
You are rewriting a GitHub project into resume-ready bullets (JD-independent).

Project:
Name: {name}
Original Skills: {', '.join(p.get('skills', []))}
Context (if any): {p.get('reasoning', '')}

Write 2–4 bullets with:
- Action verbs, outcome/impact where possible (numbers if available)
- Technology names explicitly
- Keep each bullet <= 20 words
Return JSON:
{{ "name": "{name}", "bullets": ["...","..."], "tech": ["...","..."] }}
"""
                base = self.gemini.generate_json(prompt, fallback={"name": name, "bullets": [], "tech": []})
                self._cache[base_key] = base
                _save_cache(CACHE_FILE, self._cache)

            # JD-specific light alignment (rule-based to save tokens)
            jd_lower = jd_text.lower()
            matched = [t for t in (base.get("tech") or []) if t and t.lower() in jd_lower]
            # Move matched tech to front of skills list; keep top 6
            aligned_skills = sorted(set((matched + (p.get("skills") or []))), key=lambda x: (x not in matched, x))[:6]

            enhanced_projects.append({
                "name": name,
                "description": base.get("bullets", []) or p.get("description", []),
                "skills": aligned_skills,
                "relevance_score": p.get("relevance_score", 0.0)
            })

        return {
            "summary": summary.strip(),
            "skills": skills,
            "projects": enhanced_projects
        }
