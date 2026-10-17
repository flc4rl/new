"""
Polite same-site crawler with an on-disk page cache.

Every fetched page is stored under ``<output>/cache/`` keyed by a hash of its
URL, so a crawl can be interrupted and resumed, and the extraction stage can
be re-run with different themes / settings without touching the network
again (``--no-crawl``).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import zlib
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple
from urllib import robotparser
from urllib.parse import parse_qs, quote, urldefrag, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, UnicodeDammit
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)
# urllib3 logs every retry as a warning; the crawler reports the final outcome itself.
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)

SKIP_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tif", ".tiff",
    ".mp3", ".mp4", ".m4a", ".wav", ".ogg", ".webm", ".mov", ".avi", ".mkv",
    ".zip", ".gz", ".tar", ".rar", ".7z", ".exe", ".dmg", ".iso",
    ".css", ".js", ".json", ".xml", ".rss", ".atom",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".epub",
    ".woff", ".woff2", ".ttf", ".eot",
}

# Query parameters that only track or sort and would otherwise make the
# crawler visit the same page many times.
TRACKING_PARAMS = re.compile(r"^(utm_\w+|fbclid|gclid|mc_cid|mc_eid|replytocom|share|ref|amp)$", re.I)

# Temporary server answers: never cached, so a resumed run asks again.
TRANSIENT_STATUSES = {408, 425, 429}
MAX_RETRY_AFTER = 10.0     # seconds; a server cannot make the run sleep longer per retry


def is_transient(status: int) -> bool:
    """True for HTTP statuses that mean 'try again later' (timeouts, rate limits, 5xx)."""
    return status in TRANSIENT_STATUSES or status >= 500


class _CappedRetry(Retry):
    """urllib3 Retry that honours Retry-After, but never for longer than MAX_RETRY_AFTER."""

    def get_retry_after(self, response):  # noqa: D401 - urllib3 hook
        value = super().get_retry_after(response)
        return None if value is None else min(value, MAX_RETRY_AFTER)


def make_session(user_agent: str) -> requests.Session:
    """A requests session that retries connection errors, 429 and 5xx with backoff.

    POST is included because the calendar's admin-ajax month requests are reads.
    After the last retry the final error response is returned (not raised), so
    the caller still sees the status code.
    """
    kwargs = dict(total=3, connect=2, read=1, status=3, backoff_factor=1.5,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=frozenset({"GET", "HEAD", "POST"}),
                  respect_retry_after_header=True, raise_on_status=False)
    try:
        retry = _CappedRetry(backoff_max=60, **kwargs)     # urllib3 2.x
    except TypeError:
        retry = _CappedRetry(**kwargs)                     # urllib3 1.26
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5",
    })
    return session


def fmt_duration(seconds: float) -> str:
    """'1h05m', '4m10s' or '12s'."""
    seconds = max(0, int(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s" if m else f"{s}s"


@dataclass
class CrawlConfig:
    start_urls: List[str]
    max_pages: int = 500
    max_depth: int = 5
    delay: float = 1.0
    timeout: float = 20.0
    user_agent: str = "site-actor-mapping/1.0 (academic research crawler)"
    respect_robots: bool = True
    include_subdomains: bool = False
    include_patterns: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)
    include_pdfs: bool = True
    pdfs_from_other_hosts: bool = True   # embedded via Drive, a CDN, a partner's site ...
    max_pdf_mb: float = 50.0
    use_sitemap: bool = False
    render_js: bool = False
    refresh: bool = False
    calendar: bool = True                  # sweep embedded calendars that are detected
    calendar_from: Optional[str] = None    # YYYY-MM; default: until 12 empty months
    calendar_to: Optional[str] = None      # YYYY-MM; default: until 3 empty months
    calendar_click: Optional[str] = None   # CSS selector of a "previous month" button
    calendar_clicks: int = 24
    calendar_url_pattern: Optional[str] = None  # only sweep calendars on matching URLs
    # PDF budget. None: PDFs count against max_pages (the main site). A number:
    # PDFs have their own budget and never use up the page budget (linked sites).
    max_pdfs: Optional[int] = None
    # If a start URL redirects to another domain, crawl that domain too (linked
    # sites that have moved), unless it is one of blocked_hosts.
    follow_start_redirects: bool = False
    blocked_hosts: Set[str] = field(default_factory=set)
    progress_every: float = 60.0           # seconds between progress/ETA lines (0: never)


@dataclass
class CachedPage:
    url: str
    final_url: str
    status: int
    content_type: str
    depth: int
    fetched_at: float
    body: str            # HTML, or extracted text for PDFs (pages separated by \f)
    is_pdf: bool = False
    referrer: str = ""   # for PDFs: the page that linked or embedded it
    title: str = ""      # for PDFs: document metadata title
    date: str = ""       # for PDFs: document metadata creation date
    kind: str = ""       # "calendar" for a synthetic page holding one loaded calendar month
    label: str = ""      # for calendar pages: YYYY-MM or click number


def safe_join(base: str, href: str) -> str:
    """urljoin that never raises: a malformed href (e.g. 'http://x.org]') gives ""."""
    try:
        return urljoin(base, href)
    except ValueError:
        return ""


def normalise_url(url: str) -> str:
    """Drop fragments and tracking params, lowercase scheme/host, tidy the path.

    Total: anything that is not a well-formed http(s) URL gives "" (never raises).
    """
    try:
        url, _ = urldefrag((url or "").strip())
        p = urlparse(url)
        _ = p.hostname, p.port     # validates [IPv6] brackets and the port number
    except (ValueError, TypeError, AttributeError):
        return ""
    if p.scheme not in ("http", "https") or not p.netloc:
        return ""
    query = "&".join(
        kv for kv in p.query.split("&")
        if kv and not TRACKING_PARAMS.match(kv.split("=", 1)[0])
    )
    path = re.sub(r"/{2,}", "/", p.path or "/")
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", query, ""))


def registered_host(host: str) -> str:
    host = host.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def cache_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


# A cache file cut short (power loss, force-quit) or damaged on disk raises one of these.
_CACHE_ERRORS = (OSError, ValueError, TypeError, EOFError, zlib.error)


class PageCache:
    """One gzipped JSON file per URL. Writes are atomic and safe from several threads."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        for leftover in self.root.glob(".put-*.part"):   # from a run killed mid-write
            try:
                leftover.unlink()
            except OSError:
                pass

    def _path(self, url: str) -> Path:
        return self.root / f"{cache_key(url)}.json.gz"

    def _load(self, path: Path) -> Optional[CachedPage]:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return CachedPage(**json.load(fh))
        except _CACHE_ERRORS as exc:
            # Never fatal: move the file aside so the page is simply fetched again.
            log.warning("Unreadable cache file %s (%s); moved aside as .bad, will be refetched",
                        path.name, exc)
            try:
                path.replace(path.with_name(path.name + ".bad"))
            except OSError:
                pass
            return None

    def get(self, url: str) -> Optional[CachedPage]:
        path = self._path(url)
        return self._load(path) if path.exists() else None

    def put(self, page: CachedPage) -> None:
        # Write to a unique temporary file, then rename: a reader (or a crash)
        # never sees half a file, and two threads writing one URL do not collide.
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".put-", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as raw, gzip.open(raw, "wt", encoding="utf-8") as fh:
                json.dump(page.__dict__, fh)
            os.replace(tmp, self._path(page.url))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def __iter__(self) -> Iterator[CachedPage]:
        for path in sorted(self.root.glob("*.json.gz")):   # .part / .bad files never match
            cp = self._load(path)
            if cp is not None:
                yield cp


