import openai


class Helper:
    def __init__(self, client):
        self.client = client

    def ask(self, q):
        return self.client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": q}])
