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
  website     a name written in the same paragraph as a web address whose
              domain spells it ("Code Rood, camp action climat …
              https://code-rood.org", "Ende Gelände … ende-gelaende.org")

Acronyms defined in the text ("Black Lives Matter (BLM)") are merged with
their long form.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter, OrderedDict, defaultdict
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from crawler import safe_join

from places import COUNTRY_CODES, FIRST_NAMES, LANGUAGE_NAMES, PLACES

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
    # "Mouvement contre le Racisme et pour l'Amitié entre les Peuples"
    "entre",
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
    "blockade", "blockades", "blocage", "blokkade", "blockaden", "bloqueo", "blocco",
}
# Event words that end an event's name but may begin an organiser's ("AMS camp", "Bonn
# Climate Camp", "Anarchist Bookfair", "Alter tour"; but "Camp Climat Grenoble" is a group).
EVENT_LAST_WORDS = {"camp", "camps", "tour", "biketour", "fair", "bookfair", "march", "marche", "day",
                    "actionday", "aktionstag"}
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
# A street name followed by a house number: "Heidenheimerstr. 55", "Havnegade 12".
_STREET_NUMBER = re.compile(r"(?:stra(?:ß|ss)e|str|gasse|weg|gade|vej|straat|laan|gatan|vägen)\.?$", re.I)
_HOUSE_NUMBER_AFTER = re.compile(r"^\.?\s*(?!(?:19|20)\d\d\b)\d{1,4}[a-z]?\b(?!\.\d)")
# A house number before the name and a postcode or ", Town" after it: "138/140 Pleasance, Edinburgh".
_ADDRESS_NUMBER = re.compile(r"(?<![\w.])(?!(?:19|20)\d\d\b)\d{1,5}[a-z]?(?:[/-]\d{1,5})?,?\s*$")
_POSTCODE_AFTER = re.compile(r"^\s*(?:,\s*)?(?:\d[A-Z]{2}\b|\d{4,5}\b|[A-Z]{1,2}\d{1,2}[A-Z]?\s+\d[A-Z]{2}\b)")
_PAGINATION_BEFORE = re.compile(r"(?<!\w)(?:suivant|précédent|next|previous|prev|weiter|zurück|siguiente|"
                                r"anterior|successivo|precedente|volgende|vorige)\s*[:»›>]?\s*$", re.I)
# Standalone table marks ("X", "N. A.", "M. A."): a table of ticks, not running text.
# Movement descriptors ("the climate movement X", "le mouvement X") and abstract nouns
# ("Tokenism", "Kapitalismus") for _generic_movement_pre().
_MOVEMENT_WORDS = {"movement", "movements", "mouvement", "mouvements", "bewegung", "beweging",
                   "movimento", "movimiento", "moviment", "bevægelse", "bevægelsen", "rörelse",
                   "rörelsen", "bevegelse", "bevegelsen", "ruch", "mișcare", "mișcarea", "κίνημα"}
_ISM = re.compile(r"(?:ism|isme|ismus|ismo|izm|ismi|ισμός|ισμος)$", re.I)
# "Germany and in the port of Hamburg", "Rheinmetall and the military", "X et l'armée":
# a conjunction, then a lower-case preposition or article and a lower-case word.
# ("of" is not a conjunction here: "Friends of the Earth".)
_CONJ_PHRASE = re.compile(
    r"\s+(?:and|or|et|ou|und|oder|y|e|en|og|och|&)\s+"
    r"(?:(?:in|on|at|to|for|with|from|by|into|the|a|an|this|that|these|those|its|their|his|her|our|"
    r"other|all|some|le|la|les|un|une|des|du|de|à|au|aux|dans|sur|pour|avec|par|der|die|das|den|"
    r"dem|ein|eine|einen|im|am|zum|zur|mit|für|auf|bei|het|een|op|il|lo|gli|nel|nella|del|al|el|"
    r"los|las)\s+|l['’])[^\W\d_A-ZÀ-Ý]")
# Lower-case words after a single capitalised word that are not nouns: function words,
# auxiliaries and common verbs ("Greenpeace said", "Shell must fall", "AIG are").
_NOT_NOUN_AFTER = frozenset("""
and or nor but of in on at to for with from by as is are was were be been being has have had
will would can could must should may might shall do does did also only not no which who whom
whose that this these those than then there here it its they their them we our you your he she
his her him said says say versus vs via into onto over under about after before during between
against without within near up down out off so if when where while because all any each every
both either more most much many some such very just still even yet already again now today
tomorrow tonight per around through across along toward towards since until till upon among
amongst together alone itself themselves too et
en op aan van voor met uit bij naar door zijn waren heeft hebben wordt worden de het een die
dat deze dit ook niet
og och eller i på til till med av af fra från för er är var har som den det ett
bei an der die das und im am zu von mit für auf aus nach gegen
à au aux du des le la les sur pour avec par dans contre près chez qui que
di del della il al y el los las que con por para contra
""".split())
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
    # sentence openers and headings before a name: "Neither the European Union nor ...",
    # "Auch Attac ...", "Selon Greenpeace", "INTRODUCTION Solidarity ..." ("via" is not
    # here: "Via Campesina")
    "neither", "across", "ook", "auch", "selon", "laut", "introduction", "einleitung", "bei", "chez",
}
_JUNK_OPENERS = {"neither", "across", "ook", "auch", "selon", "laut", "introduction", "einleitung",
                 "bei", "chez", "unlike", "während", "wenn"}
# Function and interface words that end a span but not a name: "Casino Group A",
# "British Film Institute At", "Ende Gelände Logo", "Attac Website" (see _trim, trim_junk).
TRAILING_STRIP = {"no", "in", "a", "an", "at", "the", "of", "und", "et", "website", "homepage",
                  "leer", "logo",
                  # a capitalised word of the next phrase ("Climate Bonds Initiative Is",
                  # "Réseau Roosevelt Du", "Association Litu Pour")
                  "is", "are", "was", "to", "for", "with", "für", "pour", "du", "des", "von", "y", "e"}
# A role before the organisation: "Presseteam von Extinction Rebellion", "Member of the
# European Parliament", "Mitarbeiterin bei Attac": the span is cut to the organisation.
ROLE_OF = re.compile(
    r"(?i)(?:president[ea]?|présidente?|präsident(?:in)?|member|mitglied|membre|director|directeur|"
    r"directrice|direktor(?:in)?|coordinator|coordinat(?:eur|rice)|koordinator(?:in)?|spokesperson|"
    r"sprecher|sprecherin|co-?founder|founder|fondat(?:eur|rice)|gründer(?:in)?|cofundador|cofundadora|"
    r"mitarbeiter|mitarbeiterin|presseteam|chair|head|researcher|professor(?:in)?|vorsitzende[r]?|"
    r"secretary|secrétaire)"
    r"\s+(?:of|der|des|von|vom|de|da|do|du|di|del|della|at|bei|beim)\s+"
    r"(?:(?:the|la|le|les|l['’]|el|il|die|der|des)\s*)?(?=[^\W\d_])")
# LEADING_STRIP words that also begin real names: dropped only at the start of a sentence
# or line (see OrgExtractor._trim). NAV_OPENERS are menu and caption words, which may be
# dropped even when a single name word is left ("Home Greenpeace" -> "Greenpeace").
NAME_OPENERS = {"via", "new", "home", "more", "we", "il", "la", "le", "les", "el", "lo", "los", "las",
                "atelier", "visit", "visite", "press", "news"}
NAV_OPENERS = {"home", "news", "press", "visit", "visite", "more"}
ACTIVITY_OPENERS = {"atelier", "visit", "visite"}
# A name made only of heads + these words is too generic to keep.
GENERIC = {
    "partner", "partners", "member", "members", "main", "other", "related", "local",
    "national", "international", "regional", "global", "european", "our", "contact",
    # not "home": "Home Office" (a lone "Home" is still rejected as a menu word)
    "about", "menu", "more", "new", "all", "general", "annual", "public",
    "private", "working", "affiliated",
    # document / event furniture that NER tags as ORG, notably in PDFs
    "workshop", "workshops", "session", "sessions", "programme", "program", "call-out",
    "callout", "statement", "report", "agenda", "schedule", "timetable", "camp", "day",
    "week", "plenary", "panel", "talk", "talks", "training", "briefing", "appendix",
    "morning", "closing", "opening", "evening", "weekly", "monthly", "assembly", "assemblies",
    "summer", "winter", "spring", "autumn", "faq", "navigation", "newsletter", "kontakt", "impressum",
    "press", "release", "media", "team", "guide", "handbook", "toolkit", "policy", "values",
    "webinar", "webinars", "recording", "recordings", "video", "videos", "resource", "resources",
    # German nouns are always capitalised, so these never show up as lower-case words
    "aktion", "aktionen", "veranstaltung", "veranstaltungen", "termine", "upcoming",
    "external", "useful", "further", "additional",
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
    # camp roles: "Arrestee Group", "Affinity Group", "Buddy Group"
    "arrestee", "arrestees", "affinity", "buddy", "buddies", "bezugsgruppe",
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
    # abbreviations and other languages, as calendar cells write them ("DEC 2024", "JUIL")
    "jan", "feb", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "janv", "févr",
    "avr", "juil", "déc", "janvier", "février", "avril", "juin", "juillet", "août", "septembre",
    "octobre", "novembre", "décembre", "januar", "februar", "märz", "juni", "juli", "oktober",
    "dezember", "gennaio", "febbraio", "aprile", "maggio", "giugno", "luglio", "settembre", "ottobre",
    "dicembre", "enero", "febrero", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre",
    "diciembre",
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
    # Chat, pad, form and other tool hosts: a link to a Signal group or a shared pad
    # names the tool, not an organisation ("Matrix", "Signal", "Cryptpad", "Framaforms").
    "matrix.org", "matrix.to", "element.io", "signal.org", "signal.group", "signal.me",
    "discord.com", "discord.gg", "slack.com", "zoom.us", "meet.jit.si", "jit.si", "bbb.de",
    "cryptpad.fr", "cryptpad.org", "cryptpad.digitalcourage.de", "framaforms.org", "framadate.org",
    "framapad.org", "framacalc.org", "framagenda.org", "framalink.org", "framaliste.org", "framateam.org",
    "pad.riseup.net", "pad.systemli.org", "yopad.eu", "nuudel.digitalcourage.de", "docs.google.com",
    "forms.gle", "drive.google.com", "calendar.google.com", "linktr.ee", "bit.ly", "tinyurl.com",
}
# Hosts of pads and forms under any domain: "pad.example.org", "etherpad.x.net", "forms.y.org"
_TOOL_HOST = re.compile(r"^(?:pads?|etherpad|hedgedoc|cryptpad|forms?|framaforms|poll|nuudel|chat|matrix|"
                        r"element|jitsi|meet|bbb|visio|cloud|nextcloud|lists?|mailman)\.", re.I)


def is_skip_host(host: str, subdomains_of_platforms: bool = True) -> bool:
    """Social media, chat, pad and form hosts, whose name is not an organisation's.
    With subdomains_of_platforms=False, "group.noblogs.org" is not skipped as a
    subdomain of a skipped platform (a blog host gives each group its own name)."""
    h = host.lower().removeprefix("www.")
    if _TOOL_HOST.match(h):
        return True
    return any(h == d or (h.endswith("." + d) and (subdomains_of_platforms or d not in _SUBDOMAIN_PLATFORMS))
               for d in LINK_SKIP_DOMAINS)


def link_text_is_name(text: str, domain: str) -> bool:
    """Is this link text plausibly an organisation's name (not an instruction,
    sentence, article title or social-media button)?"""
    domain = domain.lower().removeprefix("www.")
    if is_skip_host(domain):
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
    r"(?<!\w)(?:in|at|near|around|outside|à|au|aux|en|près\s+de|près\s+d['’]|vers|sur|"
    r"nach|bei|im|am|aus|nahe|vor|auf|bij|te|op|nabij|naar|på|vid|nära|ved|nær|cerca\s+de|"
    r"vicino\s+a|presso|em|perto\s+de)\s+(?:the\s+|der\s+|den\s+|la\s+|le\s+|l['’])?$", re.I)
# "from" and "to" are place prepositions too ("1 mile from Kinneil", "direct to Falkirk"),
# but not after people, messages or giving: "activists from Greenpeace", "support from
# Attac", "donate to Sea-Watch", "bedankt sich bei X".
LOCATIVE_WEAK = re.compile(r"(?<!\w)(?:from|to)\s+(?:the\s+)?$", re.I)
ACTOR_PREPOSITION = re.compile(
    r"(?<!\w)(?:(?:activists?|members?|people|comrades|support|statements?|messages?|letters?|speakers?|"
    r"delegat\w*|representatives?|solidarity|calls?|invitations?|friends|volunteers?|organi[sz]ers?|"
    r"campaigners?|staff|team|groups?|donations?|funding|grants?|colleagues|allies|someone|"
    r"anyone|contributions?)\s+from"
    r"|(?:donate|donating|give|giving|send|sending|write|writing|join|joining|belong|belongs|"
    r"thanks?|grateful|solidarity|support|talk|speak|letters?|messages?|donations?|apply|"
    r"complaint|petition|open\s+letter|membership)\s+to"
    r"|(?:sich|danke|dank|bedanken|arbeite[nt]?|engagier\w*|aktiv|mitglied|spenden?)\s+bei)"
    r"\s+(?:the\s+)?$", re.I)
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
    repeated_pages_skipped: int = 0   # for PDFs: pages skipped as copies of an earlier page


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
# German umlauts written as ae / oe / ue ("Ende Gelaende", "Gruene", "Koeln") are folded
# to the plain vowel, as "Gelände" becomes "gelande" when accents are removed. Only in the
# umlaut sense: between consonants and not near the end of a word, so "queer", "blue",
# "fuel" and "Michael" keep theirs, and not in the Romance "-uell-" ("actuelle", "manuell").
# On the user's real outputs (11,652 names and words) every merge this makes is a true
# umlaut pair (Gelaende/Gelände, Koeln/Köln, foerdern/fördern, Fruehling/Frühling).
_UMLAUT_VARIANT = re.compile(r"(?<=[bcdfghjklmnprstvwxz])(?<![qg])(?:([ao])e|(u)e(?!ll))"
                             r"(?=[bcdfghjklmnprstvwxz][a-z])")


def strip_elision(token: str) -> str:
    """"d’Attac" -> "Attac", "dell'ARCI" -> "ARCI" (a token without elision is returned as is)."""
    return ELIDED_PREFIX.sub("", token, count=1)


_CAMEL_BOUNDARY = re.compile(r"(?<=[a-zß-ÿ])(?=[A-ZÀ-Þ])")


def _split_camel(token: str) -> str:
    """"ShellMustFall" -> "Shell Must Fall", "ClimateCampScotland" -> "Climate Camp Scotland":
    a word with two or more lower-to-upper-case changes is several words run together.
    One change is left alone ("GroenFront", "TotalEnergies", "KontraIAA")."""
    return _CAMEL_BOUNDARY.sub(" ", token) if len(_CAMEL_BOUNDARY.findall(token)) >= 2 else token


def canonical_key(name: str) -> str:
    name = " ".join(_split_camel(t) for t in name.split()) if _CAMEL_BOUNDARY.search(name) else name
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c)).casefold()
    n = n.replace("&", " and ").replace("’", "'")
    n = re.sub(r"'s\b", "", n)
    n = _ELIDED_IN_KEY.sub("", n)
    # Hyphens separate words like spaces do, so "ANV-COP21" and "ANV Cop21", "Code-Rood"
    # and "Code Rood", "Vélo-city" and "Velo city" have one key in every module. A first
    # word joined by a hyphen is not an article ("A-camp", "La-Bas").
    glued = bool(re.match(r"\s*[^\W_]+-[^\W_]", n))
    n = re.sub(r"[^\w\s]", " ", n)
    toks = [t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is", "gas"))
            and t not in _KEEP_S else t for t in n.split()]
    toks = [_UMLAUT_VARIANT.sub(lambda m: m.group(1) or m.group(2), t) if len(t) >= 5 else t for t in toks]
    while not glued and toks and toks[0] in {"the", "il", "lo", "la", "le", "gli", "l", "el", "los", "las",
                               "les", "der", "die", "das", "o", "a", "os", "as", "η", "ο", "το", "οι", "τα"}:
        toks = toks[1:]
    return " ".join(toks)


def tidy_surface(name: str) -> str:
    name = clean_ws(name).strip(" \t\"'“”‘’«»()[]{}:;,.-–—|/")
    name = re.sub(r"(?<=[^\W\d_])-\s+(?=[^\W\d_])", "-", name)   # a PDF line break: "SOUS-LES- COTES"
    name = re.sub(r"\s*[\[(][^\])]*$", "", name)          # unclosed "[DE" / "(UK"
    name = re.sub(r"^[^\[\]()]*[\])]\s*/?\s*(?=\w)", "", name)  # "France]/ Transnational": a fragment
    name = re.sub(r"^[^\w\"“«(]+|[^\w.)\"”»]+$", "", name).strip()  # emoji, bullets, "!"
    # a lower-case elided article or preposition: "d’Extinction Rebellion" (not "L'Oréal")
    name = re.sub(r"^" + _ELISIONS + r"['’](?=[^\W\d_])", "", name)
    name = re.sub(r"^(?:the|The|THE|De|Het|Den|Det|Die|Der|Das)\s+(?=\S+\s+\S)", "", name)
    # a trailing lower-case connector picked up from the next phrase: "ZAD de", "Friends of"
    name = re.sub(r"(?:\s+(?:de|des|du|d|of|and|et|und|di|del|della|la|le|les|von|van|der|for|"
                  r"pour|für|à|a|au|aux|en|in|on|at|or|ou|oder|y|e|the|die|das|el|il|between|entre|"
                  r"zwischen|tra|fra|with|avec|mit|con|by|par|from))+$", "", name)
    # a trailing article picked up from the next phrase: "GROUPE ÉCOUTE Le", "Kollektiv X Die"
    name = re.sub(r"\s+(?:Le|La|Les|L|The|Die|Der|Das|De|Het|El|Il|Lo|Los|Las)$", "", name)
    # an agenda status word after the name: "Allemagne ANNULÉ", "Climate Camp CANCELLED"
    name = re.sub(r"\s+(?:ANNULÉE?S?|ANNULEE?S?|REPORTÉE?S?|CANCELL?ED|POSTPONED|ABGESAGT|VERSCHOBEN|"
                  r"ANNULLATO|CANCELADO|AFLYST|INSTÄLLT|GEANNULEERD)$", "", name)
    name = re.sub(r"[’']s$", "", name)
    name = re.sub(r"(?<=\D)\s+(?:19|20)\d\d$", "", name)   # "Pinksterlanddagen 2022", "Ende Gelände 2019"
    # an ALL-CAPS event label after a name: "Nordic Climate Justice Coalition ACTION CAMPS"
    caps = re.search(r"(?:\s+[^\W\d_a-zß-ÿ][^\W\da-zß-ÿ\-]+)+$", name)
    if caps and re.search(r"[a-zß-ÿ]", name[:caps.start()]) and any(
            w.casefold() in EVENT_WORDS or w.casefold() in EVENT_LAST_WORDS for w in caps.group(0).split()):
        name = name[:caps.start()]
    return name


