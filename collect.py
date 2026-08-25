#!/usr/bin/env python3
"""Verzamelaar voor het archief ondermijning — alleen Nederlandse bronnen.

Twee snelheden:

  python collect.py                 actueel: elk uur alle feeds + de recente
                                    officiele publicaties per dossier
  python collect.py --terugzoeken   diepte: loopt per dossier jaar voor jaar
                                    door het nieuwsarchief en pagineert door de
                                    officiele publicaties. Hiermee komt oud
                                    materiaal binnen (goudhandel, zorgfraude,
                                    ondergronds bankieren) dat feeds niet meer
                                    aanbieden.
  python collect.py --terugzoeken --dossier "Goudhandel & edelmetalen"
                                    alleen dat ene dossier terugzoeken.

Schrijft items.json (het archief), dossiers.json (de indeling) en status.json
(de stand van de verzamelaar). Alleen standaardbibliotheek, geen installatie.
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
PAUZE = 1.1  # seconden tussen verzoeken; bronnen niet overbelasten

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


# ---------------------------------------------------------------- hulpmiddelen

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


# ------------------------------------------------------------------- inlezers

def uit_feed(rauw: bytes):
    """(titel, link, samenvatting, datum) per item, voor RSS en Atom."""
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

    De schema's verschillen per collectie, dus we lopen de velden op naam af
    in plaats van op een vast pad. Dat is minder elegant maar breekt niet als
    KOOP het antwoord aanpast.
    """
    wortel = ET.fromstring(rauw)
    records = [k for k in wortel.iter() if _naam(k) == "record"]
    for record in records:
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
            velden.get("available")
            or velden.get("date")
            or velden.get("issued")
            or velden.get("modified")
            or ""
        )
        delen = [
            velden.get("type", ""),
            velden.get("creator", ""),
            velden.get("publisher", ""),
            ontdaan(velden.get("abstract", "")),
        ]
        samenvatting = " · ".join(d for d in delen if d)
        yield titel, link, samenvatting, datum


LEZERS = {"feed": uit_feed, "sru": uit_sru}


def adressen(bron) -> list:
    if bron.get("urls"):
        return list(bron["urls"])
    return [bron["url"]]


def eerste_werkende(bron):
    """Probeert de adressen op volgorde; levert (items, gebruikte url, fout)."""
    lezer = LEZERS[bron.get("formaat", "feed")]
    laatste = "geen adres"
    for url in adressen(bron):
        try:
            rauw = haal_op(url)
            gelezen = list(lezer(rauw))
            if gelezen or bron.get("leeg_is_ok"):
                return gelezen, url, None
            laatste = "0 records"
        except urllib.error.HTTPError as fout:
            laatste = "fout %s" % fout.code
        except (urllib.error.URLError, ET.ParseError, OSError, ValueError, KeyError) as fout:
            laatste = "fout: %s" % type(fout).__name__
        time.sleep(PAUZE)
    return None, adressen(bron)[0], laatste


# --------------------------------------------------------------- dossierindeling

def bouw_matchers(dossiers, context):
    """Zet de zoekwoorden om in kant-en-klare regels per dossier."""
    regels = []
    for d in dossiers:
        regels.append(
            {
                "naam": d["naam"],
                "hard": [w.lower() for w in d.get("hard", [])],
                "zwak": [w.lower() for w in d.get("zwak", [])],
            }
        )
    return regels, [w.lower() for w in context]


def indelen(tekst, regels, context):
    """Levert (dossiernamen, gevonden trefwoorden).

    Een hard woord is op zichzelf genoeg. Een zwak woord telt alleen mee als
    er ook een contextwoord in de tekst staat — zo levert 'goudprijs stijgt'
    geen archiefitem op, maar 'goudhandelaar aangehouden' wel.
    """
    laag = " " + tekst.lower() + " "
    heeft_context = any(w in laag for w in context)
    namen, treffers = [], []
    for regel in regels:
        raak = [w for w in regel["hard"] if w in laag]
        if heeft_context:
            raak += [w for w in regel["zwak"] if w in laag]
        if raak:
            namen.append(regel["naam"])
            treffers += raak
    return namen, treffers


# ------------------------------------------------------------------ bronnenlijst

