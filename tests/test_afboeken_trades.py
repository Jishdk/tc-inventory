"""Boekt afboeken_sales.py de juiste regels af nu de app ook trades schrijft?

Draait alleen de selectie-query (TE_BOEKEN / LOSSE) tegen een SQLite-schaduw-
schema. Het schrijvende deel van het script is Postgres-specifiek (now(),
= ANY(:ids)) en blijft hier buiten schot — wat we hier bewaken is *welke* regels
worden opgepakt, en dat is precies wat er met de trades is veranderd.

    python tests/test_afboeken_trades.py
"""

import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, text

INVENTORY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(INVENTORY / "src"))

SCHEMA = """
CREATE TABLE events (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE items (
    id INTEGER PRIMARY KEY, onze_naam TEXT, code TEXT, aantal INTEGER,
    verkocht INTEGER, status TEXT);
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY, event_id INTEGER, item_id INTEGER, datum DATE,
    tijd TIME, type TEXT, bedrag NUMERIC, code TEXT, flag TEXT, ruwe_tekst TEXT,
    afzender TEXT, stuks INTEGER, is_dubbel BOOLEAN NOT NULL DEFAULT 0,
    afgeboekt_op TIMESTAMP, group_id TEXT);
"""

# id, event, item, type, bedrag, flag, stuks, is_dubbel, afgeboekt, group
RIJEN = [
    # gewone verkopen — zoals altijd
    (1, 3, 10, "verkoop", 100.0, None, None, 0, None, None),
    (2, 3, 11, "verkoop", 45.0, "mandje", 2, 0, None, "g1"),
    # dubbel en al afgeboekt: allebei overslaan
    (3, 3, 10, "verkoop", 100.0, None, None, 1, None, None),
    (4, 3, 10, "verkoop", 100.0, None, None, 0, "2026-08-18", None),
    # trade uit de app: twee kaarten eruit + de cash-regel
    (5, 3, 10, "trade", 0.0, None, None, 0, None, "g2"),
    (6, 3, 11, "trade", 0.0, None, 3, 0, None, "g2"),
    (7, 3, None, "trade", 75.0, "trade_cash", 1, 0, None, "g2"),
    # oude trade uit de WhatsApp-import: geen group_id, dus met rust laten
    (8, 1, 10, "trade", 30.0, None, None, 0, None, None),
    # inkoop blijft altijd buiten schot
    (9, 3, 10, "inkoop", 200.0, None, None, 0, None, None),
    # losse verkoop zonder item_id: hoort in de "nog koppelen"-lijst
    (10, 3, None, "verkoop", 60.0, "vrij ingevoerd", None, 0, None, None),
]

fouten = []


def check(voorwaarde, omschrijving):
    print(("  OK   " if voorwaarde else "  FOUT ") + omschrijving)
    if not voorwaarde:
        fouten.append(omschrijving)


def main():
    pad = Path(tempfile.mkdtemp(prefix="tc_afboek_test_")) / "test.db"
    engine = create_engine(f"sqlite:///{pad}")
    with engine.begin() as c:
        for stmt in SCHEMA.strip().split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        c.execute(text("INSERT INTO events (id, name) VALUES (1, 'juli'), (3, 'beurs')"))
        for i in (10, 11):
            c.execute(text("INSERT INTO items (id, onze_naam, code, aantal, verkocht,"
                           " status) VALUES (:id, :n, 'X', 5, 0, 'Op voorraad')"),
                      {"id": i, "n": f"Kaart {i}"})
        for r in RIJEN:
            c.execute(text(
                "INSERT INTO transactions (id, event_id, item_id, datum, tijd, type,"
                " bedrag, flag, stuks, is_dubbel, afgeboekt_op, group_id) VALUES "
                "(:a, :b, :c, '2026-08-19', '12:00:00', :d, :e, :f, :g, :h, :i, :j)"),
                dict(zip("abcdefghij",
                         (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9]))))

    import afboeken_sales as af

    with engine.connect() as c:
        opgepakt = c.execute(af.TE_BOEKEN, {"event": 3}).mappings().all()
        los = c.execute(af.LOSSE, {"event": 3}).mappings().all()

    ids = sorted(r["id"] for r in opgepakt)
    check(ids == [1, 2, 5, 6], f"opgepakt: verkopen én app-trades ({ids})")
    check(3 not in ids and 4 not in ids,
          "dubbele en al afgeboekte regels blijven overgeslagen")
    check(7 not in ids,
          "de cash-regel van een trade heeft geen item_id en valt vanzelf af")
    check(8 not in ids,
          "een oude trade zonder group_id wordt met rust gelaten")
    check(9 not in ids, "inkoop boekt niet af")

    stuks = sum(r["stuks"] for r in opgepakt)
    check(stuks == 1 + 2 + 1 + 3, f"stuks tellen per regel mee (kreeg {stuks})")
    uit_trades = [r for r in opgepakt if r["type"] == "trade"]
    check(len(uit_trades) == 2 and sum(r["stuks"] for r in uit_trades) == 4,
          f"twee traderegels, samen 4 stuks ({[(r['id'], r['stuks']) for r in uit_trades]})")

    los_ids = sorted(r["id"] for r in los)
    check(los_ids == [10],
          f"alleen de echte vrije invoer moet nog gekoppeld ({los_ids})")

    print()
    if fouten:
        print(f"{len(fouten)} FOUT(EN):")
        for f in fouten:
            print("  -", f)
        sys.exit(1)
    print("Alle checks OK")


if __name__ == "__main__":
    main()
