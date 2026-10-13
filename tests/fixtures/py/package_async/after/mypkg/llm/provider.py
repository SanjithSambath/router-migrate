"""Provider module."""
from __future__ import annotations

from openai import AsyncOpenAI
try:  # router-migrate: route LLM traffic through Ramp's Router when RAMP_ROUTER_API_KEY is set
    from ..router_runtime import wrap_openai as _router_wrap_openai
except ImportError:  # router-migrate: module executed as a script
    from router_runtime import wrap_openai as _router_wrap_openai


class Provider:
    def __init__(self, api_key: str | None = None):
        self.client = _router_wrap_openai(AsyncOpenAI(api_key=api_key))

    async def stream(self, messages):
        return await self.client.chat.completions.create(model=self.model, messages=messages, stream=True)
