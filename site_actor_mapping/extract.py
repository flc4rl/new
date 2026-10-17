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
import json
import logging
from collections import Counter, defaultdict
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from places import COUNTRY_CODES, LANGUAGE_NAMES, PLACES

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Organisational vocabulary (EN / IT / ES / FR / DE / PT). Extend freely.
# --------------------------------------------------------------------------
ORG_HEADS: Dict[str, List[str]] = {
    "collective": [
        "collective", "collectives", "kollektiv", "kollektive", "collettivo", "collettiva",
        "collettivi", "colectivo", "colectiva", "colectivos", "collectif", "collectifs",
        "coletivo", "coletiva", "crew", "squat", "commune", "collectief", "kollektief",
        "kollektivet",
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
        "netwerk", "coalitie", "alliantie", "federatie", "platformen",
        "netværk", "netværket", "nätverk", "nätverket", "koalition", "koalitionen", "allians",
        "coordination", "coordinamento", "coordinación", "coordinacion", "coordenação", "koordination",
        "coordinatie",
        "alliansen", "platform", "plattformen", "forbund", "förbund", "förbundet", "forbundet",
    ],
    "association / union": [
        "association", "associations", "associazione", "associazioni", "asociación",
        "asociacion", "associação", "verein", "verband", "society", "union", "unione",
        "unión", "sindacato", "sindicato", "syndicat", "gewerkschaft", "guild", "league",
        "lega", "liga", "ligue", "club", "circle", "circolo", "círculo", "cercle",
        "chapter", "fellowship", "brotherhood", "sisterhood", "solidarity",
        "vereniging", "bond", "vakbond", "unie", "kring", "genootschap",
        "forening", "foreningen", "förening", "föreningen", "fagforening", "fackförening",
        "fackförbund", "sammenslutning", "sammanslutning", "selskab", "sällskap", "klubb",
    ],
    "foundation / trust": [
        "foundation", "fondazione", "fundación", "fundacion", "fondation", "stiftung",
        "fundação", "trust", "fund", "fondo", "fonds", "endowment", "stichting",
        "fond", "fonden", "stiftelse", "stiftelsen", "stiftelsen",
    ],
    "movement / campaign": [
        "movement", "movements", "movimento", "movimiento", "mouvement", "bewegung",
        "campaign", "campagna", "campaña", "campagne", "kampagne", "initiative",
        "iniziativa", "iniciativa", "initiativ", "front", "fronte", "frente",
        "brigade", "brigata", "brigada", "rebellion", "resistance", "resistenza",
        "resistencia", "résistance", "uprising", "insurgency",
        "beweging", "campagne", "initiatief", "verzet", "actiegroep", "brigades",
        "bevægelse", "bevægelsen", "rörelse", "rörelsen", "kampagne", "kampanj", "kampanjen",
        "initiativ", "initiativet", "modstand", "motstånd", "aktionsgruppe", "aktionsgrupp",
        "fronten",
    ],
    "committee / council / assembly": [
        "committee", "comitato", "comité", "comite", "komitee", "council", "consiglio",
        "consejo", "conseil",
        "commission", "commissione", "comisión", "board", "caucus", "tribunal",
        "comité", "raad", "werkgroep",
        "udvalg", "udvalget", "kommitté", "kommittén", "komité", "råd", "rådet", "arbejdsgruppe",
        "arbetsgrupp",
        "assemblea", "asamblea", "assemblée",
    ],
    "research / education / culture": [
        "institute", "istituto", "instituto", "institut", "centre", "center", "centro",
        "centri", "observatory", "osservatorio", "observatorio", "observatoire", "lab",
        "laboratory", "laboratorio", "academy", "accademia", "academia", "académie",
        "university", "università", "universidad", "université", "universität",
        "school", "scuola", "escuela", "école", "college", "museum", "museo", "musée",
        "library", "biblioteca", "archive", "archives", "archivio", "archivo",
        "think-tank", "radio", "instituut", "centrum", "universiteit", "hogeschool",
        "institutet", "universitet", "universitetet", "højskole", "högskola", "folkhögskola",
        "centret", "centeret",
    ],
    "cooperative": ["cooperative", "co-op", "coop", "cooperativa", "coopérative", "genossenschaft",
                    "coöperatie", "cooperatie", "kooperativ", "kooperativet", "andelsforening"],
    "party": ["party", "partito", "partido", "parti", "partei", "partij", "partiet"],
    "organisation (generic)": [
        "organisation", "organization", "organisations", "organizations",
        "organizzazione", "organización", "organisationen", "group", "groups",
        "gruppo", "gruppi", "grupo", "grupos", "groupe", "gruppe", "project", "progetto",
        "proyecto", "projet", "projekt", "project", "groep", "agency", "agenzia", "agencia", "agence",
        "gruppe", "gruppen", "grupp", "gruppen", "projektet",
        "office", "secretariat", "mission", "service", "services", "space", "spazio",
        "espacio", "espace",
    ],
}
# Organisational words that end compound names in German, Dutch, Danish and
# Swedish ("Klimakollektivet", "Klimabündnis", "Folkebevægelsen", "Klimaatcollectief").
COMPOUND_HEADS: Dict[str, str] = {
    **{w: "collective" for w in ("kollektiv", "kollektivet", "kollektive", "collectief", "kollektief")},
    **{w: "network / coalition" for w in ("netzwerk", "bündnis", "netwerk", "netværk", "netværket",
                                          "nätverk", "nätverket", "alliantie", "alliansen", "koalitionen",
                                          "plattform", "plattformen", "föderation")},
    **{w: "association / union" for w in ("verein", "verband", "vereniging", "forening", "foreningen",
                                          "förening", "föreningen", "gewerkschaft", "vakbond",
                                          "fagforening", "fackförening", "forbund", "förbund", "förbundet")},
    **{w: "foundation / trust" for w in ("stiftung", "stichting", "stiftelse", "stiftelsen", "fonden")},
    **{w: "movement / campaign" for w in ("bewegung", "beweging", "bevægelse", "bevægelsen", "rörelse",
                                          "rörelsen", "kampagne", "kampanj", "kampanjen", "initiative",
                                          "initiativ", "initiativet", "aktionsgruppe", "aktionsgrupp",
                                          "actiegroep", "widerstand")},
    **{w: "committee / council / assembly" for w in ("komitee", "kommitté", "udvalg", "werkgroep",
                                                     "arbeitskreis", "plenum")},
    **{w: "cooperative" for w in ("genossenschaft", "kooperativ", "kooperativet", "coöperatie")},
}

# Compound heads that also end everyday words ("Klimabewegung", "Frauenbewegung",
# "Delegiertenplenum", "Bürgerinitiative gegen …"): a name only when followed by a
# distinctive word ("Folkebevægelsen mod EU", "Bürgerinitiative Umweltschutz Lüchow").
WEAK_COMPOUND_HEADS = {"bewegung", "beweging", "bevægelse", "bevægelsen", "rörelse", "rörelsen", "plenum",
                       "initiative", "initiativ", "initiativet", "kampagne", "kampanj", "kampanjen", "widerstand"}

# Central/Eastern/Northern European languages (pl, cs, sk, sl, hr/sr, el, ro, fi, nb,
# ca, eu, uk/ru, lt, hu). Added to ORG_HEADS below.
MORE_ORG_HEADS: Dict[str, List[str]] = {
    "collective": ["kolektyw", "kolektiv", "kolektivu", "colectivul", "kollektiivi", "col·lectiu",
                   "collectiu", "kolektiboa", "колектив", "коллектив", "kolektyvas", "kollektíva",
                   "συλλογικότητα", "συλλογικοτητα"],
    "network / coalition": ["sieć", "koalicja", "sojusz", "porozumienie", "síť", "koalice", "aliance",
                            "mreža", "koalicija", "savez", "zveza", "rețea", "rețeaua", "coaliția",
                            "alianța", "verkosto", "koalitio", "liitto", "nettverk", "koalisjon",
                            "allianse", "xarxa", "coalició", "sarea", "koordinadora", "мережа", "сеть",
                            "коаліція", "коалиция", "tinklas", "hálózat", "koalíció", "szövetség",
                            "δίκτυο", "συμμαχία", "συντονισμός"],
    "association / union": ["stowarzyszenie", "związek", "spolek", "sdružení", "združenie", "društvo",
                            "udruga", "udruženje", "asociația", "asociatia", "sindicat", "sindicatul",
                            "yhdistys", "ammattiliitto", "forening", "fagforening", "associació",
                            "elkartea", "sindikatua", "асоціація", "ассоциация", "профспілка",
                            "профсоюз", "asociacija", "profsąjunga", "egyesület", "szakszervezet",
                            "σωματείο", "ένωση", "συνδικάτο", "odbory", "sindikat"],
    "foundation / trust": ["fundacja", "nadace", "nadácia", "fundacija", "zaklada", "fundația",
                           "fundatia", "säätiö", "stiftelse", "fundació", "fundazioa", "фонд",
                           "fondas", "alapítvány", "ίδρυμα"],
    "movement / campaign": ["ruch", "inicjatywa", "kampania", "hnutí", "hnutie", "iniciativa", "kampaň",
                            "gibanje", "pokret", "inicijativa", "kampanja", "mișcarea", "miscarea",
                            "inițiativa", "campania", "liike", "aloite", "bevegelse", "aksjon",
                            "kampanje", "moviment", "campanya", "mugimendua", "рух", "движение",
                            "ініціатива", "инициатива", "кампанія", "кампания", "judėjimas",
                            "iniciatyva", "kampanija", "mozgalom", "kezdeményezés", "kampány",
                            "κίνημα", "κίνηση", "πρωτοβουλία", "καμπάνια", "fronta", "frontul", "rintama",
                            "фронт", "frontas", "μέτωπο"],
    "committee / council / assembly": ["komitet", "výbor", "odbor", "comitetul", "toimikunta", "komité",
                                       "comitè", "batzordea", "комітет", "комитет", "komitetas",
                                       "bizottság", "επιτροπή", "συνέλευση", "asanblada", "assemblea"],
    "cooperative": ["spółdzielnia", "družstvo", "zadruga", "cooperativa", "osuuskunta", "kooperativ",
                    "кооператив", "szövetkezet", "συνεταιρισμός"],
    "party": ["partia", "strana", "stranka", "partid", "partidul", "puolue", "parti", "partit",
              "alderdia", "партія", "партия", "partija", "párt", "κόμμα"],
    "organisation (generic)": ["organizacja", "grupa", "organizace", "skupina", "organizacija",
                               "organizația", "grup", "grupul", "järjestö", "ryhmä", "organisasjon",
                               "gruppe", "organització", "grup", "taldea", "erakundea", "організація",
                               "организация", "група", "группа", "organizacija", "grupė",
                               "szervezet", "csoport", "οργάνωση", "ομάδα"],
}
for _typ, _words in MORE_ORG_HEADS.items():
    ORG_HEADS.setdefault(_typ, []).extend(w for w in _words if w not in ORG_HEADS.get(_typ, []))

# Heads that only signal an organisation as the *last* word of a name
# ("Amnesty International", "Human Rights Watch", "Médecins Sans Frontières").
TRAILING_HEADS: Dict[str, List[str]] = {
    "ngo / charity": ["international", "watch", "aid", "relief", "frontières", "now", "matter"],
}
HEAD_TO_TYPE: Dict[str, str] = {}
for _typ, _words in ORG_HEADS.items():
    for _w in _words:
        HEAD_TO_TYPE.setdefault(_w, _typ)
TRAILING_TO_TYPE = {w: t for t, ws in TRAILING_HEADS.items() for w in ws}

# Lowercase descriptors that introduce a name: "the collective X", "X, an NGO".
DESCRIPTORS = sorted(
    # not "project", "commune", "école", "centre": "le projet Cigéo", "la commune de Bure",
    # "the centre Valpré" name projects and places, not organisations
    {w for w in HEAD_TO_TYPE if w not in {"press", "radio", "space", "spazio", "espacio",
                                           "espace", "service", "services", "office",
                                           "board", "fund", "school", "front", "red", "lab",
                                           "project", "progetto", "proyecto", "projet", "projekt",
                                           "projektet", "commune", "kollektive", "école", "scuola",
                                           "escuela", "centre", "center"}}
    | {"activist group", "civil society organisation",
       "civil society organization", "advocacy group", "pressure group",
       "community group", "human rights group", "think tank", "trade union",
       "non-governmental organisation", "non-governmental organization"},
    key=len, reverse=True,
)

