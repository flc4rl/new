"""
Place names that NER often mistakes for organisations. A name found only by
NER or by link text is dropped when it is exactly one of these; names found by
a rule ("Extinction Rebellion Genève", "Rete Antirazzista Milano") are kept.
Compared after canonical_key() normalisation (case, accents, plural -s,
elided articles): extract.py builds PLACES_KEYS from these lists with the same
function, so entries can be written in their ordinary spelling ("Nantes",
"Pays de Galles"). On import, extract.py also adds those normalised forms to
PLACES itself, so other modules can compare canonical keys against it.
"""

COUNTRIES = """
albania algeria andorra argentina argentine argentinie armenia austria autriche osterreich oostenrijk
osterrike ostrig belarus belgium belgique belgien belgie belgio belgica bosnia brazil bresil brasil
bulgaria bulgarie bulgarien canada chile chili china chine colombia croatia croatie kroatien kroatie
croazia cyprus chypre czechia czech tchequie tschechien denmark danemark danmark denemarken
danimarca dinamarca ecuador egypt egypte england angleterre engeland inghilterra inglaterra estonia
finland finlande finnland suomi france frankrijk frankreich francia frankrig frankrike germany allemagne
deutschland duitsland germania alemania tyskland greece grece griechenland griekenland grecia hungary
hongrie ungarn hongarije iceland islande india inde indonesia indonesie indonesien iran iraq irak
ireland irlande irland ierland irlanda israel italy italie italien italia italie kenya kosovo kurdistan
latvia lettonie lebanon liban libya lithuania lituanie litauen luxembourg luxemburg malta mexico mexique
moldova monaco montenegro morocco maroc marokko netherlands pays-ba holland hollande nederland
niederlande nigeria norway norvege norwegen noorwegen norge norvegia palestine
palastina palestina peru philippines poland pologne polen polonia portugal portogallo romania roumanie
rumanien roemenie russia russie russland rusland rusia scotland ecosse schottland schotland escocia
serbia serbie slovakia slovaquie slovenia slovenie slowenien spain espagne spanien spanje spagna espana
spanien sudan sweden suede schweden zweden svezia suecia sverige switzerland suisse schweiz zwitserland
svizzera suiza schweiz syria syrie syrien tunisia tunisie turkey turquie turkei turkije turchia uganda
ukraine ukraina oekraine ucrania royaume-uni uk usa wales
venezuela westsahara europe europa afrique africa asia
""".split()

CITIES = """
aachen aberdeen amsterdam antwerp antwerpen anvers athens athene athen barcelona basel bale belfast
belgrade bergen berlin bern berne bielefeld bilbao birmingham bochum bologna bonn bordeaux bremen
brest bristol brno brussels bruxelles brussel brugge bucharest budapest bure cardiff clermont
cologne koln copenhagen kobenhavn copenhague cork cottbus dortmund dresden dublin duisburg dusseldorf
edinburgh erfurt essen florence firenze frankfurt freiburg gdansk geneva geneve genf genova gent
ghent glasgow gothenburg goteborg graz grenoble groningen hamburg hambourg hannover hanover heidelberg
helsinki innsbruck istanbul karlsruhe kassel kiel krakow lausanne leeds leipzig liege lille limoges
linz lisbon lisboa lisbonne liverpool ljubljana london londres lyon madrid malmo manchester mannheim
marseille milan milano montpellier munich munchen nantes naples napoli newcastle nice nijmegen
nuremberg nurnberg odense oslo oxford palermo paris porto prague praha rennes riga rome roma
rotterdam rouen salzburg sheffield sofia stockholm strasbourg stuttgart tallinn thessaloniki
toulouse turin torino tours trieste tubingen utrecht valencia venice venezia vienna wien vienne
vilnius warsaw warszawa wolfsburg wroclaw zagreb zaragoza zurich aarhus aalborg uppsala lund
""".split()

REGIONS = """
alsace andalucia aquitaine ardenne auvergne bavaria bayern bavarian bretagne brittany brandenburg
burgundy bourgogne catalonia catalunya catalogne corsica corse donbas flanders vlaanderen flandre
friesland galicia hesse hessen lausitz lusatia lombardia lombardy lorraine normandy normandie occitanie
piemonte provence rheinland rhineland rhenanie ruhr ruhrgebiet sachsen saxony sapmi sardinia sardegna
sicily sicilia silesia schlesien thuringia thuringen tyrol tirol wallonia wallonie westfalen
yorkshire baltique baltic balkans balkan scandinavia mediterranee mediterranean alps alpes
""".split()

