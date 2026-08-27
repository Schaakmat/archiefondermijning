#!/usr/bin/env python3
"""Verzamelaar voor het archief ondermijning — alleen Nederlandstalige bronnen.

Twee snelheden:

  python collect.py                 actueel: elk uur alle feeds + de recente
                                    officiele publicaties per dossier
  python collect.py --terugzoeken   diepte: loopt per dossier jaar voor jaar
                                    door het nieuwsarchief en pagineert door de
                                    officiele publicaties
  python collect.py --opschonen     alleen het bestaande archief opnieuw langs
                                    de filters halen, niets ophalen

Wat een item moet doorstaan om in het archief te komen:

  1. het adres is Nederlands (.nl, of een doorverwijzing van Google Nieuws);
  2. de tekst is Nederlands, en niet overwegend Engels;
  3. de tekst bevat een HARD zoekwoord van een dossier, of een ZWAK zoekwoord
     samen met een woord uit de opsporingssfeer;
  4. er staat geen ruiswoord in (sport, ziekte, showbizz) zonder dat er een
     hard zoekwoord tegenover staat.

De zoekopdracht van een feed telt dus niet als bewijs: elk bericht wordt op
zijn eigen tekst beoordeeld. Daardoor levert een zoekfeed voor 'zorgfraude'
geen artikelen over ziektes meer op.

Alleen standaardbibliotheek, geen installatie nodig.
"""

import argparse
import gzip
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path

ROOT = Path(__file__).parent
MAX_ITEMS = 20000
TIMEOUT = 30
PAUZE = 1.1

