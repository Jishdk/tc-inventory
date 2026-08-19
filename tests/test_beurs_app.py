"""Rookproef voor beurs_app.py — draait de hele Streamlit-app headless
(streamlit.testing) tegen een SQLite-schaduwkopie van het Supabase-schema.

Zo is de invoerflow te testen zonder database-verbinding: zoeken, item kiezen,
bedrag, VASTLEGGEN, vrij invoeren, twee invoeren snel na elkaar, het live
dagtotaal, aantallen, de voorraad-indicator, de dubbel-check en undo.

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
    staat TEXT, grade TEXT, comp_prijs NUMERIC, prijs_cm NUMERIC,
    aantal INTEGER NOT NULL DEFAULT 0);
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER, item_id INTEGER,
    bron_rij INTEGER, datum DATE NOT NULL, tijd TIME, kanaal TEXT NOT NULL,
    type TEXT NOT NULL, bedrag NUMERIC, afzender TEXT, media_file TEXT,
    flag TEXT, ruwe_tekst TEXT, code TEXT, stuks INTEGER,
    is_dubbel BOOLEAN NOT NULL DEFAULT 0, opschoon_notitie TEXT,
    afgeboekt_op TIMESTAMP, group_id TEXT,
    onderhandeld BOOLEAN NOT NULL DEFAULT 0);
"""

# Zoals de echte data: lege set_code komt voor (187 van 572) en namen zijn dubbel (54x).
ITEMS = [
    # onze_naam, officiele_naam, code, set_code, categorie, staat, grade,
    # comp_prijs, aantal — de voorraad varieert bewust: ruim, precies één, en nul.
    ("Umbreon VMAX", "Umbreon VMAX (Alternate Art)", "215", "EVS 215", "single",
     "NM", None, 450.00, 3),
    ("Umbreon V", "Umbreon V", "189", "EVS 189", "single", "NM", None, 45.00, 1),
    ("Charizard ex", "Charizard ex (Special Illustration)", "223", "OBF 223", "single",
     "NM", None, 120.00, 12),
    ("Pikachu Promo", "Pikachu (Pokémon Center)", "SWSH058", "PR SWSH058", "promo",
     "NM", None, 12.50, 0),
    # gelijknamig paar zonder set_code, alleen te scheiden op staat/grade
    ("Blastoise slab", "Blastoise", None, None, "graded", None, "PSA 9", 300.00, 1),
    ("Blastoise los", "Blastoise", None, None, "single", "GD", None, 80.00, 2),
    # zonder comp_prijs: er valt niets terug te rekenen bij inkoop
    ("Bulbasaur ruilbak", "Bulbasaur", "001", "BS 001", "single", "MP", None, None, 5),
]

# Zoals de v6-inventaris: slabs hebben géén comp_prijs, alleen een cm-prijs, en
# sealed staat zonder officiele_naam/set_code in de tabel.
# onze_naam, code, categorie, staat, grade, comp_prijs, prijs_cm
ALLEEN_CM = [
    ("Pika van Gogh", None, "Slab", "NM", "PSA 9", None, 852.00, 1),
    ("Shining Gyarados", None, "Slab", "NM", "PSA 8", None, 2300.00, 1),
    ("Moltres UPC", None, "Sealed", "NM", None, None, 330.00, 4),
    ("151 ETB", None, "Sealed", "NM", None, 500.00, 475.00, 6),
]

# De producten waar de snelknoppen (SNELKNOPPEN in de app) op mikken. Zelfde vorm
# als ALLEEN_CM. Bewust géén "Twilight Masquerade booster": die staat ook in de
# echte v7 niet, en dat is precies het geval dat de knop moet laten wegvallen.
HARDLOPERS = [
    ("Prismatic booster", None, "Sealed", "NM", None, 15.00, None, 52),
    ("Phantasamal Flames sleeved booster", "me02", "Sealed", "NM", None, 14.00, None, 283),
    ("Cosmic Eclipse tins", None, "Sealed", "NM", None, 85.00, None, 4),
    ("Mega Dream", "m2a", "Sealed", "NM", None, 95.00, None, 24),
    ("Black Bolt", "sv11b", "Sealed", "NM", None, 120.00, None, 0),
]

fouten = []


def check(voorwaarde, omschrijving):
    print(("  OK   " if voorwaarde else "  FOUT ") + omschrijving)
    if not voorwaarde:
        fouten.append(omschrijving)


def dagtotaal(at) -> str:
    """De balk bovenaan als platte tekst: 'Vandaag: verkoop €430 · trades: 0'."""
    balk = [m.value for m in at.markdown if 'class="tc-dag"' in m.value]
    if not balk:
        return ""
    plat = re.sub(r"<[^>]+>", "", balk[0]).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", plat).strip()


def knoppen(at, prefix: str) -> list:
    return [b for b in at.button if b.key and b.key.startswith(prefix)]


def prijsvelden(at) -> list:
    """De prijs-invoervelden van het mandje, in de volgorde van het scherm."""
    return [ni for ni in at.number_input if ni.key and ni.key.startswith("regelprijs_")]


def zet_prijs(at, index: int, waarde: float):
    at.number_input(key=prijsvelden(at)[index].key).set_value(waarde).run()


def kies(at, term: str, filter_op: str = ""):
    """Zoek een kaart en tik het (eerste passende) resultaat aan — daarmee staat
    hij in het mandje."""
    at.text_input(key="zoekterm").set_value(term).run()
    treffers = knoppen(at, "pick_")
    if filter_op:
        treffers = [b for b in treffers if filter_op in b.label]
    at.button(key=treffers[0].key).click().run()


def snelknoppen(at) -> list:
    return knoppen(at, "snel_")


def mandje_namen(at) -> list:
    return [r["naam"] for r in at.session_state["mandje"]]


def echt_gekozen(at, key: str) -> bool:
    """Ziet de browser ook wat session_state beweert?

    Bij een keuzebalk die pas later op het scherm verschijnt — `prijs_modus` bij
    de tweede kaart, `cash_richting` in TRADE — komt een waarde die al in
    session_state stond niet bij de frontend aan: het knoppenpaar staat dan leeg
    terwijl `.value` netjes de juiste waarde teruggeeft. Alleen `proto.default`
    verraadt het verschil, en dat is precies wat de browser meekrijgt."""
    return bool(list(at.segmented_control(key=key).proto.default))


# Selectors die hun hele familie mogen raken, met woorden in plaats van cijfers
# achter de prefix. Alles wat hier niet staat en toch overlapt is een ongeluk —
# zoals [class*="st-key-prijs_"] dat prijs_modus meepakte en onzichtbaar maakte.
BEWUSTE_OVERLAP = {
    ("preset_", "preset_comp"),
    ("preset_", "preset_korting"),
    ("preset_", "preset_hand"),
}


def css_botsingen(at) -> set:
    """Selectors die per ongeluk een ándere widget te pakken hebben.

    Streamlit hangt aan elk element met een key de class `st-key-<key>`, en de
    app selecteert daarop met `[class*="st-key-…"]`. Dat is een prefix-match: een
    selector voor `pick_` hóórt `pick_3` te raken. Maar hij raakt net zo goed
    `pick_modus`, en dat is dan een heel ander element met heel andere opmaak.
    Dat is hier één keer misgegaan — `[class*="st-key-prijs_"] button
    {display:none}` maakte de prijskeuze `prijs_modus` onzichtbaar — en het is
    niet te zien in een headless test die geen CSS uitvoert. Vandaar deze check
    op de selectors zelf.

    Een rest die alleen uit cijfers bestaat is de bedoeling (`pick_` + `3`): dat
    zijn de families met één element per kaart. Een familie die op woorden eindigt
    moet hieronder expliciet staan — dan is het een keuze en geen ongeluk."""
    stijl = "\n".join(m.value for m in at.markdown if "<style>" in m.value)
    selectors = set(re.findall(r'\[class\*="st-key-([^"]+)"\]', stijl))
    elementen = (list(at.button) + list(at.number_input) + list(at.text_input)
                 + list(at.segmented_control))
    keys = {el.key for el in elementen if el.key}
    return {(s, k) for s in selectors for k in keys
            if k.startswith(s) and k != s and not k[len(s):].isdigit()
            and (s, k) not in BEWUSTE_OVERLAP}


