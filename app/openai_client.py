from openai import OpenAI
from .config import settings

_client = None

def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


def embed_texts(texts: list[str]) -> list[list[float]]:
    client = get_client()
    resp = client.embeddings.create(model=settings.openai_embed_model, input=texts)
    return [d.embedding for d in resp.data]


def chat_answer(system_prompt: str, user_prompt: str) -> tuple[str, dict]:
    client = get_client()
    resp = client.chat.completions.create(
        model=settings.openai_chat_model,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    answer = resp.choices[0].message.content or ""
    usage = {"prompt_tokens": resp.usage.prompt_tokens, "completion_tokens": resp.usage.completion_tokens}
    return answer, usage