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
from collections import Counter, defaultdict
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from places import LANGUAGE_NAMES, PLACES

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
}
CONJUNCTIONS = {"and", "&", "e", "y", "et", "und", "en", "og", "och", "și", "si", "ja",
                "és", "ir", "και", "та", "і", "и", "eta"}
# "and" words that are something else in other languages (Italian article "i", English "in",
# Romance preposition "a"): conjunctions only on paragraphs detected as these languages.
CONJUNCTIONS_BY_LANG = {"pl": {"i"}, "hr": {"i"}, "sr": {"i"}, "sl": {"in"}, "cs": {"a"}, "sk": {"a"}}
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
}
MONTHS = {
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
}
LINK_TEXT_JUNK = re.compile(
    r"^(here|click|read|more|link|website|site|homepage|home|this|download|pdf|"
    r"facebook|twitter|x|instagram|youtube|linkedin|tiktok|telegram|mastodon|bluesky|"
    r"share|tweet|email|e-mail|contact|donate|don|faire un don|dona|spenden|subscribe|login|log in|sign up|next|"
    r"previous|back|top|menu|search|privacy|cookie|cookies|imprint|impressum|"
    r"source|via|en|it|es|fr|de|pt)\b",
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
    first = words[0].casefold().strip("’'\"“«")
    first = re.split(r"[’']", first)[0] if re.match(r"\w+[’']\w", first) else first
    if first in CTA_WORDS or words[-1].casefold().strip(".!") in {"here", "ici", "qui", "aquí", "hier"}:
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
    r"nach|bei|im|am|aus|nahe|vor|auf|bij|te|op|nabij|naar|i|på|vid|nära|ved|nær|a|cerca\s+de|"
    r"vicino\s+a|presso|em|perto\s+de)\s+(?:the\s+|der\s+|den\s+|la\s+|le\s+|l['’])?$", re.I)
PERSON_APPOS = re.compile(r"(?<!\w)(?:of|de|du|des|di|del|della|dei|von|vom|do|da|member|membre|"
                          r"membro|miembro|mitglied|militante?s?|activist[ae]?s?|attivist[ae]|"
                          r"founder|fondat\w*|fundador\w*|co-?founder|organi[sz]er|organisatri\w*|"
                          r"spokes\w*|porte-parole|portavoce|coordinat\w*)(?!\w)", re.I)
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


def split_sentences(text: str) -> List[Tuple[int, int]]:
    """Character spans of sentences (regex splitter; good enough for context windows)."""
    spans, start = [], 0
    for m in SENT_SPLIT_RE.finditer(text):
        spans.append((start, m.start() + len(m.group(0).rstrip())))
        start = m.end()
    spans.append((start, len(text)))
    return [(s, e) for s, e in spans if e > s]


_KEEP_S = {"news", "always", "towards", "perhaps", "texas", "paris", "athens", "wales",
           "lens", "mars", "kings", "james", "nicholas", "thomas", "lucas", "marcus"}


def canonical_key(name: str) -> str:
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c)).casefold()
    n = n.replace("&", " and ").replace("’", "'")
    n = re.sub(r"'s\b", "", n)
    n = re.sub(r"[^\w\s-]", " ", n)
    toks = [t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is"))
            and t not in _KEEP_S else t for t in n.split()]
    while toks and toks[0] in {"the", "il", "lo", "la", "le", "gli", "l", "el", "los", "las",
                               "les", "der", "die", "das", "o", "a", "os", "as", "η", "ο", "το", "οι", "τα"}:
        toks = toks[1:]
    return " ".join(toks)


def tidy_surface(name: str) -> str:
    name = clean_ws(name).strip(" \t\"'“”‘’«»()[]{}:;,.-–—|/")
    name = re.sub(r"\s*[\[(][^\])]*$", "", name)          # unclosed "[DE" / "(UK"
    name = re.sub(r"^[^\w\"“«(]+|[^\w.)\"”»]+$", "", name).strip()  # emoji, bullets, "!"
    name = re.sub(r"^(?:the|The|THE|De|Het|Den|Det|Die|Der|Das)\s+(?=\S+\s+\S)", "", name)
    # a trailing article picked up from the next phrase: "GROUPE ÉCOUTE Le", "Kollektiv X Die"
    name = re.sub(r"\s+(?:Le|La|Les|L|The|Die|Der|Das|De|Het|El|Il|Lo|Los|Las)$", "", name)
    name = re.sub(r"[’']s$", "", name)
    return name