def maak_engine():
    # Bestand, geen :memory: — Streamlit draait het script in een eigen thread.
    pad = Path(tempfile.mkdtemp(prefix="tc_beurs_test_")) / "test.db"
    engine = create_engine(f"sqlite:///{pad}",
                           connect_args={"check_same_thread": False})
    with engine.begin() as c:
        for stmt in SCHEMA.strip().split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        for onze, off, code, setc, cat, staat, grade, prijs, aantal in ITEMS:
            c.execute(text(
                "INSERT INTO items (onze_naam, officiele_naam, code, set_code, "
                "categorie, staat, grade, comp_prijs, aantal) "
                "VALUES (:a,:b,:c,:d,:e,:f,:g,:h,:i)"),
                {"a": onze, "b": off, "c": code, "d": setc, "e": cat, "f": staat,
                 "g": grade, "h": prijs, "i": aantal})
        for naam, code, cat, staat, grade, comp, cm, aantal in ALLEEN_CM + HARDLOPERS:
            c.execute(text(
                "INSERT INTO items (onze_naam, code, categorie, staat, grade, "
                "comp_prijs, prijs_cm, aantal) VALUES (:a,:b,:c,:d,:e,:f,:g,:h)"),
                {"a": naam, "b": code, "c": cat, "d": staat, "e": grade,
                 "f": comp, "g": cm, "h": aantal})
    return engine


def rijen(engine):
    with engine.connect() as c:
        return c.execute(text(
            "SELECT id, event_id, item_id, datum, tijd, kanaal, type, bedrag, "
            "afzender, flag, ruwe_tekst, code, stuks, group_id, onderhandeld "
            "FROM transactions ORDER BY id")).mappings().all()


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


def mandje_tests(AppTest, db):
    """Het mandje: meerdere kaarten in één transactie, per kaart of op één
    afgesproken totaalbedrag. Draait op een eigen verse database."""
    import streamlit as st

    engine = maak_engine()
    db.get_engine = lambda: engine
    st.cache_resource.clear()
    st.cache_data.clear()

    print("\n" + "=" * 72)
    print("MANDJE — meerdere kaarten in één transactie")
    print("=" * 72)

    at = AppTest.from_file(str(INVENTORY / "beurs_app.py"), default_timeout=60)
    at.run()

    # --- twee kaarten, elk zijn eigen prijs -------------------------------
    kies(at, "umbreon vmax")
    kies(at, "charizard")
    check(mandje_namen(at) == ["Umbreon VMAX (Alternate Art)",
                               "Charizard ex (Special Illustration)"],
          f"twee kaarten in het mandje ({mandje_namen(at)})")
    check([p.value for p in prijsvelden(at)] == [450.0, 120.0],
          f"elke regel begint op zijn eigen comp-prijs "
          f"({[p.value for p in prijsvelden(at)]})")
    check(any('class="tc-teller"' in m.value and ">2</b> kaarten" in m.value
              for m in at.markdown), "teller boven het mandje telt mee")
    check(at.segmented_control(key="prijs_modus").value == "Per kaart"
          and echt_gekozen(at, "prijs_modus"),
          "de prijskeuze staat op 'Per kaart' — en dat is op het scherm ook te zien")
    check("2 KAARTEN" in at.button(key="vastleggen").label,
          f"knop noemt het aantal ({at.button(key='vastleggen').label})")
    check(any("totaal <b>€570</b>" in m.value for m in at.markdown),
          "lopend totaal onderaan: 450 + 120 = 570")

    # aantal op de tweede regel omhoog, prijs van de eerste omlaag
    at.button(key=knoppen(at, "plus_")[1].key).click().run()
    zet_prijs(at, 0, 400.0)
    check(any("totaal <b>€640</b>" in m.value for m in at.markdown),
          "400 + 2 × 120 = 640")

    botsingen = css_botsingen(at)
    check(not botsingen,
          f"geen CSS-selector die stiekem een andere widget raakt ({botsingen})")

    at.button(key="vastleggen").click().run()
    r = rijen(engine)
    check(len(r) == 2, f"twee kaarten = twee regels (kreeg {len(r)})")
    check(r[0]["group_id"] and r[0]["group_id"] == r[1]["group_id"],
          "beide regels delen één group_id")
    check([float(x["bedrag"]) for x in r] == [400.0, 240.0],
          f"elke regel draagt zijn eigen bedrag ({[float(x['bedrag']) for x in r]})")
    check([x["stuks"] for x in r] == [1, 2], f"stuks per regel ({[x['stuks'] for x in r]})")
    check(all(x["item_id"] is not None for x in r),
          "beide regels blijven aan hun item gekoppeld — dus afboekbaar")
    check([x["flag"] for x in r] == ["mandje", "mandje"], "flag 'mandje'")
    uitkomst(*[toon(x) for x in r])
    check(at.session_state["mandje"] == [], "mandje is leeg na vastleggen")

    # --- één afgesproken totaalbedrag -------------------------------------
    kies(at, "umbreon vmax")        # comp 450
    kies(at, "umbreon v")           # comp 45
    at.segmented_control(key="prijs_modus").set_value("Totaalprijs").run()
    check(not prijsvelden(at), "bij een totaalprijs verdwijnen de losse prijzen")
    check(any("in totaal" in m.value for m in at.markdown),
          "de regels tonen dat ze in het totaal zitten")
    at.number_input(key="mandje_totaal").set_value(450.0).run()
    at.button(key="vastleggen").click().run()

    r = rijen(engine)[2:]
    bedragen = [float(x["bedrag"]) for x in r]
    check(sum(bedragen) == 450.0,
          f"de som is precies het afgesproken bedrag ({sum(bedragen)})")
    check(bedragen == [409.09, 40.91],
          f"naar rato van de comp-prijs verdeeld ({bedragen})")
    check(r[0]["group_id"] == r[1]["group_id"] and r[0]["flag"] == "mandje-totaal",
          "één groep, flag 'mandje-totaal'")
    uitkomst(f"afgesproken €450 over comp €450 + €45 → {bedragen}", *[toon(x) for x in r])

    # --- regel eruit halen -------------------------------------------------
    kies(at, "umbreon vmax")
    kies(at, "charizard")
    kies(at, "bulbasaur")
    check(len(at.session_state["mandje"]) == 3, "drie kaarten in het mandje")
    at.button(key=knoppen(at, "weg_")[1].key).click().run()
    check(mandje_namen(at) == ["Umbreon VMAX (Alternate Art)", "Bulbasaur"],
          f"de middelste regel is eruit ({mandje_namen(at)})")
    check([p.value for p in prijsvelden(at)] == [450.0, 0.0],
          f"de prijzen schuiven mee met hun eigen regel "
          f"({[p.value for p in prijsvelden(at)]})")
    at.button(key=knoppen(at, "weg_")[0].key).click().run()
    at.button(key=knoppen(at, "weg_")[0].key).click().run()
    check(at.session_state["mandje"] == [], "mandje helemaal leeg te maken")
    check(at.button(key="vastleggen").disabled,
          "VASTLEGGEN staat uit bij een leeg mandje")
    check(len(rijen(engine)) == 4, "weggooien schrijft niets weg")

    # --- vrij invoeren komt óók in het mandje ------------------------------
    kies(at, "charizard")
    at.text_input(key="vrij_naam").set_value("Japanse Eevee Heroes booster box").run()
    at.text_input(key="vrij_code").set_value("s6a").run()
    at.button(key="vrij_knop").click().run()
    check(mandje_namen(at) == ["Charizard ex (Special Illustration)",
                               "Japanse Eevee Heroes booster box"],
          f"vrije invoer schuift aan in hetzelfde mandje ({mandje_namen(at)})")
    zet_prijs(at, 1, 275.0)
    at.button(key="vastleggen").click().run()
    r = rijen(engine)[4:]
    check(len(r) == 2, "één klant, twee regels")
    check(r[1]["item_id"] is None and r[1]["flag"] == "vrij ingevoerd",
          "de vrije regel houdt zijn eigen flag, ook in een mandje")
    check(r[1]["code"] == "s6a", f"losse code bewaard ({r[1]['code']!r})")
    check(r[0]["group_id"] == r[1]["group_id"],
          "gekoppelde en vrije regel zitten in dezelfde groep")
    uitkomst(*[toon(x) for x in r])

    # --- undo draait de hele groep terug -----------------------------------
    check(len(rijen(engine)) == 6, "zes regels vóór de undo")
    check("2 regels" in at.button(key="undo").label,
          f"undo-knop kondigt aan dat het er twee zijn "
          f"({at.button(key='undo').label})")
    at.button(key="undo").click().run()
    at.button(key="undo_ja").click().run()
    r = rijen(engine)
    check(len(r) == 4, f"undo haalt beide regels van de afspraak weg (kreeg {len(r)})")
    check(all(x["group_id"] != "" for x in r), "de eerdere groepen blijven staan")
    uitkomst(f"rijen na undo: {[float(x['bedrag']) for x in r]}")


