BOT_NAME = "ivesna_bot"
ROBOTSTXT_OBEY = True
LOG_ENABLED = False

# Use Playwright for HTTP(S)
DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}

TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

# Politeness / timeouts
DOWNLOAD_TIMEOUT = 25
CONCURRENT_REQUESTS = 8
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 0.25
AUTOTHROTTLE_MAX_DELAY = 3.0

PLAYWRIGHT_BROWSER_TYPE = "chromium"
PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 20000  # ms
