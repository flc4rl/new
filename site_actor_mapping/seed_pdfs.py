"""
Read link lists from PDFs ("seed PDFs"): every hyperlink (clickable link
annotations and URLs written out in the text), with the text of the link, the
line it sits on, and an event name and date taken from that line or the lines
just above it.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import requests

from crawler import normalise_url
from events import find_date

log = logging.getLogger(__name__)

URL_IN_TEXT = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.I)


@dataclass
class PdfLink:
    pdf: str          # file path or URL of the PDF
    page: int
    url: str
    anchor: str       # text inside the link area
    line: str         # the line the link is on
    event: str        # best guess at the event / entry name
    date_raw: str
    date_iso: str


def load_pdf_bytes(source: str, timeout: float = 30.0) -> bytes:
    if re.match(r"https?://", source):
        resp = requests.get(source, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    return Path(source).expanduser().read_bytes()


def _lines(page) -> List[Tuple[float, float, str]]:
    """(top, bottom, text) of each text line on a pdfplumber page."""
    out = []
    for ln in page.extract_text_lines(keep_blank_chars=False) or []:
        out.append((ln["top"], ln["bottom"], ln["text"]))
    return out


GENERIC_LINK_TEXT = re.compile(
    r"(?i)(here|link|lien|website|site|web|more|more info\w*|info\w*|read more|register|sign up|"
    r"tickets?|programme|program|plus d['’]infos?|en savoir plus|inscription|mehr( infos?)?|"
    r"weitere infos|anmeldung|meer info|meer informatie|aanmelden|más info|más información|"
    r"inscripción|maggiori informazioni|info e iscrizioni|hier|ici|qui|aquí|aqui|"
    r"læs mere|mere info|tilmelding|tilmeld dig|her|läs mer|mer info|anmälan|anmäl dig|här)")


def _strip_date(text: str, date_raw: str) -> str:
    if date_raw:
        text = text.replace(date_raw, " ")
    return re.sub(r"\s+", " ", text).strip(" -–—:|•·,;/")


def _event_name(anchor: str, line: str, above: List[str], url: str, date_raw: str) -> str:
    """The link text if it reads like a title, else the line without URL and date,
    else the nearest line above that is more than a date."""
    def usable(t: str) -> bool:
        return (len(t) >= 4 and not URL_IN_TEXT.fullmatch(t) and not GENERIC_LINK_TEXT.fullmatch(t)
                and not re.fullmatch(r"[\d\s./\-–]+", t))
    for cand in (anchor, URL_IN_TEXT.sub("", line)):
        cand = _strip_date(cand, date_raw)
        if usable(cand):
            return cand
    for prev in reversed(above):
        prev = _strip_date(URL_IN_TEXT.sub("", prev), find_date(prev)[0])
        if usable(prev):
            return prev
    return url


def _date_near(line: str, above: List[str]):
    """The date on the link's own line, else on the nearest line above."""
    for t in [line] + list(reversed(above)):
        raw, iso = find_date(t)
        if raw:
            return raw, iso
    return "", ""


def extract_links(source: str) -> Tuple[List[PdfLink], str]:
    """All links in a PDF, and its full text (pages separated by form feeds)."""
    import pdfplumber
    data = load_pdf_bytes(source)
    links: List[PdfLink] = []
    texts: List[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            texts.append(page.extract_text() or "")
            lines = _lines(page)
            seen_here = set()

            def context(top: float, bottom: float) -> Tuple[str, List[str]]:
                mid = (top + bottom) / 2
                same = [t for (a, b, t) in lines if a - 2 <= mid <= b + 2]
                above = [t for (a, b, t) in lines if b <= top + 1][-3:]
                return " ".join(same), above

            # 1. clickable links
            for h in page.hyperlinks or []:
                uri = (h.get("uri") or "").strip()
                if not uri.lower().startswith(("http://", "https://", "www.")):
                    continue
                url = normalise_url(uri if "://" in uri else "https://" + uri)
                if not url:
                    continue
                try:
                    box = (max(h["x0"], 0), max(h["top"], 0), min(h["x1"], page.width),
                           min(h["bottom"], page.height))
                    anchor = (page.within_bbox(box).extract_text() or "").strip()
                except ValueError:
                    anchor = ""
                line, above = context(h["top"], h["bottom"])
                raw, iso = _date_near(line, above)
                links.append(PdfLink(source, pno, url, anchor, line,
                                     _event_name(anchor, line, above, url, raw), raw, iso))
                seen_here.add(url)

            # 2. URLs written out in the text without a link annotation
            for top, bottom, line in lines:
                for m in URL_IN_TEXT.finditer(line):
                    raw_url = m.group(0).rstrip(".,;:")
                    url = normalise_url(raw_url if "://" in raw_url else "https://" + raw_url)
                    if not url or url in seen_here:
                        continue
                    seen_here.add(url)
                    _, above = context(top, bottom)
                    raw, iso = _date_near(line, above)
                    links.append(PdfLink(source, pno, url, "", line,
                                         _event_name("", line, above, url, raw), raw, iso))
    log.info("Seed PDF %s: %d links on %d pages", source, len(links), len(texts))
    return links, "\f".join(texts)