# --------------------------------------------------------------------------
# Themes
# --------------------------------------------------------------------------
URL_LIKE = re.compile(r"(?:https?://|www\.)\S+|\b[\w.-]+\.(?:org|com|net|eu|info|de|fr|nl|be|at|it|es|"
                      r"dk|se|uk|ch|pt|pl|io|noblogs\.org|blackblogs\.org)(?:/\S*)?", re.I)


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
        hits: Dict[str, List[Tuple[int, int, str]]] = {}
        # Words inside web addresses ("…/anti-colonial-attack/", "mondialiantirazzisti.org")
        # are not uses of a theme.
        urls = [(m.start(), m.end()) for m in URL_LIKE.finditer(text)]
        for theme, pats in self.themes.items():
            found = []
            for p in pats:
                found.extend((m.start(), m.end(), m.group(0)) for m in p.finditer(text)
                             if not any(a <= m.start() < b for a, b in urls))
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
    lang = (soup.html.get("lang") if soup.html else "") or ""
    if not lang:
        loc = soup.select_one('meta[property="og:locale"]')
        lang = loc.get("content", "") if loc else ""
    for t in soup(DROP_TAGS):
        t.decompose()
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for t in soup.select("form label, form legend"):
        t.decompose()
    for t in soup.select('[id*="cookie" i], [class*="cookie" i], [id*="consent" i], '
                         '[class*="consent" i], [id*="cmplz" i], [class*="cmplz" i], '
                         '[id*="borlabs" i], [class*="borlabs" i], [id*="gdpr" i], [class*="gdpr" i]'):
        if t.name not in ("html", "body"):
            t.decompose()
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