def kern_snel(AppTest, db):
    """De losse verkoop mag door het mandje niet langer worden: zoeken, tikken,
    vastleggen — en de prijs staat al goed."""
    import streamlit as st

    engine = maak_engine()
    db.get_engine = lambda: engine
    st.cache_resource.clear()
    st.cache_data.clear()

    print("\n" + "=" * 72)
    print("KERNFLOW — losse verkoop in drie handelingen")
    print("=" * 72)

    at = AppTest.from_file(str(INVENTORY / "beurs_app.py"), default_timeout=60)
    at.run()

    kop("A.", "Hoeveel handelingen kost één losse verkoop?",
        "3: zoekterm intikken, resultaat aantikken, VASTLEGGEN")
    at.text_input(key="zoekterm").set_value("umbreon vmax").run()          # 1
    at.button(key=knoppen(at, "pick_")[0].key).click().run()               # 2
    check(len(at.session_state["mandje"]) == 1,
          "één tik op het zoekresultaat zet de kaart in het mandje")
    check(prijsvelden(at)[0].value == 450.0,
          f"de prijs staat al goed ({prijsvelden(at)[0].value})")
    check(at.text_input(key="zoekterm").value == "",
          "zoekveld is leeg — meteen door naar de volgende kaart")
    check("2 KAARTEN" not in at.button(key="vastleggen").label,
          "bij één kaart blijft de knop de gewone VASTLEGGEN")
    check(not any(sc.key == "prijs_modus" for sc in at.segmented_control),
          "per kaart/totaalprijs blijft weg zolang het er één is")
    at.button(key="vastleggen").click().run()                              # 3
    r = rijen(engine)
    check(len(r) == 1 and float(r[0]["bedrag"]) == 450.0,
          f"drie handelingen, één verkoop van €450 ({[float(x['bedrag']) for x in r]})")
    check(r[0]["group_id"] is None,
          "een losse verkoop krijgt geen group_id — niets veranderd aan de data")
    uitkomst("zoekterm → resultaat aantikken → VASTLEGGEN", toon(r[0]))
    print()


def nieuwe_features(AppTest, db):
    """Aantallen, voorraad-indicator en dubbel-check (features 1-3), nu binnen
    het mandje. Draait op een eigen verse database."""
    import streamlit as st

    engine = maak_engine()
    db.get_engine = lambda: engine
    st.cache_resource.clear()
    st.cache_data.clear()

    print("\n" + "=" * 72)
    print("FEATURES 1-3 — aantallen, voorraad, dubbel-check")
    print("=" * 72)

    at = AppTest.from_file(str(INVENTORY / "beurs_app.py"), default_timeout=60)
    at.run()

    # --- aantallen (feature 1) --------------------------------------------
    kies(at, "charizard")
    rid = at.session_state["mandje"][0]["rid"]
    check(at.session_state[f"stuks_{rid}"] == 1, "aantal begint op 1")
    check(at.button(key=f"min_{rid}").disabled,
          "de min-knop staat uit op 1 — minder dan één verkoop bestaat niet")
    at.button(key=f"plus_{rid}").click().run()
    at.button(key=f"plus_{rid}").click().run()
    check(at.session_state[f"stuks_{rid}"] == 3,
          f"twee keer plus geeft 3 ({at.session_state[f'stuks_{rid}']})")
    at.button(key=f"min_{rid}").click().run()
    check(at.session_state[f"stuks_{rid}"] == 2, "min haalt er weer een af")
    at.button(key=f"plus_{rid}").click().run()

    zet_prijs(at, 0, 15.0)
    at.button(key="vastleggen").click().run()
    laatste = rijen(engine)[-1]
    check(float(laatste["bedrag"]) == 45.0,
          f"bedrag is het totaal (3 × €15), niet de stuksprijs "
          f"({float(laatste['bedrag'])})")
    check(int(laatste["stuks"]) == 3, f"stuks = 3 in de database ({laatste['stuks']})")
    check(laatste["group_id"] is None,
          "drie stuks van één kaart is nog steeds één regel zonder groep")
    uitkomst(toon(laatste) + f"  stuks={laatste['stuks']}")

    # --- voorraad-indicator (feature 2) ------------------------------------
    at.text_input(key="zoekterm").set_value("umbreon").run()
    labels = [b.label for b in knoppen(at, "pick_")]
    check(any("3 op voorraad" in lb for lb in labels),
          f"voorraad staat bij het zoekresultaat ({labels})")
    check(any("1 op voorraad" in lb for lb in labels),
          f"laatste exemplaar toont '1 op voorraad' ({labels})")

    kies(at, "pikachu promo")
    tekst = " ".join(m.value for m in at.markdown)
    check("UITVERKOCHT" in tekst, "uitverkochte kaart waarschuwt in het mandje")
    check("klopt dan alleen niet" in tekst, "waarschuwen, niet blokkeren")
    check(not at.button(key="vastleggen").disabled,
          "VASTLEGGEN blijft bruikbaar bij voorraad 0")

    # meer dan er ligt: ook een melding, ook geen blokkade
    at.button(key=knoppen(at, "weg_")[0].key).click().run()
    kies(at, "umbreon v", "189")            # voorraad 1
    rid = at.session_state["mandje"][0]["rid"]
    at.button(key=f"plus_{rid}").click().run()
    check(any("Meer dan de 1 op voorraad" in m.value for m in at.markdown),
          "2 stuks van een kaart waarvan er 1 ligt geeft een melding")
    at.button(key=f"min_{rid}").click().run()

    # --- dubbel-waarschuwing (feature 3) -----------------------------------
    zet_prijs(at, 0, 45.0)
    at.button(key="vastleggen").click().run()
    voor = len(rijen(engine))

    kies(at, "umbreon v", "189")
    zet_prijs(at, 0, 45.0)
    at.button(key="vastleggen").click().run()
    check(len(rijen(engine)) == voor,
          "tweede identieke invoer wordt nog NIET weggeschreven")
    check(at.session_state["dubbel_vraag"] is not None, "de dubbel-vraag staat aan")
    check("Net al ingevoerd" in " ".join(m.value for m in at.markdown),
          "inline waarschuwing zichtbaar")
    check(at.button(key="vastleggen").disabled,
          "VASTLEGGEN staat uit zolang de vraag openstaat")
    banner = [m.value for m in at.markdown if 'class="tc-dubbel"' in m.value]
    uitkomst(re.sub(r"<[^>]+>", "", banner[0]).strip() if banner else "(geen banner)")

    at.button(key="dubbel_ja").click().run()
    check(len(rijen(engine)) == voor + 1, "na bevestigen wordt hij alsnog vastgelegd")
    check(at.session_state["dubbel_vraag"] is None, "de vraag is opgeruimd")

    kies(at, "umbreon v", "189")
    zet_prijs(at, 0, 45.0)
    at.button(key="vastleggen").click().run()
    at.button(key="dubbel_nee").click().run()
    check(len(rijen(engine)) == voor + 1, "na 'nee' komt er niets bij")
    check(at.session_state["dubbel_vraag"] is None, "de vraag is weg na 'nee'")
    check(len(at.session_state["mandje"]) == 1,
          "de kaart blijft in het mandje staan na 'nee'")

    zet_prijs(at, 0, 40.0)
    at.button(key="vastleggen").click().run()
    check(len(rijen(engine)) == voor + 2, "ander bedrag gaat er zonder vraag doorheen")

    # een mandje van meerdere kaarten wordt niet als dubbeling gezien
    kies(at, "umbreon vmax")
    kies(at, "charizard")
    zet_prijs(at, 0, 20.0)
    zet_prijs(at, 1, 20.0)
    at.button(key="vastleggen").click().run()
    at.button(key="undo").click().run()
    at.button(key="undo_ja").click().run()
    kies(at, "umbreon vmax")
    kies(at, "charizard")
    zet_prijs(at, 0, 20.0)
    zet_prijs(at, 1, 20.0)
    at.button(key="vastleggen").click().run()
    check(at.session_state["dubbel_vraag"] is None,
          "een mandje typ je niet per ongeluk twee keer — geen vraag")


