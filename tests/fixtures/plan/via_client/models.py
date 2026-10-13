import openai


class Embeddings:
    def embed(self, items, key=None):
        client = openai.OpenAI()
        return client.embeddings.create(model="text-embedding-3-small", input=items)


class Chat:
    def get_client(self, key, async_=False):
        if async_:
            return openai.AsyncOpenAI()
        return openai.OpenAI()

    def execute(self, prompt, key):
        client = self.get_client(key)
        return client.chat.completions.create(model="gpt-4o-mini", messages=[])

    async def execute_async(self, prompt, key):
        client = self.get_client(key, async_=True)
        return await client.chat.completions.create(model="gpt-4o-mini", messages=[])

    def nearest(self):
        client = openai.OpenAI()
        first = client.chat.completions.create(model="gpt-4o-mini", messages=[])
        client = openai.OpenAI(timeout=5)
        return client.chat.completions.create(model="gpt-4o-mini", messages=[])