CONNECTORS = {
    "of", "for", "the", "and", "&", "on", "in", "against", "to", "with", "without",
    "de", "di", "del", "della", "dei", "degli", "delle", "da", "la", "le", "les", "des",
    "du", "el", "los", "las", "por", "para", "per", "e", "y", "et", "und", "für", "der",
    "die", "das", "dos", "das", "do", "contra", "contre", "gegen", "sans", "sin", "senza",
    "pour", "al", "alla", "allo", "ai", "agli", "nel", "nella", "sul", "sulla", "a", "à",
    "i", "il", "lo", "gli", "l", "d", "von", "van", "zu", "zum", "zur", "em", "no", "na",
    "het", "voor", "en", "tegen", "met", "op", "uit", "der", "den", "ter", "te",
    "og", "och", "mod", "mot", "af", "av", "til", "till", "på", "med", "det", "for", "för",
    # pl / cs / sl / hr / ro / fi / ca / eu / hu / lt / el / uk / ru
    "w", "z", "za", "dla", "przeciw", "przeciwko", "v", "proti", "pro", "na", "od", "protiv", "u",
    "în", "pentru", "împotriva", "și", "si", "ja", "per", "contra", "amb", "dels", "eta", "és",
    "ir", "prieš", "και", "για", "κατά", "της", "του", "των", "τον", "την", "та", "і", "и", "в",
    "проти", "против", "для", "з", "с",
    # French / German / Portuguese contracted prepositions: "Droit au Logement", "Soutien aux Exilés"
    "au", "aux", "vom", "im", "beim", "ao", "pela", "pelo", "sem",
}
CONJUNCTIONS = {"and", "&", "e", "y", "et", "und", "og", "och", "și", "si", "ja",
                "és", "ir", "και", "та", "і", "и", "eta"}
# "and" words that are something else in other languages (Italian article "i", English "in",
# Romance preposition "a", Spanish / French "en"): conjunctions only on paragraphs detected
# as these languages.
CONJUNCTIONS_BY_LANG = {"pl": {"i"}, "hr": {"i"}, "sr": {"i"}, "sl": {"in"}, "cs": {"a"}, "sk": {"a"},
                        "nl": {"en"}, "af": {"en"}}
# Prepositions after which a coordination belongs to the same name: "Movimento per la Pace
# e il Disarmo", "Bund für Umwelt und Naturschutz", "Coalition against Racism and Fascism".
PURPOSE_PREPOSITIONS = {"for", "per", "por", "pour", "für", "para", "pela", "pelo", "against", "contre",
                        "contro", "contra", "gegen", "tegen", "mod", "mot", "voor", "of"}
# A locative connector followed by a place ends a name: "Associazione Ya Basta a Bologna",
# "Plataforma en Defensa de la Tierra en Valencia", "Union Syndicale Solidaires à Lyon".
LOCATIVE_CONNECTORS = {"in", "at", "near", "à", "a", "au", "aux", "en", "bei", "im", "nach", "em",
                       "na", "no", "w", "v", "u", "i", "på", "op", "te"}
# Heads that end proper names when written lower case after them: "Falkirk council",
# "Scientist rebellion", "COP27 coalition" are kept with the head.
NAME_FINAL_HEADS = {"council", "rebellion", "coalition", "assembly"}
# A name whose first or last word is one of these is an event, not an organisation:
# "World Congress on Climate Justice", "International Anti-War Conference", "Shell AGM".
EVENT_WORDS = {
    "conference", "conferences", "congress", "summit", "festival", "agm", "rally", "gathering",
    "meeting", "convergence", "webinar", "konferenz", "kongress", "gipfel", "treffen", "aktionstag",
    "aktionstage", "conférence", "congrès", "sommet", "rencontre", "rencontres", "journée", "journées",
    "conferenza", "congresso", "vertice", "incontro", "conferencia", "congreso", "cumbre", "encuentro",
    "conferência", "cimeira", "encontro", "conferentie", "congres", "top", "bijeenkomst", "konference",
    "kongres", "szczyt", "konferencja", "rassemblement", "manifestation", "kundgebung", "presidio",
    "demonstration", "demo", "week", "weekend", "days", "aktionswoche",
}
CONFERENCE_WORDS = {"conference", "conferences", "congress", "summit", "festival", "agm", "konferenz",
                    "kongress", "gipfel", "conférence", "congrès", "sommet", "conferenza", "congresso",
                    "vertice", "conferencia", "congreso", "cumbre", "conferência", "cimeira",
                    "conferentie", "congres", "konference", "kongres", "szczyt", "konferencja"}
# House numbers, street words and stops before a name found only by NER: an address.
ADDRESS_BEFORE = re.compile(
    r"(?<!\w)(?:rue|avenue|av\.|boulevard|bd|chemin|impasse|allée|place|quai|route|street|road|rd\.|"
    r"lane|square|straße|strasse|str\.|platz|weg|gasse|allee|viale|piazza|calle|avenida|plaza|rua|"
    r"praça|straat|plein|laan|gade|vej|gatan|vägen|arrêt(?:\s+de\s+bus)?|bus|tram|metro|métro|station|"
    r"gare|halte|haltestelle)(?:\s+(?:de\s+la|de\s+l['’]|de|du|des|d['’]|di|del|della))?\s*$", re.I)
# A house number before the name and a postcode or ", Town" after it: "138/140 Pleasance, Edinburgh".
_ADDRESS_NUMBER = re.compile(r"(?<![\w.])(?!(?:19|20)\d\d\b)\d{1,5}[a-z]?(?:[/-]\d{1,5})?,?\s*$")
_POSTCODE_AFTER = re.compile(r"^\s*(?:,\s*)?(?:\d[A-Z]{2}\b|\d{4,5}\b|[A-Z]{1,2}\d{1,2}[A-Z]?\s+\d[A-Z]{2}\b)")
_PAGINATION_BEFORE = re.compile(r"(?<!\w)(?:suivant|précédent|next|previous|prev|weiter|zurück|siguiente|"
                                r"anterior|successivo|precedente|volgende|vorige)\s*[:»›>]?\s*$", re.I)
# Standalone table marks ("X", "N. A.", "M. A."): a table of ticks, not running text.
_TABLE_MARKS = re.compile(r"(?<!\S)(?:X|x|✓|✔|N\.\s?A\.|M\.\s?A\.)(?!\S)")
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
    # navigation, captions and sentence openers that get capitalised
    "suivant", "précédent", "precedent", "next", "previous", "prev", "crédit", "credit",
    "voir", "afficher", "catégories", "categories", "tags", "posted", "publié", "publiée",
    "photo", "photos", "merci", "thanks", "c’est", "c'est", "cette", "ce", "cet", "ces",
    "whilst", "earlier", "later", "highlighting", "construisons", "rejoignez", "venez",
    "het", "een", "onze", "ons", "jouw", "jullie", "deze", "dit", "die", "wij", "zij",
    "η", "ο", "το", "οι", "τα", "την", "τον", "της", "του", "των",
    "waarom", "hoe", "wat", "wie", "waar", "wanneer", "lees", "bekijk", "meer",
    "vores", "vor", "vår", "våra", "vårt", "denne", "denna", "dette", "detta", "disse", "dessa",
    "hvorfor", "varför", "hvordan", "hur", "hvad", "vad", "hvor", "var", "når", "när", "vi",
    "læs", "läs", "mere", "mer", "se",
    "warum", "wieder", "welche", "welcher", "welches", "hinweise", "bitte", "dickes",
    "breite", "vollständiges", "wie", "was", "wer", "wo", "wann",
    "we", "it", "they", "he", "she", "i", "us", "them", "wir", "sie", "es", "er", "ihr",
    "nous", "ils", "elles", "elle", "il", "noi", "loro", "nosotros", "ellos", "ellas",
    # activity words heading a title: "Visite de la Coopérative X", "Workshop with Y"
    "visite", "visit", "atelier", "rencontre", "soirée", "conférence", "projection", "débat",
    "workshop", "talk", "panel", "screening", "discussion", "presentation", "launch",
    "incontro", "laboratorio", "presentazione", "taller", "charla", "encuentro", "vortrag",
}
# LEADING_STRIP words that also begin real names: dropped only at the start of a sentence
# or line (see OrgExtractor._trim). NAV_OPENERS are menu and caption words, which may be
# dropped even when a single name word is left ("Home Greenpeace" -> "Greenpeace").
NAME_OPENERS = {"via", "new", "home", "more", "il", "la", "le", "les", "el", "lo", "los", "las",
                "atelier", "visit", "visite", "press", "news"}
NAV_OPENERS = {"home", "news", "press", "visit", "visite", "more"}
ACTIVITY_OPENERS = {"atelier", "visit", "visite"}
# A name made only of heads + these words is too generic to keep.
GENERIC = {
    "partner", "partners", "member", "members", "main", "other", "related", "local",
    "national", "international", "regional", "global", "european", "our", "contact",
    "about", "home", "menu", "more", "new", "all", "general", "annual", "public",
    "private", "working", "affiliated",
    # document / event furniture that NER tags as ORG, notably in PDFs
    "workshop", "workshops", "session", "sessions", "programme", "program", "call-out",
    "callout", "statement", "report", "agenda", "schedule", "timetable", "camp", "day",
    "week", "plenary", "panel", "talk", "talks", "training", "briefing", "appendix",
    "morning", "closing", "opening", "evening", "weekly", "monthly", "assembly", "assemblies",
    "summer", "winter", "spring", "autumn", "faq", "navigation", "newsletter", "kontakt", "impressum",
    "press", "release", "media", "team", "guide", "handbook", "toolkit", "policy", "values",
    "webinar", "webinars", "recording", "recordings", "video", "videos", "resource", "resources",
}
# Generic words in other languages, used when deciding whether a name with a head word
# has anything distinctive ("Assemblée Générale", "Lokale Gruppen", "Groupe de Travail").
GENERIC_MULTI = {
    "générale", "général", "generale", "generali", "general", "generales", "allgemeine",
    "locale", "locales", "local", "locali", "lokale", "lokalen", "lokal", "lokala", "regionale",
    "regionali", "regionales", "nationale", "national", "nazionale", "nazionali", "nacional",
    "travail", "lavoro", "trabajo", "trabalho", "arbeit", "eine", "ein", "einer", "einem", "homogene",
    "homogenen", "offene", "offenes", "offenen", "neue", "neuen", "erste", "ersten", "andere",
    "anderen", "autres", "altri", "otros", "outros", "nuestra", "notre", "unsere", "unser",
    "legal", "awareness", "care", "support", "info", "welcome", "matters",
    "politique", "political", "politico", "político", "politisch", "politische", "politiek",
    # identity words that NER tags as organisations ("Black, Indigenous and People of Color")
    "black", "white", "indigenous", "intersex", "agender", "binary",
}
# Generic words before a trailing "aid": "First Aid", "Mutual Aid", "Legal Aid".
GENERIC_AID = {"first", "mutual", "legal", "medical", "emergency", "humanitarian", "food"}
MONTHS = {
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
}
LINK_TEXT_JUNK = re.compile(
    r"^(?:here|click|read|more|link|website|site|homepage|home|this|download|pdf|"
    r"facebook|twitter|x|instagram|youtube|linkedin|tiktok|telegram|mastodon|bluesky|"
    r"share|tweet|email|e-mail|contact|donate|don|faire un don|dona|spenden|subscribe|login|log in|sign up|next|"
    r"previous|menu|search|privacy|cookie|cookies|imprint|impressum)\b"
    # words that also begin names ("Via Campesina", "En Lucha", "IT Workers Union") are junk
    # only on their own
    r"|^(?:source|via|en|it|es|fr|de|pt|back|top)\.?$",
    re.I,
)

FILE_SHARING_HOSTS = {
    "drive.google.com", "docs.google.com", "dropbox.com", "dl.dropboxusercontent.com",
    "onedrive.live.com", "1drv.ms", "box.com", "wetransfer.com", "issuu.com", "scribd.com",
    "cloud.disroot.org", "nextcloud.com", "framadrive.org", "pad.riseup.net", "cryptpad.fr",
}
# Link text starting with these is an instruction, not a name ("Go to Instagram page",
# "Je fais un don", "Read the call-out").
CTA_WORDS = {
    "sign", "submit", "go", "click", "clicking", "read", "find", "see", "view", "visit", "donate", "sign", "register",
    "join", "follow", "download", "watch", "listen", "learn", "discover", "get", "buy", "book",
    "apply", "subscribe", "share", "check", "explore", "contact", "email", "write", "you", "we",
    "our", "your", "here", "more", "this", "je", "j", "nous", "vous", "voir", "afficher", "faire",
    "faites", "lire", "lisez", "découvrir", "découvrez", "rejoindre", "rejoignez", "soutenir",
    "soutenez", "cliquez", "cliquer", "inscrivez", "inscris", "télécharger", "téléchargez",
    "partager", "accéder", "accédez", "consulter", "consultez", "toutes", "tous", "tout", "plus",
    "ici", "vai", "leggi", "scopri", "guarda", "iscriviti", "unisciti", "sostieni", "dona",
    "clicca", "ver", "leer", "lee", "descubre", "únete", "apoya", "haz", "mehr", "hier", "jetzt",
    "lesen", "spenden", "mitmachen", "unterstütze", "unterstützen", "zum", "zur",
    "direkt", "finanziell", "komm", "kommt", "mach", "macht", "spende", "melde", "meldet",
    "werde", "hilf", "helft", "schau", "abonniere", "teile",
    "doneer", "steun", "lees", "bekijk", "word", "sluit", "meld", "schrijf", "kom", "doe",
    "ontdek", "volg", "deel", "download", "klik", "hier",
    "dołącz", "wesprzyj", "przeczytaj", "zapisz", "přidej", "podpoř", "přečti", "registrujte",
    "pridruži", "podpri", "preberi", "pridruži", "podrži", "pročitaj", "alătură", "susține",
    "citește", "liity", "tue", "lue", "bli", "støtt", "les", "uneix", "llegeix", "dona", "batu",
    "irakurri", "приєднуйся", "підтримай", "читай", "csatlakozz", "támogasd", "olvasd",
    "prisijunk", "paremk", "skaityk", "ελάτε", "στηρίξτε", "διαβάστε",
    "doner", "donér", "donera", "støt", "stöd", "stött", "læs", "läs", "tilmeld", "anmäl",
    "bliv", "bli", "følg", "följ", "del", "dela", "klik", "klicka", "kom", "kontakt",
    "skriv", "meld", "mere", "mer", "her", "här", "se", "køb", "köp", "hent", "ladda",
}
# CTA words that are also ordinary words or name words in another language (Norwegian
# "les" = read, but French "Les Amis de la Terre"; German "mehr" = more, but "Mehr
# Demokratie"): an instruction only when a lower-case or generic word follows
# ("Les mer", "Mehr erfahren", "Del gjerne", "Mehr Infos").
AMBIGUOUS_CTA = {"les", "mehr", "del", "her", "se", "var", "vi", "kom", "bli", "meld", "dona", "word", "tue"}
_CTA_GENERIC_NEXT = {"infos", "info", "informationen", "details", "lesen", "erfahren", "mer", "more"}


