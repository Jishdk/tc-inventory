"""Boekt beursverkopen af tegen de voorraad: aantal omlaag, verkocht omhoog.

    python src/afboeken_sales.py [--event 3] [--dry-run] [--uitvoeren]

Standaard draait dit script een **dry-run**: je ziet wat er zou gebeuren en er
verandert niets. Pas met `--uitvoeren` gaat het echt.

Wat het doet:
- elke verkoop met een `item_id` telt voor `stuks` kaarten, en `stuks` is bijna
  altijd NULL = 1 (de beurs-app kent geen aantal per regel; twee stuks zijn twee
  regels). Alleen waar het team heeft bevestigd dat één regel meer kaarten dekt
  staat er een getal, zoals de 9x Mew 205/165 die samen voor EUR 205 weggingen;
- de EUR 0,01-regels tellen gewoon mee: dat is de andere kant van diezelfde
  workaround. Ze halen wél een kaart uit de voorraad, maar zijn géén omzet;
- `items.aantal` gaat omlaag, `items.verkocht` omhoog, `status` wordt opnieuw
  bepaald. Er wordt **nooit** een item verwijderd — aantal mag naar 0 of negatief,
  want een negatieve stand is informatie: dan klopt de telling niet en moet er
  iemand naar kijken;
- `vorig_aantal` blijft met rust: dat is de boekhouding van het Excel-bestand zelf.

Wat het overslaat:
- regels met `is_dubbel` (per ongeluk twee keer ingevoerd, tellen niet mee);
- regels die al een `afgeboekt_op` hebben (anders boek je bij een tweede ronde
  dezelfde verkoop nog eens af);
- verkopen zonder `item_id`. Die zijn niet te raden — ze staan in
  `data/niet_gekoppelde_sales.csv` om met de hand te koppelen.

Trades tellen mee voor de voorraad, maar alleen die uit de beurs-app — te herkennen
aan hun `group_id`. Bij zo'n trade staat per uitgaande kaart een eigen regel met
item_id en stuks (bedrag EUR 0; het geld zit in de losse cash-regel van dezelfde
groep, en die heeft geen item_id en valt hier dus vanzelf buiten). Die kaarten zijn
de deur uit en horen van de voorraad af.

Oudere trades uit de WhatsApp-import blijven buiten schot: die hebben geen group_id
en er staat niet vast welke kaart precies is weggegeven, dus daar zou afboeken raden zijn.

Inkopen blijven buiten schot: die verhogen de voorraad, maar dat is al verwerkt in
het Excel-bestand (v7 bevat de nieuwe inkoop). Ze hier nog eens optellen zou dubbel zijn.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import get_engine, INVENTORY  # noqa: E402

NIET_GEKOPPELD = INVENTORY.parent / "data" / "niet_gekoppelde_sales.csv"

TE_BOEKEN = text("""
    SELECT t.id, t.bedrag, t.item_id, t.type, COALESCE(t.stuks, 1) AS stuks,
           i.onze_naam, i.code, i.aantal, i.verkocht, i.status
    FROM transactions t
    JOIN items i ON i.id = t.item_id
    WHERE (t.type = 'verkoop'
           OR (t.type = 'trade' AND t.group_id IS NOT NULL))
      AND t.afgeboekt_op IS NULL
      AND NOT t.is_dubbel
      AND (:event IS NULL OR t.event_id = :event)
    ORDER BY t.datum, t.tijd, t.id
""")

LOSSE = text("""
    SELECT t.id, t.datum, t.tijd, t.type, t.bedrag, t.code, t.flag, t.ruwe_tekst,
           t.afzender, e.name AS event
    FROM transactions t
    JOIN events e ON e.id = t.event_id
    WHERE t.item_id IS NULL
      AND COALESCE(t.flag, '') <> 'trade_cash'
      AND NOT t.is_dubbel
      AND (:event IS NULL OR t.event_id = :event)
    ORDER BY t.type, t.datum, t.tijd
