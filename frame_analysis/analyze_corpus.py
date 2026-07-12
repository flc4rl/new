#!/usr/bin/env python3
"""
Departmental course-literature frame analysis.

Reverse-engineered pipeline for the methodology described in the study:
a corpus of departmental course PDFs is parsed, scanned for a taxonomy of
critical-theory concepts grouped into four broader frames (colonialism,
imperialism, racism, fascism), and reduced to:

  1. Per-document conceptual breadth  -- number of *distinct* concepts
     (regardless of frequency) that appear in the document.
  2. Per-document / corpus-wide frame dominance -- how a document's (or
     the corpus's) total concept hits are distributed across the four
     frames, so length/rhetorical-style biases wash out.
  3. A ranked shortlist of the highest-hit documents, for the qualitative
     close-reading pass.

Usage
-----
    python analyze_corpus.py --corpus-dir /path/to/pdfs \\
        --exclude-list unpublished_exclude.txt \\
        --output-dir results \\
        --top-n 40

See README.md for the full CLI and output description.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import csv
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from taxonomy import TAXONOMY, FRAME_ORDER, EXCLUDE_CONTEXT, all_concepts, concept_to_frame

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("frame_analysis")

CONCEPTS = all_concepts()
CONCEPT_TO_FRAME = concept_to_frame()

# Compiled once per concept: a single alternation of all surface-form
# variants, wrapped in word boundaries.
_COMPILED_PATTERNS: Dict[str, re.Pattern] = {
    concept: re.compile(r"\b(?:" + "|".join(variants) + r")\b", re.IGNORECASE)
    for frame in TAXONOMY.values()
    for concept, variants in frame.items()
}

_HYPHEN_LINEBREAK = re.compile(r"(\w)-\s*\n\s*(\w)")
_WHITESPACE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text(pdf_path: Path) -> str:
    """Extract and lightly normalize text from a PDF.

    Uses PyMuPDF (fitz). Falls back to pdfplumber if PyMuPDF fails to open
    the file (e.g. certain malformed PDFs), since the two libraries have
    different, largely non-overlapping failure modes.
    """
    text = ""
    try:
        import fitz  # PyMuPDF

        with fitz.open(pdf_path) as doc:
            text = "\n".join(page.get_text() for page in doc)
    except Exception as exc:  # noqa: BLE001 - want to try the fallback on any failure
        log.debug("PyMuPDF failed on %s (%s); trying pdfplumber", pdf_path, exc)
        try:
            import pdfplumber

            with pdfplumber.open(pdf_path) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        except Exception as exc2:  # noqa: BLE001
            log.warning("Failed to extract text from %s: %s", pdf_path, exc2)
            return ""

    # Undo hyphenation across a line break ("coloni-\nalism" -> "colonialism")
    text = _HYPHEN_LINEBREAK.sub(r"\1\2", text)
    text = _WHITESPACE.sub(" ", text)
    return text


# ---------------------------------------------------------------------------
# Concept counting
# ---------------------------------------------------------------------------

def is_descriptive_context(text: str, start: int, end: int, window: int = 40) -> bool:
    """True if the raw hit at [start, end) sits inside a known descriptive/
    background phrase (see taxonomy.EXCLUDE_CONTEXT) and should therefore
    be discounted rather than counted as substantive conceptual engagement.
    """
    if not EXCLUDE_CONTEXT:
        return False
    lo = max(0, start - window)
    hi = min(len(text), end + window)
    snippet = text[lo:hi].lower()
    return any(phrase in snippet for phrase in EXCLUDE_CONTEXT)


def count_concepts(text: str) -> Dict[str, int]:
    """Return {concept: hit_count} for a single document's text, after
    discarding hits that fall inside a descriptive/background context.
    """
    counts: Dict[str, int] = {c: 0 for c in CONCEPTS}
    for concept, pattern in _COMPILED_PATTERNS.items():
        for match in pattern.finditer(text):
            if not is_descriptive_context(text, match.start(), match.end()):
                counts[concept] += 1
    return counts


def frame_counts_from_concepts(concept_counts: Dict[str, int]) -> Dict[str, int]:
    frame_counts = {f: 0 for f in FRAME_ORDER}
    for concept, count in concept_counts.items():
        frame_counts[CONCEPT_TO_FRAME[concept]] += count
    return frame_counts


@dataclass
class DocumentResult:
    path: str
    filename: str
    concept_counts: Dict[str, int] = field(default_factory=dict)
    frame_counts: Dict[str, int] = field(default_factory=dict)
    total_hits: int = 0
    breadth: int = 0
    dominant_frame: Optional[str] = None
    char_count: int = 0
    extraction_ok: bool = True

    def frame_proportions(self) -> Dict[str, float]:
        if self.total_hits == 0:
            return {f: 0.0 for f in FRAME_ORDER}
        return {f: self.frame_counts[f] / self.total_hits for f in FRAME_ORDER}


def analyze_document(pdf_path: Path) -> DocumentResult:
    text = extract_text(pdf_path)
    result = DocumentResult(path=str(pdf_path), filename=pdf_path.name, char_count=len(text))
    if not text.strip():
        result.extraction_ok = False
        result.concept_counts = {c: 0 for c in CONCEPTS}
        result.frame_counts = {f: 0 for f in FRAME_ORDER}
        return result

    concept_counts = count_concepts(text)
    frame_counts = frame_counts_from_concepts(concept_counts)
    total_hits = sum(concept_counts.values())
    breadth = sum(1 for c in concept_counts.values() if c > 0)
    dominant_frame = max(frame_counts, key=frame_counts.get) if total_hits > 0 else None

    result.concept_counts = concept_counts
    result.frame_counts = frame_counts
    result.total_hits = total_hits
    result.breadth = breadth
    result.dominant_frame = dominant_frame
    return result


# ---------------------------------------------------------------------------
# Corpus discovery & exclusion
# ---------------------------------------------------------------------------

def load_exclude_patterns(exclude_list_path: Optional[Path]) -> List[re.Pattern]:
    """Load one regex/substring-per-line exclusion list, used to drop
    unpublished/working documents (e.g. draft papers, fellow PhD students'
    working documents) from the corpus before analysis. Blank lines and
    lines starting with '#' are ignored. Plain substrings are matched
    case-insensitively against the filename; lines are also tried as
    regexes so patterns like `^draft_` work.
    """
    if exclude_list_path is None:
        return []
    patterns: List[re.Pattern] = []
    for raw in exclude_list_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            patterns.append(re.compile(line, re.IGNORECASE))
        except re.error:
            patterns.append(re.compile(re.escape(line), re.IGNORECASE))
    return patterns


def discover_corpus(corpus_dir: Path, exclude_patterns: List[re.Pattern]) -> List[Path]:
    all_pdfs = sorted(corpus_dir.rglob("*.pdf"))
    kept = []
    excluded_count = 0
    for pdf in all_pdfs:
        name = pdf.name
        if any(p.search(name) or p.search(str(pdf)) for p in exclude_patterns):
            excluded_count += 1
            continue
        kept.append(pdf)
    log.info(
        "Discovered %d PDFs, excluded %d (unpublished/working documents), analyzing %d.",
        len(all_pdfs), excluded_count, len(kept),
    )
    return kept


# ---------------------------------------------------------------------------
# Corpus-level aggregation
# ---------------------------------------------------------------------------

def breadth_histogram(results: List[DocumentResult]) -> Dict[str, Dict[str, float]]:
    n = len(results)
    buckets = {str(i): 0 for i in range(6)}
    buckets["6+"] = 0
    for r in results:
        key = str(r.breadth) if r.breadth < 6 else "6+"
        buckets[key] += 1
    return {
        k: {"documents": v, "pct_of_corpus": round(100 * v / n, 2) if n else 0.0}
        for k, v in buckets.items()
    }


def frame_dominance(results: List[DocumentResult]) -> Dict[str, Dict[str, float]]:
    totals = {f: 0 for f in FRAME_ORDER}
    for r in results:
        for f in FRAME_ORDER:
            totals[f] += r.frame_counts.get(f, 0)
    grand_total = sum(totals.values())
    return {
        f: {
            "total_hits": totals[f],
            "pct_of_all_concept_hits": round(100 * totals[f] / grand_total, 2) if grand_total else 0.0,
        }
        for f in FRAME_ORDER
    }


def top_documents(results: List[DocumentResult], n: int) -> List[DocumentResult]:
    return sorted(results, key=lambda r: r.total_hits, reverse=True)[:n]


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_per_document_csv(results: List[DocumentResult], out_path: Path) -> None:
    fieldnames = (
        ["filename", "path", "extraction_ok", "char_count", "total_hits", "breadth", "dominant_frame"]
        + [f"concept__{c}" for c in CONCEPTS]
        + [f"frame_hits__{f}" for f in FRAME_ORDER]
        + [f"frame_pct__{f}" for f in FRAME_ORDER]
    )
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            proportions = r.frame_proportions()
            row = {
                "filename": r.filename,
                "path": r.path,
                "extraction_ok": r.extraction_ok,
                "char_count": r.char_count,
                "total_hits": r.total_hits,
                "breadth": r.breadth,
                "dominant_frame": r.dominant_frame or "",
            }
            row.update({f"concept__{c}": r.concept_counts.get(c, 0) for c in CONCEPTS})
            row.update({f"frame_hits__{f}": r.frame_counts.get(f, 0) for f in FRAME_ORDER})
            row.update({f"frame_pct__{f}": round(100 * proportions[f], 2) for f in FRAME_ORDER})
            writer.writerow(row)


def write_top_documents_csv(top: List[DocumentResult], out_path: Path) -> None:
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["rank", "filename", "total_hits", "breadth", "dominant_frame", "path"])
        for i, r in enumerate(top, start=1):
            writer.writerow([i, r.filename, r.total_hits, r.breadth, r.dominant_frame or "", r.path])


def write_summary_json(
    results: List[DocumentResult],
    breadth_hist: Dict[str, Dict[str, float]],
    dominance: Dict[str, Dict[str, float]],
    out_path: Path,
) -> None:
    n = len(results)
    zero_concept = sum(1 for r in results if r.breadth == 0)
    summary = {
        "n_documents_analyzed": n,
        "n_documents_zero_concepts": zero_concept,
        "pct_documents_zero_concepts": round(100 * zero_concept / n, 2) if n else 0.0,
        "breadth_histogram": breadth_hist,
        "frame_dominance": dominance,
    }
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def plot_frame_dominance(dominance: Dict[str, Dict[str, float]], out_path: Path) -> None:
    """Single-series magnitude comparison across the 4 named frames.
    Fixed hue per frame (reused as the same identity anywhere else the
    frames are plotted), direct value labels on every bar since these are
    the only 4 categories and several fall under the label-contrast
    threshold on a light surface.
    """
    import matplotlib.pyplot as plt

    frame_colors = {
        "colonialism": "#2a78d6",   # categorical slot 1 (blue)
        "imperialism": "#1baf7a",   # categorical slot 2 (aqua)
        "racism": "#eda100",        # categorical slot 3 (yellow)
        "fascism": "#008300",       # categorical slot 4 (green)
    }
    frames = FRAME_ORDER
    values = [dominance[f]["pct_of_all_concept_hits"] for f in frames]
    colors = [frame_colors[f] for f in frames]

    fig, ax = plt.subplots(figsize=(6, 4.5), dpi=150)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")

    bars = ax.bar([f.capitalize() for f in frames], values, color=colors, width=0.6)
    for bar, val in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
            f"{val:.0f}%", ha="center", va="bottom", fontsize=10, color="#0b0b0b",
        )

    ax.set_ylabel("Share of all coded concept hits (%)", color="#52514e")
    ax.set_title("Frame dominance across the corpus", color="#0b0b0b", fontsize=12)
    ax.set_ylim(0, max(values + [10]) * 1.2)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#c3c2b7")
    ax.spines["bottom"].set_color("#c3c2b7")
    ax.tick_params(colors="#52514e")
    ax.yaxis.grid(True, color="#e1e0d9", linewidth=0.8)
    ax.set_axisbelow(True)

    fig.tight_layout()
    fig.savefig(out_path, facecolor=fig.get_facecolor())
    plt.close(fig)


def plot_breadth_histogram(breadth_hist: Dict[str, Dict[str, float]], out_path: Path) -> None:
    import matplotlib.pyplot as plt

    keys = [str(i) for i in range(6)] + ["6+"]
    values = [breadth_hist[k]["pct_of_corpus"] for k in keys]

    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")

    bars = ax.bar(keys, values, color="#2a78d6", width=0.65)
    for bar, val in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
            f"{val:.0f}%", ha="center", va="bottom", fontsize=9, color="#0b0b0b",
        )

    ax.set_xlabel("Number of distinct concepts engaged", color="#52514e")
    ax.set_ylabel("Share of corpus (%)", color="#52514e")
    ax.set_title("Conceptual breadth distribution", color="#0b0b0b", fontsize=12)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#c3c2b7")
    ax.spines["bottom"].set_color("#c3c2b7")
    ax.tick_params(colors="#52514e")
    ax.yaxis.grid(True, color="#e1e0d9", linewidth=0.8)
    ax.set_axisbelow(True)

    fig.tight_layout()
    fig.savefig(out_path, facecolor=fig.get_facecolor())
    plt.close(fig)


# ---------------------------------------------------------------------------
# CLI / pipeline
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus-dir", required=True, type=Path, help="Root directory containing the PDF corpus (searched recursively).")
    parser.add_argument("--exclude-list", type=Path, default=None, help="Text file, one filename/regex per line, of unpublished/working documents to exclude.")
    parser.add_argument("--output-dir", type=Path, default=Path("results"), help="Directory to write CSV/JSON/PNG outputs to.")
    parser.add_argument("--top-n", type=int, default=40, help="Number of highest-hit documents to export for qualitative review.")
    parser.add_argument("--workers", type=int, default=4, help="Number of worker processes for parallel PDF parsing.")
    parser.add_argument("--no-plots", action="store_true", help="Skip matplotlib chart generation (CSV/JSON output only).")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> None:
    args.output_dir.mkdir(parents=True, exist_ok=True)

    exclude_patterns = load_exclude_patterns(args.exclude_list)
    pdf_paths = discover_corpus(args.corpus_dir, exclude_patterns)
    if not pdf_paths:
        log.error("No PDFs found under %s after exclusions.", args.corpus_dir)
        sys.exit(1)

    results: List[DocumentResult] = []
    with futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        for i, result in enumerate(pool.map(analyze_document, pdf_paths), start=1):
            results.append(result)
            if i % 100 == 0 or i == len(pdf_paths):
                log.info("Processed %d/%d documents", i, len(pdf_paths))

    failed = [r for r in results if not r.extraction_ok]
    if failed:
        log.warning("%d documents produced no extractable text (scanned/corrupt); they count as 0 concepts.", len(failed))

    breadth_hist = breadth_histogram(results)
    dominance = frame_dominance(results)
    top = top_documents(results, args.top_n)

    write_per_document_csv(results, args.output_dir / "per_document_results.csv")
    write_top_documents_csv(top, args.output_dir / f"top_{args.top_n}_documents.csv")
    write_summary_json(results, breadth_hist, dominance, args.output_dir / "summary.json")

    if not args.no_plots:
        plot_frame_dominance(dominance, args.output_dir / "frame_dominance.png")
        plot_breadth_histogram(breadth_hist, args.output_dir / "breadth_histogram.png")

    n = len(results)
    zero = breadth_hist["0"]["documents"]
    log.info("Done. %d documents analyzed, %d (%.1f%%) engage none of the core concepts.", n, zero, 100 * zero / n)
    for f in FRAME_ORDER:
        log.info("  %-12s %5.1f%% of all concept hits", f, dominance[f]["pct_of_all_concept_hits"])
    log.info("Outputs written to %s", args.output_dir)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    run(args)


if __name__ == "__main__":
    main()
