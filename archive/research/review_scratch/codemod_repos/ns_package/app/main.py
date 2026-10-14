from openai import OpenAI
from router_runtime import wrap_openai as _router_wrap_openai  # router-migrate: route LLM traffic through Ramp's Router when RAMP_ROUTER_API_KEY is set

client = _router_wrap_openai(OpenAI())

def ask(q):
    return client.chat.completions.create(model='gpt-4o-mini', messages=[{'role':'user','content':q}])
