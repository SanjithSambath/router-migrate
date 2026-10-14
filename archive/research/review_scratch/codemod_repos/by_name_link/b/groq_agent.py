import openai
class Agent:
    def __init__(self, client):
        self.client = client  # e.g. openai.OpenAI(base_url='https://api.groq.com/openai/v1') built by the caller
    def run(self):
        return self.client.chat.completions.create(model='llama-3.3-70b', messages=[])
