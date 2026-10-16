# Website actor mapping

Crawls a website, scans every page for a set of key themes, and extracts the
names of all collectives, groups, NGOs, networks, foundations, unions,
movements and other organisations it mentions. Each mention is kept with its
surrounding text, the themes present in that passage, and a link back to the
source page, so the list can be assessed qualitatively rather than just
counted.

## Install

```bash
pip install -r requirements.txt
python -m spacy download en_core_web_sm      # or a model for the site's language
```

For non-English sites, use the matching spaCy model, e.g. `it_core_news_sm`,
`es_core_news_sm`, `fr_core_news_sm`, `de_core_news_sm`, or the multilingual
`xx_ent_wiki_sm`, and pass it with `--spacy-model`. The tool still works
without spaCy (`--spacy-model none`), relying on the rule-based methods below.

## Run

```bash
python site_actors.py https://example.org \
  --themes example_themes.txt \
  --watchlist example_watchlist.txt \
  --output-dir results/example_org \
  --max-pages 300 --delay 1
```

Then open `results/example_org/report.html` in a browser. `--output-dir` is
resolved against the folder you run the command from, so running it inside
`site_actor_mapping/` puts results in `site_actor_mapping/results/` (ignored
by git, so cached pages are never committed by accident). An absolute path
such as `~/Documents/climate_camp` works too.

Every fetched page is cached in `<output-dir>/cache/`, so an interrupted
crawl resumes where it stopped, and you can re-run the analysis with revised
themes, watchlist or stoplist without crawling again:

```bash
python site_actors.py https://example.org -o results/example_org --no-crawl \
  --themes my_themes.txt --theme-scope paragraph
```

### Useful options

| Option | Purpose |
|---|---|
| `--max-pages`, `--max-depth` | Crawl limits (default 500 pages, 5 clicks deep) |
| `--delay` | Seconds between requests (default 1; be polite to small sites) |
| `--include REGEX` / `--exclude REGEX` | Restrict the crawl, e.g. `--include /news/` or `--exclude '/tag/|/shop/'` (repeatable) |
| `--use-sitemap` | Seed from `sitemap.xml`; finds pages not linked from menus |
| `--no-pdfs` | Skip PDFs (by default they are read; see below) |
| `--pdfs-same-site-only` | Only read PDFs hosted on the site itself |
| `--max-pdf-mb` | Skip PDFs larger than this (default 50) |
| `--render-js` | Render pages in headless Chromium for sites that build content with JavaScript (needs `playwright`) |
| `--include-subdomains` | Treat `blog.example.org` etc. as part of the site |
| `--ignore-robots` | Do not honour `robots.txt` (only where you are entitled to) |
| `--spacy-model` | NER model, or `none` |
| `--theme-scope` | `any` (default), `page`, `paragraph` or `sentence`: keep only mentions whose page / paragraph / context window contains a theme term |
| `--focus-themes` | Comma-separated subset of themes that count for `--theme-scope` |
| `--context-sentences` | Sentences either side of the mention kept as context (default 1) |
| `--strip-regions` | Drop nav, header, footer and sidebars entirely (by default they are kept but flagged) |
| `--no-links` | Do not use outbound-link text and logo alt text as names |
| `--min-mentions` | Drop organisations with fewer distinct mentions |
| `--refresh` | Refetch pages already in the cache |

## Input files

- **Themes** (`example_themes.txt`): `[theme]` headers followed by one term
  per line. Matching is case-insensitive and whole-word; spaces and hyphens
  are interchangeable; `term*` is a prefix match (`decolon*`); `re:` starts
  a raw regular expression. The shipped themes are only illustrative:
  replace them with the themes of your study, and add translations when the
  site is multilingual.
- **Colonialism / racism / fascism** (`themes_colonialism_racism_fascism.txt`):
  a ready-made theme file for that standing focus.
- **Watchlist** (optional, `example_watchlist.txt`): organisations you
  already know of, as `Canonical Name | alias | alias`. These are always
  caught, merged under one name, and rated high confidence.
- **Stoplist** (`default_stoplist.txt`): names to ignore (social media
  platforms, site furniture). Add anything the first run shows to be noise.

## How organisations are found

Each mention records which methods found it (`methods` column), which is the
main handle for judging precision:

| Method | What it catches | Example |
|---|---|---|
| `watchlist` | Names and aliases you supplied | "MSF" → Médecins Sans Frontières |
| `pattern` | Capitalised names containing an organisational head noun, in English, Italian, Spanish, French, German and Portuguese | "Rete Antirazzista Milano", "Migrant Rights Coalition", "Abolitionist Futures collective" |
| `appositive` | Names introduced or followed by a descriptor | "the feminist collective **Mujeres Creando**", "**Sea-Watch**, a German NGO", "l'associazione **Naga**" |
| `ner` | spaCy named-entity recognition (`ORG`) | names without a tell-tale noun, e.g. "Abolitionist Futures" |
| `link` | Text or logo alt text of links to other websites | partner logo strips, "supported by" lists |

Acronyms defined in the text, e.g. "Independent Workers Union of Great
Britain (IWGB)", are merged with their long form. Names are normalised for
case, accents, a leading article and possessives before merging.

`confidence` is a rough guide: **high** = watchlist, appositive, or found by
two or more methods; **medium** = pattern or link alone, or NER on 3+ pages;
**low** = a single NER hit. Low-confidence rows are where to look for false
positives (companies, places, people misread as organisations) and are also
the most likely to contain informal groups no rule anticipated.

### Boilerplate

Paragraphs that repeat on more than 20% of pages (menus, footers, partner
strips) are flagged `boilerplate`. They are kept, since a partner list in
the footer is itself informative, but counted separately: `substantive_mentions`
excludes them, and the report hides organisations that only appear there by
default. Identical passages seen on several pages count once
(`repeat_context`).

## PDFs

PDFs found on a crawled page are read by default, however they are placed:
ordinary links, embedded viewers (`<iframe>`, `<embed>`, `<object>`),
WordPress PDF plugins that keep the file URL in a `data-` attribute, pdf.js
and Google Docs viewers (`viewer.html?file=…`, `docs.google.com/viewer?url=…`),
Google Drive previews, and Google Docs or Slides shared as documents (fetched
as their PDF export). They are read even when hosted on another domain (Drive,
a CDN, a partner's site) and even at the maximum crawl depth, but nothing
beyond them is crawled. `--include` patterns restrict pages, not PDFs.

Mentions in PDFs carry the page number (`region` = `pdf p.3`) and the web page
the PDF was found on (`embedded_in`). Scanned PDFs without a text layer cannot
be read (no OCR); they are listed in `pdfs.csv` as `no text layer (scanned?)`
so you can see what was missed.

## Outputs (written to `--output-dir`)

- `report.html` — interactive browser: filter organisations by name, theme,
  type, confidence and mention count; click one to read every passage that
  mentions it, with the name and theme terms highlighted and links to the
  source pages.
- `mentions.csv` — one row per mention: organisation, surface form, type,
  methods, URL, page title and date, page region, boilerplate flag, themes in
  the context window / paragraph / page, the matched theme terms, the
  sentence, the context window, the full paragraph, and other organisations
  named in the same paragraph. This is the file for coding in Excel, NVivo,
  Atlas.ti etc.
- `organisations.csv` — one row per organisation: type, confidence, mention
  and page counts, methods, spelling variants, acronyms, co-occurring themes
  (overall and one column per theme), linked domains, first URL.
- `org_cooccurrence.csv` — organisations named in the same paragraph, as a
  weighted edge list for Gephi or similar.
- `pages.csv` — one row per page: title, date, depth, word count, number of
  organisation mentions and hits per theme.
- `pdfs.csv` — every PDF found: URL, the page it was on, title, date, page
  and word counts, and whether it could be read.
- `summary.json` — theme coverage (pages, share of pages, top matched terms),
  organisation-type and confidence breakdowns.

CSV files are UTF-8 with a BOM, so Excel opens accented names correctly.

## Limitations

- The `pattern` method relies on capitalisation, so names written entirely in
  lower case are only found via the watchlist, NER or links.
- `org_type` comes from the head noun ("collective", "NGO", "Stiftung"...)
  and is empty when the name does not contain one.
- Sentence splitting is regex-based; abbreviations occasionally split a
  sentence early, which only affects the context window, not detection.
- Respect the site's terms of use and `robots.txt`; keep `--delay` at 1s or
  more on small or volunteer-run sites.
