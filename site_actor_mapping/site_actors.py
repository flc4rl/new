#!/usr/bin/env python3
"""
Crawl a website, map key themes, and extract every collective, group, NGO and
other organisation it mentions, with the surrounding text of each mention for
qualitative assessment.

Usage
-----
    python site_actors.py https://example.org \\
        --themes example_themes.txt \\
        --watchlist example_watchlist.txt \\
        --output-dir results/example_org \\
        --max-pages 300

Re-run the extraction on the cached pages with different settings, without
crawling again:

    python site_actors.py https://example.org --output-dir results/example_org \\
        --no-crawl --themes my_themes.txt

See README.md for the full CLI and output description.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gc
import hashlib
import inspect
import json
import time
import logging
import platform
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse

from crawler import CachedPage, CrawlConfig, Crawler, PageCache, normalise_url, registered_host
from extract import (
    Block, OrgExtractor, ParsedPage, detect_language, ThemeSet, Watchlist, acronym_pairs, block_hash,
    canonical_key, clean_ws, load_stoplist, parse_html, parse_pdf_text, split_sentences,
)
from events import EventIndex, EventRef
from linked_sites import (PLATFORM_HOSTS, Site, SiteRegistry, Via, collect_targets, crawl_linked,
                          filter_targets, host_matches)
from make_master import THEME_FILTERS, build_masters
from seed_pdfs import PdfLink, entries_to_json, extract_entries, pdf_display, pdf_name
from report import write_report

log = logging.getLogger("site_actors")
HERE = Path(__file__).resolve().parent
FURNITURE_REGIONS = ("nav", "header", "footer", "aside")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("start_urls", nargs="*", help="Start URL(s) of the website to crawl (optional "
                                                  "when only --seed-pdf is given)")
    ap.add_argument("--seed-pdf", action="append", default=[], metavar="PDF",
                    help="A PDF (file path or URL) listing links to check, e.g. an activist agenda: "
                         "its links are crawled and analysed like linked sites (repeatable). With "
                         "--no-crawl a local PDF is read again (no web access needed)")
    ap.add_argument("--output-dir", "-o", type=Path, required=True)

    g = ap.add_argument_group("crawling")
    g.add_argument("--max-pages", type=int, default=500)
    g.add_argument("--max-depth", type=int, default=5)
    g.add_argument("--delay", type=float, default=1.0, help="Seconds between requests")
    g.add_argument("--timeout", type=float, default=20.0)
    g.add_argument("--user-agent", default="site-actor-mapping/1.0 (academic research crawler)")
    g.add_argument("--ignore-robots", action="store_true", help="Do not honour robots.txt")
    g.add_argument("--include-subdomains", action="store_true")
    g.add_argument("--include", action="append", default=[], metavar="REGEX",
                   help="Only crawl URLs matching this regex (repeatable)")
    g.add_argument("--exclude", action="append", default=[], metavar="REGEX",
                   help="Skip URLs matching this regex (repeatable), e.g. '/tag/' '/shop/'")
    g.add_argument("--no-pdfs", action="store_true",
                   help="Skip PDFs (by default linked and embedded PDFs are read, wherever hosted)")
    g.add_argument("--pdfs-same-site-only", action="store_true",
                   help="Only read PDFs hosted on the site itself, not on Drive, CDNs etc.")
    g.add_argument("--max-pdf-mb", type=float, default=50.0, help="Skip PDFs larger than this")
    g.add_argument("--include-pdfs", action="store_true", help=argparse.SUPPRESS)  # now the default
    g.add_argument("--use-sitemap", action="store_true", help="Seed the crawl from sitemap.xml")
    g.add_argument("--render-js", action="store_true",
                   help="Render pages in headless Chromium (needs playwright) for JS-heavy sites")
    g.add_argument("--refresh", action="store_true", help="Refetch pages already in the cache")
    g.add_argument("--no-crawl", action="store_true", help="Only analyse pages already cached")

    g = ap.add_argument_group("calendars")
    g.add_argument("--no-calendar", action="store_true",
                   help="Do not load the other months of embedded calendars")
    g.add_argument("--calendar-from", metavar="YYYY-MM",
                   help="Earliest calendar month (default: go back until 12 empty months in a row)")
    g.add_argument("--calendar-to", metavar="YYYY-MM",
                   help="Latest calendar month (default: go forward until 3 empty months in a row)")
    g.add_argument("--calendar-click", metavar="CSS",
                   help="For calendars not detected automatically: CSS selector of the 'previous "
                        "month' button to click in headless Chromium (needs playwright)")
    g.add_argument("--calendar-clicks", type=int, default=24, help="Clicks for --calendar-click")
    g.add_argument("--calendar-url", metavar="REGEX",
                   help="Only treat pages whose URL matches as calendars, e.g. '/events/?$'")

    g = ap.add_argument_group("linked websites")
    g.add_argument("--follow-links", choices=["none", "events", "all"], default="none",
                   help="Also crawl websites linked from calendar events ('events') or from any "
                        "page's main content ('all'), and analyse them too")
    g.add_argument("--follow-max-sites", type=int, default=60, help="At most this many linked sites")
    g.add_argument("--follow-max-pages", type=int, default=25, help="Pages per linked site")
    g.add_argument("--follow-max-depth", type=int, default=1,
                   help="Clicks from the linked page within each linked site")
    g.add_argument("--follow-skip", action="append", default=[], metavar="HOST",
                   help="Never follow links to this host (repeatable)")
    g.add_argument("--follow-single-page", action="append", default=[], metavar="HOST",
                   help="Read only the linked page on this host, never crawl it (repeatable)")
    g.add_argument("--follow-workers", type=int, default=8,
                   help="Linked websites crawled at the same time (each host still gets one "
                        "request at a time with --delay)")

    g = ap.add_argument_group("extraction")
    g.add_argument("--themes", type=Path, default=HERE / "example_themes.txt",
                   help="Theme file ([theme] headers followed by terms)")
    g.add_argument("--watchlist", type=Path, help="Known organisations: 'Name | alias | alias' per line")
    g.add_argument("--stoplist", type=Path, default=HERE / "default_stoplist.txt",
                   help="Names to ignore (one per line, 're:' prefix for regex)")
    g.add_argument("--spacy-model", default="auto",
                   help="'auto' picks a spaCy model per page language (en/fr/it/de/es/pt/nl...); "
                        "or name one model for all pages, e.g. fr_core_news_sm; or 'none'")
    g.add_argument("--ner-size", choices=["sm", "md", "lg"], default="sm",
                   help="spaCy model size in auto mode: md/lg are more precise (fewer places and "
                        "people mistaken for groups) but miss more unusual group names; install "
                        "them first, e.g. python -m spacy download en_core_web_md")
    g.add_argument("--no-links", action="store_true",
                   help="Do not treat outbound-link text / logo alt text as organisation names")
    g.add_argument("--strip-regions", action="store_true",
                   help="Discard nav/header/footer/aside before extraction (default: keep, but flag)")
    g.add_argument("--context-sentences", type=int, default=1,
                   help="Sentences either side of the mention to keep as context")
    g.add_argument("--theme-scope", choices=["any", "page", "paragraph", "sentence"], default="any",
                   help="Keep only mentions whose page / paragraph / context window hits a theme")
    g.add_argument("--focus-themes", default="",
                   help="Comma-separated theme names that count for --theme-scope and --theme-filter "
                        "(default: all)")
    g.add_argument("--boilerplate-share", type=float, default=0.2,
                   help="Paragraphs repeated on more than this share of pages are flagged boilerplate")
    g.add_argument("--min-mentions", type=int, default=1,
                   help="Drop organisations with fewer distinct mentions from organisations.csv/report")

    g = ap.add_argument_group("master files")
    g.add_argument("--theme-filter", choices=THEME_FILTERS, default="source",
                   help="Inclusion criterion for master_network: keep organisations and events only "
                        "from sources with a theme hit ('source': the web page, PDF or agenda entry "
                        "itself; 'site': any page of the same website; 'event': any source tied to "
                        "the same event; 'none': keep everything). Sources left out are listed in "
                        "the excluded_sources sheet")
    g.add_argument("--keep-menu-themes", action="store_true",
                   help="Keep theme terms found in menus, sidebars, footers, repeated site furniture "
                        "and short labels in master_themes (default: listed in a separate sheet)")
    g.add_argument("--edition-policy", choices=["nearest", "all"], default="nearest",
                   help="A linked page reached from several dated events (editions of a recurring "
                        "event): tie it to the edition nearest its page date, else the latest one "
                        "('nearest'; the others are listed in recurring_event_editions), or to all "
                        "of them ('all', the behaviour of earlier versions)")
    ap.add_argument("--verbose", "-v", action="store_true")
    return ap.parse_args(argv)


# --------------------------------------------------------------------------
# Run record: run.log and run_info.json in the output folder
# --------------------------------------------------------------------------
def setup_logging(out: Path, verbose: bool) -> None:
    """Log to the terminal and to <output-dir>/run.log (appended, with full dates)."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_site_actors", False):
            root.removeHandler(h)
            h.close()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    term = logging.StreamHandler()
    term.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    handlers = [term]
    try:
        fileh = logging.FileHandler(out / "run.log", mode="a", encoding="utf-8")
        fileh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s",
                                             "%Y-%m-%d %H:%M:%S"))
        handlers.append(fileh)
    except OSError as exc:
        print(f"Could not write {out / 'run.log'}: {exc}", file=sys.stderr)
    for h in handlers:
        h._site_actors = True   # type: ignore[attr-defined]
        root.addHandler(h)
    for noisy in ("pdfminer", "pdfplumber", "pypdf"):   # harmless font warnings, thousands of them
        logging.getLogger(noisy).setLevel(logging.ERROR)


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def file_sha256(path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def package_versions() -> Dict[str, str]:
    from importlib import metadata
    out = {}
    for pkg in ("spacy", "pypdf", "pdfplumber", "requests", "openpyxl", "beautifulsoup4", "lxml",
                "playwright", "networkx", "matplotlib"):
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            out[pkg] = ""
    return out


def input_files(args: argparse.Namespace) -> Dict[str, object]:
    def rec(p) -> dict:
        p = Path(p).expanduser() if p else None
        if p is None:
            return {}
        return {"path": str(p), "name": p.name, "sha256": file_sha256(p) if p.exists() else "missing"}
    files: Dict[str, object] = {"themes": rec(args.themes), "watchlist": rec(args.watchlist),
                                "stoplist": rec(args.stoplist)}
    files["seed_pdfs"] = [rec(s) if not re.match(r"https?://", s) else {"url": s} for s in args.seed_pdf]
    return files


def write_run_info(out: Path, info: dict) -> None:
    """run_info.json: how this output folder was made (written at the start and again at the end)."""
    try:
        tmp = out / "run_info.json.tmp"
        tmp.write_text(json.dumps(info, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(out / "run_info.json")
    except OSError as exc:
        log.warning("Could not write run_info.json: %s", exc)


def fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"


class Progress:
    """Progress lines with elapsed time and an estimate of the time left: every
    `every` items or every `seconds`, whichever comes first."""

    def __init__(self, label: str, total: int, every: Optional[int] = None, seconds: float = 60.0):
        self.label, self.total = label, max(1, total)
        self.every = every or max(10, self.total // 50)
        self.seconds = seconds
        self.t0 = self.last = time.monotonic()

    def tick(self, n: int) -> None:
        now = time.monotonic()
        if n % self.every and now - self.last < self.seconds and n != self.total:
            return
        self.last = now
        el = now - self.t0
        left = el / n * (self.total - n) if n else 0
        log.info("  %s %d/%d (%d%%), %s elapsed, about %s left", self.label, n, self.total,
                 100 * n // self.total, fmt_duration(el), fmt_duration(left))


# --------------------------------------------------------------------------
# Reading cached pages
# --------------------------------------------------------------------------
def seed_blocks(body: str, line_entry: Dict[str, List[int]]) -> Optional[List[Block]]:
    """Blocks of a seed PDF: one per agenda entry (its wrapped lines joined), plus one
    per run of lines outside any entry (headings). Each block carries `entry`, the
    entry's index (-1 outside entries). None if the saved line map does not fit."""
    rows: List[Tuple[int, str, int]] = []      # (page, line, owner)
    for pno, ptxt in enumerate(body.split("\f"), 1):
        lines = [ln for ln in ptxt.split("\n") if ln.strip()]
        owners = line_entry.get(str(pno), [])
        if len(owners) != len(lines):
            return None
        rows += [(pno, ln, o) for ln, o in zip(lines, owners)]
    blocks: List[Block] = []
    cur: List[str] = []
    own, first_page, last_page = None, 0, 0

    def flush() -> None:
        if not cur:
            return
        txt = ""
        for ln in cur:
            ln = ln.strip()
            # A line ending in a hyphen right after a letter continues the word or URL
            # on the next line ("néo-" + "coloniales", "grande-" + "mobilisation"); the
            # hyphen is kept, as almost all such wraps are compounds or URLs.
            if txt.endswith("-") and len(txt) > 1 and not txt[-2].isspace():
                txt += ln
            else:
                txt = f"{txt} {ln}" if txt else ln
        b = Block(text=clean_ws(txt), region=f"pdf p.{first_page}")
        b.entry = own if own is not None else -1          # type: ignore[attr-defined]
        blocks.append(b)

    for pno, ln, o in rows:
        if cur and (o != own or (o < 0 and pno != last_page)):
            flush()
            cur = []
        if not cur:
            first_page = pno
        cur.append(ln)
        own, last_page = o, pno
    flush()
    return blocks


def load_pages(cache_pages: Iterable[CachedPage], registry: SiteRegistry, strip_regions: bool,
               seed_pdfs: Dict[str, dict], in_window=None, total: int = 0,
               pages: Optional[Dict[str, ParsedPage]] = None,
               pdf_rows: Optional[List[dict]] = None) -> dict:
    """Parse cached pages that belong to a registered site, plus PDFs found on
    them (wherever hosted), tagging each with its site.

    Returns {"pages": {url: ParsedPage}, "pdf_rows": [...], "meta": {url: (final_url,
    is_pdf, kind, referrer)}, "orphans": [url, ...], "moved": {host: Site}}: the
    inventory of every PDF seen (also those that could not be read, so gaps in
    coverage are visible), light metadata of every cached page (bodies are not
    kept), cached pages of no known site, and linked sites whose start page
    redirected to a host the registry does not know yet.
    """
    pages = {} if pages is None else pages
    pdf_rows = [] if pdf_rows is None else pdf_rows
    meta: Dict[str, tuple] = {}
    orphans: List[str] = []
    moved: Dict[str, Site] = {}
    prog = Progress("cached pages read", total, every=500) if total else None

    def tag(page: ParsedPage, site, cp) -> ParsedPage:
        page.site, page.site_role = site.key, site.role
        page.via_event = site.via_events(url=cp.url) if site.role == "linked" else ""
        page.kind, page.label = cp.kind, cp.label
        page.fetched_at = cp.fetched_at                     # type: ignore[attr-defined]
        page.requested_urls = {cp.url, cp.final_url}        # type: ignore[attr-defined]
        if cp.kind == "calendar":
            page.title = f"Calendar {cp.label}"
        return page

    for n, cp in enumerate(cache_pages, 1):
        if prog:
            prog.tick(n)
        if cp.kind == "calendar-index":
            continue
        if in_window is not None and not in_window(cp):
            continue        # a calendar month outside --calendar-from/--calendar-to
        meta[cp.url] = (cp.final_url, cp.is_pdf, cp.kind, cp.referrer)
        if cp.is_pdf:
            site = (registry.site_of(cp.url) or registry.site_of(cp.final_url)
                    or (registry.site_of(cp.referrer) if cp.referrer else None))
            if site is None:
                orphans.append(cp.url)
                continue
            n_pages = cp.body.count("\f") + 1 if cp.body else 0
            words = len(cp.body.split())
            status = ("http error" if cp.status >= 400 else
                      "no text layer (scanned?)" if not words else "read")
            pdf_rows.append({"pdf_url": cp.url, "embedded_in": cp.referrer, "site": site.key,
                             "title": cp.title, "date": cp.date, "pages": n_pages, "words": words,
                             "status": f"{status} ({cp.status})" if cp.status >= 400 else status})
            if status != "read" or cp.url in pages:
                continue
            try:
                blocks = None
                line_entry = (seed_pdfs.get(cp.url) or {}).get("line_entry") if cp.kind == "seed" else None
                if line_entry:
                    blocks = seed_blocks(cp.body, line_entry)
                    if blocks is None:
                        log.warning("Seed PDF %s: saved line map does not match the cached text; "
                                    "reading it line by line", pdf_display(cp.url))
                if blocks is not None:
                    page = ParsedPage(url=cp.url, title=cp.title or pdf_name(cp.url), date=cp.date,
                                      depth=cp.depth, blocks=blocks, is_pdf=True, referrer=cp.referrer)
                else:
                    body = cp.body
                    if cp.kind == "seed":   # a link list: every line is its own paragraph
                        body = re.sub(r"\n+", "\n\n", body)
                    page = parse_pdf_text(body, cp.url, cp.depth, cp.title, cp.date, cp.referrer)
                if cp.kind == "seed":
                    page.title = pdf_name(cp.url)
                pages[cp.url] = tag(page, site, cp)
            except Exception as exc:  # noqa: BLE001 - one bad file should not stop the run
                log.warning("Could not parse PDF %s: %s", cp.url, exc)
            continue
        if cp.status >= 400 or not cp.body:
            continue
        site = registry.site_of(cp.url) or registry.site_of(cp.final_url)
        if site is None:
            orphans.append(cp.url)
            continue
        if (site.role == "linked" and not site.platform and cp.depth == 0
                and registry.site_of(cp.final_url) is None):
            host = registered_host(urlparse(cp.final_url).netloc)
            if host and not host_matches(host, PLATFORM_HOSTS):
                moved.setdefault(host, site)     # a linked site that moved to another domain
        key = cp.final_url
        if key in pages:          # redirects: several requested URLs, one final page
            pages[key].requested_urls.add(cp.url)
            continue
        try:
            pages[key] = tag(parse_html(cp.body, cp.final_url, cp.depth, strip_regions), site, cp)
            pages[key].requested_url = cp.url
        except Exception as exc:  # noqa: BLE001 - one bad page should not stop the run
            log.warning("Could not parse %s: %s", cp.final_url, exc)
    return {"pages": pages, "pdf_rows": pdf_rows, "meta": meta, "orphans": orphans, "moved": moved}


def window(sents, idx: int, n: int, text: str) -> str:
    lo, hi = max(0, idx - n), min(len(sents) - 1, idx + n)
    return text[sents[lo][0]:sents[hi][1]]


def themes_str(hits: Dict[str, object]) -> str:
    """'theme (n); theme (n)' from either {theme: [hits]} or {theme: count}."""
    return "; ".join(f"{t} ({v if isinstance(v, int) else len(v)})" for t, v in sorted(hits.items()))


def _join(values: Iterable[str]) -> str:
    """Several events in one cell: " | " separated, with any " | " inside a value
    replaced, so the cell can always be split again."""
    return " | ".join((v or "").replace(" | ", " / ").replace("|", "/") for v in values)


_ISO_DAY = re.compile(r"\d{4}-\d{2}(?:-\d{2})?")


def _date_span(text: str) -> Tuple[Optional[dt.date], Optional[dt.date]]:
    """First and last date in an event date ("2023-06-01 to 2023-06-06", "2025-07")."""
    found = _ISO_DAY.findall(text or "")
    if not found:
        return None, None

    def day(s: str, last: bool) -> Optional[dt.date]:
        try:
            if len(s) == 7:
                y, m = int(s[:4]), int(s[5:7])
                if not last:
                    return dt.date(y, m, 1)
                return dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)
            return dt.date.fromisoformat(s)
        except ValueError:
            return None
    return day(found[0], False), day(found[-1], True)


_URL_YM = re.compile(r"/((?:19|20)\d{2})/(0[1-9]|1[0-2])/")


def page_date_of(page) -> Tuple[str, str]:
    """(date, where from): the page's own date (metadata, <time>), else a year and
    month in its URL ("/wp-content/uploads/2022/07/"), else ("", "")."""
    if page.date and _ISO_DAY.match(page.date):
        return page.date[:10], "page"
    m = _URL_YM.search(page.url)
    if m:
        return f"{m.group(1)}-{m.group(2)}", "url"
    return "", ""


def timing(page, ref: EventRef, layer: str) -> Tuple[str, str]:
    """(content_vs_event, years_after_event) for a page tied to an event.

    'listing' for the agenda entry, calendar cell or the event's own page on the
    main site; 'contemporaneous' when
    the page's date is within about six months of the event; 'current_site_content'
    when the page (or, without a date, the crawl) is more than six months after the
    event's end; 'unknown' otherwise (no event date, or a page dated well before).
    """
    if layer.startswith("listing") or layer == "main_event_page":
        return "listing", ""
    start, end = _date_span(ref.date)
    if start is None:
        return "unknown", ""
    pdate, _ = page_date_of(page)
    when = _date_span(pdate)[0] if pdate else None
    fetched = getattr(page, "fetched_at", 0) or 0
    if when is None and fetched:
        when = dt.date.fromtimestamp(fetched)
    if when is None:
        return "unknown", ""
    years = str(when.year - start.year)
    margin = dt.timedelta(days=183)
    if pdate and start - margin <= when <= (end or start) + margin:
        return "contemporaneous", years
    if when > (end or start) + margin:
        return "current_site_content", years
    return "unknown", years


def _norm_link(u: str) -> str:
    u = (u or "").split("#", 1)[0]
    u = normalise_url(u) or u
    return re.sub(r"^https?://(?:www\.)?", "", u).rstrip("/")


def planned_sites(targets: Dict[str, List[Via]], platform_hosts: Set[str], status: str) -> List[Site]:
    """The linked sites crawl_linked would create for `targets` (same keys), with
    `status`: saved before the linked crawl starts, so an interrupted run still
    knows which cached pages belong to which site and event."""
    by_host: Dict[str, List[str]] = defaultdict(list)
    for url in targets:
        by_host[registered_host(urlparse(url).netloc)].append(url)
    sites: List[Site] = []
    for host in sorted(by_host):
        urls = sorted(by_host[host])
        if host_matches(host, platform_hosts):
            sites += [Site(f"{host}{urlparse(u).path}".rstrip("/"), "linked", [u], True, targets[u],
                           status=status) for u in urls]
        else:
            sites.append(Site(host, "linked", urls, False, [v for u in urls for v in targets[u]],
                              status=status))
    return sites


def build_targets(cache: PageCache, hosts: Set[str], mode: str, skip: Set[str],
                  seed_links: List[PdfLink], seed_pdfs: Dict[str, dict],
                  entry_keys: Dict[tuple, str], args: argparse.Namespace) -> Dict[str, List[Via]]:
    """External URLs to follow: links of the main site (by --follow-links) and the
    links of every agenda entry in the seed PDFs, each with the event(s) it came from."""
    targets: Dict[str, List[Via]] = {}
    if hosts and mode != "none":
        targets = collect_targets(cache, hosts, mode, skip, args.calendar_from, args.calendar_to)
    seed_targets: Dict[str, List[Via]] = defaultdict(list)
    for ln in seed_links:
        # Links outside any agenda entry (introductions, "other calendars" lists)
        # are not events and are not followed.
        if seed_pdfs.get(ln.pdf) and ln.entry < 0:
            continue
        if registered_host(urlparse(ln.url).netloc) not in hosts:
            seed_targets[ln.url].append(Via(page=ln.pdf, event=ln.event, target=ln.url,
                                            event_url=entry_keys.get((ln.pdf, ln.entry), ln.url),
                                            date=ln.date_iso or ln.date_raw))
    for url, vias in filter_targets(seed_targets, skip).items():
        targets.setdefault(url, []).extend(vias)
    return targets


def _name_tokens(name: str) -> Set[str]:
    return {t for t in canonical_key(name).split() if len(t) > 2}


def assign_entry_keys(seed_pdfs: Dict[str, dict], skip_hosts: Set[str]
                      ) -> Tuple[Dict[tuple, str], Dict[tuple, str]]:
    """One event key per agenda entry.

    An entry with a followable link is keyed by link + date ("https://x.org/#2025-07-18"),
    so the same event listed twice (in French and English, or in both agendas) is one
    event, while editions of a recurring event that share a link stay separate. Two
    listings with the same link and start but different end dates stay separate too.
    An entry without a followable link gets its own key ("<pdf>#entry-12"); it is
    merged only with an earlier link-less entry of the same PDF with the same dates
    and a similar name (a repeated listing). Returns (keys, links shown)."""
    entry_keys: Dict[tuple, str] = {}
    entry_links: Dict[tuple, str] = {}
    first_span: Dict[str, Tuple[str, str]] = {}
    linkless: Dict[str, List[Tuple[str, str, Set[str], str]]] = defaultdict(list)
    for pdf_url, d in seed_pdfs.items():
        for e in d["entries"]:
            idx = (pdf_url, e["index"])
            good = [u for u in e["links"] if filter_targets({u: []}, skip_hosts)]
            # Social media links are not followed, but still shown as the event's link.
            entry_links[idx] = good[0] if good else (e["links"][0] if e["links"] else "")
            start, end = e.get("start") or "", e.get("end") or ""
            if good:
                when = start or e.get("month") or f"entry-{e['index'] + 1}"
                key = f"{good[0]}#{when}"
                prev = first_span.setdefault(key, (start, end))
                if prev[1] and end and prev[1] != end:
                    key = f"{key}#{pdf_name(pdf_url)}-entry-{e['index'] + 1}"
            else:
                key = f"{pdf_url}#entry-{e['index'] + 1}"
                toks = _name_tokens(e.get("name", ""))
                for s0, e0, t0, k0 in linkless[pdf_url]:
                    if start and (s0, e0) == (start, end) and toks and t0 \
                            and len(toks & t0) / len(toks | t0) >= 0.5:
                        key = k0
                        break
                else:
                    linkless[pdf_url].append((start, end, toks, key))
            entry_keys[idx] = key
    return entry_keys, entry_links


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    setup_logging(out, args.verbose)
    info = {"tool": "site_actors.py", "started_at": now_iso(), "status": "running",
            "command": sys.argv if argv is None else ["site_actors.py"] + list(argv),
            "cwd": str(Path.cwd()), "arguments": {k: str(v) if isinstance(v, Path) else v
                                                   for k, v in vars(args).items()},
            "python": sys.version.split()[0], "platform": platform.platform(),
            "packages": package_versions(), "input_files": input_files(args)}
    write_run_info(out, info)
    t0 = time.monotonic()
    try:
        rc = run(args, out, info)
        info["status"] = "finished" if rc == 0 else f"stopped (exit code {rc})"
        return rc
    except KeyboardInterrupt:
        info["status"] = "interrupted"
        log.warning("Interrupted. Cached pages are kept: run the same command again to resume.")
        raise
    except BaseException as exc:
        info["status"] = f"failed: {type(exc).__name__}: {exc}"
        raise
    finally:
        info["finished_at"] = now_iso()
        info["duration"] = fmt_duration(time.monotonic() - t0)
        write_run_info(out, info)


def run(args: argparse.Namespace, out: Path, info: dict) -> int:
    from calendar_sweep import month_window, parse_month
    try:
        parse_month(args.calendar_from)
        parse_month(args.calendar_to)
    except ValueError as exc:
        log.error("--calendar-from / --calendar-to: %s (e.g. 2024-01)", exc)
        return 2
    cache = PageCache(out / "cache")
    start_urls = [normalise_url(u if "://" in u else "https://" + u) for u in args.start_urls]

    base_cfg = CrawlConfig(
        start_urls=start_urls, max_pages=args.max_pages, max_depth=args.max_depth,
        delay=args.delay, timeout=args.timeout, user_agent=args.user_agent,
        respect_robots=not args.ignore_robots, include_subdomains=args.include_subdomains,
        include_patterns=args.include, exclude_patterns=args.exclude,
        include_pdfs=not args.no_pdfs, pdfs_from_other_hosts=not args.pdfs_same_site_only,
        max_pdf_mb=args.max_pdf_mb, use_sitemap=args.use_sitemap,
        render_js=args.render_js, refresh=args.refresh,
        calendar=not args.no_calendar, calendar_from=args.calendar_from,
        calendar_to=args.calendar_to, calendar_click=args.calendar_click,
        calendar_clicks=args.calendar_clicks, calendar_url_pattern=args.calendar_url,
    )
    seed_file = out / "seed_links.json"
    if not start_urls and not args.seed_pdf and not (args.no_crawl and seed_file.exists()):
        log.error("Give a start URL, a --seed-pdf, or both")
        return 2
    if args.no_crawl:
        defaults = parse_args(["-o", str(out)])
        changed = [f"--{k.replace('_', '-')}" for k in ("follow_links", "follow_max_sites",
                                                        "follow_max_pages", "follow_max_depth",
                                                        "follow_skip", "follow_single_page")
                   if getattr(args, k) != getattr(defaults, k)]
        if changed:
            log.warning("%s: no effect with --no-crawl (no website is crawled; the sites in "
                        "sites.json are analysed as they were cached)", ", ".join(changed))
    if start_urls and not args.no_crawl:
        Crawler(base_cfg, cache).crawl()

    hosts = {registered_host(urlparse(u).netloc) for u in start_urls}
    main_sites = [Site(h, "main", [u for u in start_urls if registered_host(urlparse(u).netloc) == h])
                  for h in sorted(hosts)]

    # Seed PDFs: read their entries and links (and keep their text for analysis).
    seed_links: List[PdfLink] = []
    seed_pdfs: Dict[str, dict] = {}     # pdf URI -> {"entries": [...], "line_entry": {page: [...]}}
    stored_links: List[PdfLink] = []
    stored_pdfs: Dict[str, dict] = {}
    if seed_file.exists():
        data = json.loads(seed_file.read_text(encoding="utf-8"))
        if isinstance(data, list):            # older runs: links only
            data = {"links": data, "pdfs": {}}
        stored_links = [PdfLink(**d) for d in data["links"]]
        stored_pdfs = data.get("pdfs", {})
    read_now: Dict[str, Tuple[list, dict]] = {}
    for src in args.seed_pdf:
        is_url = bool(re.match(r"https?://", src))
        pdf_url = src if is_url else Path(src).expanduser().resolve().as_uri()
        if args.no_crawl and is_url:
            if pdf_url not in stored_pdfs and not any(ln.pdf == pdf_url for ln in stored_links):
                log.warning("Seed PDF %s is not in seed_links.json and is not downloaded with "
                            "--no-crawl", src)
            continue
        try:
            entries, links, page_lines, line_entry = extract_entries(src)
        except Exception as exc:  # noqa: BLE001 - report and carry on with the others
            log.error("Could not read seed PDF %s: %s", src, exc)
            continue
        for ln in links:
            ln.pdf = pdf_url
        for e in entries:
            e.pdf = pdf_url
        read_now[pdf_url] = (links, entries_to_json(entries, line_entry))
        text = "\f".join("\n".join(lines) for lines in page_lines)
        cache.put(CachedPage(pdf_url, pdf_url, 200, "application/pdf", 0, time.time(), text,
                             is_pdf=True, title=pdf_name(pdf_url), kind="seed"))
    if args.seed_pdf and not args.no_crawl:
        stored_links, stored_pdfs = [], {}    # a crawl uses exactly the seed PDFs given
    for ln in stored_links:
        if ln.pdf not in read_now:
            seed_links.append(ln)
    for pdf_url, d in stored_pdfs.items():
        if pdf_url not in read_now:
            seed_pdfs[pdf_url] = d
    for pdf_url, (links, d) in read_now.items():
        seed_links += links
        seed_pdfs[pdf_url] = d
    if read_now:
        seed_file.write_text(json.dumps({"links": [ln.__dict__ for ln in seed_links], "pdfs": seed_pdfs},
                                        indent=1, ensure_ascii=False), encoding="utf-8")
        if args.no_crawl:
            log.info("Seed PDFs read again from disk: %s", ", ".join(pdf_name(u) for u in read_now))

    # One event per agenda entry (see assign_entry_keys).
    skip_hosts = {registered_host(h) for h in args.follow_skip}
    entry_keys, entry_links = assign_entry_keys(seed_pdfs, skip_hosts)
    seed_sites = [Site(f"pdf:{pdf_name(u)}", "seed", [u], platform=True)
                  for u in dict.fromkeys([ln.pdf for ln in seed_links] + list(seed_pdfs))]
    platform_hosts = PLATFORM_HOSTS | {registered_host(h) for h in args.follow_single_page}

    registry = SiteRegistry(main_sites + seed_sites, args.include_subdomains)
    registry_rebuilt = False
    if args.no_crawl:
        loaded = SiteRegistry.load(out / "sites.json", args.include_subdomains)
        if loaded is None:
            log.warning("sites.json is missing (the linked-site crawl was interrupted, or this "
                        "folder was made by an older version): which cached pages belong to "
                        "which linked website is rebuilt from the cached links")
        else:
            # Seed sites of older runs were keyed by the percent-encoded file name.
            sites = [s for s in loaded.sites if s.role != "seed"]
            known = {u for s in loaded.sites if s.role == "seed" for u in s.start_urls}
            sites += [Site(f"pdf:{pdf_name(s.start_urls[0])}" if s.start_urls else s.key, "seed",
                           s.start_urls, platform=True, status=s.status)
                      for s in loaded.sites if s.role == "seed"]
            sites += [s for s in seed_sites if s.start_urls[0] not in known]
            registry = SiteRegistry(sites, args.include_subdomains)
    elif args.follow_links != "none" or seed_links:
        targets = build_targets(cache, hosts, args.follow_links, skip_hosts, seed_links, seed_pdfs,
                                entry_keys, args)
        log.info("Following %d linked URLs on %d websites", len(targets),
                 len({registered_host(urlparse(u).netloc) for u in targets}))
        # Saved before the crawl and again if it is interrupted, so --no-crawl can
        # always tell which cached pages belong to which linked site.
        SiteRegistry(main_sites + seed_sites + planned_sites(targets, platform_hosts, "pending"),
                     args.include_subdomains).save(out / "sites.json")
        kwargs = {}
        params = inspect.signature(crawl_linked).parameters
        if "workers" in params:
            kwargs["workers"] = max(1, args.follow_workers)
        if "blocked_hosts" in params:
            kwargs["blocked_hosts"] = set(hosts)
        try:
            linked = crawl_linked(targets, cache, base_cfg, args.follow_max_sites,
                                  args.follow_max_pages, args.follow_max_depth, platform_hosts, **kwargs)
        except BaseException:
            SiteRegistry(main_sites + seed_sites
                         + planned_sites(targets, platform_hosts, "interrupted (partly crawled)"),
                         args.include_subdomains).save(out / "sites.json")
            log.warning("Linked-site crawl stopped. sites.json lists every planned site, so "
                        "--no-crawl analyses what was cached; run the same command again to resume.")
            raise
        registry = SiteRegistry(main_sites + seed_sites + linked, args.include_subdomains)
    if not args.no_crawl:
        registry.save(out / "sites.json")

    # ---------------------------------------------------------------- pages
    in_window = month_window(args.calendar_from, args.calendar_to)
    n_cached = sum(1 for _ in cache.root.glob("*.json.gz"))
    log.info("Reading %d cached pages ...", n_cached)
    loaded_pages = load_pages(cache, registry, args.strip_regions, seed_pdfs, in_window, n_cached)
    pages_by_url, pdf_rows, meta = loaded_pages["pages"], loaded_pages["pdf_rows"], loaded_pages["meta"]
    orphans, moved = loaded_pages["orphans"], loaded_pages["moved"]
    if args.no_crawl and orphans:
        # A crawl interrupted before sites.json was complete (or a folder of an older
        # version): rebuild the linked sites from the cached links and seed entries.
        cal_cached = any(m[2] == "calendar" for m in meta.values())
        mode = args.follow_links if args.follow_links != "none" else ("events" if cal_cached else "all")
        targets = build_targets(cache, hosts, mode, skip_hosts, seed_links, seed_pdfs, entry_keys, args)
        have = {s.key for s in registry.sites}
        orphan_hosts = {registered_host(urlparse(u).netloc) for u in orphans}
        orphan_urls = set(orphans)
        extra = [s for s in planned_sites(targets, platform_hosts, "cached (registry rebuilt)")
                 if s.key not in have and (s.key in orphan_hosts
                                           or any(u in orphan_urls for u in s.start_urls))]
        if extra:
            registry = SiteRegistry(registry.sites + extra, args.include_subdomains)
            registry_rebuilt = True
            log.warning("%d linked websites with cached pages were missing from sites.json "
                        "(interrupted crawl?) and were added back from the cached links", len(extra))
    if moved:
        # Linked sites whose start page now redirects to another domain: pages on the
        # new domain belong to the same site (and so to the same events).
        for host, site in moved.items():
            if registry.site_of(f"https://{host}/") is None and host not in site.aliases:
                site.aliases.append(host)
        registry = SiteRegistry(registry.sites, args.include_subdomains)
    if registry_rebuilt or moved:
        retry = [cp for cp in (cache.get(u) for u in orphans) if cp is not None]
        again = load_pages(retry, registry, args.strip_regions, seed_pdfs, in_window,
                           pages=pages_by_url, pdf_rows=pdf_rows)
        meta.update(again["meta"])
        orphans = again["orphans"]
        if args.no_crawl:
            registry.save(out / "sites.json")
    if orphans and args.no_crawl:
        oh = Counter(registered_host(urlparse(u).netloc) for u in orphans)
        log.warning("%d cached pages on %d hosts belong to no known site and are NOT analysed "
                    "(e.g. %s). If the linked-site crawl was interrupted, run the command once "
                    "without --no-crawl to resume (cached pages are reused).", len(orphans), len(oh),
                    ", ".join(h for h, _ in oh.most_common(5)))
    order = {"main": 0, "linked": 1}
    pages = sorted(pages_by_url.values(), key=lambda p: (order.get(p.site_role, 2), p.site, p.depth, p.url))
    del pages_by_url, loaded_pages
    if not pages:
        log.error("No pages to analyse in %s", cache.root)
        return 1
    log.info("Analysing %d pages, including %d PDFs (%d PDFs found in total)",
             len(pages), sum(p.is_pdf for p in pages), len(pdf_rows))

    # --------------------------------------------------------------- events
    # Calendar cells, event pages, seed-PDF entries. Only the few pages that need
    # their HTML are read again from the cache (bodies are not kept in memory).
    index = EventIndex()
    calendar_hosts = set()
    for url, (final_url, is_pdf, kind, referrer) in meta.items():
        if kind == "calendar" and registry.site_of(url):
            cp = cache.get(url)
            if cp is not None:
                index.add_calendar_page(cp)
                calendar_hosts.add(referrer)
    by_final = {m[0]: u for u, m in meta.items()}
    for url in calendar_hosts:   # the months shown on the calendar page when it loads
        hurl = url if url in meta else by_final.get(url)
        host = cache.get(hurl) if hurl else None
        if host is not None and (in_window is None):
            index.add_calendar_page(host)
    for url, (final_url, is_pdf, kind, referrer) in meta.items():
        if not is_pdf and (final_url in index.events or url in index.events):
            cp = cache.get(url)
            if cp is not None and cp.body:
                index.add_event_page(cp)
    for pdf_url, d in seed_pdfs.items():
        for e in d["entries"]:
            iso = f"{e['start']} to {e['end']}" if e["start"] and e["end"] else e["start"]
            key = entry_keys[(pdf_url, e["index"])]
            index.add_pdf_link(key, e["name"], e["date_raw"], iso, pdf_url,
                               link=entry_links[(pdf_url, e["index"])],
                               entry_text=" ".join(e.get("lines") or []))
            if not iso and e.get("month"):
                index.events[key].months.add(e["month"])
    if index.date_conflicts:
        log.info("%d agenda entries repeat an event with a different date range; the first "
                 "listing's dates are kept", index.date_conflicts)
    real_seed = filter_targets({ln.url: [] for ln in seed_links}, skip_hosts)
    for ln in seed_links:   # older runs without entries
        if ln.url in real_seed and not seed_pdfs.get(ln.pdf):
            index.add_pdf_link(ln.url, ln.event, ln.date_raw, ln.date_iso, ln.pdf)
    why = index.locate()
    if why:
        log.warning("Event countries not set: %s", why)
    else:
        located = sum(1 for ev in index.events.values() if ev.country.get("countries"))
        log.info("Event countries found for %d of %d events", located, len(index.events))
    event_fields = ["event", "date", "event_link", "event_key", "listed_on", "calendar_months",
                    "event_country", "country_iso", "country_source", "location_text",
                    "location_structured", "entry_words"]
    write_csv(out / "events.csv", index.rows(pdf_display), event_fields)
    log.info("Events found: %d", len(index.events))
    by_url = {p.url: p for p in pages}
    sites_by_key = {s.key: s for s in registry.sites}
    page_event_cache: Dict[str, List[EventRef]] = {}
    other_pages_of: Dict[str, str] = {}
    editions_of: Dict[str, str] = {}
    landing_of: Dict[str, Set[tuple]] = {}
    nearest = args.edition_policy == "nearest"

    def pick_edition(refs: List[EventRef], page) -> Tuple[List[EventRef], List[EventRef]]:
        """Several dated events reach one page (editions of a recurring event linking
        the same site): keep the one nearest the page's date, else the latest."""
        dated = [(r, _date_span(r.date)[0]) for r in refs]
        dated = [(r, d) for r, d in dated if d is not None]
        if not nearest or len(dated) < 2:
            return refs, []
        pdate, _ = page_date_of(page)
        when = _date_span(pdate)[0] if pdate else None
        if when is not None:
            best_day = min(dated, key=lambda x: (abs((x[1] - when).days), -x[1].toordinal()))[1]
        else:
            best_day = max(d for _, d in dated)
        best = [r for r, d in dated if d == best_day]    # events on the same day are not editions
        undated = [r for r in refs if _date_span(r.date)[0] is None]
        return best + undated, [r for r, d in dated if d != best_day]

    def page_events(page) -> List[EventRef]:
        """Events a whole page belongs to ([] for listings, handled per paragraph)."""
        if page.url in page_event_cache:
            return page_event_cache[page.url]
        page_event_cache[page.url] = []     # guards against referrer loops
        refs: List[EventRef] = []
        others: List[EventRef] = []
        if page.kind not in ("calendar", "seed"):
            urls = {page.url} | set(getattr(page, "requested_urls", ()) or ())
            nurls = urls | {normalise_url(u) for u in urls if normalise_url(u)}
            # The page is an event page itself (also when reached through a redirect).
            refs = [index.ref(k) for k in dict.fromkeys(u for u in nurls if u in index.events)]
            site = sites_by_key.get(page.site) or registry.site_of(page.url)
            linked = site is not None and site.role == "linked"
            if linked:
                exact = [v for v in site.via if v.target in nurls or normalise_url(v.target) in nurls]
                via_refs = [index.ref(v.event_url or v.target, v.event, v.date or v.month) for v in exact]
                if via_refs:     # the page the event links to: its landing page
                    landing_of[page.url] = {(r.key or r.link, r.name) for r in via_refs}
                refs += via_refs
            if not refs and page.is_pdf and page.referrer:
                ref_page = by_url.get(page.referrer)
                if ref_page is not None:
                    refs = list(page_events(ref_page))
                    if ref_page.url in other_pages_of:
                        other_pages_of[page.url] = other_pages_of[ref_page.url]
                    if ref_page.url in editions_of:
                        editions_of[page.url] = editions_of[ref_page.url]
                elif page.referrer in index.events:
                    refs = [index.ref(page.referrer)]
            if not refs and linked:
                vias = site.via
                all_refs = list({(r.key or r.link, r.name): r for r in
                                 (index.ref(v.event_url or v.target, v.event, v.date or v.month)
                                  for v in vias)}.values())
                if len({v.event_url or v.target for v in vias}) == 1:
                    refs = all_refs
                else:
                    kept, others = pick_edition(all_refs, page) if all_refs else ([], [])
                    if others:   # editions of one recurring event: the nearest one(s)
                        refs = [r for r in kept if _date_span(r.date)[0] is not None]
                        others = [r for r in all_refs if r not in refs]
                    else:
                        # Other pages of a site several events link to belong to none of
                        # them in particular; the linking events are listed separately.
                        refs = [EventRef(f"(other pages of {site.key})", "", "")]
                        others = []
                    other_pages_of[page.url] = site.via_events(30)
            uniq = list({(r.key or r.link, r.name, r.date): r for r in refs}.values())
            if len(uniq) > 1 and not others:
                uniq, others = pick_edition(uniq, page)
            refs = uniq
            if others:
                editions_of[page.url] = "; ".join(f"{r.name} ({r.date})" if r.date else r.name
                                                  for r in others[:30])
        page_event_cache[page.url] = refs
        return refs

    seed_event_keys = {k for k, ev in index.events.items() if any(ln.url == k for ln in seed_links)}
    block_event_cache: Dict[str, List[List[EventRef]]] = {}

    def block_events_for(page) -> List[List[EventRef]]:
        """Events for each paragraph of a page. Each block of a seed PDF belongs to
        the agenda entry it is part of; calendar cells to the events named in them
        (or, for a cell whose title is a link, the event it links to)."""
        if page.url not in block_event_cache:
            whole = page_events(page)
            res, last = [], []
            line_entry = seed_pdfs.get(page.url, {}).get("line_entry") if page.kind == "seed" else None
            seen_on_page: Counter = Counter()
            on_cal = page.kind == "calendar" or page.url in calendar_hosts
            for b in page.blocks:
                refs = whole
                entry = getattr(b, "entry", None)
                if not refs and on_cal:
                    if b.text:
                        refs = index.named_in(b.text)
                    else:
                        refs = [index.ref(normalise_url(lk.href) or lk.href) for lk in b.links
                                if (normalise_url(lk.href) or lk.href) in index.events]
                elif not refs and page.kind == "seed" and entry is not None:
                    key = entry_keys.get((page.url, entry)) if entry >= 0 else None
                    refs = [index.ref(key)] if key else []
                elif not refs and page.kind == "seed" and line_entry is not None:
                    pno = b.region.rsplit(".", 1)[-1]
                    k = seen_on_page[pno]
                    seen_on_page[pno] += 1
                    owners = line_entry.get(pno, [])
                    idx = owners[k] if k < len(owners) else -1
                    key = entry_keys.get((page.url, idx)) if idx >= 0 else None
                    refs = [index.ref(key)] if key else []
                elif not refs and page.kind == "seed" and b.text:   # older runs without entries
                    named = index.named_in(b.text, seed_event_keys)
                    if named:
                        last = named
                    refs = named or last
                res.append(refs)
            block_event_cache[page.url] = res
        return block_event_cache[page.url]

    def unit_of(page, bi: int, refs: List[EventRef]) -> str:
        """The source a paragraph belongs to, for the theme-hit inclusion criterion:
        an agenda entry, a calendar cell, or else the whole page or PDF."""
        entry = getattr(page.blocks[bi], "entry", None)
        if page.kind == "seed" and entry is not None:
            return f"{page.url}#entry-{entry + 1}" if entry >= 0 else page.url
        if (page.kind in ("seed", "calendar") or page.url in calendar_hosts) and refs:
            keys = "+".join(sorted(r.key or r.link for r in refs))
            return f"{page.url}#cell-{hashlib.sha1(keys.encode('utf-8')).hexdigest()[:10]}"
        return page.url

    def layer_of(page, ref: EventRef) -> str:
        """How directly a source documents an event: the listing itself (agenda entry,
        calendar cell), the event's page on the main site, the landing page of the
        event's link, or another page or PDF of a linked website."""
        if page.kind == "seed" or page.site_role == "seed":
            return "listing_seed"
        if page.kind == "calendar" or page.url in calendar_hosts:
            return "listing_calendar"
        urls = {page.url} | set(getattr(page, "requested_urls", ()) or ())
        if page.site_role == "main":
            return "main_event_page" if ref.key and any(
                (normalise_url(u) or u) == ref.key for u in urls) else "main_page"
        link = _norm_link(ref.link)
        if (ref.key or ref.link, ref.name) in landing_of.get(page.url, ()) or (
                link and any(_norm_link(u) == link for u in urls)):
            return "landing"
        return "linked_pdf" if page.is_pdf else "linked_page"

    title_keys: Dict[str, str] = {}

    def in_title(org_key: str, ref: EventRef) -> bool:
        """Is the organisation named in the event's title (as whole words, or as the
        start of a hashtag such as #ShellMustFall)?"""
        if not org_key:
            return False
        tk = title_keys.get(ref.name)
        if tk is None:
            tk = title_keys[ref.name] = f" {canonical_key(ref.name)} "
        if f" {org_key} " in tk:
            return True
        if " " not in org_key and len(org_key) >= 4 and "#" in ref.name:
            return any(canonical_key(h).startswith(org_key) for h in re.findall(r"#(\w+)", ref.name))
        return False

    themes = ThemeSet.load(args.themes if args.themes and args.themes.exists() else None)
    focus = {t.strip() for t in args.focus_themes.split(",") if t.strip()} or set(themes.names)
    unknown = focus - set(themes.names)
    if unknown:
        log.warning("Unknown --focus-themes ignored: %s", ", ".join(sorted(unknown)))
    ex_kwargs = {}
    if "cache_dir" in inspect.signature(OrgExtractor.__init__).parameters:
        ex_kwargs["cache_dir"] = out / "cache"
    extractor = OrgExtractor(Watchlist.load(args.watchlist), load_stoplist(args.stoplist),
                             args.spacy_model, use_links=not args.no_links, ner_size=args.ner_size,
                             **ex_kwargs)

    log.info("Preparing the analysis (repeated menus and footers, vocabulary, acronyms) ...")
    # Paragraphs repeated across many pages (menus, footers, partner strips).
    # Counted per site, so one linked site's footer is judged against that site only.
    block_pages: Counter = Counter()
    site_pages: Counter = Counter(p.site for p in pages)
    for p in pages:
        for b in p.blocks:
            b.hash = block_hash(b.text) if b.text else ""   # type: ignore[attr-defined]
        block_pages.update({(p.site, b.hash) for b in p.blocks if b.text})
    boilerplate = {k for k, n in block_pages.items()
                   if n > max(3, args.boilerplate_share * site_pages[k[0]])}
    del block_pages

    # Which words this corpus writes in lower case (used to reject ordinary words as names).
    extractor.prepare_corpus(b.text for p in pages for b in p.blocks if b.text)
    langs: Counter = Counter()

    # Acronym definitions across the whole site.
    acronyms: Dict[str, str] = {}
    acr_display: Dict[str, Counter] = defaultdict(Counter)
    for p in pages:
        for b in p.blocks:
            for long_form, acr in acronym_pairs(b.text):
                lk = canonical_key(long_form)
                if extractor._head_type(long_form.split()) or lk in extractor.watchlist.canonical:
                    acronyms.setdefault(canonical_key(acr), lk)
                    acr_display[lk][acr] += 1

    mention_rows: List[dict] = []
    theme_rows: List[dict] = []
    page_rows: List[dict] = []
    seen_theme_sentences: Dict[tuple, str] = {}
    theme_terms: Dict[str, Counter] = defaultdict(Counter)
    theme_pages: Counter = Counter()
    seen_context: Set[tuple] = set()
    units: Dict[str, dict] = {}                     # source unit -> row of sources.csv
    ev_pages: Dict[str, Set[str]] = defaultdict(set)
    ev_words: Counter = Counter()
    ev_theme_units: Dict[str, Set[str]] = defaultdict(set)
    ev_mentions: Counter = Counter()

    def ref_dict(page, r: EventRef) -> dict:
        layer = layer_of(page, r)
        cve, years = timing(page, r, layer)
        return {"key": pdf_display(r.key), "name": r.name, "date": r.date, "link": pdf_display(r.link),
                "layer": layer, "content_vs_event": cve, "years_after_event": years}

    log.info("Analysing %d pages ...", len(pages))
    prog = Progress("pages analysed", len(pages))
    for pi, page in enumerate(pages, 1):
        texts = [b.text for b in page.blocks]
        lang = detect_language(" ".join(texts[:200]), page.lang)
        langs[lang] += 1
        docs = extractor.pipe(texts, lang)
        block_hits = [themes.find(t) for t in texts]
        block_refs = block_events_for(page)
        page_hits: Dict[str, int] = Counter()
        for bh in block_hits:
            for t, v in bh.items():
                page_hits[t] += len(v)
                for _, _, term in v:
                    theme_terms[t][term.lower()] += 1
        for t in page_hits:
            theme_pages[t] += 1
        pdate, pdate_from = page_date_of(page)
        fetched = getattr(page, "fetched_at", 0) or 0
        fetched_iso = dt.datetime.fromtimestamp(fetched).isoformat(timespec="seconds") if fetched else ""
        on_listing = page.kind in ("seed", "calendar") or page.url in calendar_hosts
        ref_dicts: Dict[tuple, dict] = {}

        def refs_json(refs: List[EventRef]) -> List[dict]:
            outl = []
            for r in refs:
                k = (r.key, r.name, r.link, r.date)
                if k not in ref_dicts:
                    ref_dicts[k] = ref_dict(page, r)
                outl.append(ref_dicts[k])
            return outl

        # Per paragraph: its source unit, and whether it is site furniture.
        block_unit = [unit_of(page, bi, block_refs[bi]) for bi in range(len(page.blocks))]
        block_bp = [(page.site, b.hash) in boilerplate if b.text else True for b in page.blocks]
        block_menu = [b.region in FURNITURE_REGIONS or bp
                      or (not on_listing and 0 < len(b.text.split()) <= 4)   # a label or heading
                      for b, bp in zip(page.blocks, block_bp)]
        words_of = [len(b.text.split()) for b in page.blocks]
        for bi, u in enumerate(block_unit):
            row = units.get(u)
            if row is None:
                row = units[u] = {
                    "source_unit": u, "source_link": page.url, "page_title": page.title,
                    "site": page.site, "site_role": page.site_role,
                    "kind": page.kind or ("pdf" if page.is_pdf else "page"), "is_pdf": page.is_pdf,
                    "page_date": pdate, "fetched_at": fetched_iso, "words": 0,
                    "_events": {}, "theme_hits": 0, "menu_theme_hits": 0, "org_mentions": 0,
                    **{f"theme:{t}": 0 for t in themes.names}}
            row["words"] += words_of[bi]
            for r in block_refs[bi]:
                row["_events"].setdefault(r.key or r.link or r.name, r.name)
                if r.key:
                    ev_pages[r.key].add(page.url)
                    ev_words[r.key] += words_of[bi]
            for t, v in block_hits[bi].items():
                if t not in focus:
                    continue
                if block_menu[bi]:
                    row["menu_theme_hits"] += len(v)
                else:
                    row["theme_hits"] += len(v)
                    row[f"theme:{t}"] += len(v)
                    for r in block_refs[bi]:
                        if r.key:
                            ev_theme_units[r.key].add(u)

        # Sentences of the page in reading order, for theme contexts that run across
        # paragraph boundaries. Menus and footers only borrow from each other; an
        # agenda entry or calendar cell only from itself.
        page_sents: List[Tuple[int, int, int]] = []   # (block index, start, end)
        first_of_block: List[int] = []
        for bi, block in enumerate(page.blocks):
            first_of_block.append(len(page_sents))
            for st, en in split_sentences(block.text) if block.text else []:
                page_sents.append((bi, st, en))
        first_of_block.append(len(page_sents))
        content = {bi for bi, b in enumerate(page.blocks) if b.region in ("main",) or b.region.startswith("pdf")}
        group_of = [(("unit", block_unit[bi]) if block_refs[bi] else ("block", bi)) if on_listing
                    else ("content", bi in content) for bi in range(len(page.blocks))]
        groups: Dict[tuple, List[int]] = defaultdict(list)
        pos_in_group: List[int] = []
        for i, (b2, _, _) in enumerate(page_sents):
            g = groups[group_of[b2]]
            pos_in_group.append(len(g))
            g.append(i)

        def sent_text(k: int) -> str:
            bi, st, en = page_sents[k]
            return page.blocks[bi].text[st:en]

        page_seen: Set[tuple] = set()
        for bi, (block, bhits) in enumerate(zip(page.blocks, block_hits)):
            if not bhits:
                continue
            evs = block_refs[bi]
            evj = refs_json(evs)
            for theme, hits in bhits.items():
                for start, end, term in hits:
                    k = next((i for i in range(first_of_block[bi], first_of_block[bi + 1])
                              if page_sents[i][1] <= start < page_sents[i][2]), None)
                    if k is None:
                        continue
                    same = groups[group_of[bi]]
                    pos = pos_in_group[k]
                    before = " ".join(sent_text(i) for i in same[max(0, pos - 2):pos])
                    after = " ".join(sent_text(i) for i in same[pos + 1:pos + 3])
                    sentence = sent_text(k)
                    dup_key = (theme, term.lower(), sentence.strip().casefold())
                    first_seen = seen_theme_sentences.setdefault(dup_key, page.url)
                    duplicate = (pdf_display(first_seen) if first_seen != page.url
                                 else "same_page" if dup_key in page_seen else "")
                    page_seen.add(dup_key)
                    theme_rows.append({
                        "theme": theme, "term": term,
                        "event": _join(e.name for e in evs),
                        "event_date": _join(e.date for e in evs),
                        "event_link": _join(pdf_display(e.link) for e in evs),
                        "event_key": _join(pdf_display(e.key) for e in evs),
                        "n_events": len(evs),
                        "site_linked_from_events": other_pages_of.get(page.url, ""),
                        "recurring_event_editions": editions_of.get(page.url, ""),
                        "source_link": pdf_display(page.url), "source_unit": pdf_display(block_unit[bi]),
                        "page_title": page.title, "page_date": pdate, "page_date_from": pdate_from,
                        "fetched_at": fetched_iso,
                        "site": page.site, "site_role": page.site_role,
                        "is_pdf": page.is_pdf, "pdf_found_on": pdf_display(page.referrer),
                        "region": block.region, "boilerplate": block_bp[bi],
                        "menu_or_sidebar": block_menu[bi],
                        "duplicate_of": duplicate,
                        "context_before": before, "sentence": sentence, "context_after": after,
                        "events_json": json.dumps(evj, ensure_ascii=False),
                        "_block": (page.url, bi),
                    })

        n_mentions = 0
        for bi, (block, doc, bhits) in enumerate(zip(page.blocks, docs, block_hits)):
            mentions = extractor.extract(block.text, block.links, doc)
            if not mentions:
                continue
            evs = block_refs[bi]
            for m in mentions:
                m.key = acronyms.get(m.key, m.key)
            if on_listing and evs:
                # A calendar cell's title (and the link around it) names the event, not
                # an organisation that takes part in it.
                ev_hrefs = {normalise_url(lk.href) or lk.href for lk in block.links} & index.events.keys()
                titles = {canonical_key(re.sub(r"\s*[\[(][^\])]*[\])]\s*$", "", e.name)) for e in evs
                          if page.kind != "seed"}
                mentions = [m for m in mentions
                            if not (ev_hrefs and m.start < 0 and set(m.methods) == {"link"})
                            and not (titles and (canonical_key(m.surface) in titles or m.key in titles))]
                if not mentions:
                    continue
            is_bp = block_bp[bi]
            sents = split_sentences(block.text) if block.text else []
            keys_here = sorted({m.key for m in mentions})
            evj = refs_json(evs)
            unit = block_unit[bi]
            for m in mentions:
                if m.start >= 0 and sents:
                    idx = next((i for i, (s, e) in enumerate(sents) if s <= m.start < e), 0)
                    sentence = block.text[sents[idx][0]:sents[idx][1]]
                    context = window(sents, idx, args.context_sentences, block.text)
                else:
                    sentence = context = block.text or f"[link] {m.surface}"
                ctx_hits = themes.find(context)

                scope_hits = {"any": None, "page": page_hits, "paragraph": bhits,
                              "sentence": ctx_hits}[args.theme_scope]
                if scope_hits is not None and not (set(scope_hits) & focus):
                    continue

                ctx_id = (m.key, block_hash(context))
                duplicate = ctx_id in seen_context
                seen_context.add(ctx_id)
                n_mentions += 1
                units[unit]["org_mentions"] += 1
                for e in evs:
                    if e.key:
                        ev_mentions[e.key] += 1
                flags = [in_title(m.key, e) for e in evs]
                mention_rows.append({
                    "event": _join(e.name for e in evs),
                    "event_date": _join(e.date for e in evs),
                    "event_link": _join(pdf_display(e.link) for e in evs),
                    "event_key": _join(pdf_display(e.key) for e in evs),
                    "events_json": json.dumps([{**d, "in_title": f} for d, f in zip(evj, flags)],
                                              ensure_ascii=False),
                    "site_linked_from_events": other_pages_of.get(page.url, ""),
                    "recurring_event_editions": editions_of.get(page.url, ""),
                    "in_menu": block.region in FURNITURE_REGIONS,
                    "named_in_event_title": any(flags),
                    "_block": (page.url, bi),
                    "org_key": m.key,
                    "surface_form": m.surface,
                    "methods": "+".join(sorted(m.methods)),
                    "org_type": m.org_type,
                    "link_domain": m.link_domain,
                    "site": page.site,
                    "site_role": page.site_role,
                    "via_event": page.via_event,
                    "calendar_month": page.label if page.kind == "calendar" else "",
                    "url": pdf_display(page.url),
                    "source_unit": pdf_display(unit),
                    "page_title": page.title,
                    "page_date": pdate,
                    "fetched_at": fetched_iso,
                    "region": block.region,
                    "is_pdf": page.is_pdf,
                    "embedded_in": pdf_display(page.referrer),
                    "boilerplate": is_bp,
                    "repeat_context": duplicate,
                    "sentence": sentence,
                    "context": context,
                    "paragraph": block.text[:3000],
                    "context_themes": themes_str(ctx_hits),
                    "context_theme_terms": "; ".join(sorted({x[2].lower() for v in ctx_hits.values() for x in v})),
                    "paragraph_themes": themes_str(bhits),
                    "page_themes": themes_str(page_hits),
                    "co_mentioned_keys": [k for k in keys_here if k != m.key],
                    "_unit": unit,
                })
        page_keys = list(dict.fromkeys(r.key for refs in block_refs for r in refs if r.key))
        page_rows.append({
            "site": page.site, "site_role": page.site_role, "via_event": page.via_event,
            "kind": page.kind or ("pdf" if page.is_pdf else "page"), "calendar_month": page.label,
            "url": pdf_display(page.url), "requested_url": pdf_display(getattr(page, "requested_url", "")),
            "title": page.title, "date": page.date, "page_date": pdate, "page_date_from": pdate_from,
            "fetched_at": fetched_iso, "depth": page.depth,
            "is_pdf": page.is_pdf, "embedded_in": pdf_display(page.referrer),
            "words": sum(words_of), "org_mentions": n_mentions,
            "event_keys": "; ".join(pdf_display(k) for k in page_keys[:50]),
            "theme_hit": any(units[u]["theme_hits"] for u in set(block_unit)),
            **{f"theme:{t}": page_hits.get(t, 0) for t in themes.names},
        })
        prog.tick(pi)

    log.info("Page languages: %s", ", ".join(f"{k} {n}" for k, n in langs.most_common()))
    if extractor.missing_models:
        log.warning("NER was skipped for some text because these spaCy models are not installed "
                    "(rule-based detection still ran): %s. Install with: %s",
                    ", ".join(sorted(extractor.missing_models)),
                    " && ".join(f"python -m spacy download {m}" for m in sorted(extractor.missing_models)
                                if m.endswith("_sm")))
    log.info("Merging organisation names ...")
    # Site-wide vetting of names found only by NER / links / appositives.
    methods_by_key: Dict[str, Set[str]] = defaultdict(set)
    for r in mention_rows:
        methods_by_key[r["org_key"]].update(r["methods"].split("+"))
    rejected = {k for k, ms in methods_by_key.items() if not extractor.vet(k, ms)}
    if rejected:
        log.info("Dropped %d names spaCy mostly labels as places, people, laws etc.", len(rejected))
    mention_rows = [r for r in mention_rows if r["org_key"] not in rejected]
    for r in mention_rows:
        r["co_mentioned_keys"] = [k for k in r["co_mentioned_keys"] if k not in rejected]

    orgs = aggregate(mention_rows, extractor, acr_display, themes.names, args.min_mentions)
    display = {o["org_key"]: o["organisation"] for o in orgs}
    for r in mention_rows:
        r["organisation"] = display.get(r["org_key"], "")
        r["co_mentioned"] = "; ".join(display.get(k, k) for k in r.pop("co_mentioned_keys"))
    kept = [r for r in mention_rows if r["organisation"]]
    del mention_rows
    orgs_in_block: Dict[tuple, Dict[str, str]] = defaultdict(dict)
    for r in kept:
        orgs_in_block[r["_block"]].setdefault(r["org_key"], r["organisation"])
    themed_units = {u for u, row in units.items() if row["theme_hits"]}
    for r in theme_rows:
        here = orgs_in_block.get(r.pop("_block"), {})
        r["organisations_in_paragraph"] = "; ".join(here.values())
        r["organisations_in_paragraph_keys"] = "; ".join(here.keys())
    for r in kept:
        r.pop("_block", None)
        r["source_has_theme"] = r.pop("_unit") in themed_units
    theme_fields = ["theme", "term", "event", "event_date", "event_link", "event_key", "n_events",
                    "site_linked_from_events", "recurring_event_editions", "source_link", "source_unit",
                    "page_title", "page_date", "page_date_from", "fetched_at", "site", "site_role",
                    "is_pdf", "pdf_found_on", "region", "boilerplate", "menu_or_sidebar", "duplicate_of",
                    "context_before", "sentence", "context_after", "organisations_in_paragraph",
                    "organisations_in_paragraph_keys", "events_json"]
    log.info("Writing CSV outputs ...")
    write_csv(out / "theme_hits.csv", theme_rows, theme_fields)
    n_theme_rows = len(theme_rows)
    del theme_rows

    write_outputs(out, kept, orgs, page_rows, themes, theme_terms, theme_pages, len(pages), args)
    for r in pdf_rows:
        r["pdf_url"], r["embedded_in"] = pdf_display(r["pdf_url"]), pdf_display(r["embedded_in"])
    write_csv(out / "pdfs.csv", pdf_rows,
              ["pdf_url", "embedded_in", "site", "title", "date", "pages", "words", "status"])
    analysed = Counter(p.site for p in pages)
    write_csv(out / "sites.csv", [{
        "site": st.key, "role": st.role, "status": st.status or ("crawled" if st.role == "main" else ""),
        "pages_analysed": analysed.get(st.key, 0), "single_page_platform": st.platform,
        "start_urls": " ".join(pdf_display(u) for u in st.start_urls[:10]), "via_events": st.via_events(50),
        "via_months": "; ".join(sorted({v.month for v in st.via if v.month})),
        "via_pages": " ".join(sorted({pdf_display(v.page) for v in st.via})[:20]),
        "moved_to": " ".join(getattr(st, "aliases", None) or []),
    } for st in registry.sites])

    # Sources (R1): every web page, PDF, agenda entry and calendar cell analysed.
    source_rows = []
    for u, row in units.items():
        evs = row.pop("_events")
        row["source_unit"], row["source_link"] = pdf_display(u), pdf_display(row["source_link"])
        row["event_keys"] = "; ".join(pdf_display(k) for k in list(evs)[:50])
        row["events"] = "; ".join(list(dict.fromkeys(evs.values()))[:20])
        row["has_theme_hit"] = bool(row["theme_hits"])
        source_rows.append(row)
    write_csv(out / "sources.csv", source_rows,
              ["source_unit", "source_link", "page_title", "site", "site_role", "kind", "is_pdf",
               "page_date", "fetched_at", "words", "event_keys", "events", "org_mentions",
               "has_theme_hit", "theme_hits", "menu_theme_hits"] + [f"theme:{t}" for t in themes.names])
    n_units, n_units_theme = len(source_rows), sum(1 for r in source_rows if r["has_theme_hit"])
    del source_rows, units

    # events.csv again, now with how well each event is covered by what was read.
    site_status = {s.key: s for s in registry.sites}
    rows = index.rows(pdf_display)
    for ev, row in zip(sorted(index.events.values(), key=lambda e: (e.date, e.name, e.key)), rows):
        row.update(event_coverage(ev, registry, site_status, analysed, skip_hosts))
        row["pages_analysed"] = len(ev_pages.get(ev.key, ()))
        row["words_analysed"] = ev_words.get(ev.key, 0)
        row["org_mentions"] = ev_mentions.get(ev.key, 0)
        row["sources_with_theme_hit"] = len(ev_theme_units.get(ev.key, ()))
    write_csv(out / "events.csv", rows, event_fields + ["link_followed", "link_status", "pages_analysed",
                                                         "words_analysed", "org_mentions",
                                                         "sources_with_theme_hit"])

    write_report(out / "report.html", orgs, kept, themes, theme_pages, len(pages),
                 start_urls or [Path(u).name for u in args.seed_pdf],
                 [st.key for st in registry.sites if analysed.get(st.key)])

    # Run record (completed in main()).
    models = {}
    for name, nlp in (getattr(extractor, "_models", None) or {}).items():
        if nlp is not None:
            models[name] = getattr(nlp, "meta", {}).get("version", "")
    fetched = [getattr(p, "fetched_at", 0) for p in pages if getattr(p, "fetched_at", 0)]
    info.update({
        "spacy_models_used": models, "ner_size": args.ner_size, "spacy_model": args.spacy_model,
        "spacy_models_missing": sorted(getattr(extractor, "missing_models", ()) or ()),
        "spacy_models_broken": sorted(getattr(extractor, "broken_models", ()) or ()),
        "pages_fetched_between": [dt.datetime.fromtimestamp(min(fetched)).isoformat(timespec="seconds"),
                                  dt.datetime.fromtimestamp(max(fetched)).isoformat(timespec="seconds")]
        if fetched else [],
        "counts": {"pages_analysed": len(pages), "pdfs_found": len(pdf_rows), "events": len(index.events),
                   "organisations": len(orgs), "mentions": len(kept), "theme_hits": n_theme_rows,
                   "sources": n_units, "sources_with_theme_hit": n_units_theme},
        "sites_registry": "rebuilt from cached links" if registry_rebuilt else "sites.json",
    })
    n_orgs, n_kept, n_pages = len(orgs), len(kept), len(pages)

    # Free the large structures before the master files are built (they are read
    # back from the CSV files written above).
    extractor.close()
    if isinstance(getattr(extractor, "_models", None), dict):
        extractor._models.clear()
    del kept, orgs, pages, page_rows
    for d in (by_url, block_event_cache, page_event_cache, landing_of):
        d.clear()
    gc.collect()

    log.info("Writing the master files ...")
    stats: dict = {}
    try:
        for path in build_masters([out], out, theme_filter=args.theme_filter,
                                  keep_menu_themes=args.keep_menu_themes, stats=stats):
            log.info("Master file: %s", path)
    except Exception as exc:  # noqa: BLE001 - the CSV results above are complete
        log.error("Master files could not be written (%s: %s); the CSV results in %s are complete. "
                  "Try again with: python make_master.py %s -o %s", type(exc).__name__, exc, out, out, out)
    info["master_files"] = stats
    try:
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        summary["run"] = {k: info.get(k) for k in ("started_at", "command", "spacy_models_used",
                                                    "ner_size", "pages_fetched_between")}
        summary["run"]["calendar_months"] = [args.calendar_from or "", args.calendar_to or ""]
        summary["run"]["seed_pdfs"] = [pdf_name(u) for u in seed_pdfs]
        summary["master_files"] = stats
        (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str),
                                          encoding="utf-8")
    except (OSError, ValueError):
        pass
    log.info("Done: %d organisations, %d mentions, %d pages -> %s", n_orgs, n_kept, n_pages, out)
    return 0


def event_coverage(ev, registry: SiteRegistry, sites: Dict[str, Site], analysed: Counter,
                   skip_hosts: Set[str]) -> Dict[str, str]:
    """Was the event's own link read? link_followed yes/no, and why not."""
    link = ev.link or ev.key
    if not re.match(r"https?://", link or ""):
        return {"link_followed": "no", "link_status": "no link"}
    site = registry.site_of(link)
    if site is None:
        if not filter_targets({link: []}, skip_hosts):
            return {"link_followed": "no", "link_status": "not followed (social media or skipped host)"}
        return {"link_followed": "no", "link_status": "not followed"}
    n = analysed.get(site.key, 0)
    status = site.status or ("crawled" if site.role == "main" else "")
    if site.role == "main":
        status = "main site"
    return {"link_followed": "yes" if n else "no",
            "link_status": f"{status}; {n} pages analysed" if status else f"{n} pages analysed"}



# --------------------------------------------------------------------------
def aggregate(rows: List[dict], extractor: OrgExtractor, acr_display: Dict[str, Counter],
              theme_names: List[str], min_mentions: int) -> List[dict]:
    by_key: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_key[r["org_key"]].append(r)

    orgs = []
    for key, rs in by_key.items():
        distinct = [r for r in rs if not r["repeat_context"]]
        substantive = [r for r in distinct if not r["boilerplate"]]
        if len(distinct) < min_mentions:
            continue
        forms = Counter(r["surface_form"] for r in rs)
        name = extractor.watchlist.display.get(key) or _best_form(forms)
        methods = Counter(m for r in rs for m in r["methods"].split("+"))
        types = Counter(r["org_type"] for r in rs if r["org_type"])
        theme_counts = Counter()
        for r in distinct:
            for part in filter(None, r["paragraph_themes"].split("; ")):
                theme_counts[part.rsplit(" (", 1)[0]] += 1
        pages = {r["url"] for r in rs}
        sites = Counter(r["site"] for r in distinct)
        orgs.append({
            "organisation": name,
            "org_key": key,
            "org_type": types.most_common(1)[0][0] if types else "",
            "confidence": _confidence(methods, len(pages)),
            "distinct_mentions": len(distinct),
            "substantive_mentions": len(substantive),
            "total_mentions": len(rs),
            "pages": len(pages),
            "sites": len(sites),
            "on_main_site": any(r["site_role"] == "main" for r in rs),
            "site_list": "; ".join(f"{k} ({n})" for k, n in sites.most_common(10)),
            "boilerplate_only": not substantive,
            "methods": "; ".join(f"{m} ({n})" for m, n in methods.most_common()),
            "variants": "; ".join(f"{f} ({n})" for f, n in forms.most_common(8)),
            "acronyms": "; ".join(a for a, _ in acr_display.get(key, Counter()).most_common(3)),
            "themes": "; ".join(f"{t} ({n})" for t, n in theme_counts.most_common()),
            "themed_mentions": sum(1 for r in distinct if r["paragraph_themes"]),
            "linked_domains": "; ".join(sorted({r["link_domain"] for r in rs if r["link_domain"]})),
            "first_url": rs[0]["url"],
            **{f"theme:{t}": theme_counts.get(t, 0) for t in theme_names},
        })
    orgs.sort(key=lambda o: (-o["substantive_mentions"], -o["distinct_mentions"], o["organisation"].lower()))
    return orgs


def _best_form(forms: Counter) -> str:
    # Most frequent surface form, preferring spelled-out names over acronyms;
    # ties go to the longer (more complete) form.
    def is_acronym(f: str) -> bool:
        return bool(re.fullmatch(r"[A-Z0-9&.\-]{2,12}", f))
    return max(forms.items(), key=lambda kv: (not is_acronym(kv[0]), kv[1], len(kv[0])))[0]


def _confidence(methods: Counter, n_pages: int) -> str:
    strong = {"watchlist", "appositive"}
    if strong & set(methods) or len(methods) >= 2:
        return "high"
    if "pattern" in methods or "link" in methods or n_pages >= 3:
        return "medium"
    return "low"


def write_csv(path: Path, rows: List[dict], fields: Optional[List[str]] = None) -> None:
    if not rows and not fields:
        path.write_text("", encoding="utf-8")
        return
    fields = fields or list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8-sig") as fh:  # BOM so Excel reads UTF-8
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_outputs(out: Path, mentions: List[dict], orgs: List[dict], page_rows: List[dict],
                  themes: ThemeSet, theme_terms: Dict[str, Counter], theme_pages: Counter,
                  n_pages: int, args: argparse.Namespace) -> None:
    mention_fields = [
        "organisation", "surface_form", "org_type", "methods", "event", "event_date", "event_link",
        "event_key", "site_linked_from_events", "recurring_event_editions", "in_menu",
        "named_in_event_title", "site", "site_role", "via_event",
        "calendar_month", "url", "source_unit", "source_has_theme", "page_title", "page_date",
        "fetched_at", "is_pdf", "embedded_in", "region", "boilerplate", "repeat_context",
        "context_themes", "context_theme_terms",
        "paragraph_themes", "page_themes", "sentence", "context", "paragraph", "co_mentioned",
        "link_domain", "org_key", "events_json",
    ]
    write_csv(out / "mentions.csv", mentions, mention_fields)
    write_csv(out / "organisations.csv", orgs)
    write_csv(out / "pages.csv", page_rows)

    # Organisation co-occurrence (same paragraph), for network tools such as Gephi.
    names = {o["organisation"] for o in orgs}
    edges: Counter = Counter()
    for r in mentions:
        if r["repeat_context"]:
            continue
        for other in filter(None, r["co_mentioned"].split("; ")):
            if other in names and r["organisation"] < other:
                edges[(r["organisation"], other)] += 1
    write_csv(out / "org_cooccurrence.csv",
              [{"source": a, "target": b, "weight": n} for (a, b), n in edges.most_common()],
              ["source", "target", "weight"])

    summary = {
        "start_urls": args.start_urls,
        "pages_analysed": n_pages,
        "organisations": len(orgs),
        "mentions": len(mentions),
        "theme_scope": args.theme_scope,
        "themes": {
            t: {"pages": theme_pages.get(t, 0),
                "share_of_pages": round(theme_pages.get(t, 0) / n_pages, 3),
                "hits": sum(theme_terms[t].values()),
                "top_terms": theme_terms[t].most_common(15)}
            for t in themes.names
        },
        "organisation_types": Counter(o["org_type"] or "unknown" for o in orgs).most_common(),
        "confidence": Counter(o["confidence"] for o in orgs).most_common(),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                      encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
