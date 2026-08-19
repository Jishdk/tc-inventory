"""TC beurs — snelle invoer voor op de beursvloer.

Het team legt op de telefoon verkopen en inkopen vast. Elke invoer is één losse
INSERT in `transactions`, gekoppeld aan één event. Er wordt niets afgeboekt uit
`items` — alleen transacties vullen.

    streamlit run beurs_app.py

Ontwerpkeuzes:
- Eén gedeelde gebruiker: `afzender` staat vast op "TC". Geen naamkeuze, dat is
  een scherm minder tussen jou en de invoer.
- De items-tabel wordt één keer opgehaald en in cache gehouden; zoeken gebeurt
  lokaal. Zo is zoeken instant en kost het geen netwerk op een slechte beursvloer.
- Schrijven gebeurt zonder automatische retry (pool_pre_ping vangt dode
  connecties al af). Faalt het toch, dan blijft de invoer staan met een
  duidelijke foutmelding — nooit stil falen.
- VERKOOP is de snelste route en blijft ongewijzigd: zoek → tik → bedrag → weg.
  De rekenhulp en de stapel-modus zitten alléén in INKOOP, waar de deal al rond
  is en het om het vastleggen van het betaalde bedrag gaat.
"""

from __future__ import annotations

import html
import math
import os
import sys
import unicodedata
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

# ------------------------------------------------------------------ constanten

EVENT_NAAM = "Cardmaniacs Nijmegen 15-16 augustus 2026"
EVENT_DATUM = date(2026, 8, 15)
EVENT_LOCATIE = "Nijmegen"
BEURSDAGEN = {date(2026, 8, 15), date(2026, 8, 16)}

AFZENDER = "TC"          # één gedeelde gebruiker
MODI = ["VERKOOP", "INKOOP"]
INKOOP_MODI = ["Per kaart", "Totaal"]
PERCENTAGES = (70, 75, 80)   # gebruikelijke inkooppercentages van de comp-prijs
MAX_RESULTATEN = 10

st.set_page_config(page_title="TC Beurs", page_icon="🃏", layout="centered")


def secret(naam: str) -> str | None:
    """Waarde uit st.secrets (Streamlit Cloud) of uit de omgeving/.env."""
    try:
        if naam in st.secrets:
            return str(st.secrets[naam])
    except Exception:  # geen secrets.toml aanwezig — prima
        pass
    return os.getenv(naam)


# Streamlit Cloud levert secrets, niet .env — zet 'm door zodat src/db.py 'm vindt.
if secret("SUPABASE_DB_URL"):
    os.environ["SUPABASE_DB_URL"] = secret("SUPABASE_DB_URL")

from db import get_engine  # noqa: E402  (na het zetten van SUPABASE_DB_URL)

if not os.getenv("SUPABASE_DB_URL"):
    st.error("SUPABASE_DB_URL ontbreekt. Lokaal: zet 'm in `inventory/.env`. "
             "Op Streamlit Cloud: Settings → Secrets.", icon="🔑")
    st.stop()

# ------------------------------------------------------------------ mobiel-css
# Streamlit hangt aan elk element met een key een class `st-key-<key>`; daarmee
# zijn de zoekresultaten (key `pick_<id>`) los te stylen van de grote CTA.
# Dit blok wordt pas verderop gerenderd, samen met de kleuren van de actieve
# modus — één <style>-element in plaats van twee losse (die ruimte innemen).

