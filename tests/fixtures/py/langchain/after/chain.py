from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_anthropic import ChatAnthropic
try:  # router-migrate: route LLM traffic through Ramp's Router when RAMP_ROUTER_API_KEY is set
    from .router_runtime import wrap_langchain as _router_wrap_langchain
except ImportError:  # router-migrate: module executed as a script
    try:
        from router_runtime import wrap_langchain as _router_wrap_langchain
    except ImportError:  # router-migrate: runtime not importable from here; this module keeps its original behavior
        _router_wrap_langchain = lambda c, *a, **k: c  # noqa: E731

llm = _router_wrap_langchain(ChatOpenAI(model="gpt-4o-mini", temperature=0))
claude = _router_wrap_langchain(ChatAnthropic(model="claude-haiku-4-5"))
already = _router_wrap_langchain(ChatOpenAI(model="gpt-4o", http_client=None))
emb = OpenAIEmbeddings()