# PLACES and LANGUAGE_NAMES hold ordinary spellings; names are compared by key, so
# compare against the keys ("Nantes" -> "nante", "Nederlands" -> "nederland").
FIRST_NAME_KEYS = frozenset(FIRST_NAMES | {canonical_key(n) for n in FIRST_NAMES})
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


# The event-location gazetteer (gazetteer.py) knows far more places than places.py:
# towns, regions and protest sites in every country covered ("Neckarwestheim", "Le
# Bourget", "Val di Susa", "Floreffe"). Names found only by NER are also checked
# against it. A few of its entries locate events but are also the names of
# campaigns, occupations or companies ("No TAV", "ZAD de NDDL", "Drax", "Ilva",
# "MUOS"); those are never treated as places here.
try:
    import gazetteer as _gaz
    _GAZ_LOOKUP, _GAZ_KEY = _gaz._LOOKUP, _gaz._key
    _GAZ_CASE, _GAZ_WEAK = _gaz._CASE, _gaz._WEAK
except (ImportError, AttributeError):   # extract.py also works without gazetteer.py
    _GAZ_LOOKUP, _GAZ_KEY, _GAZ_CASE, _GAZ_WEAK = {}, None, set(), set()
_GAZ_NOT_PLACE = {"drax", "drax power station", "ilva", "taranto ilva", "muos", "tav torino",
                  "zad de nddl", "nddl", "mondeggi"}
_GAZ_LIST_SPLIT = re.compile(r"\s*[,;/]\s*|\s+(?:and|et|und|y|e|en|i|og|och|ja|és|&)\s+")


def is_gazetteer_place(surface: str) -> bool:
    """A place, or a list of places ("Castres and Toulouse", "Lingen, Ulm"), by the
    places.py lists or the gazetteer. As in the gazetteer, its CASE_SENSITIVE names
    ("Lingen", "Bure", "Allier") count only when written with a capital letter, and its
    WEAK names ("Nice", "Tours", "Roma", "Essen"), which are also ordinary words or
    people's names, never count on their own. These two rules also apply to names that
    places.py lists ("Nice", "Tours"), so a WEAK name counts only in a list with a place
    that is not WEAK ("Nice and Marseille"). (The plain places.py gate, is_place_key, is
    applied separately in extract().)"""
    parts = [p.strip(" .()[]") for p in _GAZ_LIST_SPLIT.split(surface.strip())]
    parts = [p for p in parts if p]
    if not parts:
        return False
    strong = False
    for p in parts:
        k = _GAZ_KEY(p) if _GAZ_KEY is not None else None
        if k is not None and k in _GAZ_CASE and not p[:1].isupper():
            return False   # "bure", "allier": ordinary words in lower case
        weak = k is not None and k in _GAZ_WEAK
        if canonical_key(p) in PLACES_KEYS:
            strong = strong or not weak
            continue
        if (k is None or k not in _GAZ_LOOKUP or k in _GAZ_NOT_PLACE or k.startswith("no ")):
            return False
        strong = strong or not weak
    return strong


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
# "#: descriptive" under a [theme] header (see ThemeSet).
_DESCRIPTIVE_MARK = re.compile(r"#:\s*descriptive\b", re.I)


class ThemeSet:
    """Themes loaded from a plain-text file (see example_themes.txt).

    A line starting with "!" is an exclusion for its theme: a hit that overlaps
    a match of the exclusion is not counted (e.g. "!énfasis" under [fascism]
    stops "*fasis*" from counting the Spanish word for "emphasis").

    A line "#: descriptive" inside a section marks that theme as descriptive: its
    hits are found and recorded like any other, but callers leave it out of the
    theme filter and the any-theme measures (see focus_names). Older versions read
    the line as a comment."""

    MEMO_SIZE = 20000   # paragraphs repeated across pages (menus, footers) are matched once

    def __init__(self, themes: Dict[str, List[re.Pattern]], raw: Dict[str, List[str]],
                 needles: Optional[Dict[str, List[Optional[List[str]]]]] = None,
                 exclude: Optional[Dict[str, List[re.Pattern]]] = None,
                 descriptive: Optional[Set[str]] = None):
        self.themes = themes
        # Themes marked "#: descriptive" in the file (reported, not used for filtering).
        self.descriptive: Set[str] = set(descriptive or ())
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

    @property
    def focus_names(self) -> List[str]:
        """The themes that are not descriptive, in file order (all of them for a file
        without "#: descriptive" marks)."""
        return [t for t in self.themes if t not in self.descriptive]

    @classmethod
    def load(cls, path: Optional[Path]) -> "ThemeSet":
        themes: Dict[str, List[re.Pattern]] = {}
        raw: Dict[str, List[str]] = {}
        needles: Dict[str, List[Optional[List[str]]]] = {}
        exclude: Dict[str, List[re.Pattern]] = {}
        descriptive: Set[str] = set()
        if path is None:
            return cls(themes, raw)
        current = None
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if current is not None and _DESCRIPTIVE_MARK.match(line):
                descriptive.add(current)
                continue
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
        return cls(themes, raw, needles, exclude, descriptive)

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

    @staticmethod
    def clip(hits: Dict[str, List[Tuple[int, int, str]]], start: int,
             end: int) -> Dict[str, List[Tuple[int, int, str]]]:
        """The hits of find(text) that lie inside text[start:end], with offsets made
        relative to `start`. For a window of whole sentences this equals
        find(text[start:end]) without running the term patterns again (finding 33);
        a hit that crosses the window edge is left out."""
        out: Dict[str, List[Tuple[int, int, str]]] = {}
        for theme, hs in hits.items():
            kept = [(s - start, e - start, w) for s, e, w in hs if start <= s and e <= end]
            if kept:
                out[theme] = kept
        return out

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
    # A malformed <base> or <a href> (e.g. 'http://[lien]') must never make the page
    # unparseable: safe_join gives "" instead of raising.
    base_url = (safe_join(url, base["href"]) if base else "") or url
    own_host = _netloc(url)
    root = soup.body or soup
    blocks: List[Block] = []
    _walk(root, blocks, "main", base_url, own_host)
    return ParsedPage(url=url, title=title, date=date, depth=depth,
                      blocks=[b for b in blocks if b.text or b.links], lang=lang)


def _region_of(tag: Tag, inherited: str) -> str:
    if tag.name in ("header", "footer") and tag.find_parent(["article", "main"]) is not None:
        return inherited   # the header or footer of an article (its title, lead, date): content
    if tag.name in REGION_TAGS:
        return tag.name
    role = (tag.get("role") or "").lower()
    if role == "navigation":
        return "nav"
    if role == "contentinfo":
        return "footer"
    return inherited


# Inline tags that style words inside a sentence: two of them side by side
# ("<b>Climate</b> <b>Justice</b>") are not two names.
_STYLE_TAGS = {"b", "strong", "i", "em", "u", "span", "small", "sup", "sub", "mark", "abbr", "font",
               "big", "s", "cite", "q", "code", "time", "label"}


def _walk(node: Tag, out: List[Block], region: str, base_url: str, own_host: str) -> None:
    buf: List[str] = []
    links: List[Link] = []
    last_item = ""    # "a": the last thing added was a link or an element holding one; "tag": another
    #                   inline element; "": text (whitespace between elements does not reset it)

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
                if str(child).strip():
                    last_item = ""
            continue
        if not isinstance(child, Tag):
            continue
        if child.name in BLOCK_TAGS or _has_block_child(child):
            flush()
            last_item = ""
            if child.name not in ("br", "hr"):
                if child.name == "a":
                    _collect_link(child, links, base_url, own_host)
                _walk(child, out, _region_of(child, region), base_url, own_host)
            continue
        # Inline element: keep its text in this block, remember links.
        inner = [child] if child.name == "a" else child.find_all("a", href=True)
        for a in inner:
            _collect_link(a, links, base_url, own_host)
        kind = "a" if (inner or child.name == "img") else ("style" if child.name in _STYLE_TAGS else "tag")
        text = child.get_text(" ")
        if text.strip():
            # A strip of links or logos ("<a>Greenpeace</a> <a>Attac</a> <a>BUND</a>"):
            # " · " keeps the names apart (it is a hard name boundary, see _spans).
            if last_item and (kind == "a" or last_item == "a" or (kind == "tag" and last_item == "tag")):
                buf.append(" · ")
            last_item = kind if kind != "style" else ""
        buf.append(text)
    flush()


def _has_block_child(tag: Tag) -> bool:
    return tag.find(BLOCK_TAGS - {"br"}) is not None


def _netloc(url: str) -> str:
    """Lower-case host of a URL without 'www.'; "" when the URL cannot be parsed."""
    try:
        p = urlparse(url)
        p.port   # raises ValueError for a bad port or bracketed host
        return p.netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


# (also in image file names used as alt text: "WILPF-logo", "Logo-PeaceSOS-RGB250")
_LOGO_BEFORE = re.compile(r"(?i)^logo(?:\s+(?:of|de|der|des|du|van))?(?:\s+|[-_])(?=\S)")
_LOGO_AFTER = re.compile(r"(?i)(?:\s+|[-_])logo(?:[-_][^\W_]+)*$")
# A home page path: "", "/", "/index.html", a language root "/en/", "/de", "/fr-fr/".
_HOME_PATH = re.compile(r"/?(?:index\.\w{2,5}|[a-z]{2}(?:[-_][a-z]{2})?/?(?:index\.\w{2,5})?)?/?", re.I)


def strip_logo(text: str) -> str:
    """Drop the word "Logo" around a name in link, alt and title texts: "Logo of Attac",
    "Ende Gelände Logo" -> "Attac", "Ende Gelände"."""
    out = _LOGO_AFTER.sub("", _LOGO_BEFORE.sub("", text))
    return out if out.strip() else text


def link_method(text: str, link: "Link") -> str:
    """"link" when the link text spells the linked site's name ("Code Rood" to
    code-rood.org) or the link goes to a site's home page; "link_text" otherwise (an
    article title or a page name linked deep into a site)."""
    names = site_names(link.domain) if link.domain else set()
    if names and (squash_key(text) in names or names & squash_runs(text)):
        return "link"
    path = urlparse(link.href).path if link.href else ""
    return "link" if _HOME_PATH.fullmatch(path) else "link_text"


def _collect_link(a: Tag, links: List[Link], base_url: str, own_host: str) -> None:
    href = (a.get("href") or "").strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return
    full = safe_join(base_url, href)
    host = _netloc(full) if full else ""
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


# A heading-like PDF line: all in capitals ("INTRODUCTION", "WHO WE ARE") or ending
# in a colon ("Speakers:"). Nothing is joined to it.
_PDF_HEADING = re.compile(r"(?:[^a-zß-ÿ]*[A-ZÀ-Þ][^a-zß-ÿ]*|.*:)")
# Lower-case words that leave a phrase open at the end of a line ("Friends of the").
_OPEN_END_WORDS = CONNECTORS | {"an", "or", "ou", "oder", "o", "with", "by", "from", "at", "as",
                                "is", "are", "was", "were", "that", "which", "our", "their", "its"}


def _pdf_words(text: str) -> int:
    return len(text.split())


def _name_line(line: str) -> bool:
    """One line of a column of names ("Conservation Council of Western Australia"): short,
    no final punctuation, and every word capitalised except connectors ("of", "de")."""
    line = line.strip()
    if not (0 < len(line) < 60) or re.search(r"[.,;:!?]$", line):
        return False
    words = [w for w in TOKEN_RE.findall(line) if w.casefold() not in CONNECTORS]
    if not words or len(words) > 8 or not (line[:1].isupper() or line[:1].isdigit()):
        return False
    return all(strip_elision(w)[:1].isupper() or w[:1].isdigit() for w in words)


def _name_columns(para: str) -> str:
    """Join the lines of a paragraph with " · " where three or more consecutive lines
    are a column of names (a signatory list, a partner list), so that they do not run
    together into one capitalised name; other lines keep their line break."""
    lines = para.split("\n")
    if len(lines) < 3:
        return para
    ok = [_name_line(x) for x in lines]
    out: List[str] = []
    i = 0
    while i < len(lines):
        j = i
        while j < len(lines) and ok[j]:
            j += 1
        if j - i >= 3:
            out.append(" · ".join(x.strip() for x in lines[i:j]))
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def _short_caps_line(text: str) -> bool:
    """A short line that starts and ends with a capitalised word: one name of a list."""
    words = TOKEN_RE.findall(text)
    return (0 < len(words) <= 6 and strip_elision(words[0])[:1].isupper()
            and (strip_elision(words[-1])[:1].isupper() or words[-1][:1].isdigit())
            and not re.search(r"[.,;:!?]$", text.strip()))


