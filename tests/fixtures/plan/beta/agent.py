from anthropic import Anthropic


class Agent:
    def __init__(self, client=None):
        self.client = client or Anthropic()

    def run_headers(self, params):
        default_headers = {"anthropic-beta": "code-execution-2025-05-22"}
        merged = {**default_headers, **params.pop("extra_headers", {})}
        return self.client.messages.create(model="claude-sonnet-4-5", max_tokens=10, messages=[], extra_headers=merged)

    def run_betas(self):
        return self.client.messages.create(model="claude-sonnet-4-5", max_tokens=10, messages=[], betas=["prompt-caching"])

    def run_beta_namespace(self):
        return self.client.beta.messages.create(model="claude-sonnet-4-5", max_tokens=10, messages=[])

    def run_split(self, betas):
        kw = {"model": "claude-sonnet-4-5", "max_tokens": 10, "messages": []}
        if betas:
            return self.client.beta.messages.create(betas=betas, **kw)
        else:
            return self.client.messages.create(**kw)

    def run_plain(self):
        return self.client.messages.create(model="claude-sonnet-4-5", max_tokens=10, messages=[], extra_headers={"x-trace": "1"})