LANGUAGES = """
english deutsch francais espanol italiano nederlands dansk svenska portugues polski cesky romana
magyar suomi norsk turkce ellinika russkij ukrainska catala euskara galego arabic arabe
""".split()

# Country names in the other supported languages (pl, cs, sk, sl, hr/sr, el, ro, fi, ca,
# eu, lt, hu, uk/ru, pt), plus cities, regions, islands and sites that agenda entries and
# linked pages name and that small NER models tag as organisations ("No LNG on Rügen!",
# "Camp action climat, Slovensko, Bratislava, Slovaquie", "Basse-Saxony, Allemagne").
MORE_COUNTRIES = """
alemanha niemcy nemecko německo njemacka nemcija germania germanija vokietija nemetorszag saksa
francja francie franca franta francuska francija prantsusmaa ranska frantzia prancuzija franciaorszag
pays bas paises bajos paises baixos olanda holanda nizozemsko niderlandy nizozemska alankomaat herbehereak
nyderlandai hollandia belgia belgie belgija belgica belgio
oostenrijk rakousko rakusko avstrija austrija itavalta austrija ausztria
schweiz svycarsko sveits sveitsi szwajcaria svica suica
regno unito reino unido grande-bretagne grossbritannien great britain britain wielka brytania velka britanie
ujedinjeno kraljevstvo erresuma batua iso-britannia nagy-britannia
irlandia irsko irska irlanti
wlochy italie italija italia italien olaszorszag
hiszpania spanelsko spanielsko spanija spanyolorszag espanya espainia espanha
portugalia portugalsko portugalija portugali portugalia portugalska
dania dansko danska tanska dania daenemark danimarca
szwecja svedsko svedska svedija ruotsi svedorszag
norwegia norsko norveska norjia norja norvegija norvegia noruega
finlandia finsko finska suomija finnorszag finlandia
polska polsko poljska polonia lengyelorszag lenkija puola
czechy cesko ceska republika ceska tsekki csehorszag cechija chequia republica checa tsjechie
slowacja slovensko slovacka slovakija slovaquia eslovaquia szlovakia slowakije
slowenia slovinsko slovenija slovenie eslovenia szlovenia slowenie
chorwacja chorvatsko hrvatska kroatija croacia horvatorszag kroatie kroatien
serbia srbsko srbija serbie servie szerbia
grecja recko grcka graikija kreikka gorogorszag hellas griekenland
rumunia rumunsko rumunija romania romunija roemenie romenia
bulgaria bulharsko bugarska bulgarija bulgarije bulgaria
ukraina ukrajina ukrayina oekraine ucraina ucrania
rosja rusko rusija venaja oroszorszag rusia russie
litwa litva litvanija liettua litvania lituania litouwen
lotwa lotyssko latvija latvija lettland letland lettonia letonia
estonia estonsko estonija viro eesti estland
luxemburgo lussemburgo luksemburg lucembursko
turcja turecko turska turkiye turquia turchia
palestyna palestina palestine palaestina
mexique meksyk mexiko
etats-unis etats unis united states stany zjednoczone
afrique du sud sudafrika south africa
"""
MORE_CITIES = """
bratislava pamplona iruna pamplona iruna kosice ostrava plzen poznan lodz katowice szczecin lublin
debrecen szeged timisoara iasi constanta varna plovdiv novi sad nis split rijeka zadar pula krk
maribor tartu kaunas klaipeda tampere turku espoo trondheim stavanger tromso malmo norrkoping
rostock lubeck magdeburg potsdam halle jena chemnitz cottbus gottingen gorleben wendland
brunsbuttel wilhelmshaven lubmin mukran sassnitz stralsund garzweiler keyenberg lutzerath
hambacher forst dannenrod grunheide kerpen
calais dunkerque bayonne biarritz bure saclay larzac notre-dame-des-landes nddl sainte-soline
poitou bar-le-duc briancon angouleme clermont-ferrand saint-etienne besancon orleans amiens
la clusaz clusaz echassieres allier briouze kingersheim mulhouse metz nancy dijon poitiers
the hague den haag la haye eindhoven delfzijl eemshaven volkel
gent gand namur charleroi leuven louvain mons ostend oostende
traiskirchen linz ottensheim
venaus val susa valsusa padova verona genoa genes
sevilla seville tarragona soria covaleda vitoria-gasteiz gasteiz gijon xixon donostia san sebastian
covas do barroso montemor-o-novo cabrela coimbra
middelfart hirtshals elmau ornois horville-en-ornois
galway
york norfolk gloucestershire chepstow kings lynn falmouth plymouth
sint maarten mexico city ciudad de mexico houses of parliament
castres albi colone
"""
MORE_REGIONS = """
rugen usedom sylt lusace lausitz basse-saxe basse-saxony basse saxe lower saxony niedersachsen
nordrhein-westfalen north rhine-westphalia mecklenburg-vorpommern schleswig-holstein
ile-de-france bretagne finistere loire-atlantique cevennes dordogne haute-durance pays basque
euskal herria iparralde provence-alpes-cote d azur paca
zeeland limburg groningen
valais wallis ticino
sicilia sardinia sardegna
transylvania silesia slask
"""