def _installed(model: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(model) is not None


def detect_language(text: str, declared: str = "") -> str:
    """Two-letter language code: the page's declared language, else a guess from
    function words."""
    declared = (declared or "").lower().replace("_", "-").split("-")[0]
    if re.fullmatch(r"[a-z]{2}", declared):
        return declared
    words = re.findall(r"[^\W\d_]+", text.lower())[:3000]
    if not words:
        return "en"
    scores = {lang: sum(w in hints for w in words) for lang, hints in _LANG_HINTS.items()}
    return max(scores, key=scores.get)


class OrgExtractor:
    def __init__(self, watchlist: Watchlist, stoplist: Tuple[Set[str], List[re.Pattern]],
                 spacy_model: Optional[str], use_links: bool = True, ner_size: str = "sm"):
        """spacy_model: a model name used for every page, "auto" (one model per page
        language, see SPACY_MODELS), or "none". ner_size: sm / md / lg models in
        auto mode; md and lg are more precise but miss more unusual group names."""
        self.watchlist = watchlist
        self.stop_exact, self.stop_pats = stoplist
        self.use_links = use_links
        self.spacy_model = (spacy_model or "none").strip()
        self.ner_size = ner_size
        self._models: Dict[str, object] = {}
        self.common_words: Set[str] = set()
        self.mid_caps: Optional[Set[str]] = None   # None until prepare_corpus() runs
        self.missing_models: Set[str] = set()
        self.label_votes: Dict[str, Counter] = defaultdict(Counter)
        self.locative: Dict[str, List[int]] = defaultdict(lambda: [0, 0])   # key -> [after "in/à/bei…", total]
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
                self._models[name] = None
        return self._models[name]

    def model_for(self, lang: str = "en"):
        if self.spacy_model.lower() == "none":
            return None
        if self.spacy_model.lower() != "auto":
            return self._load(self.spacy_model)
        nlp = None
        if lang in SPACY_MODELS:   # the requested size if installed, else the small model
            base = SPACY_MODELS[lang][:-3]
            for size in dict.fromkeys((self.ner_size, "sm")):
                if _installed(f"{base}_{size}"):
                    nlp = self._load(f"{base}_{size}")
                    break
        if nlp is None and lang != "en":
            # Never fall back to the English model on another language: it tags
            # ordinary capitalised words (every German noun) as organisations.
            nlp = self._load("xx_ent_wiki_sm") if _installed("xx_ent_wiki_sm") else None
            if nlp is None:
                self.missing_models.add(SPACY_MODELS.get(lang, f"a spaCy model for '{lang}'"))
            return nlp
        return nlp or self._load("en_core_web_sm")

    @property
    def nlp(self):
        """English (or the fixed) model; kept for callers that do not pass a language."""
        return self.model_for("en")

    def pipe(self, texts: List[str], lang: str = "en") -> list:
        """NER docs for `texts`. In auto mode each paragraph long enough to tell gets
        its own language (pages mix languages: translations, quotes, song sheets);
        short ones take the page language `lang`."""
        per_text = ([detect_language(t) if len(t.split()) >= 8 else lang for t in texts]
                    if self.spacy_model.lower() == "auto" else [lang] * len(texts))
        docs: list = [None] * len(texts)
        for code in dict.fromkeys(per_text):
            nlp = self.model_for(code)
            if nlp is None:
                continue
            idx = [i for i, c in enumerate(per_text) if c == code]
            for i, doc in zip(idx, nlp.pipe([texts[i] for i in idx], batch_size=64)):
                docs[i] = doc
                for ent in doc.ents:  # every label a name receives anywhere on the site
                    self.label_votes[canonical_key(tidy_surface(ent.text))][ent.label_] += 1
        return docs

    # ------------------------------------------------------- corpus statistics
    def prepare_corpus(self, texts: Iterable[str]) -> None:
        """Learn which words this site writes in lower case. A name made only of
        such words ("Climate Justice", "Hydrogen", "Approach") is ordinary
        language unless a rule with an organisational noun supports it."""
        lower: Counter = Counter()
        upper: Counter = Counter()
        for text in texts:
            for s, e in split_sentences(text):
                for i, m in enumerate(TOKEN_RE.finditer(text, s, e)):
                    w = m.group(0)
                    if i == 0 or w.isupper():
                        continue  # sentence-initial and ALL-CAPS words say nothing about case
                    (upper if w[:1].isupper() else lower)[w.casefold()] += 1
        self.common_words = {w for w, n in lower.items() if n >= 2 and n >= 0.5 * upper.get(w, 0)}
        self.mid_caps = set(upper)

    def capitalised_in_running_text(self, surface: str) -> bool:
        """Does the name have a word the site capitalises mid-sentence (a proper
        noun), or an acronym / camelCase / digit? Words capitalised only at the
        start of headings and lines ("Réveil", "Accessible") are not names."""
        words = [w for w in TOKEN_RE.findall(surface) if w.casefold() not in CONNECTORS]
        return any(w.casefold() in self.mid_caps or re.search(r"\d|[a-z][A-Z]", w)
                   or (w.isupper() and len(w) <= 5) for w in words)

    def is_ordinary_words(self, surface: str) -> bool:
        words = [w.casefold().strip("’'") for w in TOKEN_RE.findall(surface)]
        content = [w for w in words if w not in CONNECTORS]
        if not content:
            return True
        if re.fullmatch(r"(?:[A-Z][a-z]?\d*){1,4}", surface) and re.search(r"\d", surface):
            return True   # chemical formulae: CO2, CH4, H2O
        return all(w in self.common_words or w in LEADING_STRIP or w in GENERIC for w in content)

    def vet(self, key: str, methods: Iterable[str]) -> bool:
        """Site-wide check for names found only by NER, links or appositives:
        reject those that spaCy more often labels as a place, person, law, event..."""
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
        if typ is None:   # a compound ending in an organisational word: "Klimakollektivet"
            for t in content:
                for head, htyp in COMPOUND_HEADS.items():
                    if t.endswith(head) and len(t) >= len(head) + 3:
                        return htyp   # the prefix ("Klima-") makes it distinctive
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

        lang = doc.lang_ if doc is not None and doc.lang_ != "xx" else (
            detect_language(text) if len(text.split()) >= 8 else "")
        for s, e, _ in self._spans(text, lang):
            s, e = self._trim(text, s, e)
            surface = text[s:e]
            toks = [t.group(0) for t in TOKEN_RE.finditer(surface)]
            # Appositive check uses the trimmed span.
            before = text[max(0, s - 80):s]
            after = text[e:e + 90]
            pre = self.pre_appos.search(before)
            post = self.post_appos.match(after)
            if post and PERSON_APPOS.search(post.group(0)):
                post = None   # "Claire, une militante de l'association X": a person, not an org
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
            german = doc.lang_ == "de" and doc.has_annotation("POS")
            for ent in doc.ents:
                if ent.label_ not in ("ORG", "NORP_ORG"):
                    continue
                if (german and len(ent) == 1 and ent[0].pos_ in {"NOUN", "ADJ", "VERB", "ADV", "PRON"}
                        and not ent.text.isupper()):
                    continue   # a common German noun ("Gemeinschaft", "Küche"), not a name
                s, e = self._trim(text, ent.start_char, ent.end_char)
                surface = text[s:e]
                toks = [t.group(0) for t in TOKEN_RE.finditer(surface)]
                stats = self.locative[canonical_key(tidy_surface(surface))]
                stats[1] += 1
                if LOCATIVE_BEFORE.search(text[max(0, s - 25):s]):
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
            first = m.surface.split(" ", 1)[0].casefold() if m.surface else ""
            if first in CTA_WORDS and "watchlist" not in m.methods:
                continue   # "Sign Up Now", "Join the ...": an instruction
            if not m.key:
                m.key = canonical_key(m.surface)
            m.key = self.watchlist.canonical.get(m.key, m.key)
            if "watchlist" not in m.methods:
                if not self.plausible_name(m.surface) or self.is_stopped(m.key, m.surface):
                    continue
                words = [canonical_key(w) for w in TOKEN_RE.findall(m.surface)]
                if words and all(w in LANGUAGE_NAMES for w in words):
                    continue   # a language menu: "Deutsch English Español Français Italiano"
                if not m.methods & {"pattern", "appositive"} and m.key in PLACES:
                    continue   # a bare place name: "Genève", "Hambach", "Suisse"
                if "pattern" not in m.methods and not self._head_type(TOKEN_RE.findall(m.surface)):
                    if self.is_ordinary_words(m.surface):
                        continue
                    if (m.methods == {"ner"} and self.mid_caps is not None
                            and not self.capitalised_in_running_text(m.surface)):
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
        words = [w for w in re.split(r"[\s\-]+", long_form) if w]
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