def trade_tests(AppTest, db):
    """Trades: dezelfde mandje-flow voor de kaarten die eruit gaan, plus één
    cash-regel met de richting erin. Draait op een eigen verse database."""
    import streamlit as st

    engine = maak_engine()
    db.get_engine = lambda: engine
    st.cache_resource.clear()
    st.cache_data.clear()

    print("\n" + "=" * 72)
    print("TRADES — kaarten eruit, cash en binnenkomst in één groep")
    print("=" * 72)

    at = AppTest.from_file(str(INVENTORY / "beurs_app.py"), default_timeout=60)
    at.run()
    check(any(sc.key == "modus" for sc in at.segmented_control),
          "de moduskeuze staat er weer zodra TRADE bestaat")

    at.segmented_control(key="modus").set_value("TRADE").run()
    check(at.session_state["modus"] == "TRADE", "TRADE is te kiezen")
    check(bool(at.segmented_control(key="cash_richting")),
          "de twee cash-richtingen staan er meteen")

    # --- twee kaarten eruit, wij ontvangen cash ---------------------------
    kies(at, "umbreon vmax")
    kies(at, "charizard")
    check(not prijsvelden(at),
          "een geruilde kaart heeft geen prijsveld — het geld hangt aan de afspraak")
    check(any("eruit" in m.value for m in at.markdown),
          "de regels laten zien dat ze de deur uit gaan")
    check(not any(sc.key == "prijs_modus" for sc in at.segmented_control),
          "per kaart/totaalprijs hoort niet bij een trade")
    check(at.segmented_control(key="cash_richting").value == "WIJ ONTVANGEN €"
          and echt_gekozen(at, "cash_richting"),
          "cash staat standaard op ontvangen — en die knop is ook echt aangezet")

    at.number_input(key="cash_bedrag").set_value(75.0).run()
    at.text_input(key="binnen_notitie").set_value("5 kaarten, zie WA-foto").run()
    check(any("cash <b>+€75</b>" in m.value for m in at.markdown),
          "de balk toont de richting van het geld")
    check("TRADE" in at.button(key="vastleggen").label,
          f"knop noemt de trade ({at.button(key='vastleggen').label})")
    at.button(key="vastleggen").click().run()

    r = rijen(engine)
    check(len(r) == 3, f"twee kaarten + één cash-regel (kreeg {len(r)})")
    check(all(x["type"] == "trade" for x in r), "alles staat als type 'trade'")
    groepen = {x["group_id"] for x in r}
    check(len(groepen) == 1 and None not in groepen,
          f"kaarten én cash zitten in dezelfde groep ({groepen})")
    kaarten, cash = r[:2], r[2]
    check([float(x["bedrag"]) for x in kaarten] == [0.0, 0.0],
          f"de kaarten gaan op €0 de deur uit "
          f"({[float(x['bedrag']) for x in kaarten]})")
    check(all(x["item_id"] and x["stuks"] for x in kaarten),
          "elke kaart houdt item_id + stuks, dus de voorraad boekt af")
    check(float(cash["bedrag"]) == 75.0 and cash["flag"] == "trade_cash",
          f"één cash-regel van €75 met flag 'trade_cash' ({toon(cash)})")
    check(cash["item_id"] is None and cash["ruwe_tekst"] == "5 kaarten, zie WA-foto",
          "de cash-regel draagt de omschrijving van wat er binnenkwam")
    uitkomst(*[toon(x) for x in r])

    # --- wij leggen bij: negatief bedrag ----------------------------------
    kies(at, "umbreon v", "189")
    at.segmented_control(key="cash_richting").set_value("WIJ LEGGEN BIJ €").run()
    at.number_input(key="cash_bedrag").set_value(40.0).run()
    check(any("cash <b>−€40</b>" in m.value for m in at.markdown),
          "bijleggen leest als een min op het scherm")
    at.button(key="vastleggen").click().run()
    cash = rijen(engine)[-1]
    check(float(cash["bedrag"]) == -40.0,
          f"bijleggen staat negatief in de database ({float(cash['bedrag'])})")
    uitkomst(toon(cash))

    # --- gelijke ruil: €0 mag bij een trade -------------------------------
    kies(at, "charizard")
    at.number_input(key="cash_bedrag").set_value(0.0).run()
    at.button(key="vastleggen").click().run()
    r = rijen(engine)
    check(len(r) == 7, f"een gelijke ruil wordt gewoon vastgelegd (kreeg {len(r)})")
    check(float(r[-1]["bedrag"]) == 0.0 and r[-1]["flag"] == "trade_cash",
          "ook zonder cash is er een anker-regel voor de afspraak")
    check("niet omschreven" in r[-1]["ruwe_tekst"],
          f"zonder omschrijving staat er zichtbaar dat die ontbreekt "
          f"({r[-1]['ruwe_tekst']!r})")

    # --- verkoop weigert €0 nog steeds ------------------------------------
    at.segmented_control(key="modus").set_value("VERKOOP").run()
    kies(at, "bulbasaur")
    at.button(key="vastleggen").click().run()
    check(len(rijen(engine)) == 7, "een verkoop van €0 blijft geweigerd")
    check(bool(at.error), "met een foutmelding erbij")
    at.button(key=knoppen(at, "weg_")[0].key).click().run()

    # --- dagtotaal: trades tellen als afspraken, niet als euro's ----------
    check(dagtotaal(at) == "Vandaag: verkoop €0 · trades: 3 (cash +€35)",
          f"drie trades, netto €75 − €40 + €0 = €35 ({dagtotaal(at)!r})")
    uitkomst(f"dagtotaal: {dagtotaal(at)}")

    # --- undo haalt de hele trade weg, cash-regel incluis -----------------
    at.segmented_control(key="modus").set_value("TRADE").run()
    check("2 regels" in at.button(key="undo").label,
          f"undo kondigt kaart + cash aan ({at.button(key='undo').label})")
    at.button(key="undo").click().run()
    at.button(key="undo_ja").click().run()
    r = rijen(engine)
    check(len(r) == 5, f"de laatste trade is in zijn geheel weg (kreeg {len(r)})")
    check(dagtotaal(at) == "Vandaag: verkoop €0 · trades: 2 (cash +€35)",
          f"dagtotaal telt nog twee trades ({dagtotaal(at)!r})")
    uitkomst(f"rijen na undo: {[(x['type'], float(x['bedrag'])) for x in r]}")

    # --- trades tellen niet mee als verkoopomzet --------------------------
    at.segmented_control(key="modus").set_value("VERKOOP").run()
    kies(at, "umbreon vmax")
    zet_prijs(at, 0, 100.0)
    at.button(key="vastleggen").click().run()
    check(dagtotaal(at) == "Vandaag: verkoop €100 · trades: 2 (cash +€35)",
          f"de verkoop staat los van de trades ({dagtotaal(at)!r})")
    uitkomst(f"dagtotaal: {dagtotaal(at)}")
    print()


