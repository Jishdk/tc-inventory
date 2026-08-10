#!/usr/bin/env python3
"""Verwijder 4 gevalideerde foutregels uit transacties_beurs_2026-07.csv.

Dubbeltellingen (zelfde foto/deal 2x gepost) + 1 chatter-regel, handmatig
gecontroleerd na het beursweekend 25-26 juli 2026. Schrijft het resultaat
naar *_clean.csv; het origineel blijft staan. Stopt zonder te schrijven
als een van de 4 regels niet eenduidig gevonden wordt.
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
IN_CSV = DATA_DIR / "transacties_beurs_2026-07.csv"
OUT_CSV = DATA_DIR / "transacties_beurs_2026-07_clean.csv"

# (omschrijving, matchfunctie) — elke matcher moet precies 1 regel raken
REMOVALS = [
    ("a) dubbel WA0033 (25-07 13:54, bedrag 18)",
     lambda r: r["datum"] == "25-07-2026" and r["tijd"] == "13:54"
     and r["kanaal"] == "sales" and "WA0033" in r["media_file"] and r["bedrag"] == "18"),
    ("b) dubbel WA0030 (26-07 15:57, bedrag 45)",
     lambda r: r["datum"] == "26-07-2026" and r["tijd"] == "15:57"
     and r["kanaal"] == "sales" and "WA0030" in r["media_file"] and r["bedrag"] == "45"),
    ("c) dubbele trade-foto WA0026 (25-07 12:29, 'Rechts erin plus 380')",
     lambda r: r["datum"] == "25-07-2026" and r["tijd"] == "12:29"
     and r["kanaal"] == "sales" and "WA0026" in r["media_file"] and r["bedrag"] == "380"),
    ("d) chatter Kevin (25-07 19:08, '4 eu kaart')",
     lambda r: r["datum"] == "25-07-2026" and r["tijd"] == "19:08"
     and r["kanaal"] == "sales" and r["afzender"].startswith("Kevin")
     and "Fucking heel alsnog die 4 eu kaart" in r["ruwe_tekst"] and r["bedrag"] == "4"),
]


def bedrag(r):
    return float(r["bedrag"]) if r["bedrag"] else 0.0


def main():
    with open(IN_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    to_remove = []
    for label, match in REMOVALS:
        hits = [i for i, r in enumerate(rows) if match(r)]
        if len(hits) != 1:
            sys.exit(f"FOUT: {label} matcht {len(hits)} regels i.p.v. 1 — "
                     f"niets verwijderd, controleer de CSV eerst.")
        to_remove.append((label, hits[0]))

    print("Verwijderde regels:")
    for label, i in to_remove:
        r = rows[i]
        print(f"  {label}")
        print(f"    {r['datum']} {r['tijd']} {r['kanaal']} {r['afzender']} "
              f"type={r['type']} bedrag={r['bedrag'] or '-'} | {r['ruwe_tekst']}")

    removed_idx = {i for _, i in to_remove}
    clean = [r for i, r in enumerate(rows) if i not in removed_idx]

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(clean)

    # Samenvatting clean CSV
    sales_per_dag = defaultdict(float)
    inkoop_som = 0.0
    checks = 0
    for r in clean:
        if r["kanaal"] == "sales":
            sales_per_dag[r["datum"]] += bedrag(r)
        else:
            inkoop_som += bedrag(r)
        if r["flag"] == "CHECK BEDRAG":
            checks += 1
    sales_som = sum(sales_per_dag.values())

    print(f"\nSamenvatting clean CSV ({len(clean)} regels):")
    for dag in sorted(sales_per_dag, key=lambda d: d.split("-")[::-1]):
        print(f"  verkoop+trade {dag}: €{sales_per_dag[dag]:.2f}")
    print(f"  verkoop+trade totaal:    €{sales_som:.2f}")
    print(f"  inkoop totaal:           €{inkoop_som:.2f}")
    print(f"  CHECK BEDRAG-regels:     {checks} (blijven staan)")

    # Controles
    ruwe_sales = sum(bedrag(r) for r in rows if r["kanaal"] == "sales")
    daling = ruwe_sales - sales_som
    status = "OK" if abs(daling - 447) < 0.005 else "AFWIJKING!"
    print(f"\nControle:")
    print(f"  sales-som ruw €{ruwe_sales:.2f} -> clean €{sales_som:.2f}, "
          f"daling €{daling:.2f} (verwacht €447.00) {status}")

    REF_VERKOOP, REF_INKOOP = 16768.0, 3619.0
    print(f"  referentie verkoop €{REF_VERKOOP:.2f} - script €{sales_som:.2f} = "
          f"nog te verklaren via sticker-verkopen: €{REF_VERKOOP - sales_som:.2f}")
    print(f"  referentie inkoop  €{REF_INKOOP:.2f} - script €{inkoop_som:.2f} = "
          f"verschil €{REF_INKOOP - inkoop_som:.2f}")
    print(f"\nClean CSV: {OUT_CSV}")


if __name__ == "__main__":
    main()
