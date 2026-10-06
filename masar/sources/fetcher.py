"""
Page fetcher with an automatic browser fallback.

Plain HTTP (requests) is fast, but some sites block it from cloud servers
(GitHub runners included) with 403/429/503 bot checks. On the first such
answer the fetcher switches to a real headless Chromium (Playwright, already
installed for Sabbar) for the rest of the run, and logs that it did.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlencode

import requests

from masar.sources.base import USER_AGENT, proxy_settings

BLOCK_CODES = {403, 429, 503}


@dataclass
class Page:
    status_code: int
    text: str
    url: str


class Fetcher:
    def __init__(self, session: requests.Session, log, timeout: int = 30, force_browser: bool = False):
        self.session, self.log, self.timeout = session, log, timeout
        self.use_browser = force_browser
        self._pw = self._browser = self._page = None

    # -- public ------------------------------------------------------------
    def get(self, url: str, params: dict | None = None) -> Page | None:
        full = f"{url}{'&' if '?' in url else '?'}{urlencode(params)}" if params else url
        if not self.use_browser:
            try:
                resp = self.session.get(full, timeout=self.timeout)
                resp.encoding = resp.apparent_encoding or "utf-8"
                if resp.status_code not in BLOCK_CODES:
                    return Page(resp.status_code, resp.text, resp.url)
                self.log.warning("HTTP %d from %s: switching to the headless browser for this run.",
                                 resp.status_code, full)
            except (requests.RequestException, OSError) as e:
                self.log.warning("Request failed (%s): %s. Switching to the headless browser.", full, e)
            self.use_browser = True
        return self._browser_get(full)

    def close(self) -> None:
        for closer in (self._page, self._browser):
            try:
                closer and closer.close()
            except Exception:
                pass
        if self._pw:
            self._pw.stop()

    # -- browser -----------------------------------------------------------
    def _start(self) -> None:
        from playwright.sync_api import sync_playwright  # lazy: tests don't need it
        _, pw_proxy = proxy_settings()
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=True, proxy=pw_proxy,
            args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx = self._browser.new_context(user_agent=USER_AGENT, locale="en-US",
                                        viewport={"width": 1366, "height": 900})
        self._page = ctx.new_page()

    def _browser_get(self, url: str) -> Page | None:
        if self._page is None:
            self._start()
        for attempt in range(1, 3):
            try:
                resp = self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
                # Bot-check interstitials ("Just a moment...") resolve themselves in a few seconds.
                for _ in range(10):
                    title = (self._page.title() or "").lower()
                    if "just a moment" not in title and "attention required" not in title:
                        break
                    time.sleep(1.5)
                status = resp.status if resp else 0
                if status in BLOCK_CODES and attempt == 1:
                    time.sleep(5)
                    continue
                return Page(status, self._page.content(), self._page.url)
            except Exception as e:
                self.log.warning("Browser failed on %s (attempt %d): %s", url, attempt, str(e)[:120])
                time.sleep(3)
        return None
