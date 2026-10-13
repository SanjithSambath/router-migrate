import litellm
from openai import OpenAI
try:  # router-migrate: route LLM traffic through Ramp's Router when RAMP_ROUTER_API_KEY is set
    from .router_runtime import (
        install_litellm as _router_install_litellm,
        wrap_openai as _router_wrap_openai,
    )
except ImportError:  # router-migrate: module executed as a script
    try:
        from router_runtime import (
            install_litellm as _router_install_litellm,
            wrap_openai as _router_wrap_openai,
        )
    except ImportError:  # router-migrate: runtime not importable from here; this module keeps its original behavior
        _router_install_litellm = lambda: None  # noqa: E731
        _router_wrap_openai = lambda c, *a, **k: c  # noqa: E731
_router_install_litellm()  # router-migrate

client = _router_wrap_openai(OpenAI())
completion = client.chat.completions.create


def run(**kw):
    return completion(**kw)


def via_litellm(messages):
    return litellm.completion(model="gpt-4o-mini", messages=messages)
