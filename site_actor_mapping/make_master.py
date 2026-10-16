#!/usr/bin/env python3
"""
Build the two master files from one or more result folders:

  master_network   one row per event x organisation x source page: which
                   organisations were found for which event, and where.
                   A second sheet lists each event once with all its
                   organisations; a third holds names found only in menus,
                   headers, footers or text repeated on every page of a site
                   (site furniture), kept apart so they do not inflate the
                   network.
  master_themes    one row per theme term found: the event, the source link,
                   and the passage (two sentences before and after).

Written as .xlsx (one sheet per table, filters, frozen header) and as .csv.
site_actors.py calls this at the end of every run; run it yourself to merge
several runs:

    python make_master.py results/movementhub results/pdf_agendas -o results/master
"""

from __future__ import annotations

import argparse
import csv
import logging
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Dict, List, Sequence

log = logging.getLogger(__name__)
csv.field_size_limit(10_000_000)

NO_EVENT = "(not tied to an event)"
CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

NETWORK_FIELDS = ["event", "event_date", "event_link", "organisation", "org_type", "confidence",
                  "mentions_on_source", "source_link", "source_title", "site", "site_role",
                  "site_linked_from_events", "found_by", "named_in_event_title",
                  "only_in_menus_or_footers", "example_context"]
EVENT_FIELDS = ["event", "event_date", "event_link", "n_organisations", "organisations", "sources"]
THEME_FIELDS = ["theme", "term", "event", "event_date", "event_link", "site_linked_from_events",
                "source_link", "page_title",
                "context_before", "sentence", "context_after", "organisations_in_paragraph",
                "site", "site_role", "is_pdf", "pdf_found_on", "region", "boilerplate",
                "duplicate_of"]


