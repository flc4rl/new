"""
Country of an event (R2): where an event takes place, by agenda entry.

    event_country(name, entry_text="", link="", structured="")
        -> {"countries": ["DE", ...], "names": ["Germany", ...],
            "method": "location" | "tag" | "name" | "city" | "entry" | "online"
                      | "international" | "tld" | "none",
            "matched": "the text the answer rests on"}
    country_name(iso) -> English name ("DE" -> "Germany", "INT" -> "International")

Evidence is tried in this order, and the first step that finds something wins:

  location  the structured location of the event page (Modern Events Calendar
            location box, schema.org location / addressCountry), passed in
            as `structured` ("Venue, Street, Town, Region, Country" or "Online")
  tag       bracketed tags in the event name: [DE] [NL/BE] (Germany)
            (Amsterdam, NL) (Bure, France) [INT] [Online], and French
            departement numbers such as "Pellevoisin, dans l'Indre (36)"
  name      a country name in the event name, in any of the project's
            languages, including locative forms ("en Allemagne",
            "in Deutschland", "w Polsce", "v Nemecku", "Saksassa")
  city      a city, region or well-known protest site in the event name
            (with exonyms: "Hambourg", "Cologne", "Varsovie", "La Haye");
            country and city names in the name are weighed together, and
            the method says "name" when a country name is among the answer
  online / international
            "online", "en ligne", "webinar"; "worldwide", "partout",
            "dans le monde entier", "global day of action",
            "journee mondiale", "weltweit", "across Europe"
  entry     the same searches in the full agenda entry text (seed PDFs)
  name      a nationality adjective in the event name as a weak last word
            ("camp climat autrichien", "Serbian Mountain Camp")
  tld       the country-code domain of the event link (".de" -> Germany), a
            clearly labelled last resort; generic domains (.org, .com, .eu,
            .net, .info, .io, .me, ...) are ignored

Several countries are kept when the text names several venues ("Claviere
(Italy) to Briancon (France)", "entre l'Allemagne et la France", "from
Amsterdam to Marseille"). Places that are the topic rather than the venue are
left out where the wording shows it: "solidarity with Ukraine", "war in
Ukraine", "pour la Palestine", "Free Palestine", "accord de Paris". Countries
outside Europe, and Ukraine, Russia, Belarus, Israel and Palestine, which these
agendas mostly name as a topic, count only after a venue word ("in", "a",
"near", ...), after a comma ("..., Sydney, Australia") or inside a tag. Tags
with a year ("(Berlin '22)") name an earlier edition and are ignored. When
some places carry a venue mark (a preposition, a comma slot, a tag) and others
do not, only the marked ones are kept. A city directly followed by a country ("Geneva, France") gives way to
that country.

Codes are ISO 3166-1 alpha-2 (the United Kingdom is "GB"), plus "INT" for
international / decentralised / worldwide events and "ONLINE" for online
events.

Command line:
    python gazetteer.py --selftest
    python gazetteer.py "Klimacamp in Leipzig" "Climate camp (Amsterdam, NL)"
    python gazetteer.py --events results/x/events.csv [--seed-links results/x/seed_links.json]
"""

from __future__ import annotations

import re
import sys
import unicodedata
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

# --------------------------------------------------------------------------- tables
# ISO code -> (English name, variants). Variants are written in their ordinary
# spelling and folded (accents, case, ligatures) when the module loads; a space
# or hyphen inside a variant matches either. Locative / inflected forms are
# listed where a language changes the ending ("w Polsce", "v Nemecku").
COUNTRIES: Dict[str, Tuple[str, str]] = {
    "DE": ("Germany",
           "germany|allemagne|deutschland|duitsland|germania|alemania|alemanha|alemanya|tyskland|"
           "niemcy|niemczech|niemiec|německo|německu|nemecko|nemecku|nemčija|nemčiji|njemačka|njemačkoj|"
           "nemačka|nemačkoj|немачка|немачкој|vokietija|vokietijoje|vācija|vācijā|saksamaa|saksamaal|"
           "saksa|saksassa|németország|németországban|γερμανία|германия|германии|німеччина|німеччині|"
           "bundesrepublik deutschland|alemaña"),
    "FR": ("France",
           "france|frankreich|frankrijk|francia|frança|frankrike|frankrig|francja|francji|francie|"
           "francii|francúzsko|francúzsku|francija|franciji|francuska|francuskoj|француска|prancūzija|"
           "prancūzijoje|francijā|prantsusmaa|prantsusmaal|ranska|ranskassa|franciaország|"
           "franciaországban|frantzia|franța|γαλλία|франция|франции|франція|франції"),
    "NL": ("Netherlands",
           "netherlands|the netherlands|pays-bas|hollande|niederlande|nederland|paesi bassi|países bajos|"
           "países baixos|països baixos|holland|olanda|holanda|holandia|holandii|nizozemsko|"
           "nizozemsku|holandsko|nizozemska|nizozemskoj|nizozemskem|holandija|nyderlandai|"
           "nyderlanduose|nīderlande|madalmaad|alankomaat|alankomaissa|hollanti|hollannissa|"
           "hollandia|hollandiában|herbehereak|țările de jos|ολλανδία|нидерланды|нидерландах|"
           "голландия|нідерланди|нідерландах|nederländerna|nederlandene"),
    "BE": ("Belgium",
           "belgium|belgique|belgien|belgië|belgio|bélgica|bèlgica|belgia|belgii|belgie|belgicko|"
           "belgicku|belgija|belgiji|belgijoje|beļģija|beļģijā|belgika|βέλγιο|бельгия|бельгии|"
           "бельгія|бельгії"),
    "AT": ("Austria",
           "austria|autriche|österreich|oostenrijk|østrig|østerrike|österrike|áustria|àustria|"
           "austrii|rakousko|rakousku|rakúsko|rakúsku|avstrija|avstriji|austrija|austriji|austrijoje|"
           "austrijā|itävalta|itävallassa|ausztria|ausztriában|αυστρία|австрия|австрии|австрія|австрії"),
    "CH": ("Switzerland",
           "switzerland|suisse|schweiz|zwitserland|svizzera|suiza|suíça|suïssa|suitza|szwajcaria|"
           "szwajcarii|švýcarsko|švýcarsku|švajčiarsko|švajčiarsku|švica|švici|švicarska|"
           "švicarskoj|švajcarska|švajcarskoj|šveicarija|šveice|šveits|sveitsi|svájc|svájcban|"
           "elveția|ελβετία|швейцария|швейцарии|швейцарія|sveits"),
    "GB": ("United Kingdom",
           "united kingdom|u.k.|britain|great britain|grande-bretagne|großbritannien|groot-brittannië|"
           "verenigd koninkrijk|vereinigtes königreich|royaume-uni|regno unito|gran bretagna|"
           "reino unido|gran bretaña|regne unit|erresuma batua|storbritannien|storbritannia|"
           "wielka brytania|wielkiej brytanii|velká británie|velké británii|veľká británia|"
           "veľkej británii|velika britanija|veliki britaniji|velikoj britaniji|didžioji britanija|"
           "lielbritānija|suurbritannia|iso-britannia|nagy-britannia|egyesült királyság|"
           "marea britanie|regatul unit|ηνωμένο βασίλειο|βρετανία|великобритания|великобритании|"
           "велика британія|england|angleterre|engeland|inghilterra|inglaterra|anglia|anglii|"
           "scotland|écosse|écosses|schottland|schotland|scozia|escocia|escócia|szkocja|szkocji|"
           "skotsko|wales|pays de galles|cymru|galles|northern ireland|irlande du nord|nordirland|"
           "noord-ierland|north of england|nord de l'angleterre"),
    "IE": ("Ireland",
           "ireland|irlande|irland|ierland|irlanda|irlandia|irlandii|irsko|irsku|írsko|írsku|irska|"
           "irskoj|irskem|airija|airijoje|īrija|iirimaa|irlanti|irlannissa|írország|éire|"
           "republic of ireland|ιρλανδία|ирландия|ирландии|ірландія"),
    "IT": ("Italy",
           "italy|italie|italien|italië|italia|itália|itàlia|włochy|włoszech|itálie|itálii|"
           "taliansko|taliansku|italija|italiji|italijoje|itālija|itālijā|itaalia|italiassa|"
           "olaszország|olaszországban|ιταλία|италия|италии|італія|італії"),
    "ES": ("Spain",
           "spain|espagne|spanien|spanje|spagna|españa|espanha|espanya|espainia|spania|hiszpania|"
           "hiszpanii|španělsko|španělsku|španielsko|španielsku|španija|španiji|španjolska|"
           "španjolskoj|ispanija|ispanijoje|spānija|spānijā|hispaania|espanja|espanjassa|"
           "spanyolország|spanyolországban|ισπανία|испания|испании|іспанія|іспанії|шпанија|"
           "catalunya/spain|estado español|état espagnol"),
    "PT": ("Portugal",
           "portugal|portogallo|portugalia|portugalii|portugalsko|portugalsku|portugalska|"
           "portugalskem|portugali|portugalissa|portugalija|portugalijoje|portugāle|portugália|"
           "portugáliában|πορτογαλία|португалия|португалии|португалія"),
    "DK": ("Denmark",
           "denmark|danemark|dänemark|denemarken|danimarca|dinamarca|danmark|dania|danii|dánsko|"
           "dánsku|danska|danskoj|danija|danijoje|dānija|taani|tanska|tanskassa|dánia|dániában|"
           "danemarca|danimarka|δανία|дания|дании|данія"),
    "SE": ("Sweden",
           "sweden|suède|schweden|zweden|svezia|suecia|suécia|suècia|sverige|szwecja|szwecji|"
           "švédsko|švédsku|švedska|švedskoj|švedija|švedijoje|zviedrija|rootsi|ruotsi|ruotsissa|"
           "svédország|suedia|σουηδία|швеция|швеции|швеція"),
    "NO": ("Norway",
           "norway|norvège|norwegen|noorwegen|norvegia|noruega|norge|norwegia|norwegii|norsko|"
           "norsku|nórsko|nórsku|norveška|norveškoj|norvegija|norvēģija|norra|norja|norjassa|"
           "norvégia|νορβηγία|норвегия|норвегии|норвегія"),
    "FI": ("Finland",
           "finland|finlande|finnland|finlandia|finlândia|finlàndia|suomi|suomessa|finlandii|finsko|"
           "finsku|fínsko|finska|finskoj|suomija|somija|soome|finnország|finlanda|φινλανδία|"
           "финляндия|финляндии|фінляндія"),
    "PL": ("Poland",
           "poland|pologne|polen|polonia|polska|polsce|polónia|polônia|polònia|polsko|polsku|"
           "poľsko|poľsku|poljska|poljskoj|poljskem|lenkija|lenkijoje|polija|polijā|poola|puola|"
           "puolassa|lengyelország|lengyelországban|πολωνία|польша|польше|польща|польщі"),
    "CZ": ("Czechia",
           "czech republic|czechia|czechy|czechach|česko|česku|česká republika|české republice|"
           "république tchèque|tchéquie|tschechien|tschechische republik|tsjechië|repubblica ceca|"
           "cechia|república checa|chequia|república tcheca|txèquia|txekia|češka|češkoj|češkem|"
           "čehija|čekija|tšehhi|tšekki|csehország|cehia|τσεχία|чехия|чехии|чехія"),
    "SK": ("Slovakia",
           "slovakia|slovaquie|slowakei|slowakije|slovacchia|eslovaquia|eslováquia|eslovàquia|"
           "eslovakia|słowacja|słowacji|slovensko|slovensku|slovačka|slovačkoj|slovaška|slovaškem|"
           "slovakija|slovākija|slovakkia|szlovákia|slovacia|σλοβακία|словакия|словаччина"),
    "HU": ("Hungary",
           "hungary|hongrie|ungarn|hongarije|ungheria|hungría|hungria|hongria|węgry|węgrzech|"
           "maďarsko|maďarsku|madžarska|madžarskem|mađarska|mađarskoj|vengrija|ungārija|ungari|"
           "unkari|magyarország|magyarországon|ungaria|ουγγαρία|венгрия|венгрии|угорщина"),
    "SI": ("Slovenia",
           "slovenia|slovénie|slowenien|slovenië|eslovenia|eslovénia|slovenija|sloveniji|słowenia|"
           "słowenii|slovinsko|slovinsku|sloveenia|szlovénia|slovēnija|σλοβενία|словения|словенія"),
    "HR": ("Croatia",
           "croatia|croatie|kroatien|kroatië|croazia|croacia|croácia|croàcia|kroazia|hrvatska|"
           "hrvatskoj|chorwacja|chorwacji|chorvatsko|chorvatsku|chorvátsko|hrvaška|hrvaškem|"
           "kroatija|horvātija|horvaatia|kroatia|horvátország|croația|κροατία|хорватия|хорватія"),
    "RS": ("Serbia",
           "serbia|serbie|serbien|servië|sérvia|sèrbia|srbija|srbiji|srbsko|srbsku|serbija|szerbia|"
           "σερβία|сербия|србија|србији|сербія"),
    "GR": ("Greece",
           "greece|grèce|griechenland|griekenland|grecia|grécia|grècia|grezia|grecja|grecji|řecko|"
           "řecku|grécko|grécku|grčija|grčiji|grčka|grčkoj|graikija|grieķija|kreeka|kreikka|"
           "kreikassa|görögország|hellas|ελλάδα|ελλάς|греция|греции|греція|гърция"),
    "RO": ("Romania",
           "romania|roumanie|rumänien|roemenië|rumania|rumanía|roménia|romênia|errumania|rumunia|"
           "rumunii|rumunsko|rumunsku|romunija|rumunjska|rumunija|rumānija|rumeenia|románia|"
           "românia|ρουμανία|румыния|румунія"),
    "BG": ("Bulgaria",
           "bulgaria|bulgarie|bulgarien|bulgarije|bulgária|bulgària|bułgaria|bułgarii|bulharsko|"
           "bolgarija|bugarska|bulgarija|bulgārija|bulgaaria|βουλγαρία|болгария|болгарія|българия"),
    "LT": ("Lithuania",
           "lithuania|lituanie|litauen|litouwen|lituania|lituânia|lituània|litwa|litwie|litva|"
           "litvě|lietuva|lietuvoje|liettua|leedu|litvánia|литва|литві|литве"),
    "LV": ("Latvia",
           "latvia|lettonie|lettland|letland|lettonia|letonia|letónia|letònia|łotwa|łotwie|"
           "lotyšsko|lotyšsku|latvija|latvijā|latvijoje|läti|lettország|λετονία|латвия|латвія"),
    "EE": ("Estonia",
           "estonia|estonie|estland|estónia|estònia|estonsko|estonsku|estónsko|estonija|estija|"
           "igaunija|eesti|eestis|viro|virossa|észtország|εσθονία|эстония|естонія"),
    "LU": ("Luxembourg",
           "luxembourg|luxemburg|lussemburgo|luxemburgo|luksemburg|lucembursko|λουξεμβούργο|люксембург"),
    "MT": ("Malta", "malta|malte"),
    "CY": ("Cyprus", "cyprus|chypre|zypern|cipro|chipre|cypr|κύπρος|кипр"),
    "IS": ("Iceland", "iceland|islande|ijsland|islanda|islandia"),
    "AL": ("Albania", "albania|albanie|albanien|albanië|shqipëria|shqiperi"),
    "BA": ("Bosnia and Herzegovina",
           "bosnia and herzegovina|bosnia-herzegovina|bosnia|bosnie|bosnien|bosnië|bosna i hercegovina|"
           "bosna|босна"),
    "ME": ("Montenegro", "montenegro|monténégro|crna gora|црна гора"),
    "MK": ("North Macedonia",
           "north macedonia|macedonia|macédoine du nord|macédoine|mazedonien|nordmazedonien|"
           "severna makedonija|северна македонија|македонија"),
    "XK": ("Kosovo", "kosovo|kosova|косово"),
    "MD": ("Moldova", "moldova|moldavie|moldawien|moldavia|mołdawia|молдова|молдавия"),
    "AD": ("Andorra", "andorra|andorre"),
    "LI": ("Liechtenstein", "liechtenstein"),
    "SM": ("San Marino", "san marino|saint-marin"),
    "TR": ("Turkey",
           "turquie|türkei|turkije|turchia|turquía|turquia|türkiye|turcja|turcji|turecko|turska|"
           "τουρκία|турция|туреччина"),
    # Usually a topic in these agendas, so these count only with a venue word (see TOPIC_PRONE).
    "UA": ("Ukraine",
           "ukraine|ukraina|oekraïne|ucraina|ucrania|ucrânia|ucraïna|ukrainie|ukrajina|ukrajině|"
           "ukrajine|ukrajini|ukrainoje|ukrajna|ukrajnában|україна|україні|украина|украине|ουκρανία"),
    "RU": ("Russia",
           "russia|russie|russland|rusland|rusia|rússia|rosja|rosji|rusko|rusku|rusija|rusijoje|"
           "krievija|venemaa|venäjä|oroszország|ρωσία|россия|россии|росія|росії"),
    "BY": ("Belarus", "belarus|biélorussie|bélarus|weißrussland|białoruś|беларусь|білорусь|белоруссия"),
    "GE": ("Georgia", "géorgie|georgien|georgië|gruzja|грузия"),
    "AM": ("Armenia", "armenia|arménie|armenien"),
    "AZ": ("Azerbaijan", "azerbaijan|azerbaïdjan|aserbaidschan"),
    "PS": ("Palestine", "palestine|palestina|palästina|palestyna|palestinian territories"),
    "IL": ("Israel", "israel|israël|izrael|ισραήλ|израиль"),
    "LB": ("Lebanon", "lebanon|liban|libanon|líbano"),
    "SY": ("Syria", "syria|syrie|syrien|siria"),
    "IQ": ("Iraq", "iraq|irak"),
    "IR": ("Iran", "iran"),
    "EG": ("Egypt", "egypt|égypte|ägypten|egitto|egipto"),
    "MA": ("Morocco", "morocco|maroc|marokko|marruecos|marocco"),
    "DZ": ("Algeria", "algeria|algérie|algerien|argelia"),
    "TN": ("Tunisia", "tunisia|tunisie|tunesien|túnez"),
    "LY": ("Libya", "libya|libye|libyen"),
    "SN": ("Senegal", "senegal|sénégal"),
    "NG": ("Nigeria", "nigeria"),
    "KE": ("Kenya", "kenya|kenia"),
    "UG": ("Uganda", "uganda|ouganda"),
    "ZA": ("South Africa", "south africa|afrique du sud|südafrika|sudáfrica|zuid-afrika"),
    "CD": ("DR Congo", "democratic republic of the congo|république démocratique du congo|rdc|congo"),
    "US": ("United States",
           "united states|états-unis|etats unis|vereinigte staaten|verenigde staten|stati uniti|"
           "estados unidos|stany zjednoczone"),
    "CA": ("Canada", "canada|kanada"),
    "MX": ("Mexico", "mexico|mexique|mexiko|méxico|messico|meksyk"),
    "BR": ("Brazil", "brazil|brésil|brasilien|brasil|brasile|brazylia"),
    "AR": ("Argentina", "argentina|argentine|argentinien"),
    "CL": ("Chile", "chile"),
    "CO": ("Colombia", "colombia|colombie|kolumbien"),
    "EC": ("Ecuador", "ecuador|équateur"),
    "PE": ("Peru", "peru|pérou"),
    "BO": ("Bolivia", "bolivia|bolivie|bolivien"),
    "VE": ("Venezuela", "venezuela"),
    "IN": ("India", "india|inde|indien"),
    "ID": ("Indonesia", "indonesia|indonésie|indonesien"),
    "PH": ("Philippines", "philippines|philippinen|filipinas"),
    "CN": ("China", "china|chine|cina"),
    "JP": ("Japan", "japan|japon|giappone"),
    "AU": ("Australia", "australia|australie|australien"),
    "NZ": ("New Zealand", "new zealand|nouvelle-zélande|neuseeland"),
    "AE": ("United Arab Emirates", "united arab emirates|émirats arabes unis"),
}
SPECIAL_NAMES = {"INT": "International", "ONLINE": "Online"}

