"""
Read activist agendas / link lists from PDFs ("seed PDFs").

A PDF is read line by line and split into entries. An entry starts at a line
that begins with a date ("(05-09.07) Code Rood ...", "– 29 juillet au 1er août :
Anti-Colonial Attack! ...", "25 Jul 2025 - 27 Jul 2025 ...") and runs until the
next one. Month/year headings ("ANNÉE 2025", "JUNE 2023") give the year for
dates written without one. Every link (clickable or written out) belongs to
the entry it sits in, and so does every line of description, so themes and
organisations found in the PDF can be tied to the right event.
"""

from __future__ import annotations

import calendar
import io
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

import requests

from crawler import normalise_url
from events import (comment_timestamp_year, find_date, heading_info, is_banner,
                    normalise_date_text, parse_entry_date, parse_entry_month)

log = logging.getLogger(__name__)

# A URL stops where another one starts ("lesresitantes.fr/https://www.helloasso.com/...").
URL_IN_TEXT = re.compile(r"(?:https?://|www\.)(?:(?!https?://)[^\s<>\"')\]])+", re.I)
# Domains written without http/www: "radicalbookfairleipzig.org", "framaforms.org/inscription-..."
BARE_URL = re.compile(r"(?<![@\w./:-])(?:[a-z0-9-]+\.)+(?:org|net|com|eu|de|fr|nl|be|at|it|es|dk|se|uk|info|ch|"
                      r"pt|pl|cz|no|online|events|blog|io)(?:/(?:(?!https?://)[^\s<>\"')\]])*)?(?![\w@])", re.I)
# Month labels for entries with no exact date ("May 2023"): English, whatever the agenda's language.
MONTH_NAMES = {i: calendar.month_name[i] for i in range(1, 13)}
VALID_HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$", re.I)
# Links that are part of the PDF's own page furniture, never events.
JUNK_LINK = re.compile(r"^https?://(?:www\.)?(?:wordpress\.com|wp\.me|gravatar\.com|riseup\.net|pad\.riseup\.net)/?$",
                       re.I)
URLISH = re.compile(r"(?:https?://|www\.)|\b[\w-]+\.(?:org|com|net|eu|de|fr|nl|be|at|it|es|dk|se|uk|info|io)\b|/\S", re.I)


# French inclusive plurals look like Spanish domains: "les migrants.es", "Amis.es de Silence".
# A bare one-word ".es" match without www. or a path is read as a word, not a link
# ("kultur-an-der-kante.de" and "casapalestina.es/stopgenocide12a/" stay links).
_INCLUSIVE_PLURAL = re.compile(r"[^\W\d_]+\.es", re.I)


def _is_word_not_url(match: str) -> bool:
    return bool(_INCLUSIVE_PLURAL.fullmatch(match))


def _strip_bare_urls(text: str) -> str:
    return BARE_URL.sub(lambda m: m.group(0) if _is_word_not_url(m.group(0)) else " ", text)


def pdf_name(uri: str) -> str:
    """File name of a seed PDF, decoded ("Les échos du vent.pdf", not "Les%20e%CC%81chos...")."""
    p = urlparse(uri)
    return unicodedata.normalize("NFC", unquote(Path(p.path).name)) or uri


_FILE_URI = re.compile(r"file://[^\s|;#]*(?:#[^\s|;]*)?")


def pdf_display(value: str) -> str:
    """How a local seed PDF is shown in output files: "pdf:<file name>" (plus the
    "#entry" part of event keys) instead of a percent-encoded file:// path that
    contains the user's folders. Web URLs are returned unchanged; several values
    joined with " | " or "; " are each converted."""
    if not value or "file:" not in value:
        return value

    def one(m: "re.Match") -> str:
        p = urlparse(m.group(0))
        return f"pdf:{pdf_name(m.group(0))}" + (f"#{p.fragment}" if p.fragment else "")
    return _FILE_URI.sub(one, value)


def _title_like(line: str) -> bool:
    """A short heading-like line that could be an entry's title ("NoMinesLand - Anti-extractivist Camp")."""
    t = line.strip()
    return (4 <= len(t) < 80 and (t[:1].isupper() or not t[:1].isalnum()) and not URLISH.search(t) and ":" not in t
            and not t.endswith((")", "-", ",", "(")) and not parse_entry_date(t, None)[0])


