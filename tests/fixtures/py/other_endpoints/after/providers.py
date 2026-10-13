from openai import OpenAI, AzureOpenAI
from router_runtime import wrap_openai as _router_wrap_openai  # router-migrate: route LLM traffic through Ramp's Router when RAMP_ROUTER_API_KEY is set

groq = OpenAI(base_url="https://api.groq.com/openai/v1", api_key="x")
azure = AzureOpenAI(azure_endpoint="https://x.openai.azure.com", api_version="2024-02-01")
cfg = _router_wrap_openai(OpenAI(base_url=settings.base_url))


def a():
    return groq.chat.completions.create(model="llama3-70b", messages=[])


def b():
    return azure.chat.completions.create(model="gpt-4o", messages=[])


def c():
    return cfg.chat.completions.create(model="gpt-4o", messages=[])
