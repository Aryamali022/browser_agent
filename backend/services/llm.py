"""LLM provider abstraction.

Everything above this layer talks to `BaseLLM`; concrete providers
(NVIDIA NIM, OpenAI, Gemini, local OpenAI-compatible servers) are
interchangeable via the LLM_PROVIDER setting.
"""

import re
from abc import ABC, abstractmethod

from core.config import settings
from schemas.schemas import AgentPlanResponse


# The only escape sequences JSON permits after a backslash.
_VALID_JSON_ESCAPES = set('"\\/bfnrtu')


def _repair_json_escapes(text: str) -> str:
    """Double any backslash that isn't a valid JSON escape.

    LLMs frequently echo a CSS selector verbatim into their JSON, e.g.
    button[aria-label="Pause\\ Song"]. The backslash-space there is NOT a
    legal JSON escape, so the parser rejects the whole object. We turn each
    illegal `\\x` into `\\\\x` (a literal backslash) so it parses. Valid
    escapes (\\", \\\\, \\n, \\uXXXX ...) are consumed as pairs and left
    untouched, so this is a no-op on already-valid JSON.
    """
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if nxt in _VALID_JSON_ESCAPES:
                out.append(ch)
                out.append(nxt)
                i += 2
                continue
            out.append("\\\\")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def extract_json(text: str) -> str:
    """Pull the first balanced JSON object out of arbitrary LLM output.

    Handles markdown fences, <think> blocks and prose around the JSON, and
    repairs illegal backslash escapes so selectors with `\\ ` don't break it.
    """
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model output")
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return _repair_json_escapes(text[start:i + 1])
    return _repair_json_escapes(text[start:])


class BaseLLM(ABC):
    @abstractmethod
    def complete(self, system_prompt: str, prompt: str) -> str:
        """Return raw model text for a single-turn request."""

    def generate_plan(self, prompt: str, system_prompt: str) -> AgentPlanResponse:
        raw = self.complete(system_prompt, prompt)
        return AgentPlanResponse.model_validate_json(extract_json(raw))


class OpenAICompatibleLLM(BaseLLM):
    """Any OpenAI-compatible chat endpoint (OpenAI, NVIDIA NIM, vLLM, Ollama...)."""

    def __init__(self, base_url: str, api_key: str, model: str):
        from openai import OpenAI
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    def complete(self, system_prompt: str, prompt: str) -> str:
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            top_p=0.95,
            max_tokens=4096,
        )
        return completion.choices[0].message.content or ""


class NvidiaLLM(OpenAICompatibleLLM):
    def __init__(self, model: str = ""):
        super().__init__(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=settings.NVIDIA_API_KEY,
            model=model or settings.LLM_MODEL,
        )


class OpenAILLM(OpenAICompatibleLLM):
    def __init__(self, model: str = ""):
        super().__init__(
            base_url="https://api.openai.com/v1",
            api_key=settings.OPENAI_API_KEY,
            model=model or settings.LLM_MODEL,
        )


class GeminiLLM(BaseLLM):
    def __init__(self, model: str = ""):
        from google import genai
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.model = model or settings.LLM_MODEL

    def complete(self, system_prompt: str, prompt: str) -> str:
        from google.genai import types
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.1,
            ),
        )
        return response.text or ""


def create_llm() -> BaseLLM:
    provider = settings.LLM_PROVIDER.lower()
    if provider == "nvidia":
        return NvidiaLLM()
    if provider == "openai":
        return OpenAILLM()
    if provider == "gemini":
        return GeminiLLM()
    raise ValueError(f"Unknown LLM_PROVIDER '{settings.LLM_PROVIDER}'")
