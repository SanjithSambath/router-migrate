import requests

URL = "https://api.openai.com/v1/chat/completions"


def call(payload):
    return requests.post(URL, json=payload)
