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
from urllib.parse import parse_qs, quote, urldefrag, urljoin, urlparse, urlunparse

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
        self._swept: Set[str] = set()

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

    def fetch(self, url: str, depth: int, referrer: str = "",
              expect_pdf: bool = False) -> Optional[CachedPage]:
        self._wait()
        try:
            resp = self.session.get(url, timeout=self.cfg.timeout, allow_redirects=True, stream=True)
        except requests.RequestException as exc:
            log.warning("Fetch failed %s: %s", url, exc)
            return None
        ctype = resp.headers.get("Content-Type", "").lower()
        final_url = normalise_url(resp.url) or url
        if resp.status_code >= 400:
            log.info("HTTP %s for %s", resp.status_code, url)
            resp.close()
            return CachedPage(url, final_url, resp.status_code, ctype, depth, time.time(), "",
                              is_pdf=expect_pdf, referrer=referrer)

        looks_pdf = "pdf" in ctype or urlparse(final_url).path.lower().endswith(".pdf")
        if looks_pdf or expect_pdf:
            data = self._read_capped(resp)
            if data is None or not data.lstrip()[:5].startswith(b"%PDF"):
                if expect_pdf and data is not None:
                    log.info("Not a PDF after all (%s): %s", ctype or "no type", url)
                return None
            text, title, date = pdf_to_text(data)
            if not text.strip():
                log.warning("PDF has no extractable text (scanned image?): %s", url)
            return CachedPage(url, final_url, resp.status_code, ctype, depth, time.time(),
                              text, is_pdf=True, referrer=referrer, title=title, date=date)
        if "html" not in ctype and "xml" not in ctype:
            resp.close()
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
        queue: deque = deque()   # (url, depth, referrer, is_pdf)
        seen: Set[str] = set()
        for u in self.cfg.start_urls:
            n = normalise_url(u)
            if n:
                queue.append((n, 0, "", False))
                seen.add(n)
        if self.cfg.use_sitemap:
            for u in self.sitemap_urls():
                n = normalise_url(u)
                if n and n not in seen and self.in_scope(n):
                    queue.append((n, 1, "", False))
                    seen.add(n)

        fetched = 0
        try:
            while queue and fetched < self.cfg.max_pages:
                url, depth, referrer, is_pdf = queue.popleft()
                page = None if self.cfg.refresh else self.cache.get(url)
                if page is None:
                    if not self.allowed(url):
                        log.info("robots.txt disallows %s", url)
                        continue
                    page = self.fetch(url, depth, referrer, expect_pdf=is_pdf)
                    if page is None:
                        continue
                    self.cache.put(page)
                    log.info("[%d/%d] d=%d %s", fetched + 1, self.cfg.max_pages, depth, url)
                if page.status >= 400:
                    continue
                fetched += 1

                if page.is_pdf or not page.body:
                    continue
                for cal in self.calendar_pages(page):
                    fetched += 1
                    self._enqueue_links(cal, depth, queue, seen)
                self._enqueue_links(page, depth, queue, seen)
        finally:
            if self._browser is not None:
                self._browser.close()
                self._playwright.stop()
        log.info("Crawl finished: %d pages fetched or reused from cache, %d queued URLs not visited",
                 fetched, len(queue))
        return fetched

    def _enqueue_links(self, page: CachedPage, depth: int, queue: deque, seen: Set[str]) -> None:
        links, pdfs = extract_links(page.body, page.final_url)
        # PDFs on a page are part of that page, so they are read even at max depth.
        for link in pdfs:
            if link not in seen and self.pdf_in_scope(link):
                seen.add(link)
                queue.appendleft((link, depth + 1, page.final_url, True))
        if depth >= self.cfg.max_depth:
            return
        for link in links:
            if link not in seen and self.in_scope(link):
                seen.add(link)
                queue.append((link, depth + 1, "", False))

    # --------------------------------------------------------------- calendar
    def calendar_pages(self, page: CachedPage) -> List[CachedPage]:
        """Load every month of a calendar embedded in `page` (once per page)."""
        if not self.cfg.calendar or page.kind or page.final_url in self._swept:
            return []
        if self.cfg.calendar_url_pattern and not re.search(self.cfg.calendar_url_pattern, page.final_url):
            return []
        # The index remembers a sweep for this exact range/method, so a narrow test
        # run is not mistaken for a full sweep when the output folder is reused.
        scope = f"{self.cfg.calendar_from or ''}_{self.cfg.calendar_to or ''}_{self.cfg.calendar_click or ''}"
        index_url = (page.final_url + ("&" if "?" in page.final_url else "?")
                     + "calendar_index=" + quote(scope, safe=""))
        if not self.cfg.refresh:
            index = self.cache.get(index_url)
            if index is not None:
                self._swept.add(page.final_url)
                return [p for u in json.loads(index.body or "[]") if (p := self.cache.get(u))]

        from calendar_sweep import detect_mec, parse_month, sweep_by_clicking, sweep_mec
        sweeps = []
        if self.cfg.calendar_click:
            if re.search(r"mec-|calendar|fc-|tribe-events", page.body, re.I) or self.cfg.calendar_url_pattern:
                sweeps.append(("click", sweep_by_clicking(page.final_url, self.cfg.calendar_click,
                                                          self.cfg.calendar_clicks, self.cfg.user_agent,
                                                          self.cfg.timeout)))
        else:
            for mec in detect_mec(page.body, page.final_url):
                log.info("Calendar found on %s (Modern Events Calendar); loading all months", page.final_url)
                sweeps.append(("month", sweep_mec(self.session, mec, self._wait, self.cfg.timeout,
                                                  parse_month(self.cfg.calendar_from),
                                                  parse_month(self.cfg.calendar_to))))
        if not sweeps:
            return []
        self._swept.add(page.final_url)
        out: List[CachedPage] = []
        sep = "&" if "?" in page.final_url else "?"
        for kind, months in sweeps:
            try:
                for label, html, n_events in months:
                    url = f"{page.final_url}{sep}calendar_{kind}={label}"
                    cal = CachedPage(url, url, 200, "text/html", page.depth, time.time(),
                                     f"<html><body><main>{html}</main></body></html>",
                                     referrer=page.final_url, kind="calendar", label=label)
                    self.cache.put(cal)
                    out.append(cal)
                    log.info("  calendar %s %s: %d events", kind, label, n_events)
            except ImportError:
                log.warning("--calendar-click needs playwright: pip install playwright && "
                            "playwright install chromium")
        if out:  # an index is only saved for a sweep that worked, so failures are retried
            self.cache.put(CachedPage(index_url, index_url, 200, "application/json", page.depth,
                                      time.time(), json.dumps([c.url for c in out]),
                                      kind="calendar-index"))
        log.info("Calendar on %s: %d months/views loaded", page.final_url, len(out))
        return out


