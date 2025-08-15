import re

_token_re = re.compile(r"\w+|[^\w\s]")

def tokenize(txt: str) -> list[str]:
    return _token_re.findall(txt)


def chunk_text(text: str, max_tokens: int = 900, overlap: int = 120) -> list[str]:
    tokens = tokenize(text)
    chunks = []
    i = 0
    while i < len(tokens):
        window = tokens[i : i + max_tokens]
        chunk = " ".join(window)
        chunks.append(chunk)
        if i + max_tokens >= len(tokens):
            break
        i += max_tokens - overlap
    return chunks