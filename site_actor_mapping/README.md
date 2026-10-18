# Website actor mapping

Crawls a website (for example an activist events calendar), reads activist
agendas saved as PDF entry by entry, follows the links of each event to other
websites, and scans every page for a set of key themes. It extracts the names
of collectives, groups, NGOs, networks, foundations, unions, movements and
other organisations, ties them to events, and keeps each mention with its
surrounding text, the themes present in that passage, the country of the event
and a link back to the source, so the list can be assessed qualitatively
rather than just counted.

The end products are two master files (`master_network` and `master_themes`)
and, with `analytics.py`, tables, charts and network files for Gephi.

## Full run checklist (macOS)

For the full run (the Movement Hub calendar plus the two agenda PDFs, with
every event link followed: about 820 events, 400+ linked websites and 6,000 to
10,000 pages). It takes several hours; it can be stopped and resumed at any
time.

1. **Open Terminal** and go to the project folder (the folder that holds this
   README), e.g. `cd ~/new/site_actor_mapping`.
2. **Get the latest version**: `git pull`.
3. **Virtual environment** (once): `python3 -m venv .venv`. Then, in every new
   Terminal window: `source .venv/bin/activate` (the prompt then starts with
   `(.venv)`).
4. **Packages** (once, and again after a `git pull` that changes
   `requirements.txt`): `pip install -r requirements.txt`
