"""
Page parsing, theme matching and organisation-name extraction.

Organisation names are found by five complementary methods, each recorded on
every mention so precision can be judged afterwards:

  watchlist   names/aliases you supply (highest precision)
  pattern     capitalised name containing an organisational head noun,
              e.g. "Mujeres Creando Collective", "Rete Antirazzista"
  appositive  a name introduced by a descriptor, e.g.
              "the feminist collective Mujeres Creando",
              "Sea-Watch, a German NGO"
  ner         spaCy named-entity recogniser, label ORG (optional)
  link        anchor text / logo alt text of links to *other* websites
              (partner lists, "supported by" logos)

Acronyms defined in the text ("Black Lives Matter (BLM)") are merged with
their long form.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Organisational vocabulary (EN / IT / ES / FR / DE / PT). Extend freely.
# --------------------------------------------------------------------------
ORG_HEADS: Dict[str, List[str]] = {
    "collective": [
        "collective", "collectives", "kollektiv", "kollektive", "collettivo", "collettiva",
        "collettivi", "colectivo", "colectiva", "colectivos", "collectif", "collectifs",
        "coletivo", "coletiva", "crew", "squat", "commune",
    ],
    "ngo / charity": [
        "ngo", "ngos", "ong", "onlus", "charity", "nonprofit", "non-profit", "aps", "odv",
    ],
    "network / coalition": [
        "network", "networks", "rete", "reti", "red", "réseau", "netzwerk", "rede",
        "coalition", "coalizione", "coalición", "coalicion", "coligação", "alliance",
        "alleanza", "alianza", "aliança", "bündnis", "platform", "piattaforma",
        "plataforma", "plateforme", "plattform", "federation", "federazione",
        "federación", "fédération", "föderation", "confederation", "confederazione",
        "forum", "foro", "consortium", "consorzio", "consorcio",
    ],
    "association / union": [
        "association", "associations", "associazione", "associazioni", "asociación",
        "asociacion", "associação", "verein", "verband", "society", "union", "unione",
        "unión", "sindacato", "sindicato", "syndicat", "gewerkschaft", "guild", "league",
        "lega", "liga", "ligue", "club", "circle", "circolo", "círculo", "cercle",
        "chapter", "fellowship", "brotherhood", "sisterhood", "solidarity",
    ],
    "foundation / trust": [
        "foundation", "fondazione", "fundación", "fundacion", "fondation", "stiftung",
        "fundação", "trust", "fund", "fondo", "fonds", "endowment",
    ],
    "movement / campaign": [
        "movement", "movements", "movimento", "movimiento", "mouvement", "bewegung",
        "campaign", "campagna", "campaña", "campagne", "kampagne", "initiative",
        "iniziativa", "iniciativa", "initiativ", "front", "fronte", "frente",
        "brigade", "brigata", "brigada", "rebellion", "resistance", "resistenza",
        "resistencia", "résistance", "uprising", "insurgency",
    ],
    "committee / council / assembly": [
        "committee", "comitato", "comité", "comite", "komitee", "council", "consiglio",
        "consejo", "conseil", "assembly", "assemblea", "asamblea", "assemblée",
        "commission", "commissione", "comisión", "board", "caucus", "tribunal",
    ],
    "research / education / culture": [
        "institute", "istituto", "instituto", "institut", "centre", "center", "centro",
        "centri", "observatory", "osservatorio", "observatorio", "observatoire", "lab",
        "laboratory", "laboratorio", "academy", "accademia", "academia", "académie",
        "university", "università", "universidad", "université", "universität",
        "school", "scuola", "escuela", "école", "college", "museum", "museo", "musée",
        "library", "biblioteca", "archive", "archives", "archivio", "archivo",
        "think-tank", "press", "radio",
    ],
    "cooperative": ["cooperative", "co-op", "coop", "cooperativa", "coopérative", "genossenschaft"],
    "party": ["party", "partito", "partido", "parti", "partei"],
    "organisation (generic)": [
        "organisation", "organization", "organisations", "organizations",
        "organizzazione", "organización", "organisationen", "group", "groups",
        "gruppo", "gruppi", "grupo", "grupos", "groupe", "gruppe", "project", "progetto",
        "proyecto", "projet", "projekt", "agency", "agenzia", "agencia", "agence",
        "office", "secretariat", "mission", "service", "services", "space", "spazio",
        "espacio", "espace",
    ],
}
# Heads that only signal an organisation as the *last* word of a name
# ("Amnesty International", "Human Rights Watch", "Médecins Sans Frontières").
TRAILING_HEADS: Dict[str, List[str]] = {
    "ngo / charity": ["international", "watch", "aid", "relief", "frontières", "now", "matter", "matters"],
}
HEAD_TO_TYPE: Dict[str, str] = {}
for _typ, _words in ORG_HEADS.items():
    for _w in _words:
        HEAD_TO_TYPE.setdefault(_w, _typ)
TRAILING_TO_TYPE = {w: t for t, ws in TRAILING_HEADS.items() for w in ws}

# Lowercase descriptors that introduce a name: "the collective X", "X, an NGO".
DESCRIPTORS = sorted(
    {w for w in HEAD_TO_TYPE if w not in {"press", "radio", "space", "spazio", "espacio",
                                           "espace", "service", "services", "office",
                                           "board", "fund", "school", "front", "red", "lab"}}
    | {"grassroots", "activist group", "civil society organisation",
       "civil society organization", "advocacy group", "pressure group",
       "community group", "human rights group", "think tank", "trade union",
       "non-governmental organisation", "non-governmental organization"},
    key=len, reverse=True,
)

CONNECTORS = {
    "of", "for", "the", "and", "&", "on", "in", "against", "to", "at", "with", "without",
    "de", "di", "del", "della", "dei", "degli", "delle", "da", "la", "le", "les", "des",
    "du", "el", "los", "las", "por", "para", "per", "e", "y", "et", "und", "für", "der",
    "die", "das", "dos", "das", "do", "contra", "contre", "gegen", "sans", "sin", "senza",
    "pour", "al", "alla", "allo", "ai", "agli", "nel", "nella", "sul", "sulla", "a", "à",
    "i", "il", "lo", "gli", "l", "d", "von", "van", "zu", "zum", "zur", "em", "no", "na",
}
CONJUNCTIONS = {"and", "&", "e", "y", "et", "und"}
LEADING_STRIP = {
    "the", "a", "an", "our", "their", "this", "these", "that", "those", "its", "his", "her",
    "my", "your", "all", "every", "with", "and", "in", "on", "at", "by", "from", "for", "of",
    "to", "as", "about", "join", "support", "donate", "contact", "read", "more", "see",
    "home", "menu", "follow", "visit", "dear", "both", "also", "when", "while", "after",
    "before", "during", "since", "today", "yesterday", "il", "lo", "la", "le", "gli", "i",
    "l", "un", "una", "uno", "el", "los", "las", "les", "des", "der", "die", "das", "dem",
    "den", "ein", "eine", "une", "o", "os", "as", "nostra", "nostro", "nuestra", "nuestro",
    "notre", "unsere", "unser", "mit", "con", "avec", "e", "y", "et", "und", "but", "if",
    "how", "why", "what", "who", "where", "new", "latest", "news", "events", "event",
    "statement", "press", "photo", "video", "via", "per", "por", "para", "pour", "für",
    "together", "insieme", "including", "like", "thanks", "grazie", "according", "secondo",
    "members", "member", "activists", "join", "unisciti", "partecipa", "contro", "against",
}
# A name made only of heads + these words is too generic to keep.
GENERIC = {
    "partner", "partners", "member", "members", "main", "other", "related", "local",
    "national", "international", "regional", "global", "european", "our", "contact",
    "about", "home", "menu", "more", "new", "all", "general", "annual", "public",
    "private", "student", "students", "working", "social", "community", "communities",
    "civil", "human", "rights", "women", "youth", "research", "affiliated",
}
MONTHS = {
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
}
LINK_TEXT_JUNK = re.compile(
    r"^(here|click|read|more|link|website|site|homepage|home|this|download|pdf|"
    r"facebook|twitter|x|instagram|youtube|linkedin|tiktok|telegram|mastodon|bluesky|"
    r"share|tweet|email|e-mail|contact|donate|subscribe|login|log in|sign up|next|"
    r"previous|back|top|menu|search|privacy|cookie|cookies|imprint|impressum|"
    r"source|via|en|it|es|fr|de|pt)\b",
    re.I,
)

BLOCK_TAGS = {
    "p", "div", "li", "ul", "ol", "dl", "dd", "dt", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "main", "header", "footer", "nav", "aside", "blockquote",
    "figure", "figcaption", "table", "thead", "tbody", "tr", "td", "th", "form",
    "fieldset", "address", "details", "summary", "pre", "hr", "br", "caption",
}
DROP_TAGS = ["script", "style", "noscript", "svg", "template", "iframe", "canvas", "object"]
REGION_TAGS = ("nav", "header", "footer", "aside")

INDEFINITE_BEFORE = re.compile(r"(?<!\w)(?:an?|un[oa]?|une|ein(?:e|en)?|um[a]?|another|any|every|each)\s+$", re.I)
# Lowercase words too common after a place/name to signal an organisation by themselves.
WEAK_TRAILING = {"group", "groups", "project", "space", "service", "services", "office", "press",
                 "radio", "board", "fund", "school", "front", "red", "lab", "center", "centre",
                 "union", "party", "club", "mission", "agency", "platform", "forum", "league"}
TOKEN_RE = re.compile(r"[^\W_](?:[\w’'&\-]|\.(?=\w))*|&", re.UNICODE)
SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])[\"”»’)]?\s+(?=[\"“«‘(\[]?[^\W\d_]|\d)")
ACRONYM_DEF_RE = re.compile(r"([^\W\d_][\w’'&.\- ]{3,120}?)\s*\(\s*([A-Z][A-Za-z0-9&.\-]{1,11})\s*\)")


# --------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------
@dataclass
class Link:
    text: str
    href: str
    domain: str


@dataclass
class Block:
    text: str
    region: str               # main / nav / header / footer / aside
    links: List[Link] = field(default_factory=list)


@dataclass
class ParsedPage:
    url: str
    title: str
    date: str
    depth: int
    blocks: List[Block]
    is_pdf: bool = False


@dataclass
class Mention:
    surface: str
    key: str
    methods: Set[str]
    org_type: str
    start: int
    end: int
    link_domain: str = ""


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------
def clean_ws(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("­", "").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str) -> List[Tuple[int, int]]:
    """Character spans of sentences (regex splitter; good enough for context windows)."""
    spans, start = [], 0
    for m in SENT_SPLIT_RE.finditer(text):
        spans.append((start, m.start() + len(m.group(0).rstrip())))
        start = m.end()
    spans.append((start, len(text)))
    return [(s, e) for s, e in spans if e > s]


def canonical_key(name: str) -> str:
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c)).casefold()
    n = n.replace("&", " and ").replace("’", "'")
    n = re.sub(r"'s\b", "", n)
    n = re.sub(r"[^\w\s-]", " ", n)
    toks = n.split()
    while toks and toks[0] in {"the", "il", "lo", "la", "le", "gli", "l", "el", "los", "las",
                               "les", "der", "die", "das", "o", "a", "os", "as"}:
        toks = toks[1:]
    return " ".join(toks)


def tidy_surface(name: str) -> str:
    name = clean_ws(name).strip(" \t\"'“”‘’«»()[]{}:;,.-–—|/")
    name = re.sub(r"^(?:the|The|THE)\s+", "", name)
    name = re.sub(r"[’']s$", "", name)
    return name


# --------------------------------------------------------------------------
# Themes
# --------------------------------------------------------------------------
class ThemeSet:
    """Themes loaded from a plain-text file (see example_themes.txt)."""

    def __init__(self, themes: Dict[str, List[re.Pattern]], raw: Dict[str, List[str]]):
        self.themes = themes
        self.raw = raw

    @property
    def names(self) -> List[str]:
        return list(self.themes)

    @classmethod
    def load(cls, path: Optional[Path]) -> "ThemeSet":
        themes: Dict[str, List[re.Pattern]] = {}
        raw: Dict[str, List[str]] = {}
        if path is None:
            return cls(themes, raw)
        current = None
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.fullmatch(r"\[(.+)\]", line)
            if m:
                current = m.group(1).strip()
                themes.setdefault(current, [])
                raw.setdefault(current, [])
                continue
            if current is None:
                raise ValueError(f"{path}:{lineno}: term before any [theme] header")
            themes[current].append(cls.compile_term(line))
            raw[current].append(line)
        return cls(themes, raw)

    @staticmethod
    def compile_term(term: str) -> re.Pattern:
        if term.startswith("re:"):
            return re.compile(term[3:], re.I)
        term = term.strip('"')
        prefix = term.endswith("*")
        body = term.rstrip("*")
        parts = [re.escape(p) for p in re.split(r"[\s-]+", body)]
        pat = r"[\s\-]+".join(parts)
        return re.compile(r"(?<!\w)" + pat + (r"\w*" if prefix else r"(?!\w)"), re.I)

    def find(self, text: str) -> Dict[str, List[Tuple[int, int, str]]]:
        hits: Dict[str, List[Tuple[int, int, str]]] = {}
        for theme, pats in self.themes.items():
            found = []
            for p in pats:
                found.extend((m.start(), m.end(), m.group(0)) for m in p.finditer(text))
            # Overlapping terms ("green colonialism" / "colonialism") count once,
            # as the longest match.
            kept: List[Tuple[int, int, str]] = []
            for s, e, t in sorted(found, key=lambda h: (h[0], -(h[1] - h[0]))):
                if not kept or s >= kept[-1][1]:
                    kept.append((s, e, t))
            if kept:
                hits[theme] = kept
        return hits


# --------------------------------------------------------------------------
# HTML -> blocks
# --------------------------------------------------------------------------
def parse_html(html: str, url: str, depth: int, strip_regions: bool) -> ParsedPage:
    soup = BeautifulSoup(html, "lxml")
    title = page_title(soup)
    date = page_date(soup)
    for t in soup(DROP_TAGS):
        t.decompose()
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    if strip_regions:
        for t in soup(list(REGION_TAGS)):
            t.decompose()
        for t in soup.select('[role="navigation"], [role="banner"], [role="contentinfo"], '
                             '.cookie, #cookie, .cookies, .cookie-banner, #cookie-notice'):
            t.decompose()
    base = soup.find("base", href=True)
    base_url = urljoin(url, base["href"]) if base else url
    own_host = urlparse(url).netloc.lower().removeprefix("www.")
    root = soup.body or soup
    blocks: List[Block] = []
    _walk(root, blocks, "main", base_url, own_host)
    return ParsedPage(url=url, title=title, date=date, depth=depth,
                      blocks=[b for b in blocks if b.text or b.links])


def _region_of(tag: Tag, inherited: str) -> str:
    if tag.name in REGION_TAGS:
        return tag.name
    role = (tag.get("role") or "").lower()
    if role == "navigation":
        return "nav"
    if role == "contentinfo":
        return "footer"
    return inherited


def _walk(node: Tag, out: List[Block], region: str, base_url: str, own_host: str) -> None:
    buf: List[str] = []
    links: List[Link] = []

    def flush() -> None:
        text = clean_ws(" ".join(buf))
        if text or links:
            out.append(Block(text=text, region=region, links=list(links)))
        buf.clear()
        links.clear()

    for child in node.children:
        if isinstance(child, NavigableString):
            if not isinstance(child, Comment):
                buf.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        if child.name in BLOCK_TAGS or _has_block_child(child):
            flush()
            if child.name not in ("br", "hr"):
                if child.name == "a":
                    _collect_link(child, links, base_url, own_host)
                _walk(child, out, _region_of(child, region), base_url, own_host)
            continue
        # Inline element: keep its text in this block, remember links.
        for a in ([child] if child.name == "a" else child.find_all("a", href=True)):
            _collect_link(a, links, base_url, own_host)
        buf.append(child.get_text(" "))
    flush()


def _has_block_child(tag: Tag) -> bool:
    return tag.find(BLOCK_TAGS - {"br"}) is not None


def _collect_link(a: Tag, links: List[Link], base_url: str, own_host: str) -> None:
    href = (a.get("href") or "").strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return
    full = urljoin(base_url, href)
    host = urlparse(full).netloc.lower().removeprefix("www.")
    if not host or host == own_host or host.endswith("." + own_host):
        return
    text = clean_ws(a.get_text(" "))
    if not text:
        img = a.find("img")
        if img is not None:
            text = clean_ws(img.get("alt", "") or img.get("title", ""))
    if not text:
        text = clean_ws(a.get("title", "") or a.get("aria-label", ""))
    links.append(Link(text=text, href=full, domain=host))


def page_title(soup: BeautifulSoup) -> str:
    for sel in ('meta[property="og:title"]', 'meta[name="twitter:title"]'):
        m = soup.select_one(sel)
        if m and m.get("content"):
            return clean_ws(m["content"])
    if soup.title and soup.title.string:
        return clean_ws(soup.title.string)
    h1 = soup.find("h1")
    return clean_ws(h1.get_text(" ")) if h1 else ""


def page_date(soup: BeautifulSoup) -> str:
    for sel in ('meta[property="article:published_time"]', 'meta[name="date"]',
                'meta[name="dc.date"]', 'meta[name="DC.date.issued"]',
                'meta[itemprop="datePublished"]', 'meta[property="og:updated_time"]'):
        m = soup.select_one(sel)
        if m and m.get("content"):
            return m["content"][:10]
    t = soup.find("time", datetime=True)
    if t:
        return t["datetime"][:10]
    t = soup.find(attrs={"itemprop": "datePublished"})
    if t:
        return clean_ws(t.get("content") or t.get_text(" "))[:10]
    return ""


def parse_pdf_text(text: str, url: str, depth: int) -> ParsedPage:
    paras = [clean_ws(re.sub(r"-\n(?=\w)", "", p)) for p in re.split(r"\n\s*\n", text)]
    title = Path(urlparse(url).path).name
    return ParsedPage(url=url, title=title, date="", depth=depth,
                      blocks=[Block(text=p, region="main") for p in paras if p], is_pdf=True)


# --------------------------------------------------------------------------
# Organisation extraction
# --------------------------------------------------------------------------
class Watchlist:
    """Lines of `Canonical Name | alias | alias`. Short all-caps aliases are matched case-sensitively."""

    def __init__(self, entries: List[Tuple[str, List[str]]]):
        self.canonical: Dict[str, str] = {}
        self.patterns: List[Tuple[re.Pattern, str]] = []
        for canon, aliases in entries:
            ckey = canonical_key(canon)
            for name in [canon, *aliases]:
                self.canonical[canonical_key(name)] = ckey
                flags = 0 if (len(name) <= 6 and name.isupper()) else re.I
                pat = r"(?<!\w)" + r"[\s\-]+".join(re.escape(p) for p in name.split()) + r"(?!\w)"
                self.patterns.append((re.compile(pat, flags), ckey))
        self.display = {canonical_key(c): c for c, _ in entries}

    @classmethod
    def load(cls, path: Optional[Path]) -> "Watchlist":
        entries = []
        if path:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split("|") if p.strip()]
                entries.append((parts[0], parts[1:]))
        return cls(entries)


def load_stoplist(path: Optional[Path]) -> Tuple[Set[str], List[re.Pattern]]:
    exact: Set[str] = set()
    pats: List[re.Pattern] = []
    if path and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("re:"):
                pats.append(re.compile(line[3:], re.I))
            else:
                exact.add(canonical_key(line))
    return exact, pats


class OrgExtractor:
    def __init__(self, watchlist: Watchlist, stoplist: Tuple[Set[str], List[re.Pattern]],
                 spacy_model: Optional[str], use_links: bool = True):
        self.watchlist = watchlist
        self.stop_exact, self.stop_pats = stoplist
        self.use_links = use_links
        self.nlp = None
        if spacy_model and spacy_model.lower() != "none":
            try:
                import spacy
                self.nlp = spacy.load(spacy_model, exclude=["parser", "lemmatizer", "tagger",
                                                            "attribute_ruler", "morphologizer",
                                                            "senter", "textcat"])
                self.nlp.max_length = 2_000_000
                log.info("Loaded spaCy model %s", spacy_model)
            except (ImportError, OSError) as exc:
                log.warning("spaCy model %r unavailable (%s); continuing without NER. "
                            "Install with: python -m spacy download %s", spacy_model, exc, spacy_model)
        desc = "|".join(re.escape(d).replace(r"\ ", r"[\s\-]+") for d in DESCRIPTORS)
        # "the feminist collective Mujeres Creando" / "the NGO called Sea-Watch"
        self.pre_appos = re.compile(
            rf"(?<!\w)({desc})(?:\s+(?:called|named|known\s+as|chiamat[oaie]|denominat[oaie]|"
            rf"llamad[oa]s?|denominad[oa]s?|appelée?s?|nommée?s?|namens|genannt))?\s*[\"“«‘]?\s*$",
            re.I,
        )
        # "Sea-Watch, a German NGO" / "Mujeres Creando (an anarcha-feminist collective)"
        self.post_appos = re.compile(
            rf"^\s*(?:,|\(|–|—|-|:)?\s*(?:is\s+|was\s+|are\s+|were\s+|è\s+|es\s+|est\s+|ist\s+)?"
            rf"(?:an?|the|one\s+of\s+the|un[oa]?|une|ein[e]?|um[a]?)\s+"
            rf"(?:[\w’'\-]+\s+){{0,4}}?({desc})(?!\w)",
            re.I,
        )

    # ---------------------------------------------------------------- filters
    def is_stopped(self, key: str, surface: str) -> bool:
        if not key or key in self.stop_exact:
            return True
        return any(p.search(surface) for p in self.stop_pats)

    @staticmethod
    def plausible_name(surface: str) -> bool:
        if len(surface) < 2 or len(surface) > 120:
            return False
        if not re.search(r"[^\W\d_]", surface):
            return False
        if re.search(r"https?://|www\.|@|\.(com|org|net)\b", surface, re.I):
            return False
        toks = surface.split()
        if len(toks) > 12:
            return False
        if surface == surface.lower():
            return False
        key = canonical_key(surface)
        if key in MONTHS or key in LEADING_STRIP or key in GENERIC or key in HEAD_TO_TYPE:
            return False
        return True

    # --------------------------------------------------------------- patterns
    def _spans(self, text: str) -> List[Tuple[int, int, List[str]]]:
        """Maximal runs of capitalised tokens, allowing lowercase connectors inside."""
        toks = list(TOKEN_RE.finditer(text))
        spans = []
        i = 0
        while i < len(toks):
            if not _is_cap(toks[i].group(0)):
                i += 1
                continue
            j = i
            last_cap = i
            while j + 1 < len(toks):
                gap = text[toks[j].end():toks[j + 1].start()]
                if not re.fullmatch(r"\s{1,3}|\s?-\s?|[’']\s?", gap):
                    break
                nxt = toks[j + 1].group(0)
                if _is_cap(nxt):
                    j += 1
                    last_cap = j
                elif nxt.casefold() in CONNECTORS and j + 1 - last_cap <= 2:
                    j += 1
                else:
                    break
            span_toks = toks[i:last_cap + 1]
            for part in _split_conjoined(span_toks):
                spans.append((part[0].start(), part[-1].end(), [t.group(0) for t in part]))
            i = last_cap + 1
        return spans

    @staticmethod
    def _trim(text: str, start: int, end: int) -> Tuple[int, int]:
        """Drop leading function/sentence words ("Our", "Join", "The") from a span."""
        while True:
            m = TOKEN_RE.match(text, start)
            if not m or m.end() >= end:
                break
            word = m.group(0)
            if word.casefold().strip("’'") in LEADING_STRIP or not _is_cap(word):
                nxt = TOKEN_RE.search(text, m.end())
                if not nxt or nxt.start() >= end:
                    break
                start = nxt.start()
            else:
                break
        return start, end

    @staticmethod
    def _head_type(tokens: Sequence[str]) -> Optional[str]:
        low = [t.casefold().strip("’'") for t in tokens]
        content = [t for t in low if t not in CONNECTORS]
        if not content:
            return None
        typ = None
        for t in content:
            if t in HEAD_TO_TYPE:
                typ = HEAD_TO_TYPE[t]
                break
        if typ is None and content[-1] in TRAILING_TO_TYPE and len(content) >= 2:
            typ = TRAILING_TO_TYPE[content[-1]]
        if typ is None:
            return None
        # Need at least one distinctive word: not a head, not generic, not a connector.
        distinctive = [t for t in content
                       if t not in HEAD_TO_TYPE and t not in GENERIC and t not in TRAILING_TO_TYPE]
        acronym = any(re.fullmatch(r"[A-Z]{2,8}", t) for t in tokens)
        return typ if (distinctive or acronym) else None

    # ---------------------------------------------------------------- extract
    def extract(self, text: str, links: Sequence[Link], doc=None) -> List[Mention]:
        cands: List[Tuple[int, Mention]] = []   # (priority, mention); lower priority wins

        for pat, ckey in self.watchlist.patterns:
            for m in pat.finditer(text):
                cands.append((0, Mention(m.group(0), ckey, {"watchlist"}, "", m.start(), m.end())))

        for s, e, _ in self._spans(text):
            s, e = self._trim(text, s, e)
            surface = text[s:e]
            toks = [t.group(0) for t in TOKEN_RE.finditer(surface)]
            # Appositive check uses the trimmed span.
            before = text[max(0, s - 80):s]
            after = text[e:e + 90]
            pre = self.pre_appos.search(before)
            post = self.post_appos.match(after)
            head_typ = self._head_type(toks)
            if not head_typ and not pre and not post:
                # "the Abolitionist Futures collective": lowercase head right after the name
                nxt = re.match(r"\s+([\w\-]+)", after)
                if nxt and nxt.group(1) in HEAD_TO_TYPE and nxt.group(1).islower() \
                        and nxt.group(1) not in WEAK_TRAILING:
                    head_typ = HEAD_TO_TYPE[nxt.group(1)]
            elif head_typ and not pre and INDEFINITE_BEFORE.search(before):
                continue  # "a German NGO", "an Italian collective": a description, not a name
            if pre or post:
                d = (pre or post).group(1).casefold()
                typ = head_typ or HEAD_TO_TYPE.get(d.split()[-1].replace("-", ""), _descriptor_type(d))
                cands.append((1, Mention(surface, "", {"appositive"}, typ, s, e)))
            elif head_typ:
                cands.append((2, Mention(surface, "", {"pattern"}, head_typ, s, e)))

        if doc is not None:
            for ent in doc.ents:
                if ent.label_ not in ("ORG", "NORP_ORG"):
                    continue
                s, e = self._trim(text, ent.start_char, ent.end_char)
                surface = text[s:e]
                toks = [t.group(0) for t in TOKEN_RE.finditer(surface)]
                cands.append((3, Mention(surface, "", {"ner"}, self._head_type(toks) or "", s, e)))

        if self.use_links:
            for ln in links:
                t = tidy_surface(ln.text)
                if not t or LINK_TEXT_JUNK.match(t) or len(t.split()) > 10 or not t[:1].isupper():
                    continue
                idx = text.find(ln.text) if ln.text else -1
                s, e = (idx, idx + len(ln.text)) if idx >= 0 else (-1, -1)
                toks = [x.group(0) for x in TOKEN_RE.finditer(t)]
                cands.append((4, Mention(t, "", {"link"}, self._head_type(toks) or "", s, e,
                                         link_domain=ln.domain)))

        # Resolve overlaps: highest priority, then longest span wins; overlapping
        # lower-ranked candidates contribute their method label to the winner.
        accepted: List[Mention] = []
        for _, m in sorted(cands, key=lambda pm: (pm[0], -(pm[1].end - pm[1].start))):
            m.surface = tidy_surface(m.surface)
            if not m.key:
                m.key = canonical_key(m.surface)
            m.key = self.watchlist.canonical.get(m.key, m.key)
            if "watchlist" not in m.methods:
                if not self.plausible_name(m.surface) or self.is_stopped(m.key, m.surface):
                    continue
            hit = None
            if m.start >= 0:
                for a in accepted:
                    if a.start >= 0 and m.start < a.end and a.start < m.end:
                        hit = a
                        break
            else:
                hit = next((a for a in accepted if a.key == m.key), None)
            if hit is not None:
                if hit.key == m.key or (hit.start <= m.start and m.end <= hit.end):
                    hit.methods |= m.methods
                    hit.org_type = hit.org_type or m.org_type
                    hit.link_domain = hit.link_domain or m.link_domain
                continue
            accepted.append(m)
        return accepted


def _split_conjoined(toks: list) -> List[list]:
    """Split "Migrant Rights Coalition and the Rete Antirazzista" into two names.

    Only splits when the part before the conjunction already ends in an
    organisational head noun, so "Centre for Gender and Development" stays whole.
    """
    for k, t in enumerate(toks):
        if t.group(0).casefold() in CONJUNCTIONS and 0 < k < len(toks) - 1:
            left = toks[:k]
            if left[-1].group(0).casefold() in HEAD_TO_TYPE:
                right = toks[k + 1:]
                while right and right[0].group(0).casefold() in CONNECTORS:
                    right = right[1:]
                return [left] + (_split_conjoined(right) if right else [])
    return [toks]


ELIDED_CAP = re.compile(r"[^\W\d_]{1,5}['’][^\W\d_]")


def _is_cap(tok: str) -> bool:
    """Capitalised, or an elided article + capitalised word ("dell'Immigrazione")."""
    if tok[:1].isupper():
        return True
    m = ELIDED_CAP.match(tok)
    return bool(m) and m.group(0)[-1].isupper()


def _descriptor_type(descriptor: str) -> str:
    for word in reversed(re.split(r"[\s\-]+", descriptor)):
        if word in HEAD_TO_TYPE:
            return HEAD_TO_TYPE[word]
    if "union" in descriptor:
        return "association / union"
    return "organisation (generic)"


def acronym_pairs(text: str) -> List[Tuple[str, str]]:
    """Find "Long Name (ACR)" definitions whose word initials plausibly spell the acronym.

    The acronym's letters must be an in-order subsequence of the initials of
    the long form ("Independent Workers Union of Great Britain" -> IWGB), and
    the long form starts at the nearest word beginning with its first letter.
    """
    out = []
    for m in ACRONYM_DEF_RE.finditer(text):
        long_form, acr = m.group(1).strip(), m.group(2).replace(".", "")
        words = [w for w in re.split(r"[\s\-]+", long_form) if w]
        letters = canonical_key(acr).replace(" ", "")
        for i in range(len(words) - 1, max(-1, len(words) - len(acr) - 5), -1):
            tail = words[i:]
            initials = [canonical_key(w)[:1] for w in tail]
            if not initials or initials[0] != letters[:1] or not tail[0][:1].isupper():
                continue
            it = iter(initials)
            if len(tail) >= 2 and all(ch in it for ch in letters):
                out.append((tidy_surface(" ".join(tail)), acr))
                break
    return out


def block_hash(text: str) -> str:
    return hashlib.sha1(canonical_key(text).encode("utf-8")).hexdigest()[:16]
