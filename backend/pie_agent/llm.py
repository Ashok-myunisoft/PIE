from __future__ import annotations
import json, os
from dotenv import load_dotenv

load_dotenv()
from typing import Any

LLM_TIMEOUT_SECONDS = float(os.getenv("PIE_LLM_TIMEOUT", "90"))


class LLMProvider:
    name = "base"

    def generate(self, prompt: str) -> str:
        raise NotImplementedError

    def chat(self, system: str, messages: list[dict[str, str]]) -> str:
        """System instructions plus user/assistant turns; returns the reply text."""
        raise NotImplementedError


class OfflineProvider(LLMProvider):
    name = "offline"

    def generate(self, prompt: str) -> str:
        return ""

    def chat(self, system: str, messages: list[dict[str, str]]) -> str:
        return ""


class _ResponsesProvider(LLMProvider):
    client: Any
    model: str

    def generate(self, prompt):
        resp=self.client.responses.create(model=self.model,input=prompt)
        return resp.output_text

    def chat(self, system, messages):
        resp=self.client.responses.create(model=self.model,instructions=system,
                                          input=[{"role":m["role"],"content":m["content"]} for m in messages])
        return resp.output_text


class OpenAIProvider(_ResponsesProvider):
    name = "openai"

    def __init__(self):
        from openai import OpenAI
        self.client=OpenAI(api_key=os.environ["OPENAI_API_KEY"],timeout=LLM_TIMEOUT_SECONDS)
        self.model=os.environ.get("OPENAI_MODEL","gpt-4.1-mini")


class AzureOpenAIProvider(_ResponsesProvider):
    name = "azure_openai"

    def __init__(self):
        from openai import AzureOpenAI
        self.client=AzureOpenAI(
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION","2024-10-21"),
            timeout=LLM_TIMEOUT_SECONDS,
        )
        self.model=os.environ["AZURE_OPENAI_DEPLOYMENT"]


def get_provider(name: str|None=None) -> LLMProvider:
    name=(name or os.getenv("PIE_LLM_PROVIDER","offline")).lower()
    if name=="openai": return OpenAIProvider()
    if name in {"azure","azure_openai"}: return AzureOpenAIProvider()
    return OfflineProvider()

def extract_json(text: str) -> dict[str,Any]:
    text=text.strip()
    if text.startswith("```"):
        text=text.split("\n",1)[1].rsplit("```",1)[0]
    return json.loads(text)
