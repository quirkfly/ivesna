from typing import List
from sqlalchemy.orm import Session

from .ingest_lib import process_pages
from .crawler import crawl_urls_blocking, allowed


def ingest_urls(db: Session, tenant: str, urls: List[str]) -> dict:
    urls = [u for u in urls if allowed(u)]
    if not urls:
        return {"documents": 0}
    pages = crawl_urls_blocking(urls)
    stored = process_pages(db, tenant, pages)
    return {"documents": stored}