def starts_with_cta(words: Sequence[str]) -> bool:
    """Does a name start with an instruction ("Join the …", "Sign Up Now", "Les mer")?"""
    if not words:
        return False
    first = words[0].casefold().strip("’'\"“«")
    first = re.split(r"[’']", first)[0] if re.match(r"\w+[’']\w", first) else first
    if first not in CTA_WORDS:
        return False
    if first in AMBIGUOUS_CTA and len(words) > 1:
        nxt = words[1]
        return nxt[:1].islower() or nxt.casefold().strip(".!") in _CTA_GENERIC_NEXT
    return True


LINK_SKIP_DOMAINS = {
    "facebook.com", "instagram.com", "twitter.com", "x.com", "t.co", "tiktok.com", "linkedin.com",
    "youtube.com", "youtu.be", "vimeo.com", "flickr.com", "t.me", "telegram.me", "wa.me",
    "whatsapp.com", "bsky.app", "threads.net", "mastodon.social", "mastodon.scot", "spotify.com",
    "soundcloud.com", "wordpress.org", "wordpress.com", "w3w.co", "what3words.com",
    "maps.google.com", "goo.gl", "openstreetmap.org", "paypal.com", "paypal.me", "addtoany.com",
}


def link_text_is_name(text: str, domain: str) -> bool:
    """Is this link text plausibly an organisation's name (not an instruction,
    sentence, article title or social-media button)?"""
    domain = domain.lower().removeprefix("www.")
    if any(domain == d or domain.endswith("." + d) for d in LINK_SKIP_DOMAINS):
        return False
    if re.match(r"(?:don|dons|donate|donation|donations|give|soutenir|spenden|dona)\.", domain):
        return False
    if not text or LINK_TEXT_JUNK.match(text) or not text[:1].isupper():
        return False
    words = [w for w in re.split(r"\s+", text) if w]
    if len(words) > 5 or re.search(r"[!?→←»]|:\s|\s[–—-]\s|\.\.\.|…", text):
        return False
    if starts_with_cta(words) or words[-1].casefold().strip(".!") in {"here", "ici", "qui", "aquí", "hier"}:
        return False
    content = [w for w in words[1:] if w.casefold() not in CONNECTORS]
    if len(words) >= 3 and content and sum(not w[:1].isupper() for w in content) / len(content) > 0.5:
        return False   # mostly lower case: a sentence or a description
    return True


BLOCK_TAGS = {
    "p", "div", "li", "ul", "ol", "dl", "dd", "dt", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "main", "header", "footer", "nav", "aside", "blockquote",
    "figure", "figcaption", "table", "thead", "tbody", "tr", "td", "th", "form",
    "fieldset", "address", "details", "summary", "pre", "hr", "br", "caption",
}
DROP_TAGS = ["script", "style", "noscript", "svg", "template", "iframe", "canvas", "object",
             # form controls: country pickers, field labels and buttons are not content
             "select", "option", "datalist", "textarea", "input", "button"]
REGION_TAGS = ("nav", "header", "footer", "aside")

TITLE_SEPARATOR = re.compile(r"\s[-–—|]\s|:\s|\s/\s")
# Prepositions of place right before a name: "in Lingen", "à Bure", "bei Rügen", "near Brunsbüttel".
LOCATIVE_BEFORE = re.compile(
    r"(?<!\w)(?:in|at|near|around|outside|from|to|à|au|aux|en|près\s+de|près\s+d['’]|vers|sur|"
    r"nach|bei|im|am|aus|nahe|vor|auf|bij|te|op|nabij|naar|på|vid|nära|ved|nær|cerca\s+de|"
    r"vicino\s+a|presso|em|perto\s+de)\s+(?:the\s+|der\s+|den\s+|la\s+|le\s+|l['’])?$", re.I)
# "a" (Italian / Spanish / Portuguese / Catalan "to, at") and "i" (Scandinavian "in") are
# English and Italian articles too: locative only on paragraphs in those languages.
LOCATIVE_BY_LANG = {
    **{lang: re.compile(r"(?<!\w)a\s+$", re.I) for lang in ("it", "es", "pt", "ca")},
    **{lang: re.compile(r"(?<!\w)i\s+$", re.I) for lang in ("da", "sv", "nb", "no")},
}
PERSON_APPOS = re.compile(r"(?<!\w)(?:of|de|du|des|di|del|della|dei|von|vom|do|da|member|membre|"
                          r"membro|miembro|mitglied|militante?s?|activist[ae]?s?|attivist[ae]|"
                          r"founder|fondat\w*|fundador\w*|co-?founder|organi[sz]er|organisatri\w*|"
                          r"spokes\w*|porte-parole|portavoce|coordinat\w*)(?!\w)", re.I)
INDEFINITE_BEFORE = re.compile(r"(?<!\w)(?:an?|un[oa]?|une|ein(?:e|en)?|um[a]?|another|any|every|each)\s+$", re.I)
# Lowercase words too common after a place/name to signal an organisation by themselves.
WEAK_TRAILING = {"group", "groups", "project", "space", "service", "services", "office", "press",
                 "radio", "board", "fund", "school", "front", "red", "lab", "center", "centre",
                 "union", "party", "club", "mission", "agency", "platform", "forum", "league",
                 # "Often resistance to…", "Ukraine's resistance", "Scottish society"
                 "resistance", "résistance", "resistenza", "resistencia", "society", "solidarity"}
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
    referrer: str = ""        # for PDFs: the page that linked or embedded it
    kind: str = ""            # "calendar" for a loaded calendar month
    label: str = ""           # calendar month (YYYY-MM) or click number
    site: str = ""            # site key from the registry (host, or platform page)
    site_role: str = ""       # "main" or "linked"
    lang: str = ""            # declared page language (<html lang>), if any
    via_event: str = ""       # for linked sites: event(s) whose link led there


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


# A full stop after these is not the end of a sentence: abbreviations ("St.", "z. B.",
# "e.g.") and single letters (initials).
_ABBR_END = re.compile(
    r"(?:\b(?:St|Ste|Dr|Nr|Nos?|ca|bzw|vgl|usw|evtl|ggf|Prof|Mme|Mmes|Mlle|Mr|Mrs|Ms|Hr|Fr|Sr|Jr|vs|cf|"
    r"Abs|Bd|Av|av|Str|e\.g|i\.e|z\.\s?B|u\.\s?a|d\.\s?h|o\.\s?ä|s\.\s?o|u\.\s?U|p\.\s?ex|n°)"
    r"|(?<![\w.])[A-Za-zÀ-ÿ])\.$")
# A day number with a full stop ("12. Juni", "25. aprile") is not a sentence end when
# a month name or a lower-case word follows; numbered lists ("1. Introduction") still split.
_ORDINAL_END = re.compile(r"(?<![\w.])\d{1,2}\.$")
_MONTH_START = re.compile(
    r"(?:jan|feb|fév|fev|mär|mar|apr|avr|abr|mai|may|mag|mei|maj|jun|juin|giu|jul|juil|lug|aug|"
    r"août|ago|sep|set|okt|oct|ott|nov|dez|dec|déc|dic|gen|ene|sty|lut|kwi|cze|lip|sie|wrz|paź|lis|"
    r"gru|led|úno|bře|dub|kvě|čer|srp|zář|říj|pro|sij|vel|ožu|tra|svi|kol|ruj|stu)", re.I)


def split_sentences(text: str) -> List[Tuple[int, int]]:
    """Character spans of sentences (regex splitter; good enough for context windows)."""
    spans, start = [], 0
    for m in SENT_SPLIT_RE.finditer(text):
        head = text[max(0, m.start() - 12):m.start()].rstrip("\"”»’)")
        if _ABBR_END.search(head):
            continue
        nxt = text[m.end():m.end() + 6].lstrip("\"“«‘([")
        if _ORDINAL_END.search(head) and (nxt[:1].islower() or _MONTH_START.match(nxt)):
            continue
        spans.append((start, m.start() + len(m.group(0).rstrip())))
        start = m.end()
    spans.append((start, len(text)))
    return [(s, e) for s, e in spans if e > s]


_KEEP_S = {"news", "always", "towards", "perhaps", "texas", "paris", "athens", "wales",
           "lens", "mars", "kings", "james", "nicholas", "thomas", "lucas", "marcus"}


# French / Italian elided articles and prepositions glued to the next word:
# "d’Extinction Rebellion", "l'Association …", "dell’ARCI", "all'Associazione …".
_ELISIONS = r"(?:[dlnmstcj]|qu|jusqu|lorsqu|puisqu|quoiqu|un|quell|dell|dall|nell|sull|all|coll|degl|dagl|sugl|negl)"
ELIDED_PREFIX = re.compile(_ELISIONS + r"['’](?=[^\W\d_])", re.I)
_ELIDED_IN_KEY = re.compile(r"(?<![\w'])" + _ELISIONS + r"'(?=[^\W\d_])")
# German umlauts written as ae / oe / ue ("Ende Gelaende", "Gruene") are folded to the
# plain vowel, as "Gelände" becomes "gelande" when accents are removed. Only between
# consonants and not near the end of a word, so "queer", "blue", "fuel" and "Michael" keep theirs.
_UMLAUT_VARIANT = re.compile(r"(?<=[bcdfghjklmnprstvwxz])(?<![qg])([aou])e(?=[bcdfghjklmnprstvwxz][a-z])")


def strip_elision(token: str) -> str:
    """"d’Attac" -> "Attac", "dell'ARCI" -> "ARCI" (a token without elision is returned as is)."""
    return ELIDED_PREFIX.sub("", token, count=1)


def canonical_key(name: str) -> str:
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c)).casefold()
    n = n.replace("&", " and ").replace("’", "'")
    n = re.sub(r"'s\b", "", n)
    n = _ELIDED_IN_KEY.sub("", n)
    n = re.sub(r"[^\w\s-]", " ", n)
    toks = [t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is", "gas"))
            and t not in _KEEP_S else t for t in n.split()]
    toks = [_UMLAUT_VARIANT.sub(r"\1", t) if len(t) >= 5 else t for t in toks]
    while toks and toks[0] in {"the", "il", "lo", "la", "le", "gli", "l", "el", "los", "las",
                               "les", "der", "die", "das", "o", "a", "os", "as", "η", "ο", "το", "οι", "τα"}:
        toks = toks[1:]
    return " ".join(toks)


def tidy_surface(name: str) -> str:
    name = clean_ws(name).strip(" \t\"'“”‘’«»()[]{}:;,.-–—|/")
    name = re.sub(r"\s*[\[(][^\])]*$", "", name)          # unclosed "[DE" / "(UK"
    name = re.sub(r"^[^\[\]()]*[\])]\s*/?\s*(?=\w)", "", name)  # "France]/ Transnational": a fragment
    name = re.sub(r"^[^\w\"“«(]+|[^\w.)\"”»]+$", "", name).strip()  # emoji, bullets, "!"
    # a lower-case elided article or preposition: "d’Extinction Rebellion" (not "L'Oréal")
    name = re.sub(r"^" + _ELISIONS + r"['’](?=[^\W\d_])", "", name)
    name = re.sub(r"^(?:the|The|THE|De|Het|Den|Det|Die|Der|Das)\s+(?=\S+\s+\S)", "", name)
    # a trailing lower-case connector picked up from the next phrase: "ZAD de", "Friends of"
    name = re.sub(r"(?:\s+(?:de|des|du|d|of|and|et|und|di|del|della|la|le|les|von|van|der|for|"
                  r"pour|für|à|a|au|aux|en|in|on|at|or|ou|oder|y|e|the|die|das|el|il))+$", "", name)
    # a trailing article picked up from the next phrase: "GROUPE ÉCOUTE Le", "Kollektiv X Die"
    name = re.sub(r"\s+(?:Le|La|Les|L|The|Die|Der|Das|De|Het|El|Il|Lo|Los|Las)$", "", name)
    # an agenda status word after the name: "Allemagne ANNULÉ", "Climate Camp CANCELLED"
    name = re.sub(r"\s+(?:ANNULÉE?S?|ANNULEE?S?|REPORTÉE?S?|CANCELL?ED|POSTPONED|ABGESAGT|VERSCHOBEN|"
                  r"ANNULLATO|CANCELADO|AFLYST|INSTÄLLT|GEANNULEERD)$", "", name)
    name = re.sub(r"[’']s$", "", name)
    name = re.sub(r"(?<=\D)\s+(?:19|20)\d\d$", "", name)   # "Pinksterlanddagen 2022", "Ende Gelände 2019"
    return name


