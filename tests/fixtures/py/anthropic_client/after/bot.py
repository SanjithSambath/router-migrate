import anthropic
from router_runtime import wrap_anthropic as _router_wrap_anthropic  # router-migrate: route LLM traffic through Ramp's Router when RAMP_ROUTER_API_KEY is set

client = _router_wrap_anthropic(anthropic.Anthropic())

msg = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}],
)
old = client.messages.create(model="claude-3-5-sonnet-20241022", max_tokens=10, messages=[])
beta = client.beta.messages.create(model="claude-sonnet-4-5", max_tokens=10, messages=[], betas=["x"])
