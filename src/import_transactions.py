"""Import 2 — beurstransacties.

Leest inventory/data/transacties_beurs_2026-07_clean.csv, maakt (idempotent) het
event "Beurs 25-26 juli 2026" en koppelt alle transacties eraan. item_id blijft
NULL (foto-koppeling komt later). Idempotent via upsert op (event_id, bron_rij).

kanaal-mapping: de bron kent twee WhatsApp-kanalen ('inkoop' en 'sales'). 'sales'
is het verkoop-kanaal → kanaal='verkoop' (de referentie "verkoop €16.768" is de
som van dit kanaal). 'trade' in de enum blijft beschikbaar voor later. De aard van
een losse regel (verkoop/trade) staat in de kolom type.
"""

import csv
from datetime import datetime

from sqlalchemy import text

from db import get_engine, INVENTORY

CSV_PATH = INVENTORY / "data" / "transacties_beurs_2026-07_clean.csv"

EVENT_NAME = "Beurs 25-26 juli 2026"
KANAAL_MAP = {"sales": "verkoop", "inkoop": "inkoop"}

UPSERT_EVENT = text("""
INSERT INTO events (name, event_date, location, type)
VALUES (:name, :event_date, :location, :type)
ON CONFLICT (name) DO UPDATE SET
    event_date=EXCLUDED.event_date, location=EXCLUDED.location, type=EXCLUDED.type
RETURNING id
""")

UPSERT_TX = text("""
INSERT INTO transactions (
    event_id, item_id, bron_rij, datum, tijd, kanaal, type, bedrag,
    afzender, media_file, flag, ruwe_tekst
) VALUES (
    :event_id, NULL, :bron_rij, :datum, :tijd, :kanaal, :type, :bedrag,
    :afzender, :media_file, :flag, :ruwe_tekst
)
ON CONFLICT (event_id, bron_rij) DO UPDATE SET
    datum=EXCLUDED.datum, tijd=EXCLUDED.tijd, kanaal=EXCLUDED.kanaal,
    type=EXCLUDED.type, bedrag=EXCLUDED.bedrag, afzender=EXCLUDED.afzender,
    media_file=EXCLUDED.media_file, flag=EXCLUDED.flag, ruwe_tekst=EXCLUDED.ruwe_tekst
""")


def s(v):
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def main():
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    params = []
    for i, r in enumerate(rows, start=1):
        kanaal = KANAAL_MAP.get(r["kanaal"].strip())
        if kanaal is None:
            raise SystemExit(f"FOUT: onbekend kanaal {r['kanaal']!r} op rij {i}")
        params.append({
            "bron_rij": i,
            "datum": datetime.strptime(r["datum"].strip(), "%d-%m-%Y").date(),
            "tijd": r["tijd"].strip() or None,
            "kanaal": kanaal,
            "type": r["type"].strip(),
            "bedrag": float(r["bedrag"]) if r["bedrag"].strip() else None,
            "afzender": s(r["afzender"]),
            "media_file": s(r["media_file"]),
            "flag": s(r["flag"]),
            "ruwe_tekst": s(r["ruwe_tekst"]),
        })

    engine = get_engine()
    with engine.begin() as conn:
        event_id = conn.execute(UPSERT_EVENT, {
            "name": EVENT_NAME,
            "event_date": datetime(2026, 7, 25).date(),
            "location": None,
            "type": "beurs",
        }).scalar()
        for p in params:
            p["event_id"] = event_id
        conn.execute(UPSERT_TX, params)

        totaal = conn.execute(text(
            "SELECT count(*) FROM transactions WHERE event_id=:e"), {"e": event_id}).scalar()
        in_som = conn.execute(text(
            "SELECT coalesce(sum(bedrag),0) FROM transactions "
            "WHERE event_id=:e AND type IN ('verkoop','trade')"), {"e": event_id}).scalar()
        uit_som = conn.execute(text(
            "SELECT coalesce(sum(bedrag),0) FROM transactions "
            "WHERE event_id=:e AND type='inkoop'"), {"e": event_id}).scalar()
        check = conn.execute(text(
            "SELECT count(*) FROM transactions WHERE event_id=:e AND flag='CHECK BEDRAG'"),
            {"e": event_id}).scalar()

    REF_VERKOOP, REF_INKOOP = 16768.0, 3619.0
    print(f"Event id={event_id}: {EVENT_NAME}")
    print(f"Transacties geïmporteerd/bijgewerkt: {len(params)} (in db: {totaal})")
    print(f"Verkoop+trade in : €{float(in_som):.2f}  (referentie €{REF_VERKOOP:.2f}, "
          f"verschil €{REF_VERKOOP - float(in_som):.2f} — sticker-verkopen zonder bedrag)")
    print(f"Inkoop uit       : €{float(uit_som):.2f}  (referentie €{REF_INKOOP:.2f}, "
          f"verschil €{REF_INKOOP - float(uit_som):.2f})")
    print(f"Netto            : €{float(in_som) - float(uit_som):.2f}")
    print(f"Regels met flag CHECK BEDRAG: {check}")


if __name__ == "__main__":
    main()
