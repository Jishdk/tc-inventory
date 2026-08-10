"""Rookproef voor beurs_app.py — draait de hele Streamlit-app headless
(streamlit.testing) tegen een SQLite-schaduwkopie van het Supabase-schema.

Zo is de invoerflow te testen zonder database-verbinding: kiezen wie je bent,
zoeken, item kiezen, bedrag, VASTLEGGEN, vrij invoeren, en twee invoeren snel
na elkaar.

    python tests/test_beurs_app.py
"""

import datetime
import sqlite3
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, text

# Python 3.12 heeft geen standaard date/time-adapters meer voor SQLite;
# Postgres (de echte database) heeft dit niet nodig.
sqlite3.register_adapter(datetime.date, lambda d: d.isoformat())
sqlite3.register_adapter(datetime.time, lambda t: t.isoformat())

INVENTORY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(INVENTORY))
sys.path.insert(0, str(INVENTORY / "src"))

SCHEMA = """
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
    event_date DATE, location TEXT, type TEXT);
CREATE TABLE items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, onze_naam TEXT NOT NULL,
    officiele_naam TEXT, code TEXT, set_code TEXT, categorie TEXT,
    staat TEXT, grade TEXT, comp_prijs NUMERIC, prijs_cm NUMERIC);
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER, item_id INTEGER,
    bron_rij INTEGER, datum DATE NOT NULL, tijd TIME, kanaal TEXT NOT NULL,
    type TEXT NOT NULL, bedrag NUMERIC, afzender TEXT, media_file TEXT,
    flag TEXT, ruwe_tekst TEXT);
"""

# Zoals de echte data: lege set_code komt voor (187 van 572) en namen zijn dubbel (54x).
ITEMS = [
    # onze_naam, officiele_naam, code, set_code, categorie, staat, grade, comp_prijs
    ("Umbreon VMAX", "Umbreon VMAX (Alternate Art)", "215", "EVS 215", "single",
     "NM", None, 450.00),
    ("Umbreon V", "Umbreon V", "189", "EVS 189", "single", "NM", None, 45.00),
    ("Charizard ex", "Charizard ex (Special Illustration)", "223", "OBF 223", "single",
     "NM", None, 120.00),
    ("Pikachu Promo", "Pikachu (Pokémon Center)", "SWSH058", "PR SWSH058", "promo",
     "NM", None, 12.50),
    # gelijknamig paar zonder set_code, alleen te scheiden op staat/grade
    ("Blastoise slab", "Blastoise", None, None, "graded", None, "PSA 9", 300.00),
    ("Blastoise los", "Blastoise", None, None, "single", "GD", None, 80.00),
]

fouten = []


def check(voorwaarde, omschrijving):
    print(("  OK   " if voorwaarde else "  FOUT ") + omschrijving)
    if not voorwaarde:
        fouten.append(omschrijving)


def maak_engine():
    # Bestand, geen :memory: — Streamlit draait het script in een eigen thread.
    pad = Path(tempfile.mkdtemp(prefix="tc_beurs_test_")) / "test.db"
    engine = create_engine(f"sqlite:///{pad}",
                           connect_args={"check_same_thread": False})
    with engine.begin() as c:
        for stmt in SCHEMA.strip().split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        for onze, off, code, setc, cat, staat, grade, prijs in ITEMS:
            c.execute(text(
                "INSERT INTO items (onze_naam, officiele_naam, code, set_code, "
                "categorie, staat, grade, comp_prijs) "
                "VALUES (:a,:b,:c,:d,:e,:f,:g,:h)"),
                {"a": onze, "b": off, "c": code, "d": setc, "e": cat, "f": staat,
                 "g": grade, "h": prijs})
    return engine


def rijen(engine):
    with engine.connect() as c:
        return c.execute(text(
            "SELECT id, event_id, item_id, datum, tijd, kanaal, type, bedrag, "
            "afzender, flag, ruwe_tekst FROM transactions ORDER BY id")).mappings().all()


