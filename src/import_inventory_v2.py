"""Importeert de v6-inventaris (inventaris_datav2.xlsx) en VERVANGT de items-tabel.

    python src/import_inventory_v2.py [--dry-run] [--bestand PAD]

Waarom een tweede importer naast `import_inventory.py`: het v6-bestand heeft een
ander kolommenschema (Vorig aantal, Verkocht, Status, Vintage/Modern) en het is
een *vervanging* in plaats van een aanvulling. De oude importer blijft staan voor
het v5-bestand.

Aandachtspunten in de bron:
- Prijskolommen zijn formules; we lezen de berekende waardes (data_only=True).
  Draai het bestand dus één keer door Excel/LibreOffice als de formules nog niet
  zijn doorgerekend, anders staan er lege prijzen in.
- Codes kunnen als getal zijn opgeslagen (46.0) → genormaliseerd naar "46".
- Rijen zonder naam en placeholder-regels ("[ booset pack place holder ]") slaan
  we over; dat zijn invulhulpjes, geen voorraad.
- Verrijking uit de oude tabel (cardmarket_id, officiele_naam, set_code,
  match_status) nemen we mee waar naam+code overeenkomen — die was met de hand
  en via de Cardmarket-API opgebouwd en zit niet in het Excel-bestand.

De hele vervanging draait in één transactie: gaat er iets mis, dan verandert er
niets. `match_voorstellen.voorgesteld_item_id` verwijst met een foreign key naar
items; die koppeling wordt losgezet (de voorstelregels zelf blijven).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import openpyxl
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import get_engine, INVENTORY  # noqa: E402

STANDAARD_BESTAND = INVENTORY.parent / "data" / "inventaris_datav2.xlsx"
SHEET = "Inventaris"

# bronkolom -> databasekolom
KOLOMMEN = {
    "Naam": "onze_naam",
    "Code": "code",
    "Taal": "taal",
    "Categorie": "categorie",
    "Promo": "promo",
    "Vintage/Modern": "vintage_modern",
    "Staat": "staat",
    "Grading": "grade",
    "Aantal": "aantal",
    "Vorig aantal": "vorig_aantal",
    "Verkocht": "verkocht",
    "Prijs cm/ebay": "prijs_cm",
    "Comp prijs": "comp_prijs",
    "Status": "status",
    "Notitie": "notitie",
}
GEHEEL = {"aantal", "vorig_aantal", "verkocht"}
GETAL = {"prijs_cm", "comp_prijs"}

INSERT = text("""
INSERT INTO items (
    bron_rij, onze_naam, officiele_naam, cardmarket_id, code, set_code, taal,
    categorie, promo, vintage_modern, staat, grade, aantal, vorig_aantal,
    verkocht, prijs_cm, comp_prijs, status, match_status, notitie
) VALUES (
    :bron_rij, :onze_naam, :officiele_naam, :cardmarket_id, :code, :set_code, :taal,
    :categorie, :promo, :vintage_modern, :staat, :grade, :aantal, :vorig_aantal,
    :verkocht, :prijs_cm, :comp_prijs, :status, :match_status, :notitie
)
""")


def tekst(waarde) -> str | None:
    """Celwaarde naar nette tekst. Getallen die eigenlijk een code zijn (46.0)
    worden "46" — anders zoekt niemand ze ooit terug."""
    if waarde is None:
        return None
    if isinstance(waarde, float) and waarde.is_integer():
        waarde = int(waarde)
    schoon = str(waarde).strip()
    return schoon or None


def geheel(waarde) -> int | None:
    try:
        return int(round(float(waarde)))
    except (TypeError, ValueError):
        return None


def getal(waarde) -> float | None:
    try:
        return float(waarde)
    except (TypeError, ValueError):
        return None


def sleutel(naam, code) -> tuple[str, str]:
    """Matchsleutel voor het overnemen van verrijking: naam + code, hoofdletter-
    en spatie-ongevoelig."""
    return (str(naam or "").strip().lower(), str(code or "").strip().lower())


def lees_bestand(pad: Path) -> tuple[list[dict], dict[str, int]]:
    blad = openpyxl.load_workbook(pad, data_only=True)[SHEET]
    kop = [cel.value for cel in blad[1]]
    ontbreekt = [k for k in KOLOMMEN if k not in kop]
    if ontbreekt:
        sys.exit(f"Kolommen ontbreken in {pad.name}: {ontbreekt}")

    rijen, overgeslagen = [], {"leeg": 0, "zonder_naam": 0, "placeholder": 0}
    for nummer, waarden in enumerate(blad.iter_rows(min_row=2, values_only=True), start=2):
        rij = dict(zip(kop, waarden))
        if all(v in (None, "") for v in waarden):
            overgeslagen["leeg"] += 1
            continue
        naam = tekst(rij.get("Naam"))
        if not naam:
            overgeslagen["zonder_naam"] += 1
            continue
        if "place holder" in naam.lower() or "placeholder" in naam.lower():
            overgeslagen["placeholder"] += 1
            continue

        item = {"bron_rij": nummer}
        for bron, kolom in KOLOMMEN.items():
            waarde = rij.get(bron)
            if kolom in GEHEEL:
                item[kolom] = geheel(waarde)
            elif kolom in GETAL:
                item[kolom] = getal(waarde)
            else:
                item[kolom] = tekst(waarde)
        # `aantal` is NOT NULL in de database. Uitverkochte regels hebben in het
        # bestand een lege cel; dat is gewoon 0 op voorraad.
        if item["aantal"] is None:
            item["aantal"] = 0
        rijen.append(item)
    return rijen, overgeslagen


def bestaande_verrijking(conn) -> dict[tuple[str, str], dict]:
    """Wat de oude tabel wist en het Excel-bestand niet: de identiteit."""
    oud = conn.execute(text(
        "SELECT onze_naam, code, officiele_naam, cardmarket_id, set_code, match_status "
        "FROM items")).mappings().all()
    return {sleutel(r["onze_naam"], r["code"]): dict(r) for r in oud}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bestand", type=Path, default=STANDAARD_BESTAND)
    p.add_argument("--dry-run", action="store_true",
                   help="alleen tonen wat er zou gebeuren, niets schrijven")
    args = p.parse_args()

    if not args.bestand.exists():
        sys.exit(f"Bestand niet gevonden: {args.bestand}")

    rijen, overgeslagen = lees_bestand(args.bestand)
    print(f"Gelezen uit {args.bestand.name}: {len(rijen)} items "
          f"(overgeslagen: {overgeslagen})")

    engine = get_engine()
    with engine.begin() as conn:
        oud = bestaande_verrijking(conn)
        verrijkt = 0
        for item in rijen:
            bron = oud.get(sleutel(item["onze_naam"], item["code"]))
            if bron:
                verrijkt += 1
            item["officiele_naam"] = (bron or {}).get("officiele_naam")
            item["cardmarket_id"] = (bron or {}).get("cardmarket_id")
            item["set_code"] = (bron or {}).get("set_code")
            item["match_status"] = (bron or {}).get("match_status")
        print(f"Verrijking overgenomen uit de oude tabel: {verrijkt} van {len(rijen)}")

        if args.dry_run:
            print("\n--dry-run: niets weggeschreven. Voorbeeld van de eerste 3 items:")
            for item in rijen[:3]:
                print("   ", item)
            raise SystemExit(0)

        # match_voorstellen wijst met een FK naar items; de voorstellen zelf
        # blijven staan, alleen de (na vervanging betekenisloze) koppeling gaat los.
        los = conn.execute(text(
            "UPDATE match_voorstellen SET voorgesteld_item_id = NULL "
            "WHERE voorgesteld_item_id IS NOT NULL")).rowcount
        weg = conn.execute(text("DELETE FROM items")).rowcount
        conn.execute(INSERT, rijen)
        nieuw = conn.execute(text("SELECT count(*) FROM items")).scalar()

    print(f"match_voorstellen losgekoppeld : {los}")
    print(f"oude items verwijderd          : {weg}")
    print(f"nieuwe items geïmporteerd      : {nieuw}")


if __name__ == "__main__":
    main()
