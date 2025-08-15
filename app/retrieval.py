# app/retrieval.py
import json
import math
import unicodedata
from collections import Counter, defaultdict
from typing import List, Tuple, Dict

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Chunk, Document
from .openai_client import embed_texts
from .config import settings

# ----------------- tokenization & helpers -----------------

_SK_STOP = {
    "a","aj","alebo","ani","na","v","vo","do","z","za","od","o","u","s","so",
    "je","sú","som","si","sa","by","byť","čo","kto","ktorý","ktorá","ktoré",
    "ak","aké","ako","že","pre","pri","nad","pod","po","už","len","či","tiež",
    "slovenská","slovenska","sporiteľňa","sporitelna","slsp","sk"
}

BUSINESS_HINTS = {"biznis","firma","firemny","firemný","podnik","podnikanie","živnost","zivnost","živnostník","zivnostnik"}

def strip_acc(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", s.lower())
                   if unicodedata.category(ch) != "Mn")

def tokens(s: str) -> List[str]:
    s = strip_acc(s)
    out, cur = [], []
    for ch in s:
        if ch.isalnum():
            cur.append(ch)
        else:
            if cur:
                out.append("".join(cur)); cur=[]
    if cur: out.append("".join(cur))
    return [t for t in out if len(t) >= 3 and t not in _SK_STOP]

def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0: return 0.0
    return float(np.dot(a, b) / denom)

# ----------------- BM25 (lite) over chunks -----------------

def bm25_scores(q_toks: List[str], chunk_texts: List[List[str]], k1=1.2, b=0.75) -> List[float]:
    if not chunk_texts or not q_toks:
        return [0.0] * len(chunk_texts)

    N = len(chunk_texts)
    df: Counter = Counter()
    for toks in chunk_texts:
        for t in set(toks):
            df[t] += 1

    idf: Dict[str, float] = {}
    for t in set(q_toks):
        n_t = df.get(t, 0)
        # add-0.5 smoothing
        idf[t] = math.log((N - n_t + 0.5) / (n_t + 0.5) + 1.0)

    avgdl = sum(len(toks) for toks in chunk_texts) / max(1, N)
    scores: List[float] = []
    for toks in chunk_texts:
        tf = Counter(toks)
        dl = len(toks)
        score = 0.0
        for qt in q_toks:
            if qt not in tf: 
                continue
            num = tf[qt] * (k1 + 1)
            den = tf[qt] + k1 * (1 - b + b * dl / max(1.0, avgdl))
            score += idf.get(qt, 0.0) * (num / den)
        scores.append(score)
    return scores

# ----------------- Heuristic URL/title priors -----------------

def url_title_prior(q_toks: List[str], url: str, title: str, is_business_query: bool) -> float:
    u = strip_acc(url)
    t = strip_acc(title or "")
    prior = 0.0

    # direct term hits
    for qt in q_toks:
        if qt in u: prior += 0.20
        if qt in t: prior += 0.15

    # stems for "účty"
    if "uct" in u or "uct" in t:
        prior += 0.35

    # strong positive priors for consumer accounts hubs
    if "/ludia/vsetky-ucty" in u or "/ludia/ucty" in u:
        prior += 0.40
    if "/ludia/" in u:
        prior += 0.15

    # penalize PDFs/assets/legal/archives/landing
    if "/content/dam/" in u or u.endswith(".pdf"):
        prior -= 0.40
    if "/zmluvne-podmienky" in u or "/archiv" in u or "/landing-pages/" in u:
        prior -= 0.25

    # business area handling
    if "/biznis/" in u:
        prior += 0.05 if is_business_query else -0.20

    return max(min(prior, 0.9), -0.6)

# ----------------- Main retrieve() -----------------

def retrieve(db: Session, tenant: str, query: str, k: int | None = None):
    """
    Hybrid retriever:
      1) Cosine over embeddings (preselect top M)
      2) BM25 keyword score over chunk text
      3) URL/title priors (path heuristics)
      4) Aggregate to document level (best chunk wins)
    Returns top-k chunks (one per document) scored by combined metric.
    """
    k = k or settings.top_k
    q_vec = np.array(embed_texts([query])[0], dtype=np.float32)
    q_toks = tokens(query)
    is_business_query = any(t in BUSINESS_HINTS for t in q_toks)

    # Load chunks
    rows = db.execute(
        select(Chunk.id, Chunk.text, Chunk.embedding, Chunk.document_id)
        .where(Chunk.tenant == tenant)
    ).all()
    if not rows:
        return []

    # Cosine preselect
    cos_scored = []
    for cid, text, emb_json, doc_id in rows:
        emb = np.array(json.loads(emb_json), dtype=np.float32)
        cos_scored.append((cid, text, doc_id, cosine(q_vec, emb)))

    M = min(300, len(cos_scored))  # widen a bit
    cos_scored.sort(key=lambda x: x[3], reverse=True)
    prelim = cos_scored[:M]

    # Prepare BM25 per chunk
    prelim_text_tokens = [tokens(text) for (_, text, _, _) in prelim]
    bm25 = bm25_scores(q_toks, prelim_text_tokens)

    # Load documents for priors
    doc_ids = list({doc_id for _, _, doc_id, _ in prelim})
    doc_map = {
        d.id: d
        for d in db.execute(select(Document).where(Document.id.in_(doc_ids))).scalars()
    }

    # Combine scores per chunk
    combined_per_chunk: List[Tuple[int, str, int, float]] = []
    for (idx, (cid, text, doc_id, cos)) in enumerate(prelim):
        d = doc_map.get(doc_id)
        url = getattr(d, "url", "") or ""
        title = getattr(d, "title", "") or ""

        prior = url_title_prior(q_toks, url, title, is_business_query)
        # weights: emphasize semantic (cosine) but allow keyword/priors to steer
        final = 0.60 * cos + 0.25 * bm25[idx] + 0.15 * prior
        combined_per_chunk.append((cid, text, doc_id, final))

    # Aggregate to document: keep best chunk per document
    best_per_doc: Dict[int, Tuple[int, str, int, float]] = {}
    for cid, text, doc_id, score in combined_per_chunk:
        if doc_id not in best_per_doc or score > best_per_doc[doc_id][3]:
            best_per_doc[doc_id] = (cid, text, doc_id, score)

    # Dedup by URL (sometimes same doc inserted multiple times)
    by_url: Dict[str, Tuple[int, str, int, float]] = {}
    for cid, text, doc_id, score in best_per_doc.values():
        d = doc_map.get(doc_id)
        url = getattr(d, "url", "")
        prev = by_url.get(url)
        if prev is None or score > prev[3]:
            by_url[url] = (cid, text, doc_id, score)

    ranked = list(by_url.values())
    ranked.sort(key=lambda x: x[3], reverse=True)

    # return top-k chunks (one per unique URL)
    return ranked[:k]
