"""Provider module."""
from __future__ import annotations

from openai import AsyncOpenAI


class Provider:
    def __init__(self, api_key: str | None = None):
        self.client = AsyncOpenAI(api_key=api_key)

    async def stream(self, messages):
        return await self.client.chat.completions.create(model=self.model, messages=messages, stream=True)