_PAGINATED = re.compile(r"/page/\d+/?$")


def calendar_home(url: str) -> str:
    """The canonical page of a calendar: /events/page/2/ and ?paged=2 give /events/."""
    p = urlparse(url)
    path = _PAGINATED.sub("/", p.path)
    query = "&".join(kv for kv in p.query.split("&") if kv and not re.fullmatch(r"paged?=\d+", kv))
    return urlunparse((p.scheme, p.netloc, path, "", query, ""))


_WRAP_HEAD, _WRAP_TAIL = "<html><body><main>", "</main></body></html>"


class Crawler:
    def __init__(self, cfg: CrawlConfig, cache: PageCache):
        self.cfg = cfg
        self.cache = cache
        self.session = make_session(cfg.user_agent)
        self.hosts = {registered_host(urlparse(u).netloc) for u in cfg.start_urls}
        self.include_re = [re.compile(p) for p in cfg.include_patterns]
        self.exclude_re = [re.compile(p) for p in cfg.exclude_patterns]
        self.robots: Dict[str, Optional[robotparser.RobotFileParser]] = {}
        self.robots_blocked: List[Tuple[str, str]] = []   # (host, reason): robots.txt unreachable
        self._last_fetch = 0.0
        self._browser = None
        self._playwright = None
        self._swept: Set[str] = set()          # calendar identities already loaded
        self._cal_home: Dict[str, str] = {}    # calendar identity -> canonical host page
        # What happened to each URL: "ok", an HTTP status (int), "connection",
        # "download", "robots", "robots-unreachable", "other type", "error".
        self.outcomes: Counter = Counter()
        self.aliases: Set[str] = set()         # hosts that start URLs redirected to
        self.stop: Optional[threading.Event] = None   # set from outside to end crawl() early

    # ------------------------------------------------------------------ scope
    def in_scope(self, url: str) -> bool:
        p = urlparse(url)
        host = registered_host(p.netloc)
        if self.cfg.include_subdomains:
            if not any(host == h or host.endswith("." + h) for h in self.hosts):
                return False
        elif host not in self.hosts:
            return False
        ext = Path(p.path).suffix.lower()
        if ext in SKIP_EXTENSIONS:
            return False
        if ext == ".pdf" and not self.cfg.include_pdfs:
            return False
        if self.include_re and not any(r.search(url) for r in self.include_re):
            return False
        if any(r.search(url) for r in self.exclude_re):
            return False
        return True

    def pdf_in_scope(self, url: str) -> bool:
        """PDFs found on an in-scope page are read even when hosted elsewhere, but
        never crawled onwards; --include patterns apply to pages, not to PDFs."""
        if not self.cfg.include_pdfs or any(r.search(url) for r in self.exclude_re):
            return False
        if self.cfg.pdfs_from_other_hosts:
            return True
        host = registered_host(urlparse(url).netloc)
        return host in self.hosts or (self.cfg.include_subdomains
                                      and any(host.endswith("." + h) for h in self.hosts))

    def allowed(self, url: str) -> bool:
        if not self.cfg.respect_robots:
            return True
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self.robots:
            rp: Optional[robotparser.RobotFileParser] = robotparser.RobotFileParser()
            status: Optional[int] = None
            reason = ""
            try:   # the session already retries connection errors, 429 and 5xx
                resp = self.session.get(base + "/robots.txt", timeout=self.cfg.timeout)
                status = resp.status_code
                text = resp.text if status < 400 else ""
            except requests.RequestException as exc:
                reason = f"connection failed: {type(exc).__name__}"
                log.debug("robots.txt for %s: %s", base, exc)
            if status is not None and status < 400:
                rp.parse(text.splitlines())
            elif status is not None and status < 500 and status not in (401, 403, 429):
                rp = None   # 404/410 etc.: there is no robots.txt, so everything is allowed
            else:
                # RFC 9309 2.3.1.4: an unreachable robots.txt (5xx, network error) means
                # "disallow all"; 401/403 are treated the same way, as Python's own parser does.
                rp.disallow_all = True
                reason = reason or f"HTTP {status}"
                log.warning("robots.txt for %s could not be read (%s); host treated as disallowed",
                            base, reason)
                self.robots_blocked.append((base, reason))
            self.robots[base] = rp
        rp = self.robots[base]
        return rp is None or rp.can_fetch(self.cfg.user_agent, url)

    # ------------------------------------------------------------------ fetch
    def _wait(self) -> None:
        gap = time.monotonic() - self._last_fetch
        if gap < self.cfg.delay:
            time.sleep(self.cfg.delay - gap)
        self._last_fetch = time.monotonic()

    def _render(self, url: str) -> Tuple[str, str, int]:
        if self._browser is None:
            from playwright.sync_api import sync_playwright  # optional dependency
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch()
        page = self._browser.new_page(user_agent=self.cfg.user_agent)
        try:
            resp = page.goto(url, timeout=self.cfg.timeout * 1000, wait_until="networkidle")
            return page.content(), page.url, resp.status if resp else 200
        finally:
            page.close()

    def fetch(self, url: str, depth: int, referrer: str = "",
              expect_pdf: bool = False) -> Optional[CachedPage]:
        """Fetch one URL. None means nothing usable (and nothing to cache)."""
        self._wait()
        try:
            resp = self.session.get(url, timeout=self.cfg.timeout, allow_redirects=True, stream=True)
        except requests.RequestException as exc:
            log.warning("Fetch failed %s: %s", url, exc)
            self.outcomes["connection"] += 1
            return None
        ctype = resp.headers.get("Content-Type", "").lower()
        final_url = normalise_url(resp.url) or url
        if resp.status_code >= 400:
            resp.close()
            return CachedPage(url, final_url, resp.status_code, ctype, depth, time.time(), "",
                              is_pdf=expect_pdf, referrer=referrer)

        looks_pdf = "pdf" in ctype or urlparse(final_url).path.lower().endswith(".pdf")
        if looks_pdf or expect_pdf:
            data = self._read_capped(resp)
            if data is None or not data.lstrip()[:5].startswith(b"%PDF"):
                if expect_pdf and data is not None:
                    log.info("Not a PDF after all (%s): %s", ctype or "no type", url)
                self.outcomes["download" if data is None else "other type"] += 1
                return None
            text, title, date = pdf_to_text(data)
            if not text.strip():
                log.warning("PDF has no extractable text (scanned image?): %s", url)
            return CachedPage(url, final_url, resp.status_code, ctype, depth, time.time(),
                              text, is_pdf=True, referrer=referrer, title=title, date=date)
        if "html" not in ctype and "xml" not in ctype:
            resp.close()
            self.outcomes["other type"] += 1
            return None

        # The body is downloaded here, after the headers: a dropped connection or a
        # read timeout at this point costs this page only (it is not cached, so a
        # re-run with the same output folder fetches it again).
        try:
            if "charset=" in ctype:
                body = resp.text
            else:  # no declared charset: sniff <meta charset> / BOM rather than assume Latin-1
                body = UnicodeDammit(resp.content, is_html=True).unicode_markup or resp.text
        except requests.RequestException as exc:
            log.warning("Download failed %s: %s", url, exc)
            self.outcomes["download"] += 1
            return None
        finally:
            resp.close()
        if self.cfg.render_js:
            try:
                body, rendered_url, _ = self._render(url)
                final_url = normalise_url(rendered_url) or final_url
            except Exception as exc:  # noqa: BLE001 - playwright raises many types
                log.warning("JS render failed for %s (%s); using raw HTML", url, exc)
        return CachedPage(url, final_url, resp.status_code, ctype, depth, time.time(), body)

    def _read_capped(self, resp: requests.Response) -> Optional[bytes]:
        cap = int(self.cfg.max_pdf_mb * 1024 * 1024)
        chunks, size = [], 0
        try:
            for chunk in resp.iter_content(64 * 1024):
                size += len(chunk)
                if size > cap:
                    log.warning("PDF larger than %.0f MB skipped: %s", self.cfg.max_pdf_mb, resp.url)
                    return None
                chunks.append(chunk)
        except requests.RequestException as exc:
            log.warning("Download failed %s: %s", resp.url, exc)
            return None
        finally:
            resp.close()
        return b"".join(chunks)

    # ------------------------------------------------------------------ seeds
    def sitemap_urls(self) -> List[str]:
        found: List[str] = []
        queue = deque()
        for u in self.cfg.start_urls:
            p = urlparse(u)
            queue.append(f"{p.scheme}://{p.netloc}/sitemap.xml")
            if self.cfg.respect_robots and self.allowed(u):
                rp = self.robots.get(f"{p.scheme}://{p.netloc}")
                if rp is not None:
                    queue.extend(rp.site_maps() or [])
        seen: Set[str] = set()
        while queue and len(found) < self.cfg.max_pages * 2:
            sm = queue.popleft()
            if sm in seen:
                continue
            seen.add(sm)
            try:
                resp = self.session.get(sm, timeout=self.cfg.timeout)
                if resp.status_code >= 400:
                    continue
                content = resp.content
                if sm.endswith(".gz"):
                    content = gzip.decompress(content)
                root = ET.fromstring(content)
            except (requests.RequestException, ET.ParseError, OSError, EOFError, zlib.error, ValueError):
                continue
            for loc in root.iter():
                if not loc.tag.endswith("loc") or not loc.text:
                    continue
                if root.tag.endswith("sitemapindex"):
                    queue.append(loc.text.strip())
                else:
                    found.append(loc.text.strip())
        log.info("Sitemap seeding found %d URLs", len(found))
        return found

    # ------------------------------------------------------------------ crawl
    def crawl(self) -> int:
        """Crawl and return the number of pages and PDFs fetched or reused from cache.

        Three queues with strict priority: the start URLs first (so the exact page
        an event links to is never crowded out), then PDFs linked from pages
        already read, then everything else in breadth-first order.
        """
        starts: deque = deque()   # (url, depth, referrer, is_pdf)
        pdfq: deque = deque()
        queue: deque = deque()
        seen: Set[str] = set()
        for u in self.cfg.start_urls:
            n = normalise_url(u)
            if n and n not in seen:
                starts.append((n, 0, "", False))
                seen.add(n)
                # A shared Google Drive file / Google Doc: read the document itself too.
                if urlparse(n).netloc in FILE_HOSTS:
                    target = unwrap_pdf_url(n)
                    if target and target not in seen and self.pdf_in_scope(target):
                        pdfq.append((target, 1, n, True))
                        seen.add(target)
        if self.cfg.use_sitemap:
            for u in self.sitemap_urls():
                n = normalise_url(u)
                if n and n not in seen and self.in_scope(n):
                    queue.append((n, 1, "", False))
                    seen.add(n)

        shared = self.cfg.max_pdfs is None          # PDFs count against max_pages
        pdf_cap = self.cfg.max_pages if shared else self.cfg.max_pdfs
        fetched = pdfs = 0                          # pages (and PDFs if shared), separate PDFs
        t0 = last_report = time.monotonic()
        network = 0                                 # items actually requested this run
        try:
            while True:
                if self.stop is not None and self.stop.is_set():
                    log.info("Crawl stopped early")
                    break
                if starts and fetched < self.cfg.max_pages:
                    src = starts
                elif pdfq and (fetched < self.cfg.max_pages if shared else pdfs < pdf_cap):
                    src = pdfq
                elif queue and fetched < self.cfg.max_pages:
                    src = queue
                else:
                    break
                url, depth, referrer, is_pdf = src.popleft()
                try:
                    page = None if self.cfg.refresh else self.cache.get(url)
                    if page is not None and is_transient(page.status):
                        page = None     # an earlier run hit a temporary error: ask again
                    from_cache = page is not None
                    if page is None:
                        if not self.allowed(url):
                            base = "{0.scheme}://{0.netloc}".format(urlparse(url))
                            blocked = any(b == base for b, _ in self.robots_blocked)
                            self.outcomes["robots-unreachable" if blocked else "robots"] += 1
                            log.info("robots.txt disallows %s", url)
                            continue
                        network += 1
                        page = self.fetch(url, depth, referrer, expect_pdf=is_pdf)
                        if page is None:
                            continue    # fetch() logged and counted why
                        if is_transient(page.status):
                            self.outcomes[page.status] += 1
                            log.warning("HTTP %s (temporary error, retries used up; not cached, "
                                        "so the next run tries again): %s", page.status, url)
                            continue
                        self.cache.put(page)
                    if page.status >= 400:
                        self.outcomes[page.status] += 1
                        if not from_cache:
                            log.warning("HTTP %s, not analysed: %s", page.status, url)
                        continue
                    self.outcomes["ok"] += 1
                    if page.is_pdf and not shared:
                        pdfs += 1
                        if not from_cache:
                            log.info("[PDF %d/%d] d=%d %s", pdfs, pdf_cap, depth, url)
                    else:
                        fetched += 1
                        if not from_cache:
                            log.info("[%d/%d] d=%d %s", fetched, self.cfg.max_pages, depth, url)

                    if depth == 0 and not page.is_pdf and not page.kind:
                        self._note_redirect(page)
                    if page.is_pdf or not page.body:
                        continue
                    for cal in self.calendar_pages(page):
                        fetched += 1
                        self._enqueue_links(cal, depth, queue, pdfq, seen)
                    self._enqueue_links(page, depth, queue, pdfq, seen)
                except Exception as exc:  # noqa: BLE001 - one bad page must not end the crawl
                    self.outcomes["error"] += 1
                    log.error("Skipped %s after an unexpected error: %s: %s",
                              url, type(exc).__name__, exc)
                finally:
                    now = time.monotonic()
                    if self.cfg.progress_every and now - last_report >= self.cfg.progress_every:
                        last_report = now
                        self._log_progress(now - t0, network, fetched, pdfs,
                                           len(starts) + len(pdfq) + len(queue))
        finally:
            if self._browser is not None:
                self._browser.close()
                self._playwright.stop()
        errors = sum(n for k, n in self.outcomes.items() if k != "ok")
        log.info("Crawl finished: %d pages%s fetched or reused from cache, %d URLs failed or "
                 "skipped, %d queued URLs not visited", fetched,
                 "" if shared else f" and {pdfs} PDFs", errors, len(starts) + len(pdfq) + len(queue))
        return fetched + pdfs

    def _log_progress(self, elapsed: float, network: int, fetched: int, pdfs: int, queued: int) -> None:
        """One progress line with an estimate of the time left if the page limit is reached."""
        left = min(queued, max(0, self.cfg.max_pages - fetched)) if queued else 0
        per = elapsed / network if network else 0.0
        eta = f", at most ~{fmt_duration(left * per)} left" if per and left else ""
        log.info("Crawl progress: %d/%d pages%s after %s, %d URLs queued%s", fetched,
                 self.cfg.max_pages, f" + {pdfs} PDFs" if pdfs else "", fmt_duration(elapsed),
                 queued, eta)

    def _note_redirect(self, page: CachedPage) -> None:
        """A start URL that redirected to another domain: crawl that domain as well."""
        if not self.cfg.follow_start_redirects:
            return
        host = registered_host(urlparse(page.final_url).netloc)
        blocked = self.cfg.blocked_hosts
        if (not host or host in self.hosts
                or any(host == b or host.endswith("." + b) for b in blocked)):
            return
        self.hosts.add(host)
        self.aliases.add(host)
        log.info("%s redirects to %s; crawling that host too", page.url, host)

    def _enqueue_links(self, page: CachedPage, depth: int, queue: deque, pdfq: deque,
                       seen: Set[str]) -> None:
        links, pdfs = extract_links(page.body, page.final_url)
        # PDFs on a page are part of that page, so they are read even at max depth
        # (in page order, after the remaining start URLs).
        for link in pdfs:
            if link not in seen and self.pdf_in_scope(link):
                seen.add(link)
                pdfq.append((link, depth + 1, page.final_url, True))
        if depth >= self.cfg.max_depth:
            return
        for link in links:
            if link not in seen and self.in_scope(link):
                seen.add(link)
                queue.append((link, depth + 1, "", False))

    # --------------------------------------------------------------- calendar
    def calendar_pages(self, page: CachedPage) -> List[CachedPage]:
        """Load every month of each calendar embedded in `page`, once per calendar.

        A calendar is identified by its own settings (admin-ajax URL, shortcode
        attributes and skin id), not by the page showing it, so the paginated
        archive copies (/events/page/2/ ...) do not load the same months again.
        Month pages are anchored to the calendar's canonical page (/events/).
        """
        if not self.cfg.calendar or page.kind:
            return []
        if self.cfg.calendar_url_pattern and not re.search(self.cfg.calendar_url_pattern, page.final_url):
            return []
        from calendar_sweep import calendar_id, detect_mec, parse_month, sweep_by_clicking, sweep_mec

        home = calendar_home(page.final_url)
        # The index remembers a sweep for this exact range/method, so a narrow test
        # run is not mistaken for a full sweep when the output folder is reused.
        scope = f"{self.cfg.calendar_from or ''}_{self.cfg.calendar_to or ''}_{self.cfg.calendar_click or ''}"
        if self.cfg.calendar_click:
            scope += f"_{self.cfg.calendar_clicks}"
            cid = hashlib.sha1(home.encode("utf-8")).hexdigest()[:10]
            if cid in self._swept or not (re.search(r"mec-|calendar|fc-|tribe-events", page.body, re.I)
                                          or self.cfg.calendar_url_pattern):
                return []
            jobs = [(cid, None)]
            n_on_page = 1
        else:
            mecs = detect_mec(page.body, page.final_url)
            n_on_page = len(mecs)
            jobs = []
            for mec in mecs:
                cid = calendar_id(mec)
                if cid in self._swept:
                    log.debug("Calendar on %s already loaded from %s", page.final_url, self._cal_home[cid])
                elif cid not in {c for c, _ in jobs}:
                    jobs.append((cid, mec))

        out: List[CachedPage] = []
        for cid, mec in jobs:
            self._swept.add(cid)
            cal_home = self._cal_home.setdefault(cid, home)
            sep = "&" if "?" in cal_home else "?"
            # Several calendars on one page (or one canonical page): keep their months apart.
            shared_home = any(h == cal_home and c != cid for c, h in self._cal_home.items())
            tag = f"&cal={cid}" if (n_on_page > 1 or shared_home) else ""
            kind = "click" if mec is None else "month"
            index_url = f"{cal_home}{sep}calendar_index={cid}_{quote(scope, safe='')}"

            if not self.cfg.refresh:
                index = self.cache.get(index_url)
                if index is not None:
                    try:
                        data = json.loads(index.body or "[]")
                    except ValueError:
                        data = {}
                    if isinstance(data, list):          # older runs: a plain list of URLs
                        data = {"pages": data, "failed": [], "complete": True}
                    urls = data.get("pages", []) if isinstance(data, dict) else []
                    cached = [p for u in urls if (p := self.cache.get(u))]
                    if (isinstance(data, dict) and data.get("complete", True) and not data.get("failed")
                            and len(cached) == len(urls) and cached):
                        out += cached
                        continue
                    log.info("Calendar on %s was not complete last time; loading the missing months",
                             cal_home)

            def month_url(label: str) -> str:
                return f"{cal_home}{sep}calendar_{kind}={label}{tag}"

            reused: Dict[str, CachedPage] = {}

            def have(label: str) -> Optional[str]:
                """Month already in the cache (an interrupted or partly failed sweep)."""
                if self.cfg.refresh:
                    return None
                cp = self.cache.get(month_url(label))
                if cp is None or cp.kind != "calendar":
                    return None
                reused[label] = cp
                body = cp.body
                if body.startswith(_WRAP_HEAD) and body.endswith(_WRAP_TAIL):
                    body = body[len(_WRAP_HEAD):-len(_WRAP_TAIL)]
                return body

            if mec is None:
                log.info("Calendar on %s: clicking %r up to %d times", page.final_url,
                         self.cfg.calendar_click, self.cfg.calendar_clicks)
                months = sweep_by_clicking(page.final_url, self.cfg.calendar_click,
                                           self.cfg.calendar_clicks, self.cfg.user_agent, self.cfg.timeout)
            else:
                log.info("Calendar found on %s (Modern Events Calendar); loading all months", page.final_url)
                months = sweep_mec(self.session, mec, self._wait, self.cfg.timeout,
                                   parse_month(self.cfg.calendar_from), parse_month(self.cfg.calendar_to),
                                   have=have)
            loaded: List[CachedPage] = []
            failed: List[str] = []
            try:
                for label, html, n_events in months:
                    if html is None:
                        failed.append(label)
                        continue
                    if label in reused:
                        loaded.append(reused[label])
                        continue
                    url = month_url(label)
                    cal = CachedPage(url, url, 200, "text/html", page.depth, time.time(),
                                     f"{_WRAP_HEAD}{html}{_WRAP_TAIL}",
                                     referrer=cal_home, kind="calendar", label=label)
                    self.cache.put(cal)
                    loaded.append(cal)
                    log.info("  calendar %s %s: %d events", kind, label, n_events)
            except ImportError:
                log.warning("--calendar-click needs playwright: pip install playwright && "
                            "playwright install chromium")
            except Exception as exc:  # noqa: BLE001 - keep the months loaded so far
                log.error("Calendar on %s stopped after an error: %s", cal_home, exc)
                failed.append("(stopped)")
            if loaded or failed:
                # A sweep with failed months is saved as incomplete, so the next run with
                # this output folder loads only the missing months (cached ones are reused).
                self.cache.put(CachedPage(index_url, index_url, 200, "application/json", page.depth,
                                          time.time(), json.dumps({"pages": [c.url for c in loaded],
                                                                   "failed": failed,
                                                                   "complete": not failed}),
                                          kind="calendar-index"))
            if failed:
                log.warning("Calendar on %s: %d months could not be loaded (%s). The sweep is "
                            "incomplete; run again with the same output folder to load them.",
                            cal_home, len(failed), ", ".join(failed[:12]) + (" ..." if len(failed) > 12 else ""))
            log.info("Calendar on %s: %d months/views loaded%s", cal_home, len(loaded),
                     f" ({len(reused)} from cache)" if reused else "")
            out += loaded
        return out


