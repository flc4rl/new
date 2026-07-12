# Departmental corpus frame analysis

A reconstruction of the PDF text-mining pipeline described in the study:
scans a corpus of course-literature PDFs for a taxonomy of critical-theory
concepts (colonialism, imperialism, racism, fascism), and reduces it to the
same three outputs used in the write-up:

1. **Conceptual breadth per document** — how many *distinct* concepts a
   document engages, counted once each regardless of frequency (the
   0/1/2/3/4/5/6+ histogram).
2. **Frame dominance** — corpus-wide and per-document, how total concept
   hits are distributed across the four broader frames (the "colonialism
   ~45%, imperialism ~29%, racism ~19%, fascism ~7%" bar chart).
3. **A ranked shortlist** of the highest-hit documents, for the
   qualitative close-reading pass (default top 40).

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
python analyze_corpus.py \
  --corpus-dir /path/to/departmental_pdfs \
  --exclude-list example_exclude_list.txt \
  --output-dir results \
  --top-n 40
```

`--exclude-list` is optional; see `example_exclude_list.txt` for the format
(one filename substring or regex per line). Use it to drop unpublished
material, such as fellow PhD students' working documents, before analysis.

## Outputs (written to `--output-dir`)

- `per_document_results.csv` — one row per PDF: raw counts per concept,
  per-frame hit totals, per-frame percentage of that document's hits,
  total hits, breadth, and the dominant frame.
- `top_N_documents.csv` — the N highest-total-hit documents, ranked, for
  qualitative review.
- `summary.json` — corpus-level breadth histogram and frame dominance
  percentages.
- `frame_dominance.png` / `breadth_histogram.png` — the two summary
  charts, skip with `--no-plots`.

## How concepts are counted

`taxonomy.py` groups keyword surface forms into concepts, and concepts
into four frames — e.g. `colonialism`, `coloniality`, `colonization`, and
`settler colonialism` all roll up into the **colonialism** frame; `racism`,
`racialization`, and `white supremacy` roll up into **racism**. Matching
uses whole-word, case-insensitive regex, and PDF line-break hyphenation
(`"coloni-\nalism"`) is repaired before matching.

Only substantive/analytic word forms are included (nouns/verbs signalling
theoretical use — "colonialism", "racialization") rather than generic
descriptive adjectives ("colonial", "imperial") that mostly show up in
non-theoretical, incidental usage ("colonial architecture", "the imperial
court"). A small `EXCLUDE_CONTEXT` phrase list in `taxonomy.py` further
discounts hits that fall inside a handful of known descriptive phrases
(e.g. "colonial period", "Imperial College"). Edit `taxonomy.py` to extend
the concept list, add languages, or tune the exclusion phrases for your
own corpus.

## Notes on scale

For a corpus in the range described in the study (~1,500 PDFs), text
extraction dominates runtime; `--workers` controls how many PDFs are
parsed in parallel (`ProcessPoolExecutor`). Extraction tries PyMuPDF first
and falls back to `pdfplumber` on failure; PDFs that yield no extractable
text (e.g. pure image scans with no OCR layer) are recorded with
`extraction_ok = False` and contribute 0 hits rather than crashing the run.
