#!/usr/bin/env python3
"""
Analysis tables, charts and network files for a finished run (read-only).

    python analytics.py RESULTS_DIR -o OUT [options]

RESULTS_DIR is a site_actors.py results folder (or a make_master.py output
folder). It must hold master_network (.xlsx or .csv), master_themes (.xlsx or
.csv) and events.csv. Optional files (seed_links.json, pages.csv, sites.csv,
sources.csv) are looked up there and then in every --run folder; analyses that
need a missing file are skipped and listed in summary.json ("skipped").

Nothing in RESULTS_DIR is changed. OUT receives:

  analytics.xlsx           all tables (README sheet first), one sheet each
  tables/*.csv             the same tables as CSV (UTF-8 with BOM, for Excel)
  org_codes_template.xlsx  organisation authority list to curate, merge
                           candidates and a stratified precision sample
  kwic_for_coding.xlsx     theme passages in keyword-in-context layout with
                           coder columns and a stratified coding sample
  network/                 Gephi files (.gexf, .graphml) and node/edge CSVs
  charts/*.png             six charts (150 dpi)
  summary.json, summary.md provenance, parameters, numbers and caveats

Inclusion criterion (--theme-filter, default "source", as in make_master.py):
organisations and events count for the network only when they come from a
source with a theme hit (colonialism, racism, fascism) in its content. A
source is one web page or one PDF; for the agenda (seed) PDFs each agenda
entry is its own source. "site" keeps every source of a website with a hit,
"event" every source of an event with a hit, "none" keeps all sources. The
sensitivity table always also shows "none", and the number of organisations
and events left out is reported. Theme prevalence is never filtered (an
inclusion criterion on the outcome would make every share 100 %).

Both master layouts are read: the older one (events joined with " | ") and the
long one (one row per event, with event_key, layer and country columns).

Curated inputs (read, never written): --org-codes org_codes.csv (columns
org_key, organisation, include, merge_into_key, canonical_name, actor_class,
country, is_organisation, is_partial_name, notes) and --event-codes
event_codes.csv (event_id, series_id, duplicate_of, country_override, notes).
Fill in org_codes_template.xlsx, save its first sheet as org_codes.csv and run
again with --org-codes.

    python analytics.py results/movementhub -o results/movementhub_analytics
    python analytics.py results/master -o results/analytics --run results/pdf_agendas
    python analytics.py results/master -o results/analytics --theme-filter none
    python analytics.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import difflib
import hashlib
import json
import logging
import math
import os
import platform
import random
import re
import sys
import time
import unicodedata
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import unquote, urlparse

log = logging.getLogger("analytics")
csv.field_size_limit(10_000_000)

VERSION = "1.0"
HERE = Path(__file__).resolve().parent
DEFAULT_THEMES_FILE = HERE / "themes_colonialism_racism_fascism.txt"

THEME_FILTERS = ["source", "site", "event", "none"]
CONF_RANK = {"low": 0, "medium": 1, "high": 2}
FURNITURE_REGIONS = {"nav", "header", "footer", "aside"}
NO_EVENT = "(not tied to an event)"
OWN_LAYERS = {"listing_seed", "listing_calendar", "main_event_page"}
TIE_LEVEL = {"listing_seed": "L1", "listing_calendar": "L1", "main_event_page": "L1", "main_page": "L1",
             "landing": "L2", "linked_pdf": "L3", "linked_page": "L3"}
LAYER_GROUP = {"L1": "own", "L2": "landing", "L3": "linked"}
PERIODS = [(2015, 2017, "2015-17"), (2018, 2020, "2018-20"), (2021, 2022, "2021-22"),
           (2023, 2024, "2023-24"), (2025, 2026, "2025-26")]
DROP_CLASSES = {"place", "site_ui", "noise", "event_name"}
ACTOR_CLASSES = ["movement_org", "ngo", "party", "union", "media", "company", "state_body", "police_court",
                 "place", "person", "project_infrastructure", "event_name", "reference", "site_ui", "noise"]
RELEVANCE = ["substantive", "name_or_title_only", "list_or_tag", "off_topic_sense", "false_match", "unclear"]
SCENARIOS = {"A": "all organisations", "B": "without organisations named in the event title",
             "C": "confidence >= --min-confidence (or include=1)", "D": "C without organisations named in the title",
             "C_curated": "C without curated places, site UI, noise and event names"}
LAYER_SETS = {"L1-L3": {"L1", "L2", "L3"}, "L1+L2": {"L1", "L2"}}

# Colour-blind-safe categorical slots (validated reference palette): themes take
# slots 1-3, the three agenda streams slots 4, 7 and 6, always with a legend.
THEME_COLOUR = {"colonialism": "#2a78d6", "racism": "#eb6834", "fascism": "#1baf7a"}
STREAM_COLOUR = {"cja": "#eda100", "echos": "#6250d6", "hub": "#008300"}
STREAM_MARKER = {"cja": "o", "echos": "s", "hub": "^"}
EXTRA_COLOURS = ["#e87ba4", "#e34948", "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#6250d6", "#008300"]
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_WS = re.compile(r"\s+")
_WORD = re.compile(r"[^\W\d_]+")


# ==========================================================================
# Small helpers
# ==========================================================================
def s(v) -> str:
    """A cell value as text ("" for None and NaN; 3.0 -> "3"; booleans -> yes/no)."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        return str(int(v)) if v.is_integer() else repr(v)
    return str(v).strip()


def truthy(v) -> bool:
    return s(v).casefold() in ("yes", "true", "1", "y")


def yn(b) -> str:
    return "yes" if b else "no"


