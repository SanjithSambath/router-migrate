import anthropic

client = anthropic.Anthropic()

msg = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}],
)
old = client.messages.create(model="claude-3-5-sonnet-20241022", max_tokens=10, messages=[])
beta = client.beta.messages.create(model="claude-sonnet-4-5", max_tokens=10, messages=[], betas=["x"])
