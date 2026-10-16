"""
Place names that NER often mistakes for organisations. A name found only by
NER or by link text is dropped when it is exactly one of these; names found by
a rule ("Extinction Rebellion Genève", "Rete Antirazzista Milano") are kept.
Compared after canonical_key() normalisation (case, accents, plural -s).
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

PLACES = set(COUNTRIES) | set(CITIES) | set(REGIONS)
LANGUAGE_NAMES = set(LANGUAGES)

# Multi-word place names, matched against the whole normalised name.
PLACES |= {"united kingdom", "united state", "pays de galles", "western sahara", "sahara occidental",
           "pays-ba", "paesi bassi", "paise bajo", "czech republic", "north sea", "mer du nord"}

# Country / language codes used as tags ("[NL]", "[DE]") and calendar weekday
# headers ("MO TU WE TH FR SA SU", "LU MA ME JE VE SA DI", "MA DI WO DO VR ZA ZO").
PLACES |= {"nl", "de", "be", "fr", "uk", "at", "it", "es", "dk", "se", "gr", "pt", "pl", "ch", "int",
           "cz", "hu", "ro", "bg", "hr", "si", "sk", "ie", "fi", "no", "ee", "lv", "lt", "lu", "us", "en"}
WEEKDAY_ABBREVIATIONS = {"mo", "tu", "we", "th", "fr", "sa", "su", "mon", "tue", "wed", "thu", "fri",
                         "sat", "sun", "lu", "ma", "me", "je", "ve", "di", "mi", "do", "so", "wo",
                         "vr", "za", "zo", "lun", "mar", "mer", "jeu", "ven", "sam", "dim", "lø", "sø",
                         "ti", "on", "to", "fre", "lör", "sön"}
PLACES |= WEEKDAY_ABBREVIATIONS
