import logging
import json
import redis
import functools
from dataclasses import dataclass
from typing import Any, Dict, Optional

DEFAULT_CACHE_TTL = 24 * 3600  # 1 day

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

@dataclass
class Cache:
    client: Optional[redis.Redis]
    ttl: int = DEFAULT_CACHE_TTL
    fallback: Dict[str, Any] = None

    def __post_init__(self):
        if self.fallback is None:
            self.fallback = {}

    def get(self, key: str) -> Optional[Any]:
        try:
            if self.client:
                raw = self.client.get(key)
                return json.loads(raw) if raw else None
            return self.fallback.get(key)
        except Exception as e:
            logger.debug(f"Cache get error: {e}")
            return None

    def set(self, key: str, value: Any, ex: Optional[int] = None) -> None:
        try:
            payload = json.dumps(value)
            if self.client:
                self.client.set(key, payload, ex=(ex or self.ttl))
            else:
                self.fallback[key] = value
        except Exception as e:
            logger.debug(f"Cache set error: {e}")

TTL_MAP= {
    "repos": 24 * 3600,
    "topics": 24 * 3600,
    "dependencies": 12 * 3600,
    "languages": 7 * 24 * 3600,
    "readme": 24 * 3600,
}

# ---------- Helpers ----------
def cache_key(*parts: str) -> str:
    """
    Create a short Redis cache key from parts.
    Example: make_cache_key("github", "repo", "file.py")
    """
    joined = ":".join(map(str,parts))
    digest = hashlib.md5(joined.encode("utf-8")).hexdigest()  # or sha1
    return f"gha:{digest}"

def cached(category: str):
    def decorator(func):
        def wrapper(self, repo, *args, **kwargs):
            key = cache_key(category, repo.full_name, *args)

            cached = self.cache.get(key)
            if cached is not None:
                return cached

            try:
                result = func(self, repo, *args, **kwargs)
                self.cache.set(key, result,ttl=TTL_MAP.get(category,self.cache.ttl))
                return result

            except Exception as e:
                print(f"get {repo.full_name}: {e}") 
                self.cache.set(key, None)
                return None

        return wrapper
    return decorator
