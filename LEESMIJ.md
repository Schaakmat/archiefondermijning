# Archief ondermijning — installatie

Deze map is de complete inhoud van `github.com/schaakmat/archiefondermijning`.
Alles hieronder is gratis: GitHub Actions haalt elk uur de bronnen op, GitHub Pages
publiceert de site. Je hebt geen server, abonnement of creditcard nodig.

## Wat zit erin

| bestand | wat het doet |
| --- | --- |
| `index.html` | de archiefsite, één bestand, werkt ook offline |
| `feeds.json` | de bronnenlijst en de zoekwoorden waarop gefilterd wordt |
| `collect.py` | de verzamelaar: haalt bronnen op, filtert, schrijft `items.json` |
| `.github/workflows/collect.yml` | het uurschema dat `collect.py` draait |
| `items.json` | het archief zelf (wordt bij de eerste run aangemaakt) |
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

## Bronnen toevoegen of aanpassen

Alles gebeurt in `feeds.json`. Een bron is één blok:

```json
{
  "naam": "Gemeente Tilburg",
  "url": "https://www.tilburg.nl/actueel/nieuws/rss",
  "methode": "RSS",
  "ritme": "1 dag",
  "soort": "Nieuwsbericht",
  "regio": "Tilburg",
  "tags": ["Regio", "Bestuurlijke aanpak"]
}
```

- `soort` verschijnt in de kolom *soort* en in het filter links.
- `tags` bepalen in welk dossier het item terechtkomt.
- Zet `"altijd": true` als je álles van die bron wil bewaren, ook zonder zoekwoord.

`zoekwoorden` bovenaan `feeds.json` is het filter voor alle bronnen. Voeg toe wat
je mist; verwijder wat te veel ruis geeft.

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

## Aantekeningen

Je aantekeningen bij een item worden in je eigen browser bewaard, niet in de repo.
Ze blijven dus privé, maar ze reizen niet mee naar een andere computer. Wil je ze
wel in het archief zelf? Dat kan, maar dan staan ze in de repo — zeg het als je
dat liever hebt.
