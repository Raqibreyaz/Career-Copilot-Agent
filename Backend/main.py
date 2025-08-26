import asyncio
import json
import os
from dotenv import load_dotenv
from app.services.github_fetcher import GitHubFetcher

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
USERNAME = os.getenv("USERNAME")
GITHUB_CACHE_FILE = os.path.join(".cache","github_cache.json")

os.makedirs(".cache", exist_ok=True)    

jd_text = """
"""

def main():
    fetcher = GitHubFetcher(github_token=GITHUB_TOKEN)
    repos = fetcher.extract_many(include_user_repos=True,out_json=GITHUB_CACHE_FILE)
    print(json.dumps(repos, indent=4))

main()