def dossierbronnen(config, jaar=None):
    """Nieuws- en SRU-bronnen die uit de dossiers zelf volgen."""
    op = config["officiele_publicaties"]
    basis = op["sru_basis"]
    collectie = op["collecties"]
    per_ronde = op.get("per_ronde", 60)
    lijst = []

    for d in config["dossiers"]:
        vraag = d.get("nieuws")
        if vraag:
            if jaar:
                vraag = "%s after:%d-01-01 before:%d-01-01" % (vraag, jaar, jaar + 1)
            lijst.append(
                {
                    "naam": "Nieuws — %s%s" % (d["naam"], " (%d)" % jaar if jaar else ""),
                    "url": NIEUWS_BASIS.format(q=urllib.parse.quote(vraag, safe="")),
                    "methode": "Zoekfeed",
                    "ritme": "1 uur" if not jaar else "terugzoeken",
                    "soort": "Nieuwsbericht",
                    "regio": "Landelijk",
                    "dossiers": [d["naam"]],
                    "altijd": True,
                    "leeg_is_ok": True,
                    "uitgever_uit_titel": True,
                }
            )

        cql = d.get("sru")
        if cql:
            vraag = '%s AND cql.textAndIndexes="%s"' % (collectie, cql.replace('"', ""))
            urls = [
                "%s&query=%s&maximumRecords=%d&startRecord=1"
                % (b, urllib.parse.quote(vraag, safe=""), per_ronde)
                for b in basis
            ]
            lijst.append(
                {
                    "naam": "Officiele publicaties — %s" % d["naam"],
                    "urls": urls,
                    "formaat": "sru",
                    "methode": "SRU",
                    "ritme": "1 dag",
                    "soort": "Kamerstuk",
                    "regio": "Landelijk",
                    "dossiers": [d["naam"]],
                    "altijd": True,
                    "leeg_is_ok": True,
                }
            )
    return lijst


def sru_paginas(config, dossier):
    """Extra pagina's per dossier, alleen bij terugzoeken."""
    op = config["officiele_publicaties"]
    per_ronde = op.get("per_ronde", 60)
    paginas = op.get("pagina_s_terugzoeken", 8)
    cql = dossier.get("sru")
    if not cql:
        return []
    vraag = '%s AND cql.textAndIndexes="%s"' % (
        op["collecties"], cql.replace('"', "")
    )
    lijst = []
    for stap in range(1, paginas):
        start = stap * per_ronde + 1
        urls = [
            "%s&query=%s&maximumRecords=%d&startRecord=%d"
            % (b, urllib.parse.quote(vraag, safe=""), per_ronde, start)
            for b in op["sru_basis"]
        ]
        lijst.append(
            {
                "naam": "Officiele publicaties — %s (vanaf %d)" % (dossier["naam"], start),
                "urls": urls,
                "formaat": "sru",
                "methode": "SRU",
                "ritme": "terugzoeken",
                "soort": "Kamerstuk",
                "regio": "Landelijk",
                "dossiers": [dossier["naam"]],
                "altijd": True,
                "leeg_is_ok": True,
            }
        )
    return lijst


