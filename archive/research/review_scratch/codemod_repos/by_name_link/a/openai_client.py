from openai import OpenAI
try:  # router-migrate: route LLM traffic through Ramp's Router when RAMP_ROUTER_API_KEY is set
    from .router_runtime import wrap_openai as _router_wrap_openai
except ImportError:  # router-migrate: module executed as a script
    from router_runtime import wrap_openai as _router_wrap_openai
client = _router_wrap_openai(OpenAI())
