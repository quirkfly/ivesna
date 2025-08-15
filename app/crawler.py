# app/crawler.py
from __future__ import annotations
from typing import List, Optional
from urllib.parse import urlparse
import re
import xml.etree.ElementTree as ET

import requests
import scrapy
from scrapy.crawler import CrawlerProcess
from scrapy.linkextractors import LinkExtractor
from scrapy.spiders import CrawlSpider, Rule
from scrapy.utils.project import get_project_settings
from scrapy import signals
from scrapy_playwright.page import PageMethod

from tqdm import tqdm

from .config import settings

ALLOWED_DOMAINS = set(settings.allowed_domains)

# ------------------ helpers ------------------

def allowed(url: str) -> bool:
    try:
        netloc = urlparse(url).netloc.lower()
        return any(netloc.endswith(d) for d in ALLOWED_DOMAINS)
    except Exception:
        return False

def _extract_text(response: scrapy.http.Response) -> str:
    """
    Class-agnostic extraction using semantic tags; falls back to body text.
    """
    sel = response.selector
    parts = sel.xpath(
        """
        //main//text() |
        //article//text() |
        //section//text() |
        //p//text() |
        //li//text() |
        //td//text() | //th//text() |
        //h1//text() | //h2//text() | //h3//text()
        """
    ).getall()
    if not parts:
        parts = sel.xpath("//body//text()").getall()
    text = " ".join(" ".join(t.split()) for t in parts if t and t.strip())
    return text

def _short(s: str, n: int = 60) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"

def _compile_allow(patterns: Optional[List[str]], domains: List[str]) -> Optional[List[re.Pattern]]:
    """
    Make user-supplied allow patterns robust:
    - '^/path.*' -> matches both https://slsp.sk/path and https://www.slsp.sk/path
    - raw substrings -> treated as 'contains' on same hosts
    - full '^https?://...' kept as-is
    """
    if not patterns:
        return None
    compiled: List[re.Pattern] = []
    host_alt = "|".join(re.escape(d) for d in domains) or r"(?:slsp\.sk|www\.slsp\.sk)"
    for p in patterns:
        if not p:
            continue
        p = p.strip()
        if p.startswith("^http://") or p.startswith("^https://"):
            compiled.append(re.compile(p))
        elif p.startswith("^/"):
            rx = rf"^https?://(?:{host_alt}){p}"
            compiled.append(re.compile(rx))
        else:
            rx = rf"^https?://(?:{host_alt})/.+{re.escape(p)}"
            compiled.append(re.compile(rx))
    return compiled or None

def sitemap_seed(root: str, allow_patterns: List[str], domains: List[str]) -> List[str]:
    """
    Pull /sitemap.xml and return URLs matching compiled allow rules.
    Best-effort; ignored on errors.
    """
    seeds: List[str] = []
    try:
        r = requests.get(root.rstrip("/") + "/sitemap.xml", timeout=15)
        r.raise_for_status()
        rx_list = _compile_allow(allow_patterns, domains) or []
        xml_root = ET.fromstring(r.text)
        for loc in xml_root.findall(".//{*}loc"):
            u = (loc.text or "").strip()
            if not u or not allowed(u):
                continue
            if not rx_list or any(rx.search(u) for rx in rx_list):
                seeds.append(u)
    except Exception:
        pass
    return seeds

# ---------- Playwright behaviors (expand/click/scroll) ----------

PLAYWRIGHT_METHODS = [
    PageMethod("wait_for_load_state", "domcontentloaded"),
    PageMethod(
        "evaluate",
        """
        () => {
          // Open <details>
          document.querySelectorAll('details').forEach(d => d.open = true);

          // Force-expand common nav/tabs/accordions
          const clickables = [
            ...document.querySelectorAll('[aria-expanded="false"], [aria-haspopup="true"]'),
            ...document.querySelectorAll('nav button, nav [role="button"], nav a[role="tab"]'),
            ...document.querySelectorAll('[data-toggle], [data-target], .accordion-button, .tab, .tab-link')
          ];
          clickables.forEach(el => { try { el.click(); } catch(e) {} });

          // Generic "show more" triggers
          const labels = ['viac','zobraziť viac','zobrazit viac','show more','more','detail','expand'];
          [...document.querySelectorAll('button, a')].forEach(el => {
            const t = (el.innerText || el.textContent || '').trim().toLowerCase();
            if (labels.some(l => t.includes(l))) { try { el.click(); } catch(e) {} }
          });

          // Scroll top->bottom to load lazy content
          const h = document.documentElement.scrollHeight || 4000;
          window.scrollTo(0, 0);
          setTimeout(() => window.scrollTo(0, h), 50);
        }
        """,
    ),
    PageMethod("wait_for_load_state", "networkidle"),
    PageMethod("wait_for_timeout", 300),
]

# ------------------ Spider ------------------