def main():
    engine = maak_engine()

    import db
    db.get_engine = lambda: engine

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(INVENTORY / "beurs_app.py"), default_timeout=60)
    at.run()
    check(not at.exception, "app start zonder exception")

    # --- geen naamkeuze meer: meteen invoeren -----------------------------
    check("wie" not in at.session_state, "geen 'wie'-sessiestatus meer")
    check(not [b for b in at.button if b.key and b.key.startswith("wie_")],
          "geen naamkeuze-knoppen meer")
    check(bool(at.text_input(key="zoekterm")), "zoekbalk staat er direct bij het openen")

    # --- event aangemaakt -------------------------------------------------
    with engine.connect() as c:
        ev = c.execute(text("SELECT id, name, event_date, type FROM events")).fetchall()
    check(len(ev) == 1 and ev[0][1] == "Cardmaniacs Nijmegen 15-16 augustus 2026",
          f"event aangemaakt: {ev}")

    # --- zoeken + kiezen --------------------------------------------------
    at.text_input(key="zoekterm").set_value("umbreon").run()
    picks = [b for b in at.button if b.key and b.key.startswith("pick_")]
    check(len(picks) == 2, f"zoeken 'umbreon' geeft 2 treffers (kreeg {len(picks)})")
    check(any("450.00" in b.label for b in picks), "comp_prijs zichtbaar in resultaat")
    check(any("EVS" in b.label for b in picks), "set_code zichtbaar in resultaat")

    at.text_input(key="zoekterm").set_value("umbreon vmax").run()
    picks = [b for b in at.button if b.key and b.key.startswith("pick_")]
    check(len(picks) == 1, f"meerdere woorden filteren scherper (kreeg {len(picks)})")

    at.text_input(key="zoekterm").set_value("pokemon center").run()
    picks = [b for b in at.button if b.key and b.key.startswith("pick_")]
    check(len(picks) == 1, "accent-ongevoelig zoeken (pokemon → Pokémon)")

    # Gelijknamige kaarten zonder set_code: alleen te scheiden op staat/grade.
    at.text_input(key="zoekterm").set_value("blastoise").run()
    labels = [b.label for b in at.button if b.key and b.key.startswith("pick_")]
    check(not any("nan" in lb.lower() for lb in labels),
          f"geen 'nan' in resultaatregels bij lege set_code ({labels})")
    check(any("PSA 9" in lb for lb in labels) and any("GD" in lb for lb in labels),
          f"staat/grade scheidt gelijknamige kaarten ({labels})")
    check(labels and "300.00" in labels[0],
          f"duurste variant staat bovenaan bij gelijke naam ({labels})")

    at.text_input(key="zoekterm").set_value("umbreon vmax").run()
    at.button(key=[b.key for b in at.button if b.key.startswith("pick_")][0]).click().run()
    check(at.session_state["sel"]["naam"] == "Umbreon VMAX (Alternate Art)",
          "item geselecteerd")
    check(at.number_input(key="bedrag").value == 450.00,
          f"bedrag voorgevuld met comp_prijs (kreeg {at.number_input(key='bedrag').value})")

    # --- vastleggen (verkoop, overschreven bedrag) ------------------------
    at.number_input(key="bedrag").set_value(430.0).run()
    at.button(key="vastleggen").click().run()
    check(not at.exception, "geen exception bij vastleggen")
    r = rijen(engine)
    check(len(r) == 1, f"1 transactie weggeschreven (kreeg {len(r)})")
    if r:
        t = r[0]
        check(t["kanaal"] == "verkoop" and t["type"] == "verkoop", "kanaal/type = verkoop")
        check(float(t["bedrag"]) == 430.0, "overschreven bedrag opgeslagen")
        check(t["afzender"] == "TC", "afzender vast op 'TC'")
        check(t["item_id"] is not None, "item_id gekoppeld")
        check(t["ruwe_tekst"] == "Umbreon VMAX (Alternate Art)", "ruwe_tekst = officiële naam")
        check(t["event_id"] == ev[0][0], "gekoppeld aan het beurs-event")
        check(t["datum"] is not None and t["tijd"] is not None, "datum + tijd gevuld")

    # --- reset + bevestiging ---------------------------------------------
    check(at.session_state["sel"] is None, "formulier gereset na vastleggen")
    check(at.session_state["bedrag"] == 0.0, "bedrag gereset")
    bev = at.session_state["bevestiging"]
    check(bev == "✓ Umbreon VMAX (Alternate Art) — €430.00", f"korte bevestiging: {bev}")
    check(any("tc-ok" in m.value and bev in m.value for m in at.markdown),
          "bevestiging staat als vervagend blok op het scherm")

    # --- tweede invoer direct erna (verkoop → inkoop) ---------------------
    at.session_state["modus"] = "INKOOP"
    at.run()
    at.text_input(key="zoekterm").set_value("charizard").run()
    at.button(key=[b.key for b in at.button if b.key.startswith("pick_")][0]).click().run()
    check(at.number_input(key="bedrag").value == 0.0, "bij inkoop geen voorgevuld bedrag")
    at.number_input(key="bedrag").set_value(60.0).run()
    at.button(key="vastleggen").click().run()
    r = rijen(engine)
    check(len(r) == 2, f"tweede invoer direct erna werkt (kreeg {len(r)} rijen)")
    if len(r) == 2:
        check(r[1]["type"] == "inkoop" and float(r[1]["bedrag"]) == 60.0,
              "tweede invoer is inkoop van €60")

    # --- vangnet: vrij invoeren ------------------------------------------
    at.text_input(key="vrij_naam").set_value("Japanse Eevee Heroes booster box").run()
    at.number_input(key="vrij_bedrag").set_value(275.0).run()
    at.button(key="vrij_knop").click().run()
    r = rijen(engine)
    check(len(r) == 3, f"vrije invoer weggeschreven (kreeg {len(r)} rijen)")
    if len(r) == 3:
        v = r[2]
        check(v["item_id"] is None, "vrije invoer heeft item_id NULL")
        check(v["flag"] == "vrij ingevoerd", "flag = 'vrij ingevoerd'")
        check(v["ruwe_tekst"] == "Japanse Eevee Heroes booster box", "ruwe_tekst = ingetypte naam")
        check(v["afzender"] == "TC", "vrije invoer heeft afzender 'TC'")

    # --- bedrag 0 wordt geweigerd ----------------------------------------
    at.session_state["modus"] = "VERKOOP"
    at.run()
    at.text_input(key="zoekterm").set_value("pikachu").run()
    at.button(key=[b.key for b in at.button if b.key.startswith("pick_")][0]).click().run()
    at.number_input(key="bedrag").set_value(0.0).run()
    at.button(key="vastleggen").click().run()
    check(len(rijen(engine)) == 3, "bedrag €0 wordt niet weggeschreven")
    check(bool(at.error), "foutmelding getoond bij bedrag €0")
    check(at.session_state["sel"] is not None, "invoer blijft staan na weigering")

    # --- laatste invoeren als compacte regels -----------------------------
    log = [m.value for m in at.markdown if 'class="tc-log"' in m.value]
    check(len(log) == 1, "compacte lijst met laatste invoeren wordt getoond")
    if log:
        check(log[0].count("<br>") == 2, f"3 regels in de lijst ({log[0].count('<br>') + 1})")
        check("−€60" in log[0] and "+€430" in log[0],
              f"inkoop met − en verkoop met + ({log[0][:120]})")
    check(not at.dataframe, "geen tabel meer op het invoerscherm")

    # --- twee sessies tegelijk -------------------------------------------
    at2 = AppTest.from_file(str(INVENTORY / "beurs_app.py"), default_timeout=60)
    at2.run()
    check(not at2.exception, "tweede sessie start zonder exception")
    at2.text_input(key="zoekterm").set_value("umbreon v").run()
    at2.button(key=[b.key for b in at2.button if b.key.startswith("pick_")][0]).click().run()
    at2.number_input(key="bedrag").set_value(40.0).run()
    at2.button(key="vastleggen").click().run()
    r = rijen(engine)
    check(len(r) == 4, f"invoer vanuit tweede sessie werkt (kreeg {len(r)} rijen)")
    if len(r) == 4:
        check(r[3]["afzender"] == "TC" and float(r[3]["bedrag"]) == 40.0,
              "tweede sessie schrijft naar dezelfde database")

    # --- schrijffout: invoer blijft behouden ------------------------------
    kapot = create_engine("postgresql+psycopg2://x:y@127.0.0.1:1/none",
                          connect_args={"connect_timeout": 2})
    db.get_engine = lambda: kapot
    import streamlit as st
    st.cache_resource.clear()   # anders blijft de sqlite-engine in de cache hangen
    st.cache_data.clear()
    at3 = AppTest.from_file(str(INVENTORY / "beurs_app.py"), default_timeout=60)
    at3.run()
    check(not at3.exception, "app crasht niet als de database onbereikbaar is")
    check(bool(at3.error), f"duidelijke foutmelding bij geen verbinding: "
                           f"{[e.value[:60] for e in at3.error]}")

    print()
    if fouten:
        print(f"{len(fouten)} FOUT(EN):")
        for f in fouten:
            print("  -", f)
        sys.exit(1)
    print("Alle checks OK")


if __name__ == "__main__":
    main()
