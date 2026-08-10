"""Import 1 — inventaris.

Leest data/inventaris_verrijkt_v2.csv (verrijkte identiteit) en vult per rij aan
met aantal/prijs/promo/staat/grade/notitie uit TC_Inventaris_v5.xlsx. Beide
bestanden zijn 1-op-1 op rijvolgorde uitgelijnd (v2 is uit de xlsx afgeleid);
we koppelen op positie en verifiëren dat naam+code overeenkomen — robuust tegen
de 8 dubbele naam+code-combinaties. Idempotent via upsert op items.bron_rij.
"""

import csv
import sys

import openpyxl
from sqlalchemy import text

from db import get_engine, INVENTORY

ROOT = INVENTORY.parent
V2_CSV = ROOT / "data" / "inventaris_verrijkt_v2.csv"
XLSX = ROOT / "data" / "TC_Inventaris_v5.xlsx"

UPSERT = text("""
INSERT INTO items (
    bron_rij, onze_naam, officiele_naam, cardmarket_id, code, set_code,
    expansion, taal, categorie, promo, staat, grade, aantal, prijs_cm,
    comp_prijs, match_status, notitie
) VALUES (
    :bron_rij, :onze_naam, :officiele_naam, :cardmarket_id, :code, :set_code,
    :expansion, :taal, :categorie, :promo, :staat, :grade, :aantal, :prijs_cm,
    :comp_prijs, :match_status, :notitie
)
ON CONFLICT (bron_rij) DO UPDATE SET
    onze_naam=EXCLUDED.onze_naam, officiele_naam=EXCLUDED.officiele_naam,
    cardmarket_id=EXCLUDED.cardmarket_id, code=EXCLUDED.code,
    set_code=EXCLUDED.set_code, expansion=EXCLUDED.expansion,
    taal=EXCLUDED.taal, categorie=EXCLUDED.categorie, promo=EXCLUDED.promo,
    staat=EXCLUDED.staat, grade=EXCLUDED.grade, aantal=EXCLUDED.aantal,
    prijs_cm=EXCLUDED.prijs_cm, comp_prijs=EXCLUDED.comp_prijs,
    match_status=EXCLUDED.match_status, notitie=EXCLUDED.notitie,
    updated_at=now()
""")


def s(v):
    """text of NULL"""
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def num(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def as_int(v, default=None):
    if v in (None, ""):
        return default
    try:
        return int(float(str(v).replace(",", ".")))
    except ValueError:
        return default


def main():
    with open(V2_CSV, encoding="utf-8-sig") as f:
        v2 = list(csv.DictReader(f))
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    xl = [r for r in wb["Inventaris"].iter_rows(min_row=2, values_only=True) if r[0]]

    if len(v2) != len(xl):
        sys.exit(f"FOUT: v2 heeft {len(v2)} rijen, xlsx {len(xl)} — niet uitgelijnd, gestopt.")

    params = []
    for i, (a, b) in enumerate(zip(v2, xl), start=1):
        an = (a["onze_naam"].strip(), a["onze_code"].strip())
        bn = (str(b[0]).strip(), str(b[1]).strip() if b[1] else "")
        if an != bn:
            sys.exit(f"FOUT: rij {i} komt niet overeen: CSV {an} vs xlsx {bn} — gestopt.")
        # xlsx kolommen: 2 Aantal, 3 Prijs cm, 4 Comp prijs, 9 Promo, 10 Staat,
        # 11 Grading, 13 Notitie
        params.append({
            "bron_rij": i,
            "onze_naam": a["onze_naam"].strip(),
            "officiele_naam": s(a["officiele_naam"]),
            "cardmarket_id": as_int(a["cardmarket_id"]),
            "code": s(a["onze_code"]),
            "set_code": s(a["set_code"]),
            "expansion": s(a["expansion"]),
            "taal": s(a["onze_taal"]),
            "categorie": s(a["onze_categorie"]),
            "promo": s(b[9]),
            "staat": s(b[10]),
            "grade": s(b[11]),
            "aantal": as_int(b[2], default=1),
            "prijs_cm": num(b[3]),
            "comp_prijs": num(b[4]),
            "match_status": s(a["match_status"]),
            "notitie": s(b[13]),
        })

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(UPSERT, params)
        rows = conn.execute(text(
            "SELECT match_status, count(*) FROM items GROUP BY match_status ORDER BY 2 DESC"
        )).all()
        totaal = conn.execute(text("SELECT count(*) FROM items")).scalar()

    print(f"Items geïmporteerd/bijgewerkt: {len(params)}")
    print(f"Totaal in items-tabel: {totaal}")
    print("Verdeling over match_status:")
    for status, n in rows:
        print(f"  {status:<16} {n}")


if __name__ == "__main__":
    main()
