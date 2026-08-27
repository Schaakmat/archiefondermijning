# Archief ondermijning — installatie

Deze map is de complete inhoud van `github.com/schaakmat/archiefondermijning`.
Alles hieronder is gratis: GitHub Actions haalt elk uur de bronnen op, GitHub Pages
publiceert de site. Je hebt geen server, abonnement of creditcard nodig.

## Wat zit erin

| bestand | wat het doet |
| --- | --- |
| `index.html` | de archiefsite, één bestand, werkt ook offline |
| `feeds.json` | de dossiers, hun zoekwoorden en de bronnenlijst |
| `collect.py` | de verzamelaar: haalt bronnen op, deelt in, schrijft `items.json` |
| `.github/workflows/collect.yml` | het uurschema dat `collect.py` draait |
| `.github/workflows/terugzoeken.yml` | de maandelijkse en handmatige terugzoekronde |
| `items.json` | het archief zelf (wordt bij de eerste run aangemaakt) |
| `dossiers.json` | de indeling met het aantal items per dossier |
| `status.json` | laatste run, aantal nieuwe items, staat per bron |

Zolang `items.json` er nog niet is, laat de site voorbeelddata zien. Na de eerste
run vervangt hij die door de echte items.

## Stap 1 — uploaden

1. Ga naar `github.com/schaakmat/archiefondermijning`.
2. Klik **uploading an existing file**.
3. Sleep de inhoud van deze map erin — inclusief de map `.github`.
   Ziet GitHub `.github` niet? Maak dan handmatig het bestand
   `.github/workflows/collect.yml` aan via **Add file › Create new file** en plak
   de inhoud erin.
4. Commit.

## Stap 2 — de verzamelaar één keer starten

**Actions** › *Archief bijwerken* › **Run workflow**. De eerste run duurt een
minuut of twee en zet `items.json` en `status.json` in de repo. Daarna draait hij
elk uur zelf.

Krijg je een foutmelding over pushen: **Settings › Actions › General ›
Workflow permissions** › zet op *Read and write permissions*.

## Stap 3 — de site live zetten

**Settings › Pages**. Bij *Source* kies **Deploy from a branch**, branch `main`,
map `/ (root)`. Na een paar minuten staat het archief op:

```
https://schaakmat.github.io/archiefondermijning/
```

Wil je het niet openbaar? Laat Pages dan uit en open `index.html` gewoon lokaal;
je kunt de repo dan handmatig binnenhalen wanneer je wil bijwerken.

## Stap 4 — één keer terugzoeken

Alle bronnen zijn Nederlandstalig. Nieuwsfeeds geven alleen hun recente berichten,
dus het archief begint dun. De terugzoekronde vult het aan met oud materiaal:

**Actions** › *Terugzoeken* › **Run workflow**. Laat *dossier* leeg voor alles, of
vul één dossiernaam in. Beginjaar staat op 2012. De ronde duurt een kwartier tot
een half uur en draait daarna automatisch op de eerste van elke maand.

Wat hij doet, per dossier:

- het nieuwsarchief jaar voor jaar aflopen (2012 → nu), zodat ook berichten van
  tien jaar terug binnenkomen;
- de volledige tekst van de officiële publicaties doorzoeken via de SRU-zoekdienst
  van overheid.nl: Kamerstukken, Kamervragen, Handelingen, Staatscourant,
  Staatsblad, provinciale en gemeentebladen.

Dat tweede is waar de diepte zit. Zoek je op *goudhandel*, dan komen daar de
Kamervragen, beleidsstukken en bekendmakingen uit die geen nieuwsfeed meer heeft.

## Wat er wel en niet in komt

Een bericht wordt beoordeeld op zijn eigen tekst, niet op de zoekopdracht waarmee
het gevonden is. Het moet drie horden nemen:

1. **Nederlands adres.** Alleen .nl-domeinen (plus de doorverwijzingen van Google
   Nieuws). Wetenschappelijke databanken en buitenlandse persbureaus publiceren
   niet op een .nl-adres en vallen hier af, wat er ook in de tekst staat.
2. **Nederlandse tekst.** Genoeg Nederlandse functiewoorden, en niet meer Engelse
   dan Nederlandse.
3. **Een dossierwoord.** Een woord uit `hard` is genoeg. Een woord uit `zwak`
   telt alleen mee als er ook een woord uit `context` in staat (aanhouding,
   verdachte, rechtbank, witwassen…).