def _reflow(paras: List[str]) -> List[str]:
    """Paragraphs of a fragmented PDF page (a narrow-column export where every line or
    every few words is its own paragraph), joined back: a sentence that continues
    ("rooted in" / "colonialism" / "and capitalist ...") with a space, a list of short
    capitalised lines (names) with " · ". Nothing is joined to a heading-like line.
    Pages whose paragraphs are not mostly fragments are returned as they are."""
    raw = [p for p in paras if p.strip()]
    if len(raw) < 3:
        return raw
    texts = [clean_ws(p) for p in raw]
    sizes = sorted(_pdf_words(t) for t in texts)
    median = sizes[len(sizes) // 2] if len(sizes) % 2 else (sizes[len(sizes) // 2 - 1] + sizes[len(sizes) // 2]) / 2
    if not (median <= 6 and sum(n <= 4 for n in sizes) >= 0.6 * len(sizes)):
        return raw
    out: List[str] = [raw[0]]
    last_line = texts[0]           # the last original paragraph in out[-1]
    for t, r in zip(texts[1:], raw[1:]):
        prev = out[-1].rstrip()
        # (an acronym on its own line, "BUND", is a name of a list, not a heading)
        heading = (bool(_PDF_HEADING.fullmatch(last_line)) and bool(re.search(r"[^\W\d_]{2}", last_line))
                   and (last_line.endswith(":") or len(last_line) >= 8 or len(last_line.split()) >= 2))
        sep = None
        if heading or re.search(r"[.!?:]$", prev):
            sep = None
        elif re.match(r"[a-zß-ÿ0-9(\[,;:.)\]\-–—'’\"”»%&/]", t):
            sep = "\n"              # the sentence goes on (a line break reads as a space)
        elif re.search(r"[,;]$", prev) and not _PDF_HEADING.fullmatch(t):
            sep = "\n"              # "rooted in Capitalism," / "Colonialism,": a list in a sentence
        elif _short_caps_line(last_line) and _short_caps_line(t):
            sep = " · "             # a list of names
        else:
            tail = TOKEN_RE.findall(last_line)
            if tail and tail[-1].islower() and (tail[-1].casefold() in _OPEN_END_WORDS
                                                or not _PDF_HEADING.fullmatch(t)):
                # "Friends of the" / "Earth": a phrase left open; "complicity in apartheid" /
                # "Israel's war": a line ending in a lower-case word goes on
                sep = "\n"
        if sep is None:
            out.append(r)
        else:
            out[-1] = (clean_ws(prev) + sep + t) if sep == " · " else (prev + sep + r.strip())
        last_line = t
    return out


def parse_pdf_text(text: str, url: str, depth: int, title: str = "", date: str = "",
                   referrer: str = "", reflow: bool = True) -> ParsedPage:
    """PDF text (pages separated by form feeds) -> paragraph blocks tagged "pdf p.N".

    A page whose text (case and spacing ignored) is the same as an earlier page of the
    same PDF is skipped (slide handouts and exports that repeat every page); the count
    is kept in ParsedPage.repeated_pages_skipped. With `reflow` (the default; agenda
    PDFs read line by line use reflow=False), fragmented pages are joined back into
    sentences and columns of names are separated with " · " (see _reflow, _name_columns)."""
    blocks: List[Block] = []
    seen: Set[str] = set()
    skipped = 0
    for page_no, page_text in enumerate(text.split("\f"), 1):
        page_text = re.sub(r"-\n(?=[a-zà-ÿ])", "", page_text)          # re-join hyphenated words
        norm = clean_ws(page_text).casefold()
        if norm:
            digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()
            if digest in seen:
                skipped += 1
                continue
            seen.add(digest)
        paras = re.split(r"\n\s*\n", page_text)
        if reflow:
            paras = _reflow([_name_columns(p) for p in paras])
        for para in paras:
            for chunk in _pdf_chunks(para):
                blocks.append(Block(text=chunk, region=f"pdf p.{page_no}"))
    title = title or Path(urlparse(url).path).name or url
    return ParsedPage(url=url, title=title, date=date, depth=depth, blocks=blocks,
                      is_pdf=True, referrer=referrer, repeated_pages_skipped=skipped)


# --------------------------------------------------------------------------
# Organisation extraction
# --------------------------------------------------------------------------
_URL_OR_EMAIL = re.compile(r"(?:https?://|www\.)\S+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.I)
# The same, and bare domains ("lebenslaute.net", "fr.wikipedia.org/wiki/...").
_URL_OR_DOMAIN = re.compile(r"(?:https?://|www\.)\S+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+|"
                            r"\b[\w.-]+\.(?:org|com|net|de|fr|eu|ch|at|nl|be|it|es|uk|info|cz|pt|se|dk|pl|"
                            r"io|news|blog)\b\S*", re.I)


class _SquashDict(dict):
    """A dict of organisation keys that also finds a key written with other spacing or
    hyphenation ("geef tegengas" finds "geef tegen gas"), so `key in d` and d.get(key)
    work for every spelling variant that squash_key merges."""

    def __init__(self) -> None:
        super().__init__()
        self._squashed: Dict[str, str] = {}

    def __setitem__(self, key: str, value: str) -> None:
        super().__setitem__(key, value)
        self._squashed.setdefault(_squash_of_key(key), key)

    def _find(self, key: object) -> Optional[str]:
        if dict.__contains__(self, key):
            return key   # type: ignore[return-value]
        return self._squashed.get(_squash_of_key(key)) if isinstance(key, str) else None

    def __contains__(self, key: object) -> bool:
        return self._find(key) is not None

    def get(self, key, default=None):   # type: ignore[override]
        found = self._find(key)
        return dict.__getitem__(self, found) if found is not None else default


# What canonical_key returns: words of letters and digits separated by single spaces.
_KEY_SHAPE = re.compile(r"\w+(?: \w+)*")


def _squash_of_key(key: str) -> str:
    """The squashed form of a canonical key (see squash_key); short keys are kept as they
    are, so that two- and three-letter acronyms are never merged with anything."""
    sq = re.sub(r"[\W_]+", "", key)
    return sq if len(sq) >= 4 else key


class Watchlist:
    """Lines of `Canonical Name | alias | alias`.

    Matching ignores case, accents and umlaut spellings ("Ende Gelaende", "Ende Gëlande"
    and "ende gelände" all match "Ende Gelände"), and the words of a name may be joined by
    spaces, hyphens or nothing ("Code-Rood", "CodeRood"). Acronyms (aliases of six
    characters or fewer written in capitals, or of four or fewer with a capital inside,
    such as "FoE") are matched case-sensitively. An alias written with a leading "=" must
    start with a capital letter in the text ("=Stay Grounded" matches "Stay grounded" but
    not "we stay grounded"): use it for names that are also ordinary phrases."""

    def __init__(self, entries: List[Tuple[str, List[str]]]):
        self.canonical: Dict[str, str] = _SquashDict()
        self.patterns: List[Tuple[re.Pattern, str]] = []
        self.display: Dict[str, str] = _SquashDict()
        seen: Set[Tuple[str, int, str]] = set()
        for raw, aliases in entries:
            canon = raw.lstrip("=").strip()
            ckey = canonical_key(canon)
            if not ckey:
                continue
            self.display[ckey] = canon
            for name in [raw, *aliases]:
                first_cap = name.startswith("=")
                name = name.lstrip("=").strip()
                if not name:
                    continue
                self.canonical[canonical_key(name)] = ckey
                acronym = ((len(name) <= 6 and name.isupper())
                           or (len(name) <= 4 and " " not in name and any(c.isupper() for c in name[1:])))
                pat = self._pattern(name, case=acronym, first_cap=first_cap)
                if (pat.pattern, pat.flags, ckey) not in seen:   # "Ende Gelaende" = "Ende Gelände"
                    seen.add((pat.pattern, pat.flags, ckey))
                    self.patterns.append((pat, ckey))

    @staticmethod
    def _pattern(name: str, case: bool, first_cap: bool) -> re.Pattern:
        """A regular expression for `name`, to be run on fold_for_watchlist(text)."""
        words = []
        for w in re.split(r"[\s\-‐‑–_]+", name.strip()):
            if not w:
                continue
            # an umlaut matches its plain vowel and its "ae / oe / ue" spelling, and so does
            # an umlaut spelled out in the alias ("Gelaende" also matches "Gelände")
            w = re.sub("[äöüÄÖÜ]", lambda m: m.group(0).translate(_WATCHLIST_FOLD) + "e", w)
            piece = re.escape(w.translate(_WATCHLIST_FOLD))
            if not case:
                piece = re.sub(r"(?<=[^aeiouAEIOU\\])([aouAOU])e(?=[^aeiouAEIOU\\])", r"\1e?", piece)
            words.append(piece)
        if not words:
            return re.compile(r"(?!)")
        body = r"[\s\-‐‑–_]*".join(words)   # spaces, hyphens or nothing between the words
        if first_cap and not case:
            first = words[0][0]
            body = f"(?-i:{first.upper()})" + body[len(first):] if first.isalpha() else body
        return re.compile(r"(?<!\w)" + body + r"(?!\w)", 0 if case else re.I)

    def finditer(self, text: str):
        """(start, end, canonical key) of every watchlist name in `text`."""
        if not self.patterns:
            return
        folded = fold_for_watchlist(text)
        # Not inside a web or e-mail address ("https://lessoulevementsdelaterre.org"):
        # a name the address spells is found by the website rule, in the text around it.
        urls = [(m.start(), m.end()) for m in _URL_OR_EMAIL.finditer(text)]
        for pat, ckey in self.patterns:
            for m in pat.finditer(folded):
                if not any(a <= m.start() < b for a, b in urls):
                    yield m.start(), m.end(), ckey

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
# Methods that are weak evidence on their own: NER, and the lower-case head written after
# the name ("Falkirk council area"). A name found only by these is place-gated and vetted
# site-wide; other modules can import this to treat them alike (confidence, routing).
NER_LIKE_METHODS = frozenset({"ner", "pattern_after", "link_text", "website_elsewhere"})
# Methods that are evidence on their own: a rule with an organisational word or a
# descriptor, the watchlist, an agenda title, a link to the name's own site or home page,
# a website the paragraph itself gives. The ordinary-word rules (POS votes, case
# counts) never reject a name that one of these supports.
STRONG_METHODS = frozenset({"pattern", "appositive", "watchlist", "title", "link", "website"})
# Universal POS tags of words that are not proper nouns (spaCy's coarse tags; PROPN,
# X, SYM and PUNCT are left out).
COMMON_POS = frozenset({"NOUN", "ADJ", "VERB", "ADV", "PRON", "ADP", "DET", "AUX", "NUM",
                        "CCONJ", "SCONJ", "INTJ"})
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


# --------------------------------------------------------------------------
# Part-of-speech votes for one-word names (is the word a proper noun here?)
# --------------------------------------------------------------------------
# Weekday names in English, German, French, Spanish, Italian, Dutch, Portuguese and
# Danish: a weekday on its own ("Freitag", "Samstag") is a date, not a name, unless
# a strong method supports it.
WEEKDAYS = frozenset("""
monday tuesday wednesday thursday friday saturday sunday
montag dienstag mittwoch donnerstag freitag samstag sonnabend sonntag
lundi mardi mercredi jeudi vendredi samedi dimanche
lunes martes miércoles miercoles jueves viernes sábado sabado domingo
lunedì lunedi martedì martedi mercoledì mercoledi giovedì giovedi venerdì venerdi sabato domenica
maandag dinsdag woensdag donderdag vrijdag zaterdag zondag
segunda terça terca quarta quinta sexta segunda-feira terça-feira quarta-feira quinta-feira sexta-feira
mandag tirsdag onsdag torsdag fredag lørdag søndag
""".split())
# German, Austrian and EU institutions written as one compound ("Bundesregierung",
# "Bundestag", "Landesregierung", "Staatsanwaltschaft", "Europarat"): their parts are
# ordinary nouns, but the compound names an institution.
_INSTITUTION_COMPOUND = re.compile(r"^(?:bundes|landes|staats|europa)\w{3,}$", re.I)
_TAGGER_EXCLUDE = ["parser", "ner", "lemmatizer", "senter", "textcat"]
_TAG_WINDOW = 160     # characters of context kept on each side of the name


_PHRASE_TOKEN = re.compile(r"[^\W\d_][\w’'\-&]*|[.!?:;•|\n]")
_PHRASE_STOPS = frozenset(".!?:;•|\n")


def _phrase_shape(surface: str) -> bool:
    """Two or three plain words, without an acronym or a digit ("Climate Justice",
    "Open Space"; not "XR Youth", "COP 26")."""
    words = surface.split()
    return (2 <= len(words) <= 3 and not re.search(r"[A-Z]{2}|\d", surface)
            and all(_PHRASE_TOKEN.fullmatch(w) for w in words))


def single_word_name(surface: str) -> bool:
    """A one-word name that is neither an acronym (all capitals, up to 6 characters)
    nor camelCase or with a digit ("GroenFront", "COP26"): the names whose part of
    speech in their own contexts tells a proper noun ("Greenpeace") from an ordinary
    word ("Klimakrise", "Welt")."""
    toks = TOKEN_RE.findall(surface)
    if len(toks) != 1:
        return False
    w = strip_elision(toks[0])
    if not w or (w.isupper() and len(w) <= 6):
        return False
    return not re.search(r"\d", w) and not any(c.isupper() for c in w[1:])


def _pos_exempt(surface: str) -> bool:
    """Institutional compounds ("Bundesregierung") and hyphenated compounds ending in an
    organisational word ("Rosa-Luxemburg-Stiftung") are never ordinary words."""
    w = strip_elision(surface.strip())
    if _INSTITUTION_COMPOUND.match(w):
        return True
    parts = [x for x in re.split(r"[-‐‑]", w.casefold()) if x]
    return len(parts) >= 2 and (parts[-1] in HEAD_TO_TYPE or parts[-1] in COMPOUND_HEADS)


def _tagger_model(lang: str, resolve=None) -> Optional[str]:
    """The spaCy pipeline that tags `lang` (its own language model; the multilingual
    xx model has no tagger, so it gives none). `resolve` is OrgExtractor.model_name_for."""
    name = resolve(lang) if resolve is not None else None
    if name and not name.startswith("xx_") and name[:2] == SPACY_MODELS.get(lang, "")[:2]:
        return name
    if lang not in SPACY_MODELS:
        return None
    base = SPACY_MODELS[lang][:-3]
    for size in ("sm", "md", "lg"):
        if _installed(f"{base}_{size}"):
            return f"{base}_{size}"
    return None


def _aligned_pos(doc, surface: str, at: int) -> Optional[str]:
    """POS of the token of `doc` that is `surface` (case and elided article ignored),
    the one nearest to character `at` when the word occurs more than once."""
    target = strip_elision(surface).casefold().strip("’'")
    best = None
    for t in doc:
        if strip_elision(t.text).casefold().strip("’'") == target:
            d = abs(t.idx - at)
            if best is None or d < best[0]:
                best = (d, t.pos_)
    return best[1] if best else None


def _pos_votes(samples: Dict[str, List[Tuple[str, str, str]]], resolve=None,
               loaded: Optional[Dict[str, object]] = None) -> Dict[str, Tuple[int, int]]:
    """Tag the samples (key -> [(surface, context, lang)]) language by language with
    tagger-only pipelines; return key -> (votes, votes in COMMON_POS). Each distinct
    context is tagged once, one model is kept loaded at a time."""
    by_lang: Dict[str, Dict[str, List[Tuple[str, str, int]]]] = defaultdict(lambda: defaultdict(list))
    for key, items in samples.items():
        for surface, context, lang in items:
            i = context.casefold().find(strip_elision(surface).casefold())
            if i < 0:
                continue
            a = max(0, i - _TAG_WINDOW)
            clip = context[a:i + len(surface) + _TAG_WINDOW]
            by_lang[lang][clip].append((key, surface, i - a))
    out: Dict[str, List[int]] = {}
    for lang in sorted(by_lang):
        name = _tagger_model(lang, resolve)
        if not name:
            continue
        nlp = (loaded or {}).get(name)
        if nlp is None:
            try:
                import spacy
                nlp = spacy.load(name, exclude=_TAGGER_EXCLUDE)
            except Exception as exc:  # noqa: BLE001 - no votes for this language, the rest goes on
                log.debug("No POS votes for '%s': %s could not be loaded (%s)", lang, name, exc)
                continue
            if loaded is not None:
                loaded[name] = nlp
        texts = list(by_lang[lang])
        try:
            for text, doc in zip(texts, nlp.pipe(texts, batch_size=256)):
                for key, surface, at in by_lang[lang][text]:
                    pos = _aligned_pos(doc, surface, at)
                    if pos:
                        v = out.setdefault(key, [0, 0])
                        v[0] += 1
                        v[1] += pos in COMMON_POS
        except Exception as exc:  # noqa: BLE001
            log.warning("POS tagging with %s failed (%s: %s); no votes for '%s'",
                        name, type(exc).__name__, exc, lang)
        if loaded is None:
            del nlp
    return {k: (v[0], v[1]) for k, v in out.items()}


def common_word_votes(items: Iterable[Tuple[str, str, str, str]], max_per_key: int = 8) -> Dict[str, Tuple[int, int]]:
    """Part-of-speech votes for one-word names, without an OrgExtractor (analytics).
    items: (key, surface, context, lang). Up to `max_per_key` distinct contexts per key
    are tagged with the language's own spaCy tagger; the vote is the POS of the word in
    each context. Returns key -> (number of votes, votes for a common part of speech,
    see COMMON_POS). Languages without a tagger model give no entry."""
    samples: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
    seen: Set[Tuple[str, str, str]] = set()
    for key, surface, context, lang in items:
        if not key or not context or len(samples[key]) >= max_per_key:
            continue
        if (key, context, lang) in seen:
            continue
        seen.add((key, context, lang))
        samples[key].append((surface, context, lang))
    return _pos_votes(samples)


def is_common_vote(votes: Tuple[int, int]) -> bool:
    """At least 2 votes and at least half of them for a common part of speech."""
    n, c = votes
    return n >= 2 and c >= 0.5 * n


_LANG_NOISE = re.compile(r"https?://\S+|www\.\S+|\S+@\S+|\b\d+(?:er|ère|e|ème|st|nd|rd|th|°)\b", re.I)


# Letters used by only one or two of the supported languages; a word containing one
# counts as two more hits for those languages ("Obóz klimatyczny odbędzie się w sierpniu").
# (Romanian is written with the comma forms ș ț; the cedilla forms ş ţ are Turkish.)
_LANG_LETTERS = {"pl": "łśźżęąń", "ro": "șță", "cs": "řůě", "sk": "ľĺŕ", "hu": "őű", "hr": "đ",
                 "da": "øæ", "nb": "øæ", "lt": "ėįųū"}


# One-letter words next to a full stop are abbreviations ("U.S.", "e.g."), not the
# Croatian "u" or the Slovenian "s".
_LANG_ABBREV = re.compile(r"\b[^\W\d_]\.(?=[^\W\d_]?\.?)")
# Turkish (no spaCy model): its letters and commonest words. Three or more signals mean
# the paragraph is not Romanian, whatever its letters.
_TURKISH = re.compile(r"[şğıŞĞİ]|\b(?:ve|bir|ile|için|olarak)\b")


def language_scores(text: str) -> Counter:
    """Function-word hits per language (web addresses, ordinals such as "1er" and
    one-letter abbreviations such as "U.S." removed), plus two for a language whose own
    letters appear in a word. A language with only one distinct function word scores at
    most 1 (one repeated "i" or "a" is no evidence)."""
    words = re.findall(r"[^\W\d_]+", _LANG_ABBREV.sub(" ", _LANG_NOISE.sub(" ", text)).lower())[:3000]
    scores: Counter = Counter()
    for lang, hints in _LANG_HINTS.items():
        hit = [w for w in words if w in hints]
        scores[lang] = len(hit) if len(set(hit)) >= 2 else min(len(hit), 1)
    letters = set("".join(words))
    for lang, own in _LANG_LETTERS.items():
        if letters.intersection(own):
            scores[lang] += 2
    if scores.get("ro") and len(_TURKISH.findall(text)) >= 3:
        scores["ro"] = 0
    return scores


# Language codes pages declare by mistake (country codes): "se" (Northern Sami) for
# Swedish, "dk" for Danish ...
DECLARED_LANGUAGE_FIXES = {"se": "sv", "dk": "da", "gr": "el", "cz": "cs", "ua": "uk", "si": "sl", "jp": "ja"}


def _declared_code(declared: str) -> str:
    code = (declared or "").lower().replace("_", "-").split("-")[0]
    code = DECLARED_LANGUAGE_FIXES.get(code, code)
    return code if re.fullmatch(r"[a-z]{2}", code) else ""


def _supported_language(code: str) -> bool:
    return code in _LANG_HINTS or code in SPACY_MODELS


def detect_language(text: str, declared: str = "", prior: str = "") -> str:
    """Two-letter language code: the page's declared language, else a guess from
    function words. With `prior` (the page or document language) the guess must
    beat the prior by two function words; texts without evidence get the prior."""
    declared = _declared_code(declared)
    if declared and (_supported_language(declared) or not confident_language(text)):
        return declared   # an unsupported code ("eo") only when the text says nothing else
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
    """Language of a page or document: the declared one (codes declared by mistake
    corrected, see DECLARED_LANGUAGE_FIXES; a code without function words or a spaCy
    model, such as "eo", only when no paragraph gives clear evidence of another), else
    the most common confident language of its paragraphs (agenda lines, blocks), else
    a guess from all the text."""
    declared_code = _declared_code(declared)
    if declared_code and _supported_language(declared_code):
        return declared_code
    votes = Counter(lang for lang in (confident_language(t) for t in texts if len(t.split()) >= 5) if lang)
    if declared_code and not votes:
        return declared_code   # an unsupported code ("eo", "ja") when no paragraph says otherwise
    if votes:
        return votes.most_common(1)[0][0]
    return detect_language(" ".join(texts[:200]))


def _second_language(text: str, code: str) -> Tuple[str, List[Tuple[int, int]]]:
    """Another language that a paragraph routed to `code` is partly written in, and the
    sentences written in it: a language that leads in a sentence of four or more words
    with two or more function words ("Blockade of the armaments factory and war
    profiteer Heckler & Koch" after the French text of an agenda entry), or that has
    three or more function words in the whole paragraph against fewer for `code`.
    ("", []) when there is none."""
    sc = language_scores(text)
    sents = [(s, e, language_scores(text[s:e])) for s, e in split_sentences(text)]
    lang = ""
    best = max(((n, c) for c, n in sc.items() if c != code), default=(0, ""))
    if best[0] >= 3 and sc.get(code, 0) < best[0]:
        lang = best[1]
    else:
        votes: Counter = Counter()
        for s, e, ss in sents:
            if len(text[s:e].split()) < 4:
                continue
            ranked = ss.most_common(2)
            if not ranked:
                continue
            lead, top = ranked[0]
            second = ranked[1][1] if len(ranked) > 1 else 0
            if lead != code and top >= 2 and top > second:
                votes[lead] += top
        lang = votes.most_common(1)[0][0] if votes else ""
    if not lang:
        return "", []
    spans = [(s, e) for s, e, ss in sents if ss.get(lang, 0) >= max(1, ss.get(code, 0))]
    return (lang, spans) if spans else ("", [])


def _trim_end(text: str, start: int, end: int) -> int:
    """Drop trailing function and interface words (TRAILING_STRIP) from a span of two or
    more words: "Casino Group A" -> "Casino Group", "Scientist Rebellion Logo" ->
    "Scientist Rebellion"."""
    while True:
        toks = list(TOKEN_RE.finditer(text, start, end))
        if len(toks) < 2 or toks[-1].group(0).casefold() not in TRAILING_STRIP:
            return end
        end = toks[-2].end()


def trim_junk(surface: str) -> Tuple[int, int]:
    """(start, end) of the name inside `surface` once "Logo" around it, a role before it
    ("Presseteam von", "Member of the"), an opener ("Neither the", "INTRODUCTION") and
    trailing function words ("A", "At", "Website") are dropped. The whole surface when
    nothing is to be dropped, or when dropping would leave nothing."""
    start, end = 0, len(surface)
    m = _LOGO_BEFORE.match(surface)
    if m:
        start = m.end()
    m = _LOGO_AFTER.search(surface, start)
    if m:
        end = m.start()
    while True:
        role = ROLE_OF.match(surface, start, end)
        if role and _is_cap(surface[role.end():end]):
            start = role.end()
            continue
        first = TOKEN_RE.match(surface, start)
        if first and first.group(0).casefold() in _JUNK_OPENERS and first.end() < end:
            nxt = TOKEN_RE.search(surface, first.end(), end)
            while nxt and nxt.group(0).casefold() in ARTICLES and not _is_cap(nxt.group(0)):
                nxt = TOKEN_RE.search(surface, nxt.end(), end)   # "Neither the European Union"
            if nxt and _is_cap(nxt.group(0)):
                start = nxt.start()
                continue
        break
    end = _trim_end(surface, start, end)
    if not TOKEN_RE.search(surface, start, end):
        return 0, len(surface)
    return start, end


class OrgExtractor:
    NER_MEMO_SIZE = 200_000   # distinct (model, paragraph) results kept in memory
    # spaCy models kept loaded at once. A model takes about 70 MB (sm) to 600 MB (lg);
    # beyond this the least recently used one is freed and reloaded if its language
    # comes back. Raised automatically when models keep being reloaded.
    MAX_MODELS = 6
    MODEL_SIZES = {"lg": ("lg", "md", "sm"), "md": ("md", "sm"), "sm": ("sm",)}

    def __init__(self, watchlist: Watchlist, stoplist: Tuple[Set[str], List[re.Pattern]],
                 spacy_model: Optional[str], use_links: bool = True, ner_size: str = "sm",
                 cache_dir: Optional[Path] = None):
        """spacy_model: a model name used for every page, "auto" (one model per page
        language, see SPACY_MODELS), or "none". ner_size: sm / md / lg models in
        auto mode; md and lg are more precise but miss more unusual group names.
        cache_dir: keep NER results in cache_dir/ner_cache.sqlite, keyed by model,
        model and spaCy versions and paragraph text, so a re-run (--no-crawl with new
        themes or stop lists) does not run spaCy again on text it has already seen."""
        self.watchlist = watchlist if watchlist is not None else Watchlist.load(None)
        # Organisation keys by squashed form (see merge_key), seeded with the watchlist's
        # own keys so that a watchlist name is always the one its variants merge into.
        self._merge_keys: Dict[str, str] = {}
        for ckey in self.watchlist.display:
            self.merge_key(ckey)
        self.stop_exact, self.stop_pats = stoplist
        self.use_links = use_links
        self.spacy_model = (spacy_model or "none").strip()
        self.ner_size = ner_size
        self._models: Dict[str, object] = {}
        self.common_words: Set[str] = set()
        self.lowercase_words: Set[str] = set()      # keys of words written in lower case, see prepare_corpus()
        self._lowercase_keys: Set[str] = set()
        self.mid_caps: Optional[Set[str]] = None   # None until prepare_corpus() runs
        self._corpus_text = ""                      # distinct paragraphs, for phrase case counts
        self._modifier_memo: Dict[str, bool] = {}   # _modifier_word() results, per corpus
        self._title_counts: Counter = Counter()     # folded phrase -> agenda titles that contain it
        self._title_names: Set[str] = set()         # folded title-slot names accepted so far
        self._pretallied: Counter = Counter()        # paragraphs whose labels prime_agenda() counted
        self._corpus_low = ""
        self._corpus_sites: Dict[str, str] = {}
        self._corpus_prefixes: Set[str] = set()
        self._phrase_case: Dict[str, bool] = {}
        self.missing_models: Set[str] = set()
        self.broken_models: Set[str] = set()       # installed but failed to load or run
        self.missing_deps: Dict[str, str] = {}      # model -> Python package it could not import
        self.models_used: Dict[str, str] = {}       # every model loaded in the run -> version
        self.ner_fallbacks: Dict[str, Dict[str, str]] = {}   # lang -> {"requested", "used"}
        self._lru: "OrderedDict[str, None]" = OrderedDict()  # loaded models, least recent first
        self._evicted: Set[str] = set()
        self._cache_only: Set[str] = set()          # models whose results all came from cache_dir
        self.model_reloads = 0
        self.max_models = self.MAX_MODELS
        self._model_ids: Dict[str, str] = {}
        self._name_for: Dict[str, Optional[str]] = {}
        self._ner_memo: Dict[Tuple[str, str], NerDoc] = {}
        self._memo_cap = self.NER_MEMO_SIZE         # raised by prefetch() when there is no cache
        self._call_results: Dict[Tuple[str, str], NerDoc] = {}   # see pipe()
        self.ner_stats: Counter = Counter()          # "analysed" by spaCy, read from "cache"
        self._db = None
        if cache_dir is not None:
            self._db = self._open_cache(Path(cache_dir) / "ner_cache.sqlite")
        self.label_votes: Dict[str, Counter] = defaultdict(Counter)
        # Contexts of one-word names (key -> [(surface, sentence, language)], up to
        # POS_SAMPLES distinct ones), tagged by finish_corpus_votes() into pos_votes.
        self.pos_samples: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
        self.pos_votes: Optional[Dict[str, Tuple[int, int]]] = None   # None until tagged
        self._pos_seen: Set[Tuple[str, str]] = set()
        # Two- and three-word names (key -> surface) and their lower-case / capitalised
        # uses in the corpus, counted by finish_corpus_votes() (see is_lowercase_phrase).
        self._phrase_surfaces: Dict[str, str] = {}
        self._phrase_counts: Dict[Tuple[str, ...], List[int]] = {}
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
                if name in self._evicted:
                    self.model_reloads += 1
                    log.debug("Reloaded spaCy model %s", name)
                    if self.model_reloads > 2 * self.max_models:
                        # Languages alternate too often for the limit: keep more models
                        # loaded rather than reload them again and again.
                        self.max_models += 2
                        self.model_reloads = 0
                        log.info("spaCy models are being reloaded often; now keeping up to %d "
                                 "loaded at once", self.max_models)
                else:
                    log.info("Loaded spaCy model %s", name)
                self._models[name] = nlp
                self.models_used[name] = str(getattr(nlp, "meta", {}).get("version", ""))
                self._cache_only.discard(name)
                self._lru[name] = None
                self._evict()
            except ImportError as exc:
                # A package the model or spaCy needs (e.g. jinja2) is missing: downloading
                # the model again would not help, the Python environment is incomplete.
                dep = (getattr(exc, "name", "") or "").split(".")[0]
                if dep and dep != name:
                    log.warning("spaCy model %r could not be loaded because the Python package "
                                "%r is missing (%s). Run: pip install -r requirements.txt (inside "
                                "the activated .venv)", name, dep, exc)
                    self.missing_deps[name] = dep
                    self.broken_models.add(name)
                else:
                    log.warning("spaCy model %r unavailable (%s). Install with: python -m spacy "
                                "download %s", name, exc, name)
                    self.missing_models.add(name)
                self._models[name] = None
                self._forget_name(name)
            except OSError as exc:
                log.warning("spaCy model %r unavailable (%s). Install with: python -m spacy "
                            "download %s", name, exc, name)
                self.missing_models.add(name)
                self._models[name] = None
                self._forget_name(name)
            except Exception as exc:  # noqa: BLE001 - a broken or mismatched model must not stop the run
                log.warning("spaCy model %r is installed but failed to load (%s: %s); NER uses "
                            "another installed model for its language, if any. Check with: "
                            "python -m spacy validate, then: python -m spacy download %s --upgrade",
                            name, type(exc).__name__, exc, name)
                self.broken_models.add(name)
                self._models[name] = None
                self._forget_name(name)
        elif name in self._lru:
            self._lru.move_to_end(name)
        return self._models[name]

    def _evict(self) -> None:
        """Free the least recently used spaCy models beyond max_models (finding 57: a
        multilingual run would otherwise keep every model it ever loaded, over 1.4 GB
        for the 21 small models alone). A freed model is reloaded if needed; its NER
        results stay in the memo and the persistent cache."""
        while len(self._lru) > self.max_models:
            old, _ = self._lru.popitem(last=False)
            self._models.pop(old, None)
            self._evicted.add(old)
            log.info("Freed spaCy model %s (more than %d models loaded; it is reloaded if "
                     "its language comes up again)", old, self.max_models)
        if self._evicted:
            import gc
            gc.collect()

    def _forget_name(self, name: str) -> None:
        """After a model failed to load or run, resolve its languages again so that
        the next smaller installed model is used (see _resolve_model_name)."""
        for lang, n in list(self._name_for.items()):
            if n == name:
                del self._name_for[lang]

    def model_name_for(self, lang: str = "en") -> Optional[str]:
        """Name of the spaCy model used for `lang`, without loading it (None: no NER)."""
        if lang not in self._name_for:
            self._name_for[lang] = self._resolve_model_name(lang)
        return self._name_for[lang]

    def _usable(self, name: str) -> bool:
        return (name not in self.broken_models and name not in self.missing_models
                and _installed(name))

    def _resolve_model_name(self, lang: str) -> Optional[str]:
        if self.spacy_model.lower() == "none":
            return None
        if self.spacy_model.lower() != "auto":
            return self.spacy_model
        if lang in SPACY_MODELS:
            # The requested size if installed, else the next smaller one (lg -> md -> sm),
            # with one warning per language: results differ between sizes.
            base = SPACY_MODELS[lang][:-3]
            sizes = self.MODEL_SIZES.get(self.ner_size, (self.ner_size, "sm"))
            wanted = f"{base}_{sizes[0]}"
            for size in sizes:
                name = f"{base}_{size}"
                if self._usable(name):
                    if name != wanted and self.ner_fallbacks.get(lang, {}).get("used") != name:
                        if wanted in self.broken_models:
                            log.warning("spaCy model %s failed to load or run; using %s for '%s' "
                                        "instead. Check with: python -m spacy validate",
                                        wanted, name, lang)
                        else:
                            log.warning("spaCy model %s is not installed; using %s for '%s' instead. "
                                        "Install the requested size with: python -m spacy download %s",
                                        wanted, name, lang, wanted)
                        self.ner_fallbacks[lang] = {"requested": wanted, "used": name}
                    return name
        if lang != "en":
            # Never fall back to the English model on another language: it tags
            # ordinary capitalised words (every German noun) as organisations.
            if self._usable("xx_ent_wiki_sm"):
                if lang in SPACY_MODELS and lang not in self.ner_fallbacks:
                    # a language model exists but is not installed (or is broken)
                    wanted = f"{SPACY_MODELS[lang][:-3]}_{self.ner_size}"
                    log.warning("No usable spaCy model for '%s'; trying the multilingual "
                                "xx_ent_wiki_sm instead. %s", lang,
                                "Check with: python -m spacy validate" if self.broken_models & {
                                    f"{SPACY_MODELS[lang][:-3]}_{z}" for z in ("sm", "md", "lg")}
                                else f"Install with: python -m spacy download {wanted}")
                    self.ner_fallbacks[lang] = {"requested": wanted, "used": "xx_ent_wiki_sm"}
                return "xx_ent_wiki_sm"
            own = ({f"{SPACY_MODELS[lang][:-3]}_{z}" for z in ("sm", "md", "lg")}
                   if lang in SPACY_MODELS else set())
            if not (self.broken_models & (own | {"xx_ent_wiki_sm"})):
                # Not installed (a broken model or a missing package is reported as such)
                self.missing_models.add(SPACY_MODELS.get(lang, f"a spaCy model for '{lang}'"))
            return None
        if "en_core_web_sm" in self.broken_models:
            return None
        return "en_core_web_sm"

    def model_for(self, lang: str = "en"):
        name = self.model_name_for(lang)
        return self._load(name) if name else None

    @property
    def nlp(self):
        """English (or the fixed) model; kept for callers that do not pass a language."""
        return self.model_for("en")

    def model_record(self) -> Dict[str, object]:
        """spaCy models of the run, for run_info.json: every model whose results were
        used (loaded, even if freed since, or only read back from the NER cache), the
        size fallbacks, and the models missing or broken."""
        return {"spacy_models_used": dict(sorted(self.models_used.items())),
                "spacy_models_cache_only": sorted(self._cache_only),
                "ner_fallbacks": dict(sorted(self.ner_fallbacks.items())),
                "spacy_models_missing": sorted(self.missing_models),
                "spacy_models_broken": sorted(self.broken_models),
                "spacy_missing_packages": dict(sorted(self.missing_deps.items())),
                "spacy_models_freed": sorted(self._evicted)}

    def log_model_summary(self, logger: logging.Logger) -> None:
        """End-of-analysis summary of models that were missing, broken or replaced by
        a smaller size (one line each)."""
        if self.missing_models:
            logger.warning("NER was skipped for some text because these spaCy models are not "
                           "installed (rule-based detection still ran): %s. Install with: %s",
                           ", ".join(sorted(self.missing_models)),
                           " && ".join(f"python -m spacy download {m}" for m in sorted(self.missing_models)
                                       if m.endswith("_sm")))
        if self.missing_deps:
            logger.warning("NER was skipped for some text because Python packages that spaCy "
                           "needs are missing (%s; models affected: %s). The organisations of this "
                           "run are therefore incomplete. Run: pip install -r requirements.txt "
                           "(inside the activated .venv), then run again with --no-crawl",
                           ", ".join(sorted(set(self.missing_deps.values()))),
                           ", ".join(sorted(self.missing_deps)))
        other_broken = sorted(self.broken_models - set(self.missing_deps))
        if other_broken:
            logger.warning("These spaCy models are installed but failed to load or run, so NER "
                           "used another model or was skipped for their language: %s. They are "
                           "usually built for another spaCy version: run 'python -m spacy validate' "
                           "and reinstall the models it lists, e.g. python -m spacy download %s --upgrade",
                           ", ".join(other_broken), other_broken[0])
        # Only fallbacks that actually worked (xx_ent_wiki_sm may have failed as well).
        used = {lang: v for lang, v in self.ner_fallbacks.items()
                if v["used"] not in self.broken_models and v["used"] not in self.missing_models}
        if used:
            logger.warning("NER used another spaCy model than the one asked for (--ner-size %s) for: %s",
                           self.ner_size, ", ".join(f"{lang} ({v['used']})" for lang, v in sorted(used.items())))

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
        out = [(confident_language(t, fallback) if c else "") or fallback for t, c in zip(texts, own)]
        # a language given to only one paragraph needs strong evidence of its own
        routed = Counter(out)
        return [fallback if (c != fallback and routed[c] < 2 and language_scores(t)[c] < 4) else c
                for t, c in zip(texts, out)]

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

    @staticmethod
    def _open_cache(path: Path):
        """Open (or create) the persistent NER cache. A damaged file (power loss or a
        folder sync during a long run) is moved aside as .bad and a new, empty cache is
        started; if even that fails the run goes on without a persistent cache."""
        import sqlite3

        def connect():
            db = sqlite3.connect(str(path))
            try:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=NORMAL")
                db.execute("CREATE TABLE IF NOT EXISTS ner (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
                db.execute("SELECT 1 FROM ner LIMIT 1").fetchall()
            except Exception:
                db.close()
                raise
            return db

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return connect()
        except (sqlite3.Error, OSError) as exc:
            log.warning("The NER cache %s cannot be read (%s); it is moved aside as .bad and a "
                        "new one is started (NER runs again on text it had stored)", path, exc)
        try:
            for suffix in ("", "-wal", "-shm"):
                part = Path(str(path) + suffix)
                if part.exists():
                    part.replace(Path(str(path) + suffix + ".bad"))
            return connect()
        except (sqlite3.Error, OSError) as exc:
            log.warning("No NER cache this run (%s): results are not kept for later runs", exc)
            return None

    def _cache_failed(self, exc: Exception) -> None:
        """A read or write of the NER cache failed mid-run: carry on without it."""
        log.warning("The NER cache stopped working (%s: %s); the run goes on without it, so "
                    "NER results from now on are not kept for later runs",
                    type(exc).__name__, exc)
        try:
            self._db.close()
        except Exception:  # noqa: BLE001 - already broken
            pass
        self._db = None

    def _cache_get(self, keys: List[str]) -> Dict[str, "NerDoc"]:
        found: Dict[str, NerDoc] = {}
        if self._db is None or not keys:
            return found
        import sqlite3
        try:
            for i in range(0, len(keys), 500):
                chunk = keys[i:i + 500]
                q = "SELECT k, v FROM ner WHERE k IN (%s)" % ",".join("?" * len(chunk))
                for k, v in self._db.execute(q, chunk):
                    found[k] = v
        except sqlite3.Error as exc:
            self._cache_failed(exc)
        return found

    def pipe(self, texts: List[str], lang: str = "en") -> list:
        """NER results for `texts` (spaCy-like records, see NerDoc). In auto mode each
        paragraph long enough to tell gets its own language (pages mix languages:
        translations, quotes, song sheets); short ones take the page language `lang`.
        Each distinct (model, text) is analysed once per run; with cache_dir, also once
        across runs."""
        per_text = self.paragraph_languages(texts, lang)
        docs = self._pipe_models(texts, per_text)
        # Bilingual paragraphs (agenda entries in French followed by an English
        # translation): the model of the main language misses names in the other half
        # ("Heckler & Koch", "FossielVrij NL" as MISC), so the second language's model
        # runs too and its organisation entities that do not overlap are added.
        # Only entities inside the sentences written in that language are taken; one
        # that the first model saw only as MISC ("Heckler & Koch") is replaced.
        second: Dict[int, Tuple[str, List[Tuple[int, int]]]] = {}
        if self.spacy_model.lower() == "auto":
            for i, (t, code) in enumerate(zip(texts, per_text)):
                if docs[i] is not None and len(t.split()) >= 12:
                    c, sents = _second_language(t, code)
                    # Only pairs with English: French, Spanish, Italian and Portuguese
                    # share too many function words to tell their sentences apart.
                    if (c and "en" in (c, code)
                            and self.model_name_for(c) not in (None, self.model_name_for(code))):
                        second[i] = (c, sents)
        if second:
            idx = sorted(second)
            extra = self._pipe_models([texts[i] for i in idx], [second[i][0] for i in idx], tally=False)
            for i, rec in zip(idx, extra):
                if rec is None:
                    continue
                base, sents = docs[i], second[i][1]
                add, drop = [], set()
                for e in rec.ents:
                    if e.label_ not in ORG_LABELS or not any(a <= e.start_char and e.end_char <= b
                                                             for a, b in sents):
                        continue
                    hit = [k for k, b in enumerate(base.ents)
                           if b.start_char < e.end_char and e.start_char < b.end_char]
                    if all(base.ents[k].label_ == "MISC" for k in hit):
                        add.append(e)
                        drop.update(hit)
                if not add:
                    continue
                for ent in add:
                    self.label_votes[canonical_key(tidy_surface(ent.text))][ent.label_] += 1
                kept = [b for k, b in enumerate(base.ents) if k not in drop]
                docs[i] = NerDoc(base.lang_, base.has_annotation("POS"),
                                 sorted(kept + add, key=lambda e: e.start_char))
                self.ner_stats["second_language"] += 1
        return docs

    def _pipe_models(self, texts: List[str], per_text: List[str], tally: bool = True) -> list:
        """NER records for `texts`, each with the model for its language in `per_text`.
        With `tally`, the labels go into label_votes (see pipe())."""
        docs: list = [None] * len(texts)
        # Results of this call, kept apart from the run-wide memo: _remember() may clear
        # the memo when it is full, which must not lose texts analysed earlier in this call.
        self._call_results = {}
        by_model: Dict[str, List[int]] = defaultdict(list)
        for i, code in enumerate(per_text):
            name = self.model_name_for(code)
            if name and texts[i]:
                by_model[name].append(i)
        queue = list(by_model.items())
        while queue:
            name, idx = queue.pop(0)
            if self._models.get(name, True) is None:
                queue.extend(self._regroup(name, idx, per_text))
                continue   # missing or broken model: a smaller one may take over
            mid = self._model_id(name)
            todo = []
            for t in dict.fromkeys(texts[i] for i in idx):
                rec = self._ner_memo.get((mid, t))
                if rec is None:
                    todo.append(t)
                else:
                    self._call_results[(mid, t)] = rec
            if todo and self._db is not None:
                keys = {t: hashlib.sha1(f"{mid}\x00{t}".encode("utf-8")).hexdigest() for t in todo}
                stored = self._cache_get(list(keys.values()))
                for t, k in keys.items():
                    if k in stored:
                        self._remember(mid, t, NerDoc.from_json(stored[k], t))
                        self.ner_stats["cache"] += 1
                        if name not in self.models_used:   # results of this model, read back
                            self.models_used[name] = mid.split("|")[0].partition("@")[2]
                            self._cache_only.add(name)
                todo = [t for t in todo if (mid, t) not in self._call_results]
            if todo:
                nlp = self._load(name)
                if nlp is None:
                    queue.extend(self._regroup(name, idx, per_text))
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
                    log.warning("spaCy model %r failed while analysing text (%s: %s); from now on NER "
                                "uses another installed model for its language, if any, or is skipped "
                                "for it", name, type(exc).__name__, exc)
                    self.broken_models.add(name)
                    self._models[name] = None
                    self._lru.pop(name, None)
                    self._forget_name(name)
                    left = [i for i in idx if (mid, texts[i]) not in self._call_results]
                    queue.extend(self._regroup(name, left, per_text))
                if new_rows and self._db is not None:
                    import sqlite3
                    try:
                        self._db.executemany("INSERT OR REPLACE INTO ner (k, v) VALUES (?, ?)", new_rows)
                        self._db.commit()
                    except sqlite3.Error as exc:
                        self._cache_failed(exc)
            for i in idx:
                rec = self._call_results.get((mid, texts[i]))
                if rec is None:
                    continue
                docs[i] = rec
                if not tally:
                    continue
                if self._pretallied.get(texts[i]):
                    self._pretallied[texts[i]] -= 1   # counted already by prime_agenda()
                    continue
                for ent in rec.ents:  # every label a name receives anywhere on the site
                    self.label_votes[canonical_key(tidy_surface(ent.text))][ent.label_] += 1
                if self._title_counts:   # names in agenda titles, for entries NER missed
                    self._title_slot_spans(texts[i], rec.ents, ())
        return docs

    def _regroup(self, name: str, idx: List[int], per_text: List[str]) -> List[Tuple[str, List[int]]]:
        """Paragraphs whose model `name` failed, grouped by the model now chosen for
        their language (a smaller installed size, or none)."""
        groups: Dict[str, List[int]] = defaultdict(list)
        for i in idx:
            new = self.model_name_for(per_text[i])
            if new and new != name and self._models.get(new, True) is not None:
                groups[new].append(i)
        return list(groups.items())

    def prefetch(self, pages: Iterable[Tuple[List[str], str]], workers: int = 1) -> int:
        """Run NER ahead of the page loop: every paragraph of `pages` ((texts, page
        language) pairs) is routed as pipe() would route it (paragraph languages, and the
        second-language pass of bilingual paragraphs), de-duplicated per model, and each
        model is loaded once and run over all its texts (nlp.pipe, batch_size=64,
        n_process=workers). Texts already in the memo or the persistent cache are
        skipped. The results go to the memo and the cache without counting labels, so
        the later pipe() calls give the same results (and label votes) as without the
        prefetch, without loading or freeing models. Returns the number of texts
        analysed."""
        if self.spacy_model.lower() == "none":
            return 0
        auto = self.spacy_model.lower() == "auto"
        groups: Dict[str, Dict[str, None]] = defaultdict(dict)
        for texts, lang in pages:
            texts = list(texts)
            for t, code in zip(texts, self.paragraph_languages(texts, lang or "en")):
                if not t:
                    continue
                name = self.model_name_for(code)
                if name:
                    groups[name][t] = None
                if auto and name and len(t.split()) >= 12:
                    c, _ = _second_language(t, code)
                    if c and "en" in (c, code):
                        other = self.model_name_for(c)
                        if other and other != name:
                            groups[other][t] = None
        total = sum(len(v) for v in groups.values())
        if self._db is None and total > self._memo_cap:
            self._memo_cap = total + self.NER_MEMO_SIZE   # keep them all for the page loop
        import time
        t0, done = time.time(), 0
        for name, tdict in groups.items():
            mid = self._model_id(name)
            todo = [t for t in tdict if (mid, t) not in self._ner_memo]
            if todo and self._db is not None:
                keys = {t: hashlib.sha1(f"{mid}\x00{t}".encode("utf-8")).hexdigest() for t in todo}
                stored = self._cache_get(list(keys.values()))
                todo = [t for t in todo if keys[t] not in stored]
            if not todo:
                continue
            nlp = self._load(name)
            if nlp is None:
                continue   # pipe() falls back to another model for these texts
            n_proc = max(1, int(workers or 1))
            rows: List[Tuple[str, str]] = []
            try:
                try:
                    stream = zip(todo, nlp.pipe(todo, batch_size=64, n_process=n_proc)
                                 if n_proc > 1 else nlp.pipe(todo, batch_size=64))
                    for t, doc in stream:
                        rec = NerDoc.from_spacy(doc)
                        self._remember(mid, t, rec)
                        done += 1
                        if self._db is not None:
                            rows.append((hashlib.sha1(f"{mid}\x00{t}".encode("utf-8")).hexdigest(), rec.to_json()))
                            if len(rows) >= 2000:
                                self._cache_put(rows)
                                rows = []
                except Exception as exc:  # noqa: BLE001
                    if n_proc == 1:
                        raise
                    log.warning("NER with %d processes failed (%s: %s); analysing in one process",
                                n_proc, type(exc).__name__, exc)
                    left = [t for t in todo if (mid, t) not in self._ner_memo]
                    for t, doc in zip(left, nlp.pipe(left, batch_size=64)):
                        rec = NerDoc.from_spacy(doc)
                        self._remember(mid, t, rec)
                        done += 1
                        if self._db is not None:
                            rows.append((hashlib.sha1(f"{mid}\x00{t}".encode("utf-8")).hexdigest(), rec.to_json()))
            except Exception as exc:  # noqa: BLE001 - pipe() reports and handles a failing model
                log.warning("NER prefetch with spaCy model %r stopped (%s: %s); the page loop "
                            "analyses the rest", name, type(exc).__name__, exc)
            if rows:
                self._cache_put(rows)
        self.ner_stats["prefetched"] += done
        self._call_results = {}
        log.info("NER prefetch: %d paragraphs analysed with %d model(s) in %.1f s (%d process%s)",
                 done, len(groups), time.time() - t0, max(1, int(workers or 1)),
                 "" if max(1, int(workers or 1)) == 1 else "es")
        return done

    def _cache_put(self, rows: List[Tuple[str, str]]) -> None:
        if self._db is None or not rows:
            return
        import sqlite3
        try:
            self._db.executemany("INSERT OR REPLACE INTO ner (k, v) VALUES (?, ?)", rows)
            self._db.commit()
        except sqlite3.Error as exc:
            self._cache_failed(exc)

    def _remember(self, mid: str, text: str, rec: "NerDoc") -> None:
        if len(self._ner_memo) >= self._memo_cap:
            self._ner_memo.clear()
        self._ner_memo[(mid, text)] = rec
        self._call_results[(mid, text)] = rec

    def close(self) -> None:
        """Close the persistent NER cache (safe to call more than once)."""
        if self._db is not None:
            import sqlite3
            try:
                self._db.commit()
                self._db.close()
            except sqlite3.Error as exc:
                log.warning("Could not close the NER cache cleanly (%s)", exc)
            self._db = None

    # ------------------------------------------------------- corpus statistics
    def prepare_corpus(self, texts: Iterable[str]) -> None:
        """Learn which words this site writes in lower case. A name made only of
        such words ("Climate Justice", "Hydrogen", "Approach") is ordinary
        language unless a rule with an organisational noun supports it."""
        lower: Counter = Counter()
        upper: Counter = Counter()
        alone: Counter = Counter()    # capitalised uses not next to another capitalised word
        lower_text: Counter = Counter()   # lower-case uses outside web addresses ("wikipedia.org")
        # Each distinct paragraph counts once: calendar day cells, menus and footers repeat
        # the same Title-Case text many times and would outvote the running text.
        seen = list(dict.fromkeys(texts))
        for text in seen:
            urls = [m.span() for m in _URL_OR_DOMAIN.finditer(text)] if ("." in text or "@" in text) else []
            for s, e in split_sentences(text):
                found = list(TOKEN_RE.finditer(text, s, e))
                toks = [strip_elision(m.group(0)) for m in found]   # "d’Attac" counts as "Attac"
                # capitalised and joined to the previous / next word by spaces only
                caps = [bool(w[:1].isupper()) for w in toks]
                for i, w in enumerate(toks):
                    if i == 0 or w.isupper() or not w:
                        continue  # sentence-initial and ALL-CAPS words say nothing about case
                    if caps[i]:
                        upper[w.casefold()] += 1
                        if not ((caps[i - 1] and text[found[i - 1].end():found[i].start()].isspace())
                                or (i + 1 < len(toks) and caps[i + 1]
                                    and text[found[i].end():found[i + 1].start()].isspace())):
                            alone[canonical_key(w)] += 1     # "the Earth", not "Friends of the Earth"
                    else:
                        lower[w.casefold()] += 1
                        if not urls or not any(a <= found[i].start() < b for a, b in urls):
                            lower_text[canonical_key(w)] += 1
        self.common_words = {w for w, n in lower.items() if n >= 2 and n >= 0.5 * upper.get(w, 0)}
        # Words written in lower case at least 3 times, and at least a quarter as often as
        # capitalised on their own ("earth", "parliament", "bank"): a one-word name made
        # of one is an ordinary word unless a strong method supports it (see vet()).
        self.lowercase_words = {w for w, n in lower_text.items() if n >= 3 and n >= 0.25 * alone.get(w, 0)}
        self._lowercase_keys = set(self.lowercase_words)
        self.mid_caps = set(upper)
        self._corpus_text = "\n".join(seen)
        # A lower-case copy of the same length, so phrase positions found in it are valid
        # in the original ("İ" and a few other characters change length when lowered).
        low = self._corpus_text.lower()
        if len(low) != len(self._corpus_text):
            low = "".join(c if len(c.lower()) != 1 else c.lower() for c in self._corpus_text)
        self._corpus_low = low
        self._phrase_case = {}
        self._modifier_memo = {}
        # Phrases of two to six words in the titles of agenda entries, each distinct title
        # counted once ("Shell must fall" is in three titles of the French agenda).
        self._title_counts = Counter()
        self._title_names = set()
        titles = set()
        for text in seen:
            span = agenda_title_span(text)
            if span:
                titles.add(_fold_phrase(text[span[0]:span[1]]))
        for t in titles:
            w = t.split()
            self._title_counts.update({" ".join(w[i:j]) for i in range(len(w))
                                       for j in range(i + 2, min(len(w), i + 6) + 1)})
        # Websites given anywhere in the corpus ("https://alternatiba.eu" in one agenda
        # entry): a name that spells one is that site's organisation in other paragraphs too.
        self._corpus_sites = _sites_of(m.group(1) for m in _URL_HOST.finditer(self._corpus_text))
        self._corpus_prefixes = {n[:k] for n in self._corpus_sites for k in range(1, len(n) + 1)}

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
        """Site-wide check for names found only by NER (and by the weak lower-case-head
        rule "pattern_after", vetted the same way); names also found by another rule, a
        descriptor, a website, a link, an agenda title or the watchlist are kept.

        A name is rejected when:
        - at least 60% of its 2 or more NER mentions follow a place preposition
          (counted in extract(), see LOCATIVE_BEFORE): "in X", "at X", "à X", "bei X",
          "op X". "from X" and "to X" (LOCATIVE_WEAK) count only when no actor word
          comes before them: "1 mile from Kinneil" counts, while "activists from
          Greenpeace", "support from Attac", "donate to Sea-Watch" and "sich bei X"
          (ACTOR_PREPOSITION) do not. "a" (Romance "to, at") and "i" (Scandinavian
          "in") count only in paragraphs of those languages (LOCATIVE_BY_LANG), since
          they are also the English and Italian articles ("Despite a United Nations");
        - or spaCy labels it a place at least twice as often as an organisation (and
          3 or more times);
        - or spaCy labels it a person, law, event, work... at least three times as often
          as an organisation, with fewer than 3 organisation labels (person labels
          count for less than place labels: small models often tag coined group names
          as people).
        The place-preposition check comes first and does not look at the label votes:
        small models label many place names ORG (Kinneil, ZAD, Scholven, Sassnitz).
        Before both, a one-word name is rejected when its part-of-speech votes say it is
        an ordinary word, or when it is a weekday (see is_common_pos)."""
        if set(methods) & STRONG_METHODS or set(methods) - NER_LIKE_METHODS:
            return True   # a rule, a link or a descriptor also supports it
        if self.pos_votes is None:
            self.finish_corpus_votes()
        if self.is_common_pos(key):
            return False  # a one-word name used as an ordinary word ("Welt", "Klimakrise", "Freitag")
        if self.is_lowercase_word(key):
            return False  # "Earth", "Parliament", "Bank": written in lower case in this corpus
        if self.is_lowercase_phrase(key):
            return False  # "Social Movements", "System Change": an ordinary phrase here
        if "website_elsewhere" in methods and " " not in key and (
                key in FIRST_NAME_KEYS or is_place_key(key) or is_gazetteer_place(key.title())):
            return False  # "Rachel", "Marianne", "Colorado": the name of a site given elsewhere
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
        # MISC (and an unseen label) does not count against a name: with no non-org
        # label at all, nothing speaks against it.
        return not (other > 0 and other >= 3 * org and org < 3)

    POS_SAMPLES = 8   # distinct contexts tagged per one-word name

    def _record_pos_sample(self, key: str, surface: str, text: str, start: int, lang: str) -> None:
        """Keep the sentence around a one-word name (see single_word_name) for its POS
        votes; up to POS_SAMPLES distinct (sentence, language) contexts per key."""
        if not lang or start < 0 or not single_word_name(surface):
            return
        samples = self.pos_samples.get(key)
        if samples is not None and len(samples) >= self.POS_SAMPLES:
            return
        sent = text
        if len(text) > 2 * _TAG_WINDOW:
            for a, b in split_sentences(text):
                if a <= start < b:
                    sent = text[a:b]
                    break
        mark = (key, hashlib.sha1(f"{lang}\x1f{sent}".encode("utf-8")).hexdigest())
        if mark in self._pos_seen:
            return
        self._pos_seen.add(mark)
        self.pos_samples[key].append((surface, sent, lang))

    def finish_corpus_votes(self) -> int:
        """Tag the contexts collected for one-word names (with tagger-only spaCy
        pipelines, language by language) and keep key -> (votes, common votes) in
        pos_votes. Safe to call more than once: later calls only tag keys that have no
        votes yet. Returns the number of keys with votes. vet() calls it if needed."""
        import time
        if self.spacy_model.lower() == "none":
            self._count_phrases()
            self.pos_votes = self.pos_votes or {}
            return len(self.pos_votes)
        self._count_phrases()
        votes = dict(self.pos_votes or {})
        todo = {k: v for k, v in self.pos_samples.items() if v and k not in votes}
        if todo:
            t0 = time.time()
            resolve = self.model_name_for if self.spacy_model.lower() == "auto" else None
            new = _pos_votes(todo, resolve)
            votes.update(new)
            n_common = sum(1 for k in new if is_common_vote(new[k]))
            log.info("Part-of-speech votes for %d one-word names (%d used as ordinary words) "
                     "in %.1f s", len(new), n_common, time.time() - t0)
        self.pos_votes = votes
        return len(votes)

    def _count_phrases(self) -> None:
        """One pass over the corpus: lower-case uses ("climate justice") and capitalised
        uses not at the start of a sentence nor after another capitalised word ("for
        Climate Justice", not "Nordic Climate Justice") of every two- and three-word
        name met so far."""
        want = {tuple(w.casefold() for w in s.split()) for s in self._phrase_surfaces.values()}
        want -= set(self._phrase_counts)
        if not want or not self._corpus_text:
            return
        counts = {k: [0, 0] for k in want}
        prev_tok = ""
        toks = _PHRASE_TOKEN.findall(self._corpus_text)
        low = [t.casefold() for t in toks]
        for i in range(len(toks)):
            for n in (2, 3):
                k = tuple(low[i:i + n])
                c = counts.get(k) if len(k) == n else None
                if c is None:
                    continue
                g = toks[i:i + n]
                if all(w == w.casefold() for w in g):
                    c[0] += 1
                elif g[0][:1].isupper() and i > 0:
                    prev_tok = toks[i - 1]
                    if prev_tok not in _PHRASE_STOPS and not prev_tok[:1].isupper():
                        c[1] += 1
        self._phrase_counts.update(counts)

    def is_lowercase_phrase(self, key: str) -> bool:
        """Is this two- or three-word name an ordinary phrase of the corpus: written in
        lower case at least 3 times and at least half as often as capitalised in a
        sentence ("Climate Justice", "Social Movements")? Only for names found by weak
        methods alone (vet() asks only for those)."""
        surface = self._phrase_surfaces.get(key)
        if not surface:
            return False
        c = self._phrase_counts.get(tuple(w.casefold() for w in surface.split()))
        return bool(c) and c[0] >= 3 and c[0] >= 0.5 * c[1]

    def pos_summary(self, key: str) -> Tuple[int, int]:
        """(votes, votes for a common part of speech) of a one-word name; (0, 0) when
        it has none."""
        return (self.pos_votes or {}).get(key, (0, 0))

    def is_common_pos(self, key: str) -> bool:
        """Is this one-word name an ordinary word in its own contexts: a weekday, or at
        least 2 POS votes with at least half of them a common part of speech (noun,
        adjective, verb...; see COMMON_POS)? Institutional compounds ("Bundesregierung")
        and hyphenated names ending in an organisational word ("Rosa-Luxemburg-Stiftung")
        are exempt. Callers skip names that a strong method supports (STRONG_METHODS)."""
        samples = self.pos_samples.get(key) or ()
        surface = samples[0][0] if samples else key
        if " " in key.strip() or _pos_exempt(surface) or _pos_exempt(key):
            return False
        if key in WEEKDAYS:
            return True
        return is_common_vote(self.pos_summary(key))

    def is_lowercase_word(self, key: str) -> bool:
        """Is this one-word key a word the corpus writes in lower case (see
        prepare_corpus, lowercase_words)? Institutional compounds are exempt."""
        return (" " not in key and key in self._lowercase_keys and not _pos_exempt(key))

    def merge_key(self, name: str) -> str:
        """The organisation key for `name` (a name or a canonical key) in this run.

        Spellings that differ only in spacing or hyphens ("Geef Tegengas" and "Geef Tegen
        Gas", "Shell Must Fall" and "Shellmustfall") get the key of the spelling seen
        first; canonical_key already merges case, accents, umlaut spellings, elided
        articles and hyphens. The display name is chosen later from the surface forms,
        so the most frequent spelling is shown whichever key was seen first. Keys of
        three letters or fewer are never merged.

        A lower-case string of plain words is taken to be a key already and is not
        keyed again: canonical_key is not idempotent for a glued first word ("A-camp"
        has the key "a camp", which keyed again would lose its "a" and become "camp")."""
        key = name if _KEY_SHAPE.fullmatch(name) and name == name.casefold() else canonical_key(name)
        if not key:
            return key
        return self._merge_keys.setdefault(_squash_of_key(key), key)

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
                if nxt in _CAP_INDEFINITES:
                    break   # "Kraftwerk Eine Gruppe vorm Tor": a capitalised article starts a new phrase
                if _is_cap(nxt):
                    if j > last_cap and nxt.casefold() in MONTHS:
                        break   # "Extinction Rebellion met on Tuesday": a date, not part of the name
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
            role = ROLE_OF.match(text, start, end)
            if role and _is_cap(text[role.end():end]):
                start = role.end()       # "Presseteam von Extinction Rebellion" -> "Extinction Rebellion"
                continue
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
        return start, _trim_end(text, start, end)

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
        if not distinctive and not acronym:
            # two organisational words of different kinds joined by a preposition are a
            # name: "Colectivo en Resistencia", "Collectif en Lutte" (not "Working Group")
            heads = [k for k, t in enumerate(low) if t in HEAD_TO_TYPE]
            if (len(heads) >= 2 and heads[1] - heads[0] >= 2 and all(t in HEAD_TO_TYPE for t in content)
                    and HEAD_TO_TYPE[low[heads[0]]] != HEAD_TO_TYPE[low[heads[1]]]
                    and all(t in CONNECTORS for t in low[heads[0] + 1:heads[1]])):
                return typ
        return typ if (distinctive or acronym) else None

    # ---------------------------------------------------------------- extract
    def _pre_appositive(self, before: str):
        """pre_appos.search(before), run only when the last word could end a descriptor."""
        m = _APPOS_TAIL.search(before)
        w = m.group(1).lower() if m else ""
        if w and (w in self._appos_last or w.startswith(_APPOS_STEMS)):
            return self.pre_appos.search(before)
        return None

    @staticmethod
    def _website_names(text: str, sites: Dict[str, str], prefixes: Set[str]) -> List[Tuple[int, int, str]]:
        """Runs of up to 6 words, starting with a capitalised word (or an article before
        one) and joined only by spaces or hyphens, that spell a website name in `sites`
        ("Shell must fall" for shellmustfall.org, "Stay grounded" for stay-grounded.org,
        "Les soulèvements de la terre" for lessoulevementsdelaterre.org). `prefixes` holds
        every prefix of every name, so a run is extended only while it can still match."""
        toks = list(TOKEN_RE.finditer(text))
        squashed: Dict[int, str] = {}

        def sq(k: int) -> str:
            if k not in squashed:
                squashed[k] = _plain_squash(strip_elision(toks[k].group(0)))
            return squashed[k]
        out = []
        i = 0
        while i < len(toks):
            a = toks[i]
            word = a.group(0)
            article = word.casefold() in ARTICLES and i + 1 < len(toks)
            if not (article or _is_cap(word)):
                i += 1
                continue
            # quick test: can a site name begin here (this word, or an article and the next)?
            start = (strip_elision(word) if "'" in word or "’" in word else word).casefold()
            if article:
                start += toks[i + 1].group(0).casefold()
            start3 = start[:3].translate(_ACCENT_FOLD)
            if len(start3) == 3 and start3.isalpha() and start3 not in prefixes:
                if not article or toks[i + 1].group(0)[:3].casefold().translate(_ACCENT_FOLD) not in prefixes:
                    i += 1
                    continue
            prev = toks[i - 1].group(0) if i > 0 else ""
            if (prev and _is_cap(prev) and re.fullmatch(r"\s{1,3}", text[toks[i - 1].end():a.start()])
                    and not {prev.casefold()} & (ARTICLES | EVENT_WORDS | EVENT_LAST_WORDS | LEADING_STRIP)):
                i += 1     # "Scottish Parliament" (parliament.scot): part of a longer name
                continue   # (but "Tour Alternatiba", "Camp Code Rood" name the organiser)
            best, joined, j = None, "", i
            while j < len(toks) and j < i + 6:
                if j > i and not re.fullmatch(r"\s{1,3}|-|[’']\s?", text[toks[j - 1].end():toks[j].start()]):
                    break
                joined += sq(j)
                if joined not in prefixes:
                    break
                if joined in sites and toks[j].group(0).casefold() not in CONNECTORS:
                    best = (j, joined)
                j += 1
            if best is None:
                i += 1     # (after an article: "les Amis de la Terre" is tried from "Amis")
                continue
            if _is_cap(word) or _is_cap(toks[i + 1].group(0)):
                b = toks[best[0]]
                ws = a.start() + (len(a.group(0)) - len(strip_elision(a.group(0))))
                if not is_place_key(canonical_key(text[ws:b.end()])):
                    out.append((ws, b.end(), sites[best[1]]))
                    i = best[0] + 1
                    continue
            i += 1
        return out

    def _split_known_left(self, text: str, start: int, end: int, conjunctions: Set[str]) -> List[Tuple[int, int]]:
        """Split an entity "X and Y" when spaCy tags X alone as an organisation elsewhere
        ("Greenpeace and Civil Rights Defenders", "Exxon & Shell") and Y starts with a
        capitalised word. Names whose coordination follows "for / für / pour / against …"
        ("Bund für Umwelt und Naturschutz") or continues in lower case ("Ligue des Droits
        de l'Homme et du Citoyen") stay whole."""
        toks = list(TOKEN_RE.finditer(text, start, end))
        for k in range(1, len(toks) - 1):
            if toks[k].group(0).casefold() not in conjunctions:
                continue
            left_low = [t.group(0).casefold() for t in toks[:k] if not _is_cap(t.group(0))]
            if any(w in PURPOSE_PREPOSITIONS - {"of"} for w in left_low):
                return [(start, end)]
            j = k + 1
            while j < len(toks) - 1 and toks[j].group(0).casefold() in ARTICLES:
                j += 1
            if not _is_cap(toks[j].group(0)) or not self._org_elsewhere(text[start:toks[k - 1].end()]):
                continue
            if "of" in left_low and not self._org_elsewhere(text[toks[j].start():end]):
                # "Friends of the Earth and Greenpeace" splits when both halves are named on
                # their own elsewhere; "Ministry of Justice and Home Affairs" stays whole.
                return [(start, end)]
            return [(start, toks[k - 1].end())] + self._split_known_left(text, toks[j].start(), end, conjunctions)
        return [(start, end)]

    def _org_elsewhere(self, surface: str) -> bool:
        """Has spaCy labelled this exact name ORG at least twice in the paragraphs seen so
        far (this page included), and more often than any non-organisation label? (A
        one-word name the POS votes show to be an ordinary word, once they are tagged,
        is not.)"""
        key = canonical_key(tidy_surface(surface))
        if key in self.watchlist.canonical:
            return True   # a watchlist name ("Greenpeace" in "Greenpeace and Civil Rights Defenders")
        if self.pos_votes is not None and self.is_common_pos(key):
            return False
        votes = self.label_votes.get(key)
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
            self._phrase_case[key] = self._phrase_case_counts(body)
        return self._phrase_case[key]

    def _phrase_case_counts(self, body: str) -> bool:
        """Is `body` written capitalised, as a name of its own, at least as often as in
        lower case in the corpus? Capitalised uses inside a longer capitalised name
        ("Nordic Climate Justice Coalition" for "Climate Justice") do not count.
        Uses str.find on a lower-case copy: a regular expression over the whole corpus
        took seconds per name on large runs."""
        text, low, needle = self._corpus_text, self._corpus_low, body.lower()
        n, lower, upper, i = len(needle), 0, 0, low.find(needle)
        while i >= 0 and n:
            j = i + n
            if not (i > 0 and (low[i - 1].isalnum() or low[i - 1] == "_")) and not (
                    j < len(low) and (low[j].isalnum() or low[j] == "_")):
                found = text[i:j]
                if found == needle:
                    lower += 1
                else:
                    prev = re.search(r"([^\W\d_][\w’'\-]*)[ ]$", text[max(0, i - 40):i])
                    nxt = re.match(r" ([^\W\d_])", text[j:j + 3])
                    if not ((prev and prev.group(1)[:1].isupper()) or (nxt and nxt.group(1).isupper())):
                        upper += 1
            i = low.find(needle, i + 1)
        return upper >= lower

    def _generic_movement_pre(self, pre, surface: str, s: int, e: int, ents) -> bool:
        """A one-word "name" after a movement descriptor that is not a group: an -ism
        ("the German climate movement Tokenism can ...") or an ordinary word of the site.
        Names spaCy tags as an organisation here ("le mouvement Alternatiba") are kept."""
        words = re.findall(r"[^\W\d_]+", pre.group(1).casefold())
        if not words or words[-1] not in _MOVEMENT_WORDS:
            return False
        if any(a < e and s < b and lab in ("ORG", "NORP_ORG") for a, b, lab in ents):
            return False
        return bool(_ISM.search(surface)) or (self.mid_caps is not None and self.is_ordinary_words(surface))

    def _modifier_word(self, surface: str, after: str, lang: str) -> bool:
        """Is this single word, found only by NER, used as a modifier of the lower-case
        noun after it ("Drax power station", "the Dietenbach forest", "Ecotopia bike
        tour", "Danish movement")? Only in languages whose adjectives and noun modifiers
        come first (not French, Italian, Spanish...; not German, which capitalises nouns).
        Acronyms ("NATO summit", "IPPNW camp") and lower-case organisational heads (left
        to the head rules) do not count. With a corpus, the word must be used this way
        nearly everywhere it occurs, so that "Shell" in "Shell shareholders" stays a name
        when the site also writes "Shell" on its own."""
        if lang in _ADJ_AFTER_NOUN_LANGS or lang == "de":
            return False
        if surface.isupper() or re.search(r"\d|.[A-Z]", surface) or not _is_cap(surface):
            return False

        def noun_follows(tail: str) -> bool:
            m = re.match(r" ([^\W\d_A-ZÀ-Ý][^\W\d_]{2,})(?![\w\-’'])", tail)
            if not m:
                return False
            w = m.group(1)
            return not (w in _NOT_NOUN_AFTER or w in HEAD_TO_TYPE or w.endswith(("ly", "ed", "ing")))

        if not noun_follows(after):
            return False
        text = self._corpus_text
        if not text:
            return True
        hit = self._modifier_memo.get(surface)
        if hit is None:
            n = mod = 0
            i = text.find(surface)
            while i >= 0:
                j = i + len(surface)
                if not (i > 0 and (text[i - 1].isalnum() or text[i - 1] in "-_")) and not (
                        j < len(text) and (text[j].isalnum() or text[j] in "-_")):
                    n += 1
                    mod += noun_follows(text[j:j + 40])
                i = text.find(surface, i + 1)
            hit = self._modifier_memo[surface] = n == 0 or mod == n or (n >= 4 and mod >= 0.8 * n)
        return hit

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
            # Both halves named on their own elsewhere (spaCy ORG twice, or the watchlist):
            # "Friends of the Earth Scotland and Extinction Rebellion" is two organisations.
            both_known = (_is_cap(toks[j].group(0)) and not elided
                          and self._org_elsewhere(text[start:left_end])
                          and self._org_elsewhere(text[right_start:end]))
            if both_known:
                return [(start, left_end), (right_start, end)]
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

    def _title_name_ok(self, surface: str) -> bool:
        """A phrase from an agenda title that may be a group's name: two words or more,
        not an event, place, document, generic or stop-listed name ("Climate Camp",
        "République Tchèque", "Camp climat" are not)."""
        words = TOKEN_RE.findall(surface)
        if len(words) < 2 or tidy_surface(surface) != surface or not self.plausible_name(surface):
            return False
        first, last = words[0].casefold(), words[-1].casefold()
        if (first in CONNECTORS or first in ARTICLES or last in CONNECTORS or last in ARTICLES
                or not strip_elision(words[0])[:1].isupper() or first == "action"
                or any(strip_elision(w).casefold() in _TITLE_EVENT_WORDS for w in words)):
            return False
        key = canonical_key(surface)
        if (not key or self.is_stopped(key, surface) or starts_with_cta(surface.split())
                or is_place_key(key) or is_gazetteer_place(surface)
                or all(canonical_key(w) in LANGUAGE_KEYS or w.casefold() in MONTHS for w in words)):
            return False
        return not (_has_event_word(surface) or _is_generic_name(surface) or _is_event_name(surface)
                    or _is_document_name(surface))

    def prime_agenda(self, pages: Sequence[Tuple[Sequence[str], Sequence[Sequence[Link]], str]]) -> int:
        """Run NER over all agenda pages (seed PDFs: paragraphs, their links, language)
        before any of them is analysed: their entity labels go into label_votes now (and
        are not counted again when pipe() meets the same paragraphs), and the group
        names in their titles are accepted over all entries. Every entry then sees the
        evidence of all agenda entries, whatever order the agendas are analysed in (it
        follows their file names). Returns how many title names were accepted."""
        before = len(self._title_names)
        for texts, links, lang in pages:
            texts = [t for t in texts]
            docs = self._pipe_models(texts, self.paragraph_languages(texts, lang), tally=True)
            for t in texts:
                if t:
                    self._pretallied[t] += 1
            if self._title_counts:
                for t, ln, d in zip(texts, links, docs):
                    if t and agenda_title_span(t):
                        self._title_slot_spans(t, d.ents if d is not None else [], ln)
        return len(self._title_names) - before

    def _title_slot_spans(self, text: str, ents, links: Sequence[Link]) -> List[Tuple[int, int]]:
        """Group names in the title of an agenda entry that NER tagged as a person or
        "MISC", or only in part ("Rood-Rouge" in "Code Rood-Rouge").

        The phrase is the title's leading phrase when the entity starts the title
        ("Stay grounded / Aviaction, ..." -> "Stay grounded"), otherwise the run of
        capitalised words around it ("organisée par Shell Must Fall, Pays-Bas"). The
        longest prefix of two words or more is kept that is in the titles of at least
        two entries, or that the entry's website or link path spells
        (".../shell-must-fall-action-call-out/"). Accepted names are remembered, so an
        entry whose title has the same name without any entity gets it too."""
        span = agenda_title_span(text) if self._title_counts else None
        if not span:
            return []
        ts, te = span
        toks = list(TOKEN_RE.finditer(text, ts, te))
        if len(toks) < 2:
            return []

        def joined(a: int, b: int) -> bool:   # only spaces between tokens a and b
            return not text[toks[a].end():toks[b].start()].strip()

        def caps(i: int) -> bool:
            return strip_elision(toks[i].group(0))[:1].isupper()

        sites = set(_sites_of([m.group(1) for m in _URL_HOST.finditer(text)]
                              + [ln.domain for ln in links if getattr(ln, "domain", "")]))
        slugs = _url_slug_words(text, links)
        candidates: List[List[int]] = []   # token index lists, longest first in each
        for ent in ents:
            lab = ent.label_
            if lab not in _TITLE_SLOT_LABELS and lab not in ("ORG", "NORP_ORG"):
                continue
            idx = [i for i, t in enumerate(toks) if t.start() < ent.end_char and ent.start_char < t.end()]
            if not idx:
                continue
            a = idx[0]
            if lab in _TITLE_SLOT_LABELS and a == 0:
                b = 0
                while b + 1 < len(toks) and b < 5 and joined(b, b + 1):
                    b += 1
            else:
                b = a
                while b + 1 <= idx[-1] and joined(b, b + 1):
                    b += 1
                start = a
                while start > 0 and joined(start - 1, start) and caps(start - 1):
                    start -= 1
                if lab not in _TITLE_SLOT_LABELS:
                    if start == a:
                        continue   # an ORG span not extended to the left is NER's own business
                else:
                    while b + 1 < len(toks) and joined(b, b + 1) and caps(b + 1):
                        b += 1
                a = start
            candidates.append(list(range(a, b + 1)))
        if self._title_names:   # names accepted in other entries: "Infosessions about Code Rood-Rouge"
            for a in range(len(toks)):
                if not caps(a):
                    continue
                b = a
                while b + 1 < len(toks) and b - a < 5 and joined(b, b + 1):
                    b += 1
                for k in range(b, a, -1):
                    if _fold_phrase(text[toks[a].start():toks[k].end()]) in self._title_names:
                        candidates.append(list(range(a, k + 1)))
                        break
        events = EVENT_WORDS | EVENT_LAST_WORDS | CONFERENCE_WORDS | _TITLE_EVENT_WORDS
        out: List[Tuple[int, int]] = []
        for c in candidates:
            # The longest prefix that recurs or spells the site is the name; when that one
            # is a place or an event, a shorter part of it is not tried ("Notre Dame des
            # Landes" does not leave "Notre Dame").
            for k in range(len(c), 1, -1):
                s, e = toks[c[0]].start(), toks[c[k - 1]].end()
                surface = text[s:e]
                folded = _fold_phrase(surface)
                spelled = folded.replace(" ", "") in sites or f" {folded} " in slugs
                if not (folded in self._title_names or self._title_counts.get(folded, 0) >= 2 or spelled):
                    continue
                nxt = strip_elision(toks[c[k]].group(0)).casefold() if k < len(c) else ""
                first = toks[c[0]].group(0)
                if (self._title_name_ok(surface) and nxt not in events
                        and not (c[0] > 0 and (canonical_key(first) in PLACES_KEYS or is_gazetteer_place(first)))
                        and not any(x < e and s < y for x, y in out)):
                    # (not with an event word right after it: "Food Autonomy Festival", "Ecotopia
                    # bike tour"; not a place and a venue inside the title: "Rotterdam Ahoy")
                    out.append((s, e))
                    self._title_names.add(folded)
                break
        return out

    def extract(self, text: str, links: Sequence[Link], doc=None) -> List[Mention]:
        cands: List[Tuple[int, Mention]] = []   # (priority, mention); lower priority wins

        for ws, we, ckey in self.watchlist.finditer(text):
            cands.append((0, Mention(text[ws:we], ckey, {"watchlist"}, "", ws, we)))

        # "XR Zagreb", "XR Norway", "FFF Lisbon": a movement acronym and a place
        for m in _ACRONYM_PLACE.finditer(text):
            words = list(re.finditer(r"[^\s\-]+(?:-[^\s\-]+)*", m.group(2)))
            for k in range(len(words), 0, -1):
                place = m.group(2)[:words[k - 1].end()].rstrip("’'")
                if is_place_key(canonical_key(place)) or is_gazetteer_place(place):
                    e = m.start(2) + len(place)
                    cands.append((2, Mention(text[m.start():e], "", {"pattern"}, "movement / campaign",
                                             m.start(), e)))
                    # A local group is more specific than the movement: a watchlist
                    # alias "XR" inside "XR Zagreb" does not turn it into "Extinction
                    # Rebellion" (a watchlist alias for the whole "XR Zagreb" still does).
                    cands = [(p, c) for p, c in cands if not (
                        "watchlist" in c.methods and m.start() <= c.start and c.end <= e
                        and c.end - c.start < e - m.start())]
                    break
        # "XR - climate occupation in Rome", "XR RÉBELLION NORDIQUE", "organised by XR.":
        # the acronym on its own (not before a capitalised word such as "XR Spring
        # Rebellion" or "XR Youth", which the other rules judge as a whole name).
        for m in _ACRONYM_ALONE.finditer(text):
            cands.append((3, Mention(m.group(1), "", {"pattern"}, "movement / campaign",
                                     m.start(1), m.end(1))))

        lang = doc.lang_ if doc is not None and doc.lang_ != "xx" else (
            confident_language(text) if len(text.split()) >= 8 else "")
        conjunctions = CONJUNCTIONS | CONJUNCTIONS_BY_LANG.get(lang, set())
        ents = [(e.start_char, e.end_char, e.label_) for e in doc.ents] if doc is not None else []
        # A table of ticks and "N. A." marks: its capitalised cells are not names.
        table = len(_TABLE_MARKS.findall(text)) >= 2
        # Names of the websites this paragraph gives or links to: a capitalised phrase that
        # spells one of them is that site's organisation ("Code Rood … code-rood.org").
        sites = _sites_of([m.group(1) for m in _URL_HOST.finditer(text)] + [ln.domain for ln in links if ln.domain])
        if not table:
            here = (self._website_names(text, sites, {n[:k] for n in sites for k in range(1, len(n) + 1)})
                    if sites else [])
            found = [(w, True) for w in here]
            if self._corpus_sites:
                found += [(w, False) for w in self._website_names(text, self._corpus_sites, self._corpus_prefixes)
                          if not any(a < w[1] and w[0] < b for a, b, _ in here)]
            for (ws, we, host), local in found:
                # A website given only in another paragraph or agenda entry is weaker
                # evidence ("website_elsewhere", vetted like NER) than one this paragraph
                # gives or links to ("website").
                cands.append((2, Mention(text[ws:we], "", {"website" if local else "website_elsewhere"},
                                         self._head_type(TOKEN_RE.findall(text[ws:we])) or "",
                                         ws, we, link_domain=host)))

        for s, e, _ in ([] if table else self._spans(text, lang)):
            s, e = self._trim(text, s, e)
            surface = text[s:e]
            toks = [t.group(0) for t in TOKEN_RE.finditer(surface)]
            # Appositive check uses the trimmed span.
            before = text[max(0, s - 80):s]
            after = text[e:e + 90]
            pre = self._pre_appositive(before)
            if pre and len(toks) == 1 and self._generic_movement_pre(pre, surface, s, e, ents):
                pre = None   # "the rest of the German climate movement Tokenism can ...": a topic
            post = self.post_appos.match(after)
            if post and (PERSON_APPOS.search(post.group(0)) or re.match(r"\s+[A-ZÀ-Ý]", after[post.end():])):
                # "Claire, une militante de l'association X": a person, not an org;
                # "In Prague, the Czech climate action group Limity Jsme My": the
                # descriptor introduces the next name
                post = None
            head_typ = self._head_type(toks)
            after_head = False
            if not head_typ and not pre and not post and lang not in _ADJ_AFTER_NOUN_LANGS:
                # "the Abolitionist Futures collective": lowercase head right after the name
                # (not in French, Italian, Spanish...: "Ferme collective" is "collective farm")
                nxt = re.match(r"\s+([\w\-]+)", after)
                if (nxt and nxt.group(1) in HEAD_TO_TYPE and nxt.group(1).islower()
                        and nxt.group(1) not in WEAK_TRAILING and not _plural_head(nxt.group(1))
                        and not re.match(r"\s+[A-ZÀ-Ý]", after[nxt.end():])
                        and not _COMPOUND_MODIFIER_BEFORE.search(before)
                        and self._name_before_head(surface, toks, before)):
                    # Not "Social movements" (a plural head is a description), not "Trainers
                    # collective Stroomversnellers" (the head introduces the next name), not
                    # "the anti-Big Tech movement" (a compound modifier).
                    if (nxt.group(1) in NAME_FINAL_HEADS
                            and re.match(r"\s+(?:area|areas|region|district|boundary|boundaries|wide)\b",
                                         after[nxt.end():])):
                        continue   # "the Falkirk council area": a territory, not the council
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
                        # A head written after the name in lower case ("the Abolitionist
                        # Futures collective") is weaker evidence than one inside the name:
                        # "pattern_after" is gated and vetted like NER.
                        cands.append((2, Mention(text[ps:pe], "", {"pattern_after" if after_head else "pattern"},
                                                 ptyp, ps, pe)))

        # A column of names (lines of a PDF joined with " · " by parse_pdf_text, a strip of
        # adjacent links): when at least half of its items carry an organisational head
        # ("Indigenous Environmental Network"), it is a list of organisations, and the
        # other items that are one whole capitalised name ("Corporación Regional
        # Yariguíes") are taken as names too.
        if not table and text.count(" · ") >= 2:
            items, pos = [], 0
            for piece in text.split(" · "):
                a, b = pos + len(piece) - len(piece.lstrip()), pos + len(piece.rstrip())
                pos += len(piece) + 3
                sp = self._spans(text[a:b], lang)
                if len(sp) == 1 and self._trim(text, a + sp[0][0], a + sp[0][1]) == (a, b):
                    toks = TOKEN_RE.findall(text[a:b])
                    items.append((a, b, toks if 2 <= len(toks) <= 8 else None))
                else:
                    items.append((a, b, None))
            headed = sum(1 for _, _, toks in items if toks and self._head_type(toks))
            if headed >= 2 and 2 * headed >= len(items):
                for a, b, toks in items:
                    if (toks and not self._head_type(toks) and not is_place_key(canonical_key(text[a:b]))
                            and not is_gazetteer_place(text[a:b])):
                        cands.append((2, Mention(text[a:b], "", {"pattern"}, "", a, b)))

        if doc is not None and not table:
            german = doc.lang_ == "de" and doc.has_annotation("POS")
            locative_lang = LOCATIVE_BY_LANG.get(doc.lang_)
            for ent in doc.ents:
                if ent.label_ not in ("ORG", "NORP_ORG"):
                    continue
                if (german and len(TOKEN_RE.findall(ent.text)) == 1
                        and ent[0].pos_ in {"NOUN", "ADJ", "VERB", "ADV", "PRON"} and not ent.text.isupper()):
                    continue   # a common German noun ("Gemeinschaft", "Küche"), not a name
                # "General Election, Reform UK", "Climáximo, Lisbon": one name per comma part;
                # "IPPNW and ICAN": two acronyms joined by a conjunction are two names
                # "Bure, France]/ Transnational": a square bracket (or a round one followed by
                # a slash) ends a name too; what follows starts a new phrase. (Round brackets
                # may be inside a name, "Trespass (Scotland) Act"; a slash may join two names.)
                # "Germany and in the port of Hamburg", "Rheinmetall and the military": a
                # conjunction followed by a lower-case preposition or article phrase starts
                # a new phrase; only the part before it can be a name.
                ent_end = ent.end_char
                cut = _CONJ_PHRASE.search(text, ent.start_char, ent_end)
                if cut:
                    ent_end = cut.start()
                parts, pos, after_bracket = [], ent.start_char, set()
                # " · " (a column of names joined by parse_pdf_text, or adjacent links) is a
                # hard boundary between two names.
                for piece in re.split(r"(,\s+|\s*·\s*|\s*(?:[\[\]|]+\s*/?|\)\s*/)\s*|"
                                      r"(?<=[A-Z0-9])\s+(?:and|et|und|y|e|en|og|och|&)\s+(?=[A-Z0-9]{2,10}\b))",
                                      text[ent.start_char:ent_end]):
                    if re.fullmatch(r"\s*(?:[\[\]|]+\s*/?|\)\s*/)\s*", piece or "-"):
                        after_bracket.add(pos + len(piece))
                    elif piece and not re.fullmatch(r",\s+|\s*·\s*|\s+(?:and|et|und|y|e|en|og|och|&)\s+", piece):
                        parts.append((pos, pos + len(piece)))
                    pos += len(piece or "")
                parts = [q for ps, pe in parts for q in self._split_known_left(text, ps, pe, conjunctions)]
                for ps, pe in parts:
                    s, e = self._trim(text, ps, pe)
                    s = _leading_place_end(text, s, e)   # "Grenoble, France Stop Micro" -> "Stop Micro"
                    surface = text[s:e]
                    if not surface.strip():
                        continue
                    if (ps in after_bracket and e == ent_end and len(TOKEN_RE.findall(surface)) == 1
                            and re.match(r"\s+[^\W\d_A-Z]", text[e:e + 3]) and not surface.isupper()):
                        continue   # "…]/ Transnational meeting of peasant struggles": the start of a phrase
                    pre_text = text[max(0, s - 25):s]
                    if _PAGINATION_BEFORE.search(pre_text):
                        continue   # "Article suivant Suivant Amaury": the author of the next post
                    if ADDRESS_BEFORE.search(pre_text) or (_ADDRESS_NUMBER.search(pre_text) and (
                            _POSTCODE_AFTER.match(text[e:e + 16]) or _place_follows(text, e))):
                        continue   # "138/140 Pleasance, Edinburgh", "rue de la Papotière", "arrêt de bus X"
                    if (re.fullmatch(r"\S+\s+(?:to|à|a|nach|naar|bis)\s+\S+", surface)
                            and re.search(r"(?i)\b(?:from|de|depuis|von|vanaf|van|da|desde)\s+$", pre_text)):
                        continue   # "from Verfeil to Castres": a route between two places
                    if _STREET_NUMBER.search(surface) and _HOUSE_NUMBER_AFTER.match(text[e:e + 8]):
                        continue   # "Heidenheimerstr. 55": a street
                    m_pc = re.search(r"\s+[A-Z]{1,2}\d{1,2}[A-Z]?$", surface)
                    if m_pc and re.match(r"\s+\d[A-Z]{2}\b", text[e:e + 5]):
                        e = s + m_pc.start()   # "Edinburgh EH8 9RR": a postcode, not part of the name
                        surface = text[s:e]
                    toks = [t.group(0) for t in TOKEN_RE.finditer(surface)]
                    if len(toks) == 1 and self._modifier_word(surface, text[e:e + 40], doc.lang_):
                        continue   # "Drax power station", "the Dietenbach forest": a modifier, not a name
                    stats = self.locative[canonical_key(tidy_surface(surface))]
                    stats[1] += 1
                    if ACTOR_PREPOSITION.search(text[max(0, s - 60):s]):
                        pass   # "activists from X", "donate to X": an organisation's context
                    elif (LOCATIVE_BEFORE.search(pre_text) or LOCATIVE_WEAK.search(pre_text)
                          or (locative_lang and locative_lang.search(pre_text))):
                        stats[0] += 1
                    cands.append((3, Mention(surface, "", {"ner"}, self._head_type(toks) or "", s, e)))

        # A group named in an agenda title that NER tagged as a person or "MISC"
        if not table and self._title_counts:
            for s, e in self._title_slot_spans(text, doc.ents if doc is not None else [], links):
                cands.append((3, Mention(text[s:e], "", {"title"}, self._head_type(TOKEN_RE.findall(text[s:e])) or "",
                                         s, e)))

        if self.use_links:
            for ln in links:
                t = strip_logo(tidy_surface(ln.text))
                if not link_text_is_name(t, ln.domain):
                    continue
                idx = text.find(ln.text) if ln.text else -1
                s, e = (idx, idx + len(ln.text)) if idx >= 0 else (-1, -1)
                if idx >= 0 and t != ln.text and text.find(t, s, e) >= 0:
                    s = text.find(t, s, e)          # "Attac Logo" in the text: the name only
                    e = s + len(t)
                toks = [x.group(0) for x in TOKEN_RE.finditer(t)]
                # "link": the text spells the linked site's name or the link goes to a home
                # page; any other link text ("Tönnies und seine Kritiker" to a news article)
                # is weak evidence, "link_text", vetted like NER.
                cands.append((4, Mention(t, "", {link_method(t, ln)}, self._head_type(toks) or "", s, e,
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
            if "watchlist" not in m.methods:
                js, je = trim_junk(m.surface)
                if (js, je) != (0, len(m.surface)):
                    if m.start >= 0:
                        m.start, m.end = m.start + js, m.start + je
                    m.surface = tidy_surface(m.surface[js:je])
            if starts_with_cta(m.surface.split()) and not m.methods & {"watchlist", "website"}:
                continue   # "Sign Up Now", "Join the ...": an instruction
            if not m.key:
                m.key = canonical_key(m.surface)
            m.key = self.merge_key(self.watchlist.canonical.get(m.key, m.key))
            if "watchlist" not in m.methods:
                if not self.plausible_name(m.surface) or self.is_stopped(m.key, m.surface):
                    continue
                words = [canonical_key(w) for w in TOKEN_RE.findall(m.surface)]
                if words and all(w in LANGUAGE_KEYS for w in words):
                    continue   # a language menu: "Deutsch English Español Français Italiano"
                if "pattern" not in m.methods and is_place_key(m.key):
                    continue   # a bare place name: "Genève", "Hambach", "Suisse", "In Germany the movement"
                if not m.methods - NER_LIKE_METHODS and is_gazetteer_place(m.surface):
                    continue   # a place the gazetteer knows: "Neckarwestheim", "Castres and Toulouse"
                if (not m.methods - NER_LIKE_METHODS and m.start >= 0 and not m.surface.isupper()
                        and m.key not in _GAZ_NOT_PLACE and not self._head_type(TOKEN_RE.findall(m.surface))
                        and _in_place_slot(text, m.start, m.end)):
                    continue   # the place of an agenda entry: "[Altdorf Forest, Germany]",
                    #            "Biesenthal, Germany.", "at Thorens-Glières, France."
                if _is_generic_name(m.surface) or _is_event_name(m.surface) or _is_document_name(m.surface):
                    continue   # "Working Group", "Media Team"; "International Anti-War Conference";
                    #            "Climate Camp Scotland Safer Spaces Policy"
                # A name that spells a website needs nothing more, unless it is made of
                # ordinary words: then it must be written capitalised at least as often as
                # in lower case ("Climate justice" for climatejustice.org is not a name).
                # When the website is only given in another paragraph or agenda entry, the
                # name must be neither ordinary words ("Signal", "Camp climat" for
                # campclimat.eu, which the French agenda uses for every climate camp) nor
                # an event series ("Tour de Natur"): only the entry that gives the site
                # ties such a name to it.
                site_named = bool(m.methods & {"website", "website_elsewhere"})
                if "website_elsewhere" in m.methods and "pattern" not in m.methods and (
                        _has_event_word(m.surface)
                        or (self.mid_caps is not None and self.is_ordinary_words(m.surface))):
                    m.methods.discard("website_elsewhere")
                    if not m.methods:
                        continue
                    site_named = "website" in m.methods
                if (site_named and "pattern" not in m.methods and self.mid_caps is not None
                        and self.is_ordinary_words(m.surface)
                        and not self._phrase_case_counts(" ".join(TOKEN_RE.findall(m.surface)))):
                    continue
                if (not m.methods & {"pattern", "pattern_after", "title"} and not site_named
                        and not self._head_type(TOKEN_RE.findall(m.surface))
                        and not self._ordinary_but_named(m.surface)):
                    if self.is_ordinary_words(m.surface):
                        continue
                    if (m.methods == {"ner"} and self.mid_caps is not None
                            and not self.capitalised_in_running_text(m.surface)):
                        continue
                if m.methods <= {"ner", "link", "link_text"} and (_has_verb_words(m.surface) or (
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
        for m in accepted:
            if lang:
                self._record_pos_sample(m.key, m.surface, text, m.start, lang)
            if m.key not in self._phrase_surfaces and _phrase_shape(m.surface):
                self._phrase_surfaces[m.key] = m.surface
        return accepted

ARTICLES = {"the", "die", "der", "das", "la", "le", "les", "l", "il", "lo", "gli", "el", "los",
            "las", "de", "het", "den", "det", "o", "a", "os", "as", "η", "ο", "το", "οι", "τα", "την",
            "τον", "της", "του"}


_PLAIN_STOP_KEYS = None   # filled below, once canonical_key exists


_COORD_SPLIT = re.compile(r",\s+|\s+(?:and|&|et|und|y|e|og|och|ja|és|și)\s+(?:(?:the|la|le|les|l’|l'|el|los|las|il|die|der|das|de)\s+)?")


def coordinated_parts(name: str) -> List[str]:
    """The names joined in "Greenpeace and Civil Rights Defenders", "Exxon & Shell" or
    "Alternatiba, ANV-COP21 et Attac": one part per conjunct, each starting with a
    capital or a digit. [name] when the name is not such a list: a conjunct in lower
    case, or a coordination after "for / pour / für / against ..." ("Bund für Umwelt
    und Naturschutz", "Society for the Protection of Birds and the Environment").
    Whether the parts are organisations of their own is for the caller to decide."""
    pieces = [p.strip() for p in _COORD_SPLIT.split(name or "")]
    if len(pieces) < 2 or not all(pieces):
        return [name]
    left_low = [w.casefold() for w in TOKEN_RE.findall(pieces[0]) if not _is_cap(w)]
    # "of" does not block: "Friends of the Earth and Greenpeace" is a list, and the caller
    # keeps "Ministry of Justice and Home Affairs" whole because "Home Affairs" is not an
    # organisation of its own.
    if any(w in PURPOSE_PREPOSITIONS - {"of"} for w in left_low):
        return [name]
    if not all(_is_cap(TOKEN_RE.findall(p)[0]) or p[:1].isdigit() for p in pieces if TOKEN_RE.findall(p)):
        return [name]
    return pieces


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


# Capitalised German indefinite articles (German nouns and titles are capitalised, so these
# often follow a name without being part of it).
_CAP_INDEFINITES = {"Eine", "Ein", "Einer", "Einem", "Einen", "Eines"}
# Languages whose adjectives follow the noun, so a lower-case "collective", "populaire" or
# "solidaire" after a capitalised word describes it rather than naming a group.
# "the anti-Big Tech movement", "pro-EU groups": a hyphenated modifier before the name
_COMPOUND_MODIFIER_BEFORE = re.compile(r"(?i)(?<![\w\-])(?:anti|pro|non|post|neo|counter|contra|ex)-[\w\-]*\s*$")


def _plural_head(word: str) -> bool:
    """A plural of an organisational head word ("movements", "collectives", "groupes")."""
    w = word.casefold()
    if w in HEAD_TO_TYPE and not w.endswith("s"):
        return False
    return any(w.endswith(suf) and w[:-len(suf)] in HEAD_TO_TYPE for suf in ("s", "es", "en", "n"))


_ADJ_AFTER_NOUN_LANGS = {"fr", "it", "es", "pt", "ca", "ro"}
_INTO_PREPOSITIONS = {"in", "into", "an", "auf", "zu", "nach", "ins", "dans", "nel", "à", "a", "en", "naar", "op"}
_APPOS_TAIL = re.compile(r"([^\W\d_]+)[\s\"“«‘]*$")
_APPOS_STEMS = ("chiamat", "denominat", "llamad", "denominad", "appel", "nomm")


def _place_follows(text: str, end: int) -> bool:
    """", Edinburgh" / ", Paris": a comma and a place right after position `end`."""
    m = re.match(r"\s*,\s*([^\W\d_][\w’'\-]*)", text[end:end + 40])
    return bool(m) and canonical_key(m.group(1)) in PLACES_KEYS


def _is_place_name(surface: str) -> bool:
    return is_place_key(canonical_key(surface)) or is_gazetteer_place(surface)


# The location slot of an agenda entry: "[Altdorf Forest, Germany]", "camp, Biesenthal,
# Germany.", "at Thorens-Glières, France.", "Frankfurt, Mainkai, Frankfurt am Main."
_SLOT_OPEN = re.compile(r"(?:^|[,;(\[]|\s[-–—/]|(?<!\w)(?:at|in|near|à|au|près de|pres de|bei|im|"
                        r"en|te|op|nabij|bij|nel|nella|a))\s*$", re.I)
_SLOT_PLACE_AFTER = re.compile(r"\s*,\s*([^\W\d_][^,.;\[\]()/]{1,40}?)\s*"
                               r"(?=$|[.;)\]]|,|\s+https?://|\s*\(|\s+\d)")


def _in_place_slot(text: str, s: int, e: int) -> bool:
    """Is text[s:e] the first part of a location ("X, Country")? It follows a comma, a
    bracket or a place preposition, and a comma and a known place come right after it,
    then the end of the slot."""
    m = _SLOT_PLACE_AFTER.match(text, e, min(len(text), e + 60))
    return bool(m and _is_place_name(m.group(1)) and _SLOT_OPEN.search(text[max(0, s - 20):s]))


def _leading_place_end(text: str, s: int, e: int) -> int:
    """"Grenoble, France Stop Micro", "Lyon, France Besiege Bayer-Monsanto": in an agenda
    line whose two languages run together, NER joins the country that ends one location
    slot to the name that starts the next sentence. Returns the offset where the name
    starts (or `s`). Only when a place and a comma come right before the span, so that
    "Attac, France Nature Environnement" is left whole."""
    toks = list(TOKEN_RE.finditer(text, s, e))
    before = re.search(r"([^\W\d_][\w’'\-]*(?:\s+[^\W\d_][\w’'\-]*)?)\s*,\s*$", text[max(0, s - 40):s])
    if len(toks) < 2 or not before or not (_is_place_name(before.group(1))
                                            or _is_place_name(before.group(1).split()[-1])):
        return s
    for k in (2, 1):
        if k < len(toks) and _is_place_name(text[s:toks[k - 1].end()]) and toks[k].group(0)[:1].isupper():
            return toks[k].start()
    return s


# Hosting platforms whose sub-domain is the site's own name ("nominesland.wordpress.com").
_SUBDOMAIN_PLATFORMS = ("wordpress.com", "blogspot.com", "noblogs.org", "blackblogs.org", "wixsite.com",
                        "github.io", "over-blog.com", "canalblog.com", "squarespace.com", "weebly.com",
                        "jimdofree.com", "home.blog", "substack.com", "carrd.co", "netlify.app",
                        "webnode.page", "tumblr.com", "riseup.net", "framasite.org")
_URL_HOST = re.compile(r"(?:https?://|(?<![\w.@/])www\.)([\w.-]+\.[a-z]{2,})", re.I)


def _sites_of(hosts: Iterable[str]) -> Dict[str, str]:
    """Website names (see site_names) -> host, leaving out social media and similar hosts."""
    sites: Dict[str, str] = {}
    for host in hosts:
        h = host.lower().removeprefix("www.")
        if not is_skip_host(h, subdomains_of_platforms=False):
            for n in site_names(h):
                sites.setdefault(n, h)
    return sites


def squash_key(name: str) -> str:
    """canonical_key without spaces, hyphens or punctuation: "ShellMustFall" and "Shell
    Must Fall" both give "shellmustfall", "ende-gelaende" gives "endegelande". Use it to
    compare spellings that differ only in spacing (it is not the organisation key)."""
    return re.sub(r"[\W_]+", "", canonical_key(name))


def squash_runs(text: str, max_words: int = 6) -> Set[str]:
    """Squashed forms of every run of up to `max_words` consecutive words of `text`
    (after canonical_key), so that "Geef Tegengas" can be found in the title "Geef
    Tegen Gas ..." on word boundaries only ("climat" is not found in "climate camp")."""
    words = [re.sub(r"[\W_]+", "", w) for w in canonical_key(text).split()]
    words = [w for w in words if w]
    return {"".join(words[i:j]) for i in range(len(words))
            for j in range(i + 1, min(len(words), i + max_words) + 1)}


def site_names(host: str) -> Set[str]:
    """Squashed names a web host spells: "www.code-rood.org" -> {"coderood"},
    "reclaimthepower.org.uk" -> {"reclaimthepower"}, "nominesland2025.wordpress.com" ->
    {"nominesland2025", "nominesland"}."""
    host = host.lower().removeprefix("www.")
    labels = [x for x in host.split(".") if x]
    if len(labels) < 2 or set(labels) & {"gov", "gouv", "gob", "govt", "gv", "admin", "mil", "bund"}:
        return set()   # a council or ministry site is named after its place ("falkirk.gov.uk")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _SUBDOMAIN_PLATFORMS:
        label = labels[-3]
    elif (len(labels) >= 3 and len(labels[-1]) == 2
          and labels[-2] in {"co", "org", "ac", "gov", "com", "net", "or", "ltd", "me"}):
        label = labels[-3]       # "reclaimthepower.org.uk"
    else:
        label = labels[-2]
    name = _plain_squash(label)
    out = {name, re.sub(r"\d+$", "", name)}            # "nominesland2025" -> "nominesland"
    out |= {re.sub(r"^(?:the|les|la|le|die|der|das|het|el|il)(?=\w{5})", "", n) for n in out}   # "themovementhub"
    return {n for n in out if len(n) >= 4}


# Accented Latin letters -> plain letters (a fast str.translate table)
_ACCENT_FOLD = {ord(c): unicodedata.normalize("NFKD", c)[0] for c in map(chr, range(0xC0, 0x250))
                if unicodedata.normalize("NFKD", c)[0] != c and unicodedata.normalize("NFKD", c)[0].isascii()}


# The same, plus typographic apostrophes and hyphens, for watchlist matching: every
# character maps to exactly one character, so match positions are valid in the original.
_WATCHLIST_FOLD = {**_ACCENT_FOLD, ord("’"): "'", ord("‘"): "'", ord("‐"): "-", ord("‑"): "-"}


def fold_for_watchlist(text: str) -> str:
    """`text` with accents folded ("Gëlande" -> "Gelande"), the same length as `text`."""
    return text.translate(_WATCHLIST_FOLD)


def _plain_squash(name: str) -> str:
    """Letters and digits only, accents and umlaut spellings folded (no plural rule)."""
    n = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c)).casefold()
    n = re.sub(r"[\W_]+", "", n.replace("&", "and"))
    return _UMLAUT_VARIANT.sub(lambda m: m.group(1) or m.group(2), n) if len(n) >= 5 else n


def _is_generic_name(surface: str) -> bool:
    """One organisational word and otherwise generic words: "Working Group", "LOCAL GROUPS",
    "Media Team". Two different heads ("Conseil national de la Résistance") may be a name."""
    words = [strip_elision(w).casefold() for w in TOKEN_RE.findall(surface)]
    content = [w for w in words if w not in CONNECTORS]
    heads = {w for w in content if w in HEAD_TO_TYPE or w in TRAILING_TO_TYPE}
    return (bool(content) and len(heads) <= 1
            and all(w in heads or w in GENERIC or w in GENERIC_MULTI for w in content))


# "COP28", "La COP30", "COP 26" (not "ANV-COP21", "Action Non-Violente COP21")
_COP_EVENT = re.compile(r"(?:(?:la|le|the|die|der|el|a)\s+)?COP\s?\d{1,2}", re.I)


def _is_event_name(surface: str) -> bool:
    """An event, not an organisation: the first or last word is an event word ("Shell AGM",
    "Rassemblement des Glières"), or a conference word is anywhere ("World Congress on
    Climate Justice"), or it is a Social Forum or a COP ("World Social Forum", "COP28")."""
    if _COP_EVENT.fullmatch(surface.strip()) or re.search(r"(?i)\bsocial\s+forum\b", surface):
        return True
    content = [w.casefold() for w in TOKEN_RE.findall(surface) if w.casefold() not in CONNECTORS]
    return bool(content) and (content[0] in EVENT_WORDS or content[-1] in EVENT_WORDS
                              or (len(content) >= 2 and content[-1] in EVENT_LAST_WORDS
                                  and not any(w in HEAD_TO_TYPE for w in content))   # not "Mainshill Solidarity Camp"
                              or any(w in CONFERENCE_WORDS for w in content))


# Last words of document titles, which NER often tags as ORG ("… Accountability Toolkit")
DOCUMENT_LAST_WORDS = {"toolkit", "handbook", "guide", "guidelines", "policy", "act", "pack", "leaflet",
                       "flyer", "brochure", "factsheet", "booklet"}


# Heads of treaties, laws and agreements: the last word ("Paris Agreement", "Kyoto
# Protocol", "Energy Charter Treaty", also on its own: "Convention"), the word before an
# "of"/"on"/"de" phrase ("Convention on Biological Diversity", "Accord de Paris"), or
# the first word of a French, Spanish or Italian title ("Pacte Migratoire").
DOCUMENT_HEADS = {"convention", "conventions", "agreement", "agreements", "protocol", "protocols",
                  "treaty", "treaties", "declaration", "declarations", "directive", "directives",
                  "charter", "accord", "accords", "pact", "abkommen", "gesetz", "vertrag", "tratado",
                  "tratados", "traité", "traités", "pacte", "protocole", "déclaration", "convenio",
                  "acuerdo", "trattato", "accordo", "convenzione"}
# ... the last word of a name of two or more words only ("Sustainable Development Goals",
# "Digital Services Act", "European Green Deal")
DOCUMENT_HEADS_IN_NAME = {"goals", "contributions", "act", "acts", "deal", "regulation", "law"}
DOCUMENT_FIRST_WORDS = {"accord", "accords", "pacte", "traité", "convention", "déclaration", "protocole",
                        "directive", "tratado", "acuerdo", "convenio", "trattato", "accordo", "convenzione"}
_DOCUMENT_OF = {"of", "on", "for", "de", "du", "des", "sur", "pour", "sobre", "para", "di", "del", "zur",
                "über", "against", "contre", "between", "entre"}
# German compounds that end in a law or treaty word ("Lieferkettengesetz", "Pariser
# Klimaschutzabkommens", "Gesellschaftsvertrag").
_DOCUMENT_COMPOUND = re.compile(r"(?i)\w{3,}(?:gesetz|gesetze|gesetzes|abkommens?|vertrags?|richtlinie|verordnung)$")
# Organisations and bodies named after a document ("Glasgow Agreement", a network; the
# "Convention Citoyenne pour le Climat", a citizens' assembly).
_DOCUMENT_NAMED_ORGS = {"glasgow agreement", "convention citoyenne pour le climat", "convention citoyenne"}


# Head words that also describe what a treaty or law is about ("Digital Services Act",
# "Safer Space Agreement", "European Union Industrial Emissions Directive", "Information
# Society Directive"): they do not make such a name an organisation.
_DOCUMENT_WEAK_HEADS = {"union", "society", "service", "services", "space", "spaces", "espace", "spazio",
                        "espacio", "project", "projet", "projekt", "proyecto", "progetto"}


def _org_head_in(words: Sequence[str]) -> bool:
    """Does one of the words name an organisation (a head word, or a German/Dutch
    compound ending in one: "Klimabündnis")?"""
    for w in words:
        low = strip_elision(w).casefold()
        if low in DOCUMENT_HEADS or low in DOCUMENT_HEADS_IN_NAME or low in _DOCUMENT_WEAK_HEADS:
            continue
        if low in HEAD_TO_TYPE or low in COMPOUND_HEADS:
            return True
        if len(low) > 6 and any(low.endswith(h) for h in COMPOUND_HEADS if h not in WEAK_COMPOUND_HEADS):
            return True
    return False


def _is_document_name(surface: str) -> bool:
    """A document, not an organisation: the last word is a document word and no word is
    an organisational head ("Climate Camp Scotland Safer Spaces Policy", "Trespass
    (Scotland) Act"; not "Institute for European Environmental Policy"), or a treaty or
    law (see DOCUMENT_HEADS: "Paris Agreement", "Convention on Biological Diversity",
    "Accord de Paris", "Sustainable Development Goals"), unless an organisational word
    is in the name ("Coalition for the Treaty", "Fossil Fuel Non-Proliferation Treaty
    Initiative", "Secretariat of the Convention")."""
    words = TOKEN_RE.findall(surface)
    if not words or words[-1].isupper():
        return False
    low = [strip_elision(w).casefold() for w in words]
    treaty = (low[-1] in DOCUMENT_HEADS
              or (len(words) >= 2 and (low[-1] in DOCUMENT_HEADS_IN_NAME or low[0] in DOCUMENT_FIRST_WORDS))
              or bool(_DOCUMENT_COMPOUND.match(words[-1]))
              or any(w in DOCUMENT_HEADS and low[i + 1] in _DOCUMENT_OF and not words[i].isupper()
                     for i, w in enumerate(low[:-1])))
    if treaty:
        if canonical_key(surface) in _DOCUMENT_NAMED_ORGS:
            return False
        return not _org_head_in(words)
    if len(words) < 2 or low[-1] not in DOCUMENT_LAST_WORDS:
        return False
    return not any(w in HEAD_TO_TYPE for w in low[:-1])


# ---- Agenda entry titles ("title slots") -------------------------------------------
# An agenda entry starts with its dates and then its title: "(12.10) Stay Grounded
# European Members meeting [Milan, Italy]", "– 5 au 9 octobre : Stay grounded /
# Aviaction, conférence-action ...". The small NER models often tag the group named in
# such a title as a person or "MISC" ("Shell must fall", "Stay grounded"); a name in a
# title slot is recovered when it recurs in the titles of other entries or spells the
# entry's website (see OrgExtractor._title_slot_spans).
_AGENDA_DATE_PREFIX = re.compile(
    r"^\s*(?:\(\s*[\d.\s\-–/]+\)\s*[-–]?\s*"
    r"|[–\-]\s*(?=[^:\n]{0,45}?(?:\d|dates?\b|à venir|tba\b|tbc\b))[^:\n]{1,45}?\s?:\s+)", re.I)
_TITLE_END = re.compile(r"\s*(?:\[|https?://|www\.)|[.!?](?=\s+[A-ZÀ-Þ]|\s*$)")
# Labels a title-slot name may carry. ORG spans are only extended to the left
# ("the action Code Rood-Rouge" tagged "Rood-Rouge"); ORG spans are otherwise NER's job.
_TITLE_SLOT_LABELS = {"PER", "PERSON", "MISC"}
# Words of event titles that EVENT_WORDS leaves out because they also occur in group
# names elsewhere; a title-slot name with one of them is an event ("Semaine d’actions",
# "Week-end de mobilisation", "Salon du livre anarchiste", "Appel à une manifestation").
_TITLE_EVENT_WORDS = {
    "semaine", "semaines", "week-end", "week-ends", "mobilisation", "mobilisations", "appel", "salon",
    "fête", "fete", "voyage", "grève", "greve", "printemps", "camping", "training", "trainings",
    "actions", "occupation", "strike", "streik", "sciopero", "huelga", "woche", "aktionswoche",
    "aktionstage", "settimana", "semana", "jornada", "jornadas", "giornata", "giornate", "journée",
    "journées", "rencontres", "festival-action", "infosessions", "infosession", "actiontraining",
}


def agenda_title_span(text: str) -> Optional[Tuple[int, int]]:
    """(start, end) of the title of an agenda entry that starts with its dates, or None."""
    m = _AGENDA_DATE_PREFIX.match(text)
    if not m or m.end() >= len(text):
        return None
    end = _TITLE_END.search(text, m.end())
    return m.end(), min(end.start() if end else len(text), m.end() + 220)


def _fold_phrase(surface: str) -> str:
    """Words of `surface` folded for comparison: "Shell Must Fall" and "Shell must fall"
    give "shell must fall", "Code Rood-Rouge" gives "code roodrouge"."""
    return " ".join(filter(None, (_plain_squash(strip_elision(t)) for t in TOKEN_RE.findall(surface))))


def _url_slug_words(text: str, links: Sequence["Link"] = ()) -> str:
    """The words of the URL paths in `text` and `links`, " "-joined and folded
    (".../18-may-shell-agm-shell-must-fall-action-call-out/" -> "... shell must fall ...")."""
    urls = re.findall(r"https?://\S+", text) + [getattr(ln, "href", "") or "" for ln in links]
    words = [w for u in urls for w in re.split(r"[^0-9a-z]+", u.lower().split("://", 1)[-1]) if w]
    return " " + " ".join(words) + " "


# A movement acronym followed by a place is a local group: "XR Zagreb", "XR Norway",
# "FFF Lisbon". Only acronyms that are unambiguous in this field are listed.
MOVEMENT_ACRONYMS = {"XR", "XRY", "FFF", "FfF"}
_ACRONYM_PLACE = re.compile(r"(?<![\w\-/.@#])(" + "|".join(sorted(MOVEMENT_ACRONYMS, key=len, reverse=True))
                            + r")\s+((?:[A-ZÀ-Þ][\w’'\-]*)(?:[ \-][A-ZÀ-Þ][\w’'\-]*){0,2})")
# The same acronym on its own: followed by the end of the text, punctuation, a dash, a
# lower-case word or a word in capitals, never by a capitalised word or a hyphen.
_ACRONYM_ALONE = re.compile(r"(?<![\w\-/.@#])(" + "|".join(sorted(MOVEMENT_ACRONYMS, key=len, reverse=True))
                            + r")(?![\w\-‐‑@/])(?!\s+[A-ZÀ-Þ][a-zß-ÿ])")


def _has_event_word(surface: str) -> bool:
    """Any word of the name is an event word: "Camp climat", "Tour de Natur",
    "Climate Camp Scotland", "Klimacamp Leipziger Land" (a compound ending in one)."""
    words = {strip_elision(w).casefold() for w in TOKEN_RE.findall(surface)}
    events = EVENT_WORDS | EVENT_LAST_WORDS | CONFERENCE_WORDS
    return bool(words & events) or any(w.endswith(("camp", "festival", "tour")) and len(w) > 7 for w in words)


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
        found = list(re.finditer(r"[^\s\-]+", long_form))
        words = [strip_elision(w.group(0)) for w in found]
        letters = re.sub(r"[^0-9a-z]", "", canonical_key(acr))   # "ANV-COP21" -> "anvcop21"
        for i in range(len(words) - 1, max(-1, len(words) - len(acr) - 5), -1):
            tail = words[i:]
            if len(tail) < 2 or not tail[0][:1].isupper():
                continue
            if canonical_key(tail[0])[:1] == letters[:1] and _spells(letters, tail):
                # the original spelling from that word on ("Action Non-Violente COP21")
                start = found[i].end() - len(tail[0])
                out.append((tidy_surface(long_form[start:]), acr))
                break
    return out


def block_hash(text: str) -> str:
    return hashlib.sha1(canonical_key(text).encode("utf-8")).hexdigest()[:16]


# Accent-free forms of month names, function words, generic words and lone
# organisational words ("Université", "Association"), which are never names on their own.
_PLAIN_STOP_KEYS = {canonical_key(w) for w in (MONTHS | LEADING_STRIP | GENERIC | set(HEAD_TO_TYPE))}
