"""Importeert de v7-inventaris (TC_Inventaris_v7.xlsx) en VERVANGT de items-tabel.

    python src/import_inventory_v3.py [--dry-run] [--bestand PAD]

Waarom een derde importer naast `import_inventory_v2.py`: sinds de beurs hangen er
transacties met een foreign key aan `items` (104 verkopen van Cardmaniacs Nijmegen).
Een kale DELETE+INSERT zoals in v2 zou daarop stuklopen, en zelfs als hij lukte
zouden alle verkopen hun kaartkoppeling kwijtraken — precies wat we nodig hebben om
straks af te boeken. Deze importer *hernummert* daarom: hij zet de nieuwe rijen
erin, verlegt `transactions.item_id`, `price_points.item_id` en
`match_voorstellen.voorgesteld_item_id` naar de nieuwe id's, en ruimt dan pas de
oude rijen op. Alles in één transactie.

Koppelen gebeurt op naam+code. Die sleutel is niet uniek (23 combinaties komen 2x
voor in v7), dus tellen we per sleutel mee de hoeveelste voorkomen het is: de 1e
"Clefairy 094/088" van de oude tabel wordt de 1e van de nieuwe. Zonder die
volgnummering zouden dubbele regels willekeurig op één id samenklappen.

Verder gelijk aan v2: prijzen zijn formules (`data_only=True`), codes als getal
(46.0) worden "46", rijen zonder naam en `[ place holder ]`-regels slaan we over,
en de verrijking (officiele_naam, cardmarket_id, set_code, match_status) nemen we
over uit de oude tabel omdat die niet in het Excel-bestand zit.

Let op: `Aantal` uit v7 is de voorraad *vóór* de beursverkopen zijn afgeboekt.
Draai daarna `src/afboeken_sales.py`. Opnieuw importeren zet die afboeking dus
terug — dat is de prijs van "het Excel-bestand is de waarheid".
"""

from __future__ import annotations

import argparse
import csv
import datetime
import sys
from collections import defaultdict
from pathlib import Path

import openpyxl
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import get_engine, INVENTORY  # noqa: E402

STANDAARD_BESTAND = INVENTORY.parent / "data" / "TC_Inventaris_v7.xlsx"
VERLIES_CSV = INVENTORY.parent / "data" / "transacties_link_verloren.csv"
SHEET = "Inventaris"

# bronkolom -> databasekolom. "Set" en "Laatst bijgewerkt" staan wel in v7 maar
# zijn in het hele bestand leeg; die slaan we over.
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

# tabel, kolom: alles wat met een foreign key naar items wijst
VERWIJZERS = [
    ("transactions", "item_id"),
    ("price_points", "item_id"),
    ("match_voorstellen", "voorgesteld_item_id"),
]


def tekst(waarde) -> str | None:
    """Celwaarde naar nette tekst. Getallen die eigenlijk een code zijn (46.0)
    worden "46" — anders zoekt niemand ze ooit terug.

    Excel maakt van een kaartcode als "9/12" soms een datum. Die schrijven we als
    "2026-12-09" weg: lelijk, maar wél zichtbaar fout, zodat iemand hem repareert.
    Zelf gokken kan niet — 9/12 en 12/9 leveren dezelfde cel op."""
    if waarde is None:
        return None
    if isinstance(waarde, datetime.datetime):
        waarde = waarde.date()
    if isinstance(waarde, datetime.date):
        return waarde.isoformat()
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


# Kaarten die tussen twee versies zijn hernoemd. Zonder deze lijst raken de
# transacties die aan de oude naam hangen hun koppeling kwijt en worden ze nooit
# afgeboekt. Sleutel en waarde zijn allebei (naam, code) in kleine letters.
# Alleen invullen na bevestiging door het team — dit is geen gokwerk.
HERNOEMD = {
    # 17-08-2026, bevestigd door Jishnu: zelfde Sealed-product, beide voorraad 14.
    ("prismatic booster pack", ""): ("prismatic booster", ""),
}


def basissleutel(naam, code) -> tuple[str, str]:
    sl = (str(naam or "").strip().lower(), str(code or "").strip().lower())
    return HERNOEMD.get(sl, sl)


