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

import json
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from crawler import CachedPage, normalise_url

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
    source: str = ""                 # calendar page / seed PDF where it was listed

    @property
    def date(self) -> str:
        if self.dates:
            lo, hi = min(self.dates), max(self.dates)
            return lo if lo == hi else f"{lo} to {hi}"
        if self.date_text:
            return self.date_text
        return " / ".join(sorted(self.months))


@dataclass
class EventRef:
    name: str
    date: str
    link: str


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


class EventIndex:
    def __init__(self):
        self.events: Dict[str, Event] = {}

    def get_or_add(self, key: str) -> Event:
        key = normalise_url(key) or key
        if key not in self.events:
            self.events[key] = Event(key=key)
        return self.events[key]

    # ----------------------------------------------------------- calendars
    def add_calendar_page(self, page: CachedPage) -> None:
        soup = BeautifulSoup(page.body, "lxml")
        base = page.referrer or page.url
        for cell in soup.select("[data-mec-cell]"):
            raw = cell.get("data-mec-cell", "")
            iso = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if re.fullmatch(r"\d{8}", raw) else ""
            for a in cell.find_all("a", href=True):
                href = a["href"].strip()
                if href.startswith(("#", "javascript:")):
                    continue
                ev = self.get_or_add(urljoin(base, href))
                title = a.select_one(".mec-event-title, h4, h3") or a
                ev.name = ev.name or _clean(title.get_text(" "))
                if iso:
                    ev.dates.add(iso)
                if page.label and re.fullmatch(r"\d{4}-\d{2}", page.label):
                    ev.months.add(page.label)
                ev.source = ev.source or base
        # Calendars without date cells (click mode, other plugins): name + month only.
        if not soup.select("[data-mec-cell]"):
            for a in soup.select('a[href*="event"], .fc-event a[href], .tribe-events-calendar-month__calendar-event a[href]'):
                ev = self.get_or_add(urljoin(base, a["href"]))
                ev.name = ev.name or _clean(a.get_text(" "))
                if page.label and re.fullmatch(r"\d{4}-\d{2}", page.label):
                    ev.months.add(page.label)

    # --------------------------------------------------------- event pages
    def add_event_page(self, page: CachedPage) -> None:
        """Fill in name/date from an event's own page (schema.org Event, <time>)."""
        ev = self.events.get(page.final_url) or self.events.get(page.url)
        if ev is None:
            return
        soup = BeautifulSoup(page.body, "lxml")
        for sc in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(sc.string or "")
            except ValueError:
                continue
            for item in _walk_json(data):
                if isinstance(item, dict) and "Event" in str(item.get("@type", "")):
                    ev.name = ev.name or _clean(item.get("name", ""))
                    for k in ("startDate", "endDate"):
                        if isinstance(item.get(k), str) and re.match(r"\d{4}-\d{2}-\d{2}", item[k]):
                            ev.dates.add(item[k][:10])
        if not ev.name:
            h1 = soup.find("h1")
            ev.name = _clean(h1.get_text(" ")) if h1 else ""
        if not ev.dates:
            t = soup.find("time", datetime=True)
            if t and re.match(r"\d{4}-\d{2}-\d{2}", t["datetime"]):
                ev.dates.add(t["datetime"][:10])

    # ---------------------------------------------------------- seed PDFs
    def add_pdf_link(self, url: str, name: str, date_raw: str, date_iso: str, source: str) -> None:
        ev = self.get_or_add(url)
        ev.name = ev.name or name
        if date_iso:
            ev.dates.update(date_iso.split(" to "))
        elif date_raw and not ev.date_text:
            ev.date_text = date_raw
        ev.source = ev.source or source

    # ------------------------------------------------------------- lookup
    def ref(self, key: str, fallback_name: str = "", fallback_date: str = "") -> EventRef:
        ev = self.events.get(normalise_url(key) or key)
        if ev is None:
            return EventRef(fallback_name, fallback_date, key)
        return EventRef(ev.name or fallback_name, ev.date or fallback_date, ev.key)

    def named_in(self, text: str, candidates: Iterable[str]) -> List[EventRef]:
        """Events among `candidates` whose name appears in `text` (calendar cells,
        PDF lines)."""
        low = text.casefold()
        out = []
        for key in candidates:
            ev = self.events.get(key)
            if ev and len(ev.name) >= 4 and ev.name.casefold() in low:
                out.append(EventRef(ev.name, ev.date, ev.key))
        return out

    def rows(self) -> List[dict]:
        return [{"event": ev.name, "date": ev.date, "event_link": ev.key, "listed_on": ev.source,
                 "calendar_months": "; ".join(sorted(ev.months))}
                for ev in sorted(self.events.values(), key=lambda e: (e.date, e.name))]


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
_RANGE_SEP = r"(?:[-–—/]|au|à|to|till|til|until|bis|al|tot|t/m|–)"
_ENTRY_DATE_PATTERNS = [
    # (05-09.07)  (24.5.-1.6.)  (14.-26.08.)  (18.8 - 24.8)  (01.10 - 28.02)  29.05-01.06.
    re.compile(r"^\(?\s*(?P<d1>\d{1,2})(?!\d)\.?(?:(?P<m1>\d{1,2})(?!\d)\.?(?:(?P<y1>20\d\d|\d\d)(?!\d)\.?)?)?"
               rf"\s*{_RANGE_SEP}\s*(?P<d2>\d{{1,2}})(?!\d)\.\s*(?P<m2>\d{{1,2}})(?!\d)\.?"
               r"(?:(?P<y2>20\d\d|\d\d)(?!\d))?\.?"),
    # (24.05)  20.06.2026
    re.compile(r"^\(?\s*(?P<d1>\d{1,2})(?!\d)\.(?P<m1>\d{1,2})(?!\d)\.?(?:(?P<y1>20\d\d|\d\d)(?!\d))?"),
    # 24 of may – 1st of june / 29 juillet au 1er août / 15 au 18 juillet / 1 Aug - 8 Aug 2025
    re.compile(rf"^(?:du|from|vom|van|dal|del|de|från|fra)?\s*(?P<d1>\d{{1,2}})(?!\d){_ORD}\s*(?:of\s+|de\s+)?"
               rf"(?:(?P<mon1>{_MON})\.?\s*(?P<y1>20\d\d)?)?\s*{_RANGE_SEP}\s*"
               rf"(?P<d2>\d{{1,2}})(?!\d){_ORD}\s*(?:of\s+|de\s+)?(?P<mon2>{_MON})\.?,?\s*(?P<y2>20\d\d)?", re.I),
    # 30 juillet / 24th of may / 16th september 2025
    re.compile(rf"^(?:le|on|am|op|il|el|den|d\.)?\s*(?P<d1>\d{{1,2}})(?!\d){_ORD}\.?\s*(?:of\s+|de\s+)?"
               rf"(?P<mon1>{_MON})\.?,?\s*(?P<y1>20\d\d)?", re.I),
    # June 29-30, 2026 / May 24
    re.compile(rf"^(?P<mon1>{_MON})\.?\s+(?P<d1>\d{{1,2}})(?!\d){_ORD}(?:\s*[-–]\s*(?P<d2>\d{{1,2}})(?!\d){_ORD})?"
               r",?\s*(?P<y1>20\d\d)?", re.I),
]
_BULLETS = re.compile(r"^[\s–—\-•*·>]+")


def _iso(y, m, d) -> str:
    import datetime as _dt
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
        m1 = m1 or m2
        if not m1 or not (1 <= m1 <= 12) or (m2 and not 1 <= m2 <= 12):
            continue
        y1 = _year(g.get("y1")) or _year(g.get("y2")) or year
        y2 = _year(g.get("y2")) or y1
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


_HEADING = re.compile(rf"^(?:ann[ée]e|year|jahr|jaar|år|anno|año|ano)?\s*(?P<mon>{_MON})?\s*(?:/\s*{_MON})?\s*"
                      r"(?P<year>20\d\d)?\s*:?\s*$", re.I)


def heading_year(line: str) -> Tuple[bool, Optional[int]]:
    """Is this line a month/year heading ("ANNÉE 2025", "JUNE 2023", "Mai / May")?
    Returns (is_heading, year or None)."""
    t = re.sub(r"^[\W_]+|[\W_]+$", "", line.strip())
    if not t or len(t) > 40:
        return False, None
    m = _HEADING.match(t)
    if not m or not (m.group("mon") or m.group("year")):
        return False, None
    return True, int(m.group("year")) if m.group("year") else None