def _read(path: Path) -> List[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _split(value: str) -> List[str]:
    return [v.strip() for v in (value or "").split(" | ")]


def _events_of(row: dict) -> List[tuple]:
    names, dates, links = _split(row.get("event", "")), _split(row.get("event_date", "")), _split(row.get("event_link", ""))
    if not any(names) and not any(links):
        return [(NO_EVENT, "", "")]
    n = max(len(names), len(dates), len(links))
    pad = lambda xs: xs + [""] * (n - len(xs))  # noqa: E731
    return list(zip(pad(names), pad(dates), pad(links)))


def network_rows(results: Sequence[Path], min_confidence: str = "low") -> List[dict]:
    rows: "OrderedDict[tuple, dict]" = OrderedDict()
    for res in results:
        conf = {o["org_key"]: o for o in _read(res / "organisations.csv")}
        for m in _read(res / "mentions.csv"):
            org = conf.get(m["org_key"], {})
            confidence = org.get("confidence", "")
            if CONFIDENCE_RANK.get(confidence, 0) < CONFIDENCE_RANK[min_confidence]:
                continue
            for name, date, link in _events_of(m):
                key = (name, link, m["organisation"], m["url"])
                r = rows.get(key)
                if r is None:
                    r = rows[key] = {
                        "event": name, "event_date": date, "event_link": link,
                        "organisation": m["organisation"], "org_type": m.get("org_type") or org.get("org_type", ""),
                        "confidence": confidence, "mentions_on_source": 0,
                        "source_link": m["url"], "source_title": m.get("page_title", ""),
                        "site": m.get("site", ""), "site_role": m.get("site_role", ""),
                        "site_linked_from_events": m.get("site_linked_from_events", ""),
                        "named_in_event_title": m.get("named_in_event_title") == "True",
                        "_methods": Counter(), "_bp": True, "example_context": "", "_ex_bp": True,
                    }
                r["mentions_on_source"] += 1
                r["_methods"].update(m.get("methods", "").split("+"))
                # Menus, headers, footers and text repeated across a site's pages.
                is_bp = m.get("boilerplate") == "True" or m.get("in_menu") == "True"
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
        key = (r["event"], r["event_link"])
        e = by_event.setdefault(key, {"event": r["event"], "event_date": r["event_date"],
                                      "event_link": r["event_link"], "_orgs": OrderedDict(),
                                      "_src": OrderedDict()})
        e["_orgs"][r["organisation"]] = None
        e["_src"][r["source_link"]] = None
    out = []
    for e in by_event.values():
        orgs, srcs = list(e.pop("_orgs")), list(e.pop("_src"))
        out.append({**e, "n_organisations": len(orgs), "organisations": "; ".join(orgs),
                    "sources": " ".join(srcs)})
    return out


def theme_rows(results: Sequence[Path]) -> List[dict]:
    out = []
    for res in results:
        out += _read(res / "theme_hits.csv")
    for r in out:
        if not (r.get("event") or "").strip():
            r["event"] = NO_EVENT
    out.sort(key=lambda r: (r["event"].startswith("("), r.get("event_date") or "", r["event"].casefold(),
                            r.get("source_link", ""), r.get("theme", "")))
    return out


def _write_csv(path: Path, rows: List[dict], fields: List[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _write_xlsx(path: Path, sheets: List[tuple]) -> bool:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
        from openpyxl.utils import get_column_letter
    except ImportError:
        log.warning("openpyxl not installed; master files written as CSV only (pip install openpyxl)")
        return False
    wide = {"context_before": 60, "sentence": 60, "context_after": 60, "example_context": 60,
            "organisations": 60, "organisations_in_paragraph": 40, "event": 40, "source_title": 30,
            "page_title": 30, "sources": 50, "source_link": 40, "event_link": 40, "organisation": 30}
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows, fields in sheets:
        ws = wb.create_sheet(title)
        ws.append(fields)
        for r in rows:
            ws.append([_cell(r.get(f, "")) for f in fields])
        for i, f in enumerate(fields, 1):
            ws.column_dimensions[get_column_letter(i)].width = wide.get(f, 16)
            ws.cell(1, i).font = Font(bold=True)
        wrap = {i for i, f in enumerate(fields, 1) if wide.get(f, 0) >= 40}
        for row in ws.iter_rows(min_row=2):
            for c in row:
                if c.column in wrap:
                    c.alignment = Alignment(wrap_text=True, vertical="top")
                else:
                    c.alignment = Alignment(vertical="top")
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
    wb.save(path)
    return True


def _cell(v):
    if isinstance(v, bool):
        return "yes" if v else "no"
    if v in ("True", "False"):
        return "yes" if v == "True" else "no"
    s = str(v)
    return s[:32000]   # Excel's cell limit


def build_masters(results: Sequence[Path], out_dir: Path, min_confidence: str = "low") -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    all_net = network_rows(results, min_confidence)
    net = [r for r in all_net if not r["only_in_menus_or_footers"]]
    furniture = [r for r in all_net if r["only_in_menus_or_footers"]]
    events = event_rows(net)
    themes = theme_rows(results)
    paths = []
    _write_csv(out_dir / "master_network.csv", net, NETWORK_FIELDS)
    _write_csv(out_dir / "master_themes.csv", themes, THEME_FIELDS)
    paths += [out_dir / "master_network.csv", out_dir / "master_themes.csv"]
    if _write_xlsx(out_dir / "master_network.xlsx", [("event_organisations", net, NETWORK_FIELDS),
                                                      ("events", events, EVENT_FIELDS),
                                                      ("menus_and_footers", furniture, NETWORK_FIELDS)]):
        paths.append(out_dir / "master_network.xlsx")
    if _write_xlsx(out_dir / "master_themes.xlsx", [("theme_instances", themes, THEME_FIELDS)]):
        paths.append(out_dir / "master_themes.xlsx")
    return paths


def main() -> None:
    ap = argparse.ArgumentParser(description="Merge result folders into the two master files")
    ap.add_argument("results", nargs="+", type=Path, help="Result folders written by site_actors.py")
    ap.add_argument("--output-dir", "-o", type=Path, required=True)
    ap.add_argument("--min-confidence", choices=["low", "medium", "high"], default="low",
                    help="Leave out organisations below this confidence in master_network")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for p in build_masters(args.results, args.output_dir, args.min_confidence):
        print(p)


if __name__ == "__main__":
    main()
