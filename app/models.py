from pydantic import BaseModel, HttpUrl, Field
from typing import List, Optional

class IngestRequest(BaseModel):
    tenant: str = Field(default="slsp")
    urls: List[HttpUrl]

class ChatRequest(BaseModel):
    tenant: str = Field(default="slsp")
    message: str
    page_url: Optional[str] = None
    locale: Optional[str] = None

class ChatResponse(BaseModel):
    answer: str
    citations: list[dict]
    usage: dict | None = None