5. **spaCy language models** (once): run the download loop under
   [Install](#install), then `python -m spacy validate`. If it lists models
   as incompatible, reinstall them with `python -m spacy download NAME --upgrade`.
6. **Put the two agenda PDFs somewhere easy**, e.g. `~/Documents/agendas/`.
7. **Start the run** (one command; `caffeinate -i` keeps the Mac awake until
   it finishes; replace the PDF file names with yours):

   ```bash
   caffeinate -i python site_actors.py https://www.themovementhub.org/events/ \
     --seed-pdf ~/Documents/agendas/CJA-campaction-dates.pdf \
     --seed-pdf ~/Documents/agendas/Agenda_militant.pdf \
     --themes themes_colonialism_racism_fascism.txt \
     --follow-links events \
     -o ~/Documents/actor_mapping/full_run
   ```

   Write `--seed-pdf` before each PDF. If a file name contains spaces, either
   drag the PDF from Finder into the Terminal window after `--seed-pdf ` (this
   pastes the path correctly escaped) or put the path in quotes with `$HOME`
   instead of `~` (`~` is not expanded inside quotes), e.g.
   `--seed-pdf "$HOME/Desktop/Agenda militant.pdf"`. Use a new, empty output
   folder for the full run. Links from the PDFs are always followed; `--follow-links events`
   adds the links of the calendar's events. Every linked website is crawled
   (`--follow-max-sites` is 0, no limit, by default).
8. **Results** land in the output folder (`~/Documents/actor_mapping/full_run`):
   `master_network.xlsx`, `master_themes.xlsx`, `events.csv`, `report.html`
   and the other files under [Outputs](#outputs). The master files are written
   automatically at the end of the run; `run.log` has the whole log.
9. **If the run is interrupted** (Ctrl+C, a crash, a closed lid, a lost
   connection): activate the environment again (step 3) and run **exactly the
   same command**. Pages already downloaded are reused; only what is missing
   is fetched. See [Interrupting, resuming and re-running](#interrupting-resuming-and-re-running).
10. **Analysis**:

    ```bash
    python analytics.py ~/Documents/actor_mapping/full_run -o ~/Documents/actor_mapping/full_run_analytics
    ```

    See [Analysis tables, charts and network files](#analysis-tables-charts-and-network-files).
11. **To change the inclusion criterion** without crawling again:
    `python make_master.py ~/Documents/actor_mapping/full_run -o ~/Documents/actor_mapping/full_run_none --theme-filter none`
    (see [Theme-hit inclusion criterion](#theme-hit-inclusion-criterion)).

Keep `run_info.json` and `run.log` with the results: they record the command,
the versions and the spaCy models used, which the methods section needs. To
record the exact package versions as well: `pip freeze > requirements.lock`.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
for m in en_core_web_sm fr_core_news_sm de_core_news_sm nl_core_news_sm it_core_news_sm \
         es_core_news_sm pt_core_news_sm ca_core_news_sm da_core_news_sm sv_core_news_sm \
         nb_core_news_sm fi_core_news_sm pl_core_news_sm sl_core_news_sm hr_core_news_sm \
         ro_core_news_sm el_core_news_sm lt_core_news_sm uk_core_news_sm ru_core_news_sm \
         xx_ent_wiki_sm; do python -m spacy download $m; done
```

`networkx` and `matplotlib` are needed only by `analytics.py`.

Languages covered (theme terms, organisation words, language detection, spaCy
NER): English, French, German, Dutch, Italian, Spanish, Portuguese, Catalan,
Danish, Swedish, Norwegian, Finnish, Polish, Slovenian, Croatian/Serbian,
Romanian, Greek, Lithuanian, Ukrainian and Russian. Czech, Slovak, Hungarian
and Basque have theme terms and organisation words but no spaCy pipeline; NER
for them uses the multilingual `xx_ent_wiki_sm`.

Each page's language is read from the page or from the language most of its
paragraphs are written in, and each paragraph with clear evidence of another
language gets that language's model, so a French agenda line is never read
with the English model. A language without an installed model falls back to
`xx_ent_wiki_sm` if installed, else English. `--spacy-model fr_core_news_sm`
forces one model for every page; `--spacy-model none` turns NER off and relies
on the rule-based methods below.

`--ner-size md` (or `lg`) uses the medium (or large) models (`en_core_web_md`,
`fr_core_news_md`, ...; download them first). When a size is not installed the
next smaller one is used (lg, then md, then sm), with one warning per language
in the log and the substitution recorded in `run_info.json` (`ner_fallbacks`).
Tested on two real sites, the medium models removed about two thirds of the
remaining false names (places, people, ordinary words) but also lost some
unusual real group names (e.g. regional "Camp Climat ..." groups), so the
small models stay the default for recall.

A model that is installed but broken (usually built for another spaCy
version) is reported at the end of the run with the advice to run
`python -m spacy validate`; its text goes to the next usable model. At most
six models are kept in memory at once; others are freed and reloaded when
needed.

NER results are saved in `<output-dir>/cache/ner_cache.sqlite`, so re-running
the analysis (for example with `--no-crawl`) does not run spaCy again on
paragraphs it has already seen. The log says how many paragraphs were
analysed and how many came from this cache.

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
| `--max-pages`, `--max-depth` | Crawl limits of the main site (default 500 pages, 5 clicks deep) |
| `--delay` | Seconds between requests to one host (default 1; be polite to small sites) |
| `--include REGEX` / `--exclude REGEX` | Restrict the crawl, e.g. `--include /news/` or `--exclude '/tag/|/shop/'` (repeatable) |
| `--use-sitemap` | Seed from `sitemap.xml`; finds pages not linked from menus |
| `--no-pdfs` | Skip PDFs (by default they are read; see below) |
| `--pdfs-same-site-only` | Only read PDFs hosted on the site itself |
| `--max-pdf-mb` | Skip PDFs larger than this (default 50) |
| `--render-js` | Render pages in headless Chromium for sites that build content with JavaScript (needs `playwright`) |
| `--include-subdomains` | Treat `blog.example.org` etc. as part of the site |
| `--ignore-robots` | Do not honour `robots.txt` (only where you are entitled to) |
| `--spacy-model` | NER model, or `none` |
| `--ner-size` | `sm` (default), `md` or `lg` spaCy models in automatic mode |
| `--theme-scope` | `any` (default), `page`, `paragraph` or `sentence`: keep only mentions whose page / paragraph / context window contains a theme term |
| `--focus-themes` | Comma-separated subset of themes that count for `--theme-scope` and `--theme-filter` |
| `--context-sentences` | Sentences either side of the mention kept as context (default 1) |
| `--strip-regions` | Drop nav, header, footer and sidebars entirely (by default they are kept but flagged) |
| `--no-links` | Do not use outbound-link text and logo alt text as names |
| `--min-mentions` | Drop organisations with fewer distinct mentions |
| `--refresh` | Fetch again the pages already in the cache |
| `--no-crawl` | Only analyse what is already cached (no web access) |
| `--theme-filter` | Inclusion criterion of the master network: `source` (default), `site`, `event` or `none` (see below) |
| `--keep-menu-themes` | Keep theme terms found in menus, sidebars and site furniture in the main `master_themes` sheet |
| `--edition-policy` | `nearest` (default) or `all`: how a linked page reached from several editions of a recurring event is tied to them (see [Master files](#master-files)) |
| `-v` | More detail in the log |

Options for calendars and linked websites are listed under
[Calendars and linked websites](#calendars-and-linked-websites).

## Input files

- **Themes** (`example_themes.txt`): `[theme]` headers followed by one term
  per line. Matching is case-insensitive and whole-word; spaces and hyphens
  are interchangeable; `term*` is a prefix match (`decolon*`); `re:` starts
  a raw regular expression; a line starting with `!` lists a phrase that must
  not count for the theme (e.g. `!anti-authoritarian`). Theme words inside
  web and e-mail addresses and @handles are ignored. The shipped themes are
  only illustrative: replace them with the themes of your study, and add
  translations when the site is multilingual.
- **Colonialism / racism / fascism** (`themes_colonialism_racism_fascism.txt`):
  a ready-made theme file for that standing focus, in about 27 European
  languages.
- **Watchlist** (optional, `example_watchlist.txt`): organisations you
  already know of, as `Canonical Name | alias | alias`. These are always
  caught, merged under one name, and rated high confidence. Matching ignores
  case, accents and umlaut spellings ("Ende Gelaende", "Ende Gëlande") and
  accepts words joined by spaces, hyphens or nothing ("Code-Rood",
  "CodeRood"). Acronyms (short names in capitals, or with a capital inside
  such as "FoE") are matched with their exact case, and a leading `=` on a
  name means the text must write it with a capital (`=Stay Grounded` does not
  match "we must stay grounded"). Matches inside web or e-mail addresses are
  not counted.
- **Field watchlist** (optional, `field_watchlist.txt`): 15 central groups of
  the climate-justice field (Alternatiba, ANV-COP21, Amis de la Terre, Ende
  Gelände, Code Rood, Extinction Rebellion, Soulèvements de la Terre, Reclaim
  the Power, Stay Grounded, Attac, Greenpeace, Fridays for Future, Scientist
  Rebellion, Climate Justice Action, Shell Must Fall) with their aliases. It
  is never loaded unless you pass `--watchlist field_watchlist.txt`. Using it
  adds prior knowledge to the network (these groups are then always found and
  rated high), so report it in the methods section. Local groups stay
  separate: "XR Zagreb" is not merged into Extinction Rebellion although the
  watchlist alias "XR" is inside it; it is a more specific local group.
- **Stoplist** (`default_stoplist.txt`): names to ignore (social media
  platforms, site furniture, bank details). Add anything the first run shows
  to be noise.

## How organisations are found

Each mention records which methods found it (`methods` column, `found_by` in
the master file), which is the main handle for judging precision:

| Method | What it catches | Example |
|---|---|---|
| `watchlist` | Names and aliases you supplied | "MSF" → Médecins Sans Frontières |
| `pattern` | Capitalised names containing an organisational head noun, in English, Italian, Spanish, French, German, Portuguese, Dutch, Danish and Swedish, also at the end of compound words ("Klimakollektivet", "Klimabündnis"); also a movement acronym (XR, XRY, FFF) followed by a place ("XR Zagreb", "FFF Lisbon") or on its own ("XR - climate occupation in Rome", "organised by XR."), but not before another capitalised word ("XR Youth" is judged as a whole name) | "Rete Antirazzista Milano", "Migrant Rights Coalition", "Falkirk council" |
| `pattern_after` | A capitalised name followed by a lower-case organisational noun; weak, so it is checked like NER | "the Abolitionist Futures collective" |
| `appositive` | Names introduced or followed by a descriptor | "the feminist collective **Mujeres Creando**", "**Sea-Watch**, a German NGO", "l'associazione **Naga**" |
| `ner` | spaCy named-entity recognition (`ORG`) | names without a tell-tale noun, e.g. "Abolitionist Futures" |
| `link` | Text or logo alt text of links to other websites | partner logo strips, "supported by" lists |
| `website` | A name that a linked web address spells, found in the text around it | "Code Rood ... code-rood.org" |
| `title` | A group named in an agenda entry's title that spaCy tagged as a person or "other", kept when the same name is in at least two titles or spells the entry's website or link | "Shell must fall", "Stay grounded" |

Acronyms defined in the text, e.g. "Independent Workers Union of Great
Britain (IWGB)", are merged with their long form. Names are normalised for
case, accents, umlaut spellings, hyphens, a leading article or elided article
("d'Attac"), and possessives before merging; spellings that differ only in
spacing ("Geef Tegengas" and "Geef Tegen Gas") are merged too. The most
frequent spelling is shown.

Names found only by NER (or `pattern_after`), by link text, by a web address
or by a descriptor are checked against the whole corpus before they are kept:

- a name made only of words the corpus also writes in lower case ("Climate
  Justice", "Hydrogen", "Approach") is ordinary language, not a name;
- a NER-only name must be capitalised somewhere in running text, not only at
  the start of headings and lines ("Réveil", "Accessible");
- a NER-only name that spaCy labels as a place far more often than as an
  organisation, or that follows "in", "à", "bei" etc. most of the time (a
  town), or that is a place in the gazetteer ("Ulm", "Castres and Toulouse"),
  is dropped;
- a name that a web address spells but that is an event series or made of
  ordinary words ("Camp climat", "Global Day of Action") counts only in the
  paragraph or agenda entry that gives the address;
- a NER-only name in the location part of an agenda entry ("[Altdorf Forest,
  Germany]", "camp, Biesenthal, Germany.") is a place, and a street followed
  by a house number ("Heidenheimerstr. 55") is an address;
- documents ("... Toolkit", "... Policy") and event names are not organisations;
- link text must look like a name: not an instruction ("Go to Instagram
  page", "Je fais un don"), a sentence, or an article title, and not a link
  to social media, chat or form tools, or site infrastructure;
- names are cut at title separators ("INEOS - Press Release" → INEOS).

`confidence` is a rough guide: **high** = watchlist, appositive, or found by
two or more methods (NER and `pattern_after` count as one); **medium** =
pattern, link or title alone, or found on 3+ pages; **low** = NER,
`pattern_after` or a web address alone on fewer than 3 pages. Low-confidence
rows are where to look for false positives (companies, places, people
misread as organisations) and are also the most likely to contain informal
groups no rule anticipated.

### Boilerplate

Paragraphs that repeat on more than 20% of a site's pages (menus, footers,
partner strips) are flagged `boilerplate`. They are kept, since a partner list
in the footer is itself informative, but counted separately:
`substantive_mentions` excludes them, and the report hides organisations that
only appear there by default. Identical passages seen on several pages count
once (`repeat_context`).

## Theme-hit inclusion criterion

A source (one web page, one PDF, or one entry of an agenda PDF) whose content
has no colonialism, racism or fascism term (no theme hit) is left out of the
master network, together with its organisations. Theme terms that appear only
in menus, sidebars, footers, text repeated across a site or short labels do
not count. `master_themes` is never filtered.

`--theme-filter` sets the unit at which a hit is required, on both
`site_actors.py` and `make_master.py`:

| Value | Organisations of a source are kept when ... |
|---|---|
| `source` (default) | the source itself has a theme hit |
| `event` | any source tied to the same event has a theme hit |
| `site` | any page of the same website has a theme hit (an agenda PDF counts as one site, so this keeps every entry of an agenda that has a hit anywhere) |
| `none` | always (no inclusion criterion) |

`--focus-themes` limits which themes count. The log, the `about` sheet of
`master_network.xlsx` and `run_info.json` (`master_files`) say how many rows,
organisations and events were kept out of how many, and the
`excluded_sources` sheet lists every source left out, with its organisations
and the reason. `sources.csv` lists every source with its theme hits
(`has_theme_hit`, `theme_hits`, one `theme:` column per theme).

To compare with and without the criterion, build the master files again from
the same results; nothing is crawled or analysed again:

```bash
python make_master.py results/full_run -o results/full_run_none --theme-filter none
```

## Event countries

Every event gets the country (or countries) where it takes place:
`event_country` (English names), `country_iso` (ISO 3166-1 codes, the United
Kingdom is `GB`; `INT` for international or decentralised events, `ONLINE` for
online events), `country_source` (how it was found) and `location_text` (the
words it rests on). They are in `events.csv` and in both master files.
Several countries are separated by "; ".

The country is looked up by `gazetteer.py`, in this order; the first step that
finds something wins (`country_source`):

| `country_source` | Evidence |
|---|---|
| `location` | the event page's structured location (calendar location box, schema.org address) |
| `tag` | bracketed tags in the event name: "[DE]", "(Bure, France)", French département numbers "(36)" |
| `name` | a country name (any of the project's languages, also "en Allemagne", "in Deutschland") or a nationality adjective in the event name |
| `city` | a city, region or well-known protest site in the event name ("Hambourg", "Lützerath") |
| `online` / `international` | "online", "en ligne", "webinar" / "worldwide", "global day of action", "across Europe" |
| `entry` | the same searches in the full text of the agenda entry |
| `tld` | the country-code domain of the event link (".de"), a last resort |

Places that are the topic rather than the venue ("solidarity with Ukraine",
"pour la Palestine") are left out where the wording shows it. An empty
`country_source` means no country was found. On the two real agendas about
97% of events are located. To check the resolution rate of a run:
`python gazetteer.py --events results/full_run/events.csv`.

## Master files

Every run ends by writing two condensed files (as `.xlsx` and `.csv`; the CSV
holds the first sheet). Both are in long format: one row per event, so a
source tied to two events gives two rows.

**`master_network`**: one row per event × organisation × source. Sheets:

- `event_organisations`: the network. Columns: `event`, `event_date`,
  `event_link`, `event_key` (stable event id), the four country columns,
  `organisation`, `org_key`, `org_type`, `confidence`, `mentions_on_source`,
  `source_link`, `source_unit` (the page, PDF or agenda entry),
  `source_title`, `site`, `site_role`, `layer` (where the source sits relative
  to the event: `listing_seed`, `listing_calendar`, `main_event_page`,
  `main_page`, `landing` (the page the event links to), `linked_page`,
  `linked_pdf`), `is_pdf`, `page_date`, `fetched_at`, `content_vs_event`
  and `years_after_event` (is the page's date before, during or after the
  event), `recurring_event_editions`, `site_linked_from_events`, `found_by`,
  `named_in_event_title` (the name is part of the event's title, often the
  organiser but sometimes just the event name; filter on it as you see fit),
  `only_in_menus_or_footers`, `source_has_theme`, `example_context`.
- `events`: each event of the network once, with all of its organisations.
- `all_events`: every event found (agenda entries, calendar, event pages),
  with how well it was covered (`link_followed`, `link_status`,
  `pages_analysed`, `words_analysed`), whether it is in the network
  (`in_network`) and whether the theme filter left it out.
- `menus_and_footers`: names found only in menus, headers, footers or text
  repeated on every page of a site, kept apart so site furniture does not
  inflate the network.
- `sources`: every source read, with its theme hits.
- `excluded_sources`: sources left out by the theme filter, with their
  organisations and the reason.
- `about`: how the file was made (filter, counts before and after, the run's
  command and versions).

**`master_themes`**: one row per theme term found and event: theme, term,
event, event date, link and key, the country columns, the source link, the
sentence with two sentences before and after, the organisations named in that
paragraph, the layer, whether the same sentence was already found elsewhere
(`duplicate_of`), and `menu_or_sidebar`. Terms found only in menus, sidebars,
footers, site furniture and short labels are in a separate sheet
(`menus_and_sidebars`) unless `--keep-menu-themes` is given. Sheet `about`
as above.

How sources are tied to events: an event page to itself; the page an event
links to, to that event; the other pages of that website to the same event if
only one event links to the site, otherwise to "(other pages of <site>)" with
the linking events listed in `site_linked_from_events`; a PDF to the page it
was found on; calendar cells to the event in the cell; every line of an agenda
PDF to the agenda entry it belongs to. When several editions of a recurring
event link to the same website, a linked page goes to the edition nearest its
page date (`--edition-policy nearest`; the other editions are listed in
`recurring_event_editions`), or to all of them with `--edition-policy all`
(the behaviour of earlier versions). Event dates come from the calendar cells
(start to end), the event page's structured data, or the date written in the
agenda entry. Seed PDFs appear as `pdf:<file name>` in the `site` and
`listed_on` columns.

To merge several runs into one pair of master files, or to rebuild them with
other settings:

```bash
python make_master.py results/run1 results/run2 -o results/master \
  [--min-confidence medium] [--theme-filter source|site|event|none] [--keep-menu-themes]
```

`make_master.py` writes its own `run.log` in the output folder.

## Seed PDFs (agendas and link lists)

`--seed-pdf FILE` reads an agenda or link list saved as PDF (write
`--seed-pdf` before each file). It is split into entries: an entry starts at a
line beginning with a date ("(05-09.07) Code Rood ...", "(24.5.-1.6.) ...",
"– 29 juillet au 1er août : Anti-Colonial Attack! ...", "– 18th / 24th of
august : ...", "25 Jul 2025 - 27 Jul 2025 ...") and runs to the next one, so
wrapped lines and descriptions stay with their entry. Month/year headings
("ANNÉE 2025", "JUNE 2023") supply the year for dates written without one;
entries before any year heading keep their date as written. Every link in an
entry (clickable or written out, re-joined when a line wrap broke it) is
crawled like a linked site, and the entry becomes one event with its name and
start/end dates. The same event listed in both agendas (same link and date)
is one event. Each entry is also analysed as one source, tied to its event.
`seed_links.json` lists every entry and link found, to check the reading. The
option can be combined with a website, or used without one. With `--no-crawl`
a local PDF is read again from disk.

## Calendars and linked websites

Many movement sites embed an events calendar that shows one month and loads
the others only when a visitor clicks "previous" or "next". The crawler
detects **Modern Events Calendar** (the common WordPress plugin, `mec-*`
classes) automatically and loads every month by sending the same request the
calendar's own JavaScript sends on a click: backwards until 12 empty months in
a row, forwards until 3. Each month becomes a page in the results
(`calendar_month` column) and its event links are crawled. A calendar is
loaded once even when it appears on several pages (`/events/page/2/` ...).
Months that fail to load are tried again on the next run in the same folder.

```bash
python site_actors.py https://www.themovementhub.org/events/ \
  --themes themes_colonialism_racism_fascism.txt \
  --follow-links events -o results/movementhub
```

`--follow-links events` then follows each event's outside links: links in the
calendar itself (some events link straight to the organisers' site) and in
the main content of each on-site event page (its "more info" link). Links in
the entries of seed PDFs are always followed, also with `--follow-links
none`. Each linked website is crawled lightly and analysed like the main site.
Every mention records its `site`, whether that is the `main` or a `linked`
site, and the event that led there (`via_event`); `sites.csv` lists every site
with its status, the events, months and pages that linked to it, and the
host it moved to when its start page redirected elsewhere (`moved_to`).

- Social media, video, maps, video-call, chat (Matrix, Signal, Discord ...),
  pads and forms (CryptPad, Framaforms, Riseup pads ...), link shorteners and
  payment links are never followed.
- Platforms hosting many organisations (Action Network, Eventbrite, Linktree,
  Google Docs, Drive, Forms, Sites and Groups, petitions, crowdfunders, blog
  platforms ...) are read one page only: the linked page, never the rest of the
  platform. Add others with `--follow-single-page HOST`, or exclude hosts with
  `--follow-skip HOST`.
- `--follow-links all` follows outside links from the main content of every
  page, not only from events.
- The same outside URL appearing on a large share of pages (a sponsor, a
  standing campaign link) is treated as site furniture and not followed.
- PDFs on a linked site have their own budget and do not count against
  `--follow-max-pages`: up to as many PDFs as pages per site, and up to 3 PDFs
  per linked page on a single-page platform.
- `robots.txt` is honoured. A host whose `robots.txt` cannot be read, or
  forbids access, is treated as disallowed (status `unreachable: robots.txt
  ...` in `sites.csv`) unless `--ignore-robots` is given.
- Several linked websites are crawled at the same time (`--follow-workers`,
  default 8), but each host still gets one request at a time with `--delay`.
- Status values in `sites.csv`: `crawled`, `unreachable: <reason>` (e.g.
  `rate-limited (HTTP 429), retried on the next run`, `blocked (HTTP 403)`,
  `not found (HTTP 404)`, `connection failed`), `failed: <error>`,
  `not crawled (--follow-max-sites)` and `not crawled (stopped)`. After Ctrl+C,
  sites that were being crawled show `interrupted (partly crawled)` and sites not
  yet reached `interrupted (not finished)`; running the same command again
  completes them.

| Option | Purpose |
|---|---|
| `--calendar-from` / `--calendar-to` | Limit the months loaded and analysed, as `YYYY-MM`. Months outside the window that an earlier run left in the same folder are ignored |
| `--calendar-click CSS` | Other calendar plugins: CSS selector of the "previous month" button, clicked in headless Chromium (needs `playwright`) |
| `--calendar-clicks` | How many clicks for `--calendar-click` (default 24) |
| `--calendar-url REGEX` | Only treat matching pages as calendars |
| `--no-calendar` | Do not load other months |
| `--follow-links` | `none` (default), `events` or `all` |
| `--follow-max-sites` | At most this many linked websites, most-linked first; links from seed PDFs count too. Default 0 = no limit. Sites left out are named in the log and listed in `sites.csv` as `not crawled (--follow-max-sites)` |
| `--follow-max-pages` / `--follow-max-depth` | Limits per linked website (default 25 pages, 1 click from the linked page) |
| `--follow-workers` | Linked websites crawled at the same time (default 8; 1 with `--render-js`) |
| `--follow-skip HOST` / `--follow-single-page HOST` | Never follow a host / read only the linked page on a host (repeatable) |

When the calendar's own request fails (the log says so), use the click
fallback, e.g. `--calendar-click ".mec-previous-month"`.

## PDFs

PDFs found on a crawled page are read by default, however they are placed:
ordinary links, embedded viewers (`<iframe>`, `<embed>`, `<object>`),
WordPress PDF plugins that keep the file URL in a `data-` attribute, pdf.js
and Google Docs viewers (`viewer.html?file=...`, `docs.google.com/viewer?url=...`),
Google Drive previews, and Google Docs or Slides shared as documents (fetched
as their PDF export). They are read even when hosted on another domain (Drive,
a CDN, a partner's site) and even at the maximum crawl depth, but nothing
beyond them is crawled. `--include` patterns restrict pages, not PDFs.

Mentions in PDFs carry the page number (`region` = `pdf p.3`) and the web page
the PDF was found on (`embedded_in`). Scanned PDFs without a text layer cannot
be read (no OCR); they are listed in `pdfs.csv` as `no text layer (scanned?)`
so you can see what was missed.

## Outputs

Written to `--output-dir`:

- `master_network.xlsx` / `.csv` and `master_themes.xlsx` / `.csv`: see
  [Master files](#master-files).
- `report.html`: interactive browser: filter organisations by name, theme,
  type, confidence and mention count; click one to read every passage that
  mentions it, with the name and theme terms highlighted and links to the
  source pages.
- `events.csv`: one row per event: `event`, `date`, `event_link`,
  `event_key`, `listed_on` (calendar, agenda PDFs), `calendar_months`, the
  country columns (`event_country`, `country_iso`, `country_source`,
  `location_text`, plus `location_structured` from the event page),
  `entry_words`, `link_followed` and `link_status` (was the event's own link
  read, and if not, why), `pages_analysed`, `words_analysed`,
  `org_mentions`, `sources_with_theme_hit`.
- `mentions.csv`: one row per mention: organisation, surface form, type,
  methods, event (with `event_key`), URL, `source_unit`, page title and date,
  `fetched_at`, page region, boilerplate flag, themes in the context window /
  paragraph / page, the matched theme terms, the sentence, the context window,
  the full paragraph, and other organisations named in the same paragraph.
  This is the file for coding in Excel, NVivo, Atlas.ti etc.
- `organisations.csv`: one row per organisation: type, confidence, mention
  and page counts, methods, spelling variants, acronyms, co-occurring themes
  (overall and one column per theme), linked domains, first URL.
- `theme_hits.csv`: every theme term found, with its passage, event(s),
  source and whether it sits in a menu or sidebar (the source of
  `master_themes`).
- `sources.csv`: one row per source (web page, PDF, agenda entry, calendar
  cell): link, title, site, kind, date, `fetched_at`, word count, events,
  organisation mentions, `has_theme_hit`, theme hits in content and in menus,
  one `theme:` column per theme. This is what the inclusion criterion reads.
- `org_cooccurrence.csv`: organisations named in the same paragraph, as a
  weighted edge list for Gephi or similar.
- `pages.csv`: one row per page: requested URL, title, date, depth, word
  count, events, number of organisation mentions and hits per theme.
- `sites.csv`: the main site and every linked website: status, pages
  analysed, start URLs, and the events, calendar months and pages that linked
  to it.
- `pdfs.csv`: every PDF found: URL, the page it was on, title, date, page and
  word counts, and whether it could be read.
- `summary.json`: theme coverage (pages, share of pages, top matched terms),
  organisation-type and confidence breakdowns, and the run's key settings.
- `run_info.json`: the record of the run: command and all arguments, start
  and end time, duration, status (`running`, `finished`, `interrupted`,
  `failed: ...`), Python, platform and package versions, the theme, watchlist,
  stoplist and seed PDF files with their SHA-256 checksums, the spaCy models
  used (with versions, size fallbacks, missing and broken models), the dates
  between which the pages were fetched, counts, and how many rows the theme
  filter kept. It is written at the start too, so a crashed run still leaves
  a record.
- `run.log`: the full log of every run in this folder (appended, with dates).
- `seed_links.json`, `sites.json`: the agenda entries and links read, and the
  linked websites planned and crawled (used when resuming).
- `cache/`: downloaded pages (`*.json.gz`) and the NER cache.

CSV files are UTF-8 with a BOM, so Excel opens accented names correctly.

## Interrupting, resuming and re-running

- **Stopping**: press Ctrl+C at any time. Every page is saved in `cache/` as
  soon as it is downloaded.
- **Resuming**: run the same command again with the same output folder. Pages
  in the cache are not downloaded again, so the crawl continues where it
  stopped (the run goes quickly through what it already has, then fetches the
  rest). Pages that failed with a temporary error (HTTP 429 or 5xx, timeouts),
  hosts that could not be reached and calendar months that failed to load are
  not cached as done and are tried again. In an offline or blocked setting,
  every unreachable host is tried again on each run, which can take minutes.
- **Re-analysing without crawling**: add `--no-crawl` to analyse exactly what
  is cached, for example with another theme file, watchlist, stoplist or
  `--theme-scope`. No web access is needed; seed PDFs given as local files are
  read again from disk. Crawl options (`--follow-...`) have no effect then.
  If an interrupted crawl left `sites.json` incomplete, the linked websites
  are rebuilt from the cached links (the log says so).
- **Only the master files**: `make_master.py` rebuilds them from the CSV
  results in seconds (for another `--theme-filter` or `--min-confidence`).
- **Fresh data**: `--refresh` downloads every page again. A new output folder
  does the same and keeps the old results for comparison. Use a new folder
  for the full run rather than one made by an older version; calendar months
  that older versions loaded twice (from `/events/page/2/`) are ignored
  automatically, and the log says how many.

## Analysis tables, charts and network files

`analytics.py` reads a finished results folder (or a `make_master.py` output
folder) and never changes it:

```bash
python analytics.py results/full_run -o results/full_run_analytics
python analytics.py results/master -o results/analytics --run results/run1 results/run2
python analytics.py results/full_run -o results/analytics_curated --org-codes org_codes.csv
```

It needs `master_network`, `master_themes` and `events.csv`; `sources.csv`,
`seed_links.json`, `pages.csv` and `sites.csv` are used when present (also
from every `--run` folder, combined), and analyses that need a missing file are skipped
and listed in `summary.json`. The output folder receives:

- `analytics.xlsx` (README and diagnostics sheets first) and the same tables
  as `tables/*.csv`: coverage by stream (agenda, calendar) with confidence
  intervals, missingness, the measurement ladder, concentration of theme hits
  in a few documents, theme prevalence by stream, period, year, country and
  language, per-entry and per-page rates, organisation centrality and
  bridging, organisation by theme, theme co-occurrence, and a sensitivity
  table across filters and scenarios.
- `network/`: Gephi files (`.gexf`, `.graphml`) and node and edge CSVs of the
  event-organisation network and its organisation backbone, for the default
  scenario and scenario A.
- `charts/*.png`: six charts (coverage, measurement ladder, prevalence by
  stream and period, document concentration, backbone, prevalence by country).
- `org_codes_template.xlsx`: the organisation authority list to curate (with
  dropdowns), merge candidates and a stratified precision sample.
- `kwic_for_coding.xlsx`: theme passages in keyword-in-context layout with
  coder columns, a codebook and a stratified coding sample.
- `summary.json`, `summary.md` (a methods paragraph and caveats) and
  `analytics.log`.

Network scenarios: A = all organisations; B = without organisations named in
the event title; C (the default) = organisations at or above
`--min-confidence` (default `medium`); D = C without names in the title;
C_curated = C without the places, site furniture, noise and event names you
coded in `--org-codes`.

Options: `--theme-filter` (as in `make_master.py`, default `source`; the
master files may already be filtered, which the summary reports: to analyse
without the criterion, rebuild them with `make_master.py --theme-filter none`
first), `--min-confidence`, `--org-codes` and `--event-codes` (curation files;
examples and column meanings in `analytics_codes_example/README.txt`),
`--themes-file`, `--backbone-alpha`, `--sample-size`, `--seed`,
`--no-charts`, `--no-graphs`. When the default network has fewer than five
organisations (typical of a run on the agenda PDFs alone, where most names
are low-confidence NER finds), the log, `summary.json` and `summary.md` warn
that its centrality and backbone figures are not meaningful; use scenario A
in the sensitivity table or `--min-confidence low` instead. Curation loop: fill in the `organisations`
sheet of `org_codes_template.xlsx`, save it as `org_codes.csv` and run again
with `--org-codes org_codes.csv`. `python analytics.py --selftest` checks the
installation.

## Limitations

- The `pattern` method relies on capitalisation, so names written entirely in
  lower case are only found via the watchlist, NER or links.
- `org_type` comes from the head noun ("collective", "NGO", "Stiftung"...)
  and is empty when the name does not contain one.
- Sentence splitting is regex-based; abbreviations occasionally split a
  sentence early, which only affects the context window, not detection.
- Event countries rest on the event's name and agenda text; an event whose
  name names neither a country nor a known place gets no country.
- Respect the site's terms of use and `robots.txt`; keep `--delay` at 1s or
  more on small or volunteer-run sites.
