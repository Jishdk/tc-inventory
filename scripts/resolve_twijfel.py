#!/usr/bin/env python3
"""Tweede pass op inventaris_verrijkt.csv: twijfelgevallen verkleinen zonder
nieuwe API-calls, met de prijzen die al in _cmapi_rapid_cache.json staan.

Als "trend"-prijs wordt 30d_average gebruikt (de API heeft geen los trend-veld;
30d_average bleek in de prijstest stabieler dan de laagste listing).

Categorieën:
  A  geen enkele kandidaat-naam lijkt op de onze  -> geen_match_naam
  B  precies één kandidaat met echt id (rij mét code) -> zeker
  C  meerdere kandidaten, zelfde kaartnaam -> prijs-tiebreak -> zeker_prijs
  D  rest -> blijft twijfel (handmatig; ook alle rijen zonder code, want
     zonder code is 'zeker' per projectafspraak niet mogelijk)

    python3 inventory/scripts/resolve_twijfel.py
"""

import csv
import json
import re
from collections import Counter
from pathlib import Path

from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parent.parent.parent
IN_CSV = ROOT / "data" / "inventaris_verrijkt.csv"
CACHE_FILE = ROOT / "data" / "_cmapi_rapid_cache.json"
OUT_CSV = ROOT / "data" / "inventaris_verrijkt_v2.csv"
OUT_HANDMATIG = ROOT / "data" / "twijfel_handmatig.csv"

NAAM_DREMPEL = 60


def build_index():
    """cardmarket_id -> (item, trendprijs) uit alle gecachete zoekresultaten."""
    cache = json.loads(CACHE_FILE.read_text())
    by_id = {}
    for items in cache.values():
        for it in items or []:
            cid = it.get("cardmarket_id")
            if cid is None:
                continue
            cm = (it.get("prices") or {}).get("cardmarket") or {}
            by_id[str(cid)] = (it, cm.get("30d_average"))
    return by_id


def kern(naam):
    """Naam zonder trailing nummer/code ('Pikachu 25' -> 'Pikachu')."""
    return re.sub(r"[\s\-]+[\d/#][\w/]*$", "", naam).strip() or naam


def parse_kandidaten(s):
    out = []
    for part in s.split(";"):
        fields = part.strip().split("|")
        if len(fields) >= 3:
            out.append({"id": fields[0].strip(), "naam": fields[1].strip(),
                        "set": fields[2].strip()})
    return out


def naam_lijkt(onze_naam, kand_naam):
    return fuzz.token_set_ratio(kern(onze_naam).lower(), kand_naam.lower()) >= NAAM_DREMPEL


def vul_in(row, cid, by_id):
    it, _ = by_id[cid]
    row["cardmarket_id"] = cid
    row["officiele_naam"] = it.get("name", "")
    row["set_code"] = it.get("card_code_number", "")
    row["expansion"] = (it.get("episode") or {}).get("name", "")


def main():
    by_id = build_index()
    with open(IN_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    cat_count = Counter()
    handmatig = []

    for row in rows:
        if row["match_status"] != "twijfel":
            continue
        kands = parse_kandidaten(row["kandidaten"])
        if not kands:
            cat_count["D"] += 1
            handmatig.append(row)
            continue

        # A: geen enkele kandidaat-naam lijkt op de onze
        if not any(naam_lijkt(row["onze_naam"], k["naam"]) for k in kands):
            row["match_status"] = "geen_match_naam"
            cat_count["A"] += 1
            continue

        echte = [k for k in kands if k["id"] not in ("", "None")]

        # zonder code geen automatische 'zeker' (projectafspraak)
        if not row["onze_code"]:
            cat_count["D"] += 1
            handmatig.append(row)
            continue

        # B: precies één echte kandidaat
        if len(echte) == 1:
            row["match_status"] = "zeker"
            vul_in(row, echte[0]["id"], by_id)
            cat_count["B"] += 1
            continue

        # C: meerdere echte kandidaten met identieke kaartnaam -> prijs-tiebreak
        namen = {k["naam"].casefold() for k in echte}
        prijs_ok = row["onze_prijs"] not in ("", None)
        if len(echte) > 1 and len(namen) == 1 and prijs_ok:
            onze_prijs = float(row["onze_prijs"])
            trends = [(k, by_id.get(k["id"], (None, None))[1]) for k in echte]
            if all(t is not None for _, t in trends):
                afstanden = sorted(((abs(t - onze_prijs), k, t) for k, t in trends),
                                   key=lambda x: x[0])
                d1, winnaar, _t1 = afstanden[0]
                d2 = afstanden[1][0]
                if d1 <= 0.4 * onze_prijs and (d2 >= 1.5 * d1 and not (d1 == 0 and d2 == 0)):
                    row["match_status"] = "zeker_prijs"
                    vul_in(row, winnaar["id"], by_id)
                    cat_count["C"] += 1
                    continue
            # geen prijs beschikbaar of geen duidelijke winnaar -> D

        cat_count["D"] += 1
        handmatig.append(row)

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    def prijs_sleutel(r):
        try:
            return float(r["onze_prijs"])
        except (TypeError, ValueError):
            return 0.0

    handmatig.sort(key=prijs_sleutel, reverse=True)
    with open(OUT_HANDMATIG, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["onze_naam", "onze_code", "onze_prijs", "kandidaten"])
        for r in handmatig:
            kands = parse_kandidaten(r["kandidaten"])
            met_trend = "; ".join(
                f"{k['id']}|{k['naam']}|{k['set']}|"
                f"{by_id.get(k['id'], (None, None))[1] if k['id'] not in ('', 'None') else ''}"
                for k in kands)
            w.writerow([r["onze_naam"], r["onze_code"], r["onze_prijs"], met_trend])

    print("Tweede pass twijfelgevallen (trend = 30d_average uit cache, 0 API-calls)")
    print(f"  A naam-mismatch -> geen_match_naam : {cat_count['A']}")
    print(f"  B één echte kandidaat -> zeker     : {cat_count['B']}")
    print(f"  C prijs-tiebreak -> zeker_prijs    : {cat_count['C']}")
    print(f"  D blijft twijfel (handmatig)       : {cat_count['D']}")
    print("\nNieuwe statusverdeling:")
    for st, n in Counter(r["match_status"] for r in rows).most_common():
        print(f"  {st:<16} {n}")
    print(f"\nV2:        {OUT_CSV}")
    print(f"Handmatig: {OUT_HANDMATIG} ({len(handmatig)} rijen)")


if __name__ == "__main__":
    main()