# PLACES and LANGUAGE_NAMES hold ordinary spellings; names are compared by key, so
# compare against the keys ("Nantes" -> "nante", "Nederlands" -> "nederland").
PLACES_KEYS = frozenset(PLACES | {canonical_key(p) for p in PLACES})
LANGUAGE_KEYS = frozenset(LANGUAGE_NAMES | {canonical_key(p) for p in LANGUAGE_NAMES})
PLACES.update(PLACES_KEYS)        # so other modules comparing canonical keys get the same answer
LANGUAGE_NAMES.update(LANGUAGE_KEYS)
_PLACE_LIST_SPLIT = re.compile(r"\s+(?:and|et|und|y|e|en|i|og|och|ja|és|a|&)\s+|\s*,\s*")


def is_place_key(key: str) -> bool:
    """A place, or a list of places ("slovenie et croatie", "bruxelles belgique")."""
    if key in PLACES_KEYS:
        return True
    parts = [p for p in _PLACE_LIST_SPLIT.split(key) if p]
    if len(parts) < 2:
        toks = key.split()
        return 2 <= len(toks) <= 3 and all(t in PLACES_KEYS and (len(t) >= 3 or t in COUNTRY_CODES)
                                           for t in toks)
    return all(p in PLACES_KEYS for p in parts)


# --------------------------------------------------------------------------
# Themes
# --------------------------------------------------------------------------
# Web addresses, e-mail addresses and @handles. Words inside them are not uses of a
# theme ("…/anti-colonial-attack/", "mondialiantirazzisti.org", "@anticolonialattack").
# The bare .es / .se branch is ASCII-only, so French inclusive writing such as
# "racisé.es" or "colonisé.es" is not mistaken for a Spanish or Swedish domain.
URL_LIKE = re.compile(r"(?:https?://|www\.)\S+|@\w[\w.]*"
                      r"|\b[\w.-]+\.(?:org|com|net|eu|info|de|fr|nl|be|at|it|dk|uk|ch|pt|pl|io)(?:/\S*)?"
                      r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:es|se)(?![\w.]?\w)(?:/\S*)?", re.I | re.A)

# Greek accents are folded before matching, so the stem "φασισ" also finds
# "φασίστες" and "ναζι" finds "Ναζί". Same length, so match offsets stay valid.
_GREEK_FOLD = str.maketrans("άέήίόύώΐΰΆΈΉΊΌΎΏ", "αεηιουωϊϋΑΕΗΙΟΥΩ")


class ThemeSet:
    """Themes loaded from a plain-text file (see example_themes.txt).

    A line starting with "!" is an exclusion for its theme: a hit that overlaps
    a match of the exclusion is not counted (e.g. "!énfasis" under [fascism]
    stops "*fasis*" from counting the Spanish word for "emphasis")."""

    MEMO_SIZE = 20000   # paragraphs repeated across pages (menus, footers) are matched once

    def __init__(self, themes: Dict[str, List[re.Pattern]], raw: Dict[str, List[str]],
                 needles: Optional[Dict[str, List[Optional[List[str]]]]] = None,
                 exclude: Optional[Dict[str, List[re.Pattern]]] = None):
        self.themes = themes
        self.raw = raw
        # Lower-cased literal parts each term needs; a term whose parts are not all in
        # the text cannot match, so its regex is skipped (most paragraphs have no hit).
        self.needles = needles if needles is not None else {
            t: [None] * len(ps) for t, ps in themes.items()}
        self.exclude = exclude or {}
        self._memo: Dict[str, Dict[str, List[Tuple[int, int, str]]]] = {}

    @property
    def names(self) -> List[str]:
        return list(self.themes)

    @classmethod
    def load(cls, path: Optional[Path]) -> "ThemeSet":
        themes: Dict[str, List[re.Pattern]] = {}
        raw: Dict[str, List[str]] = {}
        needles: Dict[str, List[Optional[List[str]]]] = {}
        exclude: Dict[str, List[re.Pattern]] = {}
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
                needles.setdefault(current, [])
                continue
            if current is None:
                raise ValueError(f"{path}:{lineno}: term before any [theme] header")
            if line.startswith("!"):
                exclude.setdefault(current, []).append(cls.compile_term(line[1:].strip()))
                continue
            themes[current].append(cls.compile_term(line))
            raw[current].append(line)
            needles[current].append(cls.term_needles(line))
        return cls(themes, raw, needles, exclude)

    @staticmethod
    def term_needles(term: str) -> Optional[List[str]]:
        """Literal word parts a term needs (None for a regular expression)."""
        if term.startswith("re:"):
            return None
        body = term.strip('"').strip("*").translate(_GREEK_FOLD)
        return [p.rstrip("*").casefold() for p in re.split(r"[\s-]+", body) if p.rstrip("*")] or None

    @staticmethod
    def compile_term(term: str) -> re.Pattern:
        if term.startswith("re:"):
            return re.compile(term[3:], re.I)
        term = term.strip('"').translate(_GREEK_FOLD)
        infix = term.startswith("*")      # *fascis* also matches inside compounds
        prefix = term.endswith("*")
        body = term.strip("*")
        # each word may end in * too: "krajn* pravic*", "kraštutin* dešin*"
        parts = [re.escape(p[:-1]) + r"\w*" if p.endswith("*") else re.escape(p)
                 for p in re.split(r"[\s-]+", body)]
        pat = r"[\s\-]+".join(parts)
        return re.compile((r"(?<![\w-])[\w-]*" if infix else r"(?<!\w)") + pat
                          + (r"\w*" if prefix else r"(?!\w)"),
                          re.I)

    def find(self, text: str) -> Dict[str, List[Tuple[int, int, str]]]:
        memo = self._memo.get(text)
        if memo is not None:
            return {t: list(v) for t, v in memo.items()}
        hits = self._find(text)
        if len(self._memo) >= self.MEMO_SIZE:
            self._memo.clear()
        self._memo[text] = hits
        return {t: list(v) for t, v in hits.items()}

    def _find(self, text: str) -> Dict[str, List[Tuple[int, int, str]]]:
        hits: Dict[str, List[Tuple[int, int, str]]] = {}
        ftext = text.translate(_GREEK_FOLD)
        low = ftext.casefold().replace("ı", "i")
        urls: Optional[List[Tuple[int, int]]] = None     # computed on the first candidate term
        for theme, pats in self.themes.items():
            found = []
            for p, need in zip(pats, self.needles.get(theme, ())):
                if need is not None and not all(n in low for n in need):
                    continue
                if urls is None:
                    urls = [(m.start(), m.end()) for m in URL_LIKE.finditer(text)]
                found.extend((m.start(), m.end(), text[m.start():m.end()]) for m in p.finditer(ftext)
                             if not any(a <= m.start() < b for a, b in urls))
            if found and theme in self.exclude:
                excl = [(m.start(), m.end()) for x in self.exclude[theme] for m in x.finditer(ftext)]
                found = [h for h in found if not any(a < h[1] and h[0] < b for a, b in excl)]
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
_CONSENT_MARKS = ("cookie", "consent", "cmplz", "borlabs", "gdpr")


def _meta_index(soup: BeautifulSoup) -> Dict[Tuple[str, str], Tag]:
    """<meta> tags by (attribute, value), first in document order, as select_one finds them."""
    meta: Dict[Tuple[str, str], Tag] = {}
    for m in soup.find_all("meta"):
        for attr in ("property", "name", "itemprop"):
            v = m.get(attr)
            if v is not None:
                meta.setdefault((attr, v), m)
    return meta


def parse_html(html: str, url: str, depth: int, strip_regions: bool) -> ParsedPage:
    # One pass over the tree for each clean-up step: CSS selectors (soupsieve) took
    # about 70% of the parsing time on large pages.
    soup = BeautifulSoup(html, "lxml")
    meta = _meta_index(soup)
    title = page_title(soup, meta)
    date = page_date(soup, meta)
    lang = (soup.html.get("lang") if soup.html else "") or ""
    if not lang:
        loc = meta.get(("property", "og:locale"))
        lang = loc.get("content", "") if loc else ""
    for t in soup(DROP_TAGS):
        t.decompose()
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for f in soup.find_all("form"):
        for t in f.find_all(["label", "legend"]):
            t.decompose()
    # Cookie / consent banners: any element whose id or class mentions them.
    consent = []
    for t in soup.find_all(True):
        if t.name in ("html", "body"):
            continue
        cls = t.get("class") or []
        hay = ((t.get("id") or "") + " " + (" ".join(cls) if isinstance(cls, list) else cls)).lower()
        if any(k in hay for k in _CONSENT_MARKS):
            consent.append(t)
    for t in consent:
        if not t.decomposed:
            t.decompose()
    if strip_regions:
        for t in soup(list(REGION_TAGS)):
            t.decompose()
        for t in soup.find_all(attrs={"role": lambda r: bool(r) and r.lower() in
                                      ("navigation", "banner", "contentinfo")}):
            if not t.decomposed:
                t.decompose()
    base = soup.find("base", href=True)
    base_url = urljoin(url, base["href"]) if base else url
    own_host = urlparse(url).netloc.lower().removeprefix("www.")
    root = soup.body or soup
    blocks: List[Block] = []
    _walk(root, blocks, "main", base_url, own_host)
    return ParsedPage(url=url, title=title, date=date, depth=depth,
                      blocks=[b for b in blocks if b.text or b.links], lang=lang)


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
    if host in FILE_SHARING_HOSTS or re.search(r"\.(pdf|docx?|odt|pptx?|xlsx?)(?:$|[?#])", full, re.I):
        return  # a document, not an organisation's website
    text = clean_ws(a.get_text(" "))
    if not text:
        img = a.find("img")
        if img is not None:
            text = clean_ws(img.get("alt", "") or img.get("title", ""))
    if not text:
        text = clean_ws(a.get("title", "") or a.get("aria-label", ""))
    links.append(Link(text=text, href=full, domain=host))


def page_title(soup: BeautifulSoup, meta: Optional[Dict[Tuple[str, str], Tag]] = None) -> str:
    meta = _meta_index(soup) if meta is None else meta
    for key in (("property", "og:title"), ("name", "twitter:title")):
        m = meta.get(key)
        if m and m.get("content"):
            return clean_ws(m["content"])
    if soup.title and soup.title.string:
        return clean_ws(soup.title.string)
    h1 = soup.find("h1")
    return clean_ws(h1.get_text(" ")) if h1 else ""


def page_date(soup: BeautifulSoup, meta: Optional[Dict[Tuple[str, str], Tag]] = None) -> str:
    meta = _meta_index(soup) if meta is None else meta
    for key in (("property", "article:published_time"), ("name", "date"), ("name", "dc.date"),
                ("name", "DC.date.issued"), ("itemprop", "datePublished"),
                ("property", "og:updated_time")):
        m = meta.get(key)
        if m and m.get("content"):
            return m["content"][:10]
    t = soup.find("time", datetime=True)
    if t:
        return t["datetime"][:10]
    t = soup.find(attrs={"itemprop": "datePublished"})
    if t:
        return clean_ws(t.get("content") or t.get_text(" "))[:10]
    return ""


def _pdf_chunks(para: str, target: int = 600, limit: int = 1500) -> List[str]:
    """pypdf often returns a whole page without blank lines. Break long text at
    lines that end a sentence, then merge the pieces back into paragraph-sized
    chunks so each mention keeps a useful amount of context."""
    text = clean_ws(para)
    if len(text) <= limit:
        return [text] if text else []
    pieces = [clean_ws(x) for x in re.split(r"(?<=[.!?:;])\s*\n(?=\s*[A-ZÀ-Ý0-9•\-–])", para)]
    chunks, cur = [], ""
    for piece in filter(None, pieces):
        cur = f"{cur} {piece}".strip()
        if len(cur) >= target:
            chunks.append(cur)
            cur = ""
    if cur:
        chunks.append(cur)
    return chunks


def parse_pdf_text(text: str, url: str, depth: int, title: str = "", date: str = "",
                   referrer: str = "") -> ParsedPage:
    """PDF text (pages separated by form feeds) -> paragraph blocks tagged "pdf p.N"."""
    blocks: List[Block] = []
    for page_no, page_text in enumerate(text.split("\f"), 1):
        page_text = re.sub(r"-\n(?=[a-zà-ÿ])", "", page_text)          # re-join hyphenated words
        for para in re.split(r"\n\s*\n", page_text):
            for chunk in _pdf_chunks(para):
                blocks.append(Block(text=chunk, region=f"pdf p.{page_no}"))
    title = title or Path(urlparse(url).path).name or url
    return ParsedPage(url=url, title=title, date=date, depth=depth, blocks=blocks,
                      is_pdf=True, referrer=referrer)


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