# Country names that are also ordinary words or people's names: they count only
# when written with a capital letter, or (WEAK) only after a venue word / in a tag.
CASE_SENSITIVE = {"chile", "india", "turkey", "jordan", "georgia", "iran", "peru", "congo", "rdc",
                  "nice", "tours", "mons", "essen", "halle", "split", "bath", "reading", "cork",
                  "york", "sofia", "florence", "nancy", "angers", "bergen", "lens", "sion", "vitoria",
                  "faro", "porto", "parma", "wells", "derby", "hull", "most", "alta", "como", "brake",
                  "bure", "melle", "agen", "pau", "vienne", "arles", "tarn", "lot", "gers", "loire",
                  "landes", "meuse", "indre", "allier", "roya", "brax", "donges", "lille", "turin",
                  "washington", "grenade", "valence", "eure", "aisne", "orne", "cher", "ain", "aude",
                  "var", "gard", "doubs", "rheine", "lingen", "buir", "nis", "creuse", "champagne",
                  "provence", "lorraine", "laval", "nevers", "moria", "madeira", "lund", "derby",
                  "hull", "bath", "reading", "preston", "lincoln", "chester", "durham", "toledo",
                  "burgos", "konstanz", "jena", "gera", "harz", "chur", "biel", "lahti", "vaasa",
                  "arad", "nordland", "lappi", "barroso", "waterloo", "creil", "vitoria", "eupen",
                  "piemont", "piémont"}
WEAK = {"hollande", "roma", "nice", "tours", "mons", "essen", "halle", "split", "bath", "reading",
        "sofia", "florence", "lens", "sion", "faro", "most", "alta", "como", "brake", "washington",
        "georgia", "melle", "barroso", "waterloo", "hollande", "madeira", "lincoln", "chester"}

