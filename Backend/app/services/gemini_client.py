import hashlib
import json
import os
import subprocess
from typing import Any, Dict, List, Optional


CACHE_FILE = os.path.join(".cache", "gemini_cache.json")
os.makedirs(".cache", exist_ok=True)

def _load_cache(path: str) -> Dict[str, Any]:
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _save_cache(path: str, data: Dict[str, Any]):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def _json_safely(s: str, fallback: Any = None):
    try:
        return json.loads(s)
    except Exception:
        # naive fix: extract first {...}
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(s[start:end+1])
            except Exception:
                return fallback
        return fallback

class Gemini:
    """
    Single integration point. Business code never touches API/CLI.
    - If api_key is passed, use Google GenAI API.
    - Else, use `gemini` CLI in PATH.
    Provides:
      - generate(text) -> str
      - generate_json(text) -> dict or list
      - batch_score_repos(jd_text, fingerprints) -> List[dict]
    """
    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-2.0-flash"):
        self.api_key = api_key
        self.model = model
        self._cache = _load_cache(CACHE_FILE)
        self._api_client = None
        if api_key:
            from google.genai import Client  # lazy import
            self._api_client = Client(api_key=api_key)

    # ---------- low-level ----------
    def _run_api(self, prompt: str) -> str:
        resp = self._api_client.models.generate_content(
            model=self.model,
            contents=prompt,
        )
        return (resp.text or "").strip()

    def _run_cli(self, prompt: str) -> str:
        # Expecting: `gemini --model gemini-2.0-flash --text "<prompt>"`
        # If you use a different CLI, adjust here.
        cmd = ["gemini", "--model", self.model, "--text", prompt]
        out = subprocess.check_output(cmd, text=True)
        return out.strip()

    def _call(self, prompt: str) -> str:
        key = f"raw:{hashlib.sha1(prompt.encode()).hexdigest()}"
        if key in self._cache:
            return self._cache[key]
        if self._api_client:
            out = self._run_api(prompt)
        else:
            out = self._run_cli(prompt)
        self._cache[key] = out
        _save_cache(CACHE_FILE, self._cache)
        return out

    # ---------- public ----------
    def generate(self, prompt: str) -> str:
        return self._call(prompt)

    def generate_json(self, prompt: str, fallback: Any = None) -> Any:
        raw = self._call(prompt)
        parsed = _json_safely(raw, fallback=fallback)
        return parsed if parsed is not None else fallback

    # ---------- higher-level batching ----------
    def batch_score_repos(self, jd_text: str, fingerprints: List[Dict], batch_id: str = "", batch_size: int = 5) -> List[Dict]:
        """
        Scores many fingerprints with one LLM call per chunk.
        Caches on (JD hash + each repo fingerprint hash) so repeated calls are cheap.
        """
        results: List[Dict] = []
        jd_key = hashlib.sha1(jd_text.encode()).hexdigest()[:10]

        # split into chunks
        for i in range(0, len(fingerprints), batch_size):
            chunk = fingerprints[i:i+batch_size]

            # check cache per item first
            to_score = []
            cache_hits = {}
            for fp in chunk:
                name = fp.get("name", "repo")
                pushed_at = fp.get("pushed_at", "")
                fp_key = hashlib.sha1(json.dumps(fp, sort_keys=True).encode()).hexdigest()[:12]
                cache_key = f"score:{jd_key}:{fp_key}:{name}"
                if cache_key in self._cache:
                    cache_hits[name] = self._cache[cache_key]
                else:
                    to_score.append((name, fp, cache_key))

            if not to_score:
                results.extend(cache_hits.values())
                continue

            # batch prompt for the items that missed cache
            payload = [fp for _, fp, _ in to_score]
            prompt = f"""
            You are a senior engineer + technical recruiter. Score each repository vs the JD.

            Job Description:
            {jd_text}

            Repositories (JSON list):
            {json.dumps(payload, indent=2)}

            Instructions:
            For each repo, return an array of JSON objects in the same order as input, one per repo, each with:
            {{
            "name": "<repo name>",
            "skills": ["list","key","skills"],
            "relevance_score": 0.0,
            "reasoning": "short, grounded explanation"
            }}
            Only return a valid JSON array.
            """
            scored_list = self.generate_json(prompt, fallback=[])
            if not isinstance(scored_list, list):
                # hard fallback: naive zeros
                for name, _, cache_key in to_score:
                    item = {"name": name, "skills": [], "relevance_score": 0.0, "reasoning": "fallback"}
                    self._cache[cache_key] = item
                    results.append(item)
                _save_cache(CACHE_FILE, self._cache)
                results.extend(cache_hits.values())
                continue

            # write cache for new items, then append
            for (name, _, cache_key), scored in zip(to_score, scored_list):
                self._cache[cache_key] = scored
                results.append(scored)

            # also add cache hits
            results.extend(cache_hits.values())
            _save_cache(CACHE_FILE, self._cache)

        # keep order roughly by score desc (optional)
        results.sort(key=lambda x: x.get("relevance_score", 0.0), reverse=True)
        return results
    def __init__(self,api_key=None,model="gemini-2.5-pro"):
        self.api_key = api_key
        self.client = Client(api_key=self.api_key) if self.api_key else None
        self.model = model

    def generate(self,prompt:str)->str:
        if self.client:
            return self._call_api(prompt)
        else:
            return self._call_cli(prompt)
    
    def _call_api(self,prompt:str)->str:
        if(self.client):
            try:
                response = self.client.models.generate_content(
                    model = self.model,
                    contents = prompt
                )
                return response.text.strip()
            except Exception as e:
                print(e);
                raise e

    def _call_cli(self,prompt:str)->str:
        try:
            result = subprocess.run(
                ["gemini","-m",self.model,"-p",prompt],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()

        except Exception as e:
            print(e.stderr)
            raise e            