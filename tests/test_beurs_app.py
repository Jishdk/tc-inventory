"""Rookproef voor beurs_app.py — draait de hele Streamlit-app headless
(streamlit.testing) tegen een SQLite-schaduwkopie van het Supabase-schema.

Zo is de invoerflow te testen zonder database-verbinding: zoeken, item kiezen,
bedrag, VASTLEGGEN, vrij invoeren, twee invoeren snel na elkaar, het live
dagtotaal, de inkoop-rekenhulp, de stapel (totaal-modus) en undo.

    python tests/test_beurs_app.py
"""

import datetime
import logging
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, text

# Buiten een echte server logt Streamlit bij elke run "missing ScriptRunContext"
# en "No runtime found"; dat is hier normaal en maakt het verslag onleesbaar.
# Streamlit zet de niveaus van zijn eigen loggers zelf, dus dit moet globaal.
logging.disable(logging.WARNING)

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
    flag TEXT, ruwe_tekst TEXT, code TEXT);
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
    # zonder comp_prijs: er valt niets terug te rekenen bij inkoop
    ("Bulbasaur ruilbak", "Bulbasaur", "001", "BS 001", "single", "MP", None, None),
]

# Zoals de v6-inventaris: slabs hebben géén comp_prijs, alleen een cm-prijs, en
# sealed staat zonder officiele_naam/set_code in de tabel.
# onze_naam, code, categorie, staat, grade, comp_prijs, prijs_cm
ALLEEN_CM = [
    ("Pika van Gogh", None, "Slab", "NM", "PSA 9", None, 852.00),
    ("Shining Gyarados", None, "Slab", "NM", "PSA 8", None, 2300.00),
    ("Moltres UPC", None, "Sealed", "NM", None, None, 330.00),
    ("151 ETB", None, "Sealed", "NM", None, 500.00, 475.00),
]

fouten = []


def check(voorwaarde, omschrijving):
    print(("  OK   " if voorwaarde else "  FOUT ") + omschrijving)
    if not voorwaarde:
        fouten.append(omschrijving)


def dagtotaal(at) -> str:
    """De balk bovenaan als platte tekst: 'Vandaag: €430 in · €60 uit · netto €370'."""
    balk = [m.value for m in at.markdown if 'class="tc-dag"' in m.value]
    if not balk:
        return ""
    plat = re.sub(r"<[^>]+>", "", balk[0]).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", plat).strip()


def knoppen(at, prefix: str) -> list:
    return [b for b in at.button if b.key and b.key.startswith(prefix)]


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
        for naam, code, cat, staat, grade, comp, cm in ALLEEN_CM:
            c.execute(text(
                "INSERT INTO items (onze_naam, code, categorie, staat, grade, "
                "comp_prijs, prijs_cm) VALUES (:a,:b,:c,:d,:e,:f,:g)"),
                {"a": naam, "b": code, "c": cat, "d": staat, "e": grade,
                 "f": comp, "g": cm})
    return engine


def rijen(engine):
    with engine.connect() as c:
        return c.execute(text(
            "SELECT id, event_id, item_id, datum, tijd, kanaal, type, bedrag, "
            "afzender, flag, ruwe_tekst, code FROM transactions "
            "ORDER BY id")).mappings().all()


def toon(rij) -> str:
    """Eén transactieregel zoals hij in de database staat."""
    return (f"id={rij['id']}  type={rij['type']}  bedrag={float(rij['bedrag']):.2f}  "
            f"item_id={rij['item_id']}  flag={rij['flag']!r}  code={rij['code']!r}\n"
            f"                 ruwe_tekst={rij['ruwe_tekst']!r}")


def kop(nummer: str, vraag: str, verwacht: str):
    print(f"\n{nummer} {vraag}")
    print(f"   verwacht : {verwacht}")


def uitkomst(*regels):
    for i, regel in enumerate(regels):
        print(("   resultaat: " if i == 0 else "              ") + regel)


