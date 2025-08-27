# app/resume/generator.py
import json
import re
from typing import Dict, List, Any, Optional

# Small safe JSON loader utility
def _safe_json_loads(s: str, fallback: Any = None) -> Any:
    try:
        return json.loads(s)
    except Exception:
        # try to find first {...} block
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(s[start:end+1])
            except Exception:
                return fallback
    return fallback

def _norm_list(items: List[str]) -> List[str]:
    return [i.lower().strip() for i in items if isinstance(i, str) and i.strip()]

def _count_keyword_hits(text: str, keywords: List[str]) -> int:
    if not text or not keywords:
        return 0
    text_l = text.lower()
    hits = 0
    for k in keywords:
        if k and k.lower() in text_l:
            hits += 1
    return hits

# Main function
def build_jd_specific_resume(
    github_summary: Dict[str, Any],
    jd_text: str,
    gemini,           # instance of Gemini with .generate(prompt) -> str
    top_k: int = 3,
) -> Dict[str, Any]:
    """
    Produce jd-specific JSON with top projects and skills.
    github_summary: {
      "len": int,
      "all": ...,
      "repos": [
         {"name": "", "topics": [...], "description": "", "readme": "", "dependencies": [...], "languages": [...]}
      ]
    }
    gemini: object with generate(prompt: str) -> str
    """

    # 1) Parse JD for required languages, dependencies, and ATS keywords using LLM
    jd_parse_prompt = f"""
    You are an assistant that extracts technical requirements from a job description.
    Return a JSON object with keys:
    - required_languages: list of programming languages (exact names) that are MUST-HAVE (empty list if none).
    - required_dependencies: list of package / framework names that are MUST-HAVE (empty list if none).
    - preferred_skills: list of important but not strictly required skills.
    - keywords: list of short keywords useful for ATS matching.

    Job Description:
    \"\"\"{jd_text}\"\"\"

    Return ONLY valid JSON.
    """
    jd_parse_raw = gemini.generate(jd_parse_prompt)
    jd_parsed = _safe_json_loads(jd_parse_raw, fallback={})
    # normalize
    required_langs = _norm_list(jd_parsed.get("required_languages", []) if isinstance(jd_parsed, dict) else [])
    required_deps = _norm_list(jd_parsed.get("required_dependencies", []) if isinstance(jd_parsed, dict) else [])
    preferred = _norm_list(jd_parsed.get("preferred_skills", []) if isinstance(jd_parsed, dict) else [])
    keywords = _norm_list(jd_parsed.get("keywords", []) if isinstance(jd_parsed, dict) else [])

    # fallback simple heuristics if LLM returned nothing
    if not required_langs and not required_deps and not keywords:
        # simple regex heuristics
        lang_candidates = ["python", "java", "javascript", "typescript", "go", "c++", "c", "ruby", "php", "rust"]
        for lc in lang_candidates:
            if re.search(rf"\b{re.escape(lc)}\b", jd_text, flags=re.I):
                required_langs.append(lc)
        # basic keywords from JD: top nouns/words (naive)
        kws = re.findall(r"\b[A-Za-z\+\-\.]{2,20}\b", jd_text)
        # pick frequent words excluding stopwords (very naive)
        stop = {"the","and","or","with","to","for","a","an","of","in","on","by","is","are","as","that"}
        freq = {}
        for w in kws:
            wlow = w.lower()
            if wlow in stop or wlow.isdigit(): continue
            freq[wlow] = freq.get(wlow, 0) + 1
        keywords = sorted(freq.keys(), key=lambda k: -freq[k])[:20]

    # Prepare repo filtering and scoring
    repos = github_summary.get("repos", []) or []
    candidate_scores = []

    # Pre-normalize keywords for matching
    combined_keywords = list(set(keywords + preferred + required_langs + required_deps))

    for r in repos:
        name = r.get("name", "")
        topics = _norm_list(r.get("topics", []) or [])
        description = (r.get("description") or "")[:10000]
        readme = (r.get("readme") or "")[:10000]
        dependencies = _norm_list(r.get("dependencies", []) or [])
        languages = _norm_list(r.get("languages", []) or [])

        # Filtering rules:
        # - If required_languages exist, repo must include at least one of them (case-insensitive)
        # - If required_dependencies exist, repo must include at least one dependency (case-insensitive)
        lang_match = any(lang in languages for lang in required_langs) if required_langs else True
        dep_match = any(dep in dependencies for dep in required_deps) if required_deps else True
        if not (lang_match and dep_match):
            # filter out
            continue

        # Scoring: language weight, dependency weight, keyword hits in topics/desc/readme
        score = 0.0
        w_lang = 2.0
        w_dep = 1.5
        w_kw = 1.0

        if required_langs:
            matched_langs = sum(1 for l in languages if l in required_langs)
            score += w_lang * matched_langs

        if required_deps:
            matched_deps = sum(1 for d in dependencies if d in required_deps)
            score += w_dep * matched_deps

        # keyword hits
        hits = 0
        for k in combined_keywords:
            hits += _count_keyword_hits(" ".join(topics + [description, readme]), [k])
        score += w_kw * hits

        # small boost for having more languages/deps (indicates richness)
        score += 0.1 * (len(languages) + len(dependencies))

        candidate_scores.append({
            "repo": r,
            "score": score,
            "matched_langs": [l for l in languages if l in required_langs],
            "matched_deps": [d for d in dependencies if d in required_deps],
            "keyword_hits": hits
        })

    # if nothing left after filtering, relax filter: allow repos that have any keyword hit
    if not candidate_scores:
        for r in repos:
            name = r.get("name", "")
            topics = _norm_list(r.get("topics", []) or [])
            description = (r.get("description") or "")[:10000]
            readme = (r.get("readme") or "")[:10000]
            dependencies = _norm_list(r.get("dependencies", []) or [])
            languages = _norm_list(r.get("languages", []) or [])

            hits = 0
            for k in combined_keywords:
                hits += _count_keyword_hits(" ".join(topics + [description, readme]), [k])
            if hits > 0:
                candidate_scores.append({
                    "repo": r,
                    "score": 0.5 + 1.0 * hits,
                    "matched_langs": [],
                    "matched_deps": [],
                    "keyword_hits": hits
                })

    # sort by score desc and pick top_k
    candidate_scores.sort(key=lambda x: x["score"], reverse=True)
    top = candidate_scores[:top_k]

    # For each top repo, ask Gemini to produce resume-ready summary + highlights.
    projects_out = []
    aggregated_skills = set(required_langs + required_deps + preferred + keywords)

    for item in top:
        r = item["repo"]
        name = r.get("name")
        topics = r.get("topics", []) or []
        description = r.get("description", "") or ""
        readme_excerpt = (r.get("readme") or "")[:3000]
        dependencies = r.get("dependencies", []) or []
        languages = r.get("languages", []) or []

        # get tech list for listing skills
        repo_techs = list(dict.fromkeys([*languages, *dependencies, *topics]))
        for t in repo_techs:
            if isinstance(t, str) and t.strip():
                aggregated_skills.add(t.strip())

        # Build prompt for project highlights.
        # NOTE: user requested bolding with 3 '*' before/after important words. We instruct LLM to do so.
        prompt = f"""
        You are a resume writer. Given the job description and repository information, produce a JSON object for this project.

        Job Description (short): {jd_text[:2000]}

        Repository Name: {name}
        Topics: {', '.join(topics)}
        Languages: {', '.join(languages)}
        Dependencies: {', '.join(dependencies)}
        Short Description: {description}
        README excerpt (truncated): {readme_excerpt}

        Task:
        1) Provide a one-line 'summary' describing the project in resume-friendly language (keep <= 20 words).
        2) Provide 2-4 bullet 'highlights' (list) — each highlight must be 8-20 words, start with an action verb, focus on impact/tech used.
        3) In each bullet, emphasize important keywords/technologies by wrapping them like ***this*** (three asterisks before & after).
        4) Do NOT invent metrics — if none available, avoid numeric claims.
        5) Return ONLY valid JSON with fields:
        {{
            "name": "{name}",
            "start_date": "",
            "end_date": "",
            "summary": "...",
            "highlights": ["...","..."]
        }}
        """

        raw_out = gemini.generate(prompt)
        parsed = _safe_json_loads(raw_out, fallback=None)
        if not parsed:
            # fallback lightweight generation
            summary = description[:100] or f"{name} project"
            highlights = []
            if repo_techs:
                highlights.append(f"Built with ***{repo_techs[0]}*** and related libraries.")
            parsed = {"name": name, "start_date": "", "end_date": "", "summary": summary, "highlights": highlights}

        projects_out.append(parsed)

    # Build skills output: group into labels (simple heuristics)
    skills_out = []
    # backend/frontend/languages/databases heuristics
    lang_set = {s for s in aggregated_skills if re.search(r"^[a-zA-Z\+\-0-9]+$", s)}
    db_keywords = {"postgres", "mysql", "mongodb", "redis", "sqlite", "postgresql"}
    dbs = sorted([s for s in aggregated_skills if s.lower() in db_keywords])
    langs = sorted([s for s in aggregated_skills if s.lower() not in db_keywords])
    if langs:
        skills_out.append({"label": "Technologies", "details": ", ".join(langs)})
    if dbs:
        skills_out.append({"label": "Databases", "details": ", ".join(dbs)})
    if preferred:
        skills_out.append({"label": "Preferred / Additional", "details": ", ".join(preferred)})

    # Final output structure
    result = {
        "projects": projects_out,
        "skills": skills_out
    }
    return result