def sha12(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def collapse(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


def wilson(k: int, n: int, z: float = 1.96) -> Tuple[Optional[float], Optional[float]]:
    if not n:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round(max(0.0, (c - h) / d), 4), round(min(1.0, (c + h) / d), 4)


def byar(k: int, z: float = 1.96) -> Tuple[float, float]:
    """Byar's approximation of the 95 % Poisson interval for a count k."""
    lo = 0.0 if k == 0 else k * (1 - 1 / (9 * k) - z / (3 * math.sqrt(k))) ** 3
    k1 = k + 1
    hi = k1 * (1 - 1 / (9 * k1) + z / (3 * math.sqrt(k1))) ** 3
    return lo, hi


def share(k, n) -> Optional[float]:
    return round(k / n, 4) if n else None


def norm_link(u: str) -> str:
    """Link without scheme, www., fragment and trailing slash (for comparisons)."""
    u = s(u)
    u = re.sub(r"^https?://(?:www\.)?", "", u.split("#")[0], flags=re.I)
    return u.rstrip("/").casefold()


def host_of(u: str) -> str:
    try:
        h = (urlparse(s(u)).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def is_web(u: str) -> bool:
    return bool(re.match(r"https?://", s(u), re.I))


def year_of(date: str) -> Optional[int]:
    m = re.search(r"(?<!\d)((?:19|20)\d\d)(?!\d)", s(date))
    return int(m.group(1)) if m else None


def period_of(year: Optional[int]) -> str:
    if year is None:
        return "undated"
    for a, b, lab in PERIODS:
        if a <= year <= b:
            return lab
    return "before 2015" if year < PERIODS[0][0] else "after 2026"


def doc_year_of(url: str) -> Optional[int]:
    p = unquote(s(url))
    m = re.search(r"/(?:uploads|files)/((?:19|20)\d\d)/\d\d/", p) or re.search(r"/((?:19|20)\d\d)/\d\d/", p)
    return int(m.group(1)) if m else None


def pdf_display(value: str) -> str:
    """Seed-PDF paths as the pipeline shows them ("pdf:<file name>")."""
    try:
        from seed_pdfs import pdf_display as pd_
        return pd_(value)
    except Exception:  # noqa: BLE001 - optional project module
        if "file:" not in (value or ""):
            return value

        def one(m):
            p = urlparse(m.group(0))
            name = unicodedata.normalize("NFC", unquote(Path(p.path).name))
            return f"pdf:{name}" + (f"#{p.fragment}" if p.fragment else "")
        return re.sub(r"file://[^\s|;#]*(?:#[^\s|;]*)?", one, value)


def stream_of(source: str) -> str:
    """Which listing an event comes from: cja (Riseup pad), echos (Les échos du
    vent agenda), hub (a web calendar), or agenda:<file> for other seed PDFs."""
    v = unicodedata.normalize("NFC", unquote(s(source))).casefold()
    if not v:
        return "other"
    if "chos" in v:
        return "echos"
    if "riseup" in v or "cja" in v:
        return "cja"
    if v.startswith(("http://", "https://")):
        return "hub"
    name = Path(urlparse(v).path).name or v
    return "agenda:" + name.replace("pdf:", "")


def short(text: str, n: int = 60) -> str:
    text = collapse(text)
    return text if len(text) <= n else text[: n - 3] + "..."


# ==========================================================================
# Project modules (read-only, imported lazily so a module being edited does
# not stop the analysis)
# ==========================================================================
class Project:
    def __init__(self):
        import extract
        self.extract = extract
        self.canonical_key = extract.canonical_key
        self.URL_LIKE = getattr(extract, "URL_LIKE", re.compile(r"(?:https?://|www\.)\S+"))
        self.detect_language = getattr(extract, "detect_language", None)
        try:
            import linked_sites
            self.platform_hosts = set(linked_sites.PLATFORM_HOSTS)
            self.host_matches = linked_sites.host_matches
            self.filter_targets = linked_sites.filter_targets
        except Exception as exc:  # noqa: BLE001
            log.warning("linked_sites not importable (%s); platform hosts unknown", exc)
            self.platform_hosts, self.filter_targets = set(), None
            self.host_matches = lambda h, names: any(h == n or h.endswith("." + n) for n in names)
        try:
            import places
            self.places = set(places.PLACES)
        except Exception as exc:  # noqa: BLE001
            log.warning("places not importable (%s)", exc)
            self.places = set()
        self.gazetteer = None
        self.gazetteer_error = ""
        try:
            import gazetteer
            self.gazetteer = gazetteer
        except Exception as exc:  # noqa: BLE001
            self.gazetteer_error = f"{type(exc).__name__}: {exc}"
        self._ck: Dict[str, str] = {}

    def key(self, name: str) -> str:
        k = self._ck.get(name)
        if k is None:
            try:
                k = self.canonical_key(name or "")
            except Exception:  # noqa: BLE001
                k = collapse(name).casefold()
            self._ck[name] = k
        return k

    def country(self, name: str, entry_text: str = "", link: str = "", structured: str = "") -> dict:
        """gazetteer.event_country (both the current dict form and the older tuple form)."""
        if self.gazetteer is None:
            return {}
        fn = self.gazetteer.event_country
        try:
            res = fn(name or "", entry_text or "", link=link or "", structured=structured or "")
        except TypeError:
            res = fn(name or "", entry_text or "")
        except Exception:  # noqa: BLE001 - one odd name must not stop the analysis
            return {}
        if isinstance(res, tuple):
            res = {"countries": list(res[0] or []), "method": res[1] if len(res) > 1 else "", "matched": ""}
        return dict(res or {})

    def country_name(self, iso: str) -> str:
        if self.gazetteer is not None and hasattr(self.gazetteer, "country_name"):
            try:
                return self.gazetteer.country_name(iso)
            except Exception:  # noqa: BLE001
                pass
        return {"INT": "International", "ONLINE": "Online"}.get(iso, iso)

    def is_platform(self, link: str) -> bool:
        h = host_of(link)
        return bool(h) and self.host_matches(h, self.platform_hosts)


# ==========================================================================
# Reading
# ==========================================================================
def _sheet_rows(ws) -> Tuple[List[str], List[dict]]:
    it = ws.iter_rows(values_only=True)
    head = next(it, None)
    if not head:
        return [], []
    fields = [s(h) for h in head]
    rows = []
    for vals in it:
        if vals is None or all(v is None for v in vals):
            continue
        rows.append({f: s(v) for f, v in zip(fields, vals) if f})
    return fields, rows


def read_master(folder: Path, stem: str, sheets: Sequence[str]) -> Tuple[Optional[Path], Dict[str, Tuple[List[str], List[dict]]]]:
    """The named sheets of <stem>.xlsx (missing sheets are empty), else <stem>.csv as
    the first sheet."""
    x = folder / f"{stem}.xlsx"
    out: Dict[str, Tuple[List[str], List[dict]]] = {sh: ([], []) for sh in sheets}
    if x.exists() and x.stat().st_size:
        import openpyxl
        wb = openpyxl.load_workbook(x, read_only=True, data_only=True)
        try:
            for i, sh in enumerate(sheets):
                ws = wb[sh] if sh in wb.sheetnames else (wb.worksheets[0] if i == 0 else None)
                if ws is not None:
                    out[sh] = _sheet_rows(ws)
        finally:
            wb.close()
        return x, out
    c = folder / f"{stem}.csv"
    if c.exists():
        out[sheets[0]] = read_csv(c)
        return c, out
    return None, out


def read_csv(path: Path) -> Tuple[List[str], List[dict]]:
    if not path.exists() or not path.stat().st_size:
        return [], []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        rows = [{k: s(v) for k, v in r.items() if k} for r in rd]
        return list(rd.fieldnames or []), rows


def sha256_of(path: Optional[Path]) -> str:
    if not path or not Path(path).exists():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ==========================================================================
# Writing (every file goes to *.tmp first and is then renamed, so a crash
# never leaves a half-written or 0-byte file)
# ==========================================================================
def _atomic(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.with_name(path.name + ".tmp")


def write_text(path: Path, text: str) -> None:
    tmp = _atomic(path)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _csv_value(v):
    if isinstance(v, bool):
        return yn(v)
    if v is None:
        return ""
    if isinstance(v, float):
        return "" if math.isnan(v) else (str(int(v)) if v.is_integer() and abs(v) < 1e15 else f"{v:.6g}")
    return v


def write_csv(path: Path, rows: List[dict], fields: List[str]) -> None:
    tmp = _atomic(path)
    with tmp.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(fields)
        for r in rows:
            w.writerow([_csv_value(r.get(f, "")) for f in fields])
    os.replace(tmp, path)


def _xl(v):
    if isinstance(v, bool):
        return yn(v)
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    if isinstance(v, (int, float)):
        return v
    return _CTRL.sub("", str(v))[:32000]   # Excel's cell limit (as make_master._cell)


WIDE = {"event": 40, "organisation": 30, "sentence": 60, "context_before": 50, "context_after": 50,
        "left_ctx": 45, "right_ctx": 45, "source_link": 40, "example_context_1": 50, "example_context_2": 50,
        "variants": 30, "item": 28, "value": 90, "text": 100, "memo": 40, "notes": 30, "top1_doc": 50,
        "sites": 30, "description": 70, "name_a": 30, "name_b": 30, "top_event": 40, "document": 40}


def write_xlsx(path: Path, sheets: List[Tuple[str, List[dict], List[str]]],
               validations: Optional[Dict[str, Dict[str, List[str]]]] = None) -> None:
    """One sheet per (title, rows, fields), streamed (write-only): bold frozen header,
    filters, widths. `validations` = {sheet: {column: [allowed values]}} adds
    dropdown lists (from a hidden 'lists' range when given)."""
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation
    bold = Font(bold=True)
    wrap = Alignment(wrap_text=True, vertical="top")
    wb = Workbook(write_only=True)
    for title, rows, fields in sheets:
        ws = wb.create_sheet(title[:31])
        for i, f in enumerate(fields, 1):
            ws.column_dimensions[get_column_letter(i)].width = WIDE.get(f, 14)
        ws.freeze_panes = "A2"
        last = get_column_letter(max(1, len(fields)))
        if rows:
            ws.auto_filter.ref = f"A1:{last}{len(rows) + 1}"
        for col, allowed in ((validations or {}).get(title) or {}).items():
            if col not in fields:
                continue
            letter = get_column_letter(fields.index(col) + 1)
            dv = DataValidation(type="list", formula1='"' + ",".join(allowed) + '"', allow_blank=True)
            dv.add(f"{letter}2:{letter}{max(len(rows), 1) + 500}")
            ws.add_data_validation(dv)
        hdr = []
        for f in fields:
            c = WriteOnlyCell(ws, value=f)
            c.font = bold
            hdr.append(c)
        ws.append(hdr)
        for r in rows:
            cells = []
            for f in fields:
                v = _xl(r.get(f, ""))
                c = WriteOnlyCell(ws, value=v)
                if isinstance(v, str) and v.startswith("="):
                    c.data_type = "s"          # text, never a formula
                if WIDE.get(f, 0) >= 40:
                    c.alignment = wrap
                cells.append(c)
            ws.append(cells)
    tmp = _atomic(path)
    wb.save(tmp)
    os.replace(tmp, path)


def clean_graph(G):
    """A copy whose attributes the GEXF and GraphML writers accept: None and NaN
    become "", lists and sets "; "-joined text, control characters removed."""
    import networkx as nx

    def fix(v):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return ""
        if isinstance(v, bool):
            return yn(v)
        if isinstance(v, (list, tuple, set, frozenset)):
            v = "; ".join(str(x) for x in (sorted(v) if isinstance(v, (set, frozenset)) else v))
        if isinstance(v, (int, float)):
            return v
        return _CTRL.sub("", str(v))
    H = nx.Graph()
    for n, d in G.nodes(data=True):
        H.add_node(_CTRL.sub("", str(n)), **{k: fix(v) for k, v in d.items()})
    for a, b, d in G.edges(data=True):
        H.add_edge(_CTRL.sub("", str(a)), _CTRL.sub("", str(b)), **{k: fix(v) for k, v in d.items()})
    return H


def write_graph(G, base: Path) -> List[Path]:
    import networkx as nx
    H = clean_graph(G)
    out = []
    for ext, fn in ((".gexf", nx.write_gexf), (".graphml", nx.write_graphml)):
        p = base.with_suffix(ext)
        tmp = _atomic(p)
        fn(H, str(tmp))
        os.replace(tmp, p)
        out.append(p)
    return out


# ==========================================================================
# The analysis: inputs and core tables
# ==========================================================================
class InputError(Exception):
    pass


class Analysis:
    """Reads one results folder and builds the analysis-ready tables (events,
    ties, theme units) that every analysis below works on."""

    def __init__(self, args, project: Project):
        self.a = args
        self.p = project
        self.results = Path(args.results)
        self.runs = [Path(r) for r in (args.run or [])]
        self.inputs: Dict[str, Path] = {}
        self.skipped: Dict[str, str] = {}
        self.warnings: List[str] = []
        self.diag: List[dict] = []
        self.tables: "OrderedDict[str, Tuple[List[dict], List[str]]]" = OrderedDict()
        self.facts: Dict[str, object] = {}

    # ------------------------------------------------------------ helpers
    def warn(self, msg: str, *args) -> None:
        text = msg % args if args else msg
        log.warning(text)
        self.warnings.append(text)

    def note(self, item: str, value, note: str = "") -> None:
        self.diag.append({"item": item, "value": value, "note": note})

    def table(self, name: str, rows: List[dict], fields: List[str]) -> None:
        self.tables[name] = (rows, fields)

    def optional(self, name: str) -> Optional[Path]:
        for d in [self.results] + self.runs:
            p = d / name
            if p.exists() and p.stat().st_size:
                self.inputs[name] = p
                return p
        return None

    # ------------------------------------------------------------ loading
    def load(self) -> None:
        a = self.a
        path, net = read_master(self.results, "master_network", ["event_organisations", "menus_and_footers"])
        if path is None:
            raise InputError(f"master_network.xlsx or master_network.csv not found in {self.results}")
        self.inputs["master_network"] = path
        path, th = read_master(self.results, "master_themes", ["theme_instances", "menus_and_sidebars"])
        if path is None:
            raise InputError(f"master_themes.xlsx or master_themes.csv not found in {self.results}")
        self.inputs["master_themes"] = path
        ev_path = self.results / "events.csv"
        if not ev_path.exists():
            raise InputError(f"events.csv not found in {self.results}")
        self.inputs["events.csv"] = ev_path
        self.net_fields, self.net_rows = net["event_organisations"]
        self.menu_rows = net["menus_and_footers"][1]
        self.th_fields, self.th_rows = th["theme_instances"]
        self.th_menu_rows = th["menus_and_sidebars"][1]
        self.ev_fields, self.ev_rows = read_csv(ev_path)
        need_net = {"event", "event_date", "event_link", "organisation", "confidence", "source_link", "site",
                    "site_role"}
        need_th = {"theme", "term", "event", "event_date", "event_link", "source_link", "sentence"}
        for label, fields, need, rows in (("master_network", self.net_fields, need_net, self.net_rows),
                                          ("master_themes", self.th_fields, need_th, self.th_rows)):
            miss = need - set(fields)
            if fields and miss:
                raise InputError(f"{label}: required columns missing: {', '.join(sorted(miss))}")
        if not {"event", "date", "event_link"} <= set(self.ev_fields):
            raise InputError("events.csv: needs the columns event, date, event_link")
        self.long_net = "event_key" in self.net_fields
        self.long_th = "event_key" in self.th_fields
        self.note("layout master_network", "long (event_key)" if self.long_net else "older (' | '-joined)")
        self.note("layout master_themes", "long (event_key)" if self.long_th else "older (' | '-joined)")
        self.ts = None
        tf = Path(a.themes_file) if a.themes_file else DEFAULT_THEMES_FILE
        if tf.exists():
            self.inputs["themes_file"] = tf
            self.ts = self.p.extract.ThemeSet.load(tf)
            self.sections = self._theme_sections(tf)
        else:
            self.warn("theme file %s not found: term families, title flags and entry rates are skipped", tf)
            self.sections = {}
        names = list(self.ts.names) if self.ts else []
        for r in self.th_rows:
            if r.get("theme") and r["theme"] not in names:
                names.append(r["theme"])
        self.themes = names or ["colonialism", "racism", "fascism"]
        for name in ("seed_links.json", "pages.csv", "sites.csv", "sources.csv", "summary.json"):
            self.optional(name)
        self.org_codes = self._read_codes(a.org_codes, "org_codes") if a.org_codes else []
        self.event_codes = self._read_codes(a.event_codes, "event_codes") if a.event_codes else []
        self.curated = bool(self.org_codes)

    def _read_codes(self, path: str, label: str) -> List[dict]:
        p = Path(path)
        if not p.exists():
            raise InputError(f"--{label.replace('_', '-')}: {p} not found")
        self.inputs[label] = p
        if p.suffix.lower() == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
            try:
                return _sheet_rows(wb.worksheets[0])[1]
            finally:
                wb.close()
        return read_csv(p)[1]

    @staticmethod
    def _theme_sections(path: Path) -> Dict[Tuple[str, str], str]:
        sec, cur, out = "", None, {}
        for line in path.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if t.startswith("# ---"):
                sec = t[5:].split(":")[0].strip()
            elif re.fullmatch(r"\[(.+)\]", t):
                cur = t[1:-1].strip()
            elif t and not t.startswith(("#", "!")) and cur:
                out[(cur, t)] = sec
        return out

    # ------------------------------------------------------------ events
    def build_events(self) -> None:
        p = self.p
        self.events: List[dict] = []
        self.ev_by_id: Dict[str, dict] = {}
        self.by_key: Dict[str, str] = {}
        self.by_nld: Dict[tuple, List[str]] = defaultdict(list)
        self.by_nl: Dict[tuple, List[str]] = defaultdict(list)
        self.by_ld: Dict[tuple, List[str]] = defaultdict(list)
        self.by_link: Dict[str, List[str]] = defaultdict(list)
        for r in self.ev_rows:
            name, date, link, key = r.get("event", ""), r.get("date", ""), r.get("event_link", ""), r.get("event_key", "")
            base = f"k\x1f{key}" if key else f"{link}\x1f{date}\x1f{name}"
            eid, n = sha12(base), 1
            while eid in self.ev_by_id:
                n += 1
                eid = sha12(f"{base}\x1f{n}")
            parts = [x.strip() for x in date.split(" to ")]
            start = parts[0] if parts else ""
            listed = [x for x in re.split(r";\s+", r.get("listed_on", "")) if x]
            streams = list(dict.fromkeys(stream_of(x) for x in listed)) or ["other"]
            ev = {"event_id": eid, "event": name, "date": date, "start": start,
                  "end": parts[1] if len(parts) > 1 else "", "year": year_of(start or date),
                  "event_link": link, "event_key": key, "link_host": host_of(link),
                  "listed_on": r.get("listed_on", ""), "stream_primary": streams[0], "_streams": streams,
                  "entry_text": "", "location_structured": r.get("location_structured", ""), "_row": r}
            ev["period"] = period_of(ev["year"])
            self.events.append(ev)
            self.ev_by_id[eid] = ev
            if key:
                self.by_key.setdefault(key, eid)
            self.by_nld[(name, link, date)].append(eid)
            self.by_nl[(name, link)].append(eid)
            self.by_ld[(link, date)].append(eid)
            self.by_link[link].append(eid)
        if len(self.events) != len({(e['event_link'], e['date'], e['event']) for e in self.events}):
            self.note("events with the same name, date and link", len(self.events) - len(
                {(e['event_link'], e['date'], e['event']) for e in self.events}), "kept apart by event_key")
        self.note("events (events.csv rows)", len(self.events))
        # event_codes: series, duplicates, country overrides
        self.alias: Dict[str, str] = {}
        self.ev_codes = {}
        for c in self.event_codes:
            eid = c.get("event_id", "")
            if eid not in self.ev_by_id:
                if eid:
                    self.warn("event_codes: event_id %s is not in events.csv (ignored)", eid)
                continue
            self.ev_codes[eid] = c
            dup = c.get("duplicate_of", "")
            if dup and dup in self.ev_by_id and dup != eid:
                self.alias[eid] = dup
        self._seed_entries()
        self._countries()
        for ev in self.events:
            c = self.ev_codes.get(ev["event_id"], {})
            link = ev["event_link"]
            if c.get("series_id"):
                ev["series_id"] = c["series_id"]
            elif is_web(link) and not p.is_platform(link):
                ev["series_id"] = norm_link(link)
            else:
                ev["series_id"] = ev["event_id"]
            text = ev["entry_text"]
            ev["entry_words"] = (int(ev["_row"]["entry_words"]) if s(ev["_row"].get("entry_words")).isdigit()
                                 else len(_WORD.findall(p.URL_LIKE.sub(" ", text))) if text else 0)
            ev["entry_lang"] = ""
            if text and p.detect_language:
                try:
                    ev["entry_lang"] = p.detect_language(p.URL_LIKE.sub(" ", text))
                except Exception:  # noqa: BLE001
                    ev["entry_lang"] = ""
        self.series_of = {e["event_id"]: e["series_id"] for e in self.events}

    def _seed_entries(self) -> None:
        """Agenda entries of the seed PDFs (seed_links.json): entry text and the
        full set of agendas an event is listed in (events.csv keeps one source in
        older runs)."""
        self.entries: List[dict] = []
        path = self.inputs.get("seed_links.json")
        if not path:
            self.skipped["entry_rates"] = "seed_links.json not found (give the run folder with --run)"
            self.skipped["agenda_overlap"] = "seed_links.json not found"
            for ev in self.events:
                ev["streams"] = ev["_streams"]
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pdfs = data.get("pdfs") or {}
        except (OSError, ValueError) as exc:
            self.skipped["entry_rates"] = f"seed_links.json unreadable ({exc})"
            for ev in self.events:
                ev["streams"] = ev["_streams"]
            return
        keys = links = None
        try:
            from site_actors import assign_entry_keys
            keys, links = assign_entry_keys(pdfs, set())
            how = "site_actors.assign_entry_keys"
        except Exception as exc:  # noqa: BLE001 - module being edited or older version
            log.info("site_actors.assign_entry_keys not usable (%s); simple entry keys", exc)
            how = "simple link#date keys"
        for pdf, d in pdfs.items():
            st = stream_of(pdf_display(pdf))
            for e in d.get("entries") or []:
                idx = (pdf, e.get("index"))
                if keys is not None:
                    key, link = keys.get(idx, ""), links.get(idx, "")
                else:
                    good = [u for u in e.get("links") or []
                            if self.p.filter_targets is None or self.p.filter_targets({u: []}, set())]
                    link = good[0] if good else ((e.get("links") or [""])[0])
                    when = e.get("start") or e.get("month") or f"entry-{(e.get('index') or 0) + 1}"
                    key = f"{good[0]}#{when}" if good else f"{pdf}#entry-{(e.get('index') or 0) + 1}"
                self.entries.append({"pdf": pdf, "stream": st, "index": e.get("index"), "name": e.get("name", ""),
                                     "start": e.get("start") or "", "month": e.get("month") or "",
                                     "lines": e.get("lines") or [], "key": pdf_display(key), "raw_key": key,
                                     "link": pdf_display(link) if link else pdf_display(pdf)})
        by_key = defaultdict(set)
        for en in self.entries:
            by_key[en["key"]].add(en["pdf"])
        both = sum(1 for v in by_key.values() if len(v) > 1)
        self.note("seed entries", len(self.entries), "; ".join(f"{k}: {v}" for k, v in sorted(
            Counter(en["stream"] for en in self.entries).items())))
        self.note("seed entry keys (rebuilt)", len(by_key), how)
        self.note("seed entry keys listed in two or more agenda PDFs", both)
        # entries -> events
        ev_key = {e["event_key"]: e["event_id"] for e in self.events if e["event_key"]}
        by_ls = defaultdict(list)
        by_ns = defaultdict(list)
        for e in self.events:
            by_ls[(norm_link(pdf_display(e["event_link"])), e["start"])].append(e["event_id"])
            by_ns[(collapse(e["event"]).casefold(), e["start"])].append(e["event_id"])
        matched = 0
        self.entries_of: Dict[str, List[dict]] = defaultdict(list)
        for en in self.entries:
            eid = ev_key.get(en["key"]) or ev_key.get(en["raw_key"])
            if not eid:
                when = en["start"] or en["month"]
                c = by_ls.get((norm_link(en["link"]), when)) or []
                if len(c) != 1:
                    c = by_ns.get((collapse(en["name"]).casefold(), when)) or c
                eid = c[0] if len(c) == 1 else None
            en["event_id"] = eid
            if eid:
                matched += 1
                self.entries_of[eid].append(en)
        self.note("seed entries matched to events.csv", f"{matched}/{len(self.entries)}")
        for ev in self.events:
            ens = self.entries_of.get(ev["event_id"], [])
            if ens:
                ev["entry_text"] = "\n".join(ens[0]["lines"])
            ev["streams"] = list(dict.fromkeys(ev["_streams"] + [en["stream"] for en in ens]))
            if ev["stream_primary"] == "other" and ens:
                ev["stream_primary"] = ens[0]["stream"]
        multi = sum(1 for ev in self.events if len([x for x in ev["streams"] if x != "hub"]) > 1)
        self.facts["events_in_two_agendas"] = multi

    def _countries(self) -> None:
        p = self.p
        rows_have = "country_iso" in self.ev_fields and any(e["_row"].get("country_iso") for e in self.events)
        self.country_from = "events.csv" if rows_have else ("gazetteer" if p.gazetteer else "none")
        if not rows_have and p.gazetteer is None:
            self.skipped["country"] = f"no country columns in events.csv and gazetteer.py not importable ({p.gazetteer_error})"
        for ev in self.events:
            r = ev["_row"]
            if rows_have:
                isos = [x for x in re.split(r";\s*", r.get("country_iso", "")) if x]
                method, text = r.get("country_source", ""), r.get("location_text", "")
            else:
                res = p.country(ev["event"], ev["entry_text"], ev["event_link"] if is_web(ev["event_link"]) else "",
                                ev["location_structured"])
                isos = [str(x) for x in res.get("countries") or []]
                method = s(res.get("method"))
                text = res.get("matched", "")
                text = "; ".join(text) if isinstance(text, (list, tuple)) else s(text)
            c = self.ev_codes.get(ev["event_id"], {})
            if c.get("country_override"):
                isos = [x.strip().upper() for x in re.split(r"[;,]", c["country_override"]) if x.strip()]
                method, text = "override", "event_codes.csv"
            ev["countries"] = isos
            ev["country_method"] = method if isos else "none"
            ev["location_text"] = text if isos else ""
            ev["country_names"] = [p.country_name(x) for x in isos]
        n = sum(1 for e in self.events if e["countries"])
        self.note("events with a country", f"{n}/{len(self.events)}", f"from {self.country_from}")

    # ------------------------------------------------------------ refs
    def _explode(self, r: dict, long_format: bool) -> List[Tuple[str, str, str, str]]:
        if long_format:
            return [(r.get("event", ""), r.get("event_date", ""), r.get("event_link", ""), r.get("event_key", ""))]
        names = r.get("event", "").split(" | ")
        dates = r.get("event_date", "").split(" | ")
        links = r.get("event_link", "").split(" | ")
        if not any(names) and not any(links):
            return [(NO_EVENT, "", "", "")]
        if len(names) > len(links) == 1 and len(dates) <= 1:
            names = [" | ".join(names)]      # one event whose name contains " | "
        n = max(len(names), len(dates), len(links))
        if len(names) != n and len(links) == n:  # re-align names on the event table
            out = []
            for i, ln in enumerate(links):
                d = dates[i] if i < len(dates) else ""
                c = self.by_ld.get((ln, d)) or self.by_link.get(ln) or []
                nm = self.ev_by_id[c[0]]["event"] if len(set(c)) == 1 else (names[i] if i < len(names) else "")
                out.append((nm, d, ln, ""))
            return out
        pad = lambda xs: xs + [""] * (n - len(xs))  # noqa: E731
        return [(a, b, c, "") for a, b, c in zip(pad(names), pad(dates), pad(links))]

    def _match(self, name: str, date: str, link: str, key: str) -> Tuple[Optional[str], str]:
        if (not name and not link) or name.startswith("("):
            return None, "pseudo"
        if key:
            eid = self.by_key.get(key) or self.by_key.get(pdf_display(key))
            if eid:
                return self.alias.get(eid, eid), "key"
        c = self.by_nld.get((name, link, date), [])
        if len(c) == 1:
            return self.alias.get(c[0], c[0]), "exact"
        c = self.by_nl.get((name, link), [])
        if len(c) == 1:
            return self.alias.get(c[0], c[0]), "name+link"
        c = self.by_ld.get((link, date), [])
        if len(c) == 1:
            return self.alias.get(c[0], c[0]), "link+date"
        return None, "unmatched"

    @staticmethod
    def _layer(row: dict, ev_link: str, pseudo: bool) -> str:
        if row.get("layer"):
            return row["layer"]
        url = row.get("source_link", "")
        if row.get("site_role") == "seed" or url.startswith(("file:", "pdf:")):
            return "listing_seed"
        if "calendar_month=" in url:
            return "listing_calendar"
        if row.get("site_role") == "main":
            return "main_page" if pseudo else "main_event_page"
        if ev_link and norm_link(ev_link) == norm_link(url):
            return "landing"
        pdf = truthy(row.get("is_pdf")) or bool(re.search(r"\.pdf(?:\?|$)", url, re.I))
        return "linked_pdf" if pdf else "linked_page"

    @staticmethod
    def _unit(row: dict, layer: str, eid: Optional[str], name: str) -> str:
        if row.get("source_unit"):
            return row["source_unit"]
        if layer == "listing_seed":
            return f"{row.get('source_link', '')}#{eid or name}"
        return row.get("source_link", "")

    def _doc_year(self, url: str, page_date: str) -> Optional[int]:
        y = doc_year_of(url)
        if y is None and page_date:
            y = year_of(page_date)
        if y is None:
            y = self.page_year.get(url)
        return y

    def build_refs(self) -> None:
        self.page_year: Dict[str, int] = {}
        self.pages = read_csv(self.inputs["pages.csv"])[1] if "pages.csv" in self.inputs else []
        for pg in self.pages:
            y = year_of(pg.get("date") or pg.get("page_date") or "")
            if y:
                self.page_year[pg.get("url", "")] = y
        # ---- network ties
        self.ties: List[dict] = []
        counts = {"event_organisations": Counter(), "menus_and_footers": Counter()}
        self.unmatched_names: Counter = Counter()
        for sheet, rows in (("event_organisations", self.net_rows), ("menus_and_footers", self.menu_rows)):
            for r in rows:
                refs = self._explode(r, self.long_net)
                for name, date, link, key in refs:
                    eid, how = self._match(name, date, link, key)
                    counts[sheet][how] += 1
                    if how == "unmatched":
                        self.unmatched_names[name] += 1
                        continue
                    pseudo = eid is None
                    layer = self._layer(r, link, pseudo)
                    url = r.get("source_link", "")
                    unit = self._unit(r, layer, eid, name)
                    sht = r.get("source_has_theme", "")
                    self.ties.append({
                        "event_id": eid or "", "match": how, "pseudo_label": name if pseudo else "",
                        "organisation": collapse(r.get("organisation", "")),
                        "raw_key": r.get("org_key") or self.p.key(r.get("organisation", "")),
                        "confidence": r.get("confidence", "") or "low", "source_link": url, "unit": unit,
                        "document": unit if layer == "listing_seed" else url,
                        "site": r.get("site", ""), "site_role": r.get("site_role", ""), "layer": layer,
                        "tie_level": TIE_LEVEL.get(layer, "L3"),
                        "named_in_event_title": truthy(r.get("named_in_event_title")),
                        "in_menus": sheet == "menus_and_footers" or truthy(r.get("only_in_menus_or_footers")),
                        "found_by": r.get("found_by", ""), "mentions": int(r.get("mentions_on_source") or 1)
                        if s(r.get("mentions_on_source")).isdigit() else 1,
                        "source_has_theme": truthy(sht) if sht else None,
                        "example": r.get("example_context", ""),
                        "doc_year": self._doc_year(url, r.get("page_date", "")), "sheet": sheet})
        for sheet, c in counts.items():
            self.note(f"event refs, {sheet}", "; ".join(f"{k} {v}" for k, v in sorted(c.items())) or "0")
        self.ref_counts = counts
        if self.unmatched_names:
            self.note("unmatched event refs (dropped)", sum(self.unmatched_names.values()),
                      "; ".join(f"{k!r} x{v}" for k, v in self.unmatched_names.most_common(10)))
        # ---- theme instances
        self.instances: List[dict] = []
        for sheet, rows in (("theme_instances", self.th_rows), ("menus_and_sidebars", self.th_menu_rows)):
            groups: "OrderedDict[tuple, dict]" = OrderedDict()
            for i, r in enumerate(rows):
                gk = ((sheet, r.get("source_unit") or r.get("source_link", ""), r.get("theme", ""),
                       r.get("term", ""), r.get("sentence", ""), r.get("context_before", ""),
                       r.get("context_after", ""), r.get("region", "")) if self.long_th else (sheet, i))
                g = groups.get(gk)
                if g is None:
                    g = groups[gk] = {"row": r, "sheet": sheet, "refs": []}
                for name, date, link, key in self._explode(r, self.long_th):
                    g["refs"].append((name, date, link, key, r.get("layer", "")))
            for g in groups.values():
                self.instances.append(self._instance(g))
        self.theme_refs = [rf for inst in self.instances for rf in inst["refs"]]
        tc = Counter(rf["match"] for rf in self.theme_refs)
        self.note("event refs, theme rows", "; ".join(f"{k} {v}" for k, v in sorted(tc.items())))
        self.theme_ref_counts = tc

    def _instance(self, g: dict) -> dict:
        r = g["row"]
        theme, term, sent = r.get("theme", ""), r.get("term", ""), r.get("sentence", "")
        furniture = (truthy(r.get("boilerplate")) or r.get("region", "") in FURNITURE_REGIONS
                     or truthy(r.get("menu_or_sidebar")) or g["sheet"] == "menus_and_sidebars")
        dup = bool(r.get("duplicate_of"))
        family, section = self._family(theme, term)
        low = sent.casefold()
        tl = term.casefold()
        inst = {"theme": theme, "term": term, "sentence": sent, "context_before": r.get("context_before", ""),
                "context_after": r.get("context_after", ""), "source_link": r.get("source_link", ""),
                "site": r.get("site", ""), "site_role": r.get("site_role", ""), "is_pdf": truthy(r.get("is_pdf")),
                "region": r.get("region", ""), "furniture": furniture, "dup": dup, "clean": not furniture and not dup,
                "passage_id": sha12(r.get("source_link", "") + "\n" + collapse(sent).casefold()),
                "term_family": family, "language_section": section,
                "subtheme": subtheme(theme, term), "stance": stance(term, sent[:max(0, low.find(tl))] if tl in low else ""),
                "in_handle": bool(tl and re.search(r"[@#][\w.-]*" + re.escape(tl), low)),
                "digit_glued": bool(tl and re.search(r"\d" + re.escape(tl), low)),
                "orgs_text": r.get("organisations_in_paragraph", ""),
                "org_keys_text": r.get("organisations_in_paragraph_keys", ""),
                "page_date": r.get("page_date", ""), "refs": []}
        refs = g["refs"]
        inst["doc_year"] = self._doc_year(inst["source_link"], inst["page_date"])
        w = 1 / max(1, len(refs))
        names = []
        for name, date, link, key, layer in refs:
            eid, how = self._match(name, date, link, key)
            pseudo = eid is None
            lay = self._layer({**r, "layer": layer}, link, pseudo)
            unit = self._unit(r, lay, eid, name)
            ev = self.ev_by_id.get(eid) if eid else None
            names.append(ev["event"] if ev else name)
            inst["refs"].append({"inst": inst, "event_id": eid or "", "match": how,
                                 "pseudo_label": name if pseudo else "", "layer": lay,
                                 "tie_level": TIE_LEVEL.get(lay, "L3"), "unit": unit,
                                 "document": unit if lay == "listing_seed" else inst["source_link"], "w": w})
        inst["in_event_title"] = bool(tl) and any(tl in n.casefold() for n in names)
        inst["event_names"] = names
        return inst

    def _family(self, theme: str, term: str) -> Tuple[str, str]:
        if not self.ts:
            return "", ""
        t = term.strip()
        for raw, pat in zip(self.ts.raw.get(theme, []), self.ts.themes.get(theme, [])):
            try:
                if pat.fullmatch(t) or pat.fullmatch(t.translate(getattr(self.p.extract, "_GREEK_FOLD", {}))):
                    return raw, self.sections.get((theme, raw), "")
            except Exception:  # noqa: BLE001
                continue
        return "(unmapped)", ""


# ==========================================================================
# Term model: sub-themes and stance (rule-based, first match wins)
# ==========================================================================
SUBTHEME_RULES = {
    "colonialism": [("decolonial", r"decolon|dekolon|décolon|descolon"), ("anticolonial", r"anti.?colon|antikolon"),
                    ("neocolonial", r"neo.?colon|néo.?colon|neokolon"), ("extractivism", r"extracti|extrativ|estratti|extraktiv"),
                    ("global_south", r"global south|süden|sud global|sur global|sud del mondo"),
                    ("reparations", r"reparation|réparation|climate debt|dette|klimaschuld"),
                    ("imperialism", r"imperial|impérial|empire")],
    "racism": [("antiracism", r"anti.?ras|anti.?rac|antirazz"), ("whiteness", r"white (?:supremacy|privilege)"),
               ("xenophobia", r"xeno"), ("antisemitism", r"antisemit|anti-semit"), ("islamophobia", r"islamoph"),
               ("environmental", r"environmental rac"), ("structural", r"racial|racis(?:ed|ation)|racisé|structural")],
    "fascism": [("antifascism", r"antifa|anti.?fa[sš]c|anti.?fasch"),
                ("compound", r"(?:techno|eco|öko|oko|fossil|klima).?fa[sš]"),
                ("nazism", r"nazi"), ("far_right", r"far.?right|extrême.?droite|extreme.?droite|rechts"),
                ("authoritarianism", r"autorit|authorit")],
}
_AGAINST = re.compile(r"(?:^|\W)(?:against|contre|gegen|contro|tegen|stop)\W", re.I)


def subtheme(theme: str, term: str) -> str:
    t = term.casefold()
    for name, pat in SUBTHEME_RULES.get(theme, []):
        if re.search(pat, t):
            return name
    return "core"


def stance(term: str, left: str) -> str:
    t = term.casefold()
    if "anti" in t:
        return "anti"
    if re.match(r"(?:de|dé)-?", t) and re.match(r"(?:de|dé)-?(?:colon|kolon)", t):
        return "de"
    if _AGAINST.search(" " + left[-40:] + " "):
        return "against"
    return "unmarked"


# ==========================================================================
# Theme filter (R1), organisation identity, event coverage
# ==========================================================================
def _derive(an: Analysis) -> None:
    """Theme-filter sets, organisation keys, per-event coverage and theme flags."""
    p = an.p
    # ---- R1: sources (pages, PDFs, agenda entries) with a theme hit in their content
    units, sites, evs = set(), set(), set()
    for inst in an.instances:
        if inst["furniture"]:
            continue
        sites.add(inst["site"])
        for rf in inst["refs"]:
            units.add(rf["unit"])
            if rf["event_id"]:
                evs.add(rf["event_id"])
    an.theme_units_set, an.theme_sites, an.theme_events = units, sites, evs
    for t in an.ties:
        src = t["source_has_theme"] if t["source_has_theme"] is not None else t["unit"] in units
        t["pass"] = {"none": True, "source": src, "site": t["site"] in sites,
                     "event": (t["event_id"] in evs) if t["event_id"] else src}
    tf = an.a.theme_filter
    real = [t for t in an.ties if t["sheet"] == "event_organisations"]
    kept = [t for t in real if t["pass"][tf]]
    an.filter_counts = {
        "rows": (len(kept), len(real)),
        "organisations": (len({t["raw_key"] for t in kept}), len({t["raw_key"] for t in real})),
        "events": (len({t["event_id"] for t in kept if t["event_id"]}), len({t["event_id"] for t in real if t["event_id"]})),
        "sources": (len({t["unit"] for t in kept}), len({t["unit"] for t in real}))}
    if tf != "none":
        fc = an.filter_counts
        log.info("Theme filter '%s': kept %d of %d network rows, %d of %d organisations, %d of %d events, "
                 "%d of %d sources (use --theme-filter none to keep everything)", tf,
                 *fc["rows"], *fc["organisations"], *fc["events"], *fc["sources"])
    for k, (a_, b_) in an.filter_counts.items():
        an.note(f"theme filter '{tf}': {k} kept", f"{a_}/{b_}", f"{b_ - a_} left out")
    # ---- organisations
    _org_identity(an)
    # ---- event coverage
    pages_by_event = defaultdict(lambda: [0, 0])
    for pg in an.pages:
        for nm in [x for x in re.split(r";\s+", pg.get("via_event", "")) if x]:
            pe = pages_by_event[nm]
            pe[0] += 1
            pe[1] += int(pg.get("words") or 0) if s(pg.get("words")).isdigit() else 0
    read_hosts = set()
    sites_rows = read_csv(an.inputs["sites.csv"])[1] if "sites.csv" in an.inputs else []
    for sr in sites_rows:
        n = int(sr.get("pages_analysed") or 0) if s(sr.get("pages_analysed")).isdigit() else 0
        if sr.get("role") == "linked" and n >= 1:
            read_hosts.add(sr.get("site", "").lower())
    row_web = {t["event_id"] for t in an.ties if t["event_id"] and t["layer"] != "listing_seed"}
    row_web |= {rf["event_id"] for rf in an.theme_refs if rf["event_id"] and rf["layer"] != "listing_seed"}
    csv_followed = "link_followed" in an.ev_fields
    if csv_followed:
        an.web_method = "events.csv (link_followed) + rows"
    elif an.pages or sites_rows:
        an.web_method = "sites+pages"
    else:
        an.web_method = "proxy"
    for ev in an.events:
        r = ev["_row"]
        host = ev["link_host"]
        via = pages_by_event.get(ev["event"], [0, 0])
        on_site = bool(host) and p.host_matches(host, read_hosts)
        if csv_followed:
            followed = truthy(r.get("link_followed"))
        elif sites_rows:
            followed = on_site
        else:
            followed = ev["event_id"] in row_web and is_web(ev["event_link"])
        ev["has_link"] = is_web(ev["event_link"]) and not ev["event_link"].startswith(("file:", "pdf:"))
        ev["link_followed"] = followed
        ev["has_web_text"] = followed or on_site or via[0] > 0 or ev["event_id"] in row_web
        ev["n_pages"] = int(r["pages_analysed"]) if s(r.get("pages_analysed")).isdigit() else via[0]
        ev["n_words_web"] = int(r["words_analysed"]) if s(r.get("words_analysed")).isdigit() else via[1]
    an.note("events with web text (has_web_text)", f"{sum(e['has_web_text'] for e in an.events)}/{len(an.events)}",
            an.web_method)
    # ---- event theme flags (clean units only)
    flags = defaultdict(lambda: defaultdict(set))   # eid -> theme -> {L1, L2, L3}
    for inst in an.instances:
        if not inst["clean"]:
            continue
        for rf in inst["refs"]:
            if rf["event_id"]:
                lvl = "own" if rf["layer"] in OWN_LAYERS else LAYER_GROUP.get(rf["tie_level"], "linked")
                if rf["layer"] == "main_page":
                    lvl = "linked"
                flags[rf["event_id"]][inst["theme"]].add(lvl)
    for ev in an.events:
        f = flags.get(ev["event_id"], {})
        title_hits = an.ts.find(ev["event"]) if an.ts else {}
        for th in an.themes:
            lv = f.get(th, set())
            ev[f"{th}_own"] = "own" in lv
            ev[f"{th}_landing"] = "landing" in lv
            ev[f"{th}_linked"] = "linked" in lv
            ev[f"{th}_any"] = bool(lv)
            ev[f"{th}_in_title"] = th in title_hits
        for m in ("own", "landing", "linked", "any", "in_title"):
            ev[f"any_{m}"] = any(ev[f"{th}_{m}"] for th in an.themes)


def _fold_umlaut(k: str) -> str:
    return k.replace("ae", "a").replace("oe", "o").replace("ue", "u")


def _org_identity(an: Analysis) -> None:
    p = an.p
    surfaces: Dict[str, Counter] = defaultdict(Counter)
    for t in an.ties:
        surfaces[t["raw_key"]][t["organisation"]] += 1
    an.para_orgs: Dict[int, List[Tuple[str, str]]] = {}
    for i, inst in enumerate(an.instances):
        names = [x.strip() for x in inst["orgs_text"].split("; ") if x.strip()]
        keys = [x.strip() for x in inst["org_keys_text"].split("; ")] if inst["org_keys_text"] else []
        pairs = [(nm, keys[j] if j < len(keys) and keys[j] else p.key(nm)) for j, nm in enumerate(names)]
        an.para_orgs[i] = pairs
        for nm, k in pairs:
            surfaces[k][nm] += 1
    # Umlaut transliteration variants ("Ende Gelaende" / "Ende Gelände") merge automatically,
    # but only when the folded key exists and is written with an umlaut somewhere.
    umlaut = {}
    for k in surfaces:
        if re.search(r"ae|oe|ue", k):
            f = _fold_umlaut(k)
            if f != k and f in surfaces and any(re.search(r"[äöüÄÖÜ]", sf) for sf in surfaces[f]):
                umlaut[k] = f
    an.umlaut_merges = umlaut
    codes: Dict[str, dict] = {}
    stale = 0
    for c in an.org_codes:
        name = c.get("organisation", "")
        derived = p.key(name) if name else p.key(c.get("org_key", ""))
        if not derived:
            continue
        if c.get("org_key") and c["org_key"] != derived and p.key(c["org_key"]) != derived:
            stale += 1
        codes[derived] = c
    if stale:
        an.warn("org_codes: %d rows whose org_key no longer matches the organisation name (keys re-derived "
                "from the name; check them)", stale)
    merge = {}
    for k, c in codes.items():
        tgt = c.get("merge_into_key", "")
        if tgt:
            tk = p.key(tgt) or tgt
            if tk != k:
                merge[k] = tk

    def resolve(k: str) -> str:
        k = umlaut.get(k, k)
        seen = set()
        while k in merge and k not in seen:
            seen.add(k)
            k = umlaut.get(merge[k], merge[k])
        return k
    an.resolve = resolve
    an.codes = codes
    final_surfaces: Dict[str, Counter] = defaultdict(Counter)
    variants: Dict[str, Set[str]] = defaultdict(set)
    for k, cnt in surfaces.items():
        fk = resolve(k)
        final_surfaces[fk].update(cnt)
        variants[fk].add(k)
    an.org_name = {}
    for fk, cnt in final_surfaces.items():
        c = codes.get(fk, {})
        an.org_name[fk] = c.get("canonical_name") or sorted(cnt.items(), key=lambda x: (-x[1], x[0]))[0][0]
    an.org_variants = variants
    an.org_surfaces = final_surfaces
    for t in an.ties:
        t["org_key"] = resolve(t["raw_key"])
        c = codes.get(t["org_key"]) or codes.get(t["raw_key"]) or {}
        t["include"] = s(c.get("include"))
        t["actor_class"] = c.get("actor_class", "")
        ev = an.ev_by_id.get(t["event_id"])
        t["series_id"] = an.series_of.get(t["event_id"], "") if ev else ""
        t["year"] = ev["year"] if ev else None
        t["countries"] = ev["countries"] if ev else []
    for i, inst in enumerate(an.instances):
        inst["org_keys"] = list(dict.fromkeys(resolve(k) for _, k in an.para_orgs[i] if k))
    if umlaut:
        an.note("umlaut variants merged automatically", len(umlaut),
                "; ".join(f"{a} -> {b}" for a, b in sorted(umlaut.items())))
    if codes:
        an.note("org_codes rows read", len(codes), f"{len(merge)} merges")


def org_dropped(an: Analysis, k: str) -> bool:
    c = an.codes.get(k, {})
    return s(c.get("include")) in ("0", "no")


def org_class(an: Analysis, k: str) -> str:
    return an.codes.get(k, {}).get("actor_class", "")


# ==========================================================================
# Core analysis-ready tables
# ==========================================================================
def core_tables(an: Analysis) -> None:
    th = an.themes
    n_by_level = defaultdict(lambda: defaultdict(set))
    for t in an.ties:
        if t["event_id"] and not t["in_menus"] and t["pass"][an.a.theme_filter]:
            n_by_level[t["event_id"]][t["tie_level"]].add(t["org_key"])
    rows = []
    for ev in an.events:
        r = {k: ev[k] for k in ("event_id", "event", "date", "start", "end", "year", "period", "event_link",
                                "event_key", "link_host", "series_id", "stream_primary")}
        r.update({"streams": "; ".join(ev["streams"]), "n_streams": len(ev["streams"]),
                  "countries": "; ".join(ev["countries"]), "country_names": "; ".join(ev["country_names"]),
                  "country_method": ev["country_method"], "location_text": ev["location_text"],
                  "entry_words": ev["entry_words"], "entry_lang": ev["entry_lang"],
                  "has_link": ev["has_link"], "link_followed": ev["link_followed"],
                  "has_web_text": ev["has_web_text"], "n_pages": ev["n_pages"], "n_words_web": ev["n_words_web"]})
        for lv in ("L1", "L2", "L3"):
            r[f"n_orgs_{lv}"] = len(n_by_level[ev["event_id"]][lv])
        for t in th:
            for m in ("own", "landing", "linked", "any", "in_title"):
                r[f"{t}_{m}"] = ev[f"{t}_{m}"]
        rows.append(r)
    fields = ["event_id", "event", "date", "start", "end", "year", "period", "event_link", "event_key", "link_host",
              "series_id", "stream_primary", "streams", "n_streams", "countries", "country_names", "country_method",
              "location_text", "entry_words", "entry_lang", "has_link", "link_followed", "has_web_text", "n_pages",
              "n_words_web", "n_orgs_L1", "n_orgs_L2", "n_orgs_L3"] + [
        f"{t}_{m}" for t in th for m in ("own", "landing", "linked", "any", "in_title")]
    an.table("events_enriched", rows, fields)
    # ---- ties
    rows = []
    for t in an.ties:
        rows.append({"event_id": t["event_id"], "pseudo_label": t["pseudo_label"], "series_id": t["series_id"],
                     "org_key": t["org_key"], "organisation": t["organisation"],
                     "canonical_org": an.org_name.get(t["org_key"], t["organisation"]),
                     "actor_class": t["actor_class"], "confidence": t["confidence"], "tie_level": t["tie_level"],
                     "layer": t["layer"], "source_link": t["source_link"], "document": t["document"],
                     "site": t["site"], "named_in_event_title": t["named_in_event_title"], "in_menus": t["in_menus"],
                     "year": t["year"], "countries": "; ".join(t["countries"]),
                     "doc_year": t["doc_year"] if t["doc_year"] else "undated",
                     "source_has_theme": t["pass"]["source"], "passes_theme_filter": t["pass"][an.a.theme_filter],
                     "found_by": t["found_by"], "match": t["match"]})
    an.table("ties", rows, ["event_id", "pseudo_label", "series_id", "org_key", "organisation", "canonical_org",
                            "actor_class", "confidence", "tie_level", "layer", "source_link", "document", "site",
                            "named_in_event_title", "in_menus", "year", "countries", "doc_year", "source_has_theme",
                            "passes_theme_filter", "found_by", "match"])
    # ---- theme units: (passage_id, theme, event)
    units: "OrderedDict[tuple, dict]" = OrderedDict()
    for inst in an.instances:
        for rf in inst["refs"]:
            key = (inst["passage_id"], inst["theme"], rf["event_id"] or rf["pseudo_label"])
            u = units.get(key)
            ev = an.ev_by_id.get(rf["event_id"])
            if u is None:
                u = units[key] = {
                    "passage_id": inst["passage_id"], "theme": inst["theme"], "_terms": [], "_fam": [],
                    "language_section": inst["language_section"], "subtheme": inst["subtheme"],
                    "stance": inst["stance"], "event_id": rf["event_id"], "pseudo_label": rf["pseudo_label"],
                    "w": 0.0, "layer": rf["layer"], "tie_level": rf["tie_level"], "document": rf["document"],
                    "site": inst["site"], "is_pdf": inst["is_pdf"],
                    "doc_year": inst["doc_year"] or "undated", "event_year": ev["year"] if ev else "",
                    "furniture": inst["furniture"], "duplicate": inst["dup"], "clean": inst["clean"], "n_rows": 0,
                    "in_event_title": inst["in_event_title"], "in_handle": inst["in_handle"],
                    "orgs_in_paragraph_keys": "; ".join(inst["org_keys"]),
                    "context_before": inst["context_before"], "sentence": inst["sentence"],
                    "context_after": inst["context_after"], "source_link": inst["source_link"]}
            u["_terms"].append(inst["term"])
            u["_fam"].append(inst["term_family"])
            u["w"] += rf["w"]
            u["n_rows"] += 1
            u["clean"] = u["clean"] or inst["clean"]
            u["furniture"] = u["furniture"] and inst["furniture"]
    rows = []
    for u in units.values():
        u["terms"] = "; ".join(dict.fromkeys(u.pop("_terms")))
        u["term_families"] = "; ".join(dict.fromkeys(u.pop("_fam")))
        u["w"] = round(u["w"], 4)
        rows.append(u)
    an.units = rows
    an.table("theme_units", rows, ["passage_id", "theme", "terms", "term_families", "language_section", "subtheme",
                                   "stance", "event_id", "pseudo_label", "w", "layer", "tie_level", "document", "site",
                                   "is_pdf", "doc_year", "event_year", "furniture", "duplicate", "clean", "n_rows",
                                   "in_event_title", "in_handle", "orgs_in_paragraph_keys", "context_before",
                                   "sentence", "context_after", "source_link"])


# ==========================================================================
# A2 coverage frame and missingness
# ==========================================================================
def a2_coverage(an: Analysis) -> None:
    th = an.themes
    tf = an.a.theme_filter
    with_ties = {t["event_id"] for t in an.ties if t["event_id"] and not t["in_menus"] and t["pass"][tf]}
    groups = defaultdict(list)
    for ev in an.events:
        groups[ev["stream_primary"]].append(ev)
    groups["ALL"] = list(an.events)
    measures = [("with_link", lambda e: e["has_link"]), ("link_followed", lambda e: e["link_followed"]),
                ("has_web_text", lambda e: e["has_web_text"]), ("with_org_ties", lambda e: e["event_id"] in with_ties),
                ("with_theme_any", lambda e: e["any_any"]), ("country_resolved", lambda e: bool(e["countries"])),
                ("date_day_precision", lambda e: bool(re.fullmatch(r"\d{4}-\d\d-\d\d", e["start"])))]
    rows = []
    for st in sorted(groups, key=lambda x: (x == "ALL", x)):
        evs = groups[st]
        ys = [e["year"] for e in evs if e["year"]]
        r = {"stream": st, "years": f"{min(ys)}-{max(ys)}" if ys else "", "n_events": len(evs)}
        for name, fn in measures:
            k = sum(1 for e in evs if fn(e))
            lo, hi = wilson(k, len(evs))
            r.update({name: k, f"{name}_share": share(k, len(evs)), f"{name}_lo": lo, f"{name}_hi": hi})
        rows.append(r)
    fields = ["stream", "years", "n_events"] + [f"{m}{x}" for m, _ in measures for x in ("", "_share", "_lo", "_hi")]
    an.table("coverage_by_stream", rows, fields)
    # ---- missingness
    n = len(an.events)
    clean = [i for i in an.instances if i["clean"]]
    web_insts = [i for i in an.instances if i["site_role"] != "seed"]
    seed_clean = [i for i in clean if any(rf["layer"] == "listing_seed" for rf in i["refs"])]
    names = [e["event"] for e in an.events if len(e["event"]) >= 25]
    leak = 0
    for inst in seed_clean:
        own = {nm.casefold() for nm in inst["event_names"]}
        ctx = (inst["context_before"] + " " + inst["context_after"]).casefold()
        if any(nm.casefold() in ctx for nm in names if nm.casefold() not in own):
            leak += 1
    an.facts["seed_context_leak"] = (leak, len(seed_clean))
    net = [t for t in an.ties if t["sheet"] == "event_organisations"]
    n_refs = sum(sum(c.values()) for c in an.ref_counts.values())
    rows = [
        ("events without a country", sum(1 for e in an.events if not e["countries"]), n),
        ("events without a date", sum(1 for e in an.events if e["year"] is None), n),
        ("network ties to pseudo events ('(not tied to an event)', '(other pages of ...)')",
         sum(1 for t in net if not t["event_id"]), len(net)),
        ("network event refs that match no event (phantom splits, dropped)",
         sum(c["unmatched"] for c in an.ref_counts.values()), n_refs),
        ("clean theme rows with no organisation in the paragraph", sum(1 for i in clean if not i["orgs_text"]),
         len(clean)),
        ("web theme rows without a document year", sum(1 for i in web_insts if not i["doc_year"]), len(web_insts)),
        ("low-confidence network rows", sum(1 for t in net if t["confidence"] == "low"), len(net)),
        ("events listed in both agenda PDFs", an.facts.get("events_in_two_agendas", 0), n),
        ("seed theme rows whose context names another event (caveat C6)", leak, len(seed_clean)),
        (f"network rows left out by the theme filter '{tf}'", an.filter_counts["rows"][1] - an.filter_counts["rows"][0],
         an.filter_counts["rows"][1]),
    ]
    an.table("missingness", [{"field": f, "k": k, "n": m, "pct": round(100 * k / m, 1) if m else None}
                             for f, k, m in rows], ["field", "k", "n", "pct"])
    _ = th


# ==========================================================================
# A3 measurement ladder and concentration
# ==========================================================================
def _clean_inst_docs(an: Analysis):
    """Clean instances with their document (rule 7: one agenda entry = one document)."""
    out = []
    for inst in an.instances:
        if inst["clean"]:
            out.append((inst, inst["refs"][0]["document"] if inst["refs"] else inst["source_link"]))
    return out


def a3_ladder(an: Analysis) -> None:
    th = an.themes
    cd = _clean_inst_docs(an)
    counts = {}
    for t in th:
        insts = [i for i in an.instances if i["theme"] == t]
        cl = [(i, d) for i, d in cd if i["theme"] == t]
        evs_any = {rf["event_id"] for i, _ in cl for rf in i["refs"] if rf["event_id"]}
        evs_own = {rf["event_id"] for i, _ in cl for rf in i["refs"] if rf["event_id"] and rf["layer"] in OWN_LAYERS}
        counts[t] = OrderedDict([("raw_rows", len(insts)), ("clean_rows", len(cl)),
                                 ("unique_passages", len({i["passage_id"] for i, _ in cl})),
                                 ("documents", len({d for _, d in cl})), ("sites", len({i["site"] for i, _ in cl})),
                                 ("events_any", len(evs_any)), ("events_own", len(evs_own))])
    rows = []
    for lev in ["raw_rows", "clean_rows", "unique_passages", "documents", "sites", "events_any", "events_own"]:
        tot = sum(counts[t][lev] for t in th)
        for t in th:
            rows.append({"theme": t, "level": lev, "count": counts[t][lev], "share_of_theme_total": share(counts[t][lev], tot)})
    an.table("measurement_ladder", rows, ["theme", "level", "count", "share_of_theme_total"])
    an.ladder = counts
    # ---- concentration
    rows = []
    for t in ["ALL"] + th:
        cl = [(i, d) for i, d in cd if t == "ALL" or i["theme"] == t]
        c = Counter(d for _, d in cl)
        tot = sum(c.values())
        ev_w = Counter()
        for i, _ in cl:
            for rf in i["refs"]:
                if rf["event_id"]:
                    ev_w[rf["event_id"]] += rf["w"]
        if not tot:
            rows.append({"theme": t, "documents": 0})
            continue
        top = sorted(c.items(), key=lambda x: (-x[1], x[0]))
        te = sorted(ev_w.items(), key=lambda x: (-x[1], x[0]))
        rows.append({"theme": t, "documents": len(c), "clean_rows": tot, "top1_doc": top[0][0],
                     "top1_share": share(top[0][1], tot), "top5_share": share(sum(v for _, v in top[:5]), tot),
                     "eff_docs": round(1 / sum((v / tot) ** 2 for v in c.values()), 2),
                     "top_event": an.ev_by_id[te[0][0]]["event"] if te else "",
                     "top_event_share": round(te[0][1] / sum(ev_w.values()), 4) if te else None})
    an.table("concentration", rows, ["theme", "documents", "clean_rows", "top1_doc", "top1_share", "top5_share",
                                     "eff_docs", "top_event", "top_event_share"])
    an.concentration = {r["theme"]: r for r in rows}
    # ---- attribution: layer x theme
    rows = []
    agg = defaultdict(lambda: [0, set()])
    for inst in an.instances:
        if not inst["clean"]:
            continue
        for rf in inst["refs"]:
            a = agg[(rf["layer"], inst["theme"])]
            a[0] += 1
            a[1].add(rf["document"])
    for (lay, t), (k, docs) in sorted(agg.items()):
        rows.append({"layer": lay, "tie_level": TIE_LEVEL.get(lay, "L3"), "theme": t, "clean_refs": k,
                     "documents": len(docs)})
    an.table("attribution", rows, ["layer", "tie_level", "theme", "clean_refs", "documents"])
    # ---- term families
    fam = defaultdict(lambda: {"rows": 0, "clean": 0, "passages": set(), "docs": set(), "events": set(),
                               "forms": Counter(), "title": 0, "handle": 0, "years": []})
    for inst in an.instances:
        f = fam[(inst["theme"], inst["term_family"], inst["language_section"])]
        f["rows"] += 1
        f["forms"][inst["term"].casefold()] += 1
        f["title"] += inst["in_event_title"]
        f["handle"] += inst["in_handle"]
        if inst["clean"]:
            f["clean"] += 1
            f["passages"].add(inst["passage_id"])
            f["docs"].add(inst["refs"][0]["document"] if inst["refs"] else inst["source_link"])
            for rf in inst["refs"]:
                if rf["event_id"]:
                    f["events"].add(rf["event_id"])
                    y = an.ev_by_id[rf["event_id"]]["year"]
                    if y:
                        f["years"].append(y)
    rows = []
    for (t, tfam, sec), f in sorted(fam.items(), key=lambda x: (x[0][0], -x[1]["rows"], x[0][1])):
        rows.append({"theme": t, "term_family": tfam, "language_section": sec, "rows": f["rows"],
                     "clean_rows": f["clean"], "passages": len(f["passages"]), "documents": len(f["docs"]),
                     "events": len(f["events"]),
                     "surface_forms": "; ".join(f"{k} ({v})" for k, v in f["forms"].most_common(8)),
                     "in_title_rows": f["title"], "handle_rows": f["handle"],
                     "first_year": min(f["years"]) if f["years"] else "", "last_year": max(f["years"]) if f["years"] else ""})
    an.table("term_families", rows, ["theme", "term_family", "language_section", "rows", "clean_rows", "passages",
                                     "documents", "events", "surface_forms", "in_title_rows", "handle_rows",
                                     "first_year", "last_year"])
    an.facts["unmapped_terms"] = sum(1 for i in an.instances if i["term_family"] == "(unmapped)")


# ==========================================================================
# A4 theme prevalence (event as unit)
# ==========================================================================
MEASURES = ["own", "landing", "linked", "any", "in_title"]


def _prev_rows(an: Analysis, dimension: str, groups: Dict[tuple, List[dict]], measures=MEASURES) -> List[dict]:
    rows = []
    for (grp, st) in sorted(groups, key=lambda x: (str(x[0]), str(x[1]))):
        evs = groups[(grp, st)]
        web = [e for e in evs if e["has_web_text"]]
        for t in an.themes + ["any"]:
            for m in measures:
                base = evs if m in ("own", "in_title") else web
                k = sum(1 for e in base if e[f"{t}_{m}"])
                lo, hi = wilson(k, len(base))
                rows.append({"dimension": dimension, "group": grp, "stream": st, "theme": t, "measure": m,
                             "n_events": len(base), "k_events": k, "share": share(k, len(base)),
                             "wilson_lo": lo, "wilson_hi": hi})
    return rows


PREV_FIELDS = ["dimension", "group", "stream", "theme", "measure", "n_events", "k_events", "share", "wilson_lo",
               "wilson_hi"]


def a4_prevalence(an: Analysis) -> None:
    g = defaultdict(list)
    gy = defaultdict(list)
    for ev in an.events:
        for st in (ev["stream_primary"], "all"):
            g[(ev["period"], st)].append(ev)
            gy[(ev["year"] or "undated", st)].append(ev)
    # Sensitivity: an event listed in several agendas counts in each of them.
    if any(len(ev["streams"]) > 1 for ev in an.events):
        for ev in an.events:
            for st in ev["streams"]:
                g[(ev["period"], f"{st} (all listings)")].append(ev)
    an.table("prev_stream_period", _prev_rows(an, "period", g), PREV_FIELDS)
    an.prev_sp = an.tables["prev_stream_period"][0]
    an.table("prev_stream_year", _prev_rows(an, "year", gy), PREV_FIELDS)
    # ---- country: every country of an event; groups below 15 events pooled as "other"
    cnt = Counter(c for ev in an.events for c in (ev["countries"] or ["(unresolved)"]))
    gc = defaultdict(list)
    for ev in an.events:
        for c in ev["countries"] or ["(unresolved)"]:
            lab = c if (cnt[c] >= 15 or c == "(unresolved)") else "other (n<15 each)"
            gc[(lab, "all")].append(ev)
    rows = _prev_rows(an, "country", gc, ["own", "any"])
    for r in rows:
        r["group_name"] = an.p.country_name(r["group"]) if re.fullmatch(r"[A-Z]{2,6}", r["group"]) else r["group"]
        r["headline_ok"] = r["n_events"] >= 30
    an.table("prev_country", rows, PREV_FIELDS[:2] + ["group_name"] + PREV_FIELDS[2:] + ["headline_ok"])
    gl = defaultdict(list)
    for ev in an.events:
        gl[(ev["entry_lang"] or "(no entry text)", ev["stream_primary"])].append(ev)
    an.table("prev_lang", _prev_rows(an, "entry_lang", gl, ["own", "in_title"]), PREV_FIELDS)
    # ---- entry-corpus rates (agenda entries of the seed PDFs)
    if an.entries and an.ts:
        p = an.p
        agg = defaultdict(lambda: {"entries": 0, "words": 0, "hits": Counter(), "with": Counter()})
        total_hits = 0
        for en in an.entries:
            text = " ".join(en["lines"])
            words = len(_WORD.findall(p.URL_LIKE.sub(" ", text)))
            hits = an.ts.find(text)
            y = year_of(en["start"] or en["month"])
            for key in ((en["stream"], period_of(y)), (en["stream"], "all"), ("all", "all")):
                a = agg[key]
                a["entries"] += 1
                a["words"] += words
                for t in an.themes:
                    k = len(hits.get(t, []))
                    a["hits"][t] += k
                    a["with"][t] += k > 0
                a["hits"]["any"] += sum(len(v) for v in hits.values())
                a["with"]["any"] += bool(hits)
            total_hits += sum(len(v) for v in hits.values())
        rows = []
        for (st, per), a in sorted(agg.items()):
            for t in an.themes + ["any"]:
                k = a["hits"][t]
                lo, hi = byar(k)
                w = a["words"]
                ke = a["with"][t]
                wl, wh = wilson(ke, a["entries"])
                rows.append({"stream": st, "period": per, "theme": t, "entries": a["entries"], "words": w, "hits": k,
                             "per10k": round(1e4 * k / w, 3) if w else None,
                             "byar_lo": round(1e4 * lo / w, 3) if w else None,
                             "byar_hi": round(1e4 * hi / w, 3) if w else None,
                             "entries_with_theme": ke, "share": share(ke, a["entries"]), "wilson_lo": wl,
                             "wilson_hi": wh})
        an.table("entry_rates", rows, ["stream", "period", "theme", "entries", "words", "hits", "per10k", "byar_lo",
                                       "byar_hi", "entries_with_theme", "share", "wilson_lo", "wilson_hi"])
        seed_rows = sum(1 for i in an.instances if i["site_role"] == "seed")
        an.facts["entry_hits"] = (total_hits, seed_rows)
        an.note("entry re-run theme hits vs seed theme rows in master_themes", f"{total_hits} vs {seed_rows}",
                "equal" if total_hits == seed_rows else "differ (line joins, menu filter or a newer theme file)")
        if total_hits != seed_rows:
            an.warn("Agenda entries re-read give %d theme hits, master_themes has %d seed rows "
                    "(differences come from line joins or a changed theme file)", total_hits, seed_rows)
    elif "entry_rates" not in an.skipped:
        an.skipped["entry_rates"] = "no agenda entries or no theme file"
    # ---- per-page rates (pages.csv / sources.csv)
    src = read_csv(an.inputs["sources.csv"])[1] if "sources.csv" in an.inputs else an.pages
    if src:
        agg = defaultdict(lambda: {"n": 0, "words": 0, "hits": Counter(), "with": Counter()})
        for pg in src:
            role = pg.get("site_role", "") or "?"
            kind = pg.get("kind", "") or ("pdf" if truthy(pg.get("is_pdf")) else "page")
            w = int(pg["words"]) if s(pg.get("words")).isdigit() else 0
            for key in ((role, kind), ("all", "all")):
                a = agg[key]
                a["n"] += 1
                a["words"] += w
                anyh = 0
                for t in an.themes:
                    v = s(pg.get(f"theme:{t}"))
                    k = int(v) if v.isdigit() else 0
                    a["hits"][t] += k
                    a["with"][t] += k > 0
                    anyh += k
                a["hits"]["any"] += anyh
                a["with"]["any"] += anyh > 0
        rows = []
        for (role, kind), a in sorted(agg.items()):
            for t in an.themes + ["any"]:
                k = a["hits"][t]
                lo, hi = byar(k)
                wl, wh = wilson(a["with"][t], a["n"])
                rows.append({"site_role": role, "kind": kind, "theme": t, "sources": a["n"], "words": a["words"],
                             "hits": k, "per10k": round(1e4 * k / a["words"], 3) if a["words"] else None,
                             "byar_lo": round(1e4 * lo / a["words"], 3) if a["words"] else None,
                             "byar_hi": round(1e4 * hi / a["words"], 3) if a["words"] else None,
                             "sources_with_theme": a["with"][t], "share": share(a["with"][t], a["n"]),
                             "wilson_lo": wl, "wilson_hi": wh})
        an.table("page_rates", rows, ["site_role", "kind", "theme", "sources", "words", "hits", "per10k", "byar_lo",
                                      "byar_hi", "sources_with_theme", "share", "wilson_lo", "wilson_hi"])
    else:
        an.skipped["page_rates"] = "pages.csv / sources.csv not found"


# ==========================================================================
# A5 / A6 bipartite network, projection, backbone, centralities
# ==========================================================================
def _org_conf(an: Analysis) -> Dict[str, int]:
    if not hasattr(an, "_conf"):
        c: Dict[str, int] = {}
        for t in an.ties:
            c[t["org_key"]] = max(c.get(t["org_key"], 0), CONF_RANK.get(t["confidence"], 0))
        an._conf = c
    return an._conf


def scenario_ties(an: Analysis, sc: str, layers: str = "L1-L3", tf: Optional[str] = None) -> List[dict]:
    tf = tf or an.a.theme_filter
    min_rank = CONF_RANK[an.a.min_confidence]
    conf = _org_conf(an)
    keep_levels = LAYER_SETS[layers]
    out = []
    for t in an.ties:
        if not t["event_id"] or t["in_menus"] or not t["pass"][tf] or t["tie_level"] not in keep_levels:
            continue
        k = t["org_key"]
        if org_dropped(an, k):
            continue
        title = t["named_in_event_title"]
        conf_ok = conf.get(k, 0) >= min_rank or s(an.codes.get(k, {}).get("include")) in ("1", "yes")
        if sc in ("B", "D") and title:
            continue
        if sc in ("C", "D", "C_curated") and not conf_ok:
            continue
        if sc == "C_curated" and org_class(an, k) in DROP_CLASSES:
            continue
        out.append(t)
    return out


def bipartite(an: Analysis, ties: List[dict], level: str = "series"):
    import networkx as nx
    G = nx.Graph()
    agg: "OrderedDict[tuple, dict]" = OrderedDict()
    ev_info: Dict[str, dict] = {}
    for t in ties:
        node = t["series_id"] if level == "series" else t["event_id"]
        e, o = "E:" + node, "O:" + t["org_key"]
        ev = an.ev_by_id[t["event_id"]]
        ei = ev_info.setdefault(e, {"label": ev["event"], "editions": set(), "years": set(), "countries": set(),
                                    "streams": set()})
        ei["editions"].add(t["event_id"])
        if ev["year"]:
            ei["years"].add(ev["year"])
        ei["countries"].update(ev["countries"])
        ei["streams"].update(ev["streams"])
        a = agg.setdefault((e, o), {"editions": set(), "sources": set(), "docs": set(), "levels": set(),
                                    "title": False, "conf": 0, "years": set()})
        a["editions"].add(t["event_id"])
        a["sources"].add(t["unit"])
        a["docs"].add(t["document"])
        a["levels"].add(t["tie_level"])
        a["title"] = a["title"] or t["named_in_event_title"]
        a["conf"] = max(a["conf"], CONF_RANK.get(t["confidence"], 0))
        if ev["year"]:
            a["years"].add(ev["year"])
    inv = {v: k for k, v in CONF_RANK.items()}
    for e, ei in ev_info.items():
        G.add_node(e, kind="event", bipartite=0, label=short(ei["label"], 80), n_editions=len(ei["editions"]),
                   first_year=min(ei["years"]) if ei["years"] else "", last_year=max(ei["years"]) if ei["years"] else "",
                   countries="; ".join(sorted(ei["countries"])), streams="; ".join(sorted(ei["streams"])))
    for (e, o), a in agg.items():
        if o not in G:
            k = o[2:]
            G.add_node(o, kind="organisation", bipartite=1, label=an.org_name.get(k, k),
                       actor_class=org_class(an, k))
        G.add_edge(e, o, n_editions=len(a["editions"]), n_sources=len(a["sources"]), n_docs=len(a["docs"]),
                   tie_levels="; ".join(sorted(a["levels"])), min_tie_level=min(a["levels"]),
                   title_named=a["title"], confidence=inv[a["conf"]],
                   first_year=min(a["years"]) if a["years"] else "", last_year=max(a["years"]) if a["years"] else "")
    return G


def project(G):
    """Newman-weighted organisation projection: an event with k >= 2 organisations
    adds 1/(k-1) to every pair of them ("shared" counts the events)."""
    import networkx as nx
    acc: Dict[tuple, list] = {}
    for e in sorted(n for n in G if n.startswith("E:")):
        nb = sorted(G[e])
        k = len(nb)
        if k < 2:
            continue
        w = 1 / (k - 1)
        for i in range(k):
            a = nb[i]
            for j in range(i + 1, k):
                x = acc.get((a, nb[j]))
                if x is None:
                    acc[(a, nb[j])] = [w, 1]
                else:
                    x[0] += w
                    x[1] += 1
    P = nx.Graph()
    P.add_nodes_from((n, G.nodes[n]) for n in sorted(n for n in G if n.startswith("O:")))
    P.add_edges_from((a, b, {"weight": round(w, 6), "shared": c}) for (a, b), (w, c) in acc.items())
    return P


def disparity(P, alpha: float):
    """Disparity filter (Serrano et al. 2009): keep edge ij if (1 - w_ij/s_i)^(k_i - 1) < alpha
    from either endpoint with degree >= 2."""
    import networkx as nx
    keep = set()
    for n in P:
        k = P.degree(n)
        if k < 2:
            continue
        st = P.degree(n, weight="weight")
        for m, d in P[n].items():
            if (1 - d["weight"] / st) ** (k - 1) < alpha:
                keep.add((n, m) if n < m else (m, n))
    B = nx.Graph()
    for a, b in sorted(keep):
        B.add_node(a, **P.nodes[a])
        B.add_node(b, **P.nodes[b])
        B.add_edge(a, b, **P[a][b])
    return B


def graph_stats(G, alpha: float) -> dict:
    import networkx as nx
    orgs = [n for n in G if n.startswith("O:")]
    evs = [n for n in G if n.startswith("E:")]
    comps = sorted((len(c) for c in nx.connected_components(G)), reverse=True) if len(G) else []
    P = project(G)
    top_share = None
    if evs and P.number_of_edges():
        big = max(sorted(evs), key=G.degree)
        S = set(G[big])
        inside = sum(1 for a, b in P.edges if a in S and b in S)
        top_share = round(inside / P.number_of_edges(), 4)
    B = disparity(P, alpha)
    B2 = disparity(P, 0.2)
    return {"orgs": len(orgs), "nodes_events": len(evs), "orgs_in_2plus": sum(1 for o in orgs if G.degree(o) >= 2),
            "edges": G.number_of_edges(), "components": len(comps), "giant_nodes": comps[0] if comps else 0,
            "proj_edges": P.number_of_edges(), "top_event_share_of_proj_edges": top_share,
            "backbone_nodes": B.number_of_nodes(), "backbone_edges": B.number_of_edges(),
            "backbone02_nodes": B2.number_of_nodes(), "backbone02_edges": B2.number_of_edges(), "_P": P, "_B": B}


def a5_network(an: Analysis) -> None:
    tf = an.a.theme_filter
    scs = ["A", "B", "C", "D"] + (["C_curated"] if an.curated else [])
    an.default_sc = "C_curated" if an.curated else "C"
    rows = []
    for f in dict.fromkeys([tf, "none"]):
        for sc in scs:
            for layers in LAYER_SETS:
                ties = scenario_ties(an, sc, layers, f)
                for level in ("edition", "series"):
                    G = bipartite(an, ties, level)
                    st = graph_stats(G, an.a.backbone_alpha)
                    rows.append({"theme_filter": f, "scenario": sc, "description": SCENARIOS[sc], "layers": layers,
                                 "node_level": level, "rows": len(ties),
                                 "events": len({t["event_id"] for t in ties}),
                                 "series": len({t["series_id"] for t in ties}), "orgs": st["orgs"],
                                 "orgs_in_2plus_" + ("events" if level == "edition" else "series"): st["orgs_in_2plus"],
                                 "edges": st["edges"], "components": st["components"], "giant_nodes": st["giant_nodes"],
                                 "proj_edges": st["proj_edges"],
                                 "top_event_share_of_proj_edges": st["top_event_share_of_proj_edges"],
                                 "backbone_nodes": st["backbone_nodes"], "backbone_edges": st["backbone_edges"],
                                 "backbone_a02_nodes": st["backbone02_nodes"], "backbone_a02_edges": st["backbone02_edges"]})
    for r in rows:
        r["orgs_in_2plus"] = r.pop("orgs_in_2plus_events", None) if r["node_level"] == "edition" else r.pop(
            "orgs_in_2plus_series", None)
    an.table("sensitivity", rows, ["theme_filter", "scenario", "description", "layers", "node_level", "rows", "events",
                                   "series", "orgs", "orgs_in_2plus", "edges", "components", "giant_nodes",
                                   "proj_edges", "top_event_share_of_proj_edges", "backbone_nodes", "backbone_edges",
                                   "backbone_a02_nodes", "backbone_a02_edges"])
    an.sensitivity = rows
    # ---- default scenario: series level, all layers
    ties = scenario_ties(an, an.default_sc)
    an.default_ties = ties
    G = bipartite(an, ties, "series")
    an.G = G
    P = project(G)
    B = disparity(P, an.a.backbone_alpha)
    an.P, an.B = P, B
    an.facts["default_graph"] = {"scenario": an.default_sc, "theme_filter": tf, "series": sum(
        1 for n in G if n.startswith("E:")), "orgs": sum(1 for n in G if n.startswith("O:")),
        "edges": G.number_of_edges(), "proj_edges": P.number_of_edges(), "backbone_nodes": B.number_of_nodes(),
        "backbone_edges": B.number_of_edges()}
    a6_centrality(an, ties, G, P, B)
    an.exports = {an.default_sc: (G, B)}
    if an.default_sc != "A":
        GA = bipartite(an, scenario_ties(an, "A"), "series")
        an.exports["A"] = (GA, disparity(project(GA), an.a.backbone_alpha))


def _betweenness(an: Analysis, G) -> Tuple[Dict[str, float], str]:
    import networkx as nx
    kopt = s(an.a.betweenness_k)
    n = G.number_of_nodes()
    if not n:
        return {}, "empty"
    if kopt.isdigit() and int(kopt) < n:
        k = int(kopt)
    elif kopt in ("", "auto") and n > 5000:
        k = 500
    else:
        k = None
    t0 = time.time()
    if k is None:
        bc = nx.betweenness_centrality(G, normalized=True)
        how = "exact"
    else:
        bc = nx.betweenness_centrality(G, k=k, normalized=True, seed=an.a.seed)
        how = f"sampled k={k}, seed={an.a.seed}"
    an.note("bipartite betweenness", how, f"{G.number_of_nodes()} nodes, {time.time() - t0:.1f} s")
    return bc, how


def a6_centrality(an: Analysis, ties: List[dict], G, P, B) -> None:
    bc, how = _betweenness(an, G)
    an.betweenness_method = how
    orgs = sorted(n for n in G if n.startswith("O:"))
    # (2,2)-core: organisations in >= 2 series and series with >= 2 core organisations
    core = set(G.nodes)
    changed = True
    while changed:
        changed = False
        for n in list(core):
            if sum(1 for m in G[n] if m in core) < 2:
                core.discard(n)
                changed = True
    by_org = defaultdict(list)
    for t in ties:
        by_org[t["org_key"]].append(t)
    rows = []
    for o in orgs:
        k = o[2:]
        ts = by_org[k]
        series = sorted(G[o])
        members = {e: set(G[e]) - {o} for e in series}
        neigh = set().union(*members.values()) if members else set()
        hd = None
        if neigh:
            big = max(series, key=lambda e: (len(members[e]), e))
            others = set().union(*(members[e] for e in series if e != big)) if len(series) > 1 else set()
            hd = round(len(members[big] - others) / len(neigh), 4)
        evs = {t["event_id"] for t in ts}
        cc = Counter(c for e in evs for c in an.ev_by_id[e]["countries"])
        tot = sum(cc.values())
        years = {an.ev_by_id[e]["year"] for e in evs if an.ev_by_id[e]["year"]}
        lv = {lev: len({t["series_id"] for t in ts if t["tie_level"] == lev}) for lev in ("L1", "L2", "L3")}
        rows.append({"org_key": k, "organisation": an.org_name.get(k, k), "actor_class": org_class(an, k),
                     "curated": yn(an.curated), "n_events": len(evs), "n_series": G.degree(o), "n_years": len(years),
                     "n_countries": len(cc), "countries": "; ".join(c for c, _ in cc.most_common()),
                     "n_sites": len({t["site"] for t in ts}), "n_L1": lv["L1"], "n_L2": lv["L2"], "n_L3": lv["L3"],
                     "bip_degree": G.degree(o), "bip_betweenness": round(bc.get(o, 0.0), 6),
                     "backbone_degree": B.degree(o) if o in B else 0,
                     "backbone_strength": round(B.degree(o, weight="weight"), 4) if o in B else 0.0,
                     "in_backbone": o in B, "projection_degree": P.degree(o) if o in P else 0,
                     "hub_dependence": hd, "hub_dependent": hd is not None and hd >= 0.8,
                     "core_2_2": o in core,
                     "participation_coef_country": round(1 - sum((v / tot) ** 2 for v in cc.values()), 4) if tot else None})
    rows.sort(key=lambda r: (-r["n_series"], -r["bip_betweenness"], r["org_key"]))
    an.org_centrality = rows
    an.table("org_centrality", rows, ["org_key", "organisation", "actor_class", "curated", "n_events", "n_series",
                                      "n_years", "n_countries", "countries", "n_sites", "n_L1", "n_L2", "n_L3",
                                      "bip_degree", "bip_betweenness", "backbone_degree", "backbone_strength",
                                      "in_backbone", "projection_degree", "hub_dependence", "hub_dependent",
                                      "core_2_2", "participation_coef_country"])


# ==========================================================================
# A7 theme x organisation association and bridging
# ==========================================================================
def a7_org_theme(an: Analysis) -> None:
    th = an.themes
    # passage level (primary): organisations named in the paragraph of a clean theme passage
    pas = defaultdict(lambda: {"passages": set(), "docs": set(), "series": set(), "example": ""})
    for inst in an.instances:
        if not inst["clean"]:
            continue
        doc = inst["refs"][0]["document"] if inst["refs"] else inst["source_link"]
        series = {an.series_of[rf["event_id"]] for rf in inst["refs"] if rf["event_id"]}
        for k in inst["org_keys"]:
            if org_dropped(an, k):
                continue
            a = pas[(k, inst["theme"])]
            a["passages"].add(inst["passage_id"])
            a["docs"].add(doc)
            a["series"].update(series)
            a["example"] = a["example"] or inst["passage_id"]
    # event level (secondary): series of the default scenario whose L1/L2 text carries the theme.
    # Measured on all sources (theme filter "none") for the organisations the filter keeps,
    # so the inclusion criterion does not inflate the shares it is compared with.
    included = {t["org_key"] for t in an.default_ties}
    base = [t for t in scenario_ties(an, an.default_sc, "L1-L3", "none") if t["org_key"] in included]
    series_theme = defaultdict(set)
    for ev in an.events:
        for t in th:
            if ev[f"{t}_own"] or ev[f"{t}_landing"]:
                series_theme[ev["series_id"]].add(t)
    org_series = defaultdict(set)
    org_title_series = defaultdict(set)
    for t in base:
        org_series[t["org_key"]].add(t["series_id"])
        if t["named_in_event_title"]:
            org_title_series[t["org_key"]].add(t["series_id"])
    all_series = set().union(*org_series.values()) if org_series else set()
    p0 = {t: share(sum(1 for x in all_series if t in series_theme.get(x, ())), len(all_series)) or 0.0 for t in th}
    rows = []
    keys = sorted(set(k for k, _ in pas) | set(org_series))
    for k in keys:
        for t in th:
            a = pas.get((k, t))
            ser = org_series.get(k, set())
            n = len(ser)
            kk = sum(1 for x in ser if t in series_theme.get(x, ()))
            ev_ok = n >= 3 and kk >= 2
            if not a and not ev_ok:
                continue
            r = {"org_key": k, "organisation": an.org_name.get(k, k), "actor_class": org_class(an, k), "theme": t,
                 "passages": len(a["passages"]) if a else 0, "documents": len(a["docs"]) if a else 0,
                 "series_passage": len(a["series"]) if a else 0, "n_series": n, "k_series": kk,
                 "k_title": sum(1 for x in org_title_series.get(k, ()) if t in series_theme.get(x, ())),
                 "example_passage_id": a["example"] if a else ""}
            if ev_ok:
                lo, _ = wilson(kk, n)
                r.update({"share": share(kk, n), "wilson_lo": lo,
                          "lift": round((kk / n) / p0[t], 3) if p0[t] else None,
                          "shrunk": round((kk + 5 * p0[t]) / (n + 5), 4)})
            r["robust"] = (r["documents"] >= 2) or (ev_ok and kk >= 2)
            rows.append(r)
    an.table("org_theme", rows, ["org_key", "organisation", "actor_class", "theme", "passages", "documents",
                                 "series_passage", "n_series", "k_series", "k_title", "share", "wilson_lo", "lift",
                                 "shrunk", "robust", "example_passage_id"])
    an.org_theme = rows
    an.facts["org_theme"] = {"orgs_in_passages": len({k for k, _ in pas}),
                             "robust_pairs_passage": sum(1 for (k, t), a in pas.items() if len(a["docs"]) >= 2),
                             "p0": p0}
    cen = {r["org_key"]: r for r in getattr(an, "org_centrality", [])}
    robust = defaultdict(list)
    for r in rows:
        if r["robust"]:
            robust[r["org_key"]].append(r["theme"])
    br = []
    for k, ts_ in sorted(robust.items(), key=lambda x: (-len(x[1]), x[0])):
        c = cen.get(k, {})
        br.append({"org_key": k, "organisation": an.org_name.get(k, k), "actor_class": org_class(an, k),
                   "n_themes_robust": len(ts_), "themes": "; ".join(ts_), "bridging": len(ts_) >= 2,
                   "bip_betweenness": c.get("bip_betweenness"),
                   "participation_coef_country": c.get("participation_coef_country")})
    an.table("org_bridging", br, ["org_key", "organisation", "actor_class", "n_themes_robust", "themes", "bridging",
                                  "bip_betweenness", "participation_coef_country"])
    an.facts["bridging_orgs"] = [r["organisation"] for r in br if r["bridging"]]
    # ---- theme co-occurrence at three levels
    sets = {"passage": defaultdict(set), "document": defaultdict(set), "series": defaultdict(set)}
    site_of = {"passage": {}, "document": {}, "series": defaultdict(set)}
    for inst in an.instances:
        if not inst["clean"]:
            continue
        t = inst["theme"]
        doc = inst["refs"][0]["document"] if inst["refs"] else inst["source_link"]
        sets["passage"][t].add(inst["passage_id"])
        site_of["passage"][inst["passage_id"]] = inst["site"]
        sets["document"][t].add(doc)
        site_of["document"][doc] = inst["site"]
        for rf in inst["refs"]:
            if rf["event_id"]:
                x = an.series_of[rf["event_id"]]
                sets["series"][t].add(x)
                site_of["series"][x].add(inst["site"])
    n_series_all = len({e["series_id"] for e in an.events})
    rows = []
    for lev, d in sets.items():
        for i, a in enumerate(th):
            for b in th[i + 1:]:
                A, Bs = d.get(a, set()), d.get(b, set())
                ab = A & Bs
                uni = A | Bs
                sites = sorted({x for y in uni for x in ((site_of[lev][y] if lev == "series" else {site_of[lev][y]}))})
                loo = []
                for st in sites:
                    def ok(y):
                        v = site_of[lev][y]
                        return st not in (v if lev == "series" else {v})
                    loo.append(sum(1 for y in ab if ok(y)))
                r = {"level": lev, "theme_a": a, "theme_b": b, "n_a": len(A), "n_b": len(Bs), "n_ab": len(ab),
                     "jaccard": share(len(ab), len(uni)), "p_b_given_a": share(len(ab), len(A)),
                     "lift": round(len(ab) * n_series_all / (len(A) * len(Bs)), 3)
                     if lev == "series" and A and Bs else None,
                     "loo_site_min_n_ab": min(loo) if loo else None, "loo_site_max_n_ab": max(loo) if loo else None}
                rows.append(r)
    an.table("theme_cooccurrence", rows, ["level", "theme_a", "theme_b", "n_a", "n_b", "n_ab", "jaccard",
                                          "p_b_given_a", "lift", "loo_site_min_n_ab", "loo_site_max_n_ab"])


# ==========================================================================
# A1 organisation authority list, merge candidates, precision sample
# ==========================================================================
_COMPANY = re.compile(r"\b(?:AG|SE|PLC|GmbH|Ltd|B\.?V\.?|S\.?A\.?|N\.?V\.?|Inc|Corp|Bank|Group)\.?$", re.I)
FOSSIL_ARMS = {"shell", "rwe", "uniper", "eph", "totalenergies", "total", "equinor", "vattenfall", "leag", "bp", "eni",
               "rheinmetall", "leonardo", "thales", "exxon", "exxonmobil", "chevron", "engie", "repsol", "omv",
               "wintershall", "bayer", "tesla", "glencore", "heidelberg materials", "lafarge", "holcim"}
STATE = re.compile(r"^(?:nato|eu|european union|european commission|commission|un|united nations|unfccc|cop\d*|"
                   r"bundesregierung|government|gouvernement|regierung)$|minist|bundesamt|police|polizei|"
                   r"gendarmerie|tribunal|court|gericht|parliament|parlement|bundestag", re.I)
SITE_UI = re.compile(r"^(?:faq|newsletter|archive|archives|archiv|calendar|kalender|agenda|outlook|login|log in|"
                     r"impressum|imprint|datenschutz|privacy|cookie|cookies|contact|kontakt|menu|home|search|"
                     r"donate|spenden|shop|blog|news|press|presse|about|about us|team|events?)$", re.I)


def _route(found_by: Set[str]) -> str:
    return "ner_only" if found_by and found_by <= {"ner"} else ("rule" if found_by else "unknown")


def a1_authority(an: Analysis, cap_rows: Optional[int] = None) -> None:
    p = an.p
    tf = an.a.theme_filter
    ties = [t for t in an.ties if t["pass"][tf]]
    by = defaultdict(list)
    for t in ties:
        by[t["org_key"]].append(t)
    para = defaultdict(lambda: [set(), set()])
    for inst in an.instances:
        if inst["clean"]:
            for k in inst["org_keys"]:
                para[k][0].add(inst["passage_id"])
                para[k][1].add(inst["theme"])
    inv = {v: k for k, v in CONF_RANK.items()}
    rows = []
    for k in sorted(by):
        ts = by[k]
        real = [t for t in ts if t["event_id"]]
        evs = {t["event_id"] for t in real}
        name = an.org_name.get(k, k)
        fb = set()
        for t in ts:
            fb.update(x for x in re.split(r"[;+,\s]+", t["found_by"].lower()) if x)
        years = sorted({an.ev_by_id[e]["year"] for e in evs if an.ev_by_id[e]["year"]})
        cc = Counter(c for e in evs for c in an.ev_by_id[e]["countries"])
        lv = Counter(t["tie_level"] for t in real)
        ex = sorted(ts, key=lambda t: (t["in_menus"], -len(t["example"] or "")))
        exs = list(dict.fromkeys(collapse(t["example"]) for t in ex if t["example"]))[:2]
        surf = an.org_surfaces.get(k, Counter())
        flags = []
        if " " not in k and fb <= {"ner"}:
            flags.append("single_word_ner_only")
        if re.fullmatch(r"[A-Z0-9&.-]{2,6}", name):
            flags.append("unexpanded_acronym")
        sug = ""
        if SITE_UI.match(k):
            sug = "site_ui?"
        elif k in p.places or _is_place_name(p, name):
            sug = "place?"
        elif _COMPANY.search(name) or k in FOSSIL_ARMS:
            sug = "company?"
        elif STATE.search(k):
            sug = "state?"
        elif evs and all(k in p.key(an.ev_by_id[e]["event"]) for e in evs):
            sug = "event_name?"
        c = an.codes.get(k, {})
        rows.append({"org_key": k, "organisation": name,
                     "variants": "; ".join(v for v, _ in surf.most_common() if v != name)[:500],
                     "max_confidence": inv[max(CONF_RANK.get(t["confidence"], 0) for t in ts)],
                     "found_by": "; ".join(sorted(fb)), "route": _route(fb),
                     "n_events": len(evs), "n_series": len({an.series_of[e] for e in evs}),
                     "n_years": len(years), "years": "; ".join(map(str, years)),
                     "countries": "; ".join(x for x, _ in cc.most_common()),
                     "n_sources": len({t["unit"] for t in ts}), "n_sites": len({t["site"] for t in ts}),
                     "sites": "; ".join(x for x, _ in Counter(t["site"] for t in ts).most_common(5)),
                     "tie_levels": "; ".join(f"{x}:{lv[x]}" for x in ("L1", "L2", "L3") if lv[x]),
                     "only_in_event_title": bool(real) and all(t["named_in_event_title"] for t in real),
                     "only_menus_footers": all(t["in_menus"] for t in ts),
                     "theme_passages": len(para[k][0]), "themes_in_paragraph": "; ".join(sorted(para[k][1])),
                     "example_context_1": exs[0] if exs else "", "example_context_2": exs[1] if len(exs) > 1 else "",
                     "suggested_class": sug, "flags": "; ".join(flags), "variant_group": "",
                     "merge_into_suggested": "",
                     **{f: c.get(f, "") for f in ("include", "merge_into_key", "canonical_name", "actor_class",
                                                  "country", "is_organisation", "is_partial_name", "coder", "notes")}})
    cands = merge_candidates(an, rows)
    best: Dict[str, Tuple[float, str]] = {}
    for c in cands:
        for a_, b_ in ((c["key_a"], c["key_b"]), (c["key_b"], c["key_a"])):
            if c["score"] > best.get(a_, (0, ""))[0]:
                best[a_] = (c["score"], b_)
    n_of = {r["org_key"]: r["n_events"] for r in rows}
    for r in rows:
        if r["org_key"] in best:
            other = best[r["org_key"]][1]
            r["variant_group"] = min(r["org_key"], other)
            # suggest merging the less frequent key into the more frequent one
            if (n_of.get(other, 0), other) > (r["n_events"], r["org_key"]):
                r["merge_into_suggested"] = other
    rows.sort(key=lambda r: (-r["n_series"], -r["n_events"], r["org_key"]))
    an.authority = rows
    an.merge_cands = cands
    an.precision_sample = precision_sample(an, rows)
    an.precision_rows = precision_readback(an, rows)


def _is_place_name(p: Project, name: str) -> bool:
    if p.gazetteer is None or len(name) < 3:
        return False
    try:
        res = p.country(name)
    except Exception:  # noqa: BLE001
        return False
    m = res.get("matched", "")
    m = m[0] if isinstance(m, (list, tuple)) and m else m
    return res.get("method") in ("name", "city") and collapse(s(m)).casefold() == collapse(name).casefold()


_ART = re.compile(r"^(?:the|la|le|les|l'|d'|de|het|die|der|das|el|il|lo)\s+")


def merge_candidates(an: Analysis, rows: List[dict]) -> List[dict]:
    """Pairs of organisation keys that may be the same organisation. Compared only
    inside blocks (same first word, or a shared 3-letter piece of it), so the
    search stays fast for thousands of names."""
    keys = [r["org_key"] for r in rows]
    name = {r["org_key"]: r["organisation"] for r in rows}
    blocks = defaultdict(set)
    for k in keys:
        first = (_ART.sub("", k).split() or [""])[0]
        blocks["w:" + first].add(k)
        for i in range(max(1, len(first) - 2)):
            blocks["g:" + first[i:i + 3]].add(k)
    pairs = set()
    for b, ks in blocks.items():
        if len(ks) < 2 or len(ks) > 300:
            continue
        ks = sorted(ks)
        for i, a in enumerate(ks):
            for c in ks[i + 1:]:
                pairs.add((a, c))
    # acronyms: initials of a multi-word name equal to a one-word key
    single = {k for k in keys if " " not in k and 2 <= len(k) <= 6}
    for k in keys:
        toks = [t for t in k.split() if t not in ("of", "the", "de", "la", "le", "and", "et", "und", "for", "du", "des")]
        if len(toks) >= 2:
            ini = "".join(t[0] for t in toks)
            if ini in single and ini != k:
                pairs.add(tuple(sorted((k, ini))))

    def norm(k: str, how: str) -> str:
        if how == "umlaut":
            return _fold_umlaut(k)
        if how == "elision":
            return re.sub(r"\b[dl]'\s*", "", k)
        if how == "article":
            return _ART.sub("", k)
        if how == "year":
            return re.sub(r"\s*(?:19|20)\d\d$", "", k)
        if how == "inclusive":
            return re.sub(r"[.·•-]?(?:e[.·•-]?s|ne[.·•-]?s|e)\b", "", k)
        return k
    out = []
    for a, b in sorted(pairs):
        reason = ""
        for how in ("umlaut", "elision", "article", "year", "inclusive"):
            if norm(a, how) == norm(b, how) and norm(a, how):
                reason = how
                break
        ratio = difflib.SequenceMatcher(None, a, b).ratio()
        ta, tb = set(a.split()), set(b.split())
        if not reason:
            if ratio >= 0.88:
                reason = "similar spelling"
            elif (" " in a) != (" " in b) and "".join(t[0] for t in (a if " " in a else b).split()) == (
                    b if " " in a else a):
                reason = "acronym"
            elif ta and tb and (ta < tb or tb < ta) and max(len(x) for x in ta & tb or {""}) >= 4:
                reason = "contained"
        if reason:
            out.append({"key_a": a, "name_a": name[a], "key_b": b, "name_b": name[b], "reason": reason,
                        "score": round(ratio, 3), "accept": "yes" if reason == "umlaut" else ""})
    out.sort(key=lambda r: (r["reason"] != "umlaut", -r["score"], r["key_a"], r["key_b"]))
    return out


def precision_sample(an: Analysis, rows: List[dict]) -> List[dict]:
    rng = random.Random(an.a.seed)
    strata = defaultdict(list)
    for r in rows:
        strata[f"{r['max_confidence']} x {r['route']}"].append(r)
    out = []
    for st in sorted(strata):
        pool = sorted(strata[st], key=lambda r: r["org_key"])
        n = min(len(pool), an.a.precision_per_stratum)
        pick = rng.sample(pool, n) if n < len(pool) else pool
        for r in sorted(pick, key=lambda r: r["org_key"]):
            out.append({"stratum": st, "stratum_N": len(pool), "n": n, "weight": round(len(pool) / n, 4) if n else None,
                        "org_key": r["org_key"], "organisation": r["organisation"], "found_by": r["found_by"],
                        "n_events": r["n_events"], "example_context_1": r["example_context_1"],
                        "is_organisation": r.get("is_organisation", ""), "is_partial_name": r.get("is_partial_name", ""),
                        "coder": r.get("coder", "")})
    return out


def precision_readback(an: Analysis, rows: List[dict]) -> List[dict]:
    """Precision per stratum from coded org_codes (is_organisation filled in)."""
    coded = {k: c for k, c in an.codes.items() if s(c.get("is_organisation"))}
    if not coded:
        an.skipped["precision_by_stratum"] = "no is_organisation codes in --org-codes"
        return []
    strata = defaultdict(lambda: {"N": 0, "n": 0, "k": 0, "kl": 0})
    for r in rows:
        st = f"{r['max_confidence']} x {r['route']}"
        a = strata[st]
        a["N"] += 1
        c = coded.get(r["org_key"])
        if c:
            a["n"] += 1
            yes = truthy(c.get("is_organisation"))
            a["k"] += yes
            a["kl"] += yes or truthy(c.get("is_partial_name")) or s(c.get("is_organisation")).lower() == "partial"
    out = []
    totN = sum(a["N"] for a in strata.values() if a["n"])
    est = estl = 0.0
    for st in sorted(strata):
        a = strata[st]
        lo, hi = wilson(a["k"], a["n"])
        out.append({"stratum": st, "N": a["N"], "n": a["n"], "k": a["k"], "precision": share(a["k"], a["n"]),
                    "wilson_lo": lo, "wilson_hi": hi, "k_lenient": a["kl"], "precision_lenient": share(a["kl"], a["n"])})
        if a["n"] and totN:
            est += a["N"] / totN * a["k"] / a["n"]
            estl += a["N"] / totN * a["kl"] / a["n"]
    out.append({"stratum": "ALL (stratified estimate)", "N": totN, "n": sum(a["n"] for a in strata.values()),
                "k": sum(a["k"] for a in strata.values()), "precision": round(est, 4),
                "k_lenient": sum(a["kl"] for a in strata.values()), "precision_lenient": round(estl, 4)})
    an.facts["precision"] = out[-1]
    return out


# ==========================================================================
# A8 coding workbook (keyword in context)
# ==========================================================================
def _kwic(inst: dict, words: int = 14) -> Tuple[str, str, str]:
    sent, term = inst["sentence"], inst["term"]
    i = sent.casefold().find(term.casefold())
    if i < 0:
        left, mid, right = sent, term, ""
    else:
        left, mid, right = sent[:i], sent[i:i + len(term)], sent[i + len(term):]
    lw = (collapse(inst["context_before"]) + " " + collapse(left)).split()
    rw = (collapse(right) + " " + collapse(inst["context_after"])).split()
    return " ".join(lw[-words:]), mid, " ".join(rw[:words])


def a8_kwic(an: Analysis) -> dict:
    rng = random.Random(an.a.seed)
    units = OrderedDict()
    for inst in an.instances:
        if not inst["clean"]:
            continue
        key = (inst["passage_id"], inst["theme"])
        if key in units:
            continue
        refs = inst["refs"]
        ev = next((an.ev_by_id[rf["event_id"]] for rf in refs if rf["event_id"]), None)
        lev = min((rf["tie_level"] for rf in refs), default="L3")
        lw, mid, rw = _kwic(inst)
        units[key] = {"passage_id": inst["passage_id"], "theme": inst["theme"], "subtheme": inst["subtheme"],
                      "stance": inst["stance"], "term_family": inst["term_family"], "left_ctx": lw, "term": mid,
                      "right_ctx": rw, "context_before": inst["context_before"], "sentence": inst["sentence"],
                      "context_after": inst["context_after"],
                      "event": "; ".join(dict.fromkeys(inst["event_names"]))[:1000],
                      "event_year": ev["year"] if ev else "", "doc_year": inst["doc_year"] or "undated",
                      "countries": "; ".join(ev["countries"]) if ev else "",
                      "stream": ev["stream_primary"] if ev else "", "layer": refs[0]["layer"] if refs else "",
                      "layer_group": LAYER_GROUP.get(lev, "linked"), "period": ev["period"] if ev else "undated",
                      "site": inst["site"], "source_link": inst["source_link"],
                      "document": refs[0]["document"] if refs else inst["source_link"],
                      "orgs": inst["orgs_text"], "in_event_title": inst["in_event_title"],
                      "in_handle": inst["in_handle"]}
    allu = list(units.values())
    # per-document cap for the per-theme sheets
    capped = []
    for t in an.themes:
        by_doc = defaultdict(list)
        for u in allu:
            if u["theme"] == t:
                by_doc[u["document"]].append(u)
        for d in sorted(by_doc):
            pool = sorted(by_doc[d], key=lambda u: u["passage_id"])
            if len(pool) > an.a.doc_cap:
                pool = sorted(rng.sample(pool, an.a.doc_cap), key=lambda u: u["passage_id"])
            capped.extend(pool)
    # stratified sample: theme x layer group x period, proportional, at least 2 per stratum
    strata = defaultdict(list)
    for u in capped:
        strata[(u["theme"], u["layer_group"], u["period"])].append(u)
    N = len(capped)
    size = min(an.a.sample_size, N)
    alloc = {}
    for st, pool in strata.items():
        alloc[st] = min(len(pool), max(2, round(size * len(pool) / N))) if N else 0
    while sum(alloc.values()) > size and any(v > 2 for v in alloc.values()):
        st = max((x for x in alloc if alloc[x] > 2), key=lambda x: (alloc[x] - size * len(strata[x]) / N, x))
        alloc[st] -= 1
    frame = []
    for st in sorted(strata):
        pool = sorted(strata[st], key=lambda u: u["passage_id"])
        n = alloc[st]
        pick = {u["passage_id"] for u in (rng.sample(pool, n) if n < len(pool) else pool)}
        w = round(len(pool) / n, 4) if n else None
        for u in pool:
            u["in_sample"] = u["passage_id"] in pick
            u["sample_weight"] = w if u["in_sample"] else ""
            u["stratum"] = " x ".join(st)
        frame.append({"stratum": " x ".join(st), "theme": st[0], "layer_group": st[1], "period": st[2],
                      "N_h": len(pool), "n_h": n, "weight": w})
    sampled = sorted([u for u in capped if u.get("in_sample")], key=lambda u: u["passage_id"])
    dbl = set(u["passage_id"] + u["theme"] for u in rng.sample(sampled, round(0.25 * len(sampled)))) if sampled else set()
    for u in capped:
        u["double_coded"] = (u["passage_id"] + u["theme"]) in dbl
    for u in allu:
        u.setdefault("in_sample", False)
        u.setdefault("double_coded", False)
    an.facts["kwic"] = {"units": len(allu), "capped": len(capped), "sampled": len(sampled), "strata": len(frame),
                        "double_coded": len(dbl)}
    return {"all": allu, "capped": capped, "frame": frame}


KWIC_FIELDS = ["passage_id", "theme", "subtheme", "stance", "term_family", "left_ctx", "term", "right_ctx",
               "sentence", "context_before", "context_after", "event", "event_year", "doc_year", "countries", "stream", "layer",
               "site", "source_link", "orgs", "in_event_title", "in_handle", "in_sample", "sample_weight",
               "double_coded", "relevance", "frame_function", "code_1", "code_2", "code_3", "climate_linkage", "memo",
               "coder"]