BASIS_CSS = """
  /* Streamlit-balk weg: scheelt ruis én schermruimte op een telefoon */
  header[data-testid="stHeader"] {display: none;}
  .block-container {padding-top: 1.5rem; padding-bottom: 4rem; max-width: 34rem;}
  div[data-testid="stVerticalBlock"] {gap: .9rem;}

  /* knoppen: ruime tap-targets */
  .stButton > button {min-height: 3.25rem; border-radius: .75rem;}
  div[data-testid="stSegmentedControl"] button {min-height: 3.25rem;
      font-size: 1.05rem; font-weight: 600;}

  /* Streamlit stapelt kolommen onder een telefoonbreedte; hier moeten ze juist
     náást elkaar blijven (percentage-knoppen, stapelregels, ja/nee). */
  div[data-testid="stHorizontalBlock"] {flex-wrap: nowrap; gap: .5rem;}
  div[data-testid="stColumn"] {min-width: 0 !important;}

  /* anker-icoontje naast de kaartnaam weg — het is geen webpagina */
  [data-testid="stHeaderActionElements"] {display: none;}

  /* dagtotaal: één rustige regel, geen blok dat de invoer wegdrukt */
  .tc-dag {background: rgba(128,128,128,.12); border-radius: .6rem;
      padding: .45rem .7rem; margin-bottom: .1rem; text-align: center;
      font-size: .92rem; opacity: .8; line-height: 1.4;}
  .tc-dag b {font-weight: 700; opacity: 1;}

  /* per kaart / totaal: duidelijk ondergeschikt aan de hoofdtoggle */
  .st-key-inkoop_modus div[data-testid="stSegmentedControl"] button {
      min-height: 2.4rem; font-size: .92rem; font-weight: 500;}

  /* zoekbalk: prominent */
  .st-key-zoekterm input, .st-key-stapel_zoek input {
      font-size: 1.25rem !important; padding: .9rem .75rem !important;}

  /* zoekresultaten: naam vet, daaronder klein set + staat + prijs */
  .st-key-resultaten div[data-testid="stVerticalBlock"],
  .st-key-stapel_resultaten div[data-testid="stVerticalBlock"] {gap: .45rem;}
  [class*="st-key-pick_"] button, [class*="st-key-add_"] button {
      min-height: 3.5rem; padding: .6rem .9rem;}
  [class*="st-key-pick_"] button > div, [class*="st-key-add_"] button > div {
      width: 100%; justify-content: flex-start;}
  [class*="st-key-pick_"] button p, [class*="st-key-add_"] button p {
      white-space: pre-line; text-align: left;
      line-height: 1.35; font-size: 1.05rem; font-weight: 600; margin: 0;}
  [class*="st-key-pick_"] button p span, [class*="st-key-add_"] button p span {
      font-size: .82rem; font-weight: 400;}

  /* rekenhulp (inkoop): kleine percentage-knoppen naast een vrij %-veld */
  .tc-comp {font-size: .9rem; opacity: .7; margin: 0;}
  [class*="st-key-pct_"] button {min-height: 2.9rem; padding: 0;
      font-size: 1rem; font-weight: 600;}
  .st-key-eigen_pct button {display: none;}   /* +/- steppers weg: te smal */
  .st-key-eigen_pct input {text-align: center; font-size: 1rem;
      padding: .7rem .3rem !important;}

  /* stapel: elke regel is zelf de weg-knop — links uitgelijnd, één regel hoog */
  [class*="st-key-weg_"] button {min-height: 2.8rem; padding: .4rem .8rem;
      font-weight: 400;}
  [class*="st-key-weg_"] button > div {width: 100%; justify-content: flex-start;}
  [class*="st-key-weg_"] button p {margin: 0; font-size: 1rem; text-align: left;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;}
  .st-key-stapel_vrij_knop button {min-height: 3.1rem; font-size: .95rem;
      font-weight: 600; padding: 0;}
  .tc-teller {font-size: .9rem; font-weight: 600; opacity: .6;
      margin: .3rem 0 -.35rem; letter-spacing: .02em;}

  /* bedrag: het getal is het belangrijkste op het scherm */
  .st-key-bedrag input, .st-key-totaal_bedrag input {font-size: 2rem !important;
      font-weight: 700; text-align: right; padding: .6rem .75rem !important;}
  .st-key-bedrag [data-testid="stNumberInputContainer"]::before,
  .st-key-totaal_bedrag [data-testid="stNumberInputContainer"]::before,
  .st-key-vrij_bedrag [data-testid="stNumberInputContainer"]::before {
      content: "€"; align-self: center; padding-left: .8rem; font-weight: 700;
      opacity: .45;}
  .st-key-bedrag [data-testid="stNumberInputContainer"]::before,
  .st-key-totaal_bedrag [data-testid="stNumberInputContainer"]::before {
      font-size: 1.6rem;}

  /* VASTLEGGEN: grote knop onderaan */
  .st-key-vastleggen button, .st-key-stapel_vastleggen button {
      min-height: 4rem; font-size: 1.2rem; font-weight: 700; letter-spacing: .03em;}

  /* bevestiging: groot, kort, vervaagt vanzelf — en klapt daarna dicht, zodat
     er geen gat achterblijft midden op het invoerscherm */
  @keyframes tc-fade {
      0%, 65% {opacity: 1;}
      99% {opacity: 0; padding: .9rem 1rem; max-height: 8rem;}
      100% {opacity: 0; visibility: hidden; max-height: 0; padding: 0;
            margin: 0; border-width: 0;}}
  .tc-ok {animation: tc-fade 6s ease-in forwards; background: rgba(33,195,84,.14);
      border-left: .35rem solid rgb(33,195,84); border-radius: .6rem;
      padding: .9rem 1rem; font-size: 1.25rem; font-weight: 700; line-height: 1.3;
      margin-bottom: .5rem; overflow: hidden;}

  /* terzijdes (geen beursdag): mag je gerust over het hoofd zien */
  .tc-note {font-size: .72rem; opacity: .4; text-align: center; margin: -.4rem 0 0;}

  /* vangnet: lichte rand, het is een uitwijk en geen hoofdroute */
  [data-testid="stExpander"] details {border-color: rgba(128,128,128,.22);}
  [data-testid="stExpander"] summary {font-size: .9rem; opacity: .75;}

  /* laatste invoeren: compacte regels in plaats van een tabel */
  .tc-log {font-size: .85rem; opacity: .7; line-height: 1.9;}
  .tc-log b {font-weight: 600;}
  .st-key-undo button, .st-key-undo_ja button, .st-key-undo_nee button {
      min-height: 2.6rem; font-size: .9rem;}
"""

# ------------------------------------------------------------------ database


@st.cache_resource
def engine():
    return get_engine()


def lees(sql: str, params: dict | None = None) -> pd.DataFrame:
    """Lezen mét één retry: bij netwerkhikjes is opnieuw proberen veilig."""
    for poging in (1, 2):
        try:
            with engine().connect() as conn:
                return pd.read_sql(text(sql), conn, params=params or {})
        except SQLAlchemyError:
            if poging == 2:
                raise
            engine().dispose()
    raise RuntimeError("onbereikbaar")


UPSERT_EVENT = text("""
INSERT INTO events (name, event_date, location, type)
VALUES (:name, :event_date, :location, 'beurs')
ON CONFLICT (name) DO UPDATE SET
    event_date = EXCLUDED.event_date, location = EXCLUDED.location
RETURNING id
""")

INSERT_TX = text("""
INSERT INTO transactions (
    event_id, item_id, datum, tijd, kanaal, type, bedrag, afzender, flag,
    ruwe_tekst, code, stuks
) VALUES (
    :event_id, :item_id, :datum, :tijd, :kanaal, :type, :bedrag, :afzender,
    :flag, :ruwe_tekst, :code, :stuks
)
RETURNING id
""")

DELETE_TX = text("DELETE FROM transactions WHERE id = :id AND event_id = :event_id")


@st.cache_resource(show_spinner="Event ophalen…")
def event_id() -> int:
    with engine().begin() as conn:
        return conn.execute(UPSERT_EVENT, {
            "name": EVENT_NAAM, "event_date": EVENT_DATUM, "location": EVENT_LOCATIE,
        }).scalar()


TEKSTKOLOMMEN = ["onze_naam", "officiele_naam", "code", "set_code", "categorie",
                 "staat", "grade"]


