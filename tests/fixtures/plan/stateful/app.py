from openai import OpenAI

TOOLS = [{"type": "file_search", "vector_store_ids": ["vs_123"]}]

chain = OpenAI()
plain = OpenAI()
files = OpenAI()


def turn(prev_id, text):
    return chain.responses.create(model="gpt-4o-mini", input=text, previous_response_id=prev_id)


def first_turn(text):
    return chain.responses.create(model="gpt-4o-mini", input=text)


def chat_on_chain_client(text):
    return chain.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": text}])


def single(text):
    return plain.responses.create(model="gpt-4o-mini", input=text, store=False, temperature=0.2, max_output_tokens=50,
                                  text={"format": {"type": "json_object"}})


def search(text):
    return files.responses.create(model="gpt-4o-mini", input=text, tools=TOOLS)
