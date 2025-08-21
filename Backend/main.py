# api1 = AIzaSyBarj3fLR6vbso05I230zD8dci8PlNX5pw
# api2 = 


from app.services.github_analyzer import GitHubProfileService
import asyncio
import json

GEMINI_API_KEY = "AIzaSyBarj3fLR6vbso05I230zD8dci8PlNX5pw"
GITHUB_TOKEN = "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"

async def main():
    service = GitHubProfileService(token=GITHUB_TOKEN, llm_api_key=GEMINI_API_KEY)
    jd_text = """Job Role: As a Frontend Developer Intern, you will work closely with experienced developers to build scalable and engaging user interfaces. You’ll be involved in creating seamless user experiences, optimizing performance, and integrating with backend services while gaining real-world experience with modern web technologies.

    You should apply if you have: 
    - Experience building projects using React.js or Next.js 
    - Experience developing projects using Node.js 
    
    You should not apply if you: 
    - Lack experience working with React.js, Next.js, or Node.js Have not built or contributed to any projects using these technologies 
    - Are unwilling to learn and work in a fast-paced development environment Skills

    Required: 
    - Version Control (Git): Experience with Git for code versioning and collaboration.
    - Good understanding of React.js and Next.js and building dynamic and responsive user interfaces.
    - Good understanding with HTML CSS JS. 
    - RESTful APIs & Integration: Understanding of API consumption and integration in frontend projects.
    - Ability to debug and resolve technical issues effectively. 
    
    What will you do?
    - Develop User Interfaces: Build dynamic, responsive, and user-friendly web interfaces using React.js and Next.js. 
    - Collaborate with Teams: Work closely with designers, backend developers, to implement features and ensure seamless user experiences.
    - API Integration: Integrate frontend applications with backend APIs developed in Node.js to enable data-driven functionality."""

    profile = await service.build_profile("Raqibreyaz", jd_text)
    # print(profile)
    with open('relevance.json','w') as f:
        json.dump(profile,f,indent=4)

asyncio.run(main())
