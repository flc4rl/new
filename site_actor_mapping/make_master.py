#!/usr/bin/env python3
"""
Build the two master files from one or more result folders:

  master_network   one row per event x organisation x source: which
                   organisations were found for which event, and where.
                   Sheets:
                     event_organisations  the network (long format: a source
                                          tied to two events gives two rows)
                     events               each event of the network once, with
                                          its organisations
                     all_events           every event found (agenda entries,
                                          calendar, event pages), with how well
                                          it was covered and whether it is in
                                          the network
                     menus_and_footers    names found only in menus, headers,
                                          footers or text repeated on every page
                                          of a site (site furniture), kept apart
                                          so they do not inflate the network
                     sources              every source read (web page, PDF,
                                          agenda entry, calendar cell) with its
                                          theme hits
                     excluded_sources     sources whose organisations were left
                                          out by the theme filter, with the reason
                     about                how the file was made
  master_themes    one row per theme term found and event (long format): the
                   event, the source link, and the passage (two sentences
                   before and after). Terms in menus, sidebars, footers, site
                   furniture and short labels are in a separate sheet
                   (menus_and_sidebars) unless --keep-menu-themes is given.

Theme filter (--theme-filter, default "source"): a theme hit is the inclusion
criterion for master_network. Organisations and events are kept only from
sources with at least one theme term in their content (not only in menus or
repeated furniture): "source" = the web page, the PDF, or for seed agendas the
agenda entry itself; "site" = any page of the same website; "event" = any
source tied to the same event; "none" keeps everything. master_themes is not
filtered. The run log and the about sheet say how much was left out.

Country columns (event_country, country_iso, country_source, location_text)
come from events.csv of each result folder, matched on event_key.

Written as .xlsx (one sheet per table, filters, frozen header) and as .csv.
site_actors.py calls this at the end of every run; run it yourself to merge
several runs or to change the filter:

    python make_master.py results/movementhub results/pdf_agendas -o results/master
    python make_master.py results/movementhub -o results/movementhub --theme-filter none
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import re
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple

log = logging.getLogger(__name__)
csv.field_size_limit(10_000_000)

NO_EVENT = "(not tied to an event)"
CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
THEME_FILTERS = ["source", "site", "event", "none"]
FURNITURE_REGIONS = {"nav", "header", "footer", "aside"}
COUNTRY_FIELDS = ["event_country", "country_iso", "country_source", "location_text"]

NETWORK_FIELDS = ["event", "event_date", "event_link", "event_key", *COUNTRY_FIELDS,
                  "organisation", "org_key", "org_type", "confidence", "mentions_on_source",
                  "source_link", "source_unit", "source_title", "site", "site_role", "layer", "is_pdf",
                  "page_date", "fetched_at", "content_vs_event", "years_after_event",
                  "recurring_event_editions", "site_linked_from_events", "found_by",
                  "named_in_event_title", "only_in_menus_or_footers", "source_has_theme",
                  "example_context"]
EVENT_FIELDS = ["event", "event_date", "event_link", "event_key", *COUNTRY_FIELDS,
                "n_organisations", "organisations", "n_sources", "sources"]
ALL_EVENT_FIELDS = ["event", "date", "event_link", "event_key", *COUNTRY_FIELDS, "listed_on",
                    "calendar_months", "entry_words", "link_followed", "link_status", "pages_analysed",
                    "words_analysed", "org_mentions", "sources_with_theme_hit", "in_network",
                    "network_organisations", "left_out_by_theme_filter", "location_structured"]
THEME_FIELDS = ["theme", "term", "event", "event_date", "event_link", "event_key", *COUNTRY_FIELDS,
                "n_events_for_passage", "layer", "content_vs_event", "years_after_event",
                "recurring_event_editions", "site_linked_from_events",
                "source_link", "source_unit", "page_title", "page_date", "fetched_at",
                "context_before", "sentence", "context_after", "organisations_in_paragraph",
                "organisations_in_paragraph_keys",
                "site", "site_role", "is_pdf", "pdf_found_on", "region", "boilerplate",
                "menu_or_sidebar", "duplicate_of"]
SOURCE_FIELDS = ["source_unit", "source_link", "page_title", "site", "site_role", "site_status", "kind",
                 "is_pdf", "page_date", "fetched_at", "words", "event_keys", "events", "org_mentions",
                 "has_theme_hit", "theme_hits", "menu_theme_hits", "in_network"]
EXCLUDED_FIELDS = ["source_unit", "source_link", "site", "site_role", "events", "n_organisations",
                   "organisations", "reason"]


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------
def _read(path: Path) -> List[dict]:
    return list(_iter(path))


def _iter(path: Path) -> Iterator[dict]:
    """Rows of a CSV file, one at a time (the mentions of a large run do not fit
    comfortably in memory twice)."""
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open(encoding="utf-8-sig", newline="") as fh:
        yield from csv.DictReader(fh)


def _true(v) -> bool:
    return str(v).strip().lower() in ("true", "yes", "1")


def _split(value: str) -> List[str]:
    return [v.strip() for v in (value or "").split(" | ")]


def _events_of(row: dict) -> List[dict]:
    """The events a mention or theme row is tied to, one dict each (key, name, date,
    link, and where known layer, content_vs_event, years_after_event, in_title).

    New result folders store them as JSON (events_json); older ones joined the
    names, dates and links with " | ", which is split again here."""
    js = row.get("events_json")
    if js:
        try:
            evs = [e for e in json.loads(js) if isinstance(e, dict)]
            if evs:
                return evs
            return [{"name": NO_EVENT, "date": "", "link": "", "key": ""}]
        except ValueError:
            pass
    names, dates, links = _split(row.get("event", "")), _split(row.get("event_date", "")), _split(row.get("event_link", ""))
    keys = _split(row.get("event_key", ""))
    if not any(names) and not any(links):
        return [{"name": NO_EVENT, "date": "", "link": "", "key": ""}]
    if len(names) > len(links) == len(dates) >= 1 and len(links) == 1:
        # One event whose name itself contained " | " ("National Climate March | Netherlands").
        names = [" | ".join(names)]
    n = max(len(names), len(dates), len(links))
    pad = lambda xs: xs + [""] * (n - len(xs))  # noqa: E731
    keys = keys if len(keys) == n else [""] * n
    return [{"name": a, "date": b, "link": c, "key": k} for a, b, c, k in zip(pad(names), pad(dates), pad(links), keys)]


def _event_id(ev: dict) -> str:
    return ev.get("key") or f"{ev.get('name', '')}\x1f{ev.get('link', '')}"


def _layer_fallback(row: dict, ev: dict) -> str:
    """Layer for rows of older result folders (no events_json)."""
    url = row.get("url") or row.get("source_link") or ""
    if row.get("site_role") == "seed" or url.startswith(("file:", "pdf:")):
        return "listing_seed"
    if row.get("calendar_month") or "calendar_month=" in url:
        return "listing_calendar"
    placeholder = str(ev.get("name") or NO_EVENT).startswith("(")
    if row.get("site_role") == "main":
        return "main_page" if placeholder else "main_event_page"
    norm = lambda u: re.sub(r"^https?://(?:www\.)?", "", (u or "").split("#")[0]).rstrip("/")  # noqa: E731
    if ev.get("link") and norm(ev["link"]) == norm(url):
        return "landing"
    pdf = _true(row.get("is_pdf")) or bool(re.search(r"\.pdf(?:\?|$)", url, re.I))
    return "linked_pdf" if pdf else "linked_page"


_title_key_cache: Dict[str, str] = {}


def _in_title(org_key: str, event_name: str) -> bool:
    """Organisation named in the event title as whole words (for older folders)."""
    from extract import canonical_key
    k = canonical_key(org_key or "")
    if not k:
        return False
    t = _title_key_cache.get(event_name)
    if t is None:
        t = _title_key_cache[event_name] = f" {canonical_key(event_name or '')} "
    if f" {k} " in t:
        return True
    # "#ShellMustFall": a one-word name at the start of a hashtag.
    return " " not in k and len(k) >= 4 and any(
        canonical_key(h).startswith(k) for h in re.findall(r"#(\w+)", event_name or ""))


def _countries(results: Sequence[Path]) -> Tuple[Dict[str, dict], Dict[tuple, dict]]:
    by_key: Dict[str, dict] = {}
    by_name: Dict[tuple, dict] = {}
    for res in results:
        for e in _iter(res / "events.csv"):
            c = {f: e.get(f, "") for f in COUNTRY_FIELDS}
            if e.get("event_key"):
                by_key.setdefault(e["event_key"], c)
            by_name.setdefault((e.get("event", ""), e.get("event_link", "")), c)
    return by_key, by_name


def _country_of(ev: dict, by_key: Dict[str, dict], by_name: Dict[tuple, dict]) -> dict:
    c = by_key.get(ev.get("key") or "") or by_name.get((ev.get("name", ""), ev.get("link", "")))
    return c or {f: "" for f in COUNTRY_FIELDS}


def _is_menu_theme(r: dict) -> bool:
    if "menu_or_sidebar" in r and r["menu_or_sidebar"] != "":
        return _true(r["menu_or_sidebar"])
    return r.get("region", "") in FURNITURE_REGIONS or _true(r.get("boilerplate"))


# --------------------------------------------------------------------------
# Theme filter (inclusion criterion)
# --------------------------------------------------------------------------
class ThemeFilter:
    """Which sources, websites and events have a theme hit in their content."""

    def __init__(self, mode: str, results: Sequence[Path]):
        if mode not in THEME_FILTERS:
            raise ValueError(f"--theme-filter must be one of {', '.join(THEME_FILTERS)}")
        self.mode = mode
        self.units: Set[str] = set()
        self.sites: Set[str] = set()
        self.events: Set[str] = set()
        self.menu_only_units: Set[str] = set()
        self.old_folders: List[str] = []
        for res in results:
            src = res / "sources.csv"
            if src.exists():
                for r in _iter(src):
                    u = r.get("source_unit") or r.get("source_link", "")
                    if _true(r.get("has_theme_hit")):
                        self.units.add(u)
                        self.sites.add(r.get("site", ""))
                        self.events.update(k for k in (r.get("event_keys") or "").split("; ") if k)
                    elif int(r.get("menu_theme_hits") or 0):
                        self.menu_only_units.add(u)
                # Events tied to themed passages (also names-only ids for completeness).
                for r in _iter(res / "theme_hits.csv"):
                    if not _is_menu_theme(r) and (r.get("source_unit") or r.get("source_link")) in self.units:
                        self.events.update(_event_id(e) for e in _events_of(r))
            else:
                # Older folder: a source is a page or a whole PDF.
                self.old_folders.append(str(res))
                for r in _iter(res / "theme_hits.csv"):
                    u = r.get("source_link", "")
                    if _is_menu_theme(r):
                        self.menu_only_units.add(u)
                        continue
                    self.units.add(u)
                    self.sites.add(r.get("site", ""))
                    self.events.update(_event_id(e) for e in _events_of(r))
        self.menu_only_units -= self.units

    def unit_of(self, m: dict) -> str:
        return m.get("source_unit") or m.get("url") or m.get("source_link", "")

    def keeps(self, m: dict, ev: dict) -> bool:
        if self.mode == "none":
            return True
        if self.mode == "site":
            return m.get("site", "") in self.sites
        if self.mode == "event" and not str(ev.get("name", "")).startswith("("):
            return _event_id(ev) in self.events
        return self.unit_of(m) in self.units

    def reason(self, m: dict) -> str:
        if self.mode == "site":
            return "no theme term on any page of this website"
        if self.mode == "event":
            return "no theme term in any source tied to this event, nor in this source"
        if self.unit_of(m) in self.menu_only_units:
            return "theme terms only in menus, sidebars, repeated site furniture or short labels"
        return "no theme term in this source"


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------
def network_rows(results: Sequence[Path], min_confidence: str = "low",
                 theme_filter: Optional[ThemeFilter] = None,
                 excluded: Optional[Dict[str, dict]] = None,
                 before: Optional[Dict[tuple, list]] = None) -> List[dict]:
    """master_network rows (one per event x organisation x source). Mentions the
    theme filter leaves out are summarised per source in `excluded`; `before`
    receives every row key before the filter: [only in furniture, organisation,
    event id, placeholder event]."""
    rows: "OrderedDict[tuple, dict]" = OrderedDict()
    by_key, by_name = _countries(results)
    for res in results:
        conf = {o["org_key"]: o for o in _iter(res / "organisations.csv")}
        for m in _iter(res / "mentions.csv"):
            org = conf.get(m["org_key"], {})
            confidence = org.get("confidence", "")
            if CONFIDENCE_RANK.get(confidence, 0) < CONFIDENCE_RANK[min_confidence]:
                continue
            is_bp = _true(m.get("boilerplate")) or _true(m.get("in_menu"))
            for ev in _events_of(m):
                if before is not None:
                    b = before.setdefault((_event_id(ev), m["organisation"], m["url"]),
                                          [True, m["organisation"], _event_id(ev),
                                           str(ev.get("name") or NO_EVENT).startswith("(")])
                    b[0] = b[0] and is_bp
                if theme_filter is not None and not theme_filter.keeps(m, ev):
                    if excluded is not None:
                        u = theme_filter.unit_of(m)
                        x = excluded.setdefault(u, {
                            "source_unit": u, "source_link": m.get("url", ""), "site": m.get("site", ""),
                            "site_role": m.get("site_role", ""), "_events": OrderedDict(),
                            "_orgs": OrderedDict(), "reason": theme_filter.reason(m)})
                        x["_events"][ev.get("name", "")] = None
                        x["_orgs"][m["organisation"]] = None
                    continue
                name = ev.get("name") or NO_EVENT
                key = (_event_id(ev), m["organisation"], m["url"])
                r = rows.get(key)
                if r is None:
                    r = rows[key] = {
                        "event": name, "event_date": ev.get("date", ""), "event_link": ev.get("link", ""),
                        "event_key": ev.get("key", ""), **_country_of(ev, by_key, by_name),
                        "organisation": m["organisation"], "org_key": m.get("org_key", ""),
                        "org_type": m.get("org_type") or org.get("org_type", ""),
                        "confidence": confidence, "mentions_on_source": 0,
                        "source_link": m["url"], "source_unit": m.get("source_unit") or m["url"],
                        "source_title": m.get("page_title", ""),
                        "site": m.get("site", ""), "site_role": m.get("site_role", ""),
                        "layer": ev.get("layer") or _layer_fallback(m, ev),
                        "is_pdf": _true(m.get("is_pdf")),
                        "page_date": m.get("page_date", ""), "fetched_at": m.get("fetched_at", ""),
                        "content_vs_event": ev.get("content_vs_event", ""),
                        "years_after_event": ev.get("years_after_event", ""),
                        "recurring_event_editions": m.get("recurring_event_editions", ""),
                        "site_linked_from_events": m.get("site_linked_from_events", ""),
                        "named_in_event_title": (bool(ev["in_title"]) if "in_title" in ev
                                                 else _in_title(m.get("org_key", ""), name)),
                        "source_has_theme": (_true(m["source_has_theme"]) if m.get("source_has_theme")
                                             else ""),
                        "_methods": Counter(), "_bp": True, "example_context": "", "_ex_bp": True,
                    }
                r["mentions_on_source"] += 1
                r["_methods"].update(m.get("methods", "").split("+"))
                # Menus, headers, footers and text repeated across a site's pages.
                r["_bp"] = r["_bp"] and is_bp
                if not r["example_context"] or (r["_ex_bp"] and not is_bp):  # prefer real content
                    r["example_context"] = (m.get("context") or "")[:500]
                    r["_ex_bp"] = is_bp
    out = []
    for r in rows.values():
        r["found_by"] = "; ".join(k for k, _ in r.pop("_methods").most_common() if k)
        r["only_in_menus_or_footers"] = r.pop("_bp")
        r.pop("_ex_bp")
        out.append(r)
    out.sort(key=lambda r: (r["event"].startswith("("), r["event_date"] or "", r["event"].casefold(),
                            r["organisation"].casefold()))
    return out


def event_rows(network: List[dict]) -> List[dict]:
    by_event: Dict[tuple, dict] = OrderedDict()
    for r in network:
        key = (r.get("event_key") or "", r["event"], r["event_link"])
        e = by_event.setdefault(key, {"event": r["event"], "event_date": r["event_date"],
                                      "event_link": r["event_link"], "event_key": r.get("event_key", ""),
                                      **{f: r.get(f, "") for f in COUNTRY_FIELDS},
                                      "_orgs": OrderedDict(), "_src": OrderedDict()})
        e["_orgs"][r["organisation"]] = None
        e["_src"][r["source_link"]] = None
    out = []
    for e in by_event.values():
        orgs, srcs = list(e.pop("_orgs")), list(e.pop("_src"))
        out.append({**e, "n_organisations": len(orgs), "organisations": "; ".join(orgs),
                    "n_sources": len(srcs), "sources": " ".join(srcs)})
    return out


def all_event_rows(results: Sequence[Path], network: List[dict], before: Set[str]) -> List[dict]:
    """Every event of events.csv, with whether (and with how many organisations) it is
    in the network, so shares can be computed over all events, not only those found."""
    orgs_of: Dict[str, Set[str]] = defaultdict(set)
    for r in network:
        orgs_of[_event_id({"key": r.get("event_key"), "name": r["event"], "link": r["event_link"]})].add(
            r["organisation"])
    out, seen = [], set()
    for res in results:
        for e in _iter(res / "events.csv"):
            eid = _event_id({"key": e.get("event_key"), "name": e.get("event", ""),
                             "link": e.get("event_link", "")})
            if eid in seen:
                continue
            seen.add(eid)
            n = len(orgs_of.get(eid, ()))
            out.append({**e, "in_network": n > 0, "network_organisations": n,
                        "left_out_by_theme_filter": eid in before and n == 0})
    return out


def theme_rows(results: Sequence[Path], keep_menu_themes: bool = False) -> Tuple[List[dict], List[dict]]:
    """master_themes rows, one per theme term and event (long format), and the rows
    found in menus, sidebars, site furniture and short labels (kept apart unless
    keep_menu_themes)."""
    by_key, by_name = _countries(results)
    out: List[dict] = []
    menus: List[dict] = []
    for res in results:
        for r in _iter(res / "theme_hits.csv"):
            evs = _events_of(r)
            menu = _is_menu_theme(r)
            for ev in evs:
                row = {**r, "event": ev.get("name") or NO_EVENT, "event_date": ev.get("date", ""),
                       "event_link": ev.get("link", ""), "event_key": ev.get("key", ""),
                       **_country_of(ev, by_key, by_name),
                       "n_events_for_passage": r.get("n_events") or len(evs),
                       "layer": ev.get("layer") or _layer_fallback(r, ev),
                       "content_vs_event": ev.get("content_vs_event", ""),
                       "years_after_event": ev.get("years_after_event", ""),
                       "menu_or_sidebar": menu}
                row.pop("events_json", None)
                (menus if menu and not keep_menu_themes else out).append(row)
    key = lambda r: (r["event"].startswith("("), r.get("event_date") or "", r["event"].casefold(),  # noqa: E731
                     r.get("source_link", ""), r.get("theme", ""))
    out.sort(key=key)
    menus.sort(key=key)
    return out, menus


def source_rows(results: Sequence[Path], network: List[dict]) -> List[dict]:
    """Every source read: sources.csv (or pages.csv of older folders) plus the site's crawl status."""
    in_net = {r.get("source_unit") or r["source_link"] for r in network}
    out = []
    for res in results:
        status = {s.get("site", ""): s.get("status", "") for s in _iter(res / "sites.csv")}
        src = res / "sources.csv"
        rows = _iter(src) if src.exists() else (
            {**p, "source_unit": p.get("url", ""), "source_link": p.get("url", ""),
             "page_title": p.get("title", ""), "page_date": p.get("page_date") or p.get("date", ""),
             "has_theme_hit": any(int(v or 0) for k, v in p.items() if k.startswith("theme:"))}
            for p in _iter(res / "pages.csv"))
        for r in rows:
            r["site_status"] = status.get(r.get("site", ""), "")
            r["in_network"] = (r.get("source_unit") or r.get("source_link")) in in_net
            out.append(r)
    return out


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------
def _write_csv(path: Path, rows: List[dict], fields: List[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


EXCEL_MAX_ROWS = 1_048_575    # data rows per sheet (one more for the header)


def _write_xlsx(path: Path, sheets: List[tuple]) -> bool:
    """One sheet per (title, rows, fields), streamed (write-only), with a bold
    frozen header, filters, column widths and wrapped long text. A table longer
    than Excel's row limit continues on sheets <title>_2, <title>_3 ..."""
    try:
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.styles import Alignment, Font
        from openpyxl.utils import get_column_letter
    except ImportError:
        log.warning("openpyxl not installed; master files written as CSV only (pip install openpyxl)")
        return False
    wide = {"context_before": 60, "sentence": 60, "context_after": 60, "example_context": 60,
            "organisations": 60, "organisations_in_paragraph": 40, "event": 40, "source_title": 30,
            "page_title": 30, "sources": 50, "source_link": 40, "event_link": 40, "organisation": 30,
            "events": 40, "reason": 40, "value": 80, "listed_on": 40, "source_unit": 40}
    bold = Font(bold=True)
    a_wrap = Alignment(wrap_text=True, vertical="top")
    a_top = Alignment(vertical="top")
    wb = Workbook(write_only=True)
    tmp = path.with_name(path.name + ".part")
    for title, rows, fields in sheets:
        parts = [rows[i:i + EXCEL_MAX_ROWS] for i in range(0, len(rows), EXCEL_MAX_ROWS)] or [[]]
        if len(parts) > 1:
            log.warning("%s: %d rows are more than one Excel sheet holds; continued on %d sheets "
                        "(%s_2 ...). The CSV file has them all in one table.", title, len(rows),
                        len(parts), title)
        aligns = [a_wrap if wide.get(f, 0) >= 40 else a_top for f in fields]
        for n, part in enumerate(parts, 1):
            ws = wb.create_sheet(title if n == 1 else f"{title}_{n}"[:31])
            # Write-only sheets take their layout before the first row.
            for i, f in enumerate(fields, 1):
                ws.column_dimensions[get_column_letter(i)].width = wide.get(f, 16)
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = f"A1:{get_column_letter(max(1, len(fields)))}{len(part) + 1}"
            hdr = []
            for f in fields:
                c = WriteOnlyCell(ws, value=f)
                c.font = bold
                hdr.append(c)
            ws.append(hdr)
            for r in part:
                cells = []
                for f, a in zip(fields, aligns):
                    c = WriteOnlyCell(ws, value=_cell(r.get(f, "")))
                    c.alignment = a
                    cells.append(c)
                ws.append(cells)
    wb.save(tmp)
    tmp.replace(path)
    return True


try:
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE as _ILLEGAL
except ImportError:   # openpyxl missing: only CSV files are written anyway
    _ILLEGAL = re.compile(r"[\000-\010]|[\013-\014]|[\016-\037]")


def _cell(v):
    if isinstance(v, bool):
        return "yes" if v else "no"
    if v in ("True", "False"):
        return "yes" if v == "True" else "no"
    if isinstance(v, (int, float)):
        return v
    # Control characters (from PDFs, pasted Word text) are not allowed in Excel cells.
    return _ILLEGAL.sub("", str(v))[:32000]   # Excel's cell limit


def _about_rows(results: Sequence[Path], facts: List[Tuple[str, object]]) -> List[dict]:
    rows = [{"item": k, "value": v if isinstance(v, (int, float)) else str(v)} for k, v in facts]
    for res in results:
        p = res / "run_info.json"
        if not p.exists():
            continue
        try:
            info = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for k in ("started_at", "finished_at", "status", "command", "spacy_models_used", "ner_size",
                  "pages_fetched_between", "packages"):
            if k in info:
                v = info[k]
                rows.append({"item": f"{res.name}: {k}",
                             "value": " ".join(v) if k == "command" and isinstance(v, list)
                             else json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)})
    return rows


def build_masters(results: Sequence[Path], out_dir: Path, min_confidence: str = "low",
                  theme_filter: str = "source", keep_menu_themes: bool = False,
                  stats: Optional[dict] = None) -> List[Path]:
    """Write master_network and master_themes (.csv and .xlsx) to out_dir and
    return their paths. `stats` (if given) receives the counts of what the theme
    filter kept and left out."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results = [Path(r) for r in results]
    tf = ThemeFilter(theme_filter, results)
    if tf.old_folders and theme_filter != "none":
        log.info("No sources.csv in %s (made by an older version): for the theme filter a source "
                 "is a whole page or PDF, not an agenda entry", ", ".join(tf.old_folders))
    excluded: Dict[str, dict] = {}
    before: Dict[tuple, list] = {}
    kept_net = network_rows(results, min_confidence, tf if theme_filter != "none" else None,
                            excluded, before)
    net = [r for r in kept_net if not r["only_in_menus_or_footers"]]
    furniture = [r for r in kept_net if r["only_in_menus_or_footers"]]
    before_content = [b for b in before.values() if not b[0]]
    del before
    before_ids = {b[2] for b in before_content if not b[3]}
    events = event_rows(net)
    all_events = all_event_rows(results, net, before_ids)
    themes, menu_themes = theme_rows(results, keep_menu_themes)
    sources = source_rows(results, kept_net)
    for x in excluded.values():
        evs, orgs = list(x.pop("_events")), list(x.pop("_orgs"))
        x["events"] = "; ".join(evs[:20])
        x["n_organisations"] = len(orgs)
        x["organisations"] = "; ".join(orgs[:40])
    excl = sorted(excluded.values(), key=lambda x: (-x["n_organisations"], x["source_unit"]))

    # What the theme filter kept, for the log and the about sheet.
    n = {
        "network_rows": (len(net), len(before_content)),
        "organisations": (len({r["organisation"] for r in net}), len({b[1] for b in before_content})),
        "events": (len({_event_id({"key": r.get("event_key"), "name": r["event"], "link": r["event_link"]})
                        for r in net if not r["event"].startswith("(")}), len(before_ids)),
    }
    left_out_sources = len({x["source_unit"] for x in excl})
    if theme_filter != "none":
        log.info("Theme filter '%s': kept %d of %d network rows, %d of %d organisations and %d of %d "
                 "events; %d sources with organisations but no theme hit were left out (listed in the "
                 "excluded_sources sheet). Use --theme-filter none to keep everything.",
                 theme_filter, *n["network_rows"], *n["organisations"], *n["events"], left_out_sources)
        if before_content and not net:
            log.warning("The theme filter left master_network empty: no source has a theme hit. "
                        "Check the --themes file.")
    if menu_themes:
        log.info("%d theme rows from menus, sidebars, site furniture and short labels are in the "
                 "menus_and_sidebars sheet of master_themes (--keep-menu-themes keeps them in the main "
                 "sheet)", len(menu_themes))
    summary = {"theme_filter": theme_filter, "keep_menu_themes": keep_menu_themes,
               "min_confidence": min_confidence,
               **{f"{k}_kept": a for k, (a, _) in n.items()}, **{f"{k}_before_filter": b for k, (_, b) in n.items()},
               "sources_left_out": left_out_sources, "theme_rows": len(themes),
               "theme_rows_in_menus_and_sidebars": len(menu_themes), "all_events": len(all_events),
               "sources": len(sources)}
    if stats is not None:
        stats.update(summary)
    about = _about_rows(results, [("made_at", dt.datetime.now().astimezone().isoformat(timespec="seconds")),
                                  ("result_folders", "; ".join(str(r) for r in results))]
                        + list(summary.items()))

    paths = []
    _write_csv(out_dir / "master_network.csv", net, NETWORK_FIELDS)
    _write_csv(out_dir / "master_themes.csv", themes, THEME_FIELDS)
    paths += [out_dir / "master_network.csv", out_dir / "master_themes.csv"]
    if _write_xlsx(out_dir / "master_network.xlsx", [("event_organisations", net, NETWORK_FIELDS),
                                                      ("events", events, EVENT_FIELDS),
                                                      ("all_events", all_events, ALL_EVENT_FIELDS),
                                                      ("menus_and_footers", furniture, NETWORK_FIELDS),
                                                      ("sources", sources, SOURCE_FIELDS),
                                                      ("excluded_sources", excl, EXCLUDED_FIELDS),
                                                      ("about", about, ["item", "value"])]):
        paths.append(out_dir / "master_network.xlsx")
    if _write_xlsx(out_dir / "master_themes.xlsx", [("theme_instances", themes, THEME_FIELDS),
                                                     ("menus_and_sidebars", menu_themes, THEME_FIELDS),
                                                     ("about", about, ["item", "value"])]):
        paths.append(out_dir / "master_themes.xlsx")
    return paths


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="Merge result folders into the two master files")
    ap.add_argument("results", nargs="+", type=Path, help="Result folders written by site_actors.py")
    ap.add_argument("--output-dir", "-o", type=Path, required=True)
    ap.add_argument("--min-confidence", choices=["low", "medium", "high"], default="low",
                    help="Leave out organisations below this confidence in master_network")
    ap.add_argument("--theme-filter", choices=THEME_FILTERS, default="source",
                    help="Keep organisations and events only from sources with a theme hit: the "
                         "source itself ('source': page, PDF or agenda entry), any page of its "
                         "website ('site'), any source of the same event ('event'), or keep "
                         "everything ('none')")
    ap.add_argument("--keep-menu-themes", action="store_true",
                    help="Keep theme terms found in menus, sidebars and site furniture in the main "
                         "sheet of master_themes")
    args = ap.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    handlers: List[logging.Handler] = [logging.StreamHandler()]
    try:
        fh = logging.FileHandler(args.output_dir / "run.log", mode="a", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s make_master: %(message)s",
                                          "%Y-%m-%d %H:%M:%S"))
        handlers.append(fh)
    except OSError:
        pass
    handlers[0].setFormatter(logging.Formatter("%(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=handlers)
    missing = [str(r) for r in args.results if not (r / "mentions.csv").exists()]
    if missing:
        ap.error(f"no mentions.csv in {', '.join(missing)} (not a site_actors.py result folder?)")
    for p in build_masters(args.results, args.output_dir, args.min_confidence, args.theme_filter,
                           args.keep_menu_themes):
        print(p)


if __name__ == "__main__":
    main()
