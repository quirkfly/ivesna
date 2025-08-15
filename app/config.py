from pydantic import BaseModel
import os

class Settings(BaseModel):
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_embed_model: str = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
    openai_chat_model: str = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")

    db_url: str = os.getenv("IVESNA_DB_URL", "sqlite:///./ivesna.db")
    allowed_domains: list[str] = os.getenv("IVESNA_ALLOWED_DOMAINS", "slsp.sk,www.slsp.sk").split(",")
    tenant_name: str = os.getenv("IVESNA_TENANT_NAME", "Slovenská sporiteľňa")

    max_chunk_tokens: int = int(os.getenv("MAX_CHUNK_TOKENS", 900))
    chunk_overlap_tokens: int = int(os.getenv("CHUNK_OVERLAP_TOKENS", 120))
    top_k: int = int(os.getenv("TOP_K", 6))

settings = Settings()