PDF_HINT = re.compile(r"\.pdf(?:$|[?#&])", re.I)
FILE_HOSTS = ("drive.google.com", "docs.google.com")


def unwrap_pdf_url(url: str) -> Optional[str]:
    """Turn viewer / preview URLs into the URL of the PDF itself, or None."""
    try:
        p = urlparse(url)
        host = p.netloc.lower()
        qs = parse_qs(p.query)
    except ValueError:
        return None
    # Google Drive: /file/d/<id>/view|preview, open?id=<id>, uc?id=<id>
    if host == "drive.google.com":
        m = re.search(r"/file/d/([\w-]{10,})", p.path)
        fid = m.group(1) if m else (qs.get("id") or [None])[0]
        return f"https://drive.google.com/uc?export=download&id={fid}" if fid else None
    # Google Docs viewer and pdf.js-style viewers: ?url=... / ?file=... / ?pdf=...
    for key in ("url", "file", "pdf", "src", "document"):
        for v in qs.get(key, []):
            if PDF_HINT.search(v) or v.lower().endswith(".pdf"):
                return safe_join(url, v) or None
    # Google Docs / Slides shared as documents: fetch their PDF export.
    if host == "docs.google.com":
        m = re.match(r"/(document|presentation)/d/([\w-]{10,})", p.path)
        if m:
            kind, did = m.groups()
            return f"https://docs.google.com/{kind}/d/{did}/export?format=pdf"
        return None
    if PDF_HINT.search(p.path) or p.path.lower().endswith(".pdf"):
        return url
    return None


