import json
from sqlalchemy.orm import Session
from tqdm import tqdm

from .db import Document, Chunk
from .openai_client import embed_texts
from .utils import chunk_text
from .config import settings

def _short(s: str, n: int = 70) -> str:
    return s if len(s) <= n else s[: n-1] + "…"

def process_pages(db: Session, tenant: str, pages: list[dict]) -> int:
    stored = 0
    proc = tqdm(pages, desc="Processing", unit="page")
    for page in proc:
        title = page.get("title") or page.get("url") or ""
        text = (page.get("text") or "").strip()
        proc.set_description_str(f"Processing | {_short(title)} ({len(text)} chars)")
        if not text:
            continue

        chunks = chunk_text(text, max_tokens=settings.max_chunk_tokens, overlap=settings.chunk_overlap_tokens)
        if not chunks:
            continue
        embeddings = embed_texts(chunks)

        doc = Document(tenant=tenant, url=page.get("url"), title=title, lang="sk")
        db.add(doc); db.flush()
        for i, (ch, emb) in enumerate(zip(chunks, embeddings)):
            db.add(Chunk(document_id=doc.id, tenant=tenant, ordinal=i, text=ch, embedding=json.dumps(emb)))
        db.commit()
        stored += 1
    return stored