def snelknoppen_tests(AppTest, db):
    """Snelknoppen voor hardlopers: verschijnen alleen bij precies één treffer, en
    één tik zet het product met zijn eigen prijs in het mandje."""
    import streamlit as st

    engine = maak_engine()
    db.get_engine = lambda: engine
    st.cache_resource.clear()
    st.cache_data.clear()

    print("\n" + "=" * 72)
    print("SNELKNOPPEN — hardlopers met één tik")
    print("=" * 72)

    at = AppTest.from_file(str(INVENTORY / "beurs_app.py"), default_timeout=60)
    at.run()

    labels = [b.label for b in snelknoppen(at)]
    check(len(labels) == 5, f"vijf van de zes snelknoppen resolven ({len(labels)})")
    check(all(x in " ".join(labels) for x in
              ("Prismatic", "Sleeved", "Tins", "Mega Dream", "Black Bolt")),
          f"de knoppen die eenduidig zijn staan er ({labels})")
    check(not any("Twilight" in lb for lb in labels),
          "een snelknop zonder kaart in de inventaris verschijnt niet")
    tekst = " ".join(m.value for m in at.markdown)
    check("Snelknop zonder eenduidige kaart: Twilight (0 treffers)" in tekst,
          "en wordt wél gemeld, zodat een verouderde lijst opvalt")
    check(any("€15" in lb for lb in labels) and any("€120" in lb for lb in labels),
          f"de prijs staat op de knop ({labels})")
    uitkomst(*[lb.replace("\n", "  ") for lb in labels])

    # --- één tik = in het mandje, met prijs -------------------------------
    knop = [b for b in snelknoppen(at) if "Prismatic" in b.label][0]
    at.button(key=knop.key).click().run()
    check(mandje_namen(at) == ["Prismatic booster"],
          f"één tik zet het product in het mandje ({mandje_namen(at)})")
    check(prijsvelden(at)[0].value == 15.0,
          f"met de comp-prijs erbij ({prijsvelden(at)[0].value})")

    rid = at.session_state["mandje"][0]["rid"]
    at.button(key=f"plus_{rid}").click().run()
    at.button(key=f"plus_{rid}").click().run()
    at.button(key="vastleggen").click().run()
    r = rijen(engine)
    check(len(r) == 1 and float(r[0]["bedrag"]) == 45.0 and r[0]["stuks"] == 3,
          f"3 × €15 = €45 in één regel ({[(float(x['bedrag']), x['stuks']) for x in r]})")
    check(r[0]["item_id"] is not None,
          "gekoppeld aan het echte item — dus de voorraad boekt af")
    uitkomst(toon(r[0]) + f"  stuks={r[0]['stuks']}")

    # --- prijs blijft overschrijfbaar bij onderhandelen -------------------
    knop = [b for b in snelknoppen(at) if "Mega Dream" in b.label][0]
    at.button(key=knop.key).click().run()
    zet_prijs(at, 0, 85.0)
    at.button(key="vastleggen").click().run()
    check(float(rijen(engine)[-1]["bedrag"]) == 85.0,
          "de voorgevulde prijs is gewoon te overschrijven")

    # --- uitverkochte hardloper mag, met melding --------------------------
    knop = [b for b in snelknoppen(at) if "Black Bolt" in b.label][0]
    at.button(key=knop.key).click().run()
    check("UITVERKOCHT" in " ".join(m.value for m in at.markdown),
          "een uitverkochte hardloper waarschuwt in het mandje")
    check(not at.button(key="vastleggen").disabled,
          "maar blokkeert niet — de voorraadstand loopt achter")
    at.button(key=knoppen(at, "weg_")[0].key).click().run()

    # --- ook naast een gezochte kaart te gebruiken -----------------------
    kies(at, "charizard")
    knop = [b for b in snelknoppen(at) if "Sleeved" in b.label][0]
    at.button(key=knop.key).click().run()
    check(len(at.session_state["mandje"]) == 2,
          "een snelknop schuift aan bij wat er al in het mandje ligt")
    check([p.value for p in prijsvelden(at)] == [120.0, 14.0],
          f"beide met hun eigen prijs ({[p.value for p in prijsvelden(at)]})")
    print()


def presets_tests(AppTest, db):
    """Prijs-presets en de onderhandeld-vlag: comp / comp −10% / handmatig, en de
    vlag die vanzelf goed staat."""
    import streamlit as st

    engine = maak_engine()
    db.get_engine = lambda: engine
    st.cache_resource.clear()
    st.cache_data.clear()

    print("\n" + "=" * 72)
    print("PRESETS — comp, comp −10%, handmatig, en de onderhandeld-vlag")
    print("=" * 72)

    at = AppTest.from_file(str(INVENTORY / "beurs_app.py"), default_timeout=60)
    at.run()

    # --- auto-vullen: overal waar een kaart binnenkomt --------------------
    kop("A.", "Staat de prijs overal meteen goed?",
        "zoekresultaat, snelknop en cm-terugval vullen alle drie voor; "
        "vrij invoeren kent geen prijs en begint op nul")
    kies(at, "umbreon vmax")
    check(prijsvelden(at)[0].value == 450.0, "via het zoekresultaat: comp €450")
    at.button(key=knoppen(at, "weg_")[0].key).click().run()

    at.button(key=[b for b in snelknoppen(at) if "Prismatic" in b.label][0].key).click().run()
    check(prijsvelden(at)[0].value == 15.0, "via een snelknop: comp €15")
    at.button(key=knoppen(at, "weg_")[0].key).click().run()

    kies(at, "pika van gogh")
    check(prijsvelden(at)[0].value == 852.0, "zonder comp valt hij terug op de cm-prijs")
    at.button(key=knoppen(at, "weg_")[0].key).click().run()

    at.text_input(key="vrij_naam").set_value("Onbekende promo").run()
    at.button(key="vrij_knop").click().run()
    check(prijsvelden(at)[0].value == 0.0, "vrij ingevoerd kent geen prijs: nul")
    check(not any(b.key == "preset_comp" for b in at.button),
          "en dan is er ook niets om vanaf te rekenen: geen presets")
    at.button(key=knoppen(at, "weg_")[0].key).click().run()
    uitkomst("zoekresultaat €450 · snelknop €15 · cm-terugval €852 · vrij €0")

    # --- presets -----------------------------------------------------------
    kop("B.", "Zakken de presets zoals verwacht?",
        "comp €450 → −10% = €405 → handmatig = €0 → comp = €450 terug")
    kies(at, "umbreon vmax")
    check(bool(at.button(key="preset_comp")), "de presetrij verschijnt bij een kaart met prijs")
    at.button(key="preset_korting").click().run()
    check(prijsvelden(at)[0].value == 405.0,
          f"−10% van €450 = €405 ({prijsvelden(at)[0].value})")
    at.button(key="preset_hand").click().run()
    check(prijsvelden(at)[0].value == 0.0, "handmatig maakt het veld leeg")
    at.button(key="preset_comp").click().run()
    check(prijsvelden(at)[0].value == 450.0, "comp zet 'm terug")
    uitkomst("€450 → −10% €405 → handmatig €0 → comp €450")

    # afronding op hele euro's
    at.button(key=knoppen(at, "weg_")[0].key).click().run()
    at.button(key=[b for b in snelknoppen(at) if "Sleeved" in b.label][0].key).click().run()
    at.button(key="preset_korting").click().run()
    check(prijsvelden(at)[0].value == 13.0,
          f"−10% van €14 = €12,60 → afgerond €13 ({prijsvelden(at)[0].value})")

    # over het hele mandje tegelijk
    kies(at, "charizard")
    at.button(key="preset_korting").click().run()
    check([p.value for p in prijsvelden(at)] == [13.0, 108.0],
          f"één tik zakt het hele mandje ({[p.value for p in prijsvelden(at)]})")

    # --- onderhandeld ------------------------------------------------------
    kop("C.", "Staat de onderhandeld-vlag vanzelf goed?",
        "afwijkend bedrag → vlag aan zonder tik; terug op comp → vlag uit; "
        "en met de hand te overrulen")
    check("✓ onderhandeld" in at.button(key="onderhandeld").label,
          f"na −10% staat de vlag aan zonder dat er iets is aangetikt "
          f"({at.button(key='onderhandeld').label})")
    at.button(key="preset_comp").click().run()
    check(at.button(key="onderhandeld").label == "onderhandeld",
          f"terug op comp: vlag uit ({at.button(key='onderhandeld').label})")
    at.button(key="onderhandeld").click().run()
    check("✓" in at.button(key="onderhandeld").label,
          "met de hand aan te zetten als de prijs toevallig op comp uitkomt")
    at.button(key="vastleggen").click().run()
    r = rijen(engine)
    check(len(r) == 2 and all(x["onderhandeld"] for x in r),
          f"de overrule landt op alle regels van de afspraak "
          f"({[bool(x['onderhandeld']) for x in r]})")
    uitkomst(*[toon(x) + f"  onderhandeld={bool(x['onderhandeld'])}" for x in r])

    # afgeleid, zonder tik
    kies(at, "umbreon vmax")
    zet_prijs(at, 0, 400.0)
    at.button(key="vastleggen").click().run()
    check(bool(rijen(engine)[-1]["onderhandeld"]),
          "een bedrag onder comp zet de vlag zonder tik")

    kies(at, "umbreon vmax")
    at.button(key="vastleggen").click().run()
    check(not rijen(engine)[-1]["onderhandeld"],
          "precies de comp-prijs is niet onderhandeld")

    # trade kent geen comp-prijs per kaart, dus ook geen vlag
    at.segmented_control(key="modus").set_value("TRADE").run()
    kies(at, "charizard")
    check(not any(b.key == "preset_comp" for b in at.button),
          "bij een trade zijn er geen presets — het geld hangt aan de afspraak")
    at.button(key="vastleggen").click().run()
    check(not any(x["onderhandeld"] for x in rijen(engine)[-2:]),
          "en traderegels dragen de vlag niet")
    print()