# Cities, regions and protest sites (with exonyms), by country. Departments and
# small places that appear in the two agendas and on the calendar are included.
PLACES: Dict[str, str] = {
    "FR": (
        "paris|lyon|lyons|lione|marseille|marseilles|marsiglia|nantes|rennes|toulouse|tolosa|bordeaux|"
        "lille|strasbourg|straßburg|grenoble|montpellier|dijon|angoulême|calais|briançon|bure|"
        "bar-le-duc|notre-dame-des-landes|nddl|zad de nddl|sainte-soline|poitou|poitou-charentes|"
        "deux-sèvres|bretagne|brittany|normandie|normandy|occitanie|provence|alsace|elsass|lorraine|"
        "lothringen|cévennes|finistère|dordogne|clermont-ferrand|limoges|rouen|nice|besançon|"
        "saint-étienne|metz|nancy|le havre|amiens|orléans|glières|plateau des glières|"
        "loire-atlantique|la clusaz|eygliers|haute-durance|échassières|allier|briouze|roya|"
        "vallée de la roya|breil-sur-roya|montgenèvre|menton|kolbsheim|valence|villepinte|le bourget|"
        "bourget|bobigny|saint-denis|montreuil|nanterre|ivry-sur-seine|île-de-france|saclay|"
        "larzac|millau|aveyron|ariège|bourgogne|burgundy|corse|corsica|toulon|avignon|arles|nîmes|"
        "perpignan|narbonne|béziers|carcassonne|pau|bayonne|biarritz|hendaye|angers|tours|le mans|"
        "caen|brest|lorient|quimper|vannes|saint-nazaire|donges|saint-malo|poitiers|la rochelle|"
        "niort|melle|mulhouse|kingersheim|colmar|belfort|chambéry|annecy|le puy-en-velay|"
        "chapareillan|écully|villeurbanne|bugey|hauteville-lompnès|melvieu|pellevoisin|indre|"
        "mayenne|meuse|haute-marne|yonne|ardèche|drôme|isère|savoie|haute-savoie|hautes-alpes|"
        "alpes-maritimes|tarn|gers|gironde|landes|vendée|sarthe|haute-vienne|morvan|limousin|"
        "auvergne|aquitaine|nouvelle-aquitaine|hauts-de-france|grand est|picardie|champagne|"
        "franche-comté|vosges|dunkerque|dunkirk|fessenheim|flamanville|tricastin|golfech|cattenom|"
        "brax|agen|gonesse|saint-colomban|sivens|roybon|lannemezan|mauzé-sur-le-mignon|la hague|"
        "cap de la hague|bas-rhin|haut-rhin|moselle|maurienne|valle de maurienne|vallée de la maurienne|"
        "hérault|gard|aude|lozère|cantal|puy-de-dôme|loire|haute-loire|rhône|ain|jura français|"
        "charente|charente-maritime|creuse|corrèze|lot-et-garonne|tarn-et-garonne|"
        "haute-garonne|pyrénées-atlantiques|hautes-pyrénées|pyrénées-orientales|bouches-du-rhône|var|"
        "vaucluse|alpes-de-haute-provence|côtes-d'armor|morbihan|ille-et-vilaine|manche|calvados|"
        "orne|eure|seine-maritime|somme|pas-de-calais|nord-pas-de-calais|oise|aisne|marne|ardennes "
        "françaises|côte-d'or|nièvre|saône-et-loire|doubs|haute-saône|territoire de belfort|"
        "meurthe-et-moselle|seine-saint-denis|val-de-marne|hauts-de-seine|essonne|yvelines|"
        "val-d'oise|seine-et-marne|loiret|cher|indre-et-loire|loir-et-cher|eure-et-loir|"
        "maine-et-loire|mayenne|sarthe|vendée|deux-sèvres|grand-ouest|pays de la loire|"
        "centre-val de loire|val de loire|champs-romain|ardèche|la clémenterie|"
        "cap de la terre|bois lejuc|mandres-en-barrois|saint-victor-et-melvieu|tré-charavu|"
        "estuaire de la loire|la petite lande|valpré|marseille-fos|fos-sur-mer|gardanne|"
        "saint-avold|cordemais|le carnet|chooz|rhône-alpes|"
        "auvergne-rhône-alpes|provence-alpes-côte d'azur|paca|côte d'azur|haute-provence|"
        "alpes-de-haute-provence|eurosatory|horville-en-ornois|ornois|bourges|chalon|mâcon|nevers|"
        "auxerre|troyes|reims|châlons|épinal|chartres|blois|vierzon|châteauroux|guéret|tulle|"
        "aurillac|rodez|albi|montauban|cahors|tarbes|foix|mont-de-marsan|périgueux|bergerac|"
        "angoulême|cognac|la roche-sur-yon|cholet|laval|alençon|évreux|cherbourg|"
        "saint-lô|saint-brieuc|morlaix|douarnenez|concarneau|lannion|guingamp|redon|"
        "châteaubriant|ancenis|clisson|pornic|paimboeuf|cordemais|plouharnel|"
        "bouchemaine|pays basque nord|iparralde|lot|creil|compiègne|beauvais|"
        "aubervilliers|pantin|saint-ouen|vitry|gennevilliers|argenteuil|cergy|évry|melun|"
        "fontainebleau|versailles|massy|orly|roissy|grenoble-alpes|notre dame des landes|"
        "bocage nantais"),
    "DE": (
        "berlin|berlino|berlijn|berlín|hamburg|hambourg|amburgo|hamburgo|munich|münchen|monaco di "
        "baviera|múnich|monachium|mnichov|cologne|köln|colonia|keulen|kolonia|düsseldorf|leipzig|"
        "lipsia|lipsk|dresden|dresde|frankfurt|francfort|francoforte|fráncfort|frankfurt am main|"
        "frankfurt (oder)|kassel|bonn|stuttgart|freiburg|fribourg-en-brisgau|friburgo|hannover|"
        "hanover|hanovre|bremen|brême|brema|erfurt|rügen|brunsbüttel|lausitz|lusace|lusatia|lusazia|"
        "łużyce|lužice|rheinland|rhineland|rhénanie|renania|rijnland|rheinisches revier|ruhr|"
        "ruhrgebiet|bavaria|bayern|bavière|baviera|beieren|sachsen|saxony|saxe|sassonia|sajonia|"
        "saksen|nordsachsen|wendland|gorleben|lützerath|hambach|hambacher forst|hambacher wald|"
        "hambi|forêt de hambach|garzweiler|keyenberg|holzweiler|grevenbroich|altdorf|altdorfer wald|"
        "dannenrod|dannenröder wald|aachen|aix-la-chapelle|aquisgrana|bochum|dortmund|essen|"
        "nürnberg|nuremberg|norimberga|wolfsburg|grünheide|kiel|heidelberg|mannheim|karlsruhe|"
        "tübingen|göttingen|magdeburg|rostock|lübeck|potsdam|halle|jena|chemnitz|cottbus|"
        "wilhelmshaven|lubmin|mukran|sassnitz|stralsund|kerpen|buir|morschenich|manheim|"
        "sündenwäldchen|nörvenich|oberhausen|duisburg|gelsenkirchen|wuppertal|münster|bielefeld|"
        "osnabrück|braunschweig|brunswick|flensburg|augsburg|regensburg|würzburg|ulm|mainz|mayence|"
        "wiesbaden|darmstadt|saarbrücken|trier|trèves|koblenz|eberswalde|brake|weser|wesercamp|"
        "elmau|schloss elmau|garmisch|garmisch-partenkirchen|schwarzwald|black forest|forêt-noire|"
        "st. georgen|st georgen im schwarzwald|thuringia|thüringen|thuringe|brandenburg|brandebourg|"
        "mecklenburg|mecklenburg-vorpommern|niedersachsen|basse-saxe|lower saxony|basse-saxony|"
        "nordrhein-westfalen|north rhine-westphalia|north rhine westphalia|rhénanie-du-nord-westphalie|"
        "hessen|hesse|schleswig-holstein|saarland|sarre|pfalz|rheinland-pfalz|rhineland-palatinate|"
        "sachsen-anhalt|saxony-anhalt|westfalen|westphalia|westphalie|datteln|pödelwitz|"
        "wehr-brennet|brennet|ahaus|lingen|gundremmingen|ramstein|büchel|fehmarn|sylt|usedom|"
        "rhin|rhine|rhein|lusace allemande|freiburg im breisgau|lüneburg|celle|uelzen|dannenberg|"
        "jülich|düren|erkelenz|mönchengladbach|neuss|krefeld|leverkusen|siegen|paderborn|"
        "gütersloh|hamm|herne|recklinghausen|marl|bottrop|mülheim|moers|bergheim|niederaussem|"
        "neurath|weisweiler|frimmersdorf|inden|jänschwalde|schwarze pumpe|welzow|boxberg|"
        "nochten|weißwasser|lippendorf|schkopau|leuna|bitterfeld|zeitz|"
        "kiel-holtenau|brunsbüttel lng|stade|emden|cuxhaven|bremerhaven|oldenburg|hildesheim|"
        "wolfenbüttel|salzgitter|schacht konrad|konrad|morsleben|grafenrheinfeld|"
        "neckarwestheim|philippsburg|isar|landshut|passau|ingolstadt|rosenheim|kempten|"
        "konstanz|bodensee|lake constance|lac de constance|lörrach|offenburg|pforzheim|"
        "heilbronn|esslingen|reutlingen|ludwigsburg|böblingen|sindelfingen|zuffenhausen|"
        "erlangen|fürth|bamberg|bayreuth|hof|coburg|jena-lobeda|weimar|gera|zwickau|plauen|"
        "görlitz|bautzen|hoyerswerda|guben|frankfurt-oder|schwedt|prenzlau|neubrandenburg|"
        "greifswald|wismar|schwerin|neumünster|lüneburger heide|harz|eifel|hunsrück|"
        "sauerland|odenwald|spessart|rhön|erzgebirge|vogtland|uckermark|prignitz|altmark|"
        "oberlausitz|niederlausitz|mittelfranken|oberfranken|unterfranken|oberbayern|"
        "niederbayern|allgäu|ostfriesland|emsland|münsterland|ostwestfalen|siegerland|"
        "rheinhessen|vorderpfalz|kraichgau|ortenau|breisgau|markgräflerland|baden|"
        "württemberg|baden-württemberg|bade-wurtemberg|wurtemberg|mainz-kastel|"
        "rheinmetall unterlüß|unterlüß|unterluss|helgoland|"
        "wasserwald|tesla-wald|hamburger hafen"),
    "NL": (
        "amsterdam|the hague|den haag|den hague|la haye|l'aia|la haya|haag|haia|rotterdam|utrecht|groningen|"
        "eindhoven|nijmegen|nimègue|volkel|zeeland|zélande|leeuwarden|friesland|frisia|"
        "delfzijl|eemshaven|schiphol|maastricht|arnhem|leiden|leyde|haarlem|tilburg|breda|zwolle|"
        "enschede|ijmuiden|wageningen|deventer|apeldoorn|almere|den helder|vlissingen|"
        "terneuzen|borssele|dodewaard|noord-holland|zuid-holland|north holland|south holland|"
        "gelderland|overijssel|drenthe|flevoland|noord-brabant|brabant néerlandais|"
        "amsterdam-noord|port of amsterdam|port d'amsterdam|westpoort|lelystad|amersfoort|"
        "hilversum|zaandam|alkmaar|hoorn|purmerend|velsen|beverwijk|dordrecht|delft|gouda|"
        "zoetermeer|westland|schiedam|vlaardingen|maasvlakte|europoort|moerdijk|bergen op zoom|"
        "roosendaal|den bosch|'s-hertogenbosch|bois-le-duc|venlo|heerlen|sittard|geleen|"
        "chemelot|assen|emmen|hengelo|almelo|harderwijk|ede|veenendaal|zeist|houten|"
        "nieuwegein|ijsselmeer|waddenzee|wadden|texel|terschelling|ameland"),
    "BE": (
        "brussels|bruxelles|brussel|bruselas|bruxelas|bruksela|brüssel|bruxelles-capitale|"
        "liège|luik|lüttich|liegi|lieja|antwerp|anvers|antwerpen|anversa|amberes|gent|ghent|gand|"
        "gante|namur|namen|floreffe|charleroi|leuven|louvain|ostend|oostende|ostende|brugge|"
        "bruges|brujas|mechelen|malines|hasselt|kortrijk|courtrai|tournai|doornik|virton|arlon|"
        "la louvière|wavre|ottignies|louvain-la-neuve|flanders|vlaanderen|flandre|flandres|"
        "flandern|fiandre|wallonia|wallonie|wallonien|vallonia|tihange|zeebrugge|zeebruges|"
        "mons|seraing|verviers|eupen|spa|dinant|bastogne|marche-en-famenne|"
        "gembloux|nivelles|waterloo|halle-vilvoorde|vilvoorde|zaventem|aalst|alost|"
        "dendermonde|sint-niklaas|genk|lommel|turnhout|geel|mol|dessel|roeselare|ieper|ypres|"
        "brabant wallon|brabant flamand|hainaut|henegouwen|luxembourg belge|province de liège|"
        "province de namur|limbourg belge|west-vlaanderen|oost-vlaanderen|campine|kempen|"
        "molenbeek|anderlecht|schaerbeek|ixelles|saint-gilles|uccle|etterbeek"),
    "AT": (
        "vienna|wien|vienne|viena|wiedeń|vídeň|viedeň|dunaj|beč|bécs|βιέννη|вена|відень|graz|"
        "traiskirchen|linz|salzburg|salzbourg|salisburgo|innsbruck|ottensheim|klagenfurt|villach|"
        "st. pölten|sankt pölten|bregenz|tyrol|tirol|tirolo|styria|steiermark|styrie|carinthia|"
        "kärnten|carinthie|vorarlberg|burgenland|fehring|lobau|schwechat|steyr|dornbirn|"
        "feldkirch|leoben|kapfenberg|wiener neustadt|baden bei wien|krems|mödling|"
        "niederösterreich|oberösterreich|lower austria|upper austria|basse-autriche|"
        "haute-autriche|st. marx|sankt marx|mühlviertel|waldviertel|weinviertel|salzkammergut|"
        "zwentendorf|hainburg|gänserndorf|zistersdorf"),
    "CH": (
        "zurich|zürich|zurigo|zúrich|zurych|basel|bâle|basilea|basle|bazylea|bern|berne|berna|"
        "genève|geneva|genf|ginevra|ginebra|genewa|lausanne|losanna|la sarraz|lugano|luzern|lucerne|"
        "lucerna|winterthur|st. gallen|saint-gall|san gallo|fribourg (ch)|freiburg im üechtland|"
        "neuchâtel|neuenburg|sion|sitten|davos|valais|wallis|vallese|ticino|tessin|bienne|biel|"
        "chur|coire|thun|thoune|zug|schaffhausen|schaffhouse|aarau|solothurn|soleure|olten|"
        "uri|schwyz|glarus|graubünden|grisons|grigioni|appenzell|thurgau|thurgovie|"
        "jura suisse|vaud|vaud canton|yverdon|montreux|vevey|nyon|morges|renens|la chaux-de-fonds|"
        "delémont|martigny|monthey|sierre|brig|locarno|bellinzona|mendrisio|chiasso|"
        "beznau|gösgen|leibstadt|mühleberg|kaiseraugst|mormont|zad de la colline"),
    "CZ": (
        "prague|praha|prag|praga|praag|brno|brünn|ostrava|ostrau|plzeň|pilsen|horní jiřetín|"
        "moravia|morava|mähren|moravie|moravian-silesian|moravian-silesian region|"
        "moravskoslezský kraj|bohemia|čechy|böhmen|bohême|ústí nad labem|liberec|olomouc|"
        "české budějovice|budweis|hradec králové|pardubice|zlín|jihlava|karlovy vary|karlsbad|"
        "most|bílina|temelín|dukovany|severočeský|severní čechy|north bohemia|krušné hory|"
        "šumava|hrádek nad nisou|chomutov|teplice|litvínov|kadaň"),
    "SK": ("bratislava|pressburg|presbourg|košice|kassa|kaschau|žilina|banská bystrica|nitra|"
           "prešov|trnava|trenčín|mochovce|jaslovské bohunice|tatras|vysoké tatry"),
    "PL": (
        "warsaw|varsovie|warszawa|warschau|varsavia|varsovia|varšava|kraków|krakow|cracovie|"
        "krakau|cracovia|gdańsk|danzig|gdansk|wrocław|breslau|poznań|posen|warmia|warmia-mazury|"
        "łódź|lodz|katowice|szczecin|stettin|lublin|białowieża|bialowieza|puszcza białowieska|"
        "silesia|śląsk|schlesien|silésie|slesia|turów|bogatynia|bełchatów|ostrołęka|gdynia|"
        "sopot|trójmiasto|tricity|bydgoszcz|toruń|olsztyn|białystok|rzeszów|kielce|opole|"
        "zielona góra|gorzów|koszalin|słupsk|świnoujście|swinemünde|pomerania|pomorze|"
        "mazovia|mazowsze|małopolska|lesser poland|greater poland|wielkopolska|podlasie|"
        "kashubia|kaszuby|masuria|mazury|bieszczady|tatry|zakopane|oświęcim"),
    "HU": ("budapest|debrecen|szeged|pécs|győr|miskolc|paks|balaton|lake balaton"),
    "SI": ("ljubljana|lubiana|laibach|maribor|marburg an der drau|koper|capodistria|celje|kranj|"
           "krško|piran|nova gorica"),
    "HR": ("zagreb|zagabria|agram|split|spalato|rijeka|zadar|pula|krk|osijek|"
           "dubrovnik|šibenik|varaždin|slavonia|slavonija|dalmatia|dalmacija|dalmatie|"
           "istria|istra|istrie"),
    "RS": ("belgrade|beograd|belgrado|belgrad|bělehrad|belehrad|белград|београд|novi sad|"
           "újvidék|niš|homolje|homoljie|homolje mountains|jadar|loznica|kragujevac|subotica|"
           "vojvodina|vojvodine|voïvodine|zrenjanin|pančevo|čačak|valjevo|šabac|bor|"
           "kopaonik|zlatibor|stara planina"),
    "BA": ("sarajevo|tuzla|mostar|banja luka|banjaluka|bihać|bihac|srebrenica|zenica|"
           "una-sana|velika kladuša|lipa camp"),
    "ME": ("podgorica|nikšić|budva|kotor|sinjajevina"),
    "MK": ("skopje|skopie|ohrid|bitola|kumanovo|tetovo|gevgelija"),
    "AL": ("tirana|tiranë|durrës|durres|vlorë|vlora|shkodër|shkoder|vjosa|vjosë"),
    "XK": ("pristina|prishtina|prishtinë|priština|prizren|mitrovica|peja|gjakova"),
    "BG": ("sofia|sofija|софия|varna|plovdiv|burgas|bourgas|ruse|stara zagora|veliko tarnovo|"
           "kozloduy|belene|pirin|rila|rhodopes|rodopi"),
    "RO": ("bucharest|bucarest|bucurești|bukarest|bukareszt|bukurešť|cluj|cluj-napoca|cluj napoca|"
           "kolozsvár|klausenburg|timișoara|timisoara|temeswar|iași|iasi|jassy|constanța|constanta|"
           "brașov|brasov|sibiu|hermannstadt|oradea|arad|craiova|galați|ploiești|"
           "roșia montană|rosia montana|transylvania|transylvanie|transilvania|siebenbürgen|"
           "erdély|banat|dobrogea|dobruja|danube delta|delta dunării|maramureș|"
           "bucovina|bukovina|făgăraș|retezat|hunedoara|mureș|corbeni|crișana|oltenia|muntenia|"
           "wallachia|valachie"),
    "GR": ("athens|athènes|athen|atene|atenas|ateny|athina|αθήνα|αθηνα|thessaloniki|thessalonique|"
           "salonique|saloniki|thessalonika|θεσσαλονίκη|evia|evoia|euboea|eubée|euböa|εύβοια|"
           "northern evoia|epirus|épire|ipeiros|ήπειρος|epirus region|crete|crète|kreta|creta|"
           "κρήτη|lesbos|lesvos|lesbo|λέσβος|chios|samos|kos|leros|patras|patra|πάτρα|larissa|"
           "volos|βόλος|skouries|skoyries|σκουριές|chalkidiki|halkidiki|chalcidique|χαλκιδική|"
           "anametrisi|exarchia|εξάρχεια|piraeus|pirée|pireo|πειραιάς|ioannina|γιάννενα|"
           "heraklion|ηράκλειο|chania|χανιά|rodos|ρόδος|corfu|corfou|kerkyra|κέρκυρα|"
           "kavala|alexandroupoli|evros|έβρος|idomeni|ειδομένη|moria|samothrace|peloponnese|"
           "péloponnèse|peloponnes|πελοπόννησος|thessaly|thessalie|θεσσαλία|"
           "attica|attique|attika|αττική|agrafa|άγραφα|mount pelion|pelion|πήλιο|kalamata"),
    "DK": ("copenhagen|copenhague|københavn|kopenhagen|copenaghen|kopenhaga|copenhaga|kodaň|"
           "kööpenhamina|koppenhága|aarhus|århus|odense|aalborg|ålborg|middelfart|hirtshals|"
           "esbjerg|roskilde|christiania|makvärket|jutland|jylland|jutlande|jütland|fyn|funen|"
           "fionie|bornholm|vejle|horsens|randers|kolding|herning|silkeborg|frederikshavn|"
           "skagen|helsingør|elsinore|sjælland|lolland|falster|stevns"),
    "SE": ("stockholm|sztokholm|estocolmo|stoccolma|tukholma|malmö|malmo|gothenburg|göteborg|"
           "goteborg|gotemburgo|uppsala|upsal|lund|norrköping|linköping|örebro|västerås|"
           "jönköping|helsingborg|umeå|luleå|kiruna|gällivare|sundsvall|gävle|karlstad|växjö|"
           "kalmar|visby|gotland|skåne|scania|scanie|norrland|"
           "forsmark|ringhals|oskarshamn|lysekil|preemraff|brofjorden|ojnareskogen"),
    "NO": ("oslo|bergen|trondheim|stavanger|tromsø|tromso|hammerfest|finnmark|lofoten|alta|"
           "kristiansand|drammen|fredrikstad|bodø|narvik|kirkenes|mongstad|kårstø|melkøya|"
           "førde|førdefjorden|fosen|manndalen|kåfjord|riddu riđđu|nordland|troms|"
           "vestland|rogaland|hordaland|telemark|trøndelag|oppland|hedmark|sognefjord"),
    "FI": ("helsinki|helsingfors|tampere|tammerfors|turku|åbo|espoo|vantaa|oulu|uleåborg|"
           "jyväskylä|kuopio|lahti|rovaniemi|joensuu|vaasa|pori|kotka|olkiluoto|loviisa|"
           "pyhäjoki|hanhikivi|talvivaara|sotkamo|lappi|åland|ahvenanmaa"),
    "EE": ("tallinn|reval|tallin|tartu|dorpat|narva|pärnu|ida-viru|ida-virumaa|saaremaa"),
    "LV": ("riga|rīga|ryga|daugavpils|liepāja|jūrmala|ventspils|latgale|kurzeme|vidzeme"),
    "LT": ("vilnius|wilno|vilna|kaunas|kowno|klaipėda|klaipeda|memel|šiauliai|panevėžys|"
           "visaginas|ignalina|kuršių nerija|curonian spit"),
    "IE": ("dublin|dublino|dublín|baile átha cliath|cork|galway|limerick|waterford|kilkenny|"
           "sligo|donegal|mayo|kerry|clare|wexford|wicklow|athlone|drogheda|dundalk|"
           "corrib|rossport|ballinaboy|connemara|leitrim"),
    "GB": ("london|londres|londra|londyn|londen|lontoo|λονδίνο|лондон|glasgow|edinburgh|"
           "édimbourg|edimburgo|edynburg|manchester|bristol|leeds|york|cardiff|caerdydd|liverpool|"
           "sheffield|newcastle|newcastle upon tyne|birmingham|oxford|cambridge|norfolk|"
           "gloucestershire|chepstow|st ives|st. ives|kings lynn|king's lynn|belfast|derry|"
           "londonderry|falmouth|plymouth|nottingham|leicester|brighton|portsmouth|southampton|"
           "coventry|aberdeen|dundee|inverness|swansea|lakenheath|raf lakenheath|drax|"
           "drax power station|faslane|aldermaston|burghfield|menwith hill|fairford|cornwall|"
           "cornouailles|devon|yorkshire|lancashire|cumbria|kent|sussex|preston new road|balcombe|"
           "preston|blackpool|stoke|derby|hull|sunderland|middlesbrough|teesside|durham|carlisle|"
           "exeter|bath|norwich|ipswich|colchester|reading|luton|milton keynes|northampton|"
           "peterborough|lincoln|wolverhampton|bradford|huddersfield|wakefield|doncaster|"
           "rotherham|barnsley|salford|stockport|bolton|wigan|chester|wrexham|bangor|"
           "aberystwyth|newport|stirling|orkney|shetland|hebrides|highlands|lowlands|"
           "fife|lothian|midlands|east anglia|essex|suffolk|surrey|hampshire|dorset|wiltshire|"
           "somerset|oxfordshire|berkshire|hertfordshire|cheshire|merseyside|northumberland|"
           "the lake district|lake district|snowdonia|cumbria coal mine|whitehaven|sellafield|"
           "hinkley point|sizewell|heysham|torness|dungeness|wylfa|grangemouth|mossmorran|"
           "rosebank|cambo|sumac centre|houses of parliament|westminster|whitehall|"
           "the city of london|canary wharf|heathrow|gatwick|stansted|excel london|excel centre|"
           "dsei|farnborough|stop the arms fair"),
    "IT": ("milan|milano|mailand|milán|mediolan|rome|roma|turin|torino|turim|turyn|venice|venezia|"
           "venise|venedig|venecia|wenecja|naples|napoli|neapel|nápoles|neapol|bologna|bologne|"
           "bolonia|bolonha|florence|firenze|florenz|florencia|florença|genoa|genova|gênes|genua|"
           "génova|val susa|valsusa|val de suse|val di susa|susa valley|vallée de suse|venaus|"
           "chiomonte|bussoleno|san didero|sicily|sicilia|sicile|sizilien|sardinia|sardegna|"
           "sardaigne|sardinien|cerdeña|lombardia|lombardy|lombardie|lombardei|piemonte|piedmont|"
           "piémont|piemont|trieste|triest|padova|padua|padoue|verona|vérone|bolzano|bozen|"
           "alto adige|altoadige|south tyrol|südtirol|haut-adige|trento|trente|trentino|brescia|"
           "bergamo|pisa|pise|livorno|livourne|perugia|pérouse|ancona|ancône|bari|taranto|"
           "tarente|lecce|palermo|palerme|catania|catane|messina|messine|niscemi|cagliari|sassari|"
           "puglia|apulia|pouilles|apulien|calabria|calabre|kalabrien|campania|campanie|"
           "kampanien|tuscany|toscana|toscane|toskana|emilia-romagna|émilie-romagne|veneto|"
           "vénétie|venetien|liguria|ligurie|ligurien|ventimiglia|vintimille|ventimille|"
           "clavière|claviere|oulx|bardonecchia|malalbergo|monte san pietro|arneo|le fattizze|"
           "salento|lampedusa|aosta|aoste|valle d'aosta|vallée d'aoste|ravenna|ravenne|rimini|"
           "modena|modène|parma|parme|reggio emilia|piacenza|ferrara|ferrare|udine|"
           "friuli|frioul|gorizia|pordenone|vicenza|vicence|treviso|trévise|mestre|marghera|"
           "porto marghera|rovigo|cremona|crémone|mantova|mantoue|pavia|pavie|novara|vercelli|"
           "biella|alessandria|asti|cuneo|savona|la spezia|massa|carrara|lucca|lucques|"
           "siena|sienne|arezzo|prato|pistoia|grosseto|terni|viterbo|latina|frosinone|"
           "l'aquila|pescara|chieti|campobasso|caserta|salerno|salerne|avellino|benevento|"
           "potenza|matera|cosenza|catanzaro|reggio calabria|crotone|siracusa|syracuse|ragusa|"
           "trapani|agrigento|caltanissetta|enna|nuoro|oristano|olbia|abruzzo|abruzzes|"
           "molise|basilicata|basilicate|umbria|ombrie|marche|lazio|latium|friuli-venezia giulia|"
           "val di non|val camonica|valtellina|valtelline|dolomites|dolomiti|dolomiten|"
           "taranto ilva|ilva|muos|tav torino|no tav|mondeggi|bologona"),
    "ES": ("barcelona|barcelone|barcellona|barcelone|madrid|madryt|bilbao|bilbo|valencia|"
           "valència|sevilla|seville|séville|siviglia|zaragoza|saragosse|saragossa|saragozza|"
           "tarragona|tarragone|catalonia|catalunya|catalogne|cataluña|katalonien|catalogna|"
           "katalonia|andalucía|andalusia|andalousie|andalusien|galicia|galice|galizien|galiza|"
           "soria|covaleda|pamplona|iruña|iruñea|pampelune|vitoria-gasteiz|gasteiz|vitoria|gijón|"
           "xixón|donostia|san sebastián|saint-sébastien|málaga|cádiz|cadix|granada|córdoba|"
           "cordoue|murcia|murcie|alicante|alacant|valladolid|salamanca|salamanque|oviedo|"
           "asturias|asturies|asturien|cantabria|cantabrie|santander|a coruña|la coruña|"
           "la corogne|vigo|santiago de compostela|saint-jacques-de-compostelle|mallorca|majorca|"
           "majorque|palma de mallorca|ibiza|eivissa|menorca|minorque|"
           "canary islands|canaries|canarias|islas canarias|kanaren|tenerife|gran canaria|"
           "lanzarote|fuerteventura|navarra|navarre|nafarroa|bizkaia|biscay|vizcaya|biscaye|"
           "gipuzkoa|guipúzcoa|araba|álava|euskadi|hegoalde|portugalete|atxondo|urkabustaiz|"
           "izarra|girona|gérone|gerona|lleida|lérida|extremadura|estrémadure|aragón|aragon|"
           "castilla|castille|castilla y león|castilla-la mancha|la rioja|logroño|huesca|teruel|"
           "burgos|león|palencia|zamora|segovia|ségovie|ávila|toledo|tolède|cuenca|guadalajara (es)|"
           "albacete|ciudad real|badajoz|cáceres|huelva|jaén|almería|ceuta|melilla|castellón|"
           "castelló|tortosa|reus|manresa|sabadell|terrassa|badalona|l'hospitalet|mataró|"
           "vilanova|sant cugat|figueres|empordà|ebro delta|delta de l'ebre|ebre|doñana|"
           "sierra nevada|pyrénées espagnoles|pirineo|pirineu|picos de europa|"
           "almaraz|garoña|ascó|vandellós|trillo|cofrentes"),
    "PT": ("lisbon|lisboa|lisbonne|lissabon|lisbona|lizbona|lisabon|porto|oporto|"
           "covas do barroso|barroso|montemor-o-novo|cabrela|coimbra|braga|faro|algarve|alentejo|"
           "sines|aveiro|évora|minho|trás-os-montes|madeira|madère|azores|açores|açores|"
           "setúbal|leiria|viseu|guimarães|viana do castelo|bragança|guarda|castelo branco|"
           "santarém|portalegre|beja|montalegre|mina do barroso|serra d'arga|"
           "romano|seixal|almada|barreiro|amadora|sintra|cascais|matosinhos"),
    "LU": ("luxembourg city|ville de luxembourg|esch-sur-alzette|esch|differdange|dudelange|"
           "ettelbruck|diekirch|wiltz|remich|grevenmacher|echternach"),
    "MT": ("valletta|la valette|sliema|birkirkara|gozo"),
    "CY": ("nicosia|nicosie|lefkosia|limassol|larnaca|paphos|famagusta"),
    "IS": ("reykjavík|reykjavik|akureyri|kárahnjúkar"),
    "MD": ("chișinău|chisinau|kishinev|tiraspol|transnistria|bălți"),
    "TR": ("istanbul|stamboul|estambul|istambul|stambuł|ankara|izmir|smyrne|diyarbakır|diyarbakir|"
           "amed|antalya|adana|mersin|bursa|eskişehir|gaziantep|van|batman|hasankeyf|ilısu|"
           "akkuyu|cerattepe|artvin|ikizdere|kaz dağları|ida mountains"),
    "UA": ("kyiv|kiev|kyjev|kyjiv|kijów|kijev|київ|киев|lviv|lwów|lemberg|lvov|kharkiv|kharkov|"
           "charkiw|odesa|odessa|mariupol|zaporizhzhia|dnipro"),
    "RU": ("moscow|moscou|moskau|mosca|moscú|moskwa|moskva|москва|st petersburg|saint-pétersbourg|"
           "sankt petersburg|kaliningrad"),
    # Locative / inflected forms of large cities ("w Warszawie", "v Praze", "ve Vidni").
    "PL ": ("warszawie|krakowie|gdańsku|wrocławiu|poznaniu|łodzi|katowicach|szczecinie|lublinie"),
    "CZ ": ("praze|brně|ostravě|plzni"),
    "SK ": ("bratislave|košiciach"),
    "DE ": ("berlinie|berlíně|berlíne|berliinissä|berlinben|берлине|берліні|monachium|"
            "monachiumie|hamburgu|lipsku|drezdnie|mnichově"),
    "AT ": ("wiedniu|vídni|viedni|bécsben|wiedeńiu|beču|вене|відні"),
    "FR ": ("paryżu|paříži|paríži|parizu|párizsban|parisissa|париже|парижі"),
    "GB ": ("londynie|londýně|londýne|londonban|lontoossa|лондоне|лондоні"),
    "BE ": ("brukseli|bruselu|bruseli|brüsszelben|brysselissä|брюсселе|брюсселі"),
    "HU ": ("budapeszcie|budapešti|budapesti|budapesten|будапеште"),
    "HR ": ("zagrebu|zagrebie"),
    "RS ": ("beogradu|belgradzie|bělehradě|београду"),
    "SI ": ("ljubljani|lublanie|lublani"),
    "LT ": ("vilniuje|wilnie|vilniuse"),
    "LV ": ("rīgā|rydze"),
    "EE ": ("tallinnas|tallinnie"),
    "FI ": ("helsingissä|helsinkach|helsinkách|tampereella|turussa"),
    "SE ": ("tukholmassa|sztokholmie|stockholmu"),
    "DK ": ("kopenhadze|kodani|kööpenhaminassa"),
    "NL ": ("amsterdamie|amsterodamu"),
    "IT ": ("rzymie|římě|mediolanie|miláně|turynie|wenecji|neapolu"),
    "ES ": ("madrycie|barcelonie|madridu"),
    "PT ": ("lizbonie|lisabonu"),
    "GR ": ("atenach|aténách|ateni|atenai|αθηνών|салониках|салоніках|saloniki"),
    "RO ": ("bukareszcie|bukurešti|bucureștiul"),
    "UA ": ("kijowie|kyjevě|києві|киеве|lwowie|odessie|харкові|харькове"),
    "RU ": ("moskwie|moskvě|москве|москві"),
    "BY": ("minsk|мінск|минск|grodno|hrodna|gomel"),
    "GE": ("tbilisi|tiflis|batumi"),
    "AM": ("yerevan|erevan|eriwan"),
    "AZ": ("baku|bakou"),
    "IL": ("tel aviv|jerusalem|jérusalem|haifa"),
    "PS": ("ramallah|bethlehem|hebron|nablus|jenin"),
    "EG": ("cairo|le caire|kairo|sharm el-sheikh|charm el-cheikh|sharm el sheikh"),
    "AE": ("dubai|dubaï|abu dhabi"),
    "MA": ("marrakech|marrakesh|rabat|casablanca|tangier|tanger"),
    "KE": ("nairobi",),
    "US": ("new york|new york city|washington|washington dc|washington d.c.|los angeles|"
           "san francisco|chicago"),
    "CA": ("montreal|montréal|toronto|vancouver|ottawa"),
    "BR": ("belém|belem|rio de janeiro|são paulo|sao paulo|brasília"),
    "MX": ("chiapas|mexico city|ciudad de méxico|oaxaca"),
    "AU": ("sydney|melbourne"),
    "NZ": ("aotearoa",),
}

