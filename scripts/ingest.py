#!/usr/bin/env python
import argparse
import sys
from pathlib import Path
import os
from dotenv import load_dotenv

from app.db import SessionLocal, init_db
from app.ingest_lib import process_pages
from app.crawler import crawl_urls_blocking, allowed

# Load .env from project root
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

def read_urls_from_file(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)
    return [line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith('#')]


def main():
    parser = argparse.ArgumentParser(description="Ivesna ingestion CLI")
    parser.add_argument("--tenant", default="slsp", help="Tenant ID/name")
    parser.add_argument("--url", action="append", dest="urls", default=[], help="Seed URL (repeatable)")
    parser.add_argument("--file", dest="file", help="Path to a file with one URL per line")
    parser.add_argument("--max-pages", dest="max_pages", type=int, default=200, help="Max pages to crawl")
    parser.add_argument("--max-depth", dest="max_depth", type=int, default=3, help="Max crawl depth")
    parser.add_argument("--allow", action="append", default=[], help="Regex to allow paths (repeatable)")
    parser.add_argument("--ignore-robots", action="store_true", help="Ignore robots.txt (use only with permission)")
    args = parser.parse_args()

    urls = args.urls or []
    if args.file:
        urls += read_urls_from_file(args.file)

    urls = [u for u in urls if allowed(u)]
    if not urls:
        print("No allowed URLs provided.")
        sys.exit(0)

    init_db()
    with SessionLocal() as db:
        pages = crawl_urls_blocking(
            urls, 
            max_pages=args.max_pages, 
            max_depth=args.max_depth,
            allow_patterns=args.allow,
            ignore_robots=args.ignore_robots
        )
        stored = process_pages(db, args.tenant, pages)
        print(f"Crawled {len(pages)} page(s), stored {stored} document(s).")


if __name__ == "__main__":
    main()