@dataclass
class PdfLink:
    pdf: str          # file URI or URL of the PDF
    page: int
    url: str
    anchor: str       # text inside the link area
    line: str         # the line the link is on
    event: str        # the entry's name
    date_raw: str
    date_iso: str     # "2025-07-29" or "2025-07-29 to 2025-08-01" ("" if the year is unknown)
    entry: int = -1   # index into the PDF's entries


@dataclass
class Entry:
    pdf: str
    index: int
    page: int
    lines: List[str] = field(default_factory=list)
    date_raw: str = ""
    start: str = ""
    end: str = ""
    year_known: bool = False
    name: str = ""
    links: List[str] = field(default_factory=list)
    month: str = ""   # "2025-07" for entries with no exact date (from the month heading)

    @property
    def date_iso(self) -> str:
        if self.start:
            return f"{self.start} to {self.end}" if self.end else self.start
        return self.month


def load_pdf_bytes(source: str, timeout: float = 30.0) -> bytes:
    if re.match(r"https?://", source):
        resp = requests.get(source, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    return Path(source).expanduser().read_bytes()


def _has_name_text(lines: List[str], date_raw: str) -> bool:
    """Would these lines still give the entry a name (text besides its date and links)?"""
    text = normalise_date_text(" ".join(lines)).replace(date_raw, " ", 1)
    text = _strip_bare_urls(URL_IN_TEXT.sub(" ", text))
    return len(re.sub(r"[\W\d_]+", "", text)) >= 4


def _clean_url(raw: str) -> Optional[str]:
    raw = raw.rstrip(".,;:!?»”")
    if raw.endswith(")") and raw.count("(") < raw.count(")"):
        raw = raw.rstrip(")")
    url = normalise_url(raw if "://" in raw else "https://" + raw)
    if not url:
        return None
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
    if not VALID_HOST.match(host) or JUNK_LINK.match(url):
        return None
    return url


def _entry_name(entry: Entry) -> str:
    text = normalise_date_text(re.sub(r"^[\s–—\-•*·>]+", "", " ".join(entry.lines)))
    opened = text.startswith("(")
    before = ""
    if entry.date_raw and entry.date_raw in text:
        idx = text.index(entry.date_raw)
        before, text = text[:idx], text[idx + len(entry.date_raw):]   # a title line may precede the date
        if opened and ")" in text[:40]:          # "(13.01 onwards) ...", "(09.09-???) ..."
            text = text[text.index(")") + 1:]
    # Leftover date words before the title's colon: "until ? : March to Gaza",
    # "au 1/2 octobre : Rassemblement ...", "à fin septembre : Ecotopia ..."
    lead = re.match(r"^[\s,.;-]*([^:]{0,30}?)\s*:(?!//)\s*(?=\S)", text)   # not "https:"
    if lead and (not lead.group(1) or not lead.group(1)[:1].isupper()):
        text = text[lead.end():]
    text = f"{before.strip()} {text}" if before.strip(" ([") else text
    first_url = URL_IN_TEXT.search(text)
    if first_url and first_url.start() > 8:       # the name ends where the first link starts
        text = text[:first_url.start()]
    text = re.sub(r"\(\s*(?:https?://|www\.)[^)]*\)", " ", text)   # "(https://...)" duplicates
    text = _strip_bare_urls(URL_IN_TEXT.sub(" ", text))
    text = re.sub(r"^[\s()\[\]:\-–—,.;/>]+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" :-–—,;/>(")
    # An unmatched ")" near the start is the end of a bracketed note after the date:
    # "(14.-16. 6. (probably) Tipping Points ..." / "(27.08 - 02.09) (Italy)".
    note = re.match(r"^([^()]{1,25})\)\s*", text)
    if note:
        rest = text[note.end():]
        text = rest if len(re.sub(r"[\W\d_]+", "", rest)) >= 4 else f"({note.group(1).strip()})"
    if len(text) > 160:
        cut = re.search(r"(?<=[.;])\s", text[60:200])
        text = text[:60 + cut.start()] if cut else text[:160].rsplit(" ", 1)[0] + " …"
    return text


_DOMAIN_ONLY = re.compile(r"(?:https?://)?(?:www\.)?[\w-]+(?:\.[\w-]+)+/?", re.I)


def _fallback_name(entry: Entry, name: str = "") -> str:
    """A name for an entry whose text gave none, only a link ("endfossil.com") or only
    a bracketed note ("(Italy)"): its first title-like line, else the host of its
    first link (plus the note), else a placeholder naming its date."""
    for line in entry.lines:
        t = normalise_date_text(line)
        if entry.date_raw and entry.date_raw in t:
            t = t.replace(entry.date_raw, " ", 1)
        t = re.sub(r"^[\s()\[\]:\-–—,.;/>]+", "", t).strip()
        if _title_like(t):
            return t
    host = re.sub(r"^www\.", "", urlparse(entry.links[0]).netloc) if entry.links else ""
    if name.startswith("("):
        return f"{host} {name}".strip()
    if name:
        return name
    return host or f"Untitled agenda entry {entry.date_raw}".strip()


def extract_entries(source: str) -> Tuple[List[Entry], List[PdfLink], List[List[str]], Dict[int, List[int]]]:
    """Entries, links, the lines of each page, and for each page the entry index of
    each non-empty line (-1 before the first entry)."""
    import pdfplumber
    data = load_pdf_bytes(source)
    entries: List[Entry] = []
    links: List[PdfLink] = []
    page_lines: List[List[str]] = []
    line_entry: Dict[int, List[int]] = {}
    year: Optional[int] = None
    month: Optional[int] = None
    current: Optional[Entry] = None

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            raw_lines = [(ln["top"], ln["bottom"], ln["text"]) for ln in (page.extract_text_lines() or [])
                         if ln["text"].strip()]
            page_lines.append([t for _, _, t in raw_lines])
            owners: List[int] = []
            headings: List[bool] = []
            for i, (_, _, text) in enumerate(raw_lines):
                ts_year = comment_timestamp_year(text)
                if is_banner(text) or ts_year:
                    # Section banners ("|||| ONLINE AGENDAs ||||") and blog-comment
                    # timestamps end the current entry; a timestamp also gives the year.
                    if ts_year:
                        year = ts_year
                    month, current = None, None
                    owners.append(-1)
                    headings.append(True)
                    continue
                is_heading, hy, hm = heading_info(text)
                if is_heading:
                    if hy:
                        year = hy
                    month = hm
                    current = None
                    owners.append(-1)
                    headings.append(True)
                    continue
                headings.append(False)
                raw, start, end = parse_entry_date(text, year)
                undated = None
                u_year, u_month = year, month
                if not raw:
                    bullet = re.match(r"^\s*[–—]\s+(?P<when>[^:]{3,80}?)\s*:\s*\S", text)
                    m_raw, m_month, m_year = parse_entry_month(text)
                    if m_raw:
                        # "(Sept) Cost of living & climate marches": a month, no day
                        undated, u_month, u_year = m_raw, m_month, m_year or year
                    elif bullet:
                        # "– Début juillet jusqu’à fin septembre : Ecotopia bike tour ..."
                        undated = bullet.group("when")
                    elif current is None and month and (URL_IN_TEXT.search(text) or BARE_URL.search(text)):
                        # a link under a month heading, outside any dated entry
                        undated = f"{MONTH_NAMES.get(month, month)} {year or ''}".strip()
                if undated is not None:
                    d_raw, d_iso = find_date(undated, year)
                    new = Entry(source, len(entries), pno, [text], undated, d_iso.split(" to ")[0] if d_iso else "",
                                d_iso.split(" to ")[1] if " to " in d_iso else "", year_known=bool(d_iso),
                                month=f"{u_year}-{u_month:02d}" if u_year and u_month else "")
                    entries.append(new)
                    current = new
                    owners.append(current.index)
                    continue
                if raw:
                    new = Entry(source, len(entries), pno, [text], raw, start, end, year_known=bool(start))
                    # "Title line" then "date line" (entries pasted from other calendars,
                    # "25 Jul 2025 - 27 Jul 2025 Place"): the title line above joins this entry.
                    after_date = normalise_date_text(re.sub(r"^[\s–—\-•*·>(]+", "", text))
                    after_date = after_date[after_date.find(raw) + len(raw):] if raw in after_date else ""
                    pasted_style = not re.match(r"^\s*[):]", after_date) and len(after_date.strip()) < 50
                    if pasted_style and i > 0 and not headings[i - 1] and _title_like(raw_lines[i - 1][2]):
                        prev = raw_lines[i - 1][2]
                        if owners[-1] == -1:
                            new.lines.insert(0, prev)
                            owners[-1] = new.index
                        elif current is not None and owners[-1] == current.index and len(current.lines) >= 2 \
                                and _has_name_text(current.lines[:-1], current.date_raw):
                            current.lines.pop()
                            new.lines.insert(0, prev)
                            owners[-1] = new.index
                    entries.append(new)
                    current = new
                elif current is not None:
                    current.lines.append(text)
                owners.append(current.index if current is not None else -1)
            line_entry[pno] = owners

            def owner_at(top: float, bottom: float) -> int:
                mid = (top + bottom) / 2
                for k, (a, b, _) in enumerate(raw_lines):
                    if a - 2 <= mid <= b + 2:
                        return owners[k]
                return -1

            # 1. clickable links
            annotated = []
            for h in page.hyperlinks or []:
                url = _clean_url((h.get("uri") or "").strip())
                if not url:
                    continue
                try:
                    box = (max(h["x0"], 0), max(h["top"], 0), min(h["x1"], page.width),
                           min(h["bottom"], page.height))
                    anchor = re.sub(r"\s+", " ", page.within_bbox(box).extract_text() or "").strip()
                except ValueError:
                    anchor = ""
                ent = owner_at(h["top"], h["bottom"])
                line = next((t for a, b, t in raw_lines if a - 2 <= (h["top"] + h["bottom"]) / 2 <= b + 2), "")
                links.append(PdfLink(source, pno, url, anchor, line, "", "", "", ent))
                annotated.append(url)

            # 2. URLs written out in the text (re-joined when a line wrap broke them)
            for k, (top, bottom, text) in enumerate(raw_lines):
                masked = URL_IN_TEXT.sub(lambda m: " " * len(m.group(0)), text)
                for m in BARE_URL.finditer(masked):
                    if _is_word_not_url(m.group(0)):      # "migrants.es": a French plural
                        continue
                    url = _clean_url(m.group(0))
                    if url and not any(url.split("://", 1)[-1][:30] in a for a in annotated):
                        links.append(PdfLink(source, pno, url, "", text, "", "", "", owners[k]))
                for m in URL_IN_TEXT.finditer(text):
                    raw = m.group(0)
                    rest = text[m.end():]
                    if raw.endswith("-") and rest[:1] == " ":          # "ende- gelaende.org"
                        nxt = re.match(r"\s(\S+)", rest)
                        raw += nxt.group(1) if nxt else ""
                    elif m.end() == len(text) and k + 1 < len(raw_lines):  # continues on next line
                        nxt = raw_lines[k + 1][2].split(" ", 1)[0]
                        if (raw.endswith(("-", "/", "?", "=", "&", "_")) or re.match(r"^[\w%=&?/.#-]*[/=&%][\w%=&?/.#-]*$", nxt)) \
                                and not parse_entry_date(raw_lines[k + 1][2], year)[0]:
                            raw += nxt
                    url = _clean_url(raw)
                    if not url:
                        continue
                    stem = url.split("://", 1)[-1][:40]
                    if any(stem in a or a.split("://", 1)[-1][:40] in url for a in annotated):
                        continue   # the clickable version of the same link
                    links.append(PdfLink(source, pno, url, "", text, "", "", "", owners[k]))

    # Names, dates and links per entry
    seen = set()
    unique: List[PdfLink] = []
    for ln in links:
        if (ln.entry, ln.url) in seen:
            continue
        seen.add((ln.entry, ln.url))
        if 0 <= ln.entry < len(entries):
            entries[ln.entry].links.append(ln.url)
        else:
            ln.event = ln.anchor or re.sub(r"\s+", " ", URL_IN_TEXT.sub(" ", ln.line)).strip() or ln.url
        unique.append(ln)
    for e in entries:
        e.name = _entry_name(e)
        if not e.name or _DOMAIN_ONLY.fullmatch(e.name) or re.fullmatch(r"\([^()]*\)", e.name):
            e.name = _fallback_name(e, e.name)
    for ln in unique:
        if 0 <= ln.entry < len(entries):
            e = entries[ln.entry]
            ln.event, ln.date_raw, ln.date_iso = e.name, e.date_raw, e.date_iso
    no_year = sum(1 for e in entries if not e.year_known)
    log.info("Seed PDF %s: %d entries, %d links on %d pages%s", source, len(entries), len(unique),
             len(page_lines), f" ({no_year} entries without a known year)" if no_year else "")
    return entries, unique, page_lines, line_entry


def extract_links(source: str) -> Tuple[List[PdfLink], str]:
    """Links and the PDF text (pages separated by form feeds, one entry line per line)."""
    _, links, page_lines, _ = extract_entries(source)
    return links, "\f".join("\n".join(p) for p in page_lines)


def entries_to_json(entries: List[Entry], line_entry: Dict[int, List[int]]) -> dict:
    return {"entries": [asdict(e) for e in entries],
            "line_entry": {str(k): v for k, v in line_entry.items()}}