class SiteCrawler(CrawlSpider):
    name = "site_crawler"

    def __init__(
        self,
        start_urls: List[str],
        max_pages: int = 200,
        max_depth: int = 3,
        allow_patterns: Optional[List[str]] = None,
        ignore_robots: bool = False,
        *args, **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.start_urls = [u for u in start_urls if allowed(u)]
        self.allowed_domains = list({urlparse(u).netloc for u in self.start_urls} | ALLOWED_DOMAINS)
        self.allow_patterns = allow_patterns or []
        self._allow_regex = _compile_allow(self.allow_patterns, self.allowed_domains)
        self.results: List[dict] = []

        self.custom_settings = {
            "ROBOTSTXT_OBEY": not ignore_robots,
            "CLOSESPIDER_PAGECOUNT": int(max_pages),
            "DEPTH_LIMIT": int(max_depth),
            "LOG_ENABLED": False,
            "CLOSESPIDER_TIMEOUT": 240,
        }

        self.rules = (
            Rule(
                LinkExtractor(
                    allow_domains=self.allowed_domains,
                    allow=self._allow_regex,
                    deny_extensions=[
                        "jpg","jpeg","png","gif","svg","webp",
                        "pdf","zip","gz","tar","rar","7z",
                        "mp3","mp4","avi","mov","wmv","mkv",
                        "woff","woff2","ttf","eot",
                    ],
                    unique=True,
                ),
                callback="parse_page",
                follow=True,
                process_request="use_playwright",
            ),
        )
        super()._compile_rules()

    def use_playwright(self, request, response=None):
        request.meta["playwright"] = True
        request.meta["playwright_page_methods"] = PLAYWRIGHT_METHODS
        request.meta["playwright_context_kwargs"] = {"locale": "sk-SK"}
        return request

    def parse_start_url(self, response):
        return self.parse_page(response)

    def parse_page(self, response):
        if getattr(response, "status", 200) != 200:
            return
        title = (response.xpath("//title/text()").get() or response.url).strip()
        meta_desc = (
            response.xpath('//meta[@name="description"]/@content').get()
            or response.xpath('//meta[@property="og:description"]/@content').get()
            or ""
        ).strip()
        text = _extract_text(response)
        combined = "\n".join([t for t in (title, meta_desc, text) if t])
        if combined:
            self.results.append({"url": response.url, "title": title, "text": combined})

        # Optional: small follow summary (first few matches)
        try:
            le = LinkExtractor(
                allow_domains=self.allowed_domains,
                allow=self._allow_regex,
                deny_extensions=["jpg","jpeg","png","gif","svg","webp","pdf","zip","gz","tar","rar","7z","mp3","mp4","avi","mov","wmv","mkv","woff","woff2","ttf","eot"],
                unique=True,
            )
            links = le.extract_links(response)
            if links:
                from tqdm import tqdm as _tqdm
                _tqdm.write(f"[follow] {response.url} -> {min(5,len(links))}/{len(links)} sample:")
                for l in links[:5]:
                    _tqdm.write(f"  - {l.url}")
            else:
                from tqdm import tqdm as _tqdm
                _tqdm.write(f"[follow] {response.url} -> 0 links matched allow rules")
        except Exception:
            pass

# ------------------ Runner with tqdm + sitemap seeding ------------------

PAGE_PARSED = object()

def crawl_urls_blocking(
    urls: List[str],
    max_pages: int = 200,
    max_depth: int = 3,
    allow_patterns: Optional[List[str]] = None,
    ignore_robots: bool = False,
) -> List[dict]:
    """
    Run crawler synchronously; show tqdm progress (advances when a page is parsed).
    Also seeds from sitemap.xml for each start URL if allow_patterns are provided.
    """
    settings_obj = get_project_settings()
    process = CrawlerProcess(settings=settings_obj)

    # sitemap seeding using host-agnostic compiled rules
    domains = list({urlparse(u).netloc for u in urls} | ALLOWED_DOMAINS)
    seed_urls = list({*urls})
    if allow_patterns:
        extra: List[str] = []
        for u in urls:
            extra += sitemap_seed(u, allow_patterns, domains)
        seed_urls = list({*seed_urls, *extra})

    results_container = {"items": []}
    bar = tqdm(total=max_pages, desc="Crawling", unit="page")

    def on_page_parsed(spider, url, title):
        bar.set_description_str(f"Crawling | {_short(title or url)}")
        if bar.n < bar.total:
            bar.update(1)

    def on_spider_closed(spider, reason):
        results_container["items"] = getattr(spider, "results", [])
        if bar.n < bar.total:
            bar.update(bar.total - bar.n)
        bar.close()

    crawler = process.create_crawler(SiteCrawler)
    crawler.signals.connect(on_spider_closed, signal=signals.spider_closed)
    crawler.signals.connect(on_page_parsed, signal=PAGE_PARSED)

    # wrap parse_page to emit PAGE_PARSED with URL+title when we stored a page
    original_parse_page = SiteCrawler.parse_page
    def wrapped_parse_page(self, response):
        before = len(self.results)
        res = original_parse_page(self, response)
        after = len(self.results)
        if after > before:
            page = self.results[-1]
            crawler.signals.send_catch_log(PAGE_PARSED, spider=self, url=page["url"], title=page["title"])
        return res
    SiteCrawler.parse_page = wrapped_parse_page  # type: ignore

    process.crawl(
        crawler,
        start_urls=seed_urls,
        max_pages=max_pages,
        max_depth=max_depth,
        allow_patterns=allow_patterns,
        ignore_robots=ignore_robots,
    )
    process.start()  # blocks until done
    return results_container["items"]
