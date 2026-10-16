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
import re
import time
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple
from urllib import robotparser
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, UnicodeDammit

log = logging.getLogger(__name__)

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
    include_pdfs: bool = False
    use_sitemap: bool = False
    render_js: bool = False
    refresh: bool = False


@dataclass
class CachedPage:
    url: str
    final_url: str
    status: int
    content_type: str
    depth: int
    fetched_at: float
    body: str            # HTML, or extracted text for PDFs
    is_pdf: bool = False


def normalise_url(url: str) -> str:
    """Drop fragments and tracking params, lowercase scheme/host, tidy the path."""
    url, _ = urldefrag(url.strip())
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
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


class PageCache:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, url: str) -> Path:
        return self.root / f"{cache_key(url)}.json.gz"

    def get(self, url: str) -> Optional[CachedPage]:
        path = self._path(url)
        if not path.exists():
            return None
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return CachedPage(**json.load(fh))
        except (OSError, ValueError, TypeError) as exc:
            log.warning("Unreadable cache entry for %s (%s); refetching", url, exc)
            return None

    def put(self, page: CachedPage) -> None:
        with gzip.open(self._path(page.url), "wt", encoding="utf-8") as fh:
            json.dump(page.__dict__, fh)

    def __iter__(self) -> Iterator[CachedPage]:
        for path in sorted(self.root.glob("*.json.gz")):
            try:
                with gzip.open(path, "rt", encoding="utf-8") as fh:
                    yield CachedPage(**json.load(fh))
            except (OSError, ValueError, TypeError):
                log.warning("Skipping unreadable cache file %s", path.name)


class Crawler:
    def __init__(self, cfg: CrawlConfig, cache: PageCache):
        self.cfg = cfg
        self.cache = cache
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": cfg.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5",
        })
        self.hosts = {registered_host(urlparse(u).netloc) for u in cfg.start_urls}
        self.include_re = [re.compile(p) for p in cfg.include_patterns]
        self.exclude_re = [re.compile(p) for p in cfg.exclude_patterns]
        self.robots: Dict[str, Optional[robotparser.RobotFileParser]] = {}
        self._last_fetch = 0.0
        self._browser = None
        self._playwright = None

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

    def allowed(self, url: str) -> bool:
        if not self.cfg.respect_robots:
            return True
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self.robots:
            rp = robotparser.RobotFileParser()
            try:
                resp = self.session.get(base + "/robots.txt", timeout=self.cfg.timeout)
                if resp.status_code >= 400:
                    rp = None  # no robots.txt: everything allowed
                else:
                    rp.parse(resp.text.splitlines())
            except requests.RequestException:
                rp = None
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

    def fetch(self, url: str, depth: int) -> Optional[CachedPage]:
        self._wait()
        try:
            resp = self.session.get(url, timeout=self.cfg.timeout, allow_redirects=True)
        except requests.RequestException as exc:
            log.warning("Fetch failed %s: %s", url, exc)
            return None
        ctype = resp.headers.get("Content-Type", "").lower()
        final_url = normalise_url(resp.url) or url
        if resp.status_code >= 400:
            log.info("HTTP %s for %s", resp.status_code, url)
            return CachedPage(url, final_url, resp.status_code, ctype, depth, time.time(), "")

        if "pdf" in ctype or url.lower().endswith(".pdf"):
            if not self.cfg.include_pdfs:
                return None
            return CachedPage(url, final_url, resp.status_code, ctype, depth, time.time(),
                              pdf_to_text(resp.content), is_pdf=True)
        if "html" not in ctype and "xml" not in ctype:
            return None

        if "charset=" in ctype:
            body = resp.text
        else:  # no declared charset: sniff <meta charset> / BOM rather than assume Latin-1
            body = UnicodeDammit(resp.content, is_html=True).unicode_markup or resp.text
        if self.cfg.render_js:
            try:
                body, rendered_url, _ = self._render(url)
                final_url = normalise_url(rendered_url) or final_url
            except Exception as exc:  # noqa: BLE001 - playwright raises many types
                log.warning("JS render failed for %s (%s); using raw HTML", url, exc)
        return CachedPage(url, final_url, resp.status_code, ctype, depth, time.time(), body)

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
            except (requests.RequestException, ET.ParseError, OSError):
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
        queue: deque = deque()
        seen: Set[str] = set()
        for u in self.cfg.start_urls:
            n = normalise_url(u)
            if n:
                queue.append((n, 0))
                seen.add(n)
        if self.cfg.use_sitemap:
            for u in self.sitemap_urls():
                n = normalise_url(u)
                if n and n not in seen and self.in_scope(n):
                    queue.append((n, 1))
                    seen.add(n)

        fetched = 0
        try:
            while queue and fetched < self.cfg.max_pages:
                url, depth = queue.popleft()
                page = None if self.cfg.refresh else self.cache.get(url)
                if page is None:
                    if not self.allowed(url):
                        log.info("robots.txt disallows %s", url)
                        continue
                    page = self.fetch(url, depth)
                    if page is None:
                        continue
                    self.cache.put(page)
                    log.info("[%d/%d] d=%d %s", fetched + 1, self.cfg.max_pages, depth, url)
                if page.status >= 400:
                    continue
                fetched += 1

                if page.is_pdf or not page.body or depth >= self.cfg.max_depth:
                    continue
                for link in extract_links(page.body, page.final_url):
                    if link not in seen and self.in_scope(link):
                        seen.add(link)
                        queue.append((link, depth + 1))
        finally:
            if self._browser is not None:
                self._browser.close()
                self._playwright.stop()
        log.info("Crawl finished: %d pages fetched or reused from cache, %d queued URLs not visited",
                 fetched, len(queue))
        return fetched


def extract_links(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html, "lxml")
    base_tag = soup.find("base", href=True)
    if base_tag:
        base_url = urljoin(base_url, base_tag["href"])
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        n = normalise_url(urljoin(base_url, href))
        if n:
            out.append(n)
    return out


def pdf_to_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader  # optional dependency
    except ImportError:
        log.warning("pypdf not installed; PDF skipped (pip install pypdf)")
        return ""
    import io
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((pg.extract_text() or "") for pg in reader.pages)
    except Exception as exc:  # noqa: BLE001 - pypdf raises many types
        log.warning("PDF parse failed: %s", exc)
        return ""