def nogmaals_tests(AppTest, db):
    """Nog een: dezelfde kaart tegen dezelfde prijs, zonder opnieuw te zoeken."""
    import streamlit as st

    engine = maak_engine()
    db.get_engine = lambda: engine
    st.cache_resource.clear()
    st.cache_data.clear()

    print("\n" + "=" * 72)
    print("NOG EEN — hetzelfde pakje aan de volgende klant")
    print("=" * 72)

    at = AppTest.from_file(str(INVENTORY / "beurs_app.py"), default_timeout=60)
    at.run()
    check(not any(b.key == "nogmaals_knop" for b in at.button),
          "vóór de eerste invoer is er niets om te herhalen")

    kop("A.", "Verkoop ik hetzelfde pakje aan vijf klanten zonder opnieuw te zoeken?",
        "5× tikken op NOG EEN + VASTLEGGEN geeft 5 losse regels van €12")
    at.button(key=[b for b in snelknoppen(at) if "Prismatic" in b.label][0].key).click().run()
    zet_prijs(at, 0, 12.0)          # afgedongen van €15
    at.button(key="vastleggen").click().run()
    check(at.session_state["bevestiging"] ==
          "✓ Vastgelegd — Prismatic booster · €12.00",
          f"bevestiging: {at.session_state['bevestiging']}")
    check(bool(at.button(key="nogmaals_knop")), "de nog-een-knop staat er na de invoer")
    check("Prismatic booster" in at.button(key="nogmaals_knop").label,
          f"met de naam erop ({at.button(key='nogmaals_knop').label})")

    for _ in range(4):
        at.button(key="nogmaals_knop").click().run()
        check(prijsvelden(at)[0].value == 12.0,
              "de afgedongen prijs komt terug, niet de comp-prijs")
        at.button(key="vastleggen").click().run()

    r = rijen(engine)
    check(len(r) == 5, f"vijf losse verkopen (kreeg {len(r)})")
    check([float(x["bedrag"]) for x in r] == [12.0] * 5,
          f"allemaal €12 ({[float(x['bedrag']) for x in r]})")
    check(all(x["stuks"] == 1 for x in r), "elk één stuk — 'nog een' is er één")
    check(all(x["group_id"] is None for x in r),
          "vijf losse klanten, dus geen groep")
    check(all(x["onderhandeld"] for x in r),
          "en alle vijf als onderhandeld gemarkeerd (€12 tegen comp €15)")
    check(at.session_state["dubbel_vraag"] is None,
          "geen dubbel-vraag onderweg: via NOG EEN is de herhaling gewild")
    uitkomst(f"{len(r)} regels van €12,00 · "
             f"tikken: 1 snelknop + 1 prijs + 5× (nog een + vastleggen)")

    # ... maar een gewone dubbele tik vraagt nog steeds wel ------------------
    at.button(key=[b for b in snelknoppen(at) if "Prismatic" in b.label][0].key).click().run()
    zet_prijs(at, 0, 12.0)
    at.button(key="vastleggen").click().run()
    check(at.session_state["dubbel_vraag"] is not None,
          "dezelfde kaart opnieuw ingetikt vraagt wél om bevestiging")
    at.button(key="dubbel_nee").click().run()
    at.button(key=knoppen(at, "weg_")[0].key).click().run()
    check(len(rijen(engine)) == 5, "en die is niet geboekt")

    # --- de knop verdwijnt als er iets in het mandje ligt ------------------
    kies(at, "charizard")
    check(not any(b.key == "nogmaals_knop" for b in at.button),
          "met iets in het mandje verdwijnt de knop — hij hoort er niets bij te gooien")
    at.button(key=knoppen(at, "weg_")[0].key).click().run()
    check(bool(at.button(key="nogmaals_knop")), "en komt terug zodra het mandje leeg is")

    # --- undo haalt 'm weg -------------------------------------------------
    at.button(key="undo").click().run()
    at.button(key="undo_ja").click().run()
    check(not any(b.key == "nogmaals_knop" for b in at.button),
          "wat je net hebt teruggedraaid krijg je niet met één tik terug")
    check(len(rijen(engine)) == 4, "en de undo heeft gewoon gewerkt")

    # --- een heel mandje herhalen -----------------------------------------
    kies(at, "umbreon vmax")
    kies(at, "charizard")
    at.button(key="vastleggen").click().run()
    check("2 kaarten" in at.button(key="nogmaals_knop").label,
          f"een mandje van twee heet ook zo ({at.button(key='nogmaals_knop').label})")
    at.button(key="nogmaals_knop").click().run()
    check(mandje_namen(at) == ["Umbreon VMAX (Alternate Art)",
                               "Charizard ex (Special Illustration)"],
          f"beide kaarten komen terug ({mandje_namen(at)})")

    # --- na een trade geen herhaling --------------------------------------
    at.button(key=knoppen(at, "weg_")[0].key).click().run()
    at.button(key=knoppen(at, "weg_")[0].key).click().run()
    at.segmented_control(key="modus").set_value("TRADE").run()
    kies(at, "charizard")
    at.button(key="vastleggen").click().run()
    check(not any(b.key == "nogmaals_knop" for b in at.button),
          "een trade herhaal je niet met één tik — dezelfde kaarten nóg eens weggeven")
    print()