# Countries these agendas usually name as a topic ("war in Ukraine", "Free
# Palestine", "the Mexican Zapatistas"): they count only after a venue word or in a tag.
EUROPE = {"AL", "AD", "AT", "BA", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR",
          "GB", "GR", "HR", "HU", "IE", "IS", "IT", "LI", "LT", "LU", "LV", "MC", "MD", "ME", "MK",
          "MT", "NL", "NO", "PL", "PT", "RO", "RS", "SE", "SI", "SK", "SM", "TR", "XK", "VA"}
TOPIC_PRONE = set(COUNTRIES) - EUROPE   # UA, RU, BY, PS, IL and everything outside Europe

# Nationality adjectives: a weak last resort for the event name only (see event_country).
ADJECTIVES: Dict[str, str] = {
    "DE": "german|allemand|allemande|allemands|allemandes|deutsche|deutschen|deutscher|deutsches|"
          "duitse|tedesco|tedesca|tedeschi|tedesche|alemán|alemana|alemão|alemã|niemiecki|niemiecka",
    "FR": "french|français|française|françaises|französische|französischen|französischer|franse|"
          "francese|francesi|francés|francesa|francês|franco-",
    "NL": "dutch|néerlandais|néerlandaise|hollandais|hollandaise|niederländische|niederländischen|"
          "nederlandse|olandese|holandés|holandesa",
    "BE": "belgian|belge|belges|belgische|belgischen|belga",
    "AT": "austrian|autrichien|autrichienne|autrichiens|österreichische|österreichischen|"
          "oostenrijkse|austriaco|austriaca|austríaco",
    "CH": "swiss|schweizer|schweizerische|zwitserse|svizzero",
    "GB": "british|britannique|britanniques|britische|britischen|britse|britannico|scottish|"
          "écossais|écossaise|welsh|gallois|galloise",
    "IE": "irish|irlandais|irlandaise|irische|irischen|ierse",
    "IT": "italian|italienne|italiens|italiennes|italienische|italienischen|italiaanse|italiano|"
          "italiana|italiani|italiane",
    "ES": "spanish|espagnol|espagnole|espagnols|spanische|spanischen|spaanse|spagnolo|spagnola|"
          "español|española|catalan|catalane|catalans|katalanische|catalana",
    "PT": "portuguese|portugais|portugaise|portugiesische|portugiesischen|portugese|portoghese|"
          "portugués|portuguesa",
    "DK": "danish|danois|danoise|dänische|dänischen|deense|danese|danés|danesa",
    "SE": "swedish|suédois|suédoise|schwedische|schwedischen|zweedse|svedese|sueco|sueca",
    "NO": "norwegian|norvégien|norvégienne|norwegische|norwegischen|noorse|norvegese|noruego",
    "FI": "finnish|finlandais|finlandaise|finnische|finnischen|finse|finlandese",
    "PL": "polish|polonais|polonaise|polnische|polnischen|poolse|polacco|polacca|polaco|polaca",
    "CZ": "czech|tchèque|tchèques|tschechische|tschechischen|tsjechische|ceco|checo",
    "SK": "slovak|slovaque|slowakische|slowakischen",
    "HU": "hungarian|hongrois|hongroise|ungarische|ungarischen|hongaarse|ungherese|húngaro",
    "SI": "slovenian|slovène|slowenische|slowenischen",
    "HR": "croatian|croate|croates|kroatische|kroatischen",
    "RS": "serbian|serbe|serbes|serbische|serbischen",
    "GR": "greek|grec|grecque|grecs|grecques|griechische|griechischen|griekse|greco|greca|griego|griega",
    "RO": "romanian|roumain|roumaine|rumänische|rumänischen",
    "BG": "bulgarian|bulgare|bulgarische",
    "LT": "lithuanian|lituanien|lituanienne|litauische",
    "LV": "latvian|letton|lettone|lettische",
    "EE": "estonian|estonien|estonienne|estnische",
}

