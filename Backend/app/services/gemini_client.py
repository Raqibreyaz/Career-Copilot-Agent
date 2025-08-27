import os
import subprocess
from typing import Any, Dict, List, Optional

class Gemini:
    """
    Single integration point. Business code never touches API/CLI.
    - If api_key is passed, use Google GenAI API.
    - Else, use `gemini` CLI in PATH.
    """
    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-2.0-flash"):
        self.api_key = api_key
        self.model = model
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

    def generate(self, prompt: str) -> str:
        if self._api_client:
            out = self._run_api(prompt)
        else:
            out = self._run_cli(prompt)
 
        return out