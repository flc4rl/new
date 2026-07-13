#!/usr/bin/env python3
"""
Methodology sensitivity check for the corpus frame analysis.

Isolates the two levers identified as the main sources of divergence
between independently-coded implementations of "the same" method:

  1. Word-list scope:
       A. strict   -- analytic word forms only (no bare adjectives,
                       no agent-noun/adjective forms like colonizer(s),
                       racist(s))
       B. +adj      -- strict + bare descriptive adjectives ("colonial",
                       "imperial") counted as hits -- colonialism/
                       imperialism only
       C. +forms    -- strict + missing agent/adjective forms
                       (colonizer/colonizers, racist/racists)
       D. +adj+forms -- both of the above combined (colonial/imperial
                       adjectives only)
       F. +allAdj+forms -- D, plus the same bare-adjective treatment
                       extended symmetrically to racism ("racial") and
                       fascism ("authoritarian", "totalitarian"); note
                       "fascist" needs no separate addition since the
                       base fascism pattern already matches it

  2. Counting unit:
       token     -- raw regex-match frequency across the whole document
       sentence  -- a concept counts at most once per sentence
                    (presence, not frequency), matching a common
                    alternative implementation style

Seven variants are run (A/token, B/token, C/token, D/token, D/sentence,
F/token, F/sentence) so you can see how much of the total spread comes
from the word list versus from the counting unit, and how much further
the colonial/imperial-only adjective treatment (D) shifts once the same
logic is applied evenhandedly to racism and fascism (F).

PDF extraction is done ONCE and cached to a JSONL file; every variant
re-uses the cached page text, so re-running with --cache-file pointing
at an existing cache costs seconds, not minutes.

Usage
-----
    python sensitivity_check.py --corpus-dir /path/to/pdfs \\
        --cache-file extraction_cache.jsonl \\
        --output sensitivity_results.csv
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from taxonomy import TAXONOMY as BASE_TAXONOMY, FRAME_ORDER

HYPHEN_LINEBREAK = re.compile(r"(\w)-\s*\n\s*(\w)")
WHITESPACE = re.compile(r"\s+")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


# ---------------------------------------------------------------------------
# 1. PDF extraction + cache (run once, reused by every variant)
# ---------------------------------------------------------------------------

def extract_pages(pdf_path: Path) -> List[str]:
    """Return a list of per-page text strings, or [] on failure."""
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
# 2. Taxonomy variants -- built by layering additions on the base taxonomy
# ---------------------------------------------------------------------------

def _clone(taxonomy: Dict[str, Dict[str, List[str]]]) -> Dict[str, Dict[str, List[str]]]:
    return {frame: {c: list(v) for c, v in concepts.items()} for frame, concepts in taxonomy.items()}


def add_bare_adjectives(taxonomy: Dict[str, Dict[str, List[str]]]) -> Dict[str, Dict[str, List[str]]]:
    t = _clone(taxonomy)
    t["colonialism"]["colonial_adj"] = [r"colonial"]
    t["imperialism"]["imperial_adj"] = [r"imperial"]
    return t


def add_bare_adjectives_all_frames(taxonomy: Dict[str, Dict[str, List[str]]]) -> Dict[str, Dict[str, List[str]]]:
    """Same logic as add_bare_adjectives, applied evenhandedly to all four
    frames. "Fascist" is deliberately NOT added here: the base taxonomy's
    fascis(?:m|t|ts) pattern already matches it in any grammatical role,
    so a separate bare-adjective entry would double count the same tokens."""
    t = add_bare_adjectives(taxonomy)
    t["racism"]["racial_adj"] = [r"racial"]
    t["fascism"]["authoritarian_adj"] = [r"authoritarian"]
    t["fascism"]["totalitarian_adj"] = [r"totalitarian"]
    return t


def add_missing_forms(taxonomy: Dict[str, Dict[str, List[str]]]) -> Dict[str, Dict[str, List[str]]]:
    t = _clone(taxonomy)
    t["colonialism"]["colonization"] = t["colonialism"]["colonization"] + [
        r"coloniz(?:er|ers)", r"colonis(?:er|ers)",
    ]
    t["racism"]["racism"] = t["racism"]["racism"] + [r"racist(?:s)?"]
    return t


VARIANTS: Dict[str, Tuple[Dict[str, Dict[str, List[str]]], str]] = {
    "A_strict_token":          (BASE_TAXONOMY, "token"),
    "B_plusAdjectives_token":  (add_bare_adjectives(BASE_TAXONOMY), "token"),
    "C_plusForms_token":       (add_missing_forms(BASE_TAXONOMY), "token"),
    "D_plusAdjForms_token":    (add_missing_forms(add_bare_adjectives(BASE_TAXONOMY)), "token"),
    "E_plusAdjForms_sentence": (add_missing_forms(add_bare_adjectives(BASE_TAXONOMY)), "sentence"),
    "F_allAdjForms_token":     (add_missing_forms(add_bare_adjectives_all_frames(BASE_TAXONOMY)), "token"),
    "G_allAdjForms_sentence":  (add_missing_forms(add_bare_adjectives_all_frames(BASE_TAXONOMY)), "sentence"),
}


def compile_variant(taxonomy: Dict[str, Dict[str, List[str]]]):
    concept_pattern: Dict[str, re.Pattern] = {}
    concept_frame: Dict[str, str] = {}
    for frame, concepts in taxonomy.items():
        for concept, variants in concepts.items():
            concept_pattern[concept] = re.compile(r"\b(?:" + "|".join(variants) + r")\b", re.IGNORECASE)
            concept_frame[concept] = frame
    return concept_pattern, concept_frame


# ---------------------------------------------------------------------------
# 3. Counting methods
# ---------------------------------------------------------------------------

def token_counts_for_doc(pages: List[str], concept_pattern: Dict[str, re.Pattern]) -> Dict[str, int]:
    text = HYPHEN_LINEBREAK.sub(r"\1\2", "\n".join(pages))
    text = WHITESPACE.sub(" ", text)
    return {concept: len(pattern.findall(text)) for concept, pattern in concept_pattern.items()}


def sentence_presence_counts_for_doc(pages: List[str], concept_pattern: Dict[str, re.Pattern]) -> Dict[str, int]:
    counts: Counter = Counter()
    for page_text in pages:
        if not page_text.strip():
            continue
        norm = WHITESPACE.sub(" ", page_text)
        for sentence in SENTENCE_SPLIT.split(norm):
            for concept, pattern in concept_pattern.items():
                if pattern.search(sentence):
                    counts[concept] += 1
    return dict(counts)


# ---------------------------------------------------------------------------
# 4. Run one variant over the whole corpus
# ---------------------------------------------------------------------------

def run_variant(docs: List[dict], taxonomy: Dict[str, Dict[str, List[str]]], mode: str) -> dict:
    concept_pattern, concept_frame = compile_variant(taxonomy)
    frame_totals = {f: 0 for f in FRAME_ORDER}
    zero_docs = 0

    for d in docs:
        pages = d["pages"]
        counts = (
            token_counts_for_doc(pages, concept_pattern)
            if mode == "token"
            else sentence_presence_counts_for_doc(pages, concept_pattern)
        )
        total = sum(counts.values())
        if total == 0:
            zero_docs += 1
        for concept, c in counts.items():
            frame_totals[concept_frame[concept]] += c

    grand_total = sum(frame_totals.values())
    frame_pct = {f: (100 * v / grand_total if grand_total else 0.0) for f, v in frame_totals.items()}
    n = len(docs)
    return {
        "frame_pct": frame_pct,
        "frame_totals": frame_totals,
        "zero_engagement_pct": 100 * zero_docs / n if n else 0.0,
        "n_docs": n,
    }


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus-dir", required=True, type=Path)
    parser.add_argument("--cache-file", type=Path, default=Path("extraction_cache.jsonl"),
                         help="Where to store/reuse the one-time PDF text extraction.")
    parser.add_argument("--output", type=Path, default=Path("sensitivity_results.csv"))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)

    docs = build_or_load_cache(args.corpus_dir, args.cache_file, args.workers)

    print("\nRunning variants ...")
    results = {}
    for name, (taxonomy, mode) in VARIANTS.items():
        results[name] = run_variant(docs, taxonomy, mode)
        print(f"  done: {name}")

    header = ["variant"] + [f"{f}_pct" for f in FRAME_ORDER] + ["zero_engagement_pct", "n_docs"]
    rows = []
    for name, r in results.items():
        row = [name] + [round(r["frame_pct"][f], 1) for f in FRAME_ORDER] + [
            round(r["zero_engagement_pct"], 1), r["n_docs"],
        ]
        rows.append(row)

    with args.output.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)

    col_w = 14
    print("\n" + "=" * 100)
    print(f"{'variant':<26s}" + "".join(f"{h:>{col_w}s}" for h in header[1:]))
    for row in rows:
        print(f"{row[0]:<26s}" + "".join(f"{v:>{col_w}.1f}" if isinstance(v, float) else f"{v:>{col_w}d}" for v in row[1:]))
    print("=" * 100)
    print(f"\nFull results written to {args.output}")


if __name__ == "__main__":
    main()
