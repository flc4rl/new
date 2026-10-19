"""
Events: names, dates and links, and which events each analysed page belongs to.

Events come from three places:
  * calendar months loaded by calendar_sweep (Modern Events Calendar cells
    carry exact dates: data-mec-cell="20260629"; a multi-day event appears in
    several cells, giving its start and end date);
  * event pages themselves (schema.org Event data, <time> elements);
  * link lists in seed PDFs (the link text / line, and a date on that line).

Every page is then tied to its event(s): an event page to itself, a page of a
linked website to the event(s) whose link led there, a PDF to the page it was
found on, and calendar or PDF paragraphs to the events named in them.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from collections import defaultdict
from collections.abc import Set as AbcSet
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

from crawler import CachedPage, normalise_url, safe_join

MONTHS = {
    # en
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
    "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    # fr
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "décembre": 12, "decembre": 12,
    # de
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "juni": 6, "juli": 7, "oktober": 10,
    "dezember": 12,
    # nl
    "januari": 1, "februari": 2, "maart": 3, "mei": 5, "augustus": 8,
    # it
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "dicembre": 12,
    # es / pt
    "enero": 1, "febrero": 2, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    "janeiro": 1, "fevereiro": 2, "março": 3, "maio": 5, "junho": 6, "julho": 7,
    "setembro": 9, "outubro": 10, "dezembro": 12,
    # da / sv
    "marts": 3, "maj": 5, "augusti": 8,
}
_MONTH_RE = "|".join(sorted(map(re.escape, MONTHS), key=len, reverse=True))
DATE_PATTERNS = [
    # 2026-06-29
    re.compile(r"(?<!\d)(?P<y>20\d\d)-(?P<m>[01]?\d)-(?P<d>[0-3]?\d)(?!\d)"),
    # 29.06.2026 / 29/06/2026 / 29-06-2026 (day first, as in Europe)
    re.compile(r"(?<!\d)(?P<d>[0-3]?\d)[./-](?P<m>[01]?\d)[./-](?P<y>20\d\d)(?!\d)"),
    # 29 June 2026 / 29. Juni 2026 / 29-30 June 2026 / 29 juin – 2 juillet 2026
    re.compile(rf"(?<!\d)(?P<d>[0-3]?\d)(?:\.|er|st|nd|rd|th)?(?:\s*[-–]\s*(?P<d2>[0-3]?\d)\.?)?\s+"
               rf"(?P<mon>{_MONTH_RE})\.?,?\s+(?P<y>20\d\d)", re.I),
    # June 29, 2026 / June 29-30, 2026
    re.compile(rf"(?P<mon>{_MONTH_RE})\.?\s+(?P<d>[0-3]?\d)(?:st|nd|rd|th)?(?:\s*[-–]\s*(?P<d2>[0-3]?\d))?,?\s+"
               rf"(?P<y>20\d\d)", re.I),
    # 29 June (no year)
    re.compile(rf"(?<!\d)(?P<d>[0-3]?\d)(?:\.|er|st|nd|rd|th)?\s+(?P<mon>{_MONTH_RE})\b", re.I),
]


def find_date(text: str, default_year: Optional[int] = None) -> Tuple[str, str]:
    """(raw date text, ISO date or '') for the first date found in `text`. A
    range within one month ("12-14 June 2026") gives "2026-06-12 to 2026-06-14"."""
    for pat in DATE_PATTERNS:
        m = pat.search(text or "")
        if not m:
            continue
        g = m.groupdict()
        month = int(g["m"]) if g.get("m") else MONTHS.get((g.get("mon") or "").lower().rstrip("."))
        year = int(g["y"]) if g.get("y") else default_year
        try:
            day = int(g["d"])
            if month and year and 1 <= month <= 12 and 1 <= day <= 31:
                iso = f"{year:04d}-{month:02d}-{day:02d}"
                if g.get("d2") and int(g["d2"]) > day:
                    iso += f" to {year:04d}-{month:02d}-{int(g['d2']):02d}"
                return m.group(0), iso
        except (TypeError, ValueError):
            pass
        return m.group(0), ""
    return "", ""


@dataclass
class Event:
    key: str                         # event page URL, or the external URL it links to
    name: str = ""
    dates: Set[str] = field(default_factory=set)   # ISO dates (calendar cells, page data)
    date_text: str = ""              # date as written, when no ISO date is known
    months: Set[str] = field(default_factory=set)
    source: str = ""                 # first calendar page / seed PDF where it was listed
    link: str = ""                   # the link shown for the event (key may add a date)
    sources: List[str] = field(default_factory=list)   # every calendar page / seed PDF listing it
    entry_text: str = ""             # the agenda entry's own text (seed PDFs)
    location: str = ""               # structured location from the event page (venue, address, country)
    country: Dict[str, object] = field(default_factory=dict)   # filled by EventIndex.locate()
    merged_from: List[str] = field(default_factory=list)     # keys of duplicates merged into this one

    def add_source(self, source: str) -> None:
        if source and source not in self.sources:
            self.sources.append(source)
        self.source = self.source or source

    @property
    def date(self) -> str:
        if self.dates:
            lo, hi = min(self.dates), max(self.dates)
            return lo if lo == hi else f"{lo} to {hi}"
        months = " / ".join(sorted(self.months))
        if self.date_text:
            return f"{months} ({self.date_text})" if months else self.date_text
        return months


@dataclass
class EventRef:
    name: str
    date: str
    link: str
    key: str = ""                    # EventIndex key ("" for placeholders and unknown events)
    # How the source was tied to the event: 'event' (a direct or sole link, a listing),
    # 'edition' (chosen among editions of one recurring event), 'site_level',
    # 'not_tied' or 'other_year' (the source is about another year). "" = 'event'.
    attribution: str = ""
    source_year: str = ""            # the year the source names, when it is not the event's


# --------------------------------------------------------------- series and years
# Words left out when two event names are compared: stop words of en/de/fr/es/it/nl/pt
# and words that any kind of event has in its name (camp, action, festival, a country).
_SERIES_STOP = set("""
the of and for in on to with near at by from a an this that our your their its is are be all new
der die das den dem des ein eine einer und oder für fur im in am an auf aus bei mit nach vom von zum zur
gegen über uber unter wir ihr sie es ist sind
de la le les des du d l un une et en au aux à a pour par sur avec dans près pres contre chez nous vous
el los las del al y o con por para sin sobre entre
il lo gli dei della delle di da dal nel nella e ed per con su tra fra contro
het een van voor met naar bij op tegen te om
os as do da dos das em no na nos nas um uma com pelo pela ao
camp camps climate climat clima klima klimaat action actions aktion aktionen day days journée journées
jornada international internationale internacional internazionale mass week semaine woche festival
edition édition ausgabe edición edizione
france germany allemagne deutschland belgium belgique uk england angleterre
""".split())
_BRACKET_TAG = re.compile(r"[(\[][^)\]]*[)\]]")


def _series_link(link: str) -> str:
    """A link without scheme, www, fragment ('#2023-08-10') and trailing slash."""
    u = (link or "").split("#", 1)[0].strip().lower()
    return re.sub(r"^https?://(?:www\.)?", "", u).rstrip("/")


def series_tokens(name: str) -> Set[str]:
    """Name words of 3 or more letters, without years, digits, bracket tags ([DE],
    (online)) and stop words."""
    name = _BRACKET_TAG.sub(" ", name or "")
    return {t for t in re.findall(r"[^\W\d_]{3,}", name.casefold()) if t not in _SERIES_STOP}


def same_series(a_name: str, a_link: str, b_name: str, b_link: str) -> bool:
    """Are two events editions of one recurring event? True when their links are the
    same (scheme, www, trailing slash and the '#date' of agenda keys ignored) or their
    names share enough words (Jaccard of at least 0.4 once years, digits, bracket tags
    and stop words are removed)."""
    la, lb = _series_link(a_link), _series_link(b_link)
    if la and la == lb:
        return True
    ta, tb = series_tokens(a_name), series_tokens(b_name)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.4


def series_components(refs: List["EventRef"]) -> List[List["EventRef"]]:
    """Connected components of `refs` under same_series (in the order given)."""
    parent = list(range(len(refs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(refs)):
        for j in range(i + 1, len(refs)):
            if find(i) != find(j) and same_series(refs[i].name, refs[i].link or refs[i].key,
                                                  refs[j].name, refs[j].link or refs[j].key):
                parent[find(j)] = find(i)
    groups: Dict[int, List[EventRef]] = {}
    for i, r in enumerate(refs):
        groups.setdefault(find(i), []).append(r)
    return list(groups.values())


# Upload folders name the month a file was uploaded, not the year it is about.
_UPLOAD_DIRS = re.compile(r"/(?:wp-content/uploads|uploads|files|sites/default/files)/(?:20\d\d)(?:[-/]\d\d)?(?=/)",
                          re.I)
_PERMALINK = re.compile(r"/(20\d\d)/(?:0[1-9]|1[0-2])(?:/(?:0[1-9]|[12]\d|3[01]))?(?=/)")
_SOURCE_YEAR = re.compile(r"(?<!\d)(20[0-2]\d|203[0-5])(?!\d)")
_YEAR_SPAN = re.compile(r"(?<!\d)(20[0-3]\d)\s*[-–/_]\s*(20[0-3]\d)(?!\d)")


def source_year(url: str, title: str = "", event_years: Iterable[int] = ()) -> Optional[int]:
    """The year (2000 to 2035) a source names in its URL path, file name or title, or
    None. Upload folders (wp-content/uploads/2019/05/, /uploads/2019/,
    sites/default/files/2019-05/, /files/2019/) and query strings are ignored, and a
    blog permalink date (/2024/10/01/) counts only when it is more than a year away from
    `event_years`. When several years appear the latest counts, unless one of them is
    one of `event_years` (the event's own years), which then counts; a span of years
    that contains the event's year ("2024-2027") counts as the event's own. Years more
    than two after the event are targets ("Kohleausstieg 2030"), not what a source is
    about, and are ignored; the title is percent-decoded first."""
    path = ""
    if url:
        try:
            path = unquote(urlparse(url).path or "")
        except ValueError:
            path = ""
    path = _UPLOAD_DIRS.sub("/", path)
    # Blog permalinks (/2024/10/01/slug/) give the day a post was published: a post of the
    # year before or after the event is still about it (a call, a report back).
    published = {int(y) for y in _PERMALINK.findall(path)}
    path = _PERMALINK.sub("/", path)
    try:
        title = unquote(title or "")    # "Esperanto%20de%20nivelo...%2028" is no year 2028
    except (TypeError, ValueError):
        title = title or ""
    text = path + " " + title
    own_years = set(event_years or ())
    # A span that contains the event's year is the event's own ("Beleidsplan_2024-2027").
    for a, b in _YEAR_SPAN.findall(text):
        if any(int(a) <= y <= int(b) for y in own_years):
            return max(y for y in own_years if int(a) <= y <= int(b))
    years = {int(y) for y in _SOURCE_YEAR.findall(text)}
    # Years well after the event are targets ("Kohleausstieg 2030", "klimaneutral 2035"),
    # not the year a source is about.
    if own_years:
        years = {y for y in years if y <= max(own_years) + 2}
    if not years and published and own_years:
        years = {y for y in published if not any(abs(y - e) <= 1 for e in own_years)}
    elif not years:
        years = published
    if not years:
        return None
    own = years & own_years
    return max(own) if own else max(years)


def event_years(date: str) -> List[int]:
    """The years an event date covers ("2023-06-30 to 2023-07-02" -> [2023])."""
    found = [int(y) for y in re.findall(r"(?<!\d)((?:19|20|21)\d\d)-\d\d", date or "")]
    return list(range(min(found), max(found) + 1)) if found else []


def _host(url: str) -> str:
    try:
        h = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def _day_runs(days: List[str], gap: int) -> List[List[str]]:
    """Sorted ISO days split where two days are more than `gap` days apart."""
    runs: List[List[str]] = []
    last = None
    for d in days:
        try:
            cur = _dt.date.fromisoformat(d)
        except ValueError:
            continue
        if last is None or (cur - last).days > gap:
            runs.append([])
        runs[-1].append(d)
        last = cur
    return runs


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# A day an event can be on: "9999-12-31" (some plugins' "no end date") is not one.
_PLAUSIBLE_DAY = re.compile(r"(?:18|19|20|21)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])")


class EventIndex:
    def __init__(self):
        self.events: Dict[str, Event] = {}
        self._name_index: Optional[List[Tuple[str, str]]] = None   # (casefolded name, key)
        self.date_conflicts = 0      # same key listed with different date ranges (first kept)
        # Calendar cells: link -> MEC event id ("" when none) -> days shown (for editions).
        self._cal: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
        self.editions: Dict[str, List[str]] = {}    # a calendar link split into editions -> their keys
        self.aliases: Dict[str, str] = {}           # key of a merged duplicate -> the kept event's key

    def get_or_add(self, key: str) -> Event:
        key = normalise_url(key) or key
        if key not in self.events:
            self.events[key] = Event(key=key)
            self._name_index = None
        return self.events[key]

    # ----------------------------------------------------------- calendars
    def add_calendar_page(self, page: CachedPage) -> None:
        self._name_index = None
        soup = BeautifulSoup(page.body, "lxml")
        base = page.referrer or page.url
        for cell in soup.select("[data-mec-cell]"):
            raw = cell.get("data-mec-cell", "")
            iso = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if re.fullmatch(r"\d{8}", raw) else ""
            for a in cell.find_all("a", href=True):
                href = a["href"].strip()
                if href.startswith(("#", "javascript:")):
                    continue
                url = safe_join(base, href)
                if not url:
                    continue
                ev = self.get_or_add(url)
                title = a.select_one(".mec-event-title, h4, h3") or a
                ev.name = ev.name or _clean(title.get_text(" "))
                if iso:
                    ev.dates.add(iso)
                    holder = a if a.get("data-event-id") else a.find_parent(attrs={"data-event-id": True})
                    eid = str(holder.get("data-event-id") or "") if holder is not None else ""
                    self._cal[ev.key][eid].add(iso)
                if page.label and re.fullmatch(r"\d{4}-\d{2}", page.label):
                    ev.months.add(page.label)
                ev.add_source(base)
        # Calendars without date cells (click mode, other plugins): name + month only.
        if not soup.select("[data-mec-cell]"):
            for a in soup.select('a[href*="event"], .fc-event a[href], .tribe-events-calendar-month__calendar-event a[href], '
                                 '.mec-calendar-day a[href], a[class*="event-single-link"][href]'):
                url = safe_join(base, a["href"])
                if not url:
                    continue
                ev = self.get_or_add(url)
                ev.name = ev.name or _clean(a.get_text(" "))
                if page.label and re.fullmatch(r"\d{4}-\d{2}", page.label):
                    ev.months.add(page.label)
                ev.add_source(base)

    def split_calendar_editions(self) -> int:
        """Calendar events that are several editions under one link. A link shown with
        two or more MEC event ids (data-event-id) is one event per id; a link without ids
        whose days have gaps of more than 31 days is one event per run of days. Each edition
        is keyed "<link>#<first day>" (the form agenda entries use); the link itself
        then resolves to all its editions (refs()). Call it once all calendar months
        are added. Returns the number of links split."""
        n = 0
        for key, by_id in list(self._cal.items()):
            ev = self.events.get(key)
            if ev is None or key in self.editions:
                continue
            ids = {i: d for i, d in by_id.items() if i and d}
            if len(ids) >= 2:
                runs = [sorted(d) for d in ids.values()]
            elif ids:
                continue     # one MEC id is one event (a repeating series keeps its id)
            else:
                runs = _day_runs(sorted(ev.dates), 31)
            runs = [r for r in runs if r]
            if len(runs) < 2:
                continue
            n += 1
            keys = []
            for run in sorted(runs):
                k = f"{key}#{run[0]}"
                new = self.events.get(k) or Event(key=k)
                new.name = new.name or ev.name
                new.link = new.link or ev.link or key
                new.dates.update(run)
                new.months.update(d[:7] for d in run)
                new.months.update(m for m in ev.months if not ev.dates)
                for src in ev.sources:
                    new.add_source(src)
                new.location = new.location or ev.location
                self.events[k] = new
                keys.append(k)
            del self.events[key]
            self.editions[key] = keys
            self._name_index = None
        return n

    def known(self, key: str) -> bool:
        """Is `key` an event key, a merged duplicate's key or a link split into editions?"""
        return any(k in self.events or k in self.aliases or k in self.editions
                   for k in (key, normalise_url(key) or key))

    def _resolve(self, key: str) -> List[str]:
        for k in (key, normalise_url(key) or key):
            k = self.aliases.get(k, k)
            if k in self.events:
                return [k]
            if k in self.editions:
                return [self.aliases.get(e, e) for e in self.editions[k] if self.aliases.get(e, e) in self.events]
        return []

    def refs(self, key: str, fallback_name: str = "", fallback_date: str = "",
             month: str = "") -> List[EventRef]:
        """Like ref(), but a link split into editions gives all of them (only those
        shown in `month` when that is given and one of them is)."""
        keys = self._resolve(key)
        if len(keys) > 1 and month:
            keys = [k for k in keys if month in self.events[k].months] or keys
        if not keys:
            return [self.ref(key, fallback_name, fallback_date)]
        return [self.ref(k, fallback_name, fallback_date) for k in keys]

    def merge_duplicates(self, main_hosts: Iterable[str] = (),
                         skip_host: Optional[Callable[[str], bool]] = None) -> int:
        """One event listed twice (in the calendar and an agenda PDF, or in two agenda
        entries with different links): events with the same start date whose links have
        the same host (without www) are merged. Social-media and platform hosts
        (`skip_host`) are left alone, and on the main site (`main_hosts`, where every
        event has its own page) the whole link is compared. The calendar's key is kept
        (else the first key in sorted order); listings, months and the agenda entry text
        are joined; merged_from lists the other keys, which still resolve (aliases).
        Call it after the index is built and before locate(). Returns the number merged."""
        mains = {h.lower()[4:] if h.lower().startswith("www.") else h.lower() for h in main_hosts if h}
        groups: Dict[tuple, List[Event]] = defaultdict(list)
        for ev in self.events.values():
            start = min(ev.dates) if ev.dates else ""
            link = ev.link or ev.key
            if not start or not re.match(r"https?://", link):
                continue
            host = _host(link)
            if not host or (skip_host is not None and skip_host(host)):
                continue
            groups[(_series_link(link) if host in mains else host, start)].append(ev)
        n = 0
        for _, evs in sorted(groups.items()):
            if len(evs) < 2:
                continue
            # the calendar's event first (its key is the event page), then by key
            evs.sort(key=lambda e: (not any(re.match(r"https?://", s) and not s.lower().endswith(".pdf")
                                            for s in e.sources), e.key))
            keep = evs[0]
            for ev in evs[1:]:
                for src in ev.sources:
                    keep.add_source(src)
                keep.months |= ev.months
                if len(keep.dates) <= 1 and ev.dates and min(ev.dates) == min(keep.dates):
                    keep.dates |= ev.dates
                keep.entry_text = keep.entry_text or ev.entry_text
                keep.location = keep.location or ev.location
                keep.date_text = keep.date_text or ev.date_text
                keep.merged_from += [ev.key, *ev.merged_from]
                self.aliases[ev.key] = keep.key
                for k in ev.merged_from:
                    self.aliases[k] = keep.key
                del self.events[ev.key]
                n += 1
        if n:
            self._name_index = None
        return n

    # --------------------------------------------------------- event pages
    def add_event_page(self, page: CachedPage) -> None:
        """Fill in name, date and location from an event's own page (schema.org
        Event data, the Modern Events Calendar location box, <time>)."""
        ev = self.events.get(page.final_url) or self.events.get(page.url)
        if ev is None:
            # a link split into editions: the page names and places all of them (its
            # dates are those of one edition, so they are not used)
            keys = self._resolve(page.final_url) or self._resolve(page.url)
            if len(keys) > 1:
                probe = Event(key=keys[0])
                self._fill_from_page(probe, page)
                for k in keys:
                    self.events[k].name = self.events[k].name or probe.name
                    self.events[k].location = self.events[k].location or probe.location
                self._name_index = None
            return
        self._name_index = None
        self._fill_from_page(ev, page)

    @staticmethod
    def _fill_from_page(ev: Event, page: CachedPage) -> None:
        soup = BeautifulSoup(page.body, "lxml")
        for sc in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(sc.string or "")
            except ValueError:
                continue
            for item in _walk_json(data):
                if isinstance(item, dict) and "Event" in str(item.get("@type", "")):
                    ev.name = ev.name or _clean(str(item.get("name", "")))
                    for k in ("startDate", "endDate"):
                        if isinstance(item.get(k), str) and _PLAUSIBLE_DAY.match(item[k]):
                            ev.dates.add(item[k][:10])
                    ev.location = ev.location or schema_location(item)
        if not ev.location:
            ev.location = mec_location(soup)
        if not ev.name:
            h1 = soup.find("h1")
            ev.name = _clean(h1.get_text(" ")) if h1 else ""
        if not ev.dates:
            t = soup.find("time", datetime=True)
            if t and _PLAUSIBLE_DAY.match(t["datetime"]):
                ev.dates.add(t["datetime"][:10])

    # ---------------------------------------------------------- seed PDFs
    def add_pdf_link(self, url: str, name: str, date_raw: str, date_iso: str, source: str,
                     link: str = "", entry_text: str = "") -> None:
        """An agenda entry. `url` is its key; `link` the URL to show (several
        editions of a recurring event share a link but are separate events).

        The same key listed twice (the French and English listing of one event,
        or the same event in both agendas) is one event listed on every source.
        Its date range is the first listing's: a later listing may add an end
        date to a start-only date, but never widens a range it disagrees with."""
        self._name_index = None
        ev = self.events.get(url) or self.events.setdefault(url, Event(key=url))
        ev.link = ev.link or link
        ev.name = ev.name or name
        ev.entry_text = ev.entry_text or entry_text
        if date_iso:
            rng = set(date_iso.split(" to "))
            if not ev.dates:
                ev.dates.update(rng)
            elif not rng <= ev.dates:
                if len(ev.dates) == 1 and min(rng) == min(ev.dates):
                    ev.dates.update(rng)          # the first listing had only the start date
                else:
                    self.date_conflicts += 1
        elif date_raw and not ev.date_text:
            ev.date_text = date_raw
        ev.add_source(source)

    # ------------------------------------------------------------- lookup
    def ref(self, key: str, fallback_name: str = "", fallback_date: str = "") -> EventRef:
        # The exact key first: an agenda entry key ("https://x.org/#2023-08-10")
        # must not resolve to the calendar event keyed by the bare link.
        ev = self.events.get(key) or self.events.get(normalise_url(key) or key)
        if ev is None:
            keys = self._resolve(key)      # a merged duplicate, or the latest edition of a link
            ev = self.events[keys[-1]] if keys else None
        if ev is None:
            return EventRef(fallback_name, fallback_date, key)
        return EventRef(ev.name or fallback_name, ev.date or fallback_date, ev.link or ev.key, ev.key)

    def named_in(self, text: str, candidates: Optional[Iterable[str]] = None,
                 month: str = "") -> List[EventRef]:
        """Events among `candidates` (default: all) whose name appears in `text`
        (calendar cells, PDF lines). With `month` (a calendar month YYYY-MM), editions
        of a link split into several events count only in the month they are shown."""
        if self._name_index is None:   # names casefolded once, not for every paragraph
            self._name_index = [(ev.name.casefold(), k) for k, ev in self.events.items()
                                if len(ev.name) >= 4]
        low = text.casefold()
        keys = None if candidates is None else (candidates if isinstance(candidates, (AbcSet, dict))
                                                else set(candidates))
        out = []
        for name, k in self._name_index:
            if (keys is None or k in keys) and name in low:
                ev = self.events[k]
                out.append(EventRef(ev.name, ev.date, ev.link or ev.key, ev.key))
        if month and len(out) > 1:
            by_link: Dict[str, List[EventRef]] = defaultdict(list)
            for r in out:
                by_link[r.link].append(r)
            out = [r for r in out if len(by_link[r.link]) < 2 or month in self.events[r.key].months
                   or not any(month in self.events[x.key].months for x in by_link[r.link])]
        return out

    def named_in_title(self, title: str) -> List[EventRef]:
        """Events whose name, without bracket tags ("[DE]", "(online)") and at least 12
        characters long, appears in a page title ("Takeaways: Cables of Resistance
        Conference - The Movement Hub"). Used for main-site stories tied to no event."""
        low = (title or "").casefold()
        out = []
        for k, ev in self.events.items():
            name = _BRACKET_TAG.sub(" ", ev.name or "")
            name = re.sub(r"\s+", " ", name).strip(" -–:,").casefold()
            if len(name) >= 12 and name in low:
                out.append(EventRef(ev.name, ev.date, ev.link or ev.key, ev.key))
        return out

    # ------------------------------------------------------------ location
    def locate(self) -> str:
        """Country of every event (R2), from the gazetteer module: the structured
        location of the event page first, then tags such as [DE], country and
        city names in the name and entry text, and the link's country domain as
        a last resort. Returns "" on success, else why no country was set."""
        try:
            import gazetteer   # written separately; optional
        except Exception as exc:  # noqa: BLE001 - a missing or broken module must not stop the run
            return f"gazetteer module not available ({type(exc).__name__}: {exc})"
        failed = 0
        for ev in self.events.values():
            link = ev.link or ev.key
            link = link if re.match(r"https?://", link) else ""
            try:
                res = _call_gazetteer(gazetteer, ev.name, ev.entry_text, link, ev.location)
            except Exception:  # noqa: BLE001 - one odd name must not stop the run
                failed += 1
                res = {}
            ev.country = res
        return f"country lookup failed for {failed} events" if failed else ""

    def rows(self, display=lambda u: u) -> List[dict]:
        """One row per event. `display` turns internal links (local seed-PDF
        paths) into the form written to files."""
        out = []
        for ev in sorted(self.events.values(), key=lambda e: (e.date, e.name, e.key)):
            out.append({
                "event": ev.name, "date": ev.date, "event_link": display(ev.link or ev.key),
                "event_key": display(ev.key),
                "listed_on": "; ".join(display(s) for s in (ev.sources or [ev.source])),
                "calendar_months": "; ".join(sorted(ev.months)),
                **country_columns(ev.country),
                "location_structured": ev.location,
                "entry_words": len(ev.entry_text.split()),
                "merged_from": "; ".join(display(k) for k in ev.merged_from),
            })
        return out


COUNTRY_FIELDS = ["event_country", "country_iso", "country_source", "location_text"]


def country_columns(res: Optional[Dict[str, object]]) -> Dict[str, str]:
    """The four country columns (English names, ISO codes, method, matched text)."""
    res = res or {}
    codes = [str(c) for c in res.get("countries") or []]
    names = [str(n) for n in res.get("names") or []] or codes
    return {"event_country": "; ".join(names), "country_iso": "; ".join(codes),
            "country_source": str(res.get("method") or "") if codes else "",
            "location_text": str(res.get("matched") or "") if codes else ""}


def _call_gazetteer(mod, name: str, entry_text: str, link: str, structured: str) -> Dict[str, object]:
    """gazetteer.event_country(name, entry_text, link, structured) -> {countries,
    names, method, matched}; older prototypes take (name, entry_text) and return
    (countries, method)."""
    fn = mod.event_country
    try:
        res = fn(name or "", entry_text or "", link=link or "", structured=structured or "")
    except TypeError:
        res = fn(name or "", entry_text or "")
    if isinstance(res, tuple):
        countries, method = (list(res) + ["", ""])[:2]
        res = {"countries": list(countries or []), "names": [], "method": method or "", "matched": ""}
    return dict(res or {})


def _text(v) -> str:
    if isinstance(v, dict):
        return _clean(str(v.get("name") or v.get("@id") or ""))
    return _clean(str(v or "")) if isinstance(v, (str, int, float)) else ""


def schema_location(item: dict) -> str:
    """schema.org Event location as one line: venue, street, town, region, country."""
    if "OnlineEventAttendanceMode" in str(item.get("eventAttendanceMode", "")) \
            and "Mixed" not in str(item.get("eventAttendanceMode", "")):
        return "Online"
    locs = item.get("location")
    locs = locs if isinstance(locs, list) else [locs]
    parts: List[str] = []
    for loc in locs:
        if isinstance(loc, str):
            parts.append(_clean(loc))
            continue
        if not isinstance(loc, dict):
            continue
        if "VirtualLocation" in str(loc.get("@type", "")):
            parts.append("Online")
            continue
        parts.append(_text(loc.get("name")))
        addr = loc.get("address")
        if isinstance(addr, dict):
            parts += [_text(addr.get(k)) for k in ("streetAddress", "addressLocality", "addressRegion",
                                                    "addressCountry")]
        else:
            parts.append(_text(addr))
    return ", ".join(dict.fromkeys(p for p in parts if p))[:300]


def mec_location(soup: BeautifulSoup) -> str:
    """The location box of a Modern Events Calendar event page (venue and address)."""
    box = soup.select_one(".mec-single-event-location, .mec-event-location")
    if box is None:
        return ""
    parts = [_clean(x.get_text(" ")) for x in box.select(".author, .fn, h6, .mec-address, address")]
    if not any(parts):
        heading = box.select_one("h3, .mec-events-single-section-title")
        text = _clean(box.get_text(" "))
        if heading is not None:
            text = text.replace(_clean(heading.get_text(" ")), "", 1).strip()
        parts = [text]
    return ", ".join(dict.fromkeys(p for p in parts if p))[:300]


def _walk_json(data):
    if isinstance(data, list):
        for x in data:
            yield from _walk_json(x)
    elif isinstance(data, dict):
        yield data
        for v in data.values():
            if isinstance(v, (list, dict)):
                yield from _walk_json(v)


# ---------------------------------------------------------------- agenda entries
_ORD = r"(?:st|nd|rd|th|er|e|ème|º|°)?"
_MON = rf"(?:{_MONTH_RE})(?![^\W\d_])"
_RANGE_SEP = r"(?:->|→|[-–—/&]|au|à|to|till|til|until|bis|al|tot|t/m|et|and|und|en|og|och|y|e)"
_ENTRY_DATE_PATTERNS = [
    # (05-09.07)  (24.5.-1.6.)  (14.-26.08.)  (18.8 - 24.8)  (01.10 - 28.02)  29.05-01.06.
    re.compile(r"^\(?\s*(?P<d1>\d{1,2})(?!\d)\.?(?:\s?(?P<m1>\d{1,2})(?!\d)\.?(?:(?P<y1>20\d\d|\d\d)(?!\d)\.?)?)?"
               rf"\s*{_RANGE_SEP}\s*~?(?P<d2>\d{{1,2}})(?!\d)\.\s?(?P<m2>\d{{1,2}})(?!\d)\.?"
               r"(?:(?P<y2>20\d\d|\d\d)(?!\d))?\.?"),
    # 11.12th April (the 11th and 12th of April, not 11 December)
    re.compile(rf"^\(?\s*(?P<d1>\d{{1,2}})(?!\d)\.(?P<d2>\d{{1,2}})(?!\d){_ORD}\s+(?P<mon2>{_MON})\.?,?\s*(?P<y2>20\d\d)?",
               re.I),
    # 29.03 - 12. April (day.month to day and month name)
    re.compile(rf"^\(?\s*(?P<d1>\d{{1,2}})(?!\d)\.(?P<m1>\d{{1,2}})(?!\d)\.?\s*{_RANGE_SEP}\s*"
               rf"(?P<d2>\d{{1,2}})(?!\d){_ORD}\.?\s*(?P<mon2>{_MON})\.?,?\s*(?P<y2>20\d\d)?", re.I),
    # (24.05)  20.06.2026
    re.compile(r"^\(?\s*(?P<d1>\d{1,2})(?!\d)\.(?P<m1>\d{1,2})(?!\d)\.?(?:(?P<y1>20\d\d|\d\d)(?!\d))?"),
    # 24 of may – 1st of june / 29 juillet au 1er août / 15 au 18 juillet / 1 Aug - 8 Aug 2025
    re.compile(rf"^(?:du|from|vom|van|dal|del|de|från|fra)?\s*(?P<d1>\d{{1,2}})(?!\d){_ORD}\.?\s*(?:of\s+|pf\s+|de\s+)?"
               rf"(?:(?P<mon1>{_MON})\.?\s*(?P<y1>20\d\d)?)?\s*{_RANGE_SEP}\s*"
               rf"(?P<d2>\d{{1,2}})(?!\d){_ORD}[,.]*\s*(?:of\s+|pf\s+|de\s+)?(?P<mon2>{_MON})\.?,?\s*(?P<y2>20\d\d)?", re.I),
    # 30 juillet / 24th of may / 16th september 2025
    re.compile(rf"^(?:le|on|am|op|il|el|den|d\.)?\s*(?P<d1>\d{{1,2}})(?!\d){_ORD}\.?\s*(?:of\s+|pf\s+|de\s+)?"
               rf"(?P<mon1>{_MON})\.?,?\s*(?P<y1>20\d\d)?", re.I),
    # June 29-30, 2026 / May 24
    re.compile(rf"^(?P<mon1>{_MON})\.?\s+(?P<d1>\d{{1,2}})(?!\d){_ORD}(?:\s*[-–]\s*(?P<d2>\d{{1,2}})(?!\d){_ORD})?"
               r",?\s*(?P<y1>20\d\d)?", re.I),
]
_BULLETS = re.compile(r"^[\s–—\-•*·>]+")


def _iso(y, m, d) -> str:
    try:
        return _dt.date(int(y), int(m), int(d)).isoformat()
    except (TypeError, ValueError):
        return ""


def _year(v: Optional[str]) -> Optional[int]:
    if not v:
        return None
    y = int(v)
    return y + 2000 if y < 100 else y


def normalise_date_text(text: str) -> str:
    """Fix the letter O typed for a zero in dates at the start of a line ("1-6.O6")."""
    return re.sub(r"(?<=[\d.])O(?=[\d.)])|(?<=[(\s])O(?=\d)", "0", text[:60]) + text[60:]


def parse_entry_date(line: str, year: Optional[int]) -> Tuple[str, str, str]:
    """Date at the start of an agenda entry: (raw text, start ISO, end ISO).
    The ISO dates are empty when no year is known (no heading, none written)."""
    text = normalise_date_text(_BULLETS.sub("", line))
    for pat in _ENTRY_DATE_PATTERNS:
        m = pat.match(text)
        if not m:
            continue
        g = m.groupdict()
        m1 = int(g["m1"]) if g.get("m1") and g["m1"].isdigit() else MONTHS.get((g.get("mon1") or "").lower().rstrip("."))
        m2 = int(g["m2"]) if g.get("m2") and str(g["m2"]).isdigit() else MONTHS.get((g.get("mon2") or "").lower().rstrip("."))
        d1, d2 = int(g["d1"]), int(g["d2"]) if g.get("d2") else None
        # "(29 - 02.09)": the first day has no month and is well after the second, so it
        # is in the month before (29 August to 2 September, a short span); "25 au 16 mai"
        # is left as written
        month_before = not m1 and bool(m2) and d2 is not None and d1 - d2 >= 14
        m1 = m1 or m2
        if not m1 or not (1 <= m1 <= 12) or (m2 and not 1 <= m2 <= 12):
            continue
        y1 = _year(g.get("y1")) or _year(g.get("y2")) or year
        y2 = _year(g.get("y2")) or y1
        if month_before:
            m1 = m1 - 1 or 12
            if m1 == 12 and y1:
                y1 -= 1
        if d2 is not None and y1 and y2 == y1 and (m2 or m1) < m1:
            y2 = y1 + 1   # 01.10 - 28.02 runs into the next year
        raw = m.group(0).strip(" (")
        if not y1:
            return raw, "", ""
        start = _iso(y1, m1, d1)
        end = _iso(y2, m2 or m1, d2) if d2 is not None else ""
        if not start:
            continue
        return raw, start, (end if end and end != start else "")
    return "", "", ""


# "(Sept) Cost of living ...", "(mid-June) ...", "(début juillet) ...": a month, no day.
_ENTRY_MONTH = re.compile(rf"^\(\s*(?:(?:early|mid|late|end|end of|début|debut|fin|mi|anfang|mitte|ende)[- ]+)?"
                          rf"(?P<mon>{_MON})\.?\s*(?P<y>20\d\d)?\s*\)", re.I)


def parse_entry_month(line: str) -> Tuple[str, Optional[int], Optional[int]]:
    """A month written in brackets at the start of an agenda entry ("(Sept) ..."):
    (raw text, month, year or None). ("", None, None) when there is none."""
    m = _ENTRY_MONTH.match(_BULLETS.sub("", line))
    if not m:
        return "", None, None
    return m.group(0).strip(" ()"), MONTHS.get(m.group("mon").lower().rstrip(".")), \
        int(m.group("y")) if m.group("y") else None


_HEADING = re.compile(rf"^(?:(?:ann[ée]e|year|jahr|jaar|år|anno|año|ano)(?:\s*/\s*(?:year|ann[ée]e))?)?\s*"
                      rf"(?P<mon>{_MON})?\s*(?:/\s*{_MON})?\s*(?P<year>20\d\d)?\s*:?\s*$", re.I)
# Comment timestamps on blog pages: "10 MARS 2023 À 12 H 23 MIN", "12 March 2023 at 14:05".
_TIMESTAMP = re.compile(rf"^\d{{1,2}}\s+{_MON}\s+(?P<year>20\d\d)\s+(?:à|at|um|om|alle|a las)\s+\d{{1,2}}\s*(?:h|:)\s*\d{{1,2}}",
                        re.I)
# Section banners: "|||||||| camps/actions 2025 ||||||||", "=== ONLINE AGENDAS ==="
_BANNER = re.compile(r"^[|=#*~_-]{3,}|[|=#*~_-]{3,}$")
# Blog furniture in an agenda printed from a WordPress page: the "Reply" link under each
# comment and the WordPress.com footer ("RÉPONDRE", "CRÉEZ UN SITE WEB OU UN BLOG
# GRATUITEMENT SUR WORDPRESS.COM.", "NE PAS VENDRE NI PARTAGER MES INFORMATIONS ...").
_BLOG_FURNITURE = re.compile(
    r"^(?:R[ÉE]PONDRE|REPLY|ANTWORTEN|ANTWOORDEN|RESPONDER|RISPONDI|RESPONDRE)\b(?:\s*\d+\.?)?\s*(?:$|\d|[A-ZÀ-Ý])"
    r"|^(?:CR[ÉE]EZ UN SITE WEB|CREATE A (?:FREE )?(?:WEBSITE|BLOG)|BLOG (?:AT|SUR) WORDPRESS|"
    r"NE PAS VENDRE NI PARTAGER|DO NOT SELL OR SHARE MY PERSONAL)"
    r"|WORDPRESS\.COM\.?\s*$")


def heading_info(line: str) -> Tuple[bool, Optional[int], Optional[int]]:
    """Is this line a month/year heading ("ANNÉE 2025", "Année / Year 2022",
    "JUNE 2023", "Mai / May")? Returns (is_heading, year, month)."""
    raw = line.strip()
    if not raw or len(raw) > 40 or raw.endswith((")", ".", ",", "-")) or "http" in raw or "/)" in raw:
        return False, None, None
    t = re.sub(r"^[\W_]+|[\W_]+$", "", raw)
    m = _HEADING.match(t)
    if not t or not m or not (m.group("mon") or m.group("year")):
        return False, None, None
    if not m.group("year") and not t[:1].isupper():
        return False, None, None   # "juin." at the end of a wrapped sentence is not a heading
    month = MONTHS.get(m.group("mon").lower()) if m.group("mon") else None
    return True, int(m.group("year")) if m.group("year") else None, month


def heading_year(line: str) -> Tuple[bool, Optional[int]]:
    is_heading, year, _ = heading_info(line)
    return is_heading, year


def comment_timestamp_year(line: str) -> Optional[int]:
    m = _TIMESTAMP.match(line.strip())
    return int(m.group("year")) if m else None


def is_banner(line: str) -> bool:
    """A section banner, or blog furniture (a comment's "Reply" link, the WordPress.com
    footer): either ends the current agenda entry."""
    t = line.strip()
    return bool(_BANNER.search(t) or _BLOG_FURNITURE.search(t))