PLACES = set(COUNTRIES) | set(CITIES) | set(REGIONS)
LANGUAGE_NAMES = set(LANGUAGES)

# One entry per line for the lists above (several words on a line are one name only
# when they are known multi-word names; the rest are single words).
_MULTI = {"pays bas", "paises bajos", "paises baixos", "regno unito", "reino unido", "great britain",
          "wielka brytania", "velka britanie", "ujedinjeno kraljevstvo", "erresuma batua",
          "ceska republika", "republica checa", "etats unis", "united states", "stany zjednoczone",
          "afrique du sud", "south africa", "pamplona iruna", "novi sad", "hambacher forst",
          "the hague", "den haag", "la haye", "val susa", "san sebastian", "covas do barroso",
          "kings lynn", "sint maarten", "mexico city", "ciudad de mexico", "houses of parliament", "basse saxe", "lower saxony",
          "north rhine-westphalia", "pays basque", "euskal herria", "provence-alpes-cote d azur",
          "la clusaz"}


def _names(block: str) -> set:
    out = set()
    for line in block.strip().splitlines():
        rest = " " + line.strip() + " "
        for multi in sorted(_MULTI, key=len, reverse=True):
            if f" {multi} " in rest:
                out.add(multi)
                rest = rest.replace(f" {multi} ", " ")
        out |= set(rest.split())
    return out


PLACES |= _names(MORE_COUNTRIES) | _names(MORE_CITIES) | _names(MORE_REGIONS)

# Multi-word place names, matched against the whole normalised name.
PLACES |= {"united kingdom", "united state", "pays de galles", "western sahara", "sahara occidental",
           "pays-ba", "paesi bassi", "paise bajo", "czech republic", "north sea", "mer du nord"}

# Country / language codes used as tags ("[NL]", "[DE]") and calendar weekday
# headers ("MO TU WE TH FR SA SU", "LU MA ME JE VE SA DI", "MA DI WO DO VR ZA ZO").
COUNTRY_CODES = {"nl", "de", "be", "fr", "uk", "at", "it", "es", "dk", "se", "gr", "pt", "pl", "ch", "int",
                 "cz", "hu", "ro", "bg", "hr", "si", "sk", "ie", "fi", "no", "ee", "lv", "lt", "lu", "us"}
PLACES |= COUNTRY_CODES | {"en"}
WEEKDAY_ABBREVIATIONS = {"mo", "tu", "we", "th", "fr", "sa", "su", "mon", "tue", "wed", "thu", "fri",
                         "sat", "sun", "lu", "ma", "me", "je", "ve", "di", "mi", "do", "so", "wo",
                         "vr", "za", "zo", "lun", "mar", "mer", "jeu", "ven", "sam", "dim", "lø", "sø",
                         "ti", "on", "to", "fre", "lör", "sön"}
PLACES |= WEEKDAY_ABBREVIATIONS

# Nationalities / language adjectives NER tags as organisations ("Dutch", "German").
NATIONALITIES = {
    "dutch", "german", "french", "english", "british", "irish", "scottish", "welsh", "italian", "spanish",
    "portuguese", "belgian", "austrian", "swiss", "danish", "swedish", "norwegian", "finnish", "polish",
    "czech", "slovak", "slovenian", "croatian", "serbian", "greek", "romanian", "bulgarian", "hungarian",
    "ukrainian", "russian", "turkish", "kurdish", "european", "american", "african", "asian", "indian",
    "chinese", "nigerian", "brazilian", "mexican", "israeli", "palestinian", "lithuanian", "latvian",
    "estonian", "catalan", "basque", "deutsch", "deutsche", "francais", "francaise", "nederlandse",
    "allemand", "allemande", "anglais", "italien", "espagnol", "neerlandais", "europeen", "europeenne",
}
PLACES |= NATIONALITIES | {"india", "china", "japan", "nigeria", "brazil", "mexico", "australia", "canada",
                           "south africa", "new zealand", "indonesia", "philippines", "colombia", "peru"}