@st.cache_data(ttl=600, show_spinner="Kaarten laden…")
def laad_items() -> pd.DataFrame:
    df = lees("""
        SELECT id, onze_naam, officiele_naam, code, set_code, categorie, staat,
               grade, comp_prijs, prijs_cm, aantal
        FROM items ORDER BY officiele_naam NULLS LAST, onze_naam
    """)
    # Voorraad kan negatief zijn (er is meer verkocht dan de inventaris wist);
    # dat is informatie, geen fout, dus niet afkappen op nul.
    df["aantal"] = pd.to_numeric(df["aantal"], errors="coerce").fillna(0).astype(int)
    for kolom in ("comp_prijs", "prijs_cm"):
        df[kolom] = pd.to_numeric(df[kolom], errors="coerce")
    # Werkprijs: comp waar die er is, anders de Cardmarket/eBay-prijs. Slabs en
    # een deel van de sealed hebben namelijk alléén prijs_cm — zonder deze
    # terugval staat er "€ ?" bij juist de duurste voorraad.
    df["prijs"] = df["comp_prijs"].where(df["comp_prijs"].notna(), df["prijs_cm"])
    df["prijs_bron"] = df["comp_prijs"].notna().map({True: "comp", False: "cm"})
    df.loc[df["prijs"].isna(), "prijs_bron"] = None
    # Lege tekstvelden naar None: pandas' NaN is truthy, dus `x or y` zou anders
    # "nan" op het scherm zetten (187 items hebben geen set_code).
    for kol in TEKSTKOLOMMEN:
        df[kol] = df[kol].astype(object).where(df[kol].notna() & (df[kol] != ""), None)
    df["naam"] = df["officiele_naam"].where(df["officiele_naam"].notna(), df["onze_naam"])
    df["zoek"] = df[["onze_naam", "officiele_naam", "code", "set_code"]].apply(
        lambda r: normaliseer(" ".join(x for x in r if x)), axis=1)
    return df


def kenmerk(rij) -> str:
    """Set-code (of los codenummer) — het veld dat gelijknamige kaarten scheidt."""
    return rij["set_code"] or rij["code"] or ""


def conditie(rij) -> str:
    """Staat/grade: onderscheidt een GD Charizard van een NM Charizard."""
    return " ".join(x for x in (rij["staat"], rij["grade"]) if x)


def voorraad_tekst(aantal: int) -> str:
    """Kort label voor bij een zoekresultaat: zie je dat je de laatste verkoopt?"""
    n = int(aantal)
    if n <= 0:
        return "UITVERKOCHT"
    return f"{n} op voorraad"


def voorraad_klasse(aantal: int) -> str:
    """Kleurklasse: rood bij niets meer, oranje bij de laatste, anders rustig."""
    n = int(aantal)
    if n <= 0:
        return "tc-op"
    return "tc-laatste" if n == 1 else "tc-voorraad"


@st.cache_data(ttl=20, show_spinner=False)
def laatste_transacties(eid: int, versie: int) -> pd.DataFrame:
    """`versie` telt op na elke eigen invoer en breekt zo de cache open — géén
    underscore-prefix, want die sluit een argument uit van de cache-sleutel."""
    return lees("""
        SELECT t.id, t.item_id, t.datum, t.tijd, t.type, t.bedrag, t.afzender,
               t.ruwe_tekst, t.flag, t.stuks
        FROM transactions t WHERE t.event_id = :eid
        ORDER BY t.id DESC LIMIT 10
    """, {"eid": eid})


@st.cache_data(ttl=20, show_spinner=False)
def dagtotalen(eid: int, dag: date, versie: int) -> dict[str, float]:
    """Som per type voor één dag. `versie` breekt de cache open na elke invoer,
    zodat de balk bovenaan meteen meeloopt met wat je net hebt vastgelegd."""
    df = lees("""
        SELECT t.type, COALESCE(SUM(t.bedrag), 0) AS som
        FROM transactions t WHERE t.event_id = :eid AND t.datum = :dag
        GROUP BY t.type
    """, {"eid": eid, "dag": dag})
    return {str(r["type"]): float(r["som"]) for _, r in df.iterrows()}


def schrijf_transactie(*, item_id, bedrag, tx_type, ruwe_tekst, flag=None,
                       code=None, stuks=1) -> int:
    """Eén losse insert. Geen retry: een dubbel geboekte verkoop is erger dan
    een foutmelding waarna je zelf opnieuw tikt.

    `code` is de kaartcode ("173/195", "EVS 215"). Bij een vrij ingevoerde kaart
    is dat het enige haakje om 'm later alsnog aan de inventaris te koppelen.

    `bedrag` is altijd het TOTAAL van de regel en `stuks` hoeveel kaarten dat
    dekt — dus 3 pakjes van €15 wordt bedrag 45, stuks 3. `afboeken_sales.py`
    haalt er `coalesce(stuks, 1)` van de voorraad af. Dit vervangt de oude
    workaround waarbij het restant als regels van €0,01 werd geboekt."""
    nu = datetime.now()
    with engine().begin() as conn:
        return conn.execute(INSERT_TX, {
            "event_id": event_id(),
            "item_id": item_id,
            "datum": nu.date(),
            "tijd": nu.time().replace(microsecond=0),
            "kanaal": tx_type,
            "type": tx_type,
            "bedrag": bedrag,
            "afzender": AFZENDER,
            "flag": flag,
            "ruwe_tekst": ruwe_tekst,
            "code": (code or "").strip() or None,
            "stuks": int(stuks or 1),
        }).scalar()


DUBBEL_VENSTER = 120        # seconden waarbinnen dezelfde invoer verdacht is


