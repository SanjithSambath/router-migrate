import litellm
from openai import OpenAI
from router_runtime import (
    install_litellm as _router_install_litellm,
    wrap_openai as _router_wrap_openai,
)  # router-migrate: route LLM traffic through Ramp's Router when RAMP_ROUTER_API_KEY is set
_router_install_litellm()  # router-migrate

client = _router_wrap_openai(OpenAI())
completion = client.chat.completions.create


def run(**kw):
    return completion(**kw)


def via_litellm(messages):
    return litellm.completion(model="gpt-4o-mini", messages=messages)