4. **Geen ruis.** Staat er een woord uit `ruiswoorden` in — kanker, eredivisie,
   goudprijs — dan valt het bericht af, tenzij er een hard woord tegenover staat.

Die derde hobbel is nieuw. Zonder hem leverde de zoekopdracht voor *zorgfraude*
ook artikelen over ziektes op, omdat een zoekfeed nu eenmaal ruim teruggeeft.

Verander je iets aan de woorden, dan geldt dat ook met terugwerkende kracht: bij
elke ronde wordt het hele archief opnieuw langs de filters gehaald. Wat er niet
meer doorheen komt verdwijnt. Alleen opschonen, zonder iets op te halen:

```
python collect.py --opschonen
```

## Dossiers en zoekwoorden aanpassen

De dossiers in `feeds.json` vormen de indeling van het archief. Eén blok:

```json
{
  "naam": "Goudhandel & edelmetalen",
  "omschrijving": "Goudopkopers, smelterijen, juweliers en handel in edelmetalen.",
  "hard": ["goudhandel", "goudsmelterij", "goudopkoper"],
  "zwak": ["goud", "juwelier", "sieraden"],
  "nieuws": "goudhandel witwassen OR goudopkoper",
  "sru": "goudhandel OR edelmetaal witwassen"
}
```

- `hard` — dit woord is op zichzelf genoeg om een item op te nemen.
- `zwak` — telt alleen mee als er óók een woord uit `context` bovenaan het bestand
  in de tekst staat (aanhouding, verdachte, witwassen, rechtbank…). Zo levert
  "goudprijs stijgt" niets op en "goudhandelaar aangehouden" wel.
- `ruiswoorden` — bovenaan het bestand, geldt voor alle dossiers.
- `nieuws` — de zoekopdracht voor het nieuwsarchief.
- `sru` — de zoekopdracht voor de officiële publicaties.

Voeg je een dossier toe, dan pikt de volgende ronde het meteen op en verschijnt het
in de zijbalk. Mis je een onderwerp binnen een dossier, zet het woord dan bij
`hard` (specifiek genoeg) of `zwak` (ook alledaags).

## Bronnen toevoegen of aanpassen

Onderaan `feeds.json` staat `bronnen`: de vaste feeds. Een bron is één blok:

```json
{
  "naam": "Gemeente Tilburg",
  "url": "https://www.tilburg.nl/actueel/nieuws/rss",
  "methode": "RSS",
  "ritme": "1 dag",
  "soort": "Nieuwsbericht",
  "regio": "Tilburg"
}
```

Alles uit zo'n feed wordt tegen de dossierwoorden gehouden; wat nergens in past
gaat niet het archief in. Zet `"altijd": true` plus `"dossiers": ["…"]` als je
álles van die bron wil bewaren.

## Nieuwsbrieven per e-mail

Dit is het enige onderdeel dat niet volledig uit zichzelf werkt. Twee gratis routes:

1. **Handmatig, nul instelwerk.** Bewaar een nieuwsbrief als tekst en voeg hem als
   item toe aan `items.json` (zelfde velden als hierboven). Kost een minuut per stuk.
2. **Automatisch via een doorstuuradres.** Maak een apart mailadres aan, abonneer
   daarmee op de nieuwsbrieven, en laat een mailkoppeling elke nieuwe mail als
   bestand in de repo zetten. Dit vraagt eenmalig een half uur instellen. Zeg het
   als je dit wil, dan schrijf ik dat script er bij.

## Wat de verzamelaar niet doet

- Geen sites achter een inlog of betaalmuur.
- Geen pagina's zonder RSS-feed; die vragen per site een eigen stukje code.
- Geen AI-samenvattingen. De samenvatting in het archief is de eerste alinea van
  het origineel. Dat is gratis en verandert de tekst niet.
- Geen volledige tekst van pdf's; wel de titel, samenvatting en de link.
- Geen Engelstalige bronnen meer, en Engelstalige berichten worden bij binnenkomst
  geweigerd — ook als een zoekfeed ze meestuurt.

## Aantekeningen

Je aantekeningen bij een item worden in je eigen browser bewaard, niet in de repo.
Ze blijven dus privé, maar ze reizen niet mee naar een andere computer. Wil je ze
wel in het archief zelf? Dat kan, maar dan staan ze in de repo — zeg het als je
dat liever hebt.
