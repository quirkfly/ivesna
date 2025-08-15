from fastapi import Request, FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import select
import logging
import os
import time

from .config import settings
from .db import init_db, SessionLocal, Document
from .models import IngestRequest, ChatRequest, ChatResponse
from .ingest import ingest_urls
from .retrieval import retrieve
from .openai_client import chat_answer
from .schemas import SYSTEM_PROMPT, USER_TEMPLATE

app = FastAPI(title="ivesna – SLSP POC", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger("ivesna.chat")
if not logger.handlers:
    logging.basicConfig(level=logging.DEBUG)  # or INFO for less noise
# Opt-in to logging full prompts by setting IVESNA_LOG_PROMPT=1
LOG_PROMPT = os.getenv("IVESNA_LOG_PROMPT", "0") == "1"

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/health")
def health():
    return {"ok": True, "tenant": settings.tenant_name}


@app.post("/v1/ingest")
def ingest(req: IngestRequest, db: Session = Depends(get_db)):
    if not req.urls:
        raise HTTPException(400, "urls required")
    result = ingest_urls(db, req.tenant, [str(u) for u in req.urls])
    return result


@app.post("/v1/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request, db: Session = Depends(get_db)):
    t0 = time.perf_counter()
    logger.info("CHAT start ip=%s tenant=%s", getattr(request.client, "host", "?"), req.tenant)
    logger.debug("Incoming message=%r", req.message)

    # --- Retrieve top chunks ---
    t_retr_start = time.perf_counter()
    top = retrieve(db, req.tenant, req.message, k=settings.top_k)
    t_retr = time.perf_counter() - t_retr_start
    logger.debug("Retrieval took %.3fs; hits=%d", t_retr, len(top))

    if not top:
        answer = (
            "Ľutujem, momentálne nemám k dispozícii relevantný obsah. Skúste to prosím inak alebo kontaktujte podporu."
        )
        logger.warning("No retrieval results; returning fallback.")
        return ChatResponse(answer=answer, citations=[], usage=None)

    # Log top hits with scores and url
    doc_ids = [doc_id for _, _, doc_id, _ in top]
    docs = {d.id: d for d in db.execute(select(Document).where(Document.id.in_(doc_ids))).scalars()}
    for rank, (_, _text, doc_id, score) in enumerate(top, 1):
        d = docs.get(doc_id)
        logger.debug("HIT #%d score=%.4f url=%s title=%r", rank, score, getattr(d, "url", "?"), getattr(d, "title", "?"))

    # --- Build context & citations ---
    context_lines = []
    citations = []
    seen = set()
    for i, (_, text, doc_id, _) in enumerate(top, start=1):
        url = docs[doc_id].url
        title = docs[doc_id].title
        snippet = (text[:750] + "…") if len(text) > 750 else text
        context_lines.append(f"[{i}] {snippet}\n({url})\n")
        if url not in seen:
            citations.append({"url": url, "title": title})
            seen.add(url)

    user_prompt = USER_TEMPLATE.format(
        question=req.message,
        context="\n".join(context_lines),
        citations=", ".join(f"[{i+1}]" for i in range(len(citations)))
    )
    if LOG_PROMPT:
        logger.debug("Prompt to model:\n%s", user_prompt)
    else:
        logger.debug("Prompt built (length chars=%d). Set IVESNA_LOG_PROMPT=1 to log full text.", len(user_prompt))

    # --- Call LLM ---
    t_llm_start = time.perf_counter()
    answer, usage = chat_answer(SYSTEM_PROMPT, user_prompt)
    t_llm = time.perf_counter() - t_llm_start
    logger.debug("LLM call took %.3fs; usage=%s", t_llm, usage)

    # --- Done ---
    t_total = time.perf_counter() - t0
    logger.info("CHAT done in %.3fs", t_total)

    return ChatResponse(answer=answer, citations=citations, usage=usage)
