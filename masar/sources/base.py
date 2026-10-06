"""
Shared machinery for every scraper.

The rules that fix the old data-loss bugs live here, once, instead of being
re-implemented (differently) in each scraper:

1. Landing before checkpoint. A URL is added to the checkpoint only after the
   record for it has been written to the landing file. A crash can never leave
   the checkpoint ahead of the data.
2. One landing file per run (`<source>_<run_ts>.json`). A second run on the
   same day can never overwrite the first one.
3. Failed or invalid pages are NOT checkpointed, so they are retried next run.
4. "Page loaded but no job found" counts as gone only while it is rare. If it
   happens to most pages, the site layout probably changed: nothing is
   checkpointed and the run fails loudly.
5. Every run writes a small summary (data/runs/...) that the pipeline uses for
   health checks, and returns a non-zero exit code when a source is broken.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from masar import storage

# Exit codes (GitHub Actions marks the step red for anything non-zero).
OK = 0
NO_LISTINGS = 2      # discovery found nothing at all -> blocked or layout changed
PARSE_FAILURE = 3    # most detail pages could not be parsed

MIN_ATTEMPTS_FOR_HEALTH = 20
MAX_BAD_RATIO = 0.5

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


def get_logger(name: str) -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return logging.getLogger(name)


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    return int(value) if value else default


# ---------------------------------------------------------------------------
# Optional proxy (same env vars as the old scrapers)
# ---------------------------------------------------------------------------
def proxy_settings():
    """Return (requests_proxies, playwright_proxy) or (None, None)."""
    if os.environ.get("PROXY_ENABLED", "false").lower() != "true":
        return None, None
    host, port = os.environ.get("PROXY_HOST", ""), os.environ.get("PROXY_PORT", "")
    user, pwd = os.environ.get("PROXY_USER", ""), os.environ.get("PROXY_PASS", "")
    url = f"http://{user}:{pwd}@{host}:{port}"
    return (
        {"http": url, "https": url},
        {"server": f"http://{host}:{port}", "username": user, "password": pwd},
    )


def make_session(extra_headers: dict | None = None) -> requests.Session:
    retry = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.5",
    })
    if extra_headers:
        session.headers.update(extra_headers)
    proxies, _ = proxy_settings()
    if proxies:
        session.proxies.update(proxies)
    return session


# ---------------------------------------------------------------------------
# Run bookkeeping
# ---------------------------------------------------------------------------
@dataclass
class SourceRun:
    source: str
    flush_every: int = 50
    log: logging.Logger = None

    started_at: str = field(init=False)
    stamp: str = field(init=False)
    known_urls: set = field(init=False)
    records: list = field(default_factory=list, init=False)
    _pending_urls: set = field(default_factory=set, init=False)  # written, not yet checkpointed
    _gone_urls: set = field(default_factory=set, init=False)     # 404/410: safe to checkpoint
    _soft_gone: list = field(default_factory=list, init=False)   # loaded but empty: checkpoint only if rare
    failed: list = field(default_factory=list, init=False)
    listings_seen: int = field(default=0, init=False)  # job links found, known or new
    discovered: int = field(default=0, init=False)     # new links queued for scraping

    def __post_init__(self):
        self.log = self.log or get_logger(self.source)
        now = storage.utc_now()
        self.started_at = storage.iso(now)
        self.stamp = storage.run_stamp(now)
        self.landing_file = storage.path("landing", self.source, f"{self.source}_{self.stamp}.json")
        self.checkpoint_file = storage.path("checkpoints", f"{self.source}.json")
        self.known_urls = set(storage.read_json(self.checkpoint_file, default=[]))
        self.log.info("Checkpoint: %d URLs already handled", len(self.known_urls))

    # -- outcomes ----------------------------------------------------------
    def success(self, url: str, record: dict, required=("title",)) -> bool:
        """Keep a scraped record. Invalid records count as failures (retried)."""
        missing = [f for f in required if not str(record.get(f) or "").strip()]
        if missing:
            self.failure(url, f"invalid_record: missing {','.join(missing)}")
            return False
        record.setdefault("url", url)
        record["collected_at"] = self.started_at
        record["source_run"] = self.stamp
        self.records.append(record)
        self._pending_urls.add(url)
        if len(self.records) % self.flush_every == 0:
            self.flush()
        return True

    def gone(self, url: str) -> None:
        """Hard evidence the posting no longer exists (HTTP 404/410)."""
        self._gone_urls.add(url)

    def soft_gone(self, url: str, reason: str) -> None:
        """Page loaded but no job data: removed posting OR broken parser."""
        self._soft_gone.append({"url": url, "reason": reason})

    def failure(self, url: str, reason: str) -> None:
        self.failed.append({"url": url, "reason": reason, "attempted_at": self.started_at})

    # -- persistence -------------------------------------------------------
    def flush(self) -> None:
        """Write landing first, then checkpoint only the URLs now safely written."""
        storage.write_json_atomic(self.landing_file, self.records)
        self.known_urls |= self._pending_urls | self._gone_urls
        self._pending_urls.clear()
        self._gone_urls.clear()
        storage.write_json_atomic(self.checkpoint_file, sorted(self.known_urls))

    def finish(self) -> int:
        attempted = len(self.records) + len(self.failed) + len(self._soft_gone)
        bad = len(self.failed) + len(self._soft_gone)
        status, code = "ok", OK

        if self.listings_seen == 0:
            status, code = "no_listings", NO_LISTINGS
        elif attempted >= MIN_ATTEMPTS_FOR_HEALTH and bad / attempted > MAX_BAD_RATIO:
            status, code = "parse_failure", PARSE_FAILURE

        if code == OK:
            # Empty pages are rare -> they really are removed postings.
            self._gone_urls |= {g["url"] for g in self._soft_gone}
        else:
            # Probably a layout change: keep them out of the checkpoint so they
            # are retried once the scraper is fixed.
            self.failed.extend({**g, "attempted_at": self.started_at} for g in self._soft_gone)

        if self.records:
            self.flush()
        else:
            # Still persist gone URLs, but don't create an empty landing file.
            self.known_urls |= self._gone_urls
            storage.write_json_atomic(self.checkpoint_file, sorted(self.known_urls))

        if self.failed:
            storage.write_json_atomic(
                storage.path("failed", self.source, f"{self.source}_{self.stamp}.json"), self.failed)

        summary = {
            "source": self.source, "run": self.stamp, "started_at": self.started_at,
            "finished_at": storage.iso(), "status": status,
            "listings_seen": self.listings_seen, "discovered_new": self.discovered, "scraped": len(self.records),
            "failed": len(self.failed), "gone": len(self._soft_gone) if code == OK else 0,
            "checkpoint_size": len(self.known_urls),
        }
        storage.write_json_atomic(storage.path("runs", f"{self.source}_{self.stamp}.json"), summary)

        self.log.info("=" * 60)
        for k, v in summary.items():
            self.log.info("  %-16s %s", k, v)
        self.log.info("=" * 60)
        if code != OK:
            self.log.error("Source %s is unhealthy: %s", self.source, status)
        return code
