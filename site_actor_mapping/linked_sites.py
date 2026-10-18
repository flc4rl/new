"""
Follow websites linked from a site's events (or from all its pages), crawl
each one lightly, and keep track of which event led to which website.

The registry of sites is saved as ``sites.json`` in the output folder, so a
``--no-crawl`` re-run knows which cached pages belong to which site.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from calendar_sweep import duplicate_calendar_months, month_window
from crawler import (CachedPage, CrawlConfig, Crawler, PageCache, fmt_duration, normalise_url,
                     registered_host, safe_join)

log = logging.getLogger(__name__)

# Not crawled: social media and services whose pages say little or block crawlers.
SKIP_HOSTS = {
    "facebook.com", "fb.me", "fb.com", "instagram.com", "twitter.com", "x.com", "t.co",
    "tiktok.com", "linkedin.com", "youtube.com", "youtu.be", "vimeo.com", "flickr.com",
    "t.me", "telegram.me", "telegram.org", "wa.me", "whatsapp.com", "chat.whatsapp.com",
    "bsky.app", "threads.net", "mastodon.social", "spotify.com", "open.spotify.com",
    "soundcloud.com", "podcasts.apple.com", "zoom.us", "us02web.zoom.us", "us06web.zoom.us",
    "meet.jit.si", "teams.microsoft.com", "meet.google.com", "maps.google.com",
    "goo.gl", "maps.app.goo.gl", "openstreetmap.org", "paypal.com", "paypal.me",
    "patreon.com", "wikipedia.org", "en.wikipedia.org", "apple.com",
    "addtoany.com", "static.addtoany.com", "wordpress.org", "creativecommons.org",
    # Google services with nothing to read (Docs, Drive, Forms, Sites and Groups are read)
    "accounts.google.com", "calendar.google.com", "news.google.com", "play.google.com",
    "support.google.com", "policies.google.com", "translate.google.com", "photos.google.com",
}
# Skipped only as exactly this host (www. is ignored): google.com itself (search,
# maps links), but not docs.google.com, sites.google.com and the like.
SKIP_EXACT = {"google.com"}
# Platforms hosting many organisations: read only the linked page, never crawl further.
PLATFORM_HOSTS = {
    "actionnetwork.org", "eventbrite.com", "eventbrite.co.uk", "eventbrite.de", "eventbrite.fr",
    "eventbrite.it", "eventbrite.es", "eventbrite.ie", "eventbrite.be", "eventbrite.nl",
    "mobilize.us", "meetup.com", "tickettailor.com", "buytickets.at", "humanitix.com",
    "lu.ma", "partiful.com", "linktr.ee", "gofundme.com", "chuffed.org", "crowdfunder.co.uk",
    "change.org", "avaaz.org", "openpetition.de", "openpetition.eu", "you.38degrees.org.uk",
    "docs.google.com", "forms.gle", "framaforms.org", "drive.google.com", "medium.com",
    "sites.google.com", "groups.google.com",
    "substack.com", "blogspot.com", "wordpress.com", "github.com", "gitlab.com",
    "mailchi.mp", "eepurl.com", "campaign-archive.com", "notion.site", "padlet.com",
    "canva.site", "carrd.co", "wixsite.com", "squarespace.com", "weebly.com", "jimdofree.com",
    # link shorteners and link-in-bio pages: read the page they lead to, nothing more
    "tr.ee", "bit.ly", "tinyurl.com", "t.ly", "ow.ly", "lnkd.in", "is.gd", "buff.ly",
    "rebrand.ly", "shorturl.at", "cutt.ly", "linkin.bio", "beacons.ai", "bio.link",
}


@dataclass
class Via:
    page: str             # page on the main site where the link was found
    event: str = ""       # event title (link text in the calendar, or the event page title)
    month: str = ""       # calendar month the event appeared in, if any
    target: str = ""      # the linked URL itself
    event_url: str = ""   # key of the event in the event index (event page, or the link)
    date: str = ""        # event date as found next to the link (seed PDFs)


@dataclass
class Site:
    key: str                              # registered host, or host+path for platform pages
    role: str                             # "main", "linked" or "seed" (a seed PDF)
    start_urls: List[str] = field(default_factory=list)
    platform: bool = False
    via: List[Via] = field(default_factory=list)
    status: str = ""
    pages: int = 0
    aliases: List[str] = field(default_factory=list)   # hosts its start URLs redirected to

    def via_events(self, limit: int = 5, url: str = "") -> str:
        """Events that led here: those linking to `url` itself if any, else all of them."""
        vias = [v for v in self.via if url and v.target == url] or self.via
        names = list(dict.fromkeys(v.event for v in vias if v.event))
        more = f" (+{len(names) - limit} more)" if len(names) > limit else ""
        return "; ".join(names[:limit]) + more


class SiteRegistry:
    def __init__(self, sites: List[Site], include_subdomains: bool = False):
        self.sites = sites
        self.include_subdomains = include_subdomains
        self._platform_urls = {u: s for s in sites if s.platform for u in s.start_urls}
        self._hosts = {s.key: s for s in sites if not s.platform}
        # A linked site that moved domain: pages on the new host belong to it too
        # (setdefault: an alias never takes over another site's own key).
        for s in sites:
            if not s.platform:
                for alias in getattr(s, "aliases", None) or []:
                    self._hosts.setdefault(alias, s)

    @property
    def main(self) -> List[Site]:
        return [s for s in self.sites if s.role == "main"]

    def site_of(self, url: str) -> Optional[Site]:
        if url in self._platform_urls:
            return self._platform_urls[url]
        host = registered_host(urlparse(url).netloc)
        if host in self._hosts:
            return self._hosts[host]
        for key, site in self._hosts.items():
            if (self.include_subdomains or site.role == "linked") and host.endswith("." + key):
                return site
        return None

    def save(self, path: Path) -> None:
        path.write_text(json.dumps([asdict(s) for s in self.sites], indent=2, ensure_ascii=False),
                        encoding="utf-8")

    @classmethod
    def load(cls, path: Path, include_subdomains: bool) -> Optional["SiteRegistry"]:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        sites = [Site(**{**d, "via": [Via(**v) for v in d.get("via", [])]}) for d in raw]
        return cls(sites, include_subdomains)


def host_matches(host: str, names: Set[str]) -> bool:
    return any(host == n or host.endswith("." + n) for n in names)


def _external_links(html: str, base: str, own_hosts: Set[str], content_only: bool) -> List[tuple]:
    """(url, anchor text) of links to other sites; content_only drops nav/header/footer/aside."""
    soup = BeautifulSoup(html, "lxml")
    if content_only:
        for t in soup(["nav", "header", "footer", "aside", "script", "style"]):
            t.decompose()
        for t in soup.select('[role="navigation"], [role="banner"], [role="contentinfo"], '
                             '.a2a_kit, .addtoany_share, .sharedaddy, .social, .share'):
            t.decompose()
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = normalise_url(safe_join(base, href))   # "" for a malformed href
        if not url:
            continue
        host = registered_host(urlparse(url).netloc)
        if not host or host_matches(host, own_hosts):
            continue
        text = " ".join(a.get_text(" ").split())
        img = a.find("img")
        if not text and img is not None:
            text = img.get("alt", "")
        out.append((url, text))
    return out


def _on_site_links(html: str, base: str, own_hosts: Set[str]) -> List[tuple]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = normalise_url(safe_join(base, href))
        if url and host_matches(registered_host(urlparse(url).netloc), own_hosts):
            out.append((url, " ".join(a.get_text(" ").split())))
    return out


CALENDAR_SELECTOR = ('[class*="mec-calendar"], [id^="mec_skin_events"], .fc-view-harness, '
                     '[class*="tribe-events-calendar"], [class*="ai1ec-calendar"], .eo-fullcalendar')


def calendar_html(html: str) -> str:
    """The calendar widget(s) of a page, without menus and footers."""
    soup = BeautifulSoup(html, "lxml")
    parts = soup.select(CALENDAR_SELECTOR)
    top = [p for p in parts if not any(q is not p and p in q.descendants for q in parts)]
    return "".join(str(p) for p in top)


def collect_targets(cache: PageCache, main_hosts: Set[str], mode: str,
                    extra_skip: Set[str], calendar_from: Optional[str] = None,
                    calendar_to: Optional[str] = None,
                    platform_hosts: Optional[Set[str]] = None) -> Dict[str, List[Via]]:
    """External URLs to follow, each with where it was found.

    mode "events": links in loaded calendar months, plus links in the main
    content of the on-site event pages those months link to.
    mode "all": links in the main content of every page on the main site.
    Links present on many pages (sponsors, partner strips) are left out.
    calendar_from / calendar_to (YYYY-MM): cached calendar months outside this
    window (left by an earlier, wider run in the same folder) are ignored.
    platform_hosts: hosts read one page at a time (see filter_targets).
    """
    in_window = month_window(calendar_from, calendar_to)
    pages = {p.url: p for p in cache if not p.is_pdf and p.body and p.status < 400
             and p.kind != "calendar-index"
             and host_matches(registered_host(urlparse(p.final_url).netloc), main_hosts)}
    if in_window is not None:
        outside = [u for u, p in pages.items() if not in_window(p)]
        for u in outside:
            del pages[u]
        if outside:
            log.warning("%d cached calendar months outside --calendar-from/--calendar-to ignored",
                        len(outside))
    by_final = {p.final_url: p for p in pages.values()}
    targets: Dict[str, List[Via]] = defaultdict(list)

    def title_of(p: CachedPage) -> str:
        soup = BeautifulSoup(p.body, "lxml")
        t = soup.find("h1") or soup.title
        return " ".join(t.get_text(" ").split()) if t else ""

    if mode == "events":
        calendars = [p for p in pages.values() if p.kind == "calendar"]
        dup = duplicate_calendar_months(calendars)     # stale /events/page/N/ sweeps (finding 28)
        calendars = [p for p in calendars if p.url not in dup]
        # The calendar page itself already shows some months when it loads. Their
        # dates are not known here, so with a month window they are left out.
        for host_url in ({c.referrer for c in calendars if c.referrer} if in_window is None else ()):
            host_page = by_final.get(host_url) or pages.get(host_url)
            snippet = calendar_html(host_page.body) if host_page else ""
            if snippet:
                calendars.append(CachedPage(host_url, host_url, 200, "text/html", host_page.depth,
                                            0.0, snippet, referrer=host_url, kind="calendar"))
        if not calendars:
            log.warning("--follow-links events: no calendar was loaded, so no event links found. "
                        "Use --follow-links all, or --calendar-click if the calendar is not detected.")
        event_pages: Dict[str, Via] = {}
        for cal in calendars:
            for url, text in _external_links(cal.body, cal.referrer or cal.url, main_hosts, False):
                targets[url].append(Via(page=cal.referrer or cal.url, event=text, month=cal.label,
                                        target=url, event_url=url))
            for url, text in _on_site_links(cal.body, cal.referrer or cal.url, main_hosts):
                if url != cal.referrer and "calendar_" not in url:
                    event_pages.setdefault(url, Via(page=url, event=text, month=cal.label))
        log.info("Calendar links: %d on-site event pages, %d direct external links",
                 len(event_pages), len(targets))
        sources = [(by_final.get(u) or pages.get(u), via) for u, via in event_pages.items()]
        sources = [(p, via) for p, via in sources if p is not None]
        missing = len(event_pages) - len(sources)
        if missing:
            log.warning("%d event pages were not crawled (raise --max-pages / --max-depth)", missing)
    else:
        sources = [(p, Via(page=p.final_url)) for p in pages.values() if p.kind != "calendar"]

    # The same URL on a large share of pages (a sponsor, the site's own campaign
    # page) is site furniture. Shared *domains* are kept: many events can
    # legitimately link to, say, different Action Network pages.
    per_page = [(p, via, _external_links(p.body, p.final_url, main_hosts, True)) for p, via in sources]
    url_pages = Counter(u for _, _, links in per_page for u in {u for u, _ in links})
    common = {u for u, n in url_pages.items() if len(per_page) >= 10 and n > 0.3 * len(per_page)}
    for p, via, links in per_page:
        event = via.event or title_of(p)
        for url, _text in links:
            if url not in common:
                targets[url].append(Via(page=p.final_url, event=event, month=via.month, target=url,
                                        event_url=p.final_url if mode == "events" else ""))

    # The same event is listed on several calendar views; keep each (page, event, month) once.
    for url, vias in targets.items():
        targets[url] = list({(v.page, v.event, v.month): v for v in vias}.values())

    return filter_targets(targets, extra_skip, platform_hosts)


def filter_targets(targets: Dict[str, List[Via]], extra_skip: Set[str],
                   platform_hosts: Optional[Set[str]] = None) -> Dict[str, List[Via]]:
    """Drop links to social media and other hosts never worth crawling.

    The user's --follow-skip always wins; platform hosts (single pages that are
    read) are kept even if a broader skip entry would match them.
    """
    platform = PLATFORM_HOSTS if platform_hosts is None else platform_hosts

    def keep(url: str) -> bool:
        try:
            host = registered_host(urlparse(url).netloc)
        except ValueError:
            return False
        if not host or host_matches(host, extra_skip):
            return False
        if host_matches(host, platform):
            return True
        return host not in SKIP_EXACT and not host_matches(host, SKIP_HOSTS)
    return {u: v for u, v in targets.items() if keep(u)}


_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu", "or", "ne", "gv", "asso", "nom"}


def politeness_group(host: str) -> str:
    """Hosts that are probably one server: a.noblogs.org and b.noblogs.org share
    'noblogs.org'. Sites in one group are crawled one after another, never at once."""
    if re.fullmatch(r"[\d.]+", host) or ":" in host:
        return host
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in _SECOND_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])          # foo.org.uk, bar.co.uk
    return ".".join(parts[-2:])


def _why_unreachable(cr: Crawler) -> str:
    """Short reason why a linked site gave no page, from the crawler's outcomes."""
    o = cr.outcomes
    codes = sorted(k for k in o if isinstance(k, int))
    if o["robots-unreachable"]:
        if all(r.startswith("connection failed") for _, r in cr.robots_blocked):
            return "connection failed"
        return "robots.txt could not be read (treated as disallowed)"
    if 429 in codes:
        return "rate-limited (HTTP 429), retried on the next run"
    server = [c for c in codes if c >= 500]
    if server:
        return f"server error (HTTP {server[0]}), retried on the next run"
    if o["robots"] and not codes:
        return "robots.txt disallows"
    blocked = [c for c in codes if c in (401, 403)]
    if blocked:
        return f"blocked (HTTP {blocked[0]})"
    if codes:
        return f"{'not found' if codes[0] in (404, 410) else 'error'} (HTTP {codes[0]})"
    if o["connection"] or o["download"]:
        return "connection failed"
    if o["other type"]:
        return "not a web page or PDF"
    if o["error"]:
        return "error while reading"
    return ""