""")


def nieuwe_status(aantal: int, oud: str | None) -> str:
    """Alleen de voorraad bepaalt of iets uitverkocht is. Andere statussen uit het
    Excel-bestand (bijvoorbeeld een reservering) laten we staan zolang er nog wat ligt."""
    if aantal <= 0:
        return "Uitverkocht"
    if oud in (None, "", "Uitverkocht"):
        return "Op voorraad"
    return oud


def schrijf_losse(conn, event) -> int:
    rijen = conn.execute(LOSSE, {"event": event}).mappings().all()
    with NIET_GEKOPPELD.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "datum", "tijd", "type", "bedrag", "code", "flag", "ruwe_tekst",
                    "afzender", "event", "voorgesteld_item_id", "opmerking"])
        for r in rijen:
            w.writerow([r["id"], r["datum"], r["tijd"], r["type"], r["bedrag"],
                        r["code"] or "", r["flag"] or "", r["ruwe_tekst"] or "",
                        r["afzender"] or "", r["event"], "", ""])
    return len(rijen)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--event", type=int, default=3, help="0 = alle events")
    p.add_argument("--uitvoeren", action="store_true",
                   help="echt wegschrijven (zonder deze vlag is het een dry-run)")
    p.add_argument("--dry-run", action="store_true", help="expliciet niets schrijven")
    args = p.parse_args()
    echt = args.uitvoeren and not args.dry_run
    event = args.event or None

    engine = get_engine()
    with engine.begin() as conn:
        rijen = conn.execute(TE_BOEKEN, {"event": event}).mappings().all()

        per_item: dict[int, dict] = {}
        for r in rijen:
            it = per_item.setdefault(r["item_id"], {
                "onze_naam": r["onze_naam"], "code": r["code"], "aantal": r["aantal"],
                "verkocht": r["verkocht"] or 0, "status": r["status"], "n": 0})
            it["n"] += r["stuks"]

        stuks = sum(r["stuks"] for r in rijen)
        uit_trades = [r for r in rijen if r["type"] == "trade"]
        print(f"Af te boeken verkopen : {len(rijen)} regels = {stuks} stuks "
              f"over {len(per_item)} kaarten")
        if uit_trades:
            # Apart genoemd: dit zijn geen verkopen en ze staan dus ook niet in de
            # omzet, terwijl ze wél voorraad kosten.
            print(f"   waarvan uit trades : {len(uit_trades)} regels = "
                  f"{sum(r['stuks'] for r in uit_trades)} stuks (bedrag EUR 0)")
        if not rijen:
            print("Niets te doen.")

        negatief = [i for i in per_item.values() if i["aantal"] - i["n"] < 0]
        print(f"Kaarten die onder nul zakken: {len(negatief)}"
              + ("  (blijven staan, maar de telling klopt daar niet)" if negatief else ""))
        for it in negatief:
            print(f"   {it['onze_naam'][:38]:<38} {str(it['code'] or ''):<12} "
                  f"{it['aantal']} - {it['n']} = {it['aantal'] - it['n']}")

        print("\nVoorbeeld van de afboeking (eerste 10):")
        for it in list(per_item.values())[:10]:
            nieuw = it["aantal"] - it["n"]
            print(f"   {it['onze_naam'][:38]:<38} {str(it['code'] or ''):<12} "
                  f"{it['aantal']:>3} -> {nieuw:<3} (verkocht {it['verkocht']} -> "
                  f"{it['verkocht'] + it['n']}, {nieuwe_status(nieuw, it['status'])})")

        if not echt:
            print("\nDRY-RUN — er is niets weggeschreven. "
                  "Draai met --uitvoeren om het echt te doen.")
            los = schrijf_losse(conn, event)
            print(f"Niet-gekoppelde regels genoteerd: {los} -> {NIET_GEKOPPELD}")
            return

        for item_id, it in per_item.items():
            nieuw = it["aantal"] - it["n"]
            conn.execute(text("""
                UPDATE items SET aantal = :aantal, verkocht = :verkocht,
                                 status = :status, updated_at = now()
                WHERE id = :id"""),
                {"aantal": nieuw, "verkocht": it["verkocht"] + it["n"],
                 "status": nieuwe_status(nieuw, it["status"]), "id": item_id})
        conn.execute(text(
            "UPDATE transactions SET afgeboekt_op = now() WHERE id = ANY(:ids)"),
            {"ids": [r["id"] for r in rijen]})
        los = schrijf_losse(conn, event)

    print(f"\nAfgeboekt: {len(rijen)} regels op {len(per_item)} kaarten "
          f"({len([r for r in rijen if r['type'] == 'trade'])} daarvan uit trades).")
    print(f"Niet-gekoppelde regels genoteerd: {los} -> {NIET_GEKOPPELD}")


if __name__ == "__main__":
    main()