# Two- and three-letter codes accepted in tags ([DE], (NL/BE), [INT]).
CODE_ALIASES = {"UK": "GB", "EL": "GR", "ITA": "IT", "FRA": "FR", "GER": "DE", "DEU": "DE",
                "ESP": "ES", "NLD": "NL", "BEL": "BE", "AUT": "AT", "CHE": "CH", "POL": "PL",
                "PRT": "PT", "DNK": "DK", "SWE": "SE", "NOR": "NO", "FIN": "FI", "GBR": "GB",
                "IRL": "IE", "CZE": "CZ", "GRC": "GR", "NRW": "DE", "BRD": "DE", "CAT": "ES",
                "USA": "US", "INT": "INT", "INTL": "INT", "EU": "INT", "EUR": "INT"}
# Codes recognised outside brackets only as a whole comma slot ("Brussels, BE"),
# minus those that are ordinary words in capitals ("NO", "IT", "IS", "IN", "ME").
SLOT_CODE_EXCLUDE = {"NO", "IT", "IS", "IN", "ME", "AM", "BY", "TO", "AS", "OR", "SO", "DO", "GO",
                     "ON", "AN", "AT", "BE", "US", "CA", "ID", "PE", "MA", "AL", "IL", "LI", "SM",
                     "AD", "EU", "MD", "CO", "AR", "AU", "CH", "NG", "SN", "IR", "IQ", "EG"}

