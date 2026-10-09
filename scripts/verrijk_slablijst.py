#!/usr/bin/env python3
"""Zet bij elke weggehaalde slab-regel of er ooit een transactie aan hing.

Achtergrond: bij de v7-telling van 09-10 zijn alle slabs opnieuw onderaan gezet
en zijn de oude regels leeggemaakt. Van 47 ervan is geen tegenhanger in de nieuwe
batch gevonden. De vraag is dan: is die kaart verkocht (en dus terecht weg), of
ligt hij nog in de doos en is hij bij het overtikken vergeten?

De verkoophistorie geeft het antwoord. Een kaart die nooit een transactie had en
toch op voorraad stond, is niet verkocht — die hoort nagelopen te worden.

Gelezen wordt uit de backups van vóór de import, want de items-tabel is sindsdien
vervangen en hernummerd:
  data/backup/items_<ts>_voor_v7_import.csv
  data/backup/transactions_<ts>_voor_v7_import.csv

    python scripts/verrijk_slablijst.py
"""

import csv
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent.parent / "data"
LIJST = DATA / "v7_slabs_leeggemaakt.csv"
UIT_ALLES = DATA / "v7_slabs_leeggemaakt.csv"
UIT_NALOPEN = DATA / "v7_slabs_nalopen.csv"


def norm(v):
    if v in (None, ""):
        return ""
    s = unicodedata.normalize("NFKD", str(v))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"\.0+$", "", s.strip())).lower()


def nieuwste(patroon: str) -> Path:
    kandidaten = sorted((DATA / "backup").glob(patroon))
    if not kandidaten:
        sys.exit(f"geen backup gevonden voor {patroon}")
    return kandidaten[-1]


def getal(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def main():
    items = list(csv.DictReader(open(nieuwste("items_*_voor_v7_import.csv"))))
    txs = list(csv.DictReader(open(nieuwste("transactions_*_voor_v7_import.csv"))))
    rijen = list(csv.DictReader(open(LIJST)))

    # Oude items op naam+grade+taal, en losser op alleen naam+taal. De slabs uit
    # de vorige v7 dragen dezelfde schrijfwijze als de regels die we nu leegmaken,
    # want het zijn letterlijk dezelfde regels.
    per_sleutel, per_naam = defaultdict(list), defaultdict(list)
    for it in items:
        per_sleutel[(norm(it["onze_naam"]), norm(it["grade"]), norm(it["taal"]))].append(it)
        per_naam[(norm(it["onze_naam"]), norm(it["taal"]))].append(it)

    per_item = defaultdict(list)
    for t in txs:
        if t["item_id"]:
            per_item[t["item_id"]].append(t)

    def los_op(rij):
        """De oude items-regel(s) die bij deze v7-rij horen, met hoe zeker dat is."""
        k = (norm(rij["naam"]), norm(rij["grade"]), norm(rij["taal"]))
        if per_sleutel.get(k):
            return per_sleutel[k], "naam+grade+taal"
        kn = (norm(rij["naam"]), norm(rij["taal"]))
        if per_naam.get(kn):
            return per_naam[kn], "naam+taal"
        return [], "geen"

    def vrije_tekst(naam):
        """Verkopen waar deze naam in de ruwe tekst staat, ook zonder item_id.
        Vangt de vrije invoer op: daar hangt per definitie geen item aan."""
        n = norm(naam)
        if len(n) < 5:          # te kort om op te matchen zonder ruis
            return []
        return [t for t in txs if n in norm(t["ruwe_tekst"])]

    uit = []
    for rij in rijen:
        gevonden, hoe = los_op(rij)
        tx = [t for it in gevonden for t in per_item.get(it["id"], [])]
        los = [t for t in vrije_tekst(rij["naam"]) if t not in tx]
        echte = [t for t in tx if t["is_dubbel"] not in ("True", "t", "true", "1")]
        aantal = getal(rij["aantal"])
        types = sorted({t["type"] for t in echte})
        datums = sorted(t["datum"] for t in echte if t["datum"])
        bedragen = [getal(t["bedrag"]) for t in echte]

        if echte:
            oordeel = (f"verkocht/geruild — {len(echte)} transactie(s), "
                       f"laatste {datums[-1] if datums else '?'}")
        elif los:
            oordeel = (f"mogelijk verkocht — {len(los)} regel(s) met deze naam in "
                       f"de vrije invoer, maar niet aan dit item gekoppeld")
        elif aantal > 0:
            oordeel = f"NOOIT VERKOCHT en stond op {aantal:g} — NALOPEN IN DE DOOS"
        else:
            oordeel = "stond al op 0 en nooit verkocht — eerder afgevoerd"

        uit.append({
            **rij,
            "koppeling": hoe,
            "transacties": len(echte),
            "types": "/".join(types),
            "laatste_transactie": datums[-1] if datums else "",
            "omzet": f"{sum(bedragen):.2f}" if bedragen else "",
            "losse_naamtreffers": len(los),
            "conclusie": oordeel,
        })

    # Duurste eerst: daar zit het geld, en daar begin je met nalopen.
    uit.sort(key=lambda r: getal(r["prijs_cm"]), reverse=True)
    velden = list(uit[0].keys())
    with open(UIT_ALLES, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=velden); w.writeheader(); w.writerows(uit)

    nalopen = [r for r in uit if r["oordeel"].startswith("GEEN TEGENHANGER")]
    with open(UIT_NALOPEN, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=velden); w.writeheader(); w.writerows(nalopen)

    print(f"{UIT_ALLES.name}: {len(uit)} regels (alle leeggemaakte slabs)")
    print(f"{UIT_NALOPEN.name}: {len(nalopen)} regels (zonder tegenhanger)")
    return uit, nalopen


if __name__ == "__main__":
    main()
