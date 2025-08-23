import json
import os
from typing import Any, Dict, List, Optional


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