# ccTLD -> ISO. Anything not listed (.org, .com, .net, .eu, .info, .io, .me, .tv,
# .cc, .ly, .gl, .bz, .nu, .to, .fm, .ai, .co, ...) is treated as generic.
TLD_COUNTRY = {c.lower(): c for c in EUROPE if c not in {"MD", "ME", "AD", "TO"}}
TLD_COUNTRY.update({"uk": "GB", "gr": "GR", "cat": "ES", "gal": "ES", "eus": "",  # .eus: ES or FR
                    "bzh": "FR", "corsica": "FR", "paris": "FR", "alsace": "FR",
                    "scot": "GB", "wales": "GB", "cymru": "GB", "london": "GB",
                    "berlin": "DE", "hamburg": "DE", "koeln": "DE", "cologne": "DE",
                    "bayern": "DE", "ruhr": "DE", "nrw": "DE", "saarland": "DE", "leipzig": "DE",
                    "wien": "AT", "tirol": "AT", "swiss": "CH", "zuerich": "CH",
                    "brussels": "BE", "vlaanderen": "BE", "gent": "BE",
                    "amsterdam": "NL", "frl": "NL", "barcelona": "ES", "madrid": "ES",
                    "ua": "UA", "ru": "RU", "by": "BY", "us": "US", "ca": "CA", "br": "BR",
                    "mx": "MX", "ar": "AR", "cl": "CL", "au": "AU", "nz": "NZ", "za": "ZA",
                    "ke": "KE", "ng": "NG", "il": "IL", "ps": "PS", "lb": "LB", "ma": "MA",
                    "tn": "TN", "dz": "DZ", "eg": "EG", "jp": "JP", "cn": "CN", "id": "ID",
                    "ph": "PH", "pe": "PE", "ec": "EC", "bo": "BO", "ge": "GE", "am": "AM",
                    "az": "AZ", "md": "MD", "al": "AL", "mk": "MK", "ba": "BA", "rs": "RS",
                    "is": "IS"})
for _generic in ("me", "co", "tv", "cc", "io", "ai", "to", "fm", "ly", "gl", "nu", "ws", "bz", "la",
                 "im", "ag", "sh", "ac", "gg", "gd", "vc", "lu-x"):
    TLD_COUNTRY.pop(_generic, None)

# --------------------------------------------------------------------------- folding
_TRANSLIT = str.maketrans({"ø": "o", "Ø": "o", "ł": "l", "Ł": "l", "đ": "d", "Đ": "d", "æ": "ae",
                           "Æ": "ae", "œ": "oe", "Œ": "oe", "ı": "i", "þ": "th", "ð": "d",
                           "’": "'", "‘": "'", "`": "'", "´": "'", "ʼ": "'", "“": '"', "”": '"',
                           "«": '"', "»": '"', "–": "-", "—": "-", "‐": "-", "‑": "-",
                           " ": " ", " ": " ", "ς": "σ"})


def fold(s: str) -> str:
    """Lower case without accents: "Gênes" -> "genes", "Łódź" -> "lodz"."""
    return _fold_map(s)[0]


def _fold_map(s: str) -> Tuple[str, List[int]]:
    """Folded text plus, for each folded character, its index in `s`."""
    out: List[str] = []
    idx: List[int] = []
    for i, ch in enumerate(s or ""):
        piece = ch.translate(_TRANSLIT)
        piece = "".join(c for c in unicodedata.normalize("NFKD", piece)
                        if not unicodedata.combining(c)).casefold()
        piece = piece.replace("ς", "σ")
        out.append(piece)
        idx.extend([i] * len(piece))
    return "".join(out), idx


def _key(s: str) -> str:
    return re.sub(r"[\s\-]+", " ", fold(s)).strip()


def _strip_note(v: str) -> str:
    """'bergen (be)' -> 'bergen': a bracketed note only disambiguates the table."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", v).strip()


# --------------------------------------------------------------------------- lookup tables
# folded key -> (iso, kind, original variant); kind is "country", "city" or "adj".
_LOOKUP: Dict[str, Tuple[str, str]] = {}
for _iso, (_en, _vars) in COUNTRIES.items():
    for _v in [_en] + _vars.split("|"):
        _LOOKUP.setdefault(_key(_v), (_iso, "country"))
for _iso, _vars in PLACES.items():
    _iso = _iso.strip()   # "PL " holds inflected forms for "PL"
    _vars = "|".join(_vars) if isinstance(_vars, tuple) else _vars
    for _v in _vars.split("|"):
        _v = _strip_note(_v)
        if _v and _key(_v) not in _LOOKUP:
            _LOOKUP[_key(_v)] = (_iso, "city")
_ADJ: Dict[str, str] = {}
for _iso, _vars in ADJECTIVES.items():
    for _v in _vars.split("|"):
        if not _v.endswith("-"):
            _ADJ[_key(_v)] = _iso
# Words that are places in the tables but also common words or organisation names
# ("Lot", "Most", "Celle", "Inden", "Auch"): never matched as places.
NOT_PLACES = {"most", "lot", "var", "ain", "cher", "eure", "orne", "hof", "stade", "konrad", "spa",
              "mol", "forest", "baden", "geel", "ede", "marche", "manche", "somme", "uri", "van",
              "batman", "kos", "bar", "romano", "kent", "mayo", "clare", "kerry", "guarda", "enna",
              "massa", "latina", "potenza", "ragusa", "asti", "esch", "isar", "inden", "marl", "hamm",
              "celle", "zug", "glarus", "bor", "wells", "ruse", "highlands", "lowlands", "midlands",
              "fife", "leon", "jaen", "amed", "attica", "tulle", "cognac", "limousin", "aragon", "prato",
              "rila", "pori", "kotka", "thun", "alta", "troms", "brake", "wehr", "redon"}
for _w in NOT_PLACES:
    if _LOOKUP.get(_key(_w), ("", ""))[1] == "city":
        del _LOOKUP[_key(_w)]
_CASE = {_key(w) for w in CASE_SENSITIVE}
_WEAK = {_key(w) for w in WEAK}


def _pattern(keys) -> re.Pattern:
    parts = []
    for k in sorted(set(keys), key=len, reverse=True):
        parts.append(r"[\s\-]+".join(re.escape(t) for t in k.split(" ")))
    # not inside a word, and not the first or last half of a hyphenated word
    return re.compile(r"(?<![\w])(?<!\w-)(" + "|".join(parts) + r")(?!\w)(?!-\w)")


_PLACE_RE = _pattern(_LOOKUP)
_ADJ_RE = _pattern(_ADJ)

# --------------------------------------------------------------------------- context cues
_ART = r"(?:(?:the|la|le|l'|les|der|die|das|dem|den|des|het|el|il|lo|los|las|o|os|a|στην|στη|στο|στον)\s*)?"
_VENUE_PREP = re.compile(
    r"(?:^|[\s(\[\"'/])(?:in|at|near|nearby|around|across|to|from|through|via|outside|"
    r"a|au|aux|en|dans|pres de|pres d'|pres du|proche de|autour de|vers|sur|de|d'|du|des|"
    r"bei|im|nahe|nach|um|von|zu|zum|zur|te|op|nabij|bij|i|pa|nel|nella|nei|nelle|ad|al|alla|"
    r"di|em|no|na|nos|nas|w|we|v|ve|vo|u|σε|στην|στη|στο|στον|в|во|у|на|entre|between|"
    r"zwischen|tussen|tra|fra|par|passant par|durch|depuis|jusqu'a|until|towards|toward|"
    r"bis|naar|verso|hacia|desde|hasta|ate|z|ze|iz|od|do)\s*" + _ART + r"$")
_SLOT_BEFORE = re.compile(r"(?:^|[,;:(\[|@/]|\s-\s?|\.\s|->|=>)\s*" + _ART + r"$")
_COMMA_BEFORE = re.compile(r"(?:[,;(\[|/]|\s-\s?)\s*" + _ART + r"$")   # "..., Hollande"
_SLOT_AFTER = re.compile(r"^\s*(?:$|[,;:)\].!?|/(\[]|-\s|\s-|->|=>|\d)")
_LIST_AND = re.compile(r"\b(?:and|et|und|en|y|e|i|og|och|ja|a)\b|&")
_CHAIN = re.compile(r"^\s*(?:,|and|et|und|en|y|e|i|og|och|ja|a|&|/|-|->|=>|to|or|ou|oder|"
                    r"via|then|puis|dann)?\s*" + _ART + r"$")
# Topic cues: the place is what the event is about, not where it is.
_TOPIC_NEAR = re.compile(  # directly before the place (an article may come between)
    r"(?:^|\W)(?:with|avec|mit|met|con|com|for|pour|fur|voor|per|para|pro|free|liberez|libertad|"
    r"freiheit fur|stop|end|against|contre|gegen|tegen|contro|contra|anti|accord de|accords de|"
    r"treaty of|traite de|agreement of|vertrag von|abkommen von|people of|peuple de|peuples de|"
    r"news from|refugees from|refugies de|migrants from|delegation from|delegation of|"
    r"delegation de|delegacion de|embassy of|ambassade de|ambassade d'|botschaft von|"
    r"consulat de|consulate of|occupied|occupation of|occupation de|invasion of|invasion de|"
    r"bombing of|siege of|blockade of|blocus de)\s*" + _ART + r"$")
_TOPIC_FAR = re.compile(  # up to three words before the place
    r"(?:^|\W)(?:solidarit\w*|solidaire\w*|soutien|support\w*|unterstutz\w*|war|wars|guerre|"
    r"guerres|krieg|oorlog|guerra|wojna|genocid\w*|genozid\w*|besatzung|"
    r"embargo|boycott\w*|boycot|sanctions|massacre\w*|repression|crisis|crise|"
    r"elections|wahlen|apartheid|colonisation|colonization|annexion|annexation|"
    r"delegation\w*|delegacion\w*|zapatist\w*)"
    r"(?:\s+(?:with|avec|mit|met|con|com|for|pour|fur|voor|per|para|to|a|au|aux|the|la|le|les|"
    r"l'|de|des|du|d'|of|von|der|die|das|dem|den|het|people|peuple|peuples|peoples|"
    r"population|in|en|on|sur|im|against|contre|gegen|tegen|na|w|v|u|and|et|und|zapatistes|"
    r"zapatistas|zapatists|activists|activistes|aktivist\w*|militants|militantes|communities|"
    r"communautes|objecteurs|objectors|conscience|prisoners|prisonniers|refugees|refugies|"
    r"migrants|workers|travailleurs|travailleuses|women|femmes|children|enfants|comrades|"
    r"camarades|struggle|struggles|luttes|lutte|resistance|movement|movements|mouvement|"
    r"mouvements|conscientious|from|aus|uit|da|des|of|the|all|those|everyone|who|qui|tous|toutes))*\s*(?:l'|d')?$")
_TOPIC_AFTER = re.compile(
    r"^\s*(?:agreement|accord|abkommen|treaty|embassy|consulate|government|regierung|gouvernement|"
    r"'s government|war|krieg|guerre|solidarity|solidaritat|solidarite)\b")
_LANG_BEFORE = re.compile(r"(?:^|\W)(?:in|en|auf|op|au|em|in het|in the|nel|στα)\s*$")
_LANG_AFTER = re.compile(r"^\s*(?:speaking|speakers|language|translation|subtitles|sous-titr|"
                         r"version|langue|sprache|sprachig|text|texts|edition|only)\b")

# Online and worldwide wording (folded text).
_ONLINE = re.compile(
    r"(?<!\w)(?:online|on-line|en ligne|webinar|webinaire|webinars|zoom|livestream|live-stream|"
    r"virtual (?:event|conference|meeting|gathering|camp)|virtuel|virtuelle|virtuell|"
    r"digital(?:e|er)? (?:camp|konferenz|conference|event)|camp digital|visio|visioconference|"
    r"jitsi|big blue button|bbb|onlinekonferenz|online-konferenz|online-veranstaltung|"
    r"in rete|en linea|w sieci|internetowy)(?!\w)")
_WORLDWIDE = re.compile(
    r"(?<!\w)(?:worldwide|world-wide|world wide|everywhere|all over the world|around the world|"
    r"across the world|all around the world|across europe|europe-wide|all over europe|"
    r"europaweit|europeen(?:ne)?s? d'action|european (?:day|days|week|weeks) of action|"
    r"partout(?: en europe| dans le monde)?|dans le monde entier|a travers le monde|"
    r"dans toute l'europe|weltweit|uberall|wereldwijd|overal|in tutto il mondo|ovunque|"
    r"en todo el mundo|por todo el mundo|todo el mundo|em todo o mundo|na calym swiecie|"
    r"decentrali[sz]ed|decentralised actions|decentralis(?:e|ee|es|ees)|dezentral(?:e|en)?|"
    r"decentrale|decentrati|descentralizad[ao]s?|(?<!banque )(?<!guerre )(?<!social )(?<!congres )(?<!sommet )mondial(?:e|es)?|mondiaux|"
    r"global (?:day|days|week|weeks|month|strike|climate strike|action|actions|wave|"
    r"mobili[sz]ation|frackdown|fight|day of action|week of action|powerup|divestment)|"
    r"(?:international|internationale|internationales|internacional|internazionale|"
    r"internationaler|internationalen|world|europe(?:an)?|europeenne?s?)\s+"
    r"(?:day|days|week|weeks|month|wave|journee|journees|semaine|mois|aktionstag|aktionstage|"
    r"aktionswoche|actiedag|giornata|jornada|dia)|"
    r"(?:journee|journees|semaine|semaines|mois|day|days|week|weeks|wave|aktionstag|aktionstage|"
    r"giornata|jornada|jornadas|dia|mobilisation|mobilization|mobilisations|greve|strike|"
    r"actions) (?:[\w']+ ){0,3}(?:internationale?s?|international|mondiale?s?|"
    r"global|globale?s?|europeennes?|european|weltweit|mundial|internazionale)|"
    r"internationaler aktionstag|internationale aktionstage|"
    r"(?<=, )(?:europe|europa)(?=[.!]?\s*(?:$|[a-z]{0,3}[.!]?\s*$)))(?!\w)")


# --------------------------------------------------------------------------- mentions
class _Mention:
    __slots__ = ("start", "end", "iso", "kind", "text", "prep", "marked", "comma", "key")

    def __init__(self, start, end, iso, kind, text, key):
        self.start, self.end, self.iso, self.kind, self.text, self.key = start, end, iso, kind, text, key
        self.prep = self.marked = self.comma = False


_URLISH = re.compile(r"https?://\S+|www\.\S+|\S+@\S+\.\S+|(?<![\w@])[\w-]+(?:\.[\w-]+)*"
                     r"\.(?:org|com|net|info|eu|de|fr|nl|be|at|ch|it|es|pt|uk|ie|dk|se|no|fi|pl|"
                     r"cz|sk|si|hr|rs|gr|ro|bg|hu|lt|lv|ee|lu|cat|eus|bzh|io|me|co|tv|cc|bz|"
                     r"blog|social|online|earth|africa|global|world|green|noblogs|wordpress)"
                     r"(?:/\S*)?(?!\w)", re.I)


def _blank_urls(text: str) -> str:
    """Links, e-mail addresses and bare domains replaced by spaces (same length)."""
    return _URLISH.sub(lambda m: " " * len(m.group(0)), text or "")


def _find(text: str, with_adj: bool = False, in_tag: bool = False) -> List[_Mention]:
    """Every place (or, with_adj, nationality adjective) named in `text`, with
    venue / topic marks, after the topic filter. Inside a tag (in_tag) weak
    names need no venue word."""
    text = _blank_urls(text)
    f, idx = _fold_map(text)
    if not f:
        return []
    out: List[_Mention] = []
    regex, table = (_ADJ_RE, None) if with_adj else (_PLACE_RE, _LOOKUP)
    for m in regex.finditer(f):
        k = _key(m.group(1))
        if table is None:
            iso, kind = _ADJ.get(k), "adj"
        else:
            iso, kind = table.get(k, (None, None))
        if not iso:
            continue
        o0, o1 = idx[m.start(1)], idx[m.end(1) - 1] + 1
        orig = text[o0:o1]
        if k in _CASE and not orig[:1].isupper():
            continue
        out.append(_Mention(m.start(1), m.end(1), iso, kind, orig, k))
    if not with_adj:
        out += _compounds(text, f, idx, out)
        out += _codes(text, f, idx)
        out.sort(key=lambda x: x.start)
    kept: List[_Mention] = []
    prev: Optional[_Mention] = None
    topic_prev: Optional[_Mention] = None
    for mt in out:
        before = f[max(0, mt.start - 60):mt.start]
        after = f[mt.end:mt.end + 40]
        if with_adj and (_LANG_BEFORE.search(before) or _LANG_AFTER.search(after)):
            continue
        if (_TOPIC_NEAR.search(before) or _TOPIC_FAR.search(before) or _TOPIC_AFTER.search(after)
                or (topic_prev is not None and mt.kind == "country" and _CHAIN.search(f[topic_prev.end:mt.start])
                    and (mt.iso in TOPIC_PRONE or _LIST_AND.search(f[topic_prev.end:mt.start])))):
            prev, topic_prev = None, mt
            continue
        topic_prev = None
        mt.prep = bool(_VENUE_PREP.search(before))
        mt.comma = bool(_COMMA_BEFORE.search(before))
        mt.marked = mt.prep or bool(_SLOT_BEFORE.search(before) or _SLOT_AFTER.search(after))
        if prev is not None and prev.marked and _CHAIN.search(f[prev.end:mt.start]):
            mt.marked = True
            mt.prep = mt.prep or prev.prep
        if mt.key in _WEAK and not (mt.prep or in_tag or mt.comma):
            continue
        kept.append(mt)
        prev = mt
    return kept


def _compounds(text: str, f: str, idx: List[int], found: List[_Mention]) -> List[_Mention]:
    """Hyphenated places not in the table: "Biarritz-Hendaye" (both FR) counts,
    "Lyon-Turin" (a rail project between two countries) does not."""
    taken = [(m.start, m.end) for m in found]
    out = []
    for m in re.finditer(r"(?<![\w-])[\w']+(?:\s?-\s?[\w']+)+(?![\w-])", f):
        if any(a < m.end() and m.start() < b for a, b in taken):
            continue
        parts = [p.strip() for p in m.group(0).split("-")]
        isos = {_LOOKUP[p][0] for p in parts if p in _LOOKUP}
        hits = [_LOOKUP[p] for p in parts if p in _LOOKUP and p not in _CASE and p not in _WEAK]
        if len(isos) == 1 and hits:
            o0, o1 = idx[m.start()], idx[m.end() - 1] + 1
            iso, kind = hits[0]
            out.append(_Mention(m.start(), m.end(), iso, kind, text[o0:o1], _key(m.group(0))))
    return out


_CAPS_CODES = re.compile(r"(?<![\w.])(UK|U\.K\.|USA|U\.S\.A\.|NRW|BRD)(?![\w])")
_SLOT_CODE = re.compile(r"(?:^|[,(\[/|]|\s-)\s*([A-Z]{2})(?=\s*(?:$|[,)\]/|.;]|\s-))")


def _codes(text: str, f: str, idx: List[int]) -> List[_Mention]:
    """Country codes outside brackets: "Lakenheath Peace Camp UK", "Brussels, BE"."""
    rev = {}
    for j, i in enumerate(idx):
        rev.setdefault(i, j)
    out = []
    for m in _CAPS_CODES.finditer(text):
        code = m.group(1).replace(".", "")
        iso = {"UK": "GB", "USA": "US", "NRW": "DE", "BRD": "DE"}[code]
        s = rev.get(m.start(1), 0)
        out.append(_Mention(s, s + (m.end(1) - m.start(1)), iso, "country", m.group(1), code.lower()))
    for m in _SLOT_CODE.finditer(text):
        code = m.group(1)
        if code in SLOT_CODE_EXCLUDE or code not in COUNTRIES:
            continue
        s = rev.get(m.start(1), 0)
        out.append(_Mention(s, s + 2, code, "country", code, code.lower()))
    return out


def _select(mentions: List[_Mention], tag: bool = False) -> List[_Mention]:
    """Venue mentions among `mentions`: topic-prone countries need a venue word,
    marked mentions win over unmarked ones, and a city directly followed by a
    country ("Geneva, France") gives way to it."""
    ms = [m for m in mentions if tag or m.iso not in TOPIC_PRONE or m.prep or m.comma]
    if not tag and any(m.marked for m in ms):
        ms = [m for m in ms if m.marked]
    out = []
    for i, m in enumerate(ms):
        nxt = ms[i + 1] if i + 1 < len(ms) else None
        if (m.kind == "city" and nxt is not None and nxt.kind == "country" and nxt.iso != m.iso
                and nxt.start - m.end <= 3):
            continue
        out.append(m)
    return out


def _uniq(xs):
    return list(dict.fromkeys(x for x in xs if x))


def _result(countries, method, matched) -> Dict[str, object]:
    countries = _uniq(countries)
    return {"countries": countries, "names": [country_name(c) for c in countries],
            "method": method if countries else "none",
            "matched": "; ".join(_uniq(matched)) if countries else ""}


# --------------------------------------------------------------------------- stages
_TAG_RE = re.compile(r"[\[(]([^\[\]()]{1,80})[\])]")
_YEAR = re.compile(r"'\d\d\b|\b(?:19|20)\d\d\b")
_DEPT_RE = re.compile(r"([A-ZÉÈÀÂÎÔ][\w'’-]+)\s*\((0[1-9]|[1-8]\d|9[0-5]|2[AB])\)")
_FRENCH_HINT = re.compile(r"(?:^|\s)(?:à|au|aux|dans|le|la|les|du|des|près|contre|sur)\s", re.I)


def _tags(text: str):
    """Countries from bracketed tags: ISO codes, INT / Online markers, names of
    countries and places, French departement numbers. Returns (isos, matched,
    special) where special is "INT" / "ONLINE" / ""."""
    isos: List[str] = []
    matched: List[str] = []
    special = ""
    for m in _TAG_RE.finditer(text or ""):
        body = m.group(1).strip()
        if _YEAR.search(body):   # "(Berlin '22)": an earlier edition, not this venue
            continue
        found: List[str] = []
        for tok in re.split(r"\s*(?:[,/;+&|]|->|→|=>|\s-\s|\s–\s|\band\b|\bet\b|\bund\b)\s*", body):
            tok = tok.strip(" .-")
            up = tok.upper()
            if not tok:
                continue
            if (tok.isupper() or len(tok) <= 3) and (up in COUNTRIES or up in CODE_ALIASES):
                code = CODE_ALIASES.get(up, up)
                if code == "INT":
                    special = special or "INT"
                    matched.append(m.group(0))
                else:
                    found.append(code)
                continue
            ft = fold(tok)
            if ft in ("online", "en ligne", "virtual", "zoom", "webinar"):
                special = "ONLINE" if special != "INT" else special
                matched.append(m.group(0))
                continue
            if ft in ("global", "worldwide", "everywhere", "international", "partout", "europe",
                      "eu", "europa", "weltweit") or _WORLDWIDE.fullmatch(ft or "x"):
                special = special or "INT"
                matched.append(m.group(0))
                continue
            found += [x.iso for x in _select(_find(tok, in_tag=True), tag=True)]
        if found:
            isos += found
            matched.append(m.group(0))
        elif _WORLDWIDE.search(fold(body)) and not special:
            special = "INT"
            matched.append(m.group(0))
    if not isos and _FRENCH_HINT.search(text or ""):
        for m in _DEPT_RE.finditer(text or ""):
            isos.append("FR")
            matched.append(m.group(0))
    return _uniq(isos), matched, special


def _places(text: str):
    """(isos, matched, method) from country and place names in free text."""
    sel = _select(_find(text))
    if not sel:
        return [], [], ""
    method = "name" if any(m.kind == "country" for m in sel) else "city"
    return [m.iso for m in sel], [m.text for m in sel], method


def _special(text: str):
    """("ONLINE" | "INT" | "", matched) from online / worldwide wording."""
    clean = _blank_urls(text)
    f, idx = _fold_map(clean)
    m = _ONLINE.search(f)
    if m:
        return "ONLINE", clean[idx[m.start()]:idx[m.end() - 1] + 1]
    m = _WORLDWIDE.search(f)
    if m:
        return "INT", clean[idx[m.start()]:idx[m.end() - 1] + 1]
    return "", ""


def _structured(loc: str) -> Optional[Dict[str, object]]:
    """The event page's own location field: the last country named (addresses
    end with the country), else the last town; "Online" when it says so."""
    loc = (loc or "").strip()
    if not loc:
        return None
    segs = [s.strip() for s in re.split(r"[,;\n|]", loc) if s.strip()]
    for seg in reversed(segs):   # addressCountry may be an ISO code
        up = seg.upper()
        if re.fullmatch(r"[A-Z]{2,3}", seg) and (up in COUNTRIES or up in CODE_ALIASES) \
                and CODE_ALIASES.get(up, up) != "INT":
            iso = CODE_ALIASES.get(up, up)
            return _result([iso], "location", [seg])
    ms = [m for m in _find(loc) if m.iso]
    countries = [m for m in ms if m.kind == "country"]
    if countries:
        return _result([countries[-1].iso], "location", [countries[-1].text])
    if ms:
        return _result([ms[-1].iso], "location", [ms[-1].text])
    kind, txt = _special(loc)
    if kind == "ONLINE":
        return _result(["ONLINE"], "online", [txt])
    return None


def tld_country(link: str) -> Tuple[str, str]:
    """(ISO, ".de") from the link's country-code domain, or ("", "")."""
    try:
        host = (urlparse(link or "").hostname or "").lower().rstrip(".")
    except ValueError:
        return "", ""
    if not host or "." not in host or re.fullmatch(r"[\d.]+", host):
        return "", ""
    tld = host.rsplit(".", 1)[1]
    iso = TLD_COUNTRY.get(tld, "")
    return (iso, "." + tld) if iso else ("", "")


