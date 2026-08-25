#!/usr/bin/env python3
"""Verzamelaar voor het persoonlijke archief ondermijning.

Leest feeds.json, haalt elke bron op, filtert op zoekwoorden en schrijft
items.json (het archief) en status.json (de stand van de verzamelaar).
Gebruikt alleen de Python-standaardbibliotheek: geen installatie nodig.

Drie soorten bronnen, in te stellen met "formaat" in feeds.json:
  feed      RSS of Atom (standaard)
  crossref  wetenschappelijke publicaties via api.crossref.org
  openalex  wetenschappelijke publicaties via api.openalex.org
"""

import gzip
import hashlib
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path

ROOT = Path(__file__).parent
MAX_ITEMS = 8000
TIMEOUT = 30

KOPPEN = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/json, text/xml, */*",
    "Accept-Language": "nl,en;q=0.8",
    "Accept-Encoding": "gzip",
}

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def ontdaan(html: str) -> str:
    """Haalt opmaak uit een stukje html of jats en normaliseert witruimte."""
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
    for vorm in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(schoon, vorm)
        except ValueError:
            continue
    return None


def uit_feed(rauw: bytes):
    """Levert (titel, link, samenvatting, datum) per item, voor RSS en Atom."""
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


def uit_crossref(rauw: bytes):
    """Wetenschappelijke publicaties uit de Crossref-api."""
    data = json.loads(rauw)
    for werk in data.get("message", {}).get("items", []):
        titels = werk.get("title") or []
        if not titels:
            continue
        tijdschrift = (werk.get("container-title") or [""])[0]
        auteurs = werk.get("author") or []
        namen = ", ".join(
            (a.get("family", "") + (" " + a.get("given", "")[:1] + "." if a.get("given") else "")).strip()
            for a in auteurs[:3]
        )
        losse = ontdaan(werk.get("abstract", ""))
        delen = [d for d in [tijdschrift, namen, losse] if d]
        stukjes = (
            werk.get("issued", {}).get("date-parts", [[]])[0]
            or werk.get("created", {}).get("date-parts", [[]])[0]
        )
        datum = None
        if stukjes:
            jaar = stukjes[0]
            maand = stukjes[1] if len(stukjes) > 1 else 1
            dag = stukjes[2] if len(stukjes) > 2 else 1
            try:
                datum = datetime(jaar, maand, dag, tzinfo=timezone.utc)
            except ValueError:
                datum = None
        yield ontdaan(titels[0]), werk.get("URL", ""), " · ".join(delen), datum


def uit_openalex(rauw: bytes):
    """Wetenschappelijke publicaties uit de OpenAlex-api."""
    data = json.loads(rauw)
    for werk in data.get("results", []):
        titel = ontdaan(werk.get("display_name") or "")
        if not titel:
            continue
        link = werk.get("doi") or werk.get("id") or ""
        bron = ((werk.get("primary_location") or {}).get("source") or {}).get("display_name", "")
        # OpenAlex levert samenvattingen als woord-met-posities; weer op orde zetten
        omgekeerd = werk.get("abstract_inverted_index") or {}
        woorden = []
        for woord, plekken in omgekeerd.items():
            for plek in plekken:
                woorden.append((plek, woord))
        samenvatting = " ".join(w for _, w in sorted(woorden))
        delen = [d for d in [bron, samenvatting] if d]
        yield titel, link, " · ".join(delen), datum_van(werk.get("publication_date", ""))


LEZERS = {"feed": uit_feed, "crossref": uit_crossref, "openalex": uit_openalex}


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
            return list(lezer(rauw)), url, None
        except urllib.error.HTTPError as fout:
            laatste = f"fout {fout.code}"
        except (urllib.error.URLError, ET.ParseError, OSError, ValueError, KeyError) as fout:
            laatste = f"fout: {type(fout).__name__}"
    return None, adressen(bron)[0], laatste


def relevant(tekst: str, zoekwoorden) -> list:
    laag = tekst.lower()
    return [w for w in zoekwoorden if w.lower() in laag]


def main() -> None:
    config = json.loads((ROOT / "feeds.json").read_text("utf-8"))
    zoekwoorden = config["zoekwoorden"]

    pad_items = ROOT / "items.json"
    bestaand = json.loads(pad_items.read_text("utf-8")) if pad_items.exists() else []
    bekend = {item["id"] for item in bestaand}

    nieuw_totaal = 0
    regels = []

    for bron in config["bronnen"]:
        gevonden = 0
        gelezen, gebruikt, fout = eerste_werkende(bron)

        if gelezen is None:
            status, ok = fout, False
        else:
            for titel, link, samenvatting, datum in gelezen:
                if not link:
                    continue
                treffers = relevant(titel + " " + samenvatting, zoekwoorden)
                if not treffers and not bron.get("altijd"):
                    continue
                sleutel = hashlib.sha1(link.encode("utf-8")).hexdigest()[:10]
                if sleutel in bekend:
                    continue

                # Zoekfeeds zetten de uitgever achter de titel: "Kop - NRC"
                uitgever = bron["naam"]
                if bron.get("uitgever_uit_titel") and " - " in titel:
                    kop, _, achter = titel.rpartition(" - ")
                    if kop and len(achter) < 40:
                        titel, uitgever = kop, achter

                wanneer = datum or datetime.now(timezone.utc)
                bestaand.append(
                    {
                        "id": sleutel,
                        "datum": wanneer.strftime("%Y-%m-%d"),
                        "tijd": wanneer.strftime("%H:%M"),
                        "nieuw": True,
                        "titel": titel,
                        "bron": uitgever,
                        "verzameld_via": bron["naam"],
                        "soort": bron.get("soort", "Nieuwsbericht"),
                        "regio": bron.get("regio", "Onbekend"),
                        "tags": bron.get("tags", []) + treffers[:3],
                        "samenvatting": samenvatting[:400],
                        "citaat": samenvatting[:500] or titel,
                        "link": link,
                    }
                )
                bekend.add(sleutel)
                gevonden += 1
            status, ok = (f"{gevonden} nieuw" if gevonden else "bij"), True

        nieuw_totaal += gevonden
        eigen = sum(1 for i in bestaand if i.get("verzameld_via") == bron["naam"])
        regels.append(
            {
                "naam": bron["naam"],
                "url": gebruikt.replace("https://", "").replace("http://", "")[:64],
                "methode": bron.get("methode", "RSS"),
                "ritme": bron.get("ritme", "1 uur"),
                "items": eigen,
                "status": status,
                "ok": ok,
            }
        )
        print(f"{bron['naam']}: {status}")

    bestaand.sort(key=lambda i: (i.get("datum", ""), i.get("tijd", "")), reverse=True)
    for stand, item in enumerate(bestaand):
        item["nieuw"] = stand < nieuw_totaal
    bestaand = bestaand[:MAX_ITEMS]

    pad_items.write_text(json.dumps(bestaand, ensure_ascii=False, indent=1), "utf-8")
    (ROOT / "status.json").write_text(
        json.dumps(
            {
                "run": datetime.now(timezone.utc).strftime("%H:%M"),
                "datum": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "nieuw": nieuw_totaal,
                "totaal": len(bestaand),
                "bronnen": regels,
            },
            ensure_ascii=False,
            indent=1,
        ),
        "utf-8",
    )
    print(f"klaar: {nieuw_totaal} nieuw, {len(bestaand)} in archief")


if __name__ == "__main__":
    main()
