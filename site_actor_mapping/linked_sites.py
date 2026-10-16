"""
Follow websites linked from a site's events (or from all its pages), crawl
each one lightly, and keep track of which event led to which website.

The registry of sites is saved as ``sites.json`` in the output folder, so a
``--no-crawl`` re-run knows which cached pages belong to which site.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from crawler import CachedPage, CrawlConfig, Crawler, PageCache, normalise_url, registered_host

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
    "patreon.com", "wikipedia.org", "en.wikipedia.org", "google.com", "apple.com",
    "addtoany.com", "static.addtoany.com", "wordpress.org", "creativecommons.org",
}
# Platforms hosting many organisations: read only the linked page, never crawl further.
PLATFORM_HOSTS = {
    "actionnetwork.org", "eventbrite.com", "eventbrite.co.uk", "eventbrite.de", "eventbrite.fr",
    "eventbrite.it", "eventbrite.es", "eventbrite.ie", "eventbrite.be", "eventbrite.nl",
    "mobilize.us", "meetup.com", "tickettailor.com", "buytickets.at", "humanitix.com",
    "lu.ma", "partiful.com", "linktr.ee", "gofundme.com", "chuffed.org", "crowdfunder.co.uk",
    "change.org", "avaaz.org", "openpetition.de", "openpetition.eu", "you.38degrees.org.uk",
    "docs.google.com", "forms.gle", "framaforms.org", "drive.google.com", "medium.com",
    "substack.com", "blogspot.com", "wordpress.com", "github.com", "gitlab.com",
    "mailchi.mp", "eepurl.com", "campaign-archive.com", "notion.site", "padlet.com",
    "canva.site", "carrd.co", "wixsite.com", "squarespace.com", "weebly.com", "jimdofree.com",
}


@dataclass
class Via:
    page: str             # page on the main site where the link was found
    event: str = ""       # event title (link text in the calendar, or the event page title)
    month: str = ""       # calendar month the event appeared in, if any
    target: str = ""      # the linked URL itself


@dataclass
class Site:
    key: str                              # registered host, or host+path for platform pages
    role: str                             # "main" or "linked"
    start_urls: List[str] = field(default_factory=list)
    platform: bool = False
    via: List[Via] = field(default_factory=list)
    status: str = ""
    pages: int = 0

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
        url = normalise_url(urljoin(base, href))
        host = registered_host(urlparse(url).netloc)
        if not url or not host or host_matches(host, own_hosts):
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
        url = normalise_url(urljoin(base, href))
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
                    extra_skip: Set[str]) -> Dict[str, List[Via]]:
    """External URLs to follow, each with where it was found.

    mode "events": links in loaded calendar months, plus links in the main
    content of the on-site event pages those months link to.
    mode "all": links in the main content of every page on the main site.
    Links present on many pages (sponsors, partner strips) are left out.
    """
    pages = {p.url: p for p in cache if not p.is_pdf and p.body and p.status < 400
             and p.kind != "calendar-index"
             and host_matches(registered_host(urlparse(p.final_url).netloc), main_hosts)}
    by_final = {p.final_url: p for p in pages.values()}
    targets: Dict[str, List[Via]] = defaultdict(list)

    def title_of(p: CachedPage) -> str:
        soup = BeautifulSoup(p.body, "lxml")
        t = soup.find("h1") or soup.title
        return " ".join(t.get_text(" ").split()) if t else ""

    if mode == "events":
        calendars = [p for p in pages.values() if p.kind == "calendar"]
        # The calendar page itself already shows some months when it loads.
        for host_url in {c.referrer for c in calendars if c.referrer}:
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
                                        target=url))
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
                targets[url].append(Via(page=p.final_url, event=event, month=via.month, target=url))

    skip = SKIP_HOSTS | extra_skip
    return {u: v for u, v in targets.items()
            if not host_matches(registered_host(urlparse(u).netloc), skip)}


def crawl_linked(targets: Dict[str, List[Via]], cache: PageCache, base_cfg: CrawlConfig,
                 max_sites: int, max_pages: int, max_depth: int,
                 platform_hosts: Set[str]) -> List[Site]:
    """Crawl each linked website (grouped by host) with its own, smaller limits."""
    by_host: Dict[str, List[str]] = defaultdict(list)
    for url in targets:
        by_host[registered_host(urlparse(url).netloc)].append(url)
    ranked = sorted(by_host, key=lambda h: -sum(len(targets[u]) for u in by_host[h]))
    sites: List[Site] = []
    for i, host in enumerate(ranked):
        urls = sorted(by_host[host])
        platform = host_matches(host, platform_hosts)
        via = [v for u in urls for v in targets[u]]
        if i >= max_sites:
            sites.append(Site(host, "linked", urls, platform, via, status="not crawled (--follow-max-sites)"))
            continue
        cfg = CrawlConfig(**{**base_cfg.__dict__,
                             "start_urls": urls,
                             "max_pages": len(urls) if platform else max(max_pages, len(urls)),
                             "max_depth": 0 if platform else max_depth,
                             "include_patterns": [], "include_subdomains": False,
                             "use_sitemap": False, "calendar": False})
        log.info("Linked site %d/%d: %s (%s, %d start URL%s)", i + 1, min(len(ranked), max_sites),
                 host, "single pages" if platform else f"up to {cfg.max_pages} pages",
                 len(urls), "" if len(urls) == 1 else "s")
        try:
            n = Crawler(cfg, cache).crawl()
            status = "crawled" if n else "unreachable"
        except Exception as exc:  # noqa: BLE001 - one broken site should not stop the run
            log.warning("Linked site %s failed: %s", host, exc)
            n, status = 0, f"failed: {exc}"
        if platform:
            for u in urls:
                sites.append(Site(f"{host}{urlparse(u).path}".rstrip("/"), "linked", [u], True,
                                  targets[u], status=status))
        else:
            sites.append(Site(host, "linked", urls, False, via, status=status, pages=n))
    return sites