def country_name(iso: str) -> str:
    """English name of an ISO code (or INT / ONLINE); the code itself if unknown."""
    iso = (iso or "").upper()
    if iso in SPECIAL_NAMES:
        return SPECIAL_NAMES[iso]
    return COUNTRIES[iso][0] if iso in COUNTRIES else iso


def event_country(name: str, entry_text: str = "", link: str = "", structured: str = "") -> Dict[str, object]:
    """Country (or countries) where an event takes place; see the module docstring."""
    name = name or ""
    entry_text = entry_text or ""
    res = _structured(structured)
    if res:
        return res
    isos, matched, special = _tags(name)
    if isos:
        return _result(isos, "tag", matched)
    tag_matched = matched
    # tags already read (INT / Online) and tags naming an earlier edition ("(Berlin '22)")
    # are left out of the search for place names
    blank = re.compile(_TAG_RE.pattern) if special else re.compile(r"[\[(][^\[\]()]*" + _YEAR.pattern
                                                                 + r"[^\[\]()]*[\])]")
    isos, matched, method = _places(blank.sub(lambda m: " " * len(m.group(0)), name))
    if isos:
        return _result(isos, method, matched)
    if special:
        return _result([special], "online" if special == "ONLINE" else "international", tag_matched)
    kind, txt = _special(name)
    if kind:
        return _result([kind], "online" if kind == "ONLINE" else "international", [txt])
    if entry_text.strip():
        isos, tag_matched, special = _tags(entry_text)
        matched = tag_matched
        if not isos:
            isos, matched, _ = _places(entry_text)
        if isos:
            return _result(isos, "entry", matched)
        kind, txt = (special, tag_matched[0] if tag_matched else "") if special else _special(entry_text)
        if kind:
            return _result([kind], "online" if kind == "ONLINE" else "international", [txt])
    adj = _select(_find(name, with_adj=True))
    if adj:
        return _result([m.iso for m in adj], "name", [m.text for m in adj])
    iso, tld = tld_country(link)
    if iso:
        return _result([iso], "tld", [tld])
    return _result([], "none", [])


# --------------------------------------------------------------------------- self-test
SELFTEST = [
    # (name, entry_text, link, structured, expected countries, expected method)
    ("Anti-OPEC actions, Vienna", "", "", "", ["AT"], "city"),
    ("Los Geht's - Kommunen Fest (Germany)", "", "", "", ["DE"], "tag"),
    ("Klimacamp München", "", "", "", ["DE"], "city"),
    ("Climate camp (Amsterdam, NL)", "", "", "", ["NL"], "tag"),
    ("Week of action [NL/BE]", "", "", "", ["NL", "BE"], "tag"),
    ("Festival of revolutions [Online]", "", "", "", ["ONLINE"], "online"),
    ("Anti-EPH Action Days [INT]", "", "", "", ["INT"], "international"),
    ("March from Clavière (Italy) to Briançon (France)", "", "", "", ["IT", "FR"], "tag"),
    ("Appel pour des journées d'actions contre un transport d'uranium entre l'Allemagne et la France",
     "", "", "", ["DE", "FR"], "name"),
    ("Camp d'été anarchiste en Autriche.", "", "", "", ["AT"], "name"),
    ("Obóz klimatyczny w Polsce", "", "", "", ["PL"], "name"),
    ("Klimacamp in Deutschland", "", "", "", ["DE"], "name"),
    ("Ilmastoleiri Suomessa", "", "", "", ["FI"], "name"),
    ("Κατασκήνωση για το κλίμα στην Ελλάδα", "", "", "", ["GR"], "name"),
    ("Rally in solidarity with Ukraine, Berlin", "", "", "", ["DE"], "city"),
    ("Solidarity with Palestine", "", "", "", [], "none"),
    ("Free Palestine demo", "", "https://example.org/x", "", [], "none"),
    ("Semaine d'actions pour le climat dans le monde entier", "", "", "", ["INT"], "international"),
    ("Days of action against Tesla, worldwide", "", "", "", ["INT"], "international"),
    ("La non violence en action - Conférence internationale en ligne.", "", "", "", ["ONLINE"], "online"),
    ("Ecotopia bike tour, from Amsterdam to Marseille.", "", "", "", ["NL", "FR"], "city"),
    ("Biarritz-Hendaye, semaine intergalactique", "", "", "", ["FR"], "city"),
    ("Rassemblement contre le Lyon-Turin, Chapareillan.", "", "", "", ["FR"], "city"),
    ("Mobilisation contre l'accord de Paris", "", "", "", [], "none"),
    ("Camp climat", "", "https://www.klimacamp.de/", "", ["DE"], "tld"),
    ("Camp climat", "", "https://www.klimacamp.org/", "", [], "none"),
    ("Some gathering", "", "https://x.org/", "Sumac Centre, 245 Gladstone St, Nottingham, United Kingdom",
     ["GB"], "location"),
    ("Some gathering", "", "", "Kulturzentrum, Hauptstr. 1, 04109 Leipzig, DE", ["DE"], "location"),
    ("Some gathering", "", "", "Online", ["ONLINE"], "online"),
    ("Total Liberation Collective (TLC", "11-13th July - Total Liberation Collective (TLC) "
     "Gathering at the Sumac Centre, Nottingham, UK", "", "", ["GB"], "entry"),
    ("9ème alter-village organisé par Attac, au Relais à Pellevoisin, dans l'Indre (36)", "", "", "",
     ["FR"], "tag"),
    ("2ème camp climat autrichien, contre la piste d'aéroport", "", "", "", ["AT"], "name"),
    ("Climate Camp Anametrisi,Greece,(in Greek)", "", "", "", ["GR"], "name"),
    ("Lakenheath Peace Camp UK", "", "", "", ["GB"], "name"),
    ("Tour in the Netherlands with Les Soulèvements", "", "", "", ["NL"], "name"),
    ("Summer camp near Geneva, France", "", "", "", ["FR"], "name"),
    ("Hambacher Forst, Buir, Germany", "", "", "", ["DE"], "name"),
    ("Roma rights conference", "", "", "", [], "none"),
    ("Conférence à Roma", "", "", "", ["IT"], "city"),
    ("War in Ukraine: discussion evening in Vienna", "", "", "", ["AT"], "city"),
    ("Action in Kyiv", "", "", "", ["UA"], "city"),
    ("Camp climat, Hollande. Infos à venir", "", "", "", ["NL"], "name"),
    ("Discours de François Hollande sur le climat", "", "", "", [], "none"),
    ("Nice weather camp", "", "", "", [], "none"),
    ("Vélorution universelle à Nancy", "", "", "", ["FR"], "city"),
    # regressions found on the 826 real agenda events
    ("Festival alta felicita, contre le projet de TGV Lyon-Turin, Venaus, Italie.", "", "", "",
     ["IT"], "name"),
    ("Action Féministe contre les Frontières, Vintimille – Menton.", "", "", "", ["IT", "FR"], "city"),
    ("Feminist Congress Basel (Berlin '22)", "", "", "", ["CH"], "city"),
    ("Kongres w Warszawie", "", "", "", ["PL"], "city"),
    ("Forum social mondial anti-nucléaire", "", "", "", [], "none"),
    ("Semaines d'actions en soutien aux objecteurs de conscience d'Ukraine, Biélorussie et Russie", "",
     "", "", [], "none"),
    ("Blocade Australia, actions against climate inaction, Sydney, Australia.", "", "", "", ["AU"], "name"),
    ("XR - climate occupation in Rome", "", "", "", ["IT"], "city"),
    ("Camp climat en Adriatique, île de Krk, Croatia. Krk island, Croatia.", "", "", "", ["HR"], "name"),
    ("Marches pour la forêt, contre une scierie sur le piémont des Pyrénées", "", "", "", [], "none"),
    ("Mobilisation contre la Banque mondiale, Washington", "", "", "", ["US"], "city"),
]


def selftest(verbose: bool = False) -> int:
    bad = 0
    for name, entry, link, loc, want, method in SELFTEST:
        got = event_country(name, entry, link=link, structured=loc)
        ok = got["countries"] == want and got["method"] == method
        bad += not ok
        if verbose or not ok:
            print(("ok  " if ok else "FAIL"), name[:70], "->", got["countries"], got["method"],
                  repr(got["matched"]), "" if ok else f"(expected {want} {method})")
    assert country_name("DE") == "Germany" and country_name("INT") == "International"
    assert country_name("ONLINE") == "Online" and country_name("GB") == "United Kingdom"
    print(f"gazetteer self-test: {len(SELFTEST) - bad}/{len(SELFTEST)} passed")
    return 1 if bad else 0


def _evaluate(events_csv: str, seed_links: str = "") -> None:
    """Resolution rate per method on an events.csv (and optional seed_links.json)."""
    import csv
    import json
    from collections import Counter, defaultdict
    entries = defaultdict(list)
    if seed_links:
        with open(seed_links, encoding="utf-8") as fh:
            data = json.load(fh)
        for pdf in (data.get("pdfs") or {}).values():
            for e in pdf.get("entries") or []:
                entries[(e.get("name") or "").casefold().strip()].append(" ".join(e.get("lines") or []))
    with open(events_csv, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    methods: Counter = Counter()
    for r in rows:
        link = r.get("event_link") or ""
        link = link if link.startswith(("http://", "https://")) else ""
        entry = r.get("entry_text") or next(iter(entries.get((r.get("event") or "").casefold().strip(), [])), "")
        res = event_country(r.get("event") or "", entry, link=link,
                            structured=r.get("location_structured") or "")
        methods[res["method"]] += 1
    n = len(rows) or 1
    resolved = n - methods.get("none", 0)
    print(f"{len(rows)} events, {resolved} with a country ({100 * resolved / n:.1f}%)")
    for meth, k in methods.most_common():
        print(f"  {meth:14s} {k:5d}  {100 * k / n:5.1f}%")


def main(argv: List[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "--selftest":
        return selftest(verbose="-v" in argv)
    if argv[0] == "--events":
        seed = argv[argv.index("--seed-links") + 1] if "--seed-links" in argv else ""
        _evaluate(argv[1], seed)
        return 0
    for text in argv:
        res = event_country(text)
        print(f"{text!r}: {'; '.join(res['names']) or '-'} [{res['method']}] {res['matched']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
