from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_anthropic import ChatAnthropic
from router_runtime import (
    async_http_client as _router_async_http_client,
    chat_anthropic_kwargs as _router_chat_anthropic_kwargs,
    http_client as _router_http_client,
)  # router-migrate: route LLM traffic through Ramp's Router when RAMP_ROUTER_API_KEY is set

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, http_client=_router_http_client(), http_async_client=_router_async_http_client())
claude = ChatAnthropic(model="claude-haiku-4-5", **_router_chat_anthropic_kwargs())
already = ChatOpenAI(model="gpt-4o", http_client=None)
emb = OpenAIEmbeddings()