def lijkt_dubbel(recent, item_id, bedrag: float, ruwe_tekst: str,
                 nu: datetime | None = None) -> dict | None:
    """Staat dezelfde kaart voor hetzelfde bedrag er net al in?

    Kijkt naar de laatste transacties van dit event — dus ook naar wat een
    collega zojuist heeft geboekt, niet alleen naar de eigen telefoon. Twee
    losse pakjes van hetzelfde soort ná elkaar verkopen mag gewoon; daarom
    waarschuwen we alleen, en blokkeren we nooit."""
    if recent is None or getattr(recent, "empty", True):
        return None
    nu = nu or datetime.now()
    for _, r in recent.iterrows():
        try:
            bedrag_r = float(pd.to_numeric(r["bedrag"], errors="coerce"))
        except (TypeError, ValueError):
            continue
        if abs(bedrag_r - float(bedrag)) > 0.005:
            continue
        # Zelfde kaart: op item_id als die er is, anders op de getypte tekst.
        zelfde = (int(r["item_id"]) == int(item_id)
                  if item_id is not None and pd.notna(r["item_id"])
                  else (r["item_id"] is None or pd.isna(r["item_id"]))
                  and str(r["ruwe_tekst"] or "").strip().lower()
                  == str(ruwe_tekst or "").strip().lower())
        if not zelfde:
            continue
        seconden = _seconden_geleden(r, nu)
        if seconden is not None and seconden <= DUBBEL_VENSTER:
            return {"tijd": str(r["tijd"])[:8], "seconden": int(seconden),
                    "bedrag": bedrag_r, "afzender": r.get("afzender")}
    return None


def _seconden_geleden(rij, nu: datetime) -> float | None:
    """Hoe lang geleden is deze regel geboekt? None als de tijd onleesbaar is."""
    tijd, dag = rij.get("tijd"), rij.get("datum")
    if tijd is None or (isinstance(tijd, float) and pd.isna(tijd)):
        return None
    try:
        t = tijd if isinstance(tijd, time) else time.fromisoformat(str(tijd)[:8])
        d = nu.date()
        if dag is not None and not (isinstance(dag, float) and pd.isna(dag)):
            d = dag if isinstance(dag, date) else date.fromisoformat(str(dag)[:10])
    except (TypeError, ValueError):
        return None
    verschil = (nu - datetime.combine(d, t)).total_seconds()
    return verschil if verschil >= 0 else None


def verwijder_transactie(tx_id: int) -> int:
    """Gooit één transactie weg. Alleen binnen het eigen event, zodat een oude
    id uit een andere sessie nooit per ongeluk iets anders raakt."""
    with engine().begin() as conn:
        return conn.execute(DELETE_TX, {"id": tx_id, "event_id": event_id()}).rowcount


# ------------------------------------------------------------------ zoeken


def normaliseer(s: str) -> str:
    """Kleine letters, zonder accenten — zodat 'pokemon' ook 'Pokémon' vindt."""
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def zoek(df: pd.DataFrame, term: str) -> pd.DataFrame:
    tokens = [t for t in normaliseer(term).split() if t]
    if not tokens:
        return df.iloc[0:0]
    masker = pd.Series(True, index=df.index)
    for t in tokens:
        masker &= df["zoek"].str.contains(t, regex=False, na=False)
    treffers = df[masker].copy()
    # Naam die met de zoekterm begint eerst — dat is meestal wat je bedoelt.
    treffers["_start"] = ~treffers["naam"].map(normaliseer).str.startswith(tokens[0])
    return treffers.sort_values(["_start", "naam", "prijs"],
                                ascending=[True, True, False])


def geld(bedrag: float) -> str:
    """Hele euro's kort, centen alleen als ze er zijn: 430 → '430', 8,75 → '8.75'."""
    b = float(bedrag)
    return f"{b:,.0f}" if b == int(b) else f"{b:,.2f}"


def toon_treffers(df: pd.DataFrame, term: str, prefix: str, container_key: str,
                  leeg_tekst: str):
    """Zoekresultaten als knoppen; geeft de aangeklikte rij terug (of None)."""
    treffers = zoek(df, term)
    with st.container(key=container_key):
        for _, r in treffers.head(MAX_RESULTATEN).iterrows():
            # "cm" erachter als het geen comp-prijs is: anders lijkt een
            # Cardmarket-prijs op een afgesproken verkoopprijs.
            prijs = (f"€{r['prijs']:,.2f}" + (" cm" if r["prijs_bron"] == "cm" else "")
                     if pd.notna(r["prijs"]) else "€ ?")
            # Voorraad achteraan: zo zie je vóór het tikken of dit de laatste is.
            detail = " · ".join(x for x in [kenmerk(r), conditie(r), prijs,
                                            voorraad_tekst(r["aantal"])] if x)
            # Zachte regelafbreking (\n) + CSS `white-space: pre-line` geeft de
            # tweede regel; :gray[] maakt er een span van die klein wordt gezet.
            if st.button(f"{r['naam']}\n:gray[{detail}]", key=f"{prefix}{r['id']}",
                         width="stretch"):
                return r
        if treffers.empty:
            st.caption(leeg_tekst)
    return None


def als_dict(r) -> dict:
    return {"id": int(r["id"]), "naam": r["naam"], "kenmerk": kenmerk(r),
            "conditie": conditie(r), "aantal": int(r["aantal"]),
            "prijs": None if pd.isna(r["prijs"]) else float(r["prijs"]),
            "prijs_bron": r["prijs_bron"]}


# ------------------------------------------------------------------ sessie


def init_state():
    st.session_state.setdefault("modus", "VERKOOP")
    st.session_state.setdefault("inkoop_modus", "Per kaart")
    st.session_state.setdefault("sel", None)          # gekozen item (dict)
    st.session_state.setdefault("stapel", [])         # lijst voor de totaal-modus
    st.session_state.setdefault("mijn_invoeren", [])  # eigen inserts, voor undo
    st.session_state.setdefault("undo_vraag", False)
    st.session_state.setdefault("bevestiging", None)
    st.session_state.setdefault("fout", None)
    st.session_state.setdefault("tx_versie", 0)       # tikt de lijst-cache aan
    st.session_state.setdefault("stuks", 1)           # aantal kaarten in deze regel
    st.session_state.setdefault("prijs_modus", "Per stuk")
    st.session_state.setdefault("dubbel_vraag", None)  # wacht op "toch vastleggen"
    if st.session_state.pop("_leeg_zoek", False):
        st.session_state["stapel_zoek"] = ""
        st.session_state["stapel_vrij"] = ""
        st.session_state["stapel_code"] = ""
    if st.session_state.pop("_reset", False):
        st.session_state["sel"] = None
        st.session_state["zoekterm"] = ""
        st.session_state["bedrag"] = 0.0
        st.session_state["eigen_pct"] = None
        st.session_state["vrij_naam"] = ""
        st.session_state["vrij_code"] = ""
        st.session_state["vrij_bedrag"] = 0.0
        st.session_state["stapel"] = []
        st.session_state["stapel_zoek"] = ""
        st.session_state["stapel_vrij"] = ""
        st.session_state["stapel_code"] = ""
        st.session_state["totaal_bedrag"] = 0.0
        st.session_state["stuks"] = 1
        st.session_state["prijs_modus"] = "Per stuk"
        st.session_state["dubbel_vraag"] = None