def scenarios(AppTest, db):
    """Vier scenario's die de vragen uit de opdracht beantwoorden, met een
    leesbaar 'verwacht → resultaat' per stap. Draait op een eigen verse
    database, zodat de bedragen in het verslag niet door de rookproef lopen."""
    import streamlit as st

    engine = maak_engine()
    db.get_engine = lambda: engine
    st.cache_resource.clear()
    st.cache_data.clear()

    print("\n" + "=" * 72)
    print("TESTVERSLAG — scenario's")
    print("=" * 72)

    at = AppTest.from_file(str(INVENTORY / "beurs_app.py"), default_timeout=60)
    at.run()

    # --- 1. meerdere kaarten in één stapel-transactie ----------------------
    kop("1.", "Kan ik meerdere kaarten in één stapel-transactie zetten?",
        "3 kaarten toevoegen → 1 inkoop-rij van €185, namen + codes bewaard")
    at.session_state["modus"] = "INKOOP"
    at.session_state["inkoop_modus"] = "Totaal"
    at.run()
    at.text_input(key="stapel_zoek").set_value("umbreon vmax").run()
    at.button(key=knoppen(at, "add_")[0].key).click().run()
    at.text_input(key="stapel_zoek").set_value("charizard").run()
    at.button(key=knoppen(at, "add_")[0].key).click().run()
    at.text_input(key="stapel_vrij").set_value("Bulk commons ±200 kaarten").run()
    at.text_input(key="stapel_code").set_value("diverse").run()
    at.button(key="stapel_vrij_knop").click().run()
    check(len(at.session_state["stapel"]) == 3, "scenario 1: 3 kaarten in de stapel")
    check("(3)" in at.button(key="stapel_vastleggen").label,
          "scenario 1: knop toont het aantal")
    at.number_input(key="totaal_bedrag").set_value(185.0).run()
    at.button(key="stapel_vastleggen").click().run()

    r = rijen(engine)
    check(len(r) == 1, "scenario 1: precies één transactie")
    check(r and float(r[0]["bedrag"]) == 185.0 and r[0]["type"] == "inkoop",
          "scenario 1: inkoop van €185")
    check(r and r[0]["item_id"] is None and r[0]["flag"] == "stapel",
          "scenario 1: item_id NULL, flag 'stapel'")
    check(r and all(x in r[0]["ruwe_tekst"] for x in
                    ("Umbreon VMAX", "EVS 215", "Charizard ex", "OBF 223",
                     "Bulk commons", "diverse")),
          "scenario 1: alle namen én codes staan in ruwe_tekst")
    uitkomst(f"{len(r)} rij in de database", toon(r[0]))
    check(at.session_state["stapel"] == [], "scenario 1: stapel leegt zichzelf")
    uitkomst("stapel na vastleggen: leeg") if not at.session_state["stapel"] else None

    # --- 2. vrije invoer met code -----------------------------------------
    kop("2.", "Belandt de code van een vrij ingevoerde kaart in de code-kolom?",
        "naam 'Mew ex promo' + code '151/165' → code-kolom gevuld, item_id NULL")
    at.session_state["modus"] = "VERKOOP"
    at.run()
    at.text_input(key="vrij_naam").set_value("Mew ex promo").run()
    at.text_input(key="vrij_code").set_value("151/165").run()
    at.number_input(key="vrij_bedrag").set_value(275.0).run()
    at.button(key="vrij_knop").click().run()

    r = rijen(engine)
    vrij = r[-1]
    check(vrij["code"] == "151/165", "scenario 2: code in de code-kolom")
    check(vrij["ruwe_tekst"] == "Mew ex promo", "scenario 2: naam in ruwe_tekst")
    check(vrij["item_id"] is None and vrij["flag"] == "vrij ingevoerd",
          "scenario 2: item_id NULL, flag 'vrij ingevoerd'")
    check(vrij["type"] == "verkoop", "scenario 2: staat als verkoop geboekt")
    uitkomst(toon(vrij))

    # --- 3. percentage-rekenhulp ------------------------------------------
    kop("3.", "Rekent de percentage-hulp goed, en kan ik het bedrag overschrijven?",
        "comp €120 × 80% = €96; daarna handmatig €92 → €92 in de database")
    at.session_state["modus"] = "INKOOP"
    at.session_state["inkoop_modus"] = "Per kaart"
    at.run()
    at.text_input(key="zoekterm").set_value("charizard").run()
    at.button(key=knoppen(at, "pick_")[0].key).click().run()
    at.button(key="pct_80").click().run()
    berekend = at.number_input(key="bedrag").value
    check(berekend == 96.0, f"scenario 3: 80% van €120 = €96 (kreeg {berekend})")
    at.number_input(key="bedrag").set_value(92.0).run()
    at.button(key="vastleggen").click().run()
    r = rijen(engine)
    check(float(r[-1]["bedrag"]) == 92.0, "scenario 3: overschreven bedrag opgeslagen")
    uitkomst(f"knop 80% vulde €{berekend:.2f} in, daarna handmatig €92,00",
             toon(r[-1]))

    # --- 4. dagtotaal + undo ----------------------------------------------
    kop("4.", "Klopt het dagtotaal, en draait undo mijn eigen laatste invoer terug?",
        "eigen invoer telt direct mee; collega boekt ná mij; undo pakt míjn €92 "
        "en laat hun €50 staan")
    voor_collega = dagtotaal(at)
    check(voor_collega == "Vandaag: €275 in · €277 uit · netto −€2",
          f"scenario 4: eigen invoer telt direct mee ({voor_collega!r})")

    at2 = AppTest.from_file(str(INVENTORY / "beurs_app.py"), default_timeout=60)
    at2.run()
    at2.text_input(key="zoekterm").set_value("umbreon v").run()
    at2.button(key=knoppen(at2, "pick_")[0].key).click().run()
    at2.number_input(key="bedrag").set_value(50.0).run()
    at2.button(key="vastleggen").click().run()

    # Bewust gedrag: de balk leest uit een cache van 20 s, dus de invoer van een
    # collega hoeft niet meteen zichtbaar te zijn. Je eigen invoer wél — die
    # breekt de cache open. Zodra je zelf iets doet (hieronder: undo) staat het
    # totaal weer helemaal bij.
    at.run()
    check(dagtotaal(at) == voor_collega,
          f"scenario 4: invoer van een collega volgt binnen de cache-TTL "
          f"({dagtotaal(at)!r})")
    uitkomst(f"direct na eigen invoer  : {voor_collega}",
             f"vlak na invoer collega  : {dagtotaal(at)}  "
             f"(cache van 20 s — nog niet ververst)")

    at.button(key="undo").click().run()
    check(any("92" in w.value for w in at.warning),
          f"scenario 4: bevestiging noemt de eigen laatste invoer "
          f"({[w.value for w in at.warning]})")
    at.button(key="undo_ja").click().run()
    r = rijen(engine)
    bedragen = [float(x["bedrag"]) for x in r]
    check(bedragen == [185.0, 275.0, 50.0],
          f"scenario 4: eigen €92 weg, €50 van de collega blijft ({bedragen})")
    check(dagtotaal(at) == "Vandaag: €325 in · €185 uit · netto €140",
          f"scenario 4: dagtotaal loopt terug ({dagtotaal(at)!r})")
    uitkomst(f"bevestiging             : {at.session_state['bevestiging']}",
             f"rijen na undo           : {bedragen}",
             f"dagtotaal na undo       : {dagtotaal(at)}  "
             f"(325 in = eigen 275 + collega 50; 185 uit = de stapel)")
    print()


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

    # --- dagtotaal: leeg bij de start -------------------------------------
    check(dagtotaal(at) == "Vandaag: €0 in · €0 uit · netto €0",
          f"dagtotaal begint op nul ({dagtotaal(at)!r})")

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

    # --- slabs/sealed zonder comp_prijs: terugval op de cm-prijs -----------
    at.text_input(key="zoekterm").set_value("pika van gogh").run()
    labels = [b.label for b in at.button if b.key and b.key.startswith("pick_")]
    check(labels and "852.00" in labels[0],
          f"slab zonder comp toont de cm-prijs ({labels})")
    check(labels and "cm" in labels[0],
          f"cm-prijs is als zodanig gelabeld, niet als comp ({labels})")
    at.text_input(key="zoekterm").set_value("moltres").run()
    labels = [b.label for b in at.button if b.key and b.key.startswith("pick_")]
    check(labels and "330.00" in labels[0] and "€ ?" not in labels[0],
          f"sealed zonder comp toont de cm-prijs ({labels})")
    at.text_input(key="zoekterm").set_value("151 etb").run()
    labels = [b.label for b in at.button if b.key and b.key.startswith("pick_")]
    check(labels and "500.00" in labels[0] and " cm" not in labels[0],
          f"comp gaat vóór cm en krijgt geen cm-label ({labels})")

    at.text_input(key="zoekterm").set_value("shining gyarados").run()
    at.button(key=[b.key for b in at.button if b.key.startswith("pick_")][0]).click().run()
    check(at.number_input(key="bedrag").value == 2300.00,
          f"verkoop vult de cm-prijs voor (kreeg {at.number_input(key='bedrag').value})")
    at.button(key="deselect").click().run()

    at.text_input(key="zoekterm").set_value("umbreon vmax").run()
    at.button(key=[b.key for b in at.button if b.key.startswith("pick_")][0]).click().run()
    check(at.session_state["sel"]["naam"] == "Umbreon VMAX (Alternate Art)",
          "item geselecteerd")
    check(at.number_input(key="bedrag").value == 450.00,
          f"bedrag voorgevuld met comp_prijs (kreeg {at.number_input(key='bedrag').value})")
    check(not knoppen(at, "pct_"), "verkoop-flow blijft kaal: geen rekenhulp")

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
        check(t["code"] == "EVS 215", f"code van de gezochte kaart bewaard ({t['code']!r})")
        check(t["event_id"] == ev[0][0], "gekoppeld aan het beurs-event")
        check(t["datum"] is not None and t["tijd"] is not None, "datum + tijd gevuld")

    # --- reset + bevestiging ---------------------------------------------
    check(at.session_state["sel"] is None, "formulier gereset na vastleggen")
    check(at.session_state["bedrag"] == 0.0, "bedrag gereset")
    bev = at.session_state["bevestiging"]
    check(bev == "✓ Umbreon VMAX (Alternate Art) — €430.00", f"korte bevestiging: {bev}")
    check(any("tc-ok" in m.value and bev in m.value for m in at.markdown),
          "bevestiging staat als vervagend blok op het scherm")
    check(dagtotaal(at) == "Vandaag: €430 in · €0 uit · netto €430",
          f"dagtotaal ververst na de verkoop ({dagtotaal(at)!r})")

    # --- tweede invoer direct erna (verkoop → inkoop) ---------------------
    at.session_state["modus"] = "INKOOP"
    at.run()
    check(at.session_state["inkoop_modus"] == "Per kaart",
          "inkoop opent op 'Per kaart'")
    at.text_input(key="zoekterm").set_value("charizard").run()
    at.button(key=[b.key for b in at.button if b.key.startswith("pick_")][0]).click().run()
    check(at.number_input(key="bedrag").value == 0.0, "bij inkoop geen voorgevuld bedrag")

    # --- rekenhulp: comp × percentage, en altijd te overschrijven ---------
    check([b.key for b in knoppen(at, "pct_")] == ["pct_70", "pct_75", "pct_80"],
          f"drie percentage-snelknoppen ({[b.key for b in knoppen(at, 'pct_')]})")
    check(any("comp €120" in m.value for m in at.markdown), "comp-prijs staat erbij")
    at.button(key="pct_75").click().run()
    check(at.number_input(key="bedrag").value == 90.0,
          f"75% van comp €120 = €90 (kreeg {at.number_input(key='bedrag').value})")
    at.number_input(key="eigen_pct").set_value(65).run()
    check(at.number_input(key="bedrag").value == 78.0,
          f"eigen percentage 65% = €78 (kreeg {at.number_input(key='bedrag').value})")
    at.number_input(key="bedrag").set_value(60.0).run()
    check(at.number_input(key="bedrag").value == 60.0,
          "berekend bedrag blijft met de hand te overschrijven")
    at.button(key="vastleggen").click().run()
    r = rijen(engine)
    check(len(r) == 2, f"tweede invoer direct erna werkt (kreeg {len(r)} rijen)")
    if len(r) == 2:
        check(r[1]["type"] == "inkoop" and float(r[1]["bedrag"]) == 60.0,
              "tweede invoer is inkoop van €60")
    check(dagtotaal(at) == "Vandaag: €430 in · €60 uit · netto €370",
          f"dagtotaal telt verkoop en inkoop apart ({dagtotaal(at)!r})")

    # --- rekenhulp: afronding op hele euro's + kaart zonder comp ----------
    at.text_input(key="zoekterm").set_value("pikachu").run()
    at.button(key=[b.key for b in at.button if b.key.startswith("pick_")][0]).click().run()
    at.button(key="pct_70").click().run()
    check(at.number_input(key="bedrag").value == 9.0,
          f"70% van €12,50 = €8,75 → afgerond €9 (kreeg {at.number_input(key='bedrag').value})")
    at.button(key="deselect").click().run()
    at.text_input(key="zoekterm").set_value("bulbasaur").run()
    at.button(key=[b.key for b in at.button if b.key.startswith("pick_")][0]).click().run()
    check(not knoppen(at, "pct_"), "kaart zonder comp-prijs: geen rekenhulp")
    check(bool(at.number_input(key="bedrag")), "zonder comp gewoon zelf een bedrag intikken")
    at.button(key="deselect").click().run()
    check(len(rijen(engine)) == 2, "rekenen schrijft niets weg")

    # --- vangnet: vrij invoeren, mét losse code --------------------------
    at.text_input(key="vrij_naam").set_value("Japanse Eevee Heroes booster box").run()
    at.text_input(key="vrij_code").set_value("s6a").run()
    at.number_input(key="vrij_bedrag").set_value(275.0).run()
    at.button(key="vrij_knop").click().run()
    r = rijen(engine)
    check(len(r) == 3, f"vrije invoer weggeschreven (kreeg {len(r)} rijen)")
    if len(r) == 3:
        v = r[2]
        check(v["item_id"] is None, "vrije invoer heeft item_id NULL")
        check(v["flag"] == "vrij ingevoerd", "flag = 'vrij ingevoerd'")
        check(v["ruwe_tekst"] == "Japanse Eevee Heroes booster box", "ruwe_tekst = ingetypte naam")
        check(v["code"] == "s6a", f"losse code in de code-kolom ({v['code']!r})")
        check(v["afzender"] == "TC", "vrije invoer heeft afzender 'TC'")
    check(at.text_input(key="vrij_code").value == "", "code-veld leeg na vastleggen")

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

    # --- inkoop op totaal: stapeltje kaarten, één bedrag -------------------
    at.session_state["modus"] = "INKOOP"
    at.session_state["inkoop_modus"] = "Totaal"
    at.run()
    check("(0)" in at.button(key="stapel_vastleggen").label,
          f"lege stapel: teller op nul ({at.button(key='stapel_vastleggen').label})")
    at.text_input(key="stapel_zoek").set_value("umbreon vmax").run()
    at.button(key=knoppen(at, "add_")[0].key).click().run()
    check(len(at.session_state["stapel"]) == 1, "kaart uit de database toegevoegd")
    check(at.text_input(key="stapel_zoek").value == "",
          "zoekveld leeg na toevoegen — meteen door naar de volgende")
    at.text_input(key="stapel_zoek").set_value("charizard").run()
    at.button(key=knoppen(at, "add_")[0].key).click().run()
    at.text_input(key="stapel_vrij").set_value("Stapeltje commons").run()
    at.text_input(key="stapel_code").set_value("bulk").run()
    at.button(key="stapel_vrij_knop").click().run()
    check([k["naam"] for k in at.session_state["stapel"]] ==
          ["Umbreon VMAX (Alternate Art)", "Charizard ex (Special Illustration)",
           "Stapeltje commons"],
          f"drie kaarten in de stapel ({at.session_state['stapel']})")
    check([k["code"] for k in at.session_state["stapel"]] ==
          ["EVS 215", "OBF 223", "bulk"],
          f"codes meegenomen in de stapel ({[k['code'] for k in at.session_state['stapel']]})")
    check(any('class="tc-teller"' in m.value and ">3</b> kaarten" in m.value
              for m in at.markdown),
          "teller boven de lijst telt mee")

    at.button(key="weg_1").click().run()
    check([k["naam"] for k in at.session_state["stapel"]] ==
          ["Umbreon VMAX (Alternate Art)", "Stapeltje commons"],
          "verkeerde kaart is los te verwijderen")

    at.number_input(key="totaal_bedrag").set_value(0.0).run()
    at.button(key="stapel_vastleggen").click().run()
    check(len(rijen(engine)) == 4, "stapel zonder totaalbedrag wordt niet weggeschreven")
    check(len(at.session_state["stapel"]) == 2, "stapel blijft staan na weigering")

    at.number_input(key="totaal_bedrag").set_value(150.0).run()
    at.button(key="stapel_vastleggen").click().run()
    r = rijen(engine)
    check(len(r) == 5, f"stapel = één transactie (kreeg {len(r)} rijen)")
    if len(r) == 5:
        s = r[4]
        check(s["type"] == "inkoop" and float(s["bedrag"]) == 150.0,
              "één inkoop van €150 voor de hele stapel")
        check(s["item_id"] is None, "stapel hangt aan geen enkel los item")
        check(s["flag"] == "stapel", "flag = 'stapel'")
        check(s["ruwe_tekst"] == "Stapel 2 kaarten: Umbreon VMAX (Alternate Art) "
                                 "[EVS 215] · Stapeltje commons [bulk]",
              f"namen én codes bewaard in ruwe_tekst ({s['ruwe_tekst']})")
        check(s["code"] is None, "stapel zelf heeft geen losse code")
    check(at.session_state["stapel"] == [], "stapel leeg na vastleggen")
    check(dagtotaal(at) == "Vandaag: €470 in · €485 uit · netto −€15",
          f"dagtotaal telt de stapel mee ({dagtotaal(at)!r})")

    # --- undo: alleen de eigen laatste invoer -----------------------------
    at2.text_input(key="zoekterm").set_value("blastoise").run()
    at2.button(key=knoppen(at2, "pick_")[0].key).click().run()
    at2.number_input(key="bedrag").set_value(25.0).run()
    at2.button(key="vastleggen").click().run()
    check(len(rijen(engine)) == 6, "collega boekt er nog een verkoop tussendoor")

    at.run()
    check(bool(at.button(key="undo")), "undo-knop staat er na een eigen invoer")
    at.button(key="undo").click().run()
    check(any("Laatste invoer verwijderen" in w.value and "150" in w.value
              for w in at.warning),
          f"eerst bevestiging vragen ({[w.value for w in at.warning]})")
    check(len(rijen(engine)) == 6, "vragen alleen — nog niets verwijderd")

    at.button(key="undo_nee").click().run()
    check(len(rijen(engine)) == 6, "'nee' laat de invoer staan")
    check(not at.warning, "bevestiging weg na 'nee'")

    at.button(key="undo").click().run()
    at.button(key="undo_ja").click().run()
    r = rijen(engine)
    check(len(r) == 5, f"undo verwijdert precies één rij (kreeg {len(r)} rijen)")
    check([float(x["bedrag"]) for x in r] == [430.0, 60.0, 275.0, 40.0, 25.0],
          f"eigen stapel weg, invoer van de collega blijft ({[float(x['bedrag']) for x in r]})")
    check(dagtotaal(at) == "Vandaag: €495 in · €335 uit · netto €160",
          f"dagtotaal loopt terug na undo ({dagtotaal(at)!r})")

    at.button(key="undo").click().run()
    at.button(key="undo_ja").click().run()
    r = rijen(engine)
    check([float(x["bedrag"]) for x in r] == [430.0, 60.0, 40.0, 25.0],
          f"tweede undo pakt de eigen vorige invoer (€275), niet die van de collega "
          f"({[float(x['bedrag']) for x in r]})")

    scenarios(AppTest, db)

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