def scenarios(AppTest, db):
    """Scenario's die de vragen uit de opdracht beantwoorden, met een leesbaar
    'verwacht → resultaat' per stap."""
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

    # --- 1. inkoop is er niet meer ----------------------------------------
    kop("1.", "Kan er nog inkoop geboekt worden?",
        "geen INKOOP-modus, geen soort-keuze bij vrij invoeren, geen rekenhulp")
    modi = [sc.key for sc in at.segmented_control]
    keuzes = at.segmented_control(key="modus").options
    check("INKOOP" not in keuzes, f"scenario 1: INKOOP is geen modus meer ({keuzes})")
    check("inkoop_modus" not in modi,
          f"scenario 1: geen per-kaart/totaal-keuze voor inkoop meer ({modi})")
    check("vrij_modus" not in modi, "scenario 1: vrij invoeren kent geen soort meer")
    check(not knoppen(at, "pct_"), "scenario 1: de inkoop-rekenhulp is weg")
    uitkomst(f"modi op het scherm: {keuzes}",
             f"dagtotaal: {dagtotaal(at)}")

    # --- 2. drie kaarten aan één klant ------------------------------------
    kop("2.", "Kan ik drie kaarten aan één klant in één keer vastleggen?",
        "3 regels met dezelfde group_id, elk met item_id en stuks, samen €585")
    kies(at, "umbreon vmax")        # 450
    kies(at, "charizard")           # 120
    kies(at, "umbreon v", "189")    # 45
    check(len(at.session_state["mandje"]) == 3, "scenario 2: drie kaarten in het mandje")
    at.button(key="vastleggen").click().run()
    r = rijen(engine)
    groepen = {x["group_id"] for x in r}
    check(len(r) == 3 and len(groepen) == 1 and None not in groepen,
          f"scenario 2: drie regels in één groep ({len(r)} regels, {groepen})")
    check(sum(float(x["bedrag"]) for x in r) == 615.0,
          f"scenario 2: samen €615 ({sum(float(x['bedrag']) for x in r)})")
    check(all(x["item_id"] and x["stuks"] for x in r),
          "scenario 2: elke regel houdt item_id + stuks, dus elk boekt af")
    uitkomst(*[toon(x) for x in r])

    # --- 3. onderhandeld totaalbedrag -------------------------------------
    kop("3.", "En als de klant afdingt tot één rond bedrag?",
        "€600 voor comp €450 + €120 → naar rato verdeeld, som exact €600")
    kies(at, "umbreon vmax")
    kies(at, "charizard")
    at.segmented_control(key="prijs_modus").set_value("Totaalprijs").run()
    at.number_input(key="mandje_totaal").set_value(600.0).run()
    at.button(key="vastleggen").click().run()
    r = rijen(engine)[3:]
    bedragen = [float(x["bedrag"]) for x in r]
    check(sum(bedragen) == 600.0, f"scenario 3: som is exact €600 ({sum(bedragen)})")
    check(r[0]["flag"] == "mandje-totaal", "scenario 3: als onderhandeld totaal gemarkeerd")
    uitkomst(f"comp €450 + €120 = €570 → afgesproken €600 → {bedragen}",
             *[toon(x) for x in r])

    # --- 4. dagtotaal + undo ----------------------------------------------
    kop("4.", "Klopt het dagtotaal, en draait undo mijn eigen laatste afspraak terug?",
        "eigen invoer telt direct mee; collega boekt ná mij; undo pakt míjn "
        "hele mandje van €600 en laat hun €50 staan")
    voor_collega = dagtotaal(at)
    check(voor_collega == "Vandaag: verkoop €1,215 · trades: 0",
          f"scenario 4: eigen invoer telt direct mee ({voor_collega!r})")

    at2 = AppTest.from_file(str(INVENTORY / "beurs_app.py"), default_timeout=60)
    at2.run()
    kies(at2, "umbreon v", "189")
    zet_prijs(at2, 0, 50.0)
    at2.button(key="vastleggen").click().run()

    # Bewust gedrag: de balk leest uit een cache van 20 s, dus de invoer van een
    # collega hoeft niet meteen zichtbaar te zijn. Je eigen invoer wél — die
    # breekt de cache open.
    at.run()
    check(dagtotaal(at) == voor_collega,
          f"scenario 4: invoer van een collega volgt binnen de cache-TTL "
          f"({dagtotaal(at)!r})")
    uitkomst(f"direct na eigen invoer  : {voor_collega}",
             f"vlak na invoer collega  : {dagtotaal(at)}  "
             f"(cache van 20 s — nog niet ververst)")

    at.button(key="undo").click().run()
    check(any("600" in w.value for w in at.warning),
          f"scenario 4: bevestiging noemt de eigen laatste afspraak "
          f"({[w.value for w in at.warning]})")
    at.button(key="undo_ja").click().run()
    r = rijen(engine)
    bedragen = [float(x["bedrag"]) for x in r]
    check(len(r) == 4 and 50.0 in bedragen,
          f"scenario 4: beide regels van €600 weg, €50 van de collega blijft "
          f"({bedragen})")
    check(dagtotaal(at) == "Vandaag: verkoop €665 · trades: 0",
          f"scenario 4: dagtotaal loopt terug ({dagtotaal(at)!r})")
    uitkomst(f"bevestiging             : {at.session_state['bevestiging']}",
             f"rijen na undo           : {bedragen}",
             f"dagtotaal na undo       : {dagtotaal(at)}")
    print()