def stuks_bij(stap: int):
    """+/- knop. Minimaal 1: een verkoop van nul kaarten bestaat niet."""
    st.session_state["stuks"] = max(1, int(st.session_state.get("stuks", 1)) + stap)
    st.session_state["dubbel_vraag"] = None


def totaalbedrag() -> float:
    """Wat er als bedrag de database in gaat: altijd het totaal van de regel.

    Bij 'Per stuk' is het ingevulde bedrag de stuksprijs en vermenigvuldigen we;
    bij 'Totaal' is het al het totaal en delen we nergens door — dan is de
    stuksprijs niet rond en dat hoeft ook niet, het totaal is wat er betaald is."""
    bedrag = float(st.session_state.get("bedrag") or 0)
    n = max(1, int(st.session_state.get("stuks", 1)))
    return bedrag * n if st.session_state.get("prijs_modus") == "Per stuk" else bedrag


def prefill_bedrag():
    """Bij verkoop de bekende prijs voorinvullen, bij inkoop leeg (0,00)."""
    sel = st.session_state.get("sel")
    prijs = 0.0
    if sel and st.session_state["modus"] == "VERKOOP" and sel.get("prijs"):
        prijs = float(sel["prijs"])
    st.session_state["bedrag"] = prijs
    st.session_state["eigen_pct"] = None


def kies_item(rij: dict):
    st.session_state["sel"] = rij
    st.session_state["bevestiging"] = None
    st.session_state["fout"] = None
    st.session_state["stuks"] = 1
    st.session_state["prijs_modus"] = "Per stuk"
    st.session_state["dubbel_vraag"] = None
    prefill_bedrag()


def reken_percentage(pct: float):
    """Bedrag = comp × percentage, afgerond op hele euro's (half naar boven).

    Zet alleen de waarde van het bedrag-veld: daarna is het gewoon weer een
    invoerveld, dus altijd met de hand te overschrijven."""
    sel = st.session_state.get("sel")
    if not sel or not sel.get("prijs") or not pct:
        return
    st.session_state["bedrag"] = float(math.floor(sel["prijs"] * pct / 100 + 0.5))


def reken_eigen_percentage():
    reken_percentage(st.session_state.get("eigen_pct") or 0)


def na_succes(naam: str, bedrag: float, tx_id: int | None = None):
    st.session_state["bevestiging"] = f"✓ {naam} — €{bedrag:,.2f}"
    st.session_state["fout"] = None
    st.session_state["tx_versie"] += 1
    if tx_id is not None:
        # Alleen wat deze telefoon zelf heeft ingevoerd — undo raakt nooit de
        # invoer van een collega die tegelijk aan het boeken is.
        st.session_state["mijn_invoeren"].append(
            {"id": int(tx_id), "naam": naam, "bedrag": float(bedrag)})
    st.session_state["_reset"] = True
    st.rerun()


def meld_fout(melding: str, detail: str):
    st.session_state["fout"] = {"melding": melding, "detail": detail}
    st.rerun()


init_state()

# ------------------------------------------------------------------ stijl
# Kleur per modus: verkoop groen, inkoop rood (het TC-rood). Eén blik op de knop
# is genoeg om te zien wát je aan het boeken bent. De modus staat al in
# session_state vóór het script draait, dus dit kan in hetzelfde <style>-blok.

MODUS_KLEUR = {"VERKOOP": "#1B9E4B", "INKOOP": "#F20519"}
_modus = st.session_state.get("modus") or "VERKOOP"
_vrij_modus = st.session_state.get("vrij_modus") or _modus
KLEUR, VRIJ_KLEUR = MODUS_KLEUR[_modus], MODUS_KLEUR[_vrij_modus]

EXTRA_CSS = """
  /* voorraad bij de gekozen kaart */
  .tc-voorraad, .tc-laatste, .tc-op {font-size:.85rem; font-weight:600;
      margin:-.3rem 0 .5rem;}
  .tc-voorraad {color:#5b6472;}
  .tc-laatste  {color:#B26A00;}
  .tc-op       {color:#C0261A;}
  /* aantal-teller tussen de +/- knoppen */
  .tc-stuks {text-align:center; font-size:1.5rem; font-weight:700; line-height:2.4rem;}
  /* dubbel-waarschuwing: opvalt, maar blokkeert niet */
  .tc-dubbel {background:#FFF6E0; border-left:4px solid #E8A200; border-radius:6px;
      padding:.55rem .7rem; font-size:.9rem; margin:.4rem 0;}
"""

st.markdown(f"""<style>
{BASIS_CSS}
{EXTRA_CSS}
  /* :not(:disabled) — een uitgeschakelde knop moet grijs blijven, anders lijkt
     'ie klaar voor gebruik terwijl er nog geen kaart gekozen is. */
  .st-key-vastleggen button:not(:disabled),
  .st-key-stapel_vastleggen button:not(:disabled) {{
      background: {KLEUR} !important; border-color: {KLEUR} !important;
      color: #fff !important;}}
  .st-key-vrij_knop button:not(:disabled) {{background: {VRIJ_KLEUR} !important;
      border-color: {VRIJ_KLEUR} !important; color: #fff !important;}}
  .st-key-modus button[aria-checked="true"] {{border-color: {KLEUR} !important;
      color: {KLEUR} !important; background: {KLEUR}14 !important;}}
</style>""", unsafe_allow_html=True)