KOPPEN = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/json, text/xml, */*",
    "Accept-Language": "nl,en;q=0.6",
    "Accept-Encoding": "gzip",
}

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

NIEUWS_BASIS = "https://news.google.com/rss/search?q={q}&hl=nl&gl=NL&ceid=NL:nl"

# Woorden die in vrijwel elke Nederlandse zin staan. Twee treffers is genoeg om
# een tekst als Nederlands te beschouwen; Engelse berichten halen dat niet.
NL_WOORDEN = (
    " de ", " het ", " een ", " van ", " en ", " op ", " met ", " voor ", " aan ",
    " bij ", " naar ", " uit ", " dat ", " die ", " is ", " zijn ", " wordt ",
    " werd ", " niet ", " ook ", " maar ", " over ", " tegen ", " door ", " zich ",
)

# Engelse functiewoorden. Wegen zwaarder dan de Nederlandse, omdat een Engelse
# titel met een Nederlandse eigennaam erin anders alsnog doorglipt.
EN_WOORDEN = (
    " the ", " of ", " and ", " in the ", " for ", " with ", " from ", " this ",
    " that the ", " are ", " was ", " were ", " has ", " have ", " been ", " its ",
    " study ", " research ", " during ", " between ", " among ", " through ",
)

# Adressen die het archief in mogen: Nederlandse domeinen, plus de
# doorverwijzingen van Google Nieuws. Al het andere valt af, ongeacht de tekst.
# Dit is de vangnetregel voor wetenschappelijke databanken en buitenlandse
# persbureaus: die publiceren niet op een .nl-adres.
NL_TLDS = (".nl", ".amsterdam", ".frl", ".vlaanderen")
DOORVERWIJZERS = ("news.google.com", "google.com", "google.nl")


def ontdaan(html: str) -> str:
    if not html:
        return ""
    tekst = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", unescape(tekst)).strip()


def haal_op(url: str) -> bytes:
    verzoek = urllib.request.Request(url, headers=KOPPEN)
    with urllib.request.urlopen(verzoek, timeout=TIMEOUT) as reactie:
        rauw = reactie.read()
    if rauw[:2] == b"\x1f\x8b":
        rauw = gzip.decompress(rauw)
    return rauw


def tekst_van(knoop, *paden) -> str:
    for pad in paden:
        gevonden = knoop.find(pad, NS)
        if gevonden is not None:
            if gevonden.text:
                return gevonden.text
            href = gevonden.get("href")
            if href:
                return href
    return ""


def datum_van(ruw: str):
    ruw = (ruw or "").strip()
    if not ruw:
        return None
    try:
        return parsedate_to_datetime(ruw)
    except (TypeError, ValueError):
        pass
    schoon = ruw.replace("Z", "+00:00")
    for vorm in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(schoon, vorm)
        except ValueError:
            continue
    return None


def geldige_datum(datum):
    """Databanken leveren soms onmogelijke jaartallen. Die negeren we."""
    nu = datetime.now(timezone.utc)
    if datum is None:
        return nu
    if datum.tzinfo is None:
        datum = datum.replace(tzinfo=timezone.utc)
    if datum > nu + timedelta(days=2) or datum.year < 1995:
        return nu
    return datum


def nederlands(tekst: str) -> bool:
    laag = " " + re.sub(r"[^\w\s]", " ", tekst.lower()) + " "
    nl = sum(1 for w in NL_WOORDEN if w in laag)
    en = sum(1 for w in EN_WOORDEN if w in laag)
    return nl >= 2 and nl > en


def nl_adres(link: str) -> bool:
    host = urllib.parse.urlsplit(link).netloc.lower().split(":")[0]
    if not host:
        return False
    if host in DOORVERWIJZERS or host.endswith(DOORVERWIJZERS):
        return True
    return host.endswith(NL_TLDS)


# -------------------------------------------------------------------- inlezers

def uit_feed(rauw: bytes):
    wortel = ET.fromstring(rauw)
    knopen = wortel.findall(".//item") or wortel.findall(".//atom:entry", NS)
    for knoop in knopen:
        titel = ontdaan(tekst_van(knoop, "title", "atom:title"))
        link = tekst_van(knoop, "link", "atom:link", "guid").strip()
        samenvatting = ontdaan(
            tekst_van(knoop, "description", "atom:summary", "content:encoded", "atom:content")
        )
        datum = datum_van(tekst_van(knoop, "pubDate", "atom:updated", "atom:published", "dc:date"))
        if titel and link:
            yield titel, link, samenvatting, datum


def _naam(knoop) -> str:
    return knoop.tag.rsplit("}", 1)[-1].lower()


def uit_sru(rauw: bytes):
    """Records uit de SRU-zoekdienst van de officiele publicaties.

    De schema's verschillen per collectie, dus we lopen de velden op naam af in
    plaats van op een vast pad.
    """
    wortel = ET.fromstring(rauw)
    for record in [k for k in wortel.iter() if _naam(k) == "record"]:
        velden = {}
        for knoop in record.iter():
            naam = _naam(knoop)
            waarde = (knoop.text or "").strip()
            if waarde and naam not in velden:
                velden[naam] = waarde
        titel = ontdaan(velden.get("title", ""))
        link = velden.get("preferredurl") or velden.get("itemurl") or ""
        if not link:
            kandidaat = velden.get("identifier", "")
            link = kandidaat if kandidaat.startswith("http") else ""
        if not titel or not link:
            continue
        datum = datum_van(
            velden.get("available") or velden.get("date")
            or velden.get("issued") or velden.get("modified") or ""
        )
        delen = [
            velden.get("type", ""), velden.get("creator", ""),
            velden.get("publisher", ""), ontdaan(velden.get("abstract", "")),
        ]
        yield titel, link, " · ".join(d for d in delen if d), datum


LEZERS = {"feed": uit_feed, "sru": uit_sru}


def adressen(bron) -> list:
    return list(bron["urls"]) if bron.get("urls") else [bron["url"]]


def eerste_werkende(bron):
    """Probeert de adressen op volgorde; levert (items, gebruikte url, fout).

    Meerdere adressen per bron is de manier waarop het archief zichzelf
    overeind houdt: verandert een omroep zijn feed-adres, dan pakt de
    zoekfeed-variant het over zonder dat er iets stukgaat.
    """
    lezer = LEZERS[bron.get("formaat", "feed")]
    laatste = "geen adres"
    for url in adressen(bron):
        try:
            gelezen = list(lezer(haal_op(url)))
            if gelezen or bron.get("leeg_is_ok"):
                return gelezen, url, None
            laatste = "0 records"
        except urllib.error.HTTPError as fout:
            laatste = "fout %s" % fout.code
        except (urllib.error.URLError, ET.ParseError, OSError, ValueError, KeyError) as fout:
            laatste = "fout: %s" % type(fout).__name__
        time.sleep(PAUZE)
    return None, adressen(bron)[0], laatste


# -------------------------------------------------------------- dossierindeling

def bouw_matchers(config):
    regels = [
        {
            "naam": d["naam"],
            "hard": [w.lower() for w in d.get("hard", [])],
            "zwak": [w.lower() for w in d.get("zwak", [])],
        }
        for d in config["dossiers"]
    ]
    return (
        regels,
        [w.lower() for w in config.get("context", [])],
        [w.lower() for w in config.get("ruiswoorden", [])],
    )


def indelen(tekst, regels, context, ruis):
    """Levert (dossiernamen, trefwoorden, hard) voor een stuk tekst."""
    laag = " " + tekst.lower() + " "
    heeft_context = any(w in laag for w in context)
    namen, treffers, hard = [], [], False
    for regel in regels:
        raak = [w for w in regel["hard"] if w in laag]
        if raak:
            hard = True
        if heeft_context:
            raak += [w for w in regel["zwak"] if w in laag]
        if raak:
            namen.append(regel["naam"])
            treffers += raak
    # Een ruiswoord haalt de zwakke treffers onderuit, maar wint nooit van een
    # hard zoekwoord: 'wietkwekerij naast voetbalclub' blijft dus staan.
    if namen and not hard and any(w in laag for w in ruis):
        return [], [], False
    return namen, treffers, hard


def toegelaten(tekst, link, bron, regels, context, ruis):
    """(dossiers, trefwoorden) of (None, reden) als het item afvalt."""
    if not nl_adres(link):
        return None, "buitenlands adres"
    if not nederlands(tekst):
        return None, "niet-nederlands"
    namen, treffers, hard = indelen(tekst, regels, context, ruis)
    if namen:
        return sorted(set(namen)), sorted(set(treffers))[:6]
    # Officiele bronnen mogen door op een contextwoord alleen, maar krijgen dan
    # het dossier dat bij de bron hoort in plaats van een verzonnen indeling.
    if bron.get("soepel") and bron.get("dossiers"):
        laag = " " + tekst.lower() + " "
        if any(w in laag for w in context):
            return list(bron["dossiers"]), []
    return None, "geen dossier"


# ---------------------------------------------------------------- bronnenlijst

def sru_urls(config, cql, start=1):
    """Meerdere queryvormen per zoekopdracht.

    De zoekdienst wijst een vrije-tekst-index af met een serverfout, dus we
    proberen achtereenvolgens de titel-index, de onderwerp-index en pas daarna
    de vrije tekst. De eerste die records teruggeeft wint.
    """
    op = config["officiele_publicaties"]
    per_ronde = op.get("per_ronde", 60)
    collectie = op["collecties"]
    termen = [t.strip() for t in re.split(r"\bOR\b", cql) if t.strip()]

    vormen = []
    vormen.append(" OR ".join('dt.title="%s"' % t for t in termen))
    vormen.append(" OR ".join('dt.subject="%s"' % t for t in termen))
    vormen.append('cql.textAndIndexes="%s"' % termen[0])

    urls = []
    for basis in op["sru_basis"]:
        for vorm in vormen:
            vraag = "(%s AND (%s))" % (collectie, vorm)
            urls.append(
                "%s&query=%s&maximumRecords=%d&startRecord=%d"
                % (basis, urllib.parse.quote(vraag, safe=""), per_ronde, start)
            )
    return urls


def dossierbronnen(config, jaar=None):
    lijst = []
    for d in config["dossiers"]:
        if d.get("nieuws"):
            vraag = d["nieuws"]
            if jaar:
                vraag = "%s after:%d-01-01 before:%d-01-01" % (vraag, jaar, jaar + 1)
            lijst.append({
                "naam": "Nieuws — %s%s" % (d["naam"], " (%d)" % jaar if jaar else ""),
                "url": NIEUWS_BASIS.format(q=urllib.parse.quote(vraag, safe="")),
                "methode": "Zoekfeed",
                "ritme": "terugzoeken" if jaar else "1 uur",
                "soort": "Nieuwsbericht",
                "regio": "Landelijk",
                "dossiers": [d["naam"]],
                "leeg_is_ok": True,
                "uitgever_uit_titel": True,
            })
        if d.get("sru"):
            lijst.append({
                "naam": "Officiele publicaties — %s" % d["naam"],
                "urls": sru_urls(config, d["sru"]),
                "formaat": "sru",
                "methode": "SRU",
                "ritme": "1 dag",
                "soort": "Kamerstuk",
                "regio": "Landelijk",
                "dossiers": [d["naam"]],
                "soepel": True,
                "leeg_is_ok": True,
            })
    return lijst


def sru_paginas(config, dossier):
    op = config["officiele_publicaties"]
    per_ronde = op.get("per_ronde", 60)
    if not dossier.get("sru"):
        return []
    return [
        {
            "naam": "Officiele publicaties — %s (vanaf %d)" % (dossier["naam"], stap * per_ronde + 1),
            "urls": sru_urls(config, dossier["sru"], start=stap * per_ronde + 1),
            "formaat": "sru",
            "methode": "SRU",
            "ritme": "terugzoeken",
            "soort": "Kamerstuk",
            "regio": "Landelijk",
            "dossiers": [dossier["naam"]],
            "soepel": True,
            "leeg_is_ok": True,
        }
        for stap in range(1, op.get("pagina_s_terugzoeken", 8))
    ]


SOORT_UIT_LINK = [
    ("kv-tk", "Kamervragen"), ("ah-tk", "Kamervragen"), ("kst-", "Kamerstuk"),
    ("h-tk", "Handelingen"), ("stcrt", "Staatscourant"), ("stb-", "Staatsblad"),
    ("gmb-", "Gemeenteblad"), ("prb-", "Provinciaal blad"), ("blg-", "Bijlage kamerstuk"),
    ("trb-", "Tractatenblad"), ("uitspraken.rechtspraak.nl", "Uitspraak"),
]


def soort_van(bron, link, samenvatting):
    for stukje, naam in SOORT_UIT_LINK:
        if stukje in link.lower():
            return naam
    laag = samenvatting.lower()
    for woord, naam in (("rapport", "Rapport"), ("kamerbrief", "Kamerbrief"), ("wet", "Wetgeving")):
        if laag.startswith(woord):
            return naam
    return bron.get("soort", "Nieuwsbericht")


# ------------------------------------------------------------------------- run

def verwerk(bron, gelezen, bestaand, bekend, regels, context, ruis, geweigerd):
    gevonden = 0
    for titel, link, samenvatting, datum in gelezen:
        if not link:
            continue
        namen, treffers = toegelaten(titel + " " + samenvatting, link, bron, regels, context, ruis)
        if namen is None:
            geweigerd[treffers] = geweigerd.get(treffers, 0) + 1
            continue
        sleutel = hashlib.sha1(link.encode("utf-8")).hexdigest()[:10]
        if sleutel in bekend:
            continue

        uitgever = bron["naam"]
        if bron.get("uitgever_uit_titel") and " - " in titel:
            kop, _, achter = titel.rpartition(" - ")
            if kop and len(achter) < 40:
                titel, uitgever = kop, achter

        wanneer = geldige_datum(datum)
        bestaand.append({
            "id": sleutel,
            "datum": wanneer.strftime("%Y-%m-%d"),
            "tijd": wanneer.strftime("%H:%M"),
            "nieuw": True,
            "titel": titel,
            "bron": uitgever,
            "verzameld_via": bron["naam"],
            "soort": soort_van(bron, link, samenvatting),
            "regio": bron.get("regio", "Onbekend"),
            "dossiers": namen,
            "tags": treffers,
            "samenvatting": samenvatting[:320],
            "link": link,
        })
        bekend.add(sleutel)
        gevonden += 1
    return gevonden


def opschonen(bestaand, config, regels, context, ruis):
    """Haalt het bestaande archief opnieuw langs de filters.

    Nodig omdat de dossiers en de ruiswoorden veranderen: een strenger filter
    moet ook gelden voor wat er al staat. Wat nu niet meer door de filters komt
    (Engelstalige resten, artikelen over ziektes uit de zorgfraude-zoekopdracht)
    verdwijnt uit het archief.
    """
    soepele = {}
    for d in config["dossiers"]:
        soepele["Officiele publicaties — %s" % d["naam"]] = [d["naam"]]
    for bron in config["bronnen"]:
        if bron.get("soepel") and bron.get("dossiers"):
            soepele[bron["naam"]] = list(bron["dossiers"])

    houden, weg = [], 0
    for item in bestaand:
        tekst = item.get("titel", "") + " " + item.get("samenvatting", "")
        via = (item.get("verzameld_via") or "").split(" (")[0]
        namen, treffers = toegelaten(
            tekst, item.get("link", ""),
            {"soepel": via in soepele, "dossiers": soepele.get(via, [])},
            regels, context, ruis,
        )
        if namen is None:
            weg += 1
            continue
        item["dossiers"] = namen
        item["tags"] = treffers
        houden.append(item)
    return houden, weg


def main() -> None:
    praat = argparse.ArgumentParser(description="Verzamelaar archief ondermijning")
    praat.add_argument("--terugzoeken", action="store_true", help="loop jaar voor jaar door de archieven")
    praat.add_argument("--opschonen", action="store_true", help="alleen het bestaande archief herbeoordelen")
    praat.add_argument("--dossier", default=None, help="beperk terugzoeken tot dit dossier")
    praat.add_argument("--vanaf", type=int, default=None, help="beginjaar bij terugzoeken")
    keuze = praat.parse_args()

    config = json.loads((ROOT / "feeds.json").read_text("utf-8"))
    regels, context, ruis = bouw_matchers(config)

    pad_items = ROOT / "items.json"
    bestaand = json.loads(pad_items.read_text("utf-8")) if pad_items.exists() else []

    verwijderd = 0
    if bestaand:
        bestaand, verwijderd = opschonen(bestaand, config, regels, context, ruis)
        print("opschonen: %d items verwijderd, %d over" % (verwijderd, len(bestaand)))
    bekend = {item["id"] for item in bestaand}

    if keuze.opschonen:
        bronnen, stand_bewaren = [], False
    elif keuze.terugzoeken:
        dossiers = config["dossiers"]
        if keuze.dossier:
            dossiers = [d for d in dossiers if d["naam"].lower().startswith(keuze.dossier.lower())]
            if not dossiers:
                sys.exit("dossier niet gevonden: %s" % keuze.dossier)
        vanaf = keuze.vanaf or config["nieuwsarchief"].get("vanaf_jaar", 2012)
        nu = datetime.now(timezone.utc).year
        bronnen = []
        for d in dossiers:
            bronnen += sru_paginas(config, d)
        for jaar in range(nu, vanaf - 1, -1):
            bronnen += [
                b for b in dossierbronnen({**config, "dossiers": dossiers}, jaar=jaar)
                if b["methode"] == "Zoekfeed"
            ]
        stand_bewaren = False
    else:
        bronnen = dossierbronnen(config) + config["bronnen"]
        stand_bewaren = True

    nieuw_totaal = 0
    geweigerd = {}
    regels_status = []

    for bron in bronnen:
        gelezen, gebruikt, fout = eerste_werkende(bron)
        if gelezen is None:
            status, ok, gevonden = fout, False, 0
        else:
            gevonden = verwerk(bron, gelezen, bestaand, bekend, regels, context, ruis, geweigerd)
            status, ok = ("%d nieuw" % gevonden if gevonden else "bij"), True
        nieuw_totaal += gevonden
        eigen = sum(1 for i in bestaand if i.get("verzameld_via") == bron["naam"])
        regels_status.append({
            "naam": bron["naam"],
            "url": gebruikt.replace("https://", "").replace("http://", "")[:72],
            "methode": bron.get("methode", "RSS"),
            "ritme": bron.get("ritme", "1 uur"),
            "items": eigen,
            "status": status,
            "ok": ok,
        })
        print("%s: %s" % (bron["naam"], status))
        time.sleep(PAUZE)

    bestaand.sort(key=lambda i: (i.get("datum", ""), i.get("tijd", "")), reverse=True)
    for stand, item in enumerate(bestaand):
        item["nieuw"] = stand < nieuw_totaal
    bestaand = bestaand[:MAX_ITEMS]

    pad_items.write_text(json.dumps(bestaand, ensure_ascii=False, indent=1), "utf-8")

    per_dossier = {}
    for item in bestaand:
        for naam in item.get("dossiers", []):
            hok = per_dossier.setdefault(naam, {"aantal": 0, "laatst": ""})
            hok["aantal"] += 1
            if item.get("datum", "") > hok["laatst"]:
                hok["laatst"] = item.get("datum", "")
    (ROOT / "dossiers.json").write_text(
        json.dumps(
            [
                {
                    "naam": d["naam"],
                    "omschrijving": d["omschrijving"],
                    "aantal": per_dossier.get(d["naam"], {}).get("aantal", 0),
                    "laatst": per_dossier.get(d["naam"], {}).get("laatst", ""),
                    "woorden": d.get("hard", [])[:8],
                }
                for d in config["dossiers"]
            ],
            ensure_ascii=False, indent=1,
        ),
        "utf-8",
    )

    if stand_bewaren:
        (ROOT / "status.json").write_text(
            json.dumps(
                {
                    "run": datetime.now(timezone.utc).strftime("%H:%M"),
                    "datum": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "nieuw": nieuw_totaal,
                    "totaal": len(bestaand),
                    "verwijderd": verwijderd,
                    "geweigerd": geweigerd,
                    "bronnen": regels_status,
                },
                ensure_ascii=False, indent=1,
            ),
            "utf-8",
        )

    afgewezen = sum(geweigerd.values())
    print("klaar: %d nieuw, %d afgewezen, %d in archief" % (nieuw_totaal, afgewezen, len(bestaand)))


if __name__ == "__main__":
    main()
