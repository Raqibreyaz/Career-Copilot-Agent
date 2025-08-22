import subprocess
import json
import os
from google.genai import Client

class Gemini:
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