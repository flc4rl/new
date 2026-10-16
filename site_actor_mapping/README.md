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
python -m spacy download en_core_web_sm
python -m spacy download fr_core_news_sm     # plus any other language you will meet:
python -m spacy download de_core_news_sm
python -m spacy download nl_core_news_sm
python -m spacy download da_core_news_sm
python -m spacy download sv_core_news_sm
# it_core_news_sm, es_core_news_sm, pt_core_news_sm, nb_core_news_sm
```

Each page's language is read from the page (or guessed from its words) and
the matching spaCy model is used, so a French page is never read with the
English model. A language without an installed model falls back to
`xx_ent_wiki_sm` if installed, else English. `--spacy-model fr_core_news_sm`
forces one model for every page; `--spacy-model none` turns NER off and relies
on the rule-based methods below.

`--ner-size md` uses the medium models (`en_core_web_md`, `fr_core_news_md`, …;
download them first). Tested on two real sites, they removed about two thirds
of the remaining false names (places, people, ordinary words) but also lost
some unusual real group names (e.g. regional "Camp Climat …" groups), so the
small models stay the default for recall.

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
| `pattern` | Capitalised names containing an organisational head noun, in English, Italian, Spanish, French, German, Portuguese, Dutch, Danish and Swedish, also at the end of compound words ("Klimakollektivet", "Klimabündnis") | "Rete Antirazzista Milano", "Migrant Rights Coalition", "Abolitionist Futures collective" |
| `appositive` | Names introduced or followed by a descriptor | "the feminist collective **Mujeres Creando**", "**Sea-Watch**, a German NGO", "l'associazione **Naga**" |
| `ner` | spaCy named-entity recognition (`ORG`) | names without a tell-tale noun, e.g. "Abolitionist Futures" |
| `link` | Text or logo alt text of links to other websites | partner logo strips, "supported by" lists |

Acronyms defined in the text, e.g. "Independent Workers Union of Great
Britain (IWGB)", are merged with their long form. Names are normalised for
case, accents, a leading article and possessives before merging.

Names found only by NER, by link text or by a descriptor are checked against
the whole site before they are kept:

- a name made only of words the site also writes in lower case ("Climate
  Justice", "Hydrogen", "Approach") is ordinary language, not a name;
- a NER-only name must be capitalised somewhere in running text, not only at
  the start of headings and lines ("Réveil", "Accessible");
- a NER-only name that spaCy labels as a place far more often than as an
  organisation (a town) is dropped;
- link text must look like a name: not an instruction ("Go to Instagram
  page", "Je fais un don"), a sentence, or an article title, and not a link
  to social media or site infrastructure;
- names are cut at title separators ("INEOS - Press Release" → INEOS).

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

## Master files

Every run ends by writing two condensed files (as `.xlsx` and `.csv`):

- **`master_network`** — one row per event × organisation × source page:
  event name, event date, event link, organisation, type, confidence, how many
  times it was mentioned on that source, the source link, and an example
  passage. `named_in_event_title` marks names that are (part of) the event's
  own title, often the organiser ("Ende Gelände action") but sometimes just
  the event name; filter on it as you see fit. A second sheet (`events`, xlsx
  only) lists each event once with all of its organisations. A third
  (`menus_and_footers`) holds names found only in menus, headers, footers or
  text repeated on every page of a site, kept apart so site furniture does not
  inflate the network.
- **`master_themes`** — one row per theme term found: theme, term, event,
  event date, event link, source link, the sentence, two sentences before and
  after, the organisations named in that paragraph, and whether the same
  sentence was already found elsewhere (`duplicate_of`).

How pages are tied to events: an event page to itself; the page an event links
to, to that event; the other pages of that website to the same event if only
one event links to the site, otherwise to "(other pages of <site>)" with the
linking events listed in `site_linked_from_events`; a PDF to the page it was
found on; calendar cells to the event in the cell; every line of a seed PDF to
the agenda entry it belongs to. Theme words inside web addresses are ignored. Event dates come from the calendar
cells (start to end), the event page's structured data, or the date written
next to a link in a seed PDF.

To merge several runs into one pair of master files:

```bash
python make_master.py results/run1 results/run2 -o results/master [--min-confidence medium]
```

## Seed PDFs (link lists)

`--seed-pdf FILE` reads an agenda or link list saved as PDF. It is split into
entries: an entry starts at a line beginning with a date — "(05-09.07) Code
Rood …", "(24.5.-1.6.) …", "– 29 juillet au 1er août : Anti-Colonial Attack! …",
"– 18th / 24th of august : …", "25 Jul 2025 - 27 Jul 2025 …" — and runs to the
next one, so wrapped lines and descriptions stay with their entry. Month/year
headings ("ANNÉE 2025", "JUNE 2023") supply the year for dates written without
one; entries before any year heading keep their date as written. Every link in
an entry (clickable or written out, re-joined when a line wrap broke it) is
crawled like a linked site, and the entry becomes one event with its name and
start/end dates. The PDF's own text is analysed too, line by line, each line
tied to its entry. `seed_links.json` lists every entry and link found, to check
the reading. Repeat the option for several PDFs; it can be combined with a
website, or used without one.

## Calendars and linked websites

Many movement sites embed an events calendar that shows one month and loads
the others only when a visitor clicks "previous" or "next". The crawler
detects **Modern Events Calendar** (the common WordPress plugin, `mec-*`
classes) automatically and loads every month by sending the same request the
calendar's own JavaScript sends on a click: backwards until 12 empty months in
a row, forwards until 3. Each month becomes a page in the results
(`calendar_month` column) and its event links are crawled.

```bash
python site_actors.py https://www.themovementhub.org/events/ \
  --themes themes_colonialism_racism_fascism.txt \
  --follow-links events -o results/movementhub