SOORT_UIT_LINK = [
    ("kv-tk", "Kamervragen"),
    ("ah-tk", "Kamervragen"),
    ("kst-", "Kamerstuk"),
    ("h-tk", "Handelingen"),
    ("stcrt", "Staatscourant"),
    ("stb-", "Staatsblad"),
    ("gmb-", "Gemeenteblad"),
    ("prb-", "Provinciaal blad"),
    ("blg-", "Bijlage kamerstuk"),
    ("trb-", "Tractatenblad"),
    ("uitspraken.rechtspraak.nl", "Uitspraak"),
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

def vaste_dossiers(config):
    """Per bronnaam het dossier dat geldt als de tekst zelf niets oplevert."""
    kaart = {}
    for d in config["dossiers"]:
        kaart["Nieuws — %s" % d["naam"]] = [d["naam"]]
        kaart["Officiele publicaties — %s" % d["naam"]] = [d["naam"]]
    for bron in config["bronnen"]:
        if bron.get("dossiers"):
            kaart[bron["naam"]] = list(bron["dossiers"])
    return kaart


def herindelen(bestaand, regels, context, terugval):
    """Deelt alles wat al in het archief zit opnieuw in.

    Nodig omdat de dossiers veranderen: een nieuw dossier of een nieuw zoekwoord
    moet ook gelden voor items die er al staan. Items die nergens in passen
    houden een leeg dossierveld; die staan op de site onder 'Nog niet ingedeeld'.
    """
    ingedeeld = 0
    for item in bestaand:
        namen, treffers = indelen(
            item.get("titel", "") + " " + item.get("samenvatting", ""), regels, context
        )
        item["dossiers"] = namen or terugval.get(
            (item.get("verzameld_via") or "").split(" (")[0], []
        )
        item["tags"] = sorted(set(treffers))[:6]
        if item["dossiers"]:
            ingedeeld += 1
    return ingedeeld


def verwerk(bron, gelezen, bestaand, bekend, regels, context):
    gevonden = 0
    for titel, link, samenvatting, datum in gelezen:
        if not link:
            continue
        namen, treffers = indelen(titel + " " + samenvatting, regels, context)
        if not namen:
            if not bron.get("altijd"):
                continue
            namen = list(bron.get("dossiers", []))
            if not namen:
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
        gezien = sorted(set(namen + list(bron.get("dossiers", []))))
        bestaand.append(
            {
                "id": sleutel,
                "datum": wanneer.strftime("%Y-%m-%d"),
                "tijd": wanneer.strftime("%H:%M"),
                "nieuw": True,
                "titel": titel,
                "bron": uitgever,
                "verzameld_via": bron["naam"],
                "soort": soort_van(bron, link, samenvatting),
                "regio": bron.get("regio", "Onbekend"),
                "dossiers": gezien,
                "tags": sorted(set(treffers))[:6],
                "samenvatting": samenvatting[:320],
                "link": link,
            }
        )
        bekend.add(sleutel)
        gevonden += 1
    return gevonden


def main() -> None:
    praat = argparse.ArgumentParser(description="Verzamelaar archief ondermijning")
    praat.add_argument("--terugzoeken", action="store_true", help="loop jaar voor jaar door de archieven")
    praat.add_argument("--dossier", default=None, help="beperk terugzoeken tot dit dossier")
    praat.add_argument("--vanaf", type=int, default=None, help="beginjaar bij terugzoeken")
    keuze = praat.parse_args()

    config = json.loads((ROOT / "feeds.json").read_text("utf-8"))
    regels, context = bouw_matchers(config["dossiers"], config["context"])

    pad_items = ROOT / "items.json"
    bestaand = json.loads(pad_items.read_text("utf-8")) if pad_items.exists() else []
    bekend = {item["id"] for item in bestaand}

    if bestaand:
        ingedeeld = herindelen(bestaand, regels, context, vaste_dossiers(config))
        print("herindeling: %d van %d items in een dossier" % (ingedeeld, len(bestaand)))

    if keuze.terugzoeken:
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
                b
                for b in dossierbronnen({**config, "dossiers": dossiers}, jaar=jaar)
                if b["methode"] == "Zoekfeed"
            ]
        stand_bewaren = False
    else:
        bronnen = dossierbronnen(config) + config["bronnen"]
        stand_bewaren = True

    nieuw_totaal = 0
    regels_status = []

    for bron in bronnen:
        gelezen, gebruikt, fout = eerste_werkende(bron)
        if gelezen is None:
            status, ok, gevonden = fout, False, 0
        else:
            gevonden = verwerk(bron, gelezen, bestaand, bekend, regels, context)
            status, ok = ("%d nieuw" % gevonden if gevonden else "bij"), True
        nieuw_totaal += gevonden
        eigen = sum(1 for i in bestaand if i.get("verzameld_via") == bron["naam"])
        regels_status.append(
            {
                "naam": bron["naam"],
                "url": gebruikt.replace("https://", "").replace("http://", "")[:72],
                "methode": bron.get("methode", "RSS"),
                "ritme": bron.get("ritme", "1 uur"),
                "items": eigen,
                "status": status,
                "ok": ok,
            }
        )
        print("%s: %s" % (bron["naam"], status))
        time.sleep(PAUZE)

    bestaand.sort(key=lambda i: (i.get("datum", ""), i.get("tijd", "")), reverse=True)
    for stand, item in enumerate(bestaand):
        item["nieuw"] = stand < nieuw_totaal
    bestaand = bestaand[:MAX_ITEMS]

    pad_items.write_text(json.dumps(bestaand, ensure_ascii=False, indent=1), "utf-8")

    # dossiers.json: de indeling plus de stand per dossier, voor de website
    per_dossier = {}
    for item in bestaand:
        for naam in item.get("dossiers", []):
            hok = per_dossier.setdefault(naam, {"aantal": 0, "laatst": "", "nieuw": 0})
            hok["aantal"] += 1
            hok["nieuw"] += 1 if item.get("nieuw") else 0
            stempel = item.get("datum", "")
            if stempel > hok["laatst"]:
                hok["laatst"] = stempel
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
            ensure_ascii=False,
            indent=1,
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
                    "bronnen": regels_status,
                },
                ensure_ascii=False,
                indent=1,
            ),
            "utf-8",
        )
    print("klaar: %d nieuw, %d in archief" % (nieuw_totaal, len(bestaand)))


if __name__ == "__main__":
    main()