def crawl_linked(targets: Dict[str, List[Via]], cache: PageCache, base_cfg: CrawlConfig,
                 max_sites: Optional[int], max_pages: int, max_depth: int,
                 platform_hosts: Set[str], workers: int = 8,
                 blocked_hosts: Optional[Set[str]] = None,
                 progress: Optional[List[Site]] = None) -> List[Site]:
    """Crawl each linked website (grouped by host) with its own, smaller limits.

    max_sites: crawl at most this many sites, most-linked first; 0, None or a
    negative number means no limit. Sites left out are logged and listed with
    status 'not crawled (--follow-max-sites)'.
    workers: sites crawled at the same time (each host still gets one request at
    a time with the usual --delay; hosts on one server share a worker).
    blocked_hosts: hosts a moved site may never be redirected onto (e.g. the
    main site); skip and platform hosts and the other linked sites are added.
    progress: if given, each site is appended to it as soon as it is finished, so
    a caller interrupted by Ctrl+C still knows which sites were completed.
    The returned list is in the same order whatever the number of workers.
    """
    by_host: Dict[str, List[str]] = defaultdict(list)
    for url in targets:
        by_host[registered_host(urlparse(url).netloc)].append(url)
    ranked = sorted(by_host, key=lambda h: (-sum(len(targets[u]) for u in by_host[h]), h))
    limit = len(ranked) if not max_sites or max_sites < 0 else min(max_sites, len(ranked))
    if limit < len(ranked):
        log.warning("%d of %d linked websites will NOT be crawled because of --follow-max-sites %d "
                    "(the most-linked are crawled first; links from seed PDFs count too). "
                    "Use --follow-max-sites 0 for no limit.", len(ranked) - limit, len(ranked), max_sites)
        # Name only the first few: with a small limit the full list runs to hundreds of hosts,
        # and every one of them is in sites.csv anyway.
        left_out = ranked[limit:]
        more = f" and {len(left_out) - 20} more" if len(left_out) > 20 else ""
        log.warning("Not crawled: %s%s (all listed in sites.csv)", ", ".join(left_out[:20]), more)
    if base_cfg.render_js and workers > 1:
        log.info("--render-js: linked sites are crawled one at a time")
        workers = 1
    workers = max(1, workers)
    blocked = (SKIP_HOSTS | SKIP_EXACT | set(platform_hosts) | set(blocked_hosts or ())
               | set(ranked))

    stop = threading.Event()
    lock = threading.Lock()
    results: Dict[int, List[Site]] = {}
    status_of: Dict[str, str] = {}
    t0 = time.monotonic()
    done = [0]

    def crawl_one(i: int, host: str) -> List[Site]:
        urls = sorted(by_host[host])
        platform = host_matches(host, platform_hosts)
        via = [v for u in urls for v in targets[u]]
        page_cap = len(urls) if platform else max(max_pages, len(urls))
        cfg = CrawlConfig(**{**base_cfg.__dict__,
                             "start_urls": urls,
                             "max_pages": page_cap,
                             # PDFs have their own budget, so they never crowd out the
                             # linked pages; single platform pages read a few PDFs each.
                             "max_pdfs": min(3 * len(urls), page_cap) if platform else page_cap,
                             "max_depth": 0 if platform else max_depth,
                             "include_patterns": [], "include_subdomains": False,
                             "use_sitemap": False, "calendar": False,
                             "follow_start_redirects": not platform,
                             "blocked_hosts": blocked - {host}})
        log.info("Linked site %d/%d: %s (%s, %d start URL%s)", i + 1, limit, host,
                 "single pages" if platform else f"up to {cfg.max_pages} pages",
                 len(urls), "" if len(urls) == 1 else "s")
        cr = None
        try:
            cr = Crawler(cfg, cache)
            cr.stop = stop
            n = cr.crawl()
            why = "" if n else _why_unreachable(cr)
            status = "crawled" if n else f"unreachable: {why}" if why else "unreachable"
            if stop.is_set():          # Ctrl-C stopped this site before it was finished
                status = "interrupted (partly crawled)"
        except Exception as exc:  # noqa: BLE001 - one broken site should not stop the run
            log.error("Linked site %s failed: %s: %s", host, type(exc).__name__, exc)
            n, status = 0, f"failed: {exc}"
        aliases = sorted(cr.aliases) if cr is not None else []
        if platform:
            out = [Site(f"{host}{urlparse(u).path}".rstrip("/"), "linked", [u], True,
                        targets[u], status=status) for u in urls]
        else:
            out = [Site(host, "linked", urls, False, via, status=status, pages=n, aliases=aliases)]
        with lock:
            done[0] += 1
            status_of[host] = status
            elapsed = time.monotonic() - t0
            left = elapsed / done[0] * (limit - done[0])
            log.info("Linked site done (%d of %d finished): %s, %s (%d pages); %s elapsed, ~%s left",
                     done[0], limit, host, status, n, fmt_duration(elapsed), fmt_duration(left))
            if progress is not None:   # lets the caller record finished sites if the run is stopped
                progress.extend(out)
        return out

    # Hosts on one server form one job, run in ranked order; jobs run side by side.
    groups: Dict[str, List[int]] = {}
    for i in range(limit):
        groups.setdefault(politeness_group(ranked[i]), []).append(i)

    def run_group(indices: List[int]) -> None:
        for i in indices:
            if stop.is_set():
                return
            results[i] = crawl_one(i, ranked[i])

    if limit:
        log.info("Crawling %d linked websites, %d at a time", limit, min(workers, len(groups)))
    if workers == 1:
        for indices in groups.values():
            run_group(indices)
    else:
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="linked")
        futures = [pool.submit(run_group, indices) for indices in groups.values()]
        try:
            for f in futures:
                f.result()
        except BaseException:      # Ctrl-C: stop the running crawls at their next page
            stop.set()
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:      # Python < 3.9
                pool.shutdown(wait=False)
            raise
        pool.shutdown()

    sites: List[Site] = []
    for i, host in enumerate(ranked):
        if i in results:
            sites += results[i]
            continue
        urls = sorted(by_host[host])
        platform = host_matches(host, platform_hosts)
        reason = "not crawled (--follow-max-sites)" if i >= limit else "not crawled (stopped)"
        status_of[host] = reason
        if platform:
            sites += [Site(f"{host}{urlparse(u).path}".rstrip("/"), "linked", [u], True, targets[u],
                           status=reason) for u in urls]
        else:
            sites.append(Site(host, "linked", urls, False, [v for u in urls for v in targets[u]],
                              status=reason))
    if ranked:
        tally = Counter(st.split(":")[0].split(" (")[0] for st in status_of.values())
        log.info("Linked websites: %s (%s)", ", ".join(f"{n} {s}" for s, n in tally.most_common()),
                 fmt_duration(time.monotonic() - t0))
    return sites