```

`--follow-links events` then follows each event's outside links: links in the
calendar itself (some events link straight to the organisers' site) and in
the main content of each on-site event page (its "more info" link). Each
linked website is crawled lightly and analysed like the main site. Every
mention records its `site`, whether that is the `main` or a `linked` site,
and the event that led there (`via_event`); `sites.csv` lists every site with
the events, months and pages that linked to it.

- Social media, video, maps, video-call and payment links are never followed.
- Platforms hosting many organisations (Action Network, Eventbrite, Linktree,
  Google Forms, petitions, crowdfunders, blog platforms …) are read one page
  only: the linked event page, never the rest of the platform. Add others
  with `--follow-single-page HOST`, or exclude hosts with `--follow-skip HOST`.
- `--follow-links all` follows outside links from the main content of every
  page, not only from events.
- The same outside URL appearing on a large share of pages (a sponsor, a
  standing campaign link) is treated as site furniture and not followed.

| Option | Purpose |
|---|---|
| `--calendar-from` / `--calendar-to` | Limit the months loaded, as `YYYY-MM` |
| `--calendar-click CSS` | Other calendar plugins: CSS selector of the "previous month" button, clicked in headless Chromium (needs `playwright`) |
| `--calendar-clicks` | How many clicks for `--calendar-click` (default 24) |
| `--calendar-url REGEX` | Only treat matching pages as calendars |
| `--no-calendar` | Do not load other months |
| `--follow-links` | `none` (default), `events` or `all` |
| `--follow-max-sites` | At most this many linked websites (default 60, most-linked first) |
| `--follow-max-pages` / `--follow-max-depth` | Limits per linked website (default 25 pages, 1 click from the linked page) |

When the calendar's own request fails (the log says so), use the click
fallback, e.g. `--calendar-click ".mec-previous-month"`.

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
- `sites.csv` — the main site and every linked website: status, pages
  analysed, start URLs, and the events, calendar months and pages that linked
  to it.
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