def main():
    engine = maak_engine()

    import db
    db.get_engine = lambda: engine

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(INVENTORY / "beurs_app.py"), default_timeout=60)
    at.run()
    check(not at.exception, "app start zonder exception")

    # --- geen naamkeuze: meteen invoeren ----------------------------------
    check("wie" not in at.session_state, "geen 'wie'-sessiestatus meer")
    check(not knoppen(at, "wie_"), "geen naamkeuze-knoppen meer")
    check(bool(at.text_input(key="zoekterm")), "zoekbalk staat er direct bij het openen")

    # --- event aangemaakt -------------------------------------------------
    with engine.connect() as c:
        ev = c.execute(text("SELECT id, name, event_date, type FROM events")).fetchall()
    check(len(ev) == 1 and ev[0][1] == "Cardmaniacs Nijmegen 15-16 augustus 2026",
          f"event aangemaakt: {ev}")

    # --- dagtotaal: leeg bij de start -------------------------------------
    check(dagtotaal(at) == "Vandaag: verkoop €0 · trades: 0",
          f"dagtotaal begint op nul ({dagtotaal(at)!r})")
    check(at.segmented_control(key="modus").options == ["VERKOOP", "TRADE"],
          f"twee modi: verkoop en trade "
          f"({at.segmented_control(key='modus').options})")

    # --- zoeken -----------------------------------------------------------
    at.text_input(key="zoekterm").set_value("umbreon").run()
    picks = knoppen(at, "pick_")
    check(len(picks) == 2, f"zoeken 'umbreon' geeft 2 treffers (kreeg {len(picks)})")
    check(any("450.00" in b.label for b in picks), "comp_prijs zichtbaar in resultaat")
    check(any("EVS" in b.label for b in picks), "set_code zichtbaar in resultaat")

    at.text_input(key="zoekterm").set_value("umbreon vmax").run()
    check(len(knoppen(at, "pick_")) == 1, "meerdere woorden filteren scherper")

    at.text_input(key="zoekterm").set_value("pokemon center").run()
    check(len(knoppen(at, "pick_")) == 1, "accent-ongevoelig zoeken (pokemon → Pokémon)")

    # Gelijknamige kaarten zonder set_code: alleen te scheiden op staat/grade.
    at.text_input(key="zoekterm").set_value("blastoise").run()
    labels = [b.label for b in knoppen(at, "pick_")]
    check(not any("nan" in lb.lower() for lb in labels),
          f"geen 'nan' in resultaatregels bij lege set_code ({labels})")
    check(any("PSA 9" in lb for lb in labels) and any("GD" in lb for lb in labels),
          f"staat/grade scheidt gelijknamige kaarten ({labels})")
    check(labels and "300.00" in labels[0],
          f"duurste variant staat bovenaan bij gelijke naam ({labels})")

    # --- voorraad-indicator bij het zoekresultaat -------------------------
    at.text_input(key="zoekterm").set_value("pikachu promo").run()
    labels = [b.label for b in knoppen(at, "pick_")]
    check(labels and "UITVERKOCHT" in labels[0],
          f"voorraad 0 toont UITVERKOCHT ({labels})")

    # --- slabs/sealed zonder comp_prijs: terugval op de cm-prijs -----------
    at.text_input(key="zoekterm").set_value("pika van gogh").run()
    labels = [b.label for b in knoppen(at, "pick_")]
    check(labels and "852.00" in labels[0],
          f"slab zonder comp toont de cm-prijs ({labels})")
    check(labels and "cm" in labels[0],
          f"cm-prijs is als zodanig gelabeld, niet als comp ({labels})")
    at.text_input(key="zoekterm").set_value("moltres").run()
    labels = [b.label for b in knoppen(at, "pick_")]
    check(labels and "330.00" in labels[0] and "€ ?" not in labels[0],
          f"sealed zonder comp toont de cm-prijs ({labels})")
    at.text_input(key="zoekterm").set_value("151 etb").run()
    labels = [b.label for b in knoppen(at, "pick_")]
    check(labels and "500.00" in labels[0] and " cm" not in labels[0],
          f"comp gaat vóór cm en krijgt geen cm-label ({labels})")

    kies(at, "shining gyarados")
    check(prijsvelden(at)[0].value == 2300.00,
          f"de cm-prijs wordt voorgevuld (kreeg {prijsvelden(at)[0].value})")
    at.button(key=knoppen(at, "weg_")[0].key).click().run()

    # --- vastleggen (overschreven bedrag) ---------------------------------
    kies(at, "umbreon vmax")
    check(at.session_state["mandje"][0]["naam"] == "Umbreon VMAX (Alternate Art)",
          "kaart staat in het mandje")
    check(prijsvelden(at)[0].value == 450.00,
          f"prijs voorgevuld met comp_prijs (kreeg {prijsvelden(at)[0].value})")
    check(not knoppen(at, "pct_"), "geen inkoop-rekenhulp meer in de app")

    zet_prijs(at, 0, 430.0)
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
        check(t["ruwe_tekst"] == "Umbreon VMAX (Alternate Art)",
              "ruwe_tekst = officiële naam")
        check(t["code"] == "EVS 215", f"code van de gezochte kaart bewaard ({t['code']!r})")
        check(t["event_id"] == ev[0][0], "gekoppeld aan het beurs-event")
        check(t["datum"] is not None and t["tijd"] is not None, "datum + tijd gevuld")
        check(t["group_id"] is None, "losse verkoop van één kaart krijgt geen group_id")

    # --- reset + bevestiging ---------------------------------------------
    check(at.session_state["mandje"] == [], "mandje leeg na vastleggen")
    bev = at.session_state["bevestiging"]
    check(bev == "✓ Vastgelegd — Umbreon VMAX (Alternate Art) · €430.00",
          f"bevestiging noemt wat en hoeveel: {bev}")
    check(any("tc-ok" in m.value and bev in m.value for m in at.markdown),
          "bevestiging staat als vervagend blok op het scherm")
    check(dagtotaal(at) == "Vandaag: verkoop €430 · trades: 0",
          f"dagtotaal ververst na de verkoop ({dagtotaal(at)!r})")

    # --- tweede invoer direct erna ---------------------------------------
    kies(at, "charizard")
    check(prijsvelden(at)[0].value == 120.0, "tweede kaart vult ook voor")
    zet_prijs(at, 0, 60.0)
    at.button(key="vastleggen").click().run()
    r = rijen(engine)
    check(len(r) == 2, f"tweede invoer direct erna werkt (kreeg {len(r)} rijen)")
    check(dagtotaal(at) == "Vandaag: verkoop €490 · trades: 0",
          f"dagtotaal telt de tweede verkoop mee ({dagtotaal(at)!r})")

    # --- kaart zonder prijs ------------------------------------------------
    kies(at, "bulbasaur")
    check(prijsvelden(at)[0].value == 0.0, "kaart zonder prijs begint op nul")

    # --- bedrag 0 wordt geweigerd ----------------------------------------
    at.button(key="vastleggen").click().run()
    check(len(rijen(engine)) == 2, "bedrag €0 wordt niet weggeschreven")
    check(bool(at.error), "foutmelding getoond bij bedrag €0")
    check(len(at.session_state["mandje"]) == 1, "invoer blijft staan na weigering")
    at.button(key=knoppen(at, "weg_")[0].key).click().run()

    # --- vangnet: vrij invoeren, mét losse code --------------------------
    at.text_input(key="vrij_naam").set_value("Japanse Eevee Heroes booster box").run()
    at.text_input(key="vrij_code").set_value("s6a").run()
    at.button(key="vrij_knop").click().run()
    check(len(at.session_state["mandje"]) == 1, "vrije invoer komt in het mandje")
    check(at.text_input(key="vrij_naam").value == "",
          "naamveld leeg na toevoegen — meteen door")
    zet_prijs(at, 0, 275.0)
    at.button(key="vastleggen").click().run()
    r = rijen(engine)
    check(len(r) == 3, f"vrije invoer weggeschreven (kreeg {len(r)} rijen)")
    if len(r) == 3:
        v = r[2]
        check(v["item_id"] is None, "vrije invoer heeft item_id NULL")
        check(v["flag"] == "vrij ingevoerd", "flag = 'vrij ingevoerd'")
        check(v["ruwe_tekst"] == "Japanse Eevee Heroes booster box",
              "ruwe_tekst = ingetypte naam")
        check(v["code"] == "s6a", f"losse code in de code-kolom ({v['code']!r})")
        check(v["afzender"] == "TC", "vrije invoer heeft afzender 'TC'")
        check(v["type"] == "verkoop", "vrije invoer is altijd een verkoop")
    check(not any(sc.key == "vrij_modus" for sc in at.segmented_control),
          "geen inkoop/verkoop-keuze meer bij vrij invoeren")

    # --- laatste invoeren als compacte regels -----------------------------
    log = [m.value for m in at.markdown if 'class="tc-log"' in m.value]
    check(len(log) == 1, "compacte lijst met laatste invoeren wordt getoond")
    if log:
        check(log[0].count("<br>") == 2, f"3 regels in de lijst ({log[0].count('<br>') + 1})")
        check("+€430" in log[0] and "+€60" in log[0],
              f"verkopen met + ({log[0][:120]})")
    check(not at.dataframe, "geen tabel meer op het invoerscherm")

    # --- twee sessies tegelijk -------------------------------------------
    at2 = AppTest.from_file(str(INVENTORY / "beurs_app.py"), default_timeout=60)
    at2.run()
    check(not at2.exception, "tweede sessie start zonder exception")
    kies(at2, "umbreon v", "189")
    zet_prijs(at2, 0, 40.0)
    at2.button(key="vastleggen").click().run()
    r = rijen(engine)
    check(len(r) == 4, f"invoer vanuit tweede sessie werkt (kreeg {len(r)} rijen)")
    if len(r) == 4:
        check(r[3]["afzender"] == "TC" and float(r[3]["bedrag"]) == 40.0,
              "tweede sessie schrijft naar dezelfde database")

    # --- undo: alleen de eigen laatste invoer -----------------------------
    kies(at2, "blastoise")
    zet_prijs(at2, 0, 25.0)
    at2.button(key="vastleggen").click().run()
    check(len(rijen(engine)) == 5, "collega boekt er nog een verkoop tussendoor")

    at.run()
    check(bool(at.button(key="undo")), "undo-knop staat er na een eigen invoer")
    at.button(key="undo").click().run()
    check(any("Laatste invoer verwijderen" in w.value and "275" in w.value
              for w in at.warning),
          f"eerst bevestiging vragen ({[w.value for w in at.warning]})")
    check(len(rijen(engine)) == 5, "vragen alleen — nog niets verwijderd")

    at.button(key="undo_nee").click().run()
    check(len(rijen(engine)) == 5, "'nee' laat de invoer staan")
    check(not at.warning, "bevestiging weg na 'nee'")

    at.button(key="undo").click().run()
    at.button(key="undo_ja").click().run()
    r = rijen(engine)
    check(len(r) == 4, f"undo verwijdert precies één rij (kreeg {len(r)} rijen)")
    check([float(x["bedrag"]) for x in r] == [430.0, 60.0, 40.0, 25.0],
          f"eigen vrije invoer weg, die van de collega blijft "
          f"({[float(x['bedrag']) for x in r]})")
    check(dagtotaal(at) == "Vandaag: verkoop €555 · trades: 0",
          f"dagtotaal loopt terug na undo ({dagtotaal(at)!r})")

    at.button(key="undo").click().run()
    at.button(key="undo_ja").click().run()
    r = rijen(engine)
    check([float(x["bedrag"]) for x in r] == [430.0, 40.0, 25.0],
          f"tweede undo pakt de eigen vorige invoer (€60), niet die van de collega "
          f"({[float(x['bedrag']) for x in r]})")

    mandje_tests(AppTest, db)
    trade_tests(AppTest, db)
    snelknoppen_tests(AppTest, db)
    presets_tests(AppTest, db)
    nogmaals_tests(AppTest, db)
    nieuwe_features(AppTest, db)
    kern_snel(AppTest, db)
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
