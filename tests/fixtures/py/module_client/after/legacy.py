import openai
from router_runtime import install_openai_module as _router_install_openai_module  # router-migrate: route LLM traffic through Ramp's Router when RAMP_ROUTER_API_KEY is set
_router_install_openai_module()  # router-migrate


def go():
    return openai.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