# ------------------------------------------------------------------ pincode

PIN = secret("TC_BEURS_PIN")
if PIN and not st.session_state.get("ontgrendeld"):
    st.text_input("Pincode", type="password", key="pin_invoer")
    if st.button("Open", type="primary", width="stretch"):
        if st.session_state["pin_invoer"] == PIN:
            st.session_state["ontgrendeld"] = True
            st.rerun()
        else:
            st.error("Verkeerde pincode.")
    st.stop()

# ------------------------------------------------------------------ dagtotaal

VANDAAG = date.today()

try:
    totalen = dagtotalen(event_id(), VANDAAG, st.session_state["tx_versie"])
except SQLAlchemyError:
    totalen = None      # database plat: de foutmelding komt hieronder al

if totalen is not None:
    binnen, uit = totalen.get("verkoop", 0.0), totalen.get("inkoop", 0.0)
    netto = binnen - uit
    teken = "−" if netto < 0 else ""      # min vóór het euroteken leest rustiger
    st.markdown(
        f'<div class="tc-dag">Vandaag: <b>€{geld(binnen)}</b> in &nbsp;·&nbsp; '
        f'<b>€{geld(uit)}</b> uit &nbsp;·&nbsp; netto <b>{teken}€{geld(abs(netto))}</b>'
        f'</div>', unsafe_allow_html=True)

# ------------------------------------------------------------------ modus

st.segmented_control("Wat leg je vast?", MODI, key="modus", width="stretch",
                     on_change=prefill_bedrag, label_visibility="collapsed",
                     required=True)
MODUS = st.session_state["modus"] or "VERKOOP"
TX_TYPE = "verkoop" if MODUS == "VERKOOP" else "inkoop"

if MODUS == "INKOOP":
    st.segmented_control("Hoe reken je af?", INKOOP_MODI, key="inkoop_modus",
                         width="stretch", label_visibility="collapsed", required=True)
INKOOP_MODUS = (st.session_state.get("inkoop_modus") or "Per kaart"
                if MODUS == "INKOOP" else "Per kaart")
STAPEL_MODUS = MODUS == "INKOOP" and INKOOP_MODUS == "Totaal"

if VANDAAG not in BEURSDAGEN:
    # Klein en grijs: het is een terzijde, geen waarschuwing — de invoer telt gewoon.
    st.markdown(f'<div class="tc-note">{VANDAAG.strftime("%d-%m-%Y")} valt buiten de '
                f'beursdagen — invoer telt wél mee</div>', unsafe_allow_html=True)

# ------------------------------------------------------------------ meldingen

if st.session_state["bevestiging"]:
    st.markdown(f'<div class="tc-ok">{st.session_state["bevestiging"]}</div>',
                unsafe_allow_html=True)

if st.session_state["fout"]:
    st.error(st.session_state["fout"]["melding"], icon="⚠️")
    with st.expander("Details"):
        st.code(st.session_state["fout"]["detail"])

# ------------------------------------------------------------------ kaarten

try:
    items = laad_items()
except SQLAlchemyError as e:
    st.error("Geen verbinding met de database.", icon="🚫")
    with st.expander("Details"):
        st.code(str(e))
    st.stop()

# Eén keer ophalen en twee keer gebruiken: de dubbel-check hieronder en het
# logje onderaan. Scheelt een tweede query op een beursnetwerk dat toch al traag is.
try:
    recent_voor_check = laatste_transacties(event_id(), st.session_state["tx_versie"])
except SQLAlchemyError:
    recent_voor_check = None

if STAPEL_MODUS:
    # --- stapel: meerdere kaarten, één totaalbedrag ------------------------
    # Eén transactie met item_id NULL: een stapel hangt per definitie aan meer
    # dan één kaart. Wát erin zat staat in ruwe_tekst, het totaal is leidend.
    stapel = st.session_state["stapel"]

    st.text_input("Zoek kaart", key="stapel_zoek",
                  placeholder="zoek kaart om toe te voegen",
                  label_visibility="collapsed")
    term = st.session_state.get("stapel_zoek", "")
    if len(term.strip()) >= 2:
        gekozen = toon_treffers(items, term, "add_", "stapel_resultaten",
                                "Niets gevonden — typ de naam hieronder.")
        if gekozen is not None:
            stapel.append({"id": int(gekozen["id"]), "naam": gekozen["naam"],
                           "code": kenmerk(gekozen)})
            st.session_state["_leeg_zoek"] = True
            st.rerun()

    # Vrij toevoegen: de naam is het langste veld en krijgt een eigen regel,
    # code + knop eronder. Zo blijft het op telefoonbreedte twee nette rijen.
    st.text_input("Vrij toevoegen", key="stapel_vrij",
                  placeholder="of typ zelf een naam", label_visibility="collapsed")
    kol_code, kol_plus = st.columns([2, 1], vertical_alignment="center")
    kol_code.text_input("Code", key="stapel_code", placeholder="code (optioneel)",
                        label_visibility="collapsed")
    if kol_plus.button("+ toevoegen", key="stapel_vrij_knop", width="stretch"):
        naam = (st.session_state.get("stapel_vrij") or "").strip()
        if naam:
            stapel.append({"id": None, "naam": naam,
                           "code": (st.session_state.get("stapel_code") or "").strip()})
            st.session_state["_leeg_zoek"] = True
            st.rerun()

    st.markdown(f'<div class="tc-teller">Stapel: <b>{len(stapel)}</b> '
                f'{"kaart" if len(stapel) == 1 else "kaarten"}</div>',
                unsafe_allow_html=True)

    if stapel:
        # Elke regel is zelf de weg-knop: één tap haalt 'm eruit. Scheelt een
        # kolommen-layout die op een telefoon toch uit elkaar valt.
        for i, kaart in enumerate(stapel):
            regel = f"{i + 1}. {kaart['naam']}"
            if kaart.get("code"):
                regel += f" · {kaart['code']}"
            if st.button(f"✕  {regel}", key=f"weg_{i}", width="stretch"):
                stapel.pop(i)
                st.rerun()
        st.caption("Tik een kaart om 'm uit de stapel te halen.")
    else:
        st.caption("Nog leeg — zoek een kaart, of typ zelf een naam en tik "
                   "**+ toevoegen**.")

    st.number_input("Totaalbedrag", min_value=0.0, step=0.50, format="%.2f",
                    key="totaal_bedrag", label_visibility="collapsed")

    if st.button(f"VASTLEGGEN — STAPEL ({len(stapel)})", type="primary",
                 width="stretch", disabled=not stapel,
                 key="stapel_vastleggen") and stapel:
        totaal = float(st.session_state["totaal_bedrag"])
        if totaal <= 0:
            meld_fout("Vul een totaalbedrag in.", "geen database-actie uitgevoerd")
        else:
            # Naam mét code per kaart, zodat de regel later nog te herleiden is.
            namen = " · ".join(
                f"{k['naam']} [{k['code']}]" if k.get("code") else k["naam"]
                for k in stapel)
            omschrijving = f"Stapel {len(stapel)} kaarten: {namen}"
            try:
                tx = schrijf_transactie(item_id=None, bedrag=totaal, tx_type="inkoop",
                                        ruwe_tekst=omschrijving, flag="stapel")
                na_succes(f"stapel ({len(stapel)} kaarten)", totaal, tx)
            except SQLAlchemyError as e:
                engine().dispose()
                meld_fout(f"NIET vastgelegd: stapel €{totaal:,.2f}. "
                          "Je stapel staat er nog — probeer opnieuw.", str(e)[:800])

