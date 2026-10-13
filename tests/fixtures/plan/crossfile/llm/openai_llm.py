from openai import OpenAI


class OpenAIService:
    def __init__(self, config):
        self.client = OpenAI(base_url=config.api_base)

    def chat(self, messages):
        return self.client.chat.completions.create(model=self.config.model, messages=messages)