PDF_HINT = re.compile(r"\.pdf(?:$|[?#&])", re.I)
FILE_HOSTS = ("drive.google.com", "docs.google.com")


def unwrap_pdf_url(url: str) -> Optional[str]:
    """Turn viewer / preview URLs into the URL of the PDF itself, or None."""
    p = urlparse(url)
    host = p.netloc.lower()
    qs = parse_qs(p.query)
    # Google Drive: /file/d/<id>/view|preview, open?id=<id>, uc?id=<id>
    if host == "drive.google.com":
        m = re.search(r"/file/d/([\w-]{10,})", p.path)
        fid = m.group(1) if m else (qs.get("id") or [None])[0]
        return f"https://drive.google.com/uc?export=download&id={fid}" if fid else None
    # Google Docs viewer and pdf.js-style viewers: ?url=... / ?file=... / ?pdf=...
    for key in ("url", "file", "pdf", "src", "document"):
        for v in qs.get(key, []):
            if PDF_HINT.search(v) or v.lower().endswith(".pdf"):
                return urljoin(url, v)
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
        base_url = urljoin(base_url, base_tag["href"])
    pages: List[str] = []
    pdfs: List[str] = []

    def add_pdf(raw: str) -> bool:
        full = urljoin(base_url, raw.strip())
        target = unwrap_pdf_url(full)
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
            n = normalise_url(urljoin(base_url, link["href"]))
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
                n = normalise_url(urljoin(base_url, v))
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
