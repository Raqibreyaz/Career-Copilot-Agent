import asyncio
import json
import os
from app.services.github_profile import GitHubProfileService
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
USERNAME = os.getenv("USERNAME")

jd_text = """
"""

async def main():
    service = GitHubProfileService(token=GITHUB_TOKEN, llm_api_key=None)

    profile = await service.build_profile(USERNAME, jd_text)
    # print(profile)
    with open('output/profile.json','w') as f:
        json.dump(profile,f,indent=4)

asyncio.run(main())