SPACY_MODELS = {
    "en": "en_core_web_sm", "fr": "fr_core_news_sm", "it": "it_core_news_sm",
    "de": "de_core_news_sm", "es": "es_core_news_sm", "pt": "pt_core_news_sm",
    "nl": "nl_core_news_sm", "ca": "ca_core_news_sm", "pl": "pl_core_news_sm",
    "da": "da_core_news_sm", "sv": "sv_core_news_sm", "nb": "nb_core_news_sm",
    "no": "nb_core_news_sm", "el": "el_core_news_sm", "fi": "fi_core_news_sm",
    "ro": "ro_core_news_sm", "lt": "lt_core_news_sm", "hr": "hr_core_news_sm",
    "sr": "hr_core_news_sm", "sl": "sl_core_news_sm", "uk": "uk_core_news_sm",
    "ru": "ru_core_news_sm", "mk": "mk_core_news_sm",
    # cs, sk, hu, eu have no spaCy pipeline: the multilingual xx_ent_wiki_sm is used if installed
}
ORG_LABELS = {"ORG"}
# Labels that, when given to the same name more often than ORG, show it is not one.
NON_ORG_LABELS = {"PERSON", "PER", "GPE", "LOC", "FAC", "EVENT", "LAW", "PRODUCT",
                  "WORK_OF_ART", "LANGUAGE"}   # MISC is too broad to count against a name
_LANG_HINTS = {
    "en": {"the", "and", "of", "to", "is", "with", "for", "this", "that", "we", "are"},
    "fr": {"le", "la", "les", "et", "des", "est", "une", "pour", "dans", "nous", "avec", "sur"},
    "it": {"il", "la", "che", "di", "e", "per", "una", "sono", "con", "della", "non", "gli"},
    "es": {"el", "la", "que", "de", "y", "los", "las", "una", "para", "con", "por", "es"},
    "de": {"der", "die", "und", "das", "ist", "nicht", "mit", "ein", "eine", "für", "wir", "auf"},
    "pt": {"o", "a", "que", "de", "e", "os", "uma", "para", "com", "não", "por", "são"},
    "nl": {"de", "het", "een", "en", "van", "is", "dat", "niet", "voor", "met", "zijn", "wij"},
    "da": {"og", "at", "det", "er", "af", "ikke", "til", "på", "med", "jeg", "vi", "har", "som", "mod"},
    "sv": {"och", "att", "det", "är", "av", "inte", "till", "på", "med", "jag", "vi", "har", "som", "mot"},
    "nb": {"og", "ikke", "det", "er", "av", "til", "på", "med", "jeg", "vi", "har", "som", "mot", "være", "ble"},
    "pl": {"i", "w", "na", "się", "nie", "jest", "z", "do", "że", "to", "przeciwko", "dla", "oraz", "jak"},
    "cs": {"a", "v", "na", "se", "je", "že", "to", "s", "z", "do", "proti", "pro", "jsou", "také"},
    "sk": {"a", "v", "na", "sa", "je", "že", "to", "s", "z", "do", "proti", "pre", "sú", "aj"},
    "sl": {"in", "v", "na", "je", "da", "se", "za", "z", "proti", "ki", "so", "tudi", "pri", "od"},
    "hr": {"i", "u", "na", "je", "da", "se", "za", "s", "protiv", "koji", "su", "od", "iz", "kao"},
    "ro": {"și", "în", "de", "la", "cu", "pe", "nu", "este", "care", "pentru", "împotriva", "sunt", "din"},
    "fi": {"ja", "on", "ei", "että", "se", "kanssa", "ovat", "myös", "tai", "kuin", "joka", "vastaan"},
    "ca": {"i", "el", "la", "els", "les", "que", "amb", "per", "contra", "és", "una", "dels", "també"},
    "eu": {"eta", "da", "ez", "bat", "dira", "baina", "ere", "bere", "aurka", "dute", "zen"},
    "hu": {"és", "a", "az", "hogy", "nem", "egy", "is", "van", "ellen", "meg", "volt", "mint"},
    "lt": {"ir", "yra", "kad", "su", "prieš", "kaip", "bet", "jie", "taip", "nuo", "iki"},
    "el": {"και", "το", "η", "ο", "να", "του", "της", "με", "για", "στο", "στην", "κατά", "τα", "οι"},
    "uk": {"і", "та", "в", "у", "на", "що", "не", "з", "до", "проти", "це", "як", "для", "від"},
    "ru": {"и", "в", "на", "что", "не", "с", "по", "против", "это", "как", "для", "от", "из"},
}
# Common function words added so that short agenda lines are not decided by a single
# shared word ("de" and "la" are Spanish too, "in" is Slovene, "on" Finnish, "at" Danish).
for _lang, _words in {
    "en": {"in", "a", "an", "on", "at", "by", "from", "our", "will", "be", "us", "against", "near",
           "about", "more", "you", "it", "as", "or", "have", "has", "their", "they", "was", "were",
           "which", "not", "but", "can", "all", "into", "after", "about"},
    "fr": {"de", "du", "à", "au", "aux", "l", "d", "qu", "ce", "pas", "en", "contre", "un", "près",
           "il", "qui", "ne", "se", "sont", "par", "plus", "ou", "mais", "été", "leur", "aussi", "ces"},
    "de": {"den", "dem", "des", "es", "gegen", "zu", "im", "von", "in", "zum", "zur", "sich", "auch",
           "bei", "nach", "aus", "um", "am", "werden", "wird", "sind", "sie", "ich", "noch", "vom"},
    "nl": {"op", "we", "naar", "te", "in", "tegen", "bij", "nog", "om", "ook", "aan", "er", "wordt",
           "worden", "zich", "deze", "hun", "uit", "ons"},
    "pt": {"do", "da", "no", "na", "em", "contra", "um", "se", "dos", "das", "pelo", "pela", "ao",
           "mais", "como", "nos", "nas"},
    "it": {"a", "in", "del", "le", "dei", "contro", "al", "da", "delle", "degli", "nel", "nella", "sul",
           "alla", "anche", "più", "è", "si", "ci"},
    "es": {"del", "al", "en", "se", "un", "contra", "lo", "como", "más", "sobre", "este", "esta",
           "está", "son", "su", "sus"},
}.items():
    _LANG_HINTS[_lang] |= _words


class _NerToken:
    __slots__ = ("pos_",)

    def __init__(self, pos: str):
        self.pos_ = pos


class NerEnt:
    """One named entity, with the parts of spaCy's Span that extraction uses."""
    __slots__ = ("label_", "start_char", "end_char", "text", "_n", "_pos")

    def __init__(self, label: str, start: int, end: int, text: str, n_tokens: int, first_pos: str):
        self.label_, self.start_char, self.end_char, self.text = label, start, end, text
        self._n, self._pos = n_tokens, first_pos

    def __len__(self) -> int:
        return self._n

    def __getitem__(self, i: int) -> _NerToken:
        if i not in (0, -self._n):
            raise IndexError("only the first token of a cached entity is kept")
        return _NerToken(self._pos)


class NerDoc:
    """The parts of a spaCy Doc that extraction uses (language, POS flag, entities).
    Small enough to keep for every distinct paragraph and to store on disk."""
    __slots__ = ("lang_", "ents", "_has_pos")

    def __init__(self, lang: str, has_pos: bool, ents: List[NerEnt]):
        self.lang_, self._has_pos, self.ents = lang, has_pos, ents

    def has_annotation(self, attr: str) -> bool:
        return attr == "POS" and self._has_pos

    @classmethod
    def from_spacy(cls, doc) -> "NerDoc":
        has_pos = bool(doc.has_annotation("POS"))
        return cls(doc.lang_, has_pos, [NerEnt(e.label_, e.start_char, e.end_char, e.text, len(e),
                                               e[0].pos_ if has_pos and len(e) else "")
                                        for e in doc.ents])

    def to_json(self) -> str:
        return json.dumps([self.lang_, int(self._has_pos),
                           [[e.label_, e.start_char, e.end_char, e._n, e._pos] for e in self.ents]],
                          ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, data: str, text: str) -> "NerDoc":
        lang, has_pos, ents = json.loads(data)
        return cls(lang, bool(has_pos), [NerEnt(lab, s, e, text[s:e], n, pos) for lab, s, e, n, pos in ents])


