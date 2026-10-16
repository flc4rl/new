#!/usr/bin/env python3
"""
Crawl a website, map key themes, and extract every collective, group, NGO and
other organisation it mentions, with the surrounding text of each mention for
qualitative assessment.

Usage
-----
    python site_actors.py https://example.org \\
        --themes example_themes.txt \\
        --watchlist example_watchlist.txt \\
        --output-dir results/example_org \\
        --max-pages 300

Re-run the extraction on the cached pages with different settings, without
crawling again:

    python site_actors.py https://example.org --output-dir results/example_org \\
        --no-crawl --themes my_themes.txt

See README.md for the full CLI and output description.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from crawler import CrawlConfig, Crawler, PageCache, normalise_url, registered_host
from extract import (
    OrgExtractor, ParsedPage, ThemeSet, Watchlist, acronym_pairs, block_hash,
    canonical_key, load_stoplist, parse_html, parse_pdf_text, split_sentences,
)
from report import write_report

log = logging.getLogger("site_actors")
HERE = Path(__file__).resolve().parent


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("start_urls", nargs="+", help="One or more start URLs (same site)")
    ap.add_argument("--output-dir", "-o", type=Path, required=True)

    g = ap.add_argument_group("crawling")
    g.add_argument("--max-pages", type=int, default=500)
    g.add_argument("--max-depth", type=int, default=5)
    g.add_argument("--delay", type=float, default=1.0, help="Seconds between requests")
    g.add_argument("--timeout", type=float, default=20.0)
    g.add_argument("--user-agent", default="site-actor-mapping/1.0 (academic research crawler)")
    g.add_argument("--ignore-robots", action="store_true", help="Do not honour robots.txt")
    g.add_argument("--include-subdomains", action="store_true")
    g.add_argument("--include", action="append", default=[], metavar="REGEX",
                   help="Only crawl URLs matching this regex (repeatable)")
    g.add_argument("--exclude", action="append", default=[], metavar="REGEX",
                   help="Skip URLs matching this regex (repeatable), e.g. '/tag/' '/shop/'")
    g.add_argument("--no-pdfs", action="store_true",
                   help="Skip PDFs (by default linked and embedded PDFs are read, wherever hosted)")
    g.add_argument("--pdfs-same-site-only", action="store_true",
                   help="Only read PDFs hosted on the site itself, not on Drive, CDNs etc.")
    g.add_argument("--max-pdf-mb", type=float, default=50.0, help="Skip PDFs larger than this")
    g.add_argument("--include-pdfs", action="store_true", help=argparse.SUPPRESS)  # now the default
    g.add_argument("--use-sitemap", action="store_true", help="Seed the crawl from sitemap.xml")
    g.add_argument("--render-js", action="store_true",
                   help="Render pages in headless Chromium (needs playwright) for JS-heavy sites")
    g.add_argument("--refresh", action="store_true", help="Refetch pages already in the cache")
    g.add_argument("--no-crawl", action="store_true", help="Only analyse pages already cached")

    g = ap.add_argument_group("extraction")
    g.add_argument("--themes", type=Path, default=HERE / "example_themes.txt",
                   help="Theme file ([theme] headers followed by terms)")
    g.add_argument("--watchlist", type=Path, help="Known organisations: 'Name | alias | alias' per line")
    g.add_argument("--stoplist", type=Path, default=HERE / "default_stoplist.txt",
                   help="Names to ignore (one per line, 're:' prefix for regex)")
    g.add_argument("--spacy-model", default="en_core_web_sm",
                   help="spaCy NER model, e.g. it_core_news_sm, xx_ent_wiki_sm, or 'none'")
    g.add_argument("--no-links", action="store_true",
                   help="Do not treat outbound-link text / logo alt text as organisation names")
    g.add_argument("--strip-regions", action="store_true",
                   help="Discard nav/header/footer/aside before extraction (default: keep, but flag)")
    g.add_argument("--context-sentences", type=int, default=1,
                   help="Sentences either side of the mention to keep as context")
    g.add_argument("--theme-scope", choices=["any", "page", "paragraph", "sentence"], default="any",
                   help="Keep only mentions whose page / paragraph / context window hits a theme")
    g.add_argument("--focus-themes", default="",
                   help="Comma-separated theme names that count for --theme-scope (default: all)")
    g.add_argument("--boilerplate-share", type=float, default=0.2,
                   help="Paragraphs repeated on more than this share of pages are flagged boilerplate")
    g.add_argument("--min-mentions", type=int, default=1,
                   help="Drop organisations with fewer distinct mentions from organisations.csv/report")
    ap.add_argument("--verbose", "-v", action="store_true")
    return ap.parse_args(argv)


# --------------------------------------------------------------------------
def load_pages(cache: PageCache, hosts: Set[str], include_subdomains: bool,
               strip_regions: bool) -> Tuple[List[ParsedPage], List[dict]]:
    """Parse cached pages of this site, plus PDFs found on them (wherever hosted).

    Also returns an inventory of every PDF seen, including those that could
    not be read, so gaps in coverage are visible.
    """
    def ours(url: str) -> bool:
        host = registered_host(urlparse(url).netloc)
        return host in hosts or (include_subdomains and any(host.endswith("." + h) for h in hosts))

    pages: Dict[str, ParsedPage] = {}
    pdf_rows: List[dict] = []
    for cp in cache:
        if cp.is_pdf:
            if not (ours(cp.url) or (cp.referrer and ours(cp.referrer))):
                continue
            n_pages = cp.body.count("\f") + 1 if cp.body else 0
            words = len(cp.body.split())
            status = ("http error" if cp.status >= 400 else
                      "no text layer (scanned?)" if not words else "read")
            pdf_rows.append({"pdf_url": cp.url, "embedded_in": cp.referrer, "title": cp.title,
                             "date": cp.date, "pages": n_pages, "words": words,
                             "status": f"{status} ({cp.status})" if cp.status >= 400 else status})
            if status != "read" or cp.url in pages:
                continue
            try:
                pages[cp.url] = parse_pdf_text(cp.body, cp.url, cp.depth, cp.title, cp.date, cp.referrer)
            except Exception as exc:  # noqa: BLE001 - one bad file should not stop the run
                log.warning("Could not parse PDF %s: %s", cp.url, exc)
            continue
        if cp.status >= 400 or not cp.body or not ours(cp.final_url):
            continue
        key = cp.final_url
        if key in pages:          # redirects: several requested URLs, one final page
            continue
        try:
            pages[key] = parse_html(cp.body, cp.final_url, cp.depth, strip_regions)
        except Exception as exc:  # noqa: BLE001 - one bad page should not stop the run
            log.warning("Could not parse %s: %s", cp.final_url, exc)
    return sorted(pages.values(), key=lambda p: (p.depth, p.url)), pdf_rows


def window(sents, idx: int, n: int, text: str) -> str:
    lo, hi = max(0, idx - n), min(len(sents) - 1, idx + n)
    return text[sents[lo][0]:sents[hi][1]]


def themes_str(hits: Dict[str, object]) -> str:
    """'theme (n); theme (n)' from either {theme: [hits]} or {theme: count}."""
    return "; ".join(f"{t} ({v if isinstance(v, int) else len(v)})" for t, v in sorted(hits.items()))


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    cache = PageCache(out / "cache")
    start_urls = [normalise_url(u if "://" in u else "https://" + u) for u in args.start_urls]

    if not args.no_crawl:
        Crawler(CrawlConfig(
            start_urls=start_urls, max_pages=args.max_pages, max_depth=args.max_depth,
            delay=args.delay, timeout=args.timeout, user_agent=args.user_agent,
            respect_robots=not args.ignore_robots, include_subdomains=args.include_subdomains,
            include_patterns=args.include, exclude_patterns=args.exclude,
            include_pdfs=not args.no_pdfs, pdfs_from_other_hosts=not args.pdfs_same_site_only,
            max_pdf_mb=args.max_pdf_mb, use_sitemap=args.use_sitemap,
            render_js=args.render_js, refresh=args.refresh,
        ), cache).crawl()

    hosts = {registered_host(urlparse(u).netloc) for u in start_urls}
    pages, pdf_rows = load_pages(cache, hosts, args.include_subdomains, args.strip_regions)
    if not pages:
        log.error("No pages to analyse in %s", cache.root)
        return 1
    log.info("Analysing %d pages, including %d PDFs (%d PDFs found in total)",
             len(pages), sum(p.is_pdf for p in pages), len(pdf_rows))

    themes = ThemeSet.load(args.themes if args.themes and args.themes.exists() else None)
    focus = {t.strip() for t in args.focus_themes.split(",") if t.strip()} or set(themes.names)
    unknown = focus - set(themes.names)
    if unknown:
        log.warning("Unknown --focus-themes ignored: %s", ", ".join(sorted(unknown)))
    extractor = OrgExtractor(Watchlist.load(args.watchlist), load_stoplist(args.stoplist),
                             args.spacy_model, use_links=not args.no_links)

    # Paragraphs repeated across many pages (menus, footers, partner strips).
    block_pages: Counter = Counter()
    for p in pages:
        block_pages.update({block_hash(b.text) for b in p.blocks if b.text})
    bp_threshold = max(3, args.boilerplate_share * len(pages))
    boilerplate = {h for h, n in block_pages.items() if n > bp_threshold}

    # Acronym definitions across the whole site.
    acronyms: Dict[str, str] = {}
    acr_display: Dict[str, Counter] = defaultdict(Counter)
    for p in pages:
        for b in p.blocks:
            for long_form, acr in acronym_pairs(b.text):
                lk = canonical_key(long_form)
                if extractor._head_type(long_form.split()) or lk in extractor.watchlist.canonical:
                    acronyms.setdefault(canonical_key(acr), lk)
                    acr_display[lk][acr] += 1

    mention_rows: List[dict] = []
    page_rows: List[dict] = []
    theme_terms: Dict[str, Counter] = defaultdict(Counter)
    theme_pages: Counter = Counter()
    seen_context: Set[tuple] = set()

    for pi, page in enumerate(pages, 1):
        if pi % 50 == 0:
            log.info("  %d/%d pages", pi, len(pages))
        texts = [b.text for b in page.blocks]
        docs = (list(extractor.nlp.pipe(texts, batch_size=64)) if extractor.nlp is not None
                else [None] * len(texts))
        block_hits = [themes.find(t) for t in texts]
        page_hits: Dict[str, int] = Counter()
        for bh in block_hits:
            for t, v in bh.items():
                page_hits[t] += len(v)
                for _, _, term in v:
                    theme_terms[t][term.lower()] += 1
        for t in page_hits:
            theme_pages[t] += 1

        n_mentions = 0
        for block, doc, bhits in zip(page.blocks, docs, block_hits):
            mentions = extractor.extract(block.text, block.links, doc)
            if not mentions:
                continue
            for m in mentions:
                m.key = acronyms.get(m.key, m.key)
            is_bp = block_hash(block.text) in boilerplate if block.text else True
            sents = split_sentences(block.text) if block.text else []
            keys_here = sorted({m.key for m in mentions})
            for m in mentions:
                if m.start >= 0 and sents:
                    idx = next((i for i, (s, e) in enumerate(sents) if s <= m.start < e), 0)
                    sentence = block.text[sents[idx][0]:sents[idx][1]]
                    context = window(sents, idx, args.context_sentences, block.text)
                else:
                    sentence = context = block.text or f"[link] {m.surface}"
                ctx_hits = themes.find(context)

                scope_hits = {"any": None, "page": page_hits, "paragraph": bhits,
                              "sentence": ctx_hits}[args.theme_scope]
                if scope_hits is not None and not (set(scope_hits) & focus):
                    continue

                ctx_id = (m.key, block_hash(context))
                duplicate = ctx_id in seen_context
                seen_context.add(ctx_id)
                n_mentions += 1
                mention_rows.append({
                    "org_key": m.key,
                    "surface_form": m.surface,
                    "methods": "+".join(sorted(m.methods)),
                    "org_type": m.org_type,
                    "link_domain": m.link_domain,
                    "url": page.url,
                    "page_title": page.title,
                    "page_date": page.date,
                    "region": block.region,
                    "is_pdf": page.is_pdf,
                    "embedded_in": page.referrer,
                    "boilerplate": is_bp,
                    "repeat_context": duplicate,
                    "sentence": sentence,
                    "context": context,
                    "paragraph": block.text[:3000],
                    "context_themes": themes_str(ctx_hits),
                    "context_theme_terms": "; ".join(sorted({x[2].lower() for v in ctx_hits.values() for x in v})),
                    "paragraph_themes": themes_str(bhits),
                    "page_themes": themes_str(page_hits),
                    "co_mentioned_keys": [k for k in keys_here if k != m.key],
                })
        page_rows.append({
            "url": page.url, "title": page.title, "date": page.date, "depth": page.depth,
            "is_pdf": page.is_pdf, "embedded_in": page.referrer, "words": sum(len(b.text.split()) for b in page.blocks),
            "org_mentions": n_mentions,
            **{f"theme:{t}": page_hits.get(t, 0) for t in themes.names},
        })

    orgs = aggregate(mention_rows, extractor, acr_display, themes.names, args.min_mentions)
    display = {o["org_key"]: o["organisation"] for o in orgs}
    for r in mention_rows:
        r["organisation"] = display.get(r["org_key"], "")
        r["co_mentioned"] = "; ".join(display.get(k, k) for k in r.pop("co_mentioned_keys"))
    kept = [r for r in mention_rows if r["organisation"]]

    write_outputs(out, kept, orgs, page_rows, themes, theme_terms, theme_pages, len(pages), args)
    write_csv(out / "pdfs.csv", pdf_rows,
              ["pdf_url", "embedded_in", "title", "date", "pages", "words", "status"])
    write_report(out / "report.html", orgs, kept, themes, theme_pages, len(pages), start_urls)
    log.info("Done: %d organisations, %d mentions, %d pages -> %s",
             len(orgs), len(kept), len(pages), out)
    return 0


# --------------------------------------------------------------------------
def aggregate(rows: List[dict], extractor: OrgExtractor, acr_display: Dict[str, Counter],
              theme_names: List[str], min_mentions: int) -> List[dict]:
    by_key: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_key[r["org_key"]].append(r)

    orgs = []
    for key, rs in by_key.items():
        distinct = [r for r in rs if not r["repeat_context"]]
        substantive = [r for r in distinct if not r["boilerplate"]]
        if len(distinct) < min_mentions:
            continue
        forms = Counter(r["surface_form"] for r in rs)
        name = extractor.watchlist.display.get(key) or _best_form(forms)
        methods = Counter(m for r in rs for m in r["methods"].split("+"))
        types = Counter(r["org_type"] for r in rs if r["org_type"])
        theme_counts = Counter()
        for r in distinct:
            for part in filter(None, r["paragraph_themes"].split("; ")):
                theme_counts[part.rsplit(" (", 1)[0]] += 1
        pages = {r["url"] for r in rs}
        orgs.append({
            "organisation": name,
            "org_key": key,
            "org_type": types.most_common(1)[0][0] if types else "",
            "confidence": _confidence(methods, len(pages)),
            "distinct_mentions": len(distinct),
            "substantive_mentions": len(substantive),
            "total_mentions": len(rs),
            "pages": len(pages),
            "boilerplate_only": not substantive,
            "methods": "; ".join(f"{m} ({n})" for m, n in methods.most_common()),
            "variants": "; ".join(f"{f} ({n})" for f, n in forms.most_common(8)),
            "acronyms": "; ".join(a for a, _ in acr_display.get(key, Counter()).most_common(3)),
            "themes": "; ".join(f"{t} ({n})" for t, n in theme_counts.most_common()),
            "themed_mentions": sum(1 for r in distinct if r["paragraph_themes"]),
            "linked_domains": "; ".join(sorted({r["link_domain"] for r in rs if r["link_domain"]})),
            "first_url": rs[0]["url"],
            **{f"theme:{t}": theme_counts.get(t, 0) for t in theme_names},
        })
    orgs.sort(key=lambda o: (-o["substantive_mentions"], -o["distinct_mentions"], o["organisation"].lower()))
    return orgs


def _best_form(forms: Counter) -> str:
    # Most frequent surface form, preferring spelled-out names over acronyms;
    # ties go to the longer (more complete) form.
    def is_acronym(f: str) -> bool:
        return bool(re.fullmatch(r"[A-Z0-9&.\-]{2,12}", f))
    return max(forms.items(), key=lambda kv: (not is_acronym(kv[0]), kv[1], len(kv[0])))[0]


def _confidence(methods: Counter, n_pages: int) -> str:
    strong = {"watchlist", "appositive"}
    if strong & set(methods) or len(methods) >= 2:
        return "high"
    if "pattern" in methods or "link" in methods or n_pages >= 3:
        return "medium"
    return "low"


def write_csv(path: Path, rows: List[dict], fields: Optional[List[str]] = None) -> None:
    if not rows and not fields:
        path.write_text("", encoding="utf-8")
        return
    fields = fields or list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8-sig") as fh:  # BOM so Excel reads UTF-8
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_outputs(out: Path, mentions: List[dict], orgs: List[dict], page_rows: List[dict],
                  themes: ThemeSet, theme_terms: Dict[str, Counter], theme_pages: Counter,
                  n_pages: int, args: argparse.Namespace) -> None:
    mention_fields = [
        "organisation", "surface_form", "org_type", "methods", "url", "page_title", "page_date",
        "is_pdf", "embedded_in", "region", "boilerplate", "repeat_context", "context_themes", "context_theme_terms",
        "paragraph_themes", "page_themes", "sentence", "context", "paragraph", "co_mentioned",
        "link_domain", "org_key",
    ]
    write_csv(out / "mentions.csv", mentions, mention_fields)
    write_csv(out / "organisations.csv", orgs)
    write_csv(out / "pages.csv", page_rows)

    # Organisation co-occurrence (same paragraph), for network tools such as Gephi.
    names = {o["organisation"] for o in orgs}
    edges: Counter = Counter()
    for r in mentions:
        if r["repeat_context"]:
            continue
        for other in filter(None, r["co_mentioned"].split("; ")):
            if other in names and r["organisation"] < other:
                edges[(r["organisation"], other)] += 1
    write_csv(out / "org_cooccurrence.csv",
              [{"source": a, "target": b, "weight": n} for (a, b), n in edges.most_common()],
              ["source", "target", "weight"])

    summary = {
        "start_urls": args.start_urls,
        "pages_analysed": n_pages,
        "organisations": len(orgs),
        "mentions": len(mentions),
        "theme_scope": args.theme_scope,
        "themes": {
            t: {"pages": theme_pages.get(t, 0),
                "share_of_pages": round(theme_pages.get(t, 0) / n_pages, 3),
                "hits": sum(theme_terms[t].values()),
                "top_terms": theme_terms[t].most_common(15)}
            for t in themes.names
        },
        "organisation_types": Counter(o["org_type"] or "unknown" for o in orgs).most_common(),
        "confidence": Counter(o["confidence"] for o in orgs).most_common(),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                      encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
