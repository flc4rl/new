"""
Load every month of an embedded events calendar.

Calendars usually show one month and fetch the others with JavaScript when
the visitor clicks "previous" / "next", so a plain crawl only sees the
current month. Two strategies:

  mec      Modern Events Calendar (WordPress, ``mec-*`` classes): detected
           automatically. Months are requested directly from the site's
           admin-ajax endpoint, exactly as the calendar's own JavaScript does
           when you click a month, so no browser is needed.
  click    Any other calendar: headless Chromium (playwright) clicks a
           "previous month" button you name with a CSS selector, saving the
           page after every click.

Each loaded month becomes a synthetic page (``<calendar url>?calendar_month=YYYY-MM``
or ``?calendar_click=N``) in the cache, so its event titles are analysed and
its links (event pages, external event websites) are crawled like any other.
The calendar URL is the calendar's canonical page (``/events/``, not the
paginated ``/events/page/2/``); when one page holds several calendars,
``&cal=<id>`` keeps their months apart. Each calendar is loaded once per run.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, List, Optional, Set, Tuple
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

MEC_EVENT_MARKERS = re.compile(r"mec-event-article|data-event-id")
EVENT_MARKERS = re.compile(r"mec-event-article|data-event-id|tribe-events-calendar-month__calendar-event"
                           r"|\bfc-event\b|\bvevent\b|\bevent-item\b|schema\.org/Event")


@dataclass
class MecConfig:
    ajax_url: str
    atts: str            # URL-encoded shortcode attributes, sent back verbatim
    skin_id: str
    sed_method: str
    image_popup: str
    year: int
    month: int


def calendar_id(cfg: MecConfig) -> str:
    """Stable identity of one calendar (endpoint, shortcode attributes, skin), whatever page shows it."""
    return hashlib.sha1(f"{cfg.ajax_url}|{cfg.atts}|{cfg.skin_id}".encode("utf-8")).hexdigest()[:10]


def _inline_scripts(soup: BeautifulSoup) -> Iterator[str]:
    """Inline script bodies, including base64 ``data:`` scripts (as some cache plugins emit)."""
    for sc in soup.find_all("script"):
        src = sc.get("src") or ""
        if src.startswith("data:") and ";base64," in src:
            try:
                yield base64.b64decode(src.split(",", 1)[1]).decode("utf-8", "replace")
            except ValueError:
                continue
        elif sc.string:
            yield sc.string


def detect_mec(html: str, page_url: str) -> List[MecConfig]:
    if "mecMonthlyView" not in html and "mec-" not in html:
        return []
    soup = BeautifulSoup(html, "lxml")
    configs = []
    for js in _inline_scripts(soup):
        for m in re.finditer(r"\.mecMonthlyView\(\s*\{(.*?)\}\s*\)\s*;", js, re.S):
            body = m.group(1)

            def field(name: str, default: str = "") -> str:
                f = re.search(rf"\b{name}\s*:\s*\"([^\"]*)\"", body)
                return f.group(1) if f else default

            active = re.search(r"active_month\s*:\s*\{\s*year\s*:\s*\"(\d{4})\"\s*,\s*month\s*:\s*\"(\d{1,2})\"", body)
            atts = field("atts")
            if not atts:
                continue
            ajax = field("ajax_url") or f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}/wp-admin/admin-ajax.php"
            today = dt.date.today()
            configs.append(MecConfig(
                ajax_url=ajax.replace("\\/", "/"), atts=atts, skin_id=field("id"),
                sed_method=field("sed_method", "0"), image_popup=field("image_popup", "0"),
                year=int(active.group(1)) if active else today.year,
                month=int(active.group(2)) if active else today.month,
            ))
    return configs


def _shift(year: int, month: int, delta: int) -> Tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def _html_strings(obj) -> List[str]:
    """All HTML-looking strings in a JSON response, whatever its key names."""
    if isinstance(obj, str):
        return [obj] if "<" in obj else []
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _html_strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _html_strings(v)]
    return []


def count_events(html: str, mec: bool = False) -> int:
    return len((MEC_EVENT_MARKERS if mec else EVENT_MARKERS).findall(html))


def sweep_mec(session: requests.Session, cfg: MecConfig, wait: Callable[[], None], timeout: float,
              start: Optional[Tuple[int, int]], end: Optional[Tuple[int, int]],
              empty_stop: int = 12, max_months: int = 240,
              have: Optional[Callable[[str], Optional[str]]] = None
              ) -> Iterator[Tuple[str, Optional[str], int]]:
    """Yield (YYYY-MM, html, n_events) for every month, walking backwards from the
    active month to `start` (or until `empty_stop` empty months in a row) and
    forwards to `end` (or until 3 empty months in a row).

    A month that could not be loaded is yielded as (YYYY-MM, None, -1), so the
    caller can record it and retry it later. Three failures in a row end that
    direction. `have(label)` may return the HTML of a month already cached, which
    is then used without a request.
    """
    def fetch(year: int, month: int) -> Optional[str]:
        if have is not None:
            cached = have(f"{year}-{month:02d}")
            if cached is not None:
                return cached
        wait()
        data = (f"action=mec_monthly_view_load_month&mec_year={year}&mec_month={month:02d}"
                f"&{cfg.atts}&apply_sf_date=0&navigator_click=true"
                f"&sed={quote(cfg.sed_method)}&image={quote(cfg.image_popup)}")
        try:
            resp = session.post(cfg.ajax_url, data=data, timeout=timeout, headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest"})
        except requests.RequestException as exc:
            log.warning("Calendar month %d-%02d failed: %s", year, month, exc)
            return None
        if resp.status_code >= 400:
            log.warning("Calendar month %d-%02d: HTTP %s", year, month, resp.status_code)
            return None
        try:
            return "\n".join(_html_strings(resp.json()))
        except ValueError:
            return resp.text if "<" in resp.text else None

    if start and end and start > end:
        log.warning("--calendar-from is after --calendar-to; no calendar months loaded")
        return
    done = 0
    for direction, stop_after in ((-1, empty_stop), (1, 3)):
        if direction < 0:  # backwards from the shown month, or from `end` if that is earlier
            year, month = min((cfg.year, cfg.month), end) if end else (cfg.year, cfg.month)
        else:              # forwards from the month after it, or from `start` if that is later
            year, month = _shift(cfg.year, cfg.month, 1)
            if start and (year, month) < start:
                year, month = start
        empty = failures = 0
        while done < max_months:
            if direction < 0 and start and (year, month) < start:
                break
            if direction > 0 and end and (year, month) > end:
                break
            html = fetch(year, month)
            done += 1
            if html is None:
                yield f"{year}-{month:02d}", None, -1
                failures += 1
                if failures >= 3:
                    log.warning("Calendar endpoint failed for 3 months in a row; stopping this "
                                "direction (run again later, or try --calendar-click)")
                    break
            else:
                failures = 0   # only consecutive failures end a direction
                n = count_events(html, mec=True)
                yield f"{year}-{month:02d}", html, n
                empty = 0 if n else empty + 1
                bounded = start if direction < 0 else end
                if not bounded and empty >= stop_after:
                    break
            year, month = _shift(year, month, direction)


def sweep_by_clicking(url: str, selector: str, clicks: int, user_agent: str,
                      timeout: float) -> Iterator[Tuple[str, str, int]]:
    """Open `url` in headless Chromium and click `selector` up to `clicks` times,
    yielding (click number, page HTML, n_events) after each click."""
    from playwright.sync_api import sync_playwright  # optional dependency
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(user_agent=user_agent)
            page.goto(url, timeout=timeout * 1000, wait_until="networkidle")
            html = page.content()
            yield "0", html, count_events(html)
            for i in range(1, clicks + 1):
                target = page.locator(selector)
                if target.count() == 0:
                    log.info("Calendar: no element matches %r after %d clicks; stopping", selector, i - 1)
                    break
                target.first.click(timeout=timeout * 1000)
                page.wait_for_load_state("networkidle", timeout=timeout * 1000)
                page.wait_for_timeout(500)
                html = page.content()
                yield str(i), html, count_events(html)
        finally:
            browser.close()


def parse_month(value: Optional[str]) -> Optional[Tuple[int, int]]:
    if not value:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", value.strip())
    if not m:
        raise ValueError(f"expected YYYY-MM, got {value!r}")
    return int(m.group(1)), int(m.group(2))


def month_window(calendar_from: Optional[str], calendar_to: Optional[str]
                 ) -> Optional[Callable[[object], bool]]:
    """Predicate for cached pages: False for a calendar month outside
    --calendar-from/--calendar-to (so a reused output folder does not mix in
    months from an earlier, wider run). None when no window is set.

    Pages that are not calendar months, and click-sweep views (labelled by
    click number), are always kept.
    """
    lo, hi = parse_month(calendar_from), parse_month(calendar_to)
    if not lo and not hi:
        return None

    def keep(page) -> bool:
        if getattr(page, "kind", "") != "calendar":
            return True
        m = re.fullmatch(r"(\d{4})-(\d{2})", getattr(page, "label", "") or "")
        if not m:
            return True
        ym = (int(m.group(1)), int(m.group(2)))
        return (not lo or ym >= lo) and (not hi or ym <= hi)
    return keep


_PAGINATED = re.compile(r"/page/\d+/?(?=[?#]|$)")


def duplicate_calendar_months(pages: Iterable[object]) -> Set[str]:
    """URLs of cached calendar months that repeat a month already loaded from another
    page of the same calendar (finding 28). Earlier versions swept the calendar again
    from every paginated archive page (/events/page/2/), so an old cache can hold
    /events/page/2/?calendar_month=2026-04 next to /events/?calendar_month=2026-04.

    Pages are visited shortest referrer first; a month page is a duplicate when the
    same label has already been seen with the same body, or at the same URL without
    the /page/N/ part. Pages that are not calendar months are ignored."""
    cal = [p for p in pages if getattr(p, "kind", "") == "calendar"]
    cal.sort(key=lambda p: (len(getattr(p, "referrer", "") or ""), p.url))
    seen: Set[tuple] = set()
    dup: Set[str] = set()
    for p in cal:
        label = getattr(p, "label", "") or ""
        body = hashlib.sha1((getattr(p, "body", "") or "").encode("utf-8", "replace")).hexdigest()
        keys = {("body", label, body), ("url", label, _PAGINATED.sub("/", p.url))}
        if keys & seen:
            dup.add(p.url)
        else:
            seen |= keys
    return dup