def nummer_sleutels(paren) -> list[tuple[str, str, int]]:
    """(naam, code) -> (naam, code, hoeveelste). Houdt dubbele regels uit elkaar."""
    teller: dict[tuple[str, str], int] = defaultdict(int)
    uit = []
    for naam, code in paren:
        sl = basissleutel(naam, code)
        teller[sl] += 1
        uit.append((*sl, teller[sl]))
    return uit


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


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bestand", type=Path, default=STANDAARD_BESTAND)
    p.add_argument("--dry-run", action="store_true",
                   help="alleen tonen wat er zou gebeuren, niets schrijven")
    args = p.parse_args()

    if not args.bestand.exists():
        sys.exit(f"Bestand niet gevonden: {args.bestand}")

    nieuw, overgeslagen = lees_bestand(args.bestand)
    print(f"Gelezen uit {args.bestand.name}: {len(nieuw)} items "
          f"(overgeslagen: {overgeslagen})")

    nieuwe_sleutels = nummer_sleutels([(r["onze_naam"], r["code"]) for r in nieuw])

    engine = get_engine()
    with engine.begin() as conn:
        oud = conn.execute(text(
            "SELECT id, onze_naam, code, officiele_naam, cardmarket_id, set_code, "
            "match_status FROM items ORDER BY bron_rij NULLS LAST, id")).mappings().all()
        oude_sleutels = nummer_sleutels([(r["onze_naam"], r["code"]) for r in oud])
        oud_per_sleutel = {sl: r for sl, r in zip(oude_sleutels, oud)}

        # 1. verrijking overnemen
        verrijkt = 0
        for item, sl in zip(nieuw, nieuwe_sleutels):
            bron = oud_per_sleutel.get(sl)
            if bron:
                verrijkt += 1
            for veld in ("officiele_naam", "cardmarket_id", "set_code", "match_status"):
                item[veld] = (bron or {}).get(veld)
        print(f"Verrijking overgenomen uit de oude tabel: {verrijkt} van {len(nieuw)}")

        # 2. wat verwijst er naar de oude items, en overleeft dat de vervanging?
        nieuw_per_sleutel = {sl: i for i, sl in enumerate(nieuwe_sleutels)}
        oud_naar_positie = {r["id"]: nieuw_per_sleutel.get(sl)
                            for sl, r in zip(oude_sleutels, oud)}
        oud_info = {r["id"]: r for r in oud}

        verwijzingen = {}
        for tabel, kolom in VERWIJZERS:
            verwijzingen[tabel] = conn.execute(text(
                f"SELECT id, {kolom} AS item_id FROM {tabel} "
                f"WHERE {kolom} IS NOT NULL")).mappings().all()
        verloren = [(tabel, r) for tabel, rs in verwijzingen.items() for r in rs
                    if oud_naar_positie.get(r["item_id"]) is None]

        for tabel, rijen in verwijzingen.items():
            kwijt = sum(1 for r in rijen if oud_naar_positie.get(r["item_id"]) is None)
            print(f"{tabel:<18} verwijst naar items: {len(rijen):>4}  "
                  f"(zonder tegenhanger in v7: {kwijt})")

        if verloren:
            print("\nDeze verwijzingen verliezen hun kaart (item_id wordt NULL):")
            for tabel, r in verloren[:15]:
                info = oud_info.get(r["item_id"], {})
                print(f"   {tabel} #{r['id']} -> was '{info.get('onze_naam')}' "
                      f"[{info.get('code')}]")
            if len(verloren) > 15:
                print(f"   ... en {len(verloren) - 15} meer")

        if args.dry_run:
            print("\n--dry-run: niets weggeschreven. Voorbeeld van de eerste 3 items:")
            for item in nieuw[:3]:
                print("   ", item)
            raise SystemExit(0)

        # 3. nieuwe rijen erbij (oude staan er nog: de FK's wijzen er nog naar).
        # `bron_rij` is UNIQUE en oud en nieuw gebruiken dezelfde rijnummers, dus
        # nemen we de oude rijen hun nummer af — ze verdwijnen straks toch. NULL
        # mag meerdere keren voorkomen onder een unique constraint.
        oude_ids = [r["id"] for r in oud]
        conn.execute(text("UPDATE items SET bron_rij = NULL WHERE bron_rij IS NOT NULL"))
        conn.execute(INSERT, nieuw)
        # RETURNING levert bij een executemany geen rijen op; de nieuwe id's zijn
        # daarom op te halen via bron_rij, die nu alleen de nieuwe rijen nog hebben.
        per_bron_rij = dict(conn.execute(text(
            "SELECT bron_rij, id FROM items WHERE bron_rij IS NOT NULL")).all())
        nieuwe_ids = [per_bron_rij[r["bron_rij"]] for r in nieuw]
        if len(set(nieuwe_ids)) != len(nieuw):
            raise SystemExit("Onverwacht: niet elke nieuwe rij heeft een eigen id.")

        # 4. verwijzingen verleggen naar de nieuwe id's
        hernummerd = {}
        for tabel, kolom in VERWIJZERS:
            paren = [{"oud": r["item_id"],
                      "nieuw": (nieuwe_ids[oud_naar_positie[r["item_id"]]]
                                if oud_naar_positie.get(r["item_id"]) is not None
                                else None),
                      "rij": r["id"]}
                     for r in verwijzingen[tabel]]
            if paren:
                conn.execute(text(
                    f"UPDATE {tabel} SET {kolom} = :nieuw WHERE id = :rij"), paren)
            hernummerd[tabel] = sum(1 for p in paren if p["nieuw"] is not None)

        # 5. pas nu mogen de oude rijen weg
        weg = conn.execute(text("DELETE FROM items WHERE id = ANY(:ids)"),
                           {"ids": oude_ids}).rowcount
        totaal = conn.execute(text("SELECT count(*) FROM items")).scalar()

    for tabel, aantal in hernummerd.items():
        print(f"{tabel:<18} hernummerd naar nieuwe item-id's: {aantal}")
    print(f"oude items verwijderd          : {weg}")
    print(f"items in de tabel              : {totaal}")

    if verloren:
        with VERLIES_CSV.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["tabel", "rij_id", "oud_item_id", "onze_naam", "code"])
            for tabel, r in verloren:
                info = oud_info.get(r["item_id"], {})
                w.writerow([tabel, r["id"], r["item_id"],
                            info.get("onze_naam"), info.get("code")])
        print(f"verbroken koppelingen genoteerd: {VERLIES_CSV}")


if __name__ == "__main__":
    main()