else:
    # --- per kaart: zoeken, kiezen, bedrag ---------------------------------
    sel = st.session_state["sel"]

    if sel:
        regels = [x for x in [sel.get("kenmerk"), sel.get("conditie")] if x]
        st.markdown(f"### {sel['naam']}")
        if regels:
            st.caption(" · ".join(regels))
        voorraad = int(sel.get("aantal", 0))
        st.markdown(f'<div class="{voorraad_klasse(voorraad)}">'
                    f'{voorraad_tekst(voorraad)}</div>', unsafe_allow_html=True)
        if voorraad <= 0:
            # Waarschuwen, niet blokkeren: de voorraadstand loopt achter zodra
            # er buiten de app om iets is verkocht of bijgekocht.
            st.markdown('<div class="tc-note">Staat op nul of lager — vastleggen '
                        'mag, de voorraad klopt dan alleen niet.</div>',
                        unsafe_allow_html=True)
        if st.button("✕ andere kaart", key="deselect"):
            st.session_state["sel"] = None
            st.rerun()
    else:
        st.text_input("Zoek kaart", key="zoekterm", placeholder="zoek kaart",
                      label_visibility="collapsed")
        term = st.session_state.get("zoekterm", "")
        if len(term.strip()) >= 2:
            gekozen = toon_treffers(items, term, "pick_", "resultaten",
                                    "Niets gevonden — gebruik *vrij invoeren* hieronder.")
            if gekozen is not None:
                kies_item(als_dict(gekozen))
                st.rerun()

    # rekenhulp: alleen bij inkoop, en alleen als er een comp-prijs is om
    # vanaf te rekenen. Geen comp → gewoon het bedrag intikken.
    if MODUS == "INKOOP" and sel and sel.get("prijs"):
        comp = float(sel["prijs"])
        label = "comp" if sel.get("prijs_bron") == "comp" else "cm"
        st.markdown(f'<div class="tc-comp">{label} €{geld(comp)} — betaald:</div>',
                    unsafe_allow_html=True)
        kolommen = st.columns([1, 1, 1, 1.3], vertical_alignment="center")
        for kolom, pct in zip(kolommen, PERCENTAGES):
            if kolom.button(f"{pct}%", key=f"pct_{pct}", width="stretch"):
                reken_percentage(pct)
        with kolommen[3]:
            st.number_input("Eigen percentage", min_value=1, max_value=100, step=1,
                            value=None, key="eigen_pct", placeholder="…%",
                            on_change=reken_eigen_percentage,
                            label_visibility="collapsed")

    # --- aantal -----------------------------------------------------------
    # Blijft één tik voor het gewone geval: de teller staat op 1 en je hoeft er
    # niets aan te doen. Pas bij meerdere stuks komt de per-stuk/totaal-keuze
    # tevoorschijn, zodat de kernflow niet langer wordt voor de 90% die er één
    # verkoopt.
    n_stuks = max(1, int(st.session_state.get("stuks", 1)))
    kol_min, kol_n, kol_plus = st.columns([1, 2, 1], vertical_alignment="center")
    kol_min.button("−", key="stuks_min", width="stretch", disabled=n_stuks <= 1,
                   on_click=stuks_bij, args=(-1,))
    kol_n.markdown(f'<div class="tc-stuks">{n_stuks}&nbsp;×</div>',
                   unsafe_allow_html=True)
    kol_plus.button("+", key="stuks_plus", width="stretch",
                    on_click=stuks_bij, args=(1,))

    if n_stuks > 1:
        st.segmented_control("Bedrag is", ["Per stuk", "Totaal"], key="prijs_modus",
                             width="stretch", label_visibility="collapsed",
                             required=True)

    st.number_input("Bedrag", min_value=0.0, step=0.50, format="%.2f", key="bedrag",
                    label_visibility="collapsed")

    TOTAAL = totaalbedrag()
    if n_stuks > 1 and TOTAAL > 0:
        per = TOTAAL / n_stuks
        st.markdown(f'<div class="tc-comp">{n_stuks} × €{geld(per)} = '
                    f'<b>€{geld(TOTAAL)}</b></div>', unsafe_allow_html=True)

    # --- dubbel-waarschuwing ---------------------------------------------
    vraag = st.session_state.get("dubbel_vraag")
    if vraag:
        st.markdown(f'<div class="tc-dubbel">Net al ingevoerd: {vraag["naam"]} '
                    f'€{geld(vraag["bedrag"])} om {vraag["tijd"]} '
                    f'({vraag["seconden"]} s geleden) — toch vastleggen?</div>',
                    unsafe_allow_html=True)
        kol_ja, kol_nee = st.columns(2)
        bevestigd = kol_ja.button("Ja, toch vastleggen", key="dubbel_ja",
                                  type="primary", width="stretch")
        if kol_nee.button("Nee, laat maar", key="dubbel_nee", width="stretch"):
            st.session_state["dubbel_vraag"] = None
            st.rerun()
    else:
        bevestigd = False

    klik = st.button(f"VASTLEGGEN — {MODUS}", type="primary", width="stretch",
                     disabled=sel is None or bool(vraag), key="vastleggen")

    if (klik or bevestigd) and sel:
        if TOTAAL <= 0:
            meld_fout("Vul een bedrag in.", "geen database-actie uitgevoerd")
        else:
            al_gezien = lijkt_dubbel(recent_voor_check, sel["id"], TOTAAL,
                                     sel["naam"]) if klik else None
            if al_gezien:
                st.session_state["dubbel_vraag"] = {
                    "naam": sel["naam"], "bedrag": TOTAAL, **al_gezien}
                st.rerun()
            try:
                tx = schrijf_transactie(item_id=sel["id"], bedrag=TOTAAL,
                                        tx_type=TX_TYPE, ruwe_tekst=sel["naam"],
                                        code=sel.get("kenmerk"), stuks=n_stuks)
                label = sel["naam"] if n_stuks == 1 else f"{n_stuks}× {sel['naam']}"
                na_succes(label, TOTAAL, tx)
            except SQLAlchemyError as e:
                engine().dispose()
                meld_fout(f"NIET vastgelegd: {sel['naam']} €{TOTAAL:,.2f}. "
                          "Je invoer staat er nog — probeer opnieuw.", str(e)[:800])

    # --- vangnet ----------------------------------------------------------
    with st.expander("Kaart staat er niet in → vrij invoeren"):
        st.text_input("Naam", key="vrij_naam", placeholder="naam kaart of product",
                      label_visibility="collapsed")
        # Code is optioneel, maar wél het enige haakje om deze regel later alsnog
        # aan de inventaris te koppelen — hij heeft geen item_id.
        st.text_input("Code", key="vrij_code",
                      placeholder="code, bijv. 173/195 of swsh260 (mag leeg)",
                      label_visibility="collapsed")
        st.number_input("Bedrag", min_value=0.0, step=0.50, format="%.2f",
                        key="vrij_bedrag", label_visibility="collapsed")
        vrij_type = st.segmented_control("Soort", MODI, key="vrij_modus", default=MODUS,
                                         required=True, width="stretch",
                                         label_visibility="collapsed") or MODUS
        if st.button("VASTLEGGEN", type="primary", width="stretch", key="vrij_knop"):
            naam = (st.session_state.get("vrij_naam") or "").strip()
            vb = float(st.session_state.get("vrij_bedrag") or 0)
            if not naam or vb <= 0:
                st.error("Vul een naam én een bedrag in.")
            else:
                try:
                    tx = schrijf_transactie(
                        item_id=None, bedrag=vb,
                        tx_type="verkoop" if vrij_type == "VERKOOP" else "inkoop",
                        ruwe_tekst=naam, flag="vrij ingevoerd",
                        code=st.session_state.get("vrij_code"))
                    na_succes(naam, vb, tx)
                except SQLAlchemyError as e:
                    engine().dispose()
                    meld_fout(f"NIET vastgelegd: {naam} €{vb:,.2f}. "
                              "Je invoer staat er nog — probeer opnieuw.", str(e)[:800])