def extract_links(html: str, base_url: str) -> Tuple[List[str], List[str]]:
    """Return (page links, PDF URLs).

    PDFs are picked up from ordinary links, from embedded viewers (<iframe>,
    <embed>, <object>), from any attribute holding a .pdf URL (data-* attributes
    used by WordPress PDF plugins), and from Google Drive / Docs / pdf.js
    viewer URLs, which are unwrapped to the file itself.
    """
    soup = BeautifulSoup(html, "lxml")
    base_tag = soup.find("base", href=True)
    if base_tag:
        base_url = safe_join(base_url, base_tag["href"]) or base_url
    pages: List[str] = []
    pdfs: List[str] = []

    def add_pdf(raw: str) -> bool:
        full = safe_join(base_url, raw.strip())
        target = unwrap_pdf_url(full) if full else None
        if target:
            n = normalise_url(target)
            if n:
                pdfs.append(n)
                return True
        return False

    # Paginated archives (<link rel="next">), e.g. /events/page/2/
    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel") or []).lower()
        if "next" in rel or "prev" in rel:
            n = normalise_url(safe_join(base_url, link["href"]))
            if n:
                pages.append(n)

    for tag in soup.find_all(True):
        for attr, val in tag.attrs.items():
            if not isinstance(val, str) or not val.strip():
                continue
            v = val.strip()
            if v.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
                continue
            is_src = attr in ("href", "src", "data") or attr.startswith("data-")
            if is_src and (PDF_HINT.search(v) or any(h in v for h in FILE_HOSTS)
                           or tag.name in ("iframe", "embed", "object")):
                if add_pdf(v):
                    continue
            if tag.name == "a" and attr == "href":
                n = normalise_url(safe_join(base_url, v))
                if n:
                    pages.append(n)
    return pages, pdfs


def pdf_to_text(data: bytes) -> Tuple[str, str, str]:
    """Text of a PDF with pages separated by form feeds, plus metadata title and date."""
    try:
        from pypdf import PdfReader
    except ImportError:
        log.warning("pypdf not installed; PDF skipped (pip install pypdf)")
        return "", "", ""
    import io
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for pg in reader.pages:
            try:
                pages.append(pg.extract_text() or "")
            except Exception:  # noqa: BLE001 - one broken page should not lose the rest
                pages.append("")
        meta = reader.metadata or {}
        title = str(meta.get("/Title") or "").strip()
        if title.lower() in ("untitled", "untitled document", "microsoft word", "document"):
            title = ""
        raw_date = str(meta.get("/CreationDate") or "")
        m = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?", raw_date)
        date = "-".join(x for x in m.groups() if x) if m else ""
        return "\f".join(pages), title, date
    except Exception as exc:  # noqa: BLE001 - pypdf raises many types
        log.warning("PDF parse failed: %s", exc)
        return "", "", ""
