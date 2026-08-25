#!/usr/bin/env python3
"""Verzamelaar voor het persoonlijke archief ondermijning.

Leest feeds.json, haalt elke bron op, filtert op zoekwoorden en schrijft
items.json (het archief) en status.json (de stand van de verzamelaar).
Gebruikt alleen de Python-standaardbibliotheek: geen installatie nodig.
"""

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
MAX_ITEMS = 6000
TIMEOUT = 25
UA = "archief-ondermijning/1.0 (persoonlijk archief)"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def ontdaan(html: str) -> str:
    """Haalt opmaak uit een stukje html en normaliseert witruimte."""
    if not html:
        return ""
    tekst = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", unescape(tekst)).strip()


def haal_op(url: str) -> bytes:
    verzoek = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(verzoek, timeout=TIMEOUT) as reactie:
        return reactie.read()


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


def inzendingen(rauw: bytes):
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
        try:
            rauw = haal_op(bron["url"])
            for titel, link, samenvatting, datum in inzendingen(rauw):
                treffers = relevant(titel + " " + samenvatting, zoekwoorden)
                if not treffers and not bron.get("altijd"):
                    continue
                sleutel = hashlib.sha1(link.encode("utf-8")).hexdigest()[:10]
                if sleutel in bekend:
                    continue
                wanneer = datum or datetime.now(timezone.utc)
                bestaand.append(
                    {
                        "id": sleutel,
                        "datum": wanneer.strftime("%Y-%m-%d"),
                        "tijd": wanneer.strftime("%H:%M"),
                        "nieuw": True,
                        "titel": titel,
                        "bron": bron["naam"],
                        "soort": bron.get("soort", "Nieuwsbericht"),
                        "regio": bron.get("regio", "Onbekend"),
                        "tags": bron.get("tags", []) + treffers[:3],
                        "samenvatting": samenvatting[:320],
                        "citaat": samenvatting[:400] or titel,
                        "link": link,
                    }
                )
                bekend.add(sleutel)
                gevonden += 1
            status, ok = (f"{gevonden} nieuw" if gevonden else "bij"), True
        except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError, OSError) as fout:
            status, ok = f"fout: {type(fout).__name__}", False

        nieuw_totaal += gevonden
        eigen = sum(1 for i in bestaand if i["bron"] == bron["naam"])
        regels.append(
            {
                "naam": bron["naam"],
                "url": bron["url"].replace("https://", "").replace("http://", ""),
                "methode": bron.get("methode", "RSS"),
                "ritme": bron.get("ritme", "1 uur"),
                "items": eigen,
                "status": status,
                "ok": ok,
            }
        )
        print(f"{bron['naam']}: {status}")

    # nieuwste eerst, en het archief afkappen zodat de repo klein blijft
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