# ------------------------------------------------------------------ undo

mijn = st.session_state["mijn_invoeren"]
if mijn:
    laatste = mijn[-1]
    if st.session_state["undo_vraag"]:
        st.warning(f"Laatste invoer verwijderen: {laatste['naam']} "
                   f"€{geld(laatste['bedrag'])}?", icon="↩️")
        kol_ja, kol_nee = st.columns(2)
        if kol_ja.button("Ja, verwijderen", key="undo_ja", type="primary",
                         width="stretch"):
            try:
                weg = verwijder_transactie(laatste["id"])
                mijn.pop()
                st.session_state["undo_vraag"] = False
                st.session_state["tx_versie"] += 1
                st.session_state["bevestiging"] = (
                    f"↩ Verwijderd: {laatste['naam']} — €{laatste['bedrag']:,.2f}"
                    if weg else f"↩ {laatste['naam']} stond er al niet meer in.")
                st.rerun()
            except SQLAlchemyError as e:
                engine().dispose()
                st.session_state["undo_vraag"] = False
                meld_fout(f"NIET verwijderd: {laatste['naam']}. Probeer opnieuw.",
                          str(e)[:800])
        if kol_nee.button("Nee, laten staan", key="undo_nee", width="stretch"):
            st.session_state["undo_vraag"] = False
            st.rerun()
    elif st.button(f"↩ Laatste invoer terugdraaien — €{geld(laatste['bedrag'])}",
                   key="undo", width="stretch"):
        st.session_state["undo_vraag"] = True
        st.rerun()

# ------------------------------------------------------------------ laatste 10

recent = recent_voor_check

if recent is not None and not recent.empty:
    regels = []
    for _, r in recent.iterrows():
        bedrag = float(pd.to_numeric(r["bedrag"], errors="coerce") or 0)
        teken = "−" if r["type"] == "inkoop" else "+"
        regels.append(f'{str(r["tijd"])[:5]} &nbsp; <b>{teken}€{geld(bedrag)}</b> '
                      f'&nbsp; {html.escape(str(r["ruwe_tekst"])[:38])}')
    st.markdown(f'<div class="tc-log">{"<br>".join(regels)}</div>',
                unsafe_allow_html=True)
