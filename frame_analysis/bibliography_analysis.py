#!/usr/bin/env python3
"""
Cross-corpus bibliography / works-cited extraction.

Finds the reference section(s) in each PDF, splits them into individual
entries, and extracts a rough (surname, year) citation key for each one.
Aggregating those keys across the whole corpus gives you two things:

  1. most_cited_works.csv   -- which specific (author, year) entries recur
                               across the largest number of *different*
                               documents' bibliographies.
  2. most_cited_authors.csv -- the same, collapsed to surname only, for a
                               coarser "who does this canon draw on" view.

This is a HEURISTIC, regex-based extractor, not a full bibliographic
parser. Read this before trusting the output:

  - It works best on author-date reference styles (APA, Harvard, Chicago
    author-date) -- the norm in most social-science/humanities course
    literature. Numbered/footnote styles (IEEE, Vancouver, Chicago notes)
    are handled with a much cruder fallback and will be noisier.
  - It does NOT merge name variants ("Said, E." vs "Said, Edward W." vs
    an OCR-mangled "Sa1d, E."). The most_cited_authors.csv is a starting
    point for manual consolidation, not a finished dedup.
  - Multi-chapter edited volumes/handbooks often have one reference list
    per chapter; the heading finder collects text after EVERY "References"
    -style heading it finds, not just the last one, specifically to
    handle this case -- but it can still miss sections that use an
    unusual or missing heading.
  - Every raw extracted entry is written to per_document_entries.csv so
    you can spot-check extraction quality before trusting the rankings.

PDF extraction reuses the same cache format as sensitivity_check.py
(a JSONL file of {"path": ..., "pages": [...]}), so if you already built
extraction_cache.jsonl for the sensitivity check, point --cache-file at
it and this script skips straight to parsing -- no re-extraction needed.

Usage
-----
    python bibliography_analysis.py --corpus-dir /path/to/pdfs \\
        --cache-file extraction_cache.jsonl \\
        --output-dir bibliography_results \\
        --top-n 50
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 1. PDF extraction + cache (shared format with sensitivity_check.py)
# ---------------------------------------------------------------------------

def extract_pages(pdf_path: Path) -> List[str]:
    try:
        import fitz  # PyMuPDF

        with fitz.open(pdf_path) as doc:
            return [page.get_text() for page in doc]
    except Exception:
        return []


def build_or_load_cache(corpus_dir: Path, cache_path: Path, workers: int) -> List[dict]:
    if cache_path.exists():
        print(f"Loading cached extraction from {cache_path} ...")
        docs = []
        with cache_path.open(encoding="utf-8") as f:
            for line in f:
                docs.append(json.loads(line))
        print(f"  {len(docs)} documents loaded from cache.")
        return docs

    pdfs = sorted(corpus_dir.rglob("*.pdf"))
    print(f"Extracting text from {len(pdfs)} PDFs (one-time cost) ...")
    docs = []
    with futures.ProcessPoolExecutor(max_workers=workers) as pool:
        for i, (path, pages) in enumerate(zip(pdfs, pool.map(extract_pages, pdfs)), start=1):
            docs.append({"path": str(path), "pages": pages})
            if i % 100 == 0 or i == len(pdfs):
                print(f"  extracted {i}/{len(pdfs)}")

    with cache_path.open("w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d) + "\n")
    print(f"Cached extracted text to {cache_path}")
    return docs


# ---------------------------------------------------------------------------
# 2. Locating bibliography section(s) within a document
# ---------------------------------------------------------------------------

REFERENCE_HEADING = re.compile(
    r"^\s*(references|bibliography|works cited|literature cited|reference list)\s*$",
    re.IGNORECASE,
)
# Headings that plausibly end a reference section in an edited volume
# (next chapter's title, an index, notes, appendix ...). Best-effort only.
STOP_HEADING = re.compile(
    r"^\s*(index|appendix|acknowledg(e)?ments|notes|about the author)\s*$",
    re.IGNORECASE,
)

HYPHEN_LINEBREAK = re.compile(r"(\w)-\s*\n\s*(\w)")


def find_bibliography_text(pages: List[str]) -> str:
    """Collect text following every reference-heading occurrence (not just
    the last), so multi-chapter volumes with several bibliographies are
    covered, up to the next reference heading, stop heading, or EOF."""
    full_text = "\n".join(pages)
    lines = full_text.split("\n")

    heading_idxs = [i for i, line in enumerate(lines) if REFERENCE_HEADING.match(line.strip())]
    if not heading_idxs:
        return ""

    spans = []
    for j, start in enumerate(heading_idxs):
        end = len(lines)
        # stop at the next reference heading
        if j + 1 < len(heading_idxs):
            end = heading_idxs[j + 1]
        # or earlier, at a stop heading, whichever comes first
        for k in range(start + 1, end):
            if STOP_HEADING.match(lines[k].strip()):
                end = k
                break
        spans.append("\n".join(lines[start + 1 : end]))

    return "\n".join(spans)


# ---------------------------------------------------------------------------
# 3. Splitting bibliography text into individual entries
# ---------------------------------------------------------------------------

# Author-date style: a new entry typically starts at a line beginning with
# a capitalized surname followed by a comma (e.g. "Said, E." / "Go, J.").
AUTHOR_DATE_SPLIT = re.compile(r"\n(?=[A-Z][A-Za-zÀ-ſ'\-]+,\s)")

# Numbered/bracketed style: "[12] " or "12. " at the start of a line.
NUMBERED_SPLIT = re.compile(r"\n(?=\[\d{1,4}\]\s|\d{1,4}\.\s)")

YEAR_PAREN = re.compile(r"\((\d{4}[a-z]?)\)")
YEAR_BARE = re.compile(r"\b(19|20)\d{2}[a-z]?\b")
# Author-date style: surname comes first ("Said, E." / "Wallerstein, I.").
SURNAME_FIRST = re.compile(r"^([A-Z][A-Za-zÀ-ſ'\-]+)")
# Numbered/IEEE style: one or more initials precede the surname
# ("E. Said," / "H. J. Arendt,").
INITIALS_THEN_SURNAME = re.compile(r"^(?:[A-Z]\.\s*){1,3}([A-Z][A-Za-zÀ-ſ'\-]+)")
# Last resort: any capitalized word of 3+ letters before the year.
ANY_CAPITALIZED_WORD = re.compile(r"\b([A-Z][A-Za-zÀ-ſ'\-]{2,})\b")
LEADING_NUMBER = re.compile(r"^(\[\d{1,4}\]|\d{1,4}\.)\s*")
WHITESPACE = re.compile(r"\s+")


def split_entries(biblio_text: str) -> Tuple[List[str], str]:
    """Returns (entries, style_guess). Tries author-date splitting first;
    falls back to numbered-style splitting if that yields too few entries
    relative to the amount of text (a rough proxy for "wrong style")."""
    cleaned = HYPHEN_LINEBREAK.sub(r"\1\2", biblio_text)

    ad_entries = [e.strip() for e in AUTHOR_DATE_SPLIT.split(cleaned) if e.strip()]
    if len(ad_entries) >= 3 and len(cleaned) / max(len(ad_entries), 1) < 600:
        return ad_entries, "author_date"

    num_entries = [e.strip() for e in NUMBERED_SPLIT.split(cleaned) if e.strip()]
    if len(num_entries) >= 3:
        return num_entries, "numbered"

    return ad_entries, "author_date"  # best guess, may be empty/sparse


# ---------------------------------------------------------------------------
# 4. Extracting a rough (surname, year) citation key per entry
# ---------------------------------------------------------------------------

def parse_entry(entry: str, style: str) -> Optional[Tuple[str, str]]:
    text = LEADING_NUMBER.sub("", WHITESPACE.sub(" ", entry)).strip()
    if not text:
        return None

    year_match = YEAR_PAREN.search(text) or YEAR_BARE.search(text)
    if not year_match:
        return None
    year = year_match.group(0).strip("()")

    author_field = text[: year_match.start()]
    surname_match = (
        SURNAME_FIRST.search(author_field)
        or INITIALS_THEN_SURNAME.search(author_field)
        or ANY_CAPITALIZED_WORD.search(author_field)
    )
    if not surname_match:
        return None
    surname = surname_match.group(1)

    # filter obviously-wrong single-letter / stray matches
    if len(surname) < 2:
        return None
    return surname, year


# ---------------------------------------------------------------------------
# 5. Run over the whole corpus
# ---------------------------------------------------------------------------

def analyze_corpus(docs: List[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    citation_key_docs: Dict[Tuple[str, str], set] = defaultdict(set)
    author_docs: Dict[str, set] = defaultdict(set)
    per_doc_rows = []

    for d in docs:
        path = d["path"]
        biblio = find_bibliography_text(d["pages"])
        if not biblio:
            continue
        entries, style = split_entries(biblio)
        for entry in entries:
            parsed = parse_entry(entry, style)
            per_doc_rows.append({
                "document": path,
                "style_guess": style,
                "surname": parsed[0] if parsed else "",
                "year": parsed[1] if parsed else "",
                "raw_entry": entry[:300],
            })
            if parsed:
                surname, year = parsed
                key = (surname.lower(), year)
                citation_key_docs[key].add(path)
                author_docs[surname.lower()].add(path)

    with (output_dir / "per_document_entries.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["document", "style_guess", "surname", "year", "raw_entry"])
        w.writeheader()
        w.writerows(per_doc_rows)

    with (output_dir / "most_cited_works.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["surname", "year", "cited_by_n_documents"])
        for (surname, year), doc_set in sorted(citation_key_docs.items(), key=lambda kv: -len(kv[1])):
            w.writerow([surname, year, len(doc_set)])

    with (output_dir / "most_cited_authors.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["surname", "cited_by_n_documents"])
        for surname, doc_set in sorted(author_docs.items(), key=lambda kv: -len(kv[1])):
            w.writerow([surname, len(doc_set)])

    docs_with_biblio = sum(1 for d in docs if find_bibliography_text(d["pages"]))
    print(f"\n{docs_with_biblio}/{len(docs)} documents had a detectable bibliography section.")
    print(f"{len(per_doc_rows)} raw reference entries extracted "
          f"({sum(1 for r in per_doc_rows if r['surname'])} parsed to author+year).")
    print(f"Results written to {output_dir}/")


# ---------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus-dir", required=True, type=Path)
    parser.add_argument("--cache-file", type=Path, default=Path("extraction_cache.jsonl"),
                         help="Reuses the same cache format as sensitivity_check.py.")
    parser.add_argument("--output-dir", type=Path, default=Path("bibliography_results"))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)

    docs = build_or_load_cache(args.corpus_dir, args.cache_file, args.workers)
    analyze_corpus(docs, args.output_dir)


if __name__ == "__main__":
    main()