def _installed(model: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(model) is not None


_LANG_NOISE = re.compile(r"https?://\S+|www\.\S+|\S+@\S+|\b\d+(?:er|ère|e|ème|st|nd|rd|th|°)\b", re.I)


def language_scores(text: str) -> Counter:
    """Function-word hits per language (web addresses and ordinals such as "1er" removed)."""
    words = re.findall(r"[^\W\d_]+", _LANG_NOISE.sub(" ", text).lower())[:3000]
    return Counter({lang: sum(w in hints for w in words) for lang, hints in _LANG_HINTS.items()})


def detect_language(text: str, declared: str = "", prior: str = "") -> str:
    """Two-letter language code: the page's declared language, else a guess from
    function words. With `prior` (the page or document language) the guess must
    beat the prior by two function words; texts without evidence get the prior."""
    declared = (declared or "").lower().replace("_", "-").split("-")[0]
    if re.fullmatch(r"[a-z]{2}", declared):
        return declared
    scores = language_scores(text)
    top = max(scores.values()) if scores else 0
    if top == 0:
        return prior or "en"
    if prior and scores.get(prior, 0) + 2 > top:
        return prior
    return next(lang for lang in scores if scores[lang] == top)


def confident_language(text: str, fallback: str = "", min_hits: int = 2, margin: int = 2) -> str:
    """The language of a paragraph or agenda line when the evidence is clear, else
    `fallback`: at least `min_hits` function words, more than any other language, and
    `margin` more than the fallback language. Short lines such as "1er au 5 septembre :
    Camp action climat, Bratislava" are not sent to the Danish model on one word."""
    scores = language_scores(text)
    ranked = scores.most_common(2)
    if not ranked:
        return fallback
    best, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    if top < min_hits or top == second:
        return fallback
    if fallback and fallback != best and top < scores.get(fallback, 0) + margin:
        return fallback
    return best


def page_language(texts: Sequence[str], declared: str = "") -> str:
    """Language of a page or document: the declared one, else the most common
    confident language of its paragraphs (agenda lines, blocks), else a guess
    from all the text."""
    declared_code = (declared or "").lower().replace("_", "-").split("-")[0]
    if re.fullmatch(r"[a-z]{2}", declared_code):
        return declared_code
    votes = Counter(lang for lang in (confident_language(t) for t in texts if len(t.split()) >= 5) if lang)
    if votes:
        return votes.most_common(1)[0][0]
    return detect_language(" ".join(texts[:200]))


class OrgExtractor:
    NER_MEMO_SIZE = 200_000   # distinct (model, paragraph) results kept in memory

    def __init__(self, watchlist: Watchlist, stoplist: Tuple[Set[str], List[re.Pattern]],
                 spacy_model: Optional[str], use_links: bool = True, ner_size: str = "sm",
                 cache_dir: Optional[Path] = None):
        """spacy_model: a model name used for every page, "auto" (one model per page
        language, see SPACY_MODELS), or "none". ner_size: sm / md / lg models in
        auto mode; md and lg are more precise but miss more unusual group names.
        cache_dir: keep NER results in cache_dir/ner_cache.sqlite, keyed by model,
        model and spaCy versions and paragraph text, so a re-run (--no-crawl with new
        themes or stop lists) does not run spaCy again on text it has already seen."""
        self.watchlist = watchlist
        self.stop_exact, self.stop_pats = stoplist
        self.use_links = use_links
        self.spacy_model = (spacy_model or "none").strip()
        self.ner_size = ner_size
        self._models: Dict[str, object] = {}
        self.common_words: Set[str] = set()
        self.mid_caps: Optional[Set[str]] = None   # None until prepare_corpus() runs
        self._corpus_text = ""                      # distinct paragraphs, for phrase case counts
        self._phrase_case: Dict[str, bool] = {}
        self.missing_models: Set[str] = set()
        self.broken_models: Set[str] = set()       # installed but failed to load or run
        self._model_ids: Dict[str, str] = {}
        self._name_for: Dict[str, Optional[str]] = {}
        self._ner_memo: Dict[Tuple[str, str], NerDoc] = {}
        self.ner_stats: Counter = Counter()          # "analysed" by spaCy, read from "cache"
        self._db = None
        if cache_dir is not None:
            import sqlite3
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(Path(cache_dir) / "ner_cache.sqlite"))
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=OFF")
            self._db.execute("CREATE TABLE IF NOT EXISTS ner (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
        self.label_votes: Dict[str, Counter] = defaultdict(Counter)
        self.locative: Dict[str, List[int]] = defaultdict(lambda: [0, 0])   # key -> [after "in/à/bei…", total]
        desc = "|".join(re.escape(d).replace(r"\ ", r"[\s\-]+") for d in DESCRIPTORS)
        # "the feminist collective Mujeres Creando" / "the NGO called Sea-Watch"
        self.pre_appos = re.compile(
            rf"(?<!\w)({desc})(?:\s+(?:called|named|known\s+as|chiamat[oaie]|denominat[oaie]|"
            rf"llamad[oa]s?|denominad[oa]s?|appelée?s?|nommée?s?|namens|genannt))?\s*[\"“«‘]?\s*$",
            re.I,
        )
        # "Sea-Watch, a German NGO" / "Mujeres Creando (an anarcha-feminist collective)" /
        # "Alternatiba est un mouvement": punctuation or a copula must link the two.
        self.post_appos = re.compile(
            rf"^\s*(?:(?:,|\(|–|—|\s-|:)\s*(?:is\s+|was\s+|are\s+|were\s+|è\s+|es\s+|est\s+|ist\s+)?"
            rf"|\s+(?:is|was|are|were|è|es|est|ist)\s+)"
            rf"(?:an?|the|one\s+of\s+the|un[oa]?|une|ein[e]?|um[a]?)\s+"
            rf"(?:[\w’'\-]+\s+){{0,4}}?({desc})(?!\w)",
            re.I,
        )
        # Cheap guard for pre_appos (a long alternation): the last word before the name must
        # end a descriptor or be "called", "named", "chiamata"...
        self._appos_last = ({re.findall(r"[^\W\d_]+", d.lower())[-1] for d in DESCRIPTORS}
                            | {"called", "named", "as", "namens", "genannt"})

    # ------------------------------------------------------------------- NER
    def _load(self, name: str):
        if name not in self._models:
            try:
                import spacy
                # German keeps its tagger: with every noun capitalised, the part of
                # speech is the only way to tell "Gemeinschaft" from a name.
                keep_pos = name.startswith("de_")
                exclude = ["parser", "lemmatizer", "senter", "textcat"]
                if not keep_pos:
                    exclude += ["tagger", "attribute_ruler", "morphologizer"]
                nlp = spacy.load(name, exclude=exclude)
                nlp.max_length = 2_000_000
                log.info("Loaded spaCy model %s", name)
                self._models[name] = nlp
            except (ImportError, OSError) as exc:
                log.warning("spaCy model %r unavailable (%s). Install with: python -m spacy "
                            "download %s", name, exc, name)
                self.missing_models.add(name)
                self._models[name] = None
            except Exception as exc:  # noqa: BLE001 - a broken or mismatched model must not stop the run
                log.warning("spaCy model %r is installed but failed to load (%s: %s); NER is skipped "
                            "for its language. Check with: python -m spacy validate, then: "
                            "python -m spacy download %s --upgrade", name, type(exc).__name__, exc, name)
                self.broken_models.add(name)
                self._models[name] = None
        return self._models[name]

    def model_name_for(self, lang: str = "en") -> Optional[str]:
        """Name of the spaCy model used for `lang`, without loading it (None: no NER)."""
        if lang not in self._name_for:
            self._name_for[lang] = self._resolve_model_name(lang)
        return self._name_for[lang]

    def _resolve_model_name(self, lang: str) -> Optional[str]:
        if self.spacy_model.lower() == "none":
            return None
        if self.spacy_model.lower() != "auto":
            return self.spacy_model
        if lang in SPACY_MODELS:   # the requested size if installed, else the small model
            base = SPACY_MODELS[lang][:-3]
            for size in dict.fromkeys((self.ner_size, "sm")):
                if _installed(f"{base}_{size}"):
                    return f"{base}_{size}"
        if lang != "en":
            # Never fall back to the English model on another language: it tags
            # ordinary capitalised words (every German noun) as organisations.
            if _installed("xx_ent_wiki_sm"):
                return "xx_ent_wiki_sm"
            self.missing_models.add(SPACY_MODELS.get(lang, f"a spaCy model for '{lang}'"))
            return None
        return "en_core_web_sm"

    def model_for(self, lang: str = "en"):
        name = self.model_name_for(lang)
        return self._load(name) if name else None

    @property
    def nlp(self):
        """English (or the fixed) model; kept for callers that do not pass a language."""
        return self.model_for("en")

    def paragraph_languages(self, texts: Sequence[str], lang: str = "en") -> List[str]:
        """Language used for NER on each paragraph. Paragraphs of 8 or more words with
        clear evidence (see confident_language) get their own language; the rest get
        the page language. When most confident paragraphs agree on another language
        than `lang` (a French agenda whose text starts with English), that one is the
        page language. A language found in only one paragraph of the page needs strong
        evidence, so a stray line does not load a model for a language that is not there."""
        if self.spacy_model.lower() != "auto":
            return [lang] * len(texts)
        own = [confident_language(t) if len(t.split()) >= 8 else "" for t in texts]
        votes = Counter(c for c in own if c)
        fallback = lang
        if votes:
            top, n = votes.most_common(1)[0]
            if top != lang and n >= 3 and n >= 0.6 * sum(votes.values()):
                fallback = top
        out = []
        for t, c in zip(texts, own):
            if c:
                c = confident_language(t, fallback)
                if c != fallback and votes[c] < 2 and language_scores(t)[c] < 4:
                    c = fallback
            out.append(c or fallback)
        return out

    def _model_id(self, name: str) -> str:
        """Model name + versions: a persistent cache entry is reused only for the same model."""
        if name not in self._model_ids:
            from importlib import metadata
            versions = []
            for dist in (name, "spacy"):
                try:
                    versions.append(metadata.version(dist))
                except Exception:  # noqa: BLE001 - not installed as a distribution
                    versions.append("?")
            self._model_ids[name] = f"{name}@{versions[0]}|spacy@{versions[1]}|pos={name.startswith('de_')}"
        return self._model_ids[name]

    def _cache_get(self, keys: List[str]) -> Dict[str, "NerDoc"]:
        found: Dict[str, NerDoc] = {}
        if self._db is None or not keys:
            return found
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            q = "SELECT k, v FROM ner WHERE k IN (%s)" % ",".join("?" * len(chunk))
            for k, v in self._db.execute(q, chunk):
                found[k] = v
        return found

    def pipe(self, texts: List[str], lang: str = "en") -> list:
        """NER results for `texts` (spaCy-like records, see NerDoc). In auto mode each
        paragraph long enough to tell gets its own language (pages mix languages:
        translations, quotes, song sheets); short ones take the page language `lang`.
        Each distinct (model, text) is analysed once per run; with cache_dir, also once
        across runs."""
        per_text = self.paragraph_languages(texts, lang)
        docs: list = [None] * len(texts)
        by_model: Dict[str, List[int]] = defaultdict(list)
        for i, code in enumerate(per_text):
            name = self.model_name_for(code)
            if name and texts[i]:
                by_model[name].append(i)
        for name, idx in by_model.items():
            if self._models.get(name, True) is None:
                continue   # missing or broken model
            mid = self._model_id(name)
            todo = [t for t in dict.fromkeys(texts[i] for i in idx) if (mid, t) not in self._ner_memo]
            if todo and self._db is not None:
                keys = {t: hashlib.sha1(f"{mid}\x00{t}".encode("utf-8")).hexdigest() for t in todo}
                stored = self._cache_get(list(keys.values()))
                for t, k in keys.items():
                    if k in stored:
                        self._remember(mid, t, NerDoc.from_json(stored[k], t))
                        self.ner_stats["cache"] += 1
                todo = [t for t in todo if (mid, t) not in self._ner_memo]
            if todo:
                nlp = self._load(name)
                if nlp is None:
                    continue
                new_rows = []
                try:
                    for t, doc in zip(todo, nlp.pipe(todo, batch_size=64)):
                        rec = NerDoc.from_spacy(doc)
                        self._remember(mid, t, rec)
                        self.ner_stats["analysed"] += 1
                        if self._db is not None:
                            new_rows.append((hashlib.sha1(f"{mid}\x00{t}".encode("utf-8")).hexdigest(),
                                             rec.to_json()))
                except Exception as exc:  # noqa: BLE001 - one bad model must not stop the analysis
                    log.warning("spaCy model %r failed while analysing text (%s: %s); NER is skipped "
                                "for its language from now on", name, type(exc).__name__, exc)
                    self.broken_models.add(name)
                    self._models[name] = None
                if new_rows:
                    self._db.executemany("INSERT OR REPLACE INTO ner (k, v) VALUES (?, ?)", new_rows)
                    self._db.commit()
            for i in idx:
                rec = self._ner_memo.get((mid, texts[i]))
                if rec is None:
                    continue
                docs[i] = rec
                for ent in rec.ents:  # every label a name receives anywhere on the site
                    self.label_votes[canonical_key(tidy_surface(ent.text))][ent.label_] += 1
        return docs

    def _remember(self, mid: str, text: str, rec: "NerDoc") -> None:
        if len(self._ner_memo) >= self.NER_MEMO_SIZE:
            self._ner_memo.clear()
        self._ner_memo[(mid, text)] = rec

    def close(self) -> None:
        """Close the persistent NER cache (safe to call more than once)."""
        if self._db is not None:
            self._db.commit()
            self._db.close()
            self._db = None

    # ------------------------------------------------------- corpus statistics
    def prepare_corpus(self, texts: Iterable[str]) -> None:
        """Learn which words this site writes in lower case. A name made only of
        such words ("Climate Justice", "Hydrogen", "Approach") is ordinary
        language unless a rule with an organisational noun supports it."""
        lower: Counter = Counter()
        upper: Counter = Counter()
        # Each distinct paragraph counts once: calendar day cells, menus and footers repeat
        # the same Title-Case text many times and would outvote the running text.
        seen = list(dict.fromkeys(texts))
        for text in seen:
            for s, e in split_sentences(text):
                for i, m in enumerate(TOKEN_RE.finditer(text, s, e)):
                    w = strip_elision(m.group(0))       # "d’Attac" counts as "Attac"
                    if i == 0 or w.isupper() or not w:
                        continue  # sentence-initial and ALL-CAPS words say nothing about case
                    (upper if w[:1].isupper() else lower)[w.casefold()] += 1
        self.common_words = {w for w, n in lower.items() if n >= 2 and n >= 0.5 * upper.get(w, 0)}
        self.mid_caps = set(upper)
        self._corpus_text = "\n".join(seen)
        self._phrase_case = {}

    def capitalised_in_running_text(self, surface: str) -> bool:
        """Does the name have a word the site capitalises mid-sentence (a proper
        noun), or an acronym / camelCase / digit? Words capitalised only at the
        start of headings and lines ("Réveil", "Accessible") are not names."""
        words = [strip_elision(w) for w in TOKEN_RE.findall(surface) if w.casefold() not in CONNECTORS]
        return any(w.casefold() in self.mid_caps or re.search(r"\d|[a-z][A-Z]", w)
                   or (w.isupper() and len(w) <= 5) for w in words)

    def is_ordinary_words(self, surface: str) -> bool:
        words = [strip_elision(w).casefold().strip("’'") for w in TOKEN_RE.findall(surface)]
        content = [w for w in words if w not in CONNECTORS]
        if not content:
            return True
        if len(surface) <= 5 and re.fullmatch(r"(?:[A-Z][a-z]?\d?){1,3}", surface) and re.search(r"\d", surface):
            return True   # chemical formulae: CO2, CH4, H2O (not "COP27")
        return all(w in self.common_words or w in LEADING_STRIP or w in GENERIC for w in content)

    def vet(self, key: str, methods: Iterable[str]) -> bool:
        """Site-wide check for names found only by NER: reject those that are mostly
        written after a place preposition ("in X", "à X", "bei X"), or that spaCy more
        often labels as a place, person, law, event... Names also found by a rule, a
        descriptor, a link or the watchlist are kept."""
        if set(methods) - {"ner"}:
            return True   # a rule, a link or a descriptor also supports it
        after_loc, total = self.locative.get(key, (0, 0))
        if total >= 2 and after_loc / total >= 0.6:
            return False  # mostly written "in X", "à X", "bei X": a place
        votes = self.label_votes.get(key)
        if not votes:
            return True
        org = sum(n for lab, n in votes.items() if lab in ORG_LABELS)
        place = sum(n for lab, n in votes.items() if lab in {"GPE", "LOC", "FAC"})
        other = sum(n for lab, n in votes.items() if lab in NON_ORG_LABELS)
        # Small models often call coined names people ("Alternatiba" as PER), so
        # person labels count for less than place labels.
        if place >= 2 * org and place >= 3:
            return False
        return not (other >= 3 * org and org < 3)

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
        if len(re.findall(r"\d", surface)) >= 6:
            return False   # IBANs, phone numbers, dates
        toks = surface.split()
        if len(toks) > 12:
            return False
        if len(toks) >= 4 and surface.isupper():
            return False   # "YOU CAN DELIVER A BIG WIN FOR YOUR CAMPAIGN": a shouted sentence
        if surface == surface.lower():
            return False
        key = canonical_key(surface)
        if key in _PLAIN_STOP_KEYS:
            return False
        return True

    # --------------------------------------------------------------- patterns
    def _spans(self, text: str, lang: str = "") -> List[Tuple[int, int, List[str]]]:
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
                if not re.fullmatch(r"\s{1,3}|-|[’']\s?", gap):
                    break
                nxt = toks[j + 1].group(0)
                if _is_cap(nxt):
                    j += 1
                    last_cap = j
                elif nxt.casefold() in CONNECTORS and j + 1 - last_cap <= 2:
                    if (nxt.casefold() in _NEW_NAME_ARTICLES and j == last_cap and j > i
                            and _is_cap(toks[j - 1].group(0)) and _starts_named_org(toks, j + 2)):
                        break   # "At Kelvingrove Park the Glasgow Prisoner Solidarity group": a new name
                    if (nxt.casefold() in LOCATIVE_CONNECTORS and j + 2 < len(toks)
                            and _is_cap(toks[j + 2].group(0)) and len(toks[j + 2].group(0)) >= 3
                            and canonical_key(toks[j + 2].group(0)) in PLACES_KEYS):
                        break   # "… a Bologna", "… à Lyon": the place is not part of the name
                    j += 1
                else:
                    break
            span_toks = toks[i:last_cap + 1]
            for part in _split_conjoined(span_toks, CONJUNCTIONS | CONJUNCTIONS_BY_LANG.get(lang, set())):
                spans.append((part[0].start(), part[-1].end(), [t.group(0) for t in part]))
            i = last_cap + 1
        return spans

    @staticmethod
    def _trim(text: str, start: int, end: int) -> Tuple[int, int]:
        """Drop leading function/sentence words ("Our", "Join", "The") from a span.

        Words that also begin real names ("La Via Campesina", "New Economics
        Foundation", "Home Office", "Atelier Paysan") are dropped only at the start
        of a sentence or line, and never when that would leave a lone organisational
        word or (for most of them) a single word."""
        while True:
            m = TOKEN_RE.match(text, start)
            if not m or m.end() >= end:
                break
            word = m.group(0)
            elided = ELIDED_PREFIX.match(word)
            if elided and _is_cap(word[elided.end():]):
                start += elided.end()      # "d’Extinction Rebellion" -> "Extinction Rebellion"
                continue
            low = word.casefold().strip("’'")
            if low in NAME_OPENERS and _is_cap(word):
                rest = TOKEN_RE.findall(text, m.end(), end)
                if low in ACTIVITY_OPENERS:   # "Visite de la Coopérative X": skip the connectors too
                    rest = [w for w in rest if w.casefold() not in CONNECTORS or _is_cap(w)]
                at_start = not text[:start].strip() or text[:start].rstrip()[-1] in ".!?:;·|•–—-»\"“(["
                single_ok = (low in NAV_OPENERS and len(rest) == 1
                             and rest[0].casefold() not in HEAD_TO_TYPE and rest[0].casefold() not in GENERIC)
                if not (at_start and rest and _is_cap(rest[0]) and (len(rest) >= 2 or single_ok)):
                    break
            elif not (low in LEADING_STRIP or not _is_cap(word)):
                break
            nxt = TOKEN_RE.search(text, m.end())
            if not nxt or nxt.start() >= end:
                break
            start = nxt.start()
        return start, end

    @staticmethod
    def _head_type(tokens: Sequence[str]) -> Optional[str]:
        tokens = [strip_elision(t) for t in tokens]      # "l'Association …" -> "Association …"
        low = [t.casefold().strip("’'") for t in tokens]
        content = [t for t in low if t not in CONNECTORS]
        if not content:
            return None

        def generic(t: str) -> bool:
            return t in GENERIC or t in GENERIC_MULTI

        # A real acronym (not an ALL-CAPS heading word such as "LOCAL" or "GROUPS").
        acronym = any(re.fullmatch(r"[A-Z]{2,8}", t) and t.casefold() not in HEAD_TO_TYPE
                      and not generic(t.casefold()) and t.casefold() not in TRAILING_TO_TYPE
                      for t in tokens)
        # A head inside a place phrase belongs to that phrase, not to the name:
        # "Stunde pro Woche in das Projekt", "mit in die Aktion".
        in_phrase = {low[h] for h in range(2, len(low))
                     if low[h - 1] in ARTICLES and low[h - 2] in _INTO_PREPOSITIONS}
        typ = None
        for t in content:
            if t in HEAD_TO_TYPE and t not in in_phrase:
                typ = HEAD_TO_TYPE[t]
                break
        if typ is None and content[-1] in TRAILING_TO_TYPE and len(content) >= 2:
            if not (content[-1] == "aid" and all(t in GENERIC_AID or generic(t) for t in content[:-1])):
                typ = TRAILING_TO_TYPE[content[-1]]
        if typ is None:   # a compound ending in an organisational word: "Klimakollektivet"
            for i, t in enumerate(content):
                for head, htyp in COMPOUND_HEADS.items():
                    if t.endswith(head) and len(t) >= len(head) + 3:
                        if head not in WEAK_COMPOUND_HEADS:
                            return htyp   # the prefix ("Klima-") makes it distinctive
                        # weak heads: first word of the name, followed by a distinctive word
                        rest = [x for x in content[i + 1:] if not generic(x) and x not in HEAD_TO_TYPE]
                        if i == 0 and (rest or acronym):
                            return htyp
        if typ is None:
            return None
        # Need at least one distinctive word: not a head, not generic, not a connector.
        distinctive = [t for t in content
                       if t not in HEAD_TO_TYPE and not generic(t) and t not in TRAILING_TO_TYPE]
        return typ if (distinctive or acronym) else None

    # ---------------------------------------------------------------- extract
    def _pre_appositive(self, before: str):
        """pre_appos.search(before), run only when the last word could end a descriptor."""
        m = _APPOS_TAIL.search(before)
        w = m.group(1).lower() if m else ""
        if w and (w in self._appos_last or w.startswith(_APPOS_STEMS)):
            return self.pre_appos.search(before)
        return None

    def _org_elsewhere(self, surface: str) -> bool:
        """Has spaCy labelled this exact name ORG at least twice in the paragraphs seen so
        far (this page included), and more often than any non-organisation label?"""
        votes = self.label_votes.get(canonical_key(tidy_surface(surface)))
        if not votes:
            return False
        org = sum(n for lab, n in votes.items() if lab in ORG_LABELS)
        other = sum(n for lab, n in votes.items() if lab in NON_ORG_LABELS)
        if len(TOKEN_RE.findall(surface)) == 1:   # single words are often fragments ("d’Earth")
            return org >= 3 and org >= 2 * other
        return org >= 2 and org >= other

    def _ordinary_but_named(self, surface: str) -> bool:
        """A name of ordinary words that is still a name ("Les Soulèvements de la Terre",
        "Climate Justice Action"): several words, tagged ORG by spaCy elsewhere, and
        written capitalised at least as often as the same words in lower case (unlike
        "climate justice" or "carbon capture")."""
        words = [w for w in TOKEN_RE.findall(surface) if w.casefold() not in CONNECTORS]
        if len(words) < 2 or not self._org_elsewhere(surface):
            return False
        key = canonical_key(surface)
        if key not in self._phrase_case:
            body = " ".join(TOKEN_RE.findall(surface))
            body = re.sub(r"^(?:the|les|la|le|die|der|das|el|il)\s+", "", body, flags=re.I)
            pat = r"(?<!\w)" + r"\s+".join(re.escape(w) for w in body.split()) + r"(?!\w)"
            lower = len(re.findall(pat.lower(), self._corpus_text))
            upper = len(re.findall(pat, self._corpus_text, re.I)) - lower
            self._phrase_case[key] = upper >= lower
        return self._phrase_case[key]

    def _name_before_head(self, surface: str, toks: Sequence[str], before: str) -> bool:
        """For "the Abolitionist Futures collective" (a lower-case head after the name):
        is the capitalised part a plausible name, not a place, nationality, sentence
        opener or ordinary word ("Danish movement", "Often resistance", "Ukraine's")?"""
        key = canonical_key(tidy_surface(surface))
        sentence_start = (not before.strip()
                          or re.search(r"(?:[.!?:;«“\"(\-–—•|]|^\s*\w?\.?)\s*$", before[-6:]) is not None)
        if is_place_key(key):
            return False
        if len(toks) >= 2:
            return True   # "Climate Justice Action network": ordinary words, but a name
        return (not sentence_start
                and (self.mid_caps is None or self.capitalised_in_running_text(surface))
                and not self.is_ordinary_words(surface))

    def _conjunction_parts(self, text: str, start: int, end: int, ents: List[Tuple[int, int, str]],
                           conjunctions: Set[str], listed: bool = False) -> List[Tuple[int, int]]:
        """Split "Greenpeace and the Climate Justice Coalition" where spaCy sees two
        entities, one ending before the conjunction and one starting after it (an
        article may come between). With `listed` ("le mouvement Alternatiba et
        ANV-COP21") a conjunction between two capitalised names splits without NER.
        Names whose coordination follows "for / per / pour / für / against …" stay whole,
        and so do "Place and Place Name" ("Grangemouth and Mossmorran Solidarity",
        "Perth and Kinross Council"): without an article after the conjunction, the
        part before it must itself be an organisation entity."""
        toks = list(TOKEN_RE.finditer(text, start, end))
        for k in range(1, len(toks) - 1):
            if toks[k].group(0).casefold() not in conjunctions:
                continue
            left_end = toks[k - 1].end()
            j = k + 1
            while j < len(toks) - 1 and toks[j].group(0).casefold() in ARTICLES:
                j += 1
            right_start = toks[j].start()
            elided = ELIDED_PREFIX.match(toks[j].group(0))
            if elided:
                right_start += elided.end()
            left_low = [t.group(0).casefold() for t in toks[:k]
                        if not _is_cap(t.group(0)) and t.group(0).casefold() not in ARTICLES]
            if left_low and left_low[-1] in PURPOSE_PREPOSITIONS:
                continue
            crossing = any(a < toks[k].start() and b > toks[k].end() for a, b, _ in ents)
            left_ents = [lab for a, b, lab in ents if b == left_end]
            article = j > k + 1 or bool(elided and toks[j].group(0)[:1] in "lL")   # "et l’Union …"
            ner_split = (not crossing and bool(left_ents)
                         and any(toks[k + 1].start() <= a <= right_start for a, b, _ in ents)
                         and (article or "ORG" in left_ents))
            listed_split = (listed and not crossing and not elided and _is_cap(toks[j].group(0))
                            and not ELIDED_PREFIX.match(toks[k - 1].group(0)))
            if ner_split or listed_split:
                return [(start, left_end)] + self._conjunction_parts(text, right_start, end, ents,
                                                                     conjunctions, listed)
        return [(start, end)]

    def extract(self, text: str, links: Sequence[Link], doc=None) -> List[Mention]:
        cands: List[Tuple[int, Mention]] = []   # (priority, mention); lower priority wins

        for pat, ckey in self.watchlist.patterns:
            for m in pat.finditer(text):
                cands.append((0, Mention(m.group(0), ckey, {"watchlist"}, "", m.start(), m.end())))

        lang = doc.lang_ if doc is not None and doc.lang_ != "xx" else (
            confident_language(text) if len(text.split()) >= 8 else "")
        conjunctions = CONJUNCTIONS | CONJUNCTIONS_BY_LANG.get(lang, set())
        ents = [(e.start_char, e.end_char, e.label_) for e in doc.ents] if doc is not None else []
        # A table of ticks and "N. A." marks: its capitalised cells are not names.
        table = len(_TABLE_MARKS.findall(text)) >= 2

        for s, e, _ in ([] if table else self._spans(text, lang)):
            s, e = self._trim(text, s, e)
            surface = text[s:e]
            toks = [t.group(0) for t in TOKEN_RE.finditer(surface)]
            # Appositive check uses the trimmed span.
            before = text[max(0, s - 80):s]
            after = text[e:e + 90]
            pre = self._pre_appositive(before)
            post = self.post_appos.match(after)
            if post and (PERSON_APPOS.search(post.group(0)) or re.match(r"\s+[A-ZÀ-Ý]", after[post.end():])):
                # "Claire, une militante de l'association X": a person, not an org;
                # "In Prague, the Czech climate action group Limity Jsme My": the
                # descriptor introduces the next name
                post = None
            head_typ = self._head_type(toks)
            after_head = False
            if not head_typ and not pre and not post:
                # "the Abolitionist Futures collective": lowercase head right after the name
                nxt = re.match(r"\s+([\w\-]+)", after)
                if (nxt and nxt.group(1) in HEAD_TO_TYPE and nxt.group(1).islower()
                        and nxt.group(1) not in WEAK_TRAILING and self._name_before_head(surface, toks, before)):
                    head_typ = HEAD_TO_TYPE[nxt.group(1)]
                    after_head = True
                    if nxt.group(1) in NAME_FINAL_HEADS:   # "Falkirk council" -> "Falkirk council"
                        e += nxt.end()
                        surface = text[s:e]
                        after_head = False
            elif head_typ and not pre and INDEFINITE_BEFORE.search(before):
                continue  # "a German NGO", "an Italian collective": a description, not a name
            if pre or post:
                d = (pre or post).group(1).casefold()
                typ = head_typ or HEAD_TO_TYPE.get(d.split()[-1].replace("-", ""), _descriptor_type(d))
                # "le mouvement Alternatiba et ANV-COP21": a list of names after the descriptor
                parts = (self._conjunction_parts(text, s, e, ents, conjunctions, listed=True)
                         if pre else [(s, e)])
                for ps, pe in parts:
                    ptyp = typ if len(parts) == 1 else (self._head_type(TOKEN_RE.findall(text[ps:pe])) or typ)
                    cands.append((1, Mention(text[ps:pe], "", {"appositive"}, ptyp, ps, pe)))
            elif not head_typ and self._org_elsewhere(surface) and not any(
                    a < e and s < b and lab in ("ORG", "NORP_ORG") for a, b, lab in ents):
                # spaCy missed it here ("Ende Gelande, action de …" as MISC) but tags the
                # same name as an organisation elsewhere in the text it has seen
                cands.append((3, Mention(surface, "", {"ner"}, "", s, e)))
            elif head_typ:
                parts = self._conjunction_parts(text, s, e, ents, conjunctions) if ents else [(s, e)]
                if after_head:
                    parts = parts[-1:]   # only the part next to the lower-case head
                for ps, pe in parts:
                    ptyp = (head_typ if len(parts) == 1 or after_head
                            else self._head_type(TOKEN_RE.findall(text[ps:pe])))
                    if ptyp:   # a part without a head is left to NER ("Greenpeace and the X Coalition")
                        cands.append((2, Mention(text[ps:pe], "", {"pattern"}, ptyp, ps, pe)))

        if doc is not None and not table:
            german = doc.lang_ == "de" and doc.has_annotation("POS")
            locative_lang = LOCATIVE_BY_LANG.get(doc.lang_)
            for ent in doc.ents:
                if ent.label_ not in ("ORG", "NORP_ORG"):
                    continue
                if (german and len(ent) == 1 and ent[0].pos_ in {"NOUN", "ADJ", "VERB", "ADV", "PRON"}
                        and not ent.text.isupper()):
                    continue   # a common German noun ("Gemeinschaft", "Küche"), not a name
                # "General Election, Reform UK", "Climáximo, Lisbon": one name per comma part
                parts, pos = [], ent.start_char
                for piece in re.split(r"(,\s+)", text[ent.start_char:ent.end_char]):
                    if piece and not re.fullmatch(r",\s+", piece):
                        parts.append((pos, pos + len(piece)))
                    pos += len(piece)
                for ps, pe in parts:
                    s, e = self._trim(text, ps, pe)
                    surface = text[s:e]
                    if not surface.strip():
                        continue
                    pre_text = text[max(0, s - 25):s]
                    if _PAGINATION_BEFORE.search(pre_text):
                        continue   # "Article suivant Suivant Amaury": the author of the next post
                    if ADDRESS_BEFORE.search(pre_text) or (_ADDRESS_NUMBER.search(pre_text) and (
                            _POSTCODE_AFTER.match(text[e:e + 16]) or _place_follows(text, e))):
                        continue   # "138/140 Pleasance, Edinburgh", "rue de la Papotière", "arrêt de bus X"
                    m_pc = re.search(r"\s+[A-Z]{1,2}\d{1,2}[A-Z]?$", surface)
                    if m_pc and re.match(r"\s+\d[A-Z]{2}\b", text[e:e + 5]):
                        e = s + m_pc.start()   # "Edinburgh EH8 9RR": a postcode, not part of the name
                        surface = text[s:e]
                    toks = [t.group(0) for t in TOKEN_RE.finditer(surface)]
                    stats = self.locative[canonical_key(tidy_surface(surface))]
                    stats[1] += 1
                    if LOCATIVE_BEFORE.search(pre_text) or (locative_lang and locative_lang.search(pre_text)):
                        stats[0] += 1
                    cands.append((3, Mention(surface, "", {"ner"}, self._head_type(toks) or "", s, e)))

        if self.use_links:
            for ln in links:
                t = tidy_surface(ln.text)
                if not link_text_is_name(t, ln.domain):
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
            cut = TITLE_SEPARATOR.search(m.surface)
            if cut and "watchlist" not in m.methods:   # "INEOS - Press Release" -> "INEOS"
                if m.start >= 0:
                    m.end = m.start + cut.start()
                m.surface = m.surface[:cut.start()]
            m.surface = tidy_surface(m.surface)
            if starts_with_cta(m.surface.split()) and "watchlist" not in m.methods:
                continue   # "Sign Up Now", "Join the ...": an instruction
            if not m.key:
                m.key = canonical_key(m.surface)
            m.key = self.watchlist.canonical.get(m.key, m.key)
            if "watchlist" not in m.methods:
                if not self.plausible_name(m.surface) or self.is_stopped(m.key, m.surface):
                    continue
                words = [canonical_key(w) for w in TOKEN_RE.findall(m.surface)]
                if words and all(w in LANGUAGE_KEYS for w in words):
                    continue   # a language menu: "Deutsch English Español Français Italiano"
                if "pattern" not in m.methods and is_place_key(m.key):
                    continue   # a bare place name: "Genève", "Hambach", "Suisse", "In Germany the movement"
                if _is_generic_name(m.surface) or _is_event_name(m.surface):
                    continue   # "Working Group", "Media Team"; "International Anti-War Conference"
                if ("pattern" not in m.methods and not self._head_type(TOKEN_RE.findall(m.surface))
                        and not self._ordinary_but_named(m.surface)):
                    if self.is_ordinary_words(m.surface):
                        continue
                    if (m.methods == {"ner"} and self.mid_caps is not None
                            and not self.capitalised_in_running_text(m.surface)):
                        continue
                if m.methods <= {"ner", "link"} and (_has_verb_words(m.surface) or (
                        _mostly_lowercase(m.surface) and not self._head_type(TOKEN_RE.findall(m.surface)))):
                    continue   # "Résistance ne devait pas être": a phrase, not a name
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

ARTICLES = {"the", "die", "der", "das", "la", "le", "les", "l", "il", "lo", "gli", "el", "los",
            "las", "de", "het", "den", "det", "o", "a", "os", "as", "η", "ο", "το", "οι", "τα", "την",
            "τον", "της", "του"}


_PLAIN_STOP_KEYS = None   # filled below, once canonical_key exists


def _is_head_word(word: str) -> bool:
    """An organisational word, alone ("Collective") or ending a compound ("Klimabündnis")."""
    return word in HEAD_TO_TYPE or any(word.endswith(h) and len(word) >= len(h) + 3
                                       for h in COMPOUND_HEADS)


def _split_conjoined(toks: list, conjunctions: Set[str] = CONJUNCTIONS) -> List[list]:
    """Split "Migrant Rights Coalition and the Rete Antirazzista" into two names.

    Only splits when the part before the conjunction already ends in an
    organisational head noun, so "Centre for Gender and Development" stays whole.
    """
    for k, t in enumerate(toks):
        if t.group(0).casefold() in conjunctions and 0 < k < len(toks) - 1:
            left = toks[:k]
            last = left[-1].group(0).casefold()
            right = toks[k + 1:]
            while right and right[0].group(0).casefold() in CONNECTORS:
                right = right[1:]
            right_head = bool(right) and _is_head_word(right[0].group(0).casefold())
            # "X Centre ... and the Y Coalition": an article after the conjunction, with an
            # organisational word on both sides, starts a second name.
            article_split = (k + 1 < len(toks) and toks[k + 1].group(0).casefold() in ARTICLES
                             and any(_is_head_word(t.group(0).casefold()) for t in left)
                             and any(_is_head_word(t.group(0).casefold()) for t in right))
            if (_is_head_word(last) or (last in TRAILING_TO_TYPE and len(left) >= 2) or right_head
                    or article_split):
                return [left] + (_split_conjoined(right, conjunctions) if right else [])
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


# An article right after two capitalised words starts a new name when what follows is
# itself a name with an organisational word ("Kelvingrove Park the Glasgow Prisoner
# Solidarity group"), but not in "Take Back the University" or "Break the Chain".
_NEW_NAME_ARTICLES = {"the", "la", "le", "les", "el", "los", "las", "il", "gli", "het"}


def _starts_named_org(toks: list, k: int) -> bool:
    caps = []
    while k < len(toks) and _is_cap(toks[k].group(0)):
        caps.append(toks[k].group(0).casefold())
        k += 1
    return len(caps) >= 2 and any(_is_head_word(c) for c in caps) and not all(_is_head_word(c) for c in caps)


_INTO_PREPOSITIONS = {"in", "into", "an", "auf", "zu", "nach", "ins", "dans", "nel", "à", "a", "en", "naar", "op"}
_APPOS_TAIL = re.compile(r"([^\W\d_]+)[\s\"“«‘]*$")
_APPOS_STEMS = ("chiamat", "denominat", "llamad", "denominad", "appel", "nomm")


def _place_follows(text: str, end: int) -> bool:
    """", Edinburgh" / ", Paris": a comma and a place right after position `end`."""
    m = re.match(r"\s*,\s*([^\W\d_][\w’'\-]*)", text[end:end + 40])
    return bool(m) and canonical_key(m.group(1)) in PLACES_KEYS


def _is_generic_name(surface: str) -> bool:
    """One organisational word and otherwise generic words: "Working Group", "LOCAL GROUPS",
    "Media Team". Two different heads ("Conseil national de la Résistance") may be a name."""
    words = [strip_elision(w).casefold() for w in TOKEN_RE.findall(surface)]
    content = [w for w in words if w not in CONNECTORS]
    heads = {w for w in content if w in HEAD_TO_TYPE or w in TRAILING_TO_TYPE}
    return (bool(content) and len(heads) <= 1
            and all(w in heads or w in GENERIC or w in GENERIC_MULTI for w in content))


def _is_event_name(surface: str) -> bool:
    """An event, not an organisation: the first or last word is an event word ("Shell AGM",
    "Rassemblement des Glières"), or a conference word is anywhere ("World Congress on
    Climate Justice")."""
    content = [w.casefold() for w in TOKEN_RE.findall(surface) if w.casefold() not in CONNECTORS]
    return bool(content) and (content[0] in EVENT_WORDS or content[-1] in EVENT_WORDS
                              or any(w in CONFERENCE_WORDS for w in content))


_VERB_WORDS = {"ne", "pas", "n", "est", "sont", "était", "être", "devait", "doit", "peut", "is", "are",
               "was", "were", "be", "has", "have", "will", "ist", "sind", "war", "nicht", "è", "sono",
               "es", "son", "está", "están", "não", "é", "kan", "kann", "can"}


def _has_verb_words(surface: str) -> bool:
    """A lower-case auxiliary, copula or negation: "Résistance ne devait pas être"."""
    return any(w in _VERB_WORDS for w in TOKEN_RE.findall(surface)[1:])


def _mostly_lowercase(surface: str) -> bool:
    words = TOKEN_RE.findall(surface)[1:]
    content = [w for w in words if w.casefold() not in CONNECTORS]
    lower = sum(not w[:1].isupper() for w in content)
    return lower >= 2 and lower / len(content) > 0.5


def _spells(acr: str, words: List[str], skips: int = 1) -> bool:
    """Can `acr` be spelt from `words`? Each word contributes its initial (lower-case
    connectors and up to `skips` capitalised words may contribute nothing), and
    may add further letters from inside it, for compounds such as
    "Klimagerechtigkeit" -> KG."""
    if not acr:
        return all(not w[:1].isupper() for w in words) or skips >= sum(w[:1].isupper() for w in words)
    if not words:
        return False
    w, rest = words[0], words[1:]
    key = canonical_key(w).replace(" ", "")
    if key[:1] == acr[0]:
        # consume the initial, then optionally more letters from inside the word
        i, j = 1, 1
        while True:
            if _spells(acr[j:], rest, skips):
                return True
            pos = key.find(acr[j], i) if j < len(acr) else -1
            if pos < 0:
                break
            i, j = pos + 1, j + 1
    if not w[:1].isupper():
        return _spells(acr, rest, skips)
    return skips > 0 and _spells(acr, rest, skips - 1)


def acronym_pairs(text: str) -> List[Tuple[str, str]]:
    """Find "Long Name (ACR)" definitions whose words plausibly spell the acronym
    ("Independent Workers Union of Great Britain (IWGB)", "Kollektiv
    Klimagerechtigkeit (KKG)"). The long form starts at the nearest word that
    begins with the acronym's first letter and can spell it."""
    out = []
    for m in ACRONYM_DEF_RE.finditer(text):
        long_form, acr = m.group(1).strip(), m.group(2).replace(".", "")
        # "de l'Union Juive Française pour la Paix (UJFP)": the elided article is not part of it
        words = [strip_elision(w) for w in re.split(r"[\s\-]+", long_form) if w]
        letters = canonical_key(acr).replace(" ", "")
        for i in range(len(words) - 1, max(-1, len(words) - len(acr) - 5), -1):
            tail = words[i:]
            if len(tail) < 2 or not tail[0][:1].isupper():
                continue
            if canonical_key(tail[0])[:1] == letters[:1] and _spells(letters, tail):
                out.append((tidy_surface(" ".join(tail)), acr))
                break
    return out


def block_hash(text: str) -> str:
    return hashlib.sha1(canonical_key(text).encode("utf-8")).hexdigest()[:16]


# Accent-free forms of month names, function words, generic words and lone
# organisational words ("Université", "Association"), which are never names on their own.
_PLAIN_STOP_KEYS = {canonical_key(w) for w in (MONTHS | LEADING_STRIP | GENERIC | set(HEAD_TO_TYPE))}
