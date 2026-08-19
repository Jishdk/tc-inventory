"""TC beurs — snelle invoer voor op de beursvloer.

Het team legt op de telefoon vast wat er de deur uit gaat: verkopen en trades.
Elke invoer is een INSERT in `transactions`, gekoppeld aan één event. Er wordt
niets afgeboekt uit `items` — dat doet `afboeken_sales.py` later.

    streamlit run beurs_app.py

Ontwerpkeuzes:
- Eén gedeelde gebruiker: `afzender` staat vast op "TC". Geen naamkeuze, dat is
  een scherm minder tussen jou en de invoer.
- De items-tabel wordt één keer opgehaald en in cache gehouden; zoeken gebeurt
  lokaal. Zo is zoeken instant en kost het geen netwerk op een slechte beursvloer.
- Schrijven gebeurt zonder automatische retry (pool_pre_ping vangt dode
  connecties al af). Faalt het toch, dan blijft de invoer staan met een
  duidelijke foutmelding — nooit stil falen.
- INKOOP zit er bewust niet meer in. Wat er binnenkomt gaat via de inventaris,
  niet via de beursvloer; de app houdt bij wat er wéggaat. Oude inkooprijen
  blijven gewoon in de database staan en tellen mee in het dagtotaal.
"""

from __future__ import annotations

import html
import os
import sys
import uuid
import unicodedata
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

# ------------------------------------------------------------------ constanten

EVENT_NAAM = "Cardmaniacs Nijmegen 15-16 augustus 2026"
EVENT_DATUM = date(2026, 8, 15)
EVENT_LOCATIE = "Nijmegen"
BEURSDAGEN = {date(2026, 8, 15), date(2026, 8, 16)}

AFZENDER = "TC"          # één gedeelde gebruiker
# TRADE komt er in een volgende stap bij; met één modus heeft de keuzebalk geen
# functie en verbergen we 'm, zodat er niets tussen jou en het zoekveld staat.
MODI = ["VERKOOP"]
PRIJS_MODI = ["Per kaart", "Totaalprijs"]
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
     náást elkaar blijven (de +/- stepper, ja/nee-vragen). */
  div[data-testid="stHorizontalBlock"] {flex-wrap: nowrap; gap: .5rem;}
  div[data-testid="stColumn"] {min-width: 0 !important;}

  /* anker-icoontje naast de kaartnaam weg — het is geen webpagina */
  [data-testid="stHeaderActionElements"] {display: none;}

  /* dagtotaal: één rustige regel, geen blok dat de invoer wegdrukt */
  .tc-dag {background: rgba(128,128,128,.12); border-radius: .6rem;
      padding: .45rem .7rem; margin-bottom: .1rem; text-align: center;
      font-size: .92rem; opacity: .8; line-height: 1.4;}
  .tc-dag b {font-weight: 700; opacity: 1;}

  /* zoekbalk: prominent */
  .st-key-zoekterm input {font-size: 1.25rem !important;
      padding: .9rem .75rem !important;}

  /* zoekresultaten: naam vet, daaronder klein set + staat + prijs */
  .st-key-resultaten div[data-testid="stVerticalBlock"] {gap: .45rem;}
  [class*="st-key-pick_"] button {min-height: 3.5rem; padding: .6rem .9rem;}
  [class*="st-key-pick_"] button > div {width: 100%; justify-content: flex-start;}
  [class*="st-key-pick_"] button p {
      white-space: pre-line; text-align: left;
      line-height: 1.35; font-size: 1.05rem; font-weight: 600; margin: 0;}
  [class*="st-key-pick_"] button p span {font-size: .82rem; font-weight: 400;}

  /* mandje: elke regel is een naamregel plus één bedieningsregel */
  [class*="st-key-regel_"] div[data-testid="stVerticalBlock"] {gap: .3rem;}
  [class*="st-key-regel_"] {border-bottom: 1px solid rgba(128,128,128,.16);
      padding-bottom: .5rem;}
  .tc-regelnaam {font-size: 1.05rem; font-weight: 600; line-height: 1.3;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;}
  .tc-regelnaam span {font-size: .82rem; font-weight: 400; opacity: .6;}
  [class*="st-key-weg_"] button {min-height: 2.4rem; padding: 0;
      color: #8a8f98; border-color: transparent; background: transparent;}
  /* stepper: klein en inline, zodat VASTLEGGEN op een telefoon boven de vouw
     blijft — een teller van drie grote blokken duwde 'm eruit */
  [class*="st-key-min_"] button, [class*="st-key-plus_"] button {
      min-height: 2.6rem; padding: 0; font-size: 1.15rem; font-weight: 700;}
  .tc-stuks {text-align: center; font-size: 1.1rem; font-weight: 700;
      line-height: 2.6rem;}
  .tc-inbegrepen {font-size: .85rem; opacity: .5; text-align: right;
      line-height: 2.6rem; padding-right: .5rem;}
  .tc-teller {font-size: .9rem; font-weight: 600; opacity: .6;
      margin: .2rem 0 -.35rem; letter-spacing: .02em;}
  .tc-totaal {text-align: right; font-size: 1.05rem; margin: -.2rem 0 .2rem;}
  .tc-totaal b {font-size: 1.35rem;}

  /* prijs per regel: het getal is het belangrijkste op het scherm */
  [class*="st-key-prijs_"] input {font-size: 1.45rem !important;
      font-weight: 700; text-align: right; padding: .35rem .6rem !important;}
  [class*="st-key-prijs_"] [data-testid="stNumberInputContainer"]::before,
  .st-key-mandje_totaal [data-testid="stNumberInputContainer"]::before {
      content: "€"; align-self: center; padding-left: .7rem; font-weight: 700;
      opacity: .45;}
  [class*="st-key-prijs_"] button {display: none;}   /* +/- steppers: te smal */
  .st-key-mandje_totaal input {font-size: 2rem !important; font-weight: 700;
      text-align: right; padding: .6rem .75rem !important;}
  .st-key-mandje_totaal [data-testid="stNumberInputContainer"]::before {
      font-size: 1.6rem;}
  .st-key-prijs_modus div[data-testid="stSegmentedControl"] button {
      min-height: 2.4rem; font-size: .92rem; font-weight: 500;}

  /* VASTLEGGEN: grote knop onderaan */
  .st-key-vastleggen button {
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
    ruwe_tekst, code, stuks, group_id
) VALUES (
    :event_id, :item_id, :datum, :tijd, :kanaal, :type, :bedrag, :afzender,
    :flag, :ruwe_tekst, :code, :stuks, :group_id
)
RETURNING id
""")

# Alleen binnen het eigen event, en alleen wat nog niet is afgeboekt: een regel
# waarvan de voorraad al af is halen we niet stilletjes weg, want dan klopt de
# voorraad niet meer met wat er ooit is verkocht.
DELETE_TX = text("""
DELETE FROM transactions
WHERE id IN :ids AND event_id = :event_id AND afgeboekt_op IS NULL
""").bindparams(bindparam("ids", expanding=True))

TEL_AFGEBOEKT = text("""
SELECT COUNT(*) FROM transactions
WHERE id IN :ids AND event_id = :event_id AND afgeboekt_op IS NOT NULL
""").bindparams(bindparam("ids", expanding=True))


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
def dagtotalen(eid: int, dag: date, versie: int) -> dict[str, dict]:
    """Som én aantal per type voor één dag. `versie` breekt de cache open na elke
    invoer, zodat de balk bovenaan meteen meeloopt met wat je net hebt vastgelegd.

    Voor trades telt niet het bedrag maar het aantal *afspraken*: vier kaarten de
    deur uit tegen wat kaarten terug is één trade, geen vier. Regels van dezelfde
    trade delen een group_id; een losse regel zonder groep telt als één."""
    df = lees("""
        SELECT t.type,
               COALESCE(SUM(t.bedrag), 0) AS som,
               COUNT(DISTINCT COALESCE(t.group_id, 'tx-' || t.id)) AS groepen
        FROM transactions t WHERE t.event_id = :eid AND t.datum = :dag
        GROUP BY t.type
    """, {"eid": eid, "dag": dag})
    return {str(r["type"]): {"som": float(r["som"]), "groepen": int(r["groepen"])}
            for _, r in df.iterrows()}


def schrijf_transactie(*, item_id, bedrag, tx_type, ruwe_tekst, flag=None,
                       code=None, stuks=1, group_id=None) -> int:
    """Eén losse insert. Geen retry: een dubbel geboekte verkoop is erger dan
    een foutmelding waarna je zelf opnieuw tikt.

    `code` is de kaartcode ("173/195", "EVS 215"). Bij een vrij ingevoerde kaart
    is dat het enige haakje om 'm later alsnog aan de inventaris te koppelen.

    `bedrag` is altijd het TOTAAL van de regel en `stuks` hoeveel kaarten dat
    dekt — dus 3 pakjes van €15 wordt bedrag 45, stuks 3. `afboeken_sales.py`
    haalt er `coalesce(stuks, 1)` van de voorraad af. Dit vervangt de oude
    workaround waarbij het restant als regels van €0,01 werd geboekt.

    `group_id` bindt de regels van één afspraak aan elkaar. NULL bij een losse
    verkoop van één kaart — dat is nog steeds het normale geval."""
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
            "group_id": group_id,
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


def verwijder_transacties(tx_ids: list[int]) -> int:
    """Gooit de regels van één afspraak weg — alle kaarten van een mandje in één
    keer, want een half teruggedraaide verkoop is erger dan geen.

    Beperkt tot het eigen event, zodat een oude id uit een andere sessie nooit
    per ongeluk iets anders raakt. Al afgeboekte regels blijven staan: daar hangt
    een voorraadmutatie aan die hier niet meer terug te draaien is."""
    ids = [int(i) for i in tx_ids]
    with engine().begin() as conn:
        params = {"ids": ids, "event_id": event_id()}
        if conn.execute(TEL_AFGEBOEKT, params).scalar():
            raise RuntimeError("al afgeboekt van de voorraad — niet verwijderd")
        return conn.execute(DELETE_TX, params).rowcount


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


def verdeel(gewichten: list[float], totaal: float) -> list[float]:
    """Verdeel één afgesproken totaalbedrag over de regels van het mandje.

    Naar rato van de richtprijs (comp × stuks), zodat de dure kaart ook het
    grootste deel van de korting draagt. Het afrondingsrestant gaat naar de
    zwaarste regel: de som is daardoor tot op de cent het bedrag dat is
    afgesproken, en de omzet blijft dus kloppen zonder een aparte totaalregel.

    Weet geen enkele regel een prijs, dan is er niets om naar rato te verdelen
    en delen we gelijk — dat is dan net zo goed een gok als elke andere."""
    if not gewichten:
        return []
    som = sum(gewichten)
    if som <= 0:
        gewichten = [1.0] * len(gewichten)
        som = float(len(gewichten))
    delen = [round(totaal * g / som, 2) for g in gewichten]
    grootste = max(range(len(delen)), key=lambda i: gewichten[i])
    delen[grootste] = round(delen[grootste] + totaal - sum(delen), 2)
    return delen


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
    st.session_state.setdefault("mandje", [])         # regels van deze afspraak
    st.session_state.setdefault("regel_teller", 0)    # geeft elke regel een eigen id
    st.session_state.setdefault("mijn_invoeren", [])  # eigen inserts, voor undo
    st.session_state.setdefault("undo_vraag", False)
    st.session_state.setdefault("bevestiging", None)
    st.session_state.setdefault("fout", None)
    st.session_state.setdefault("tx_versie", 0)       # tikt de lijst-cache aan
    st.session_state.setdefault("prijs_modus", "Per kaart")
    st.session_state.setdefault("dubbel_vraag", None)  # wacht op "toch vastleggen"

    # Widget-state opruimen kan alleen vóórdat de widgets van deze run bestaan;
    # daarom zetten de knoppen hieronder een vlag en gebeurt het werk hier.
    for rid in st.session_state.pop("_weg_rids", []):
        st.session_state["mandje"] = [r for r in st.session_state["mandje"]
                                      if r["rid"] != rid]
        vergeet_regel(rid)
    if st.session_state.pop("_leeg_zoek", False):
        st.session_state["zoekterm"] = ""
    if st.session_state.pop("_leeg_vrij", False):
        st.session_state["vrij_naam"] = ""
        st.session_state["vrij_code"] = ""
    if st.session_state.pop("_reset", False):
        for regel in st.session_state["mandje"]:
            vergeet_regel(regel["rid"])
        st.session_state["mandje"] = []
        st.session_state["zoekterm"] = ""
        st.session_state["vrij_naam"] = ""
        st.session_state["vrij_code"] = ""
        st.session_state["mandje_totaal"] = 0.0
        st.session_state["prijs_modus"] = "Per kaart"
        st.session_state["dubbel_vraag"] = None


def vergeet_regel(rid: int):
    """Haalt de widget-state van één mandje-regel weg. Zonder dit blijft het
    aantal van een verwijderde kaart hangen en duikt het op bij een volgende
    regel die toevallig hetzelfde nummer krijgt."""
    for sleutel in (f"stuks_{rid}", f"prijs_{rid}"):
        st.session_state.pop(sleutel, None)


def voeg_toe(*, item_id, naam, kenmerk="", conditie="", voorraad=0, prijs=None,
             prijs_bron=None, code=None):
    """Zet een kaart in het mandje. Eén tik op een zoekresultaat is genoeg — de
    kaart staat er meteen in met zijn eigen prijs erbij. Dat houdt de losse
    verkoop precies even kort als voorheen: zoeken, tikken, vastleggen."""
    st.session_state["regel_teller"] += 1
    rid = st.session_state["regel_teller"]
    st.session_state[f"stuks_{rid}"] = 1
    # `bedrag` is de waarheid, niet de widget-state: Streamlit gooit de state van
    # een widget weg zodra die één run niet gerenderd wordt, en dat gebeurt hier
    # elke keer dat een tik op een zoekresultaat meteen een rerun uitlokt — de
    # regels eronder komen dan niet meer aan bod. Vandaar `value=` bij het veld.
    st.session_state["mandje"].append({
        "rid": rid, "id": item_id, "naam": naam, "kenmerk": kenmerk,
        "conditie": conditie, "voorraad": int(voorraad), "prijs": prijs,
        "prijs_bron": prijs_bron, "code": code,
        "bedrag": float(prijs) if prijs else 0.0,
    })
    st.session_state["bevestiging"] = None
    st.session_state["fout"] = None
    st.session_state["dubbel_vraag"] = None
    st.session_state["_leeg_zoek"] = True


def regel_stuks(rid: int) -> int:
    return max(1, int(st.session_state.get(f"stuks_{rid}", 1)))


def stuks_bij(rid: int, stap: int):
    """+/- knop. Minimaal 1: een verkoop van nul kaarten bestaat niet."""
    st.session_state[f"stuks_{rid}"] = max(1, regel_stuks(rid) + stap)
    st.session_state["dubbel_vraag"] = None


def haal_weg(rid: int):
    st.session_state.setdefault("_weg_rids", []).append(rid)
    st.session_state["dubbel_vraag"] = None


def mandje_totaal() -> float:
    """Wat er in totaal de database in gaat.

    Per kaart: de som van prijs × aantal per regel. Totaalprijs: het ene bedrag
    dat met de klant is afgesproken — dan wordt er niets vermenigvuldigd, want
    dat bedrag geldt al voor de hele stapel."""
    mandje = st.session_state["mandje"]
    if st.session_state.get("prijs_modus") == "Totaalprijs" and len(mandje) > 1:
        return float(st.session_state.get("mandje_totaal") or 0)
    return sum(float(r["bedrag"]) * regel_stuks(r["rid"]) for r in mandje)


def na_succes(naam: str, bedrag: float, tx_ids: list[int] | None = None):
    st.session_state["bevestiging"] = f"✓ {naam} — €{bedrag:,.2f}"
    st.session_state["fout"] = None
    st.session_state["tx_versie"] += 1
    if tx_ids:
        # Alleen wat deze telefoon zelf heeft ingevoerd — undo raakt nooit de
        # invoer van een collega die tegelijk aan het boeken is. Eén afspraak
        # kan meer dan één regel zijn; die gaan samen weg of samen niet.
        st.session_state["mijn_invoeren"].append(
            {"ids": [int(i) for i in tx_ids], "naam": naam, "bedrag": float(bedrag)})
    st.session_state["_reset"] = True
    st.rerun()


def meld_fout(melding: str, detail: str):
    st.session_state["fout"] = {"melding": melding, "detail": detail}
    st.rerun()


init_state()

# ------------------------------------------------------------------ stijl
# Kleur per modus. Eén blik op de knop is genoeg om te zien wát je aan het boeken
# bent. De modus staat al in session_state vóór het script draait, dus dit kan in
# hetzelfde <style>-blok als de basis-CSS — één element, geen extra witruimte.

MODUS_KLEUR = {"VERKOOP": "#1B9E4B"}
_modus = st.session_state.get("modus") or "VERKOOP"
KLEUR = VRIJ_KLEUR = MODUS_KLEUR[_modus]

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
  .st-key-vastleggen button:not(:disabled) {{
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
    # Verkoop in euro's, trades in aantallen: bij een trade is het bedrag alleen
    # het verschil dat er cash bij komt of af gaat, dus een som van euro's zou
    # onderschatten hoeveel er die dag over tafel is gegaan.
    verkoop = totalen.get("verkoop", {}).get("som", 0.0)
    trades = totalen.get("trade", {})
    n_trades, cash = trades.get("groepen", 0), trades.get("som", 0.0)
    cash_teken = "−" if cash < 0 else "+"
    regel = (f'Vandaag: verkoop <b>€{geld(verkoop)}</b> &nbsp;·&nbsp; '
             f'trades: <b>{n_trades}</b>')
    if n_trades:
        regel += f' (cash {cash_teken}€{geld(abs(cash))})'
    # Inkoop kan de app niet meer boeken; wat er uit een oudere versie of uit de
    # WhatsApp-import staat, laten we wél zien — anders lijkt de dag onvolledig.
    inkoop = totalen.get("inkoop", {}).get("som", 0.0)
    if inkoop:
        regel += f' &nbsp;·&nbsp; inkoop <b>€{geld(inkoop)}</b>'
    st.markdown(f'<div class="tc-dag">{regel}</div>', unsafe_allow_html=True)

# ------------------------------------------------------------------ modus

# Eén modus = geen keuze te maken; de balk verschijnt zodra TRADE erbij komt.
if len(MODI) > 1:
    st.segmented_control("Wat leg je vast?", MODI, key="modus", width="stretch",
                         label_visibility="collapsed", required=True)
MODUS = st.session_state["modus"] or "VERKOOP"
TX_TYPE = "verkoop"

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

# ------------------------------------------------------------------ mandje
# Eén klant koopt zelden precies één kaart. Alles wat de deur uit gaat komt
# daarom eerst in een mandje; VASTLEGGEN schrijft er per kaart een eigen regel
# van weg, met een gedeelde group_id. Per kaart een regel is wat de afboeking
# nodig heeft (item_id + stuks); de group_id houdt de afspraak bij elkaar.
#
# De losse verkoop van één kaart is hetzelfde pad zonder extra stappen: een tik
# op het zoekresultaat zet 'm er meteen in mét prijs, dus zoeken → tikken →
# VASTLEGGEN is nog steeds drie handelingen.

mandje = st.session_state["mandje"]

st.text_input("Zoek kaart", key="zoekterm", placeholder="zoek kaart",
              label_visibility="collapsed")
term = st.session_state.get("zoekterm", "")
if len(term.strip()) >= 2:
    gekozen = toon_treffers(items, term, "pick_", "resultaten",
                            "Niets gevonden — gebruik *vrij invoeren* hieronder.")
    if gekozen is not None:
        r = als_dict(gekozen)
        voeg_toe(item_id=r["id"], naam=r["naam"], kenmerk=r["kenmerk"],
                 conditie=r["conditie"], voorraad=r["aantal"], prijs=r["prijs"],
                 prijs_bron=r["prijs_bron"], code=r["kenmerk"])
        st.rerun()

TOTAALPRIJS = st.session_state.get("prijs_modus") == "Totaalprijs" and len(mandje) > 1

if len(mandje) > 1:
    st.markdown(f'<div class="tc-teller">Mandje: <b>{len(mandje)}</b> '
                f'{"kaart" if len(mandje) == 1 else "kaarten"}</div>',
                unsafe_allow_html=True)

for regel in mandje:
    rid = regel["rid"]
    with st.container(key=f"regel_{rid}"):
        # Naam links, weg-knop rechts. Daaronder de bedieningsregel: het aantal
        # als kleine stepper náást de prijs, zodat een mandje van twee kaarten
        # nog steeds boven de vouw past op een telefoon.
        kol_naam, kol_weg = st.columns([6, 1], vertical_alignment="center")
        detail = " · ".join(x for x in [regel["kenmerk"], regel["conditie"]] if x)
        kol_naam.markdown(
            f'<div class="tc-regelnaam">{html.escape(regel["naam"])}'
            + (f'<span> · {html.escape(detail)}</span>' if detail else "")
            + "</div>", unsafe_allow_html=True)
        kol_weg.button("✕", key=f"weg_{rid}", width="stretch",
                       on_click=haal_weg, args=(rid,), help="uit het mandje halen")

        n = regel_stuks(rid)
        kolommen = st.columns([1, 1, 1, 3], vertical_alignment="center")
        kolommen[0].button("−", key=f"min_{rid}", width="stretch", disabled=n <= 1,
                           on_click=stuks_bij, args=(rid, -1))
        kolommen[1].markdown(f'<div class="tc-stuks">{n}×</div>',
                             unsafe_allow_html=True)
        kolommen[2].button("+", key=f"plus_{rid}", width="stretch",
                           on_click=stuks_bij, args=(rid, 1))
        with kolommen[3]:
            if TOTAALPRIJS:
                # Bij een afgesproken totaalprijs zegt een prijs per regel niets
                # meer; hij wordt straks naar rato berekend.
                st.markdown('<div class="tc-inbegrepen">in totaal</div>',
                            unsafe_allow_html=True)
            else:
                regel["bedrag"] = st.number_input(
                    "Prijs", min_value=0.0, step=0.50, format="%.2f",
                    value=float(regel["bedrag"]), key=f"prijs_{rid}",
                    label_visibility="collapsed")

        if regel["voorraad"] <= 0 and regel["id"] is not None:
            # Waarschuwen, niet blokkeren: de voorraadstand loopt achter zodra er
            # buiten de app om iets is verkocht of bijgekocht.
            st.markdown(f'<div class="{voorraad_klasse(regel["voorraad"])}">'
                        f'{voorraad_tekst(regel["voorraad"])} — vastleggen mag, de '
                        f'voorraad klopt dan alleen niet.</div>',
                        unsafe_allow_html=True)
        elif n > regel["voorraad"] and regel["id"] is not None:
            st.markdown(f'<div class="tc-laatste">Meer dan de '
                        f'{regel["voorraad"]} op voorraad</div>',
                        unsafe_allow_html=True)

if not mandje:
    st.caption("Zoek een kaart om te beginnen — of gebruik **vrij invoeren** "
               "hieronder als hij er niet in staat.")

# --- prijs: per kaart of één afgesproken bedrag ----------------------------
# Pas vanaf twee kaarten een keuze: bij één kaart is de prijs van die kaart al
# het totaal, en zou de knop alleen maar ruimte kosten.
if len(mandje) > 1:
    st.segmented_control("Prijs", PRIJS_MODI, key="prijs_modus", width="stretch",
                         label_visibility="collapsed", required=True)
    if TOTAALPRIJS:
        st.number_input("Afgesproken totaalbedrag", min_value=0.0, step=0.50,
                        format="%.2f", key="mandje_totaal",
                        label_visibility="collapsed")

TOTAAL = mandje_totaal()
STUKS_TOTAAL = sum(regel_stuks(r["rid"]) for r in mandje)

if len(mandje) > 1 and TOTAAL > 0:
    st.markdown(f'<div class="tc-totaal">{STUKS_TOTAAL} kaarten &nbsp;·&nbsp; '
                f'totaal <b>€{geld(TOTAAL)}</b></div>', unsafe_allow_html=True)

# --- dubbel-waarschuwing ---------------------------------------------------
# Alleen bij één kaart: daar zit de dubbele tik, en daar is een vergelijking op
# kaart + bedrag ook betrouwbaar. Een mandje typ je niet per ongeluk twee keer.
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

knoplabel = (f"VASTLEGGEN — {MODUS}" if len(mandje) <= 1
             else f"VASTLEGGEN — {len(mandje)} KAARTEN")
klik = st.button(knoplabel, type="primary", width="stretch",
                 disabled=not mandje or bool(vraag), key="vastleggen")

if (klik or bevestigd) and mandje:
    if TOTAAL <= 0:
        meld_fout("Vul een bedrag in.", "geen database-actie uitgevoerd")
    else:
        eerste = mandje[0]
        al_gezien = (lijkt_dubbel(recent_voor_check, eerste["id"], TOTAAL,
                                  eerste["naam"])
                     if klik and len(mandje) == 1 else None)
        if al_gezien:
            st.session_state["dubbel_vraag"] = {
                "naam": eerste["naam"], "bedrag": TOTAAL, **al_gezien}
            st.rerun()

        # Eén group_id zodra er meer dan één regel is. Bij een losse verkoop
        # laten we 'm leeg: dat is nog steeds het normale geval en een groep van
        # één zegt niets extra's.
        groep = uuid.uuid4().hex if len(mandje) > 1 else None
        if TOTAALPRIJS:
            gewichten = [(r["prijs"] or 0) * regel_stuks(r["rid"]) for r in mandje]
            bedragen = verdeel(gewichten, TOTAAL)
        else:
            bedragen = [float(r["bedrag"]) * regel_stuks(r["rid"]) for r in mandje]

        geschreven: list[int] = []
        try:
            for regel, bedrag in zip(mandje, bedragen):
                geschreven.append(schrijf_transactie(
                    item_id=regel["id"], bedrag=bedrag, tx_type=TX_TYPE,
                    ruwe_tekst=regel["naam"], code=regel.get("code"),
                    stuks=regel_stuks(regel["rid"]), group_id=groep,
                    flag=("vrij ingevoerd" if regel["id"] is None else
                          "mandje-totaal" if TOTAALPRIJS else
                          "mandje" if groep else None)))
            if len(mandje) > 1:
                label = f"{len(mandje)} kaarten"
            elif regel_stuks(eerste["rid"]) > 1:
                label = f"{regel_stuks(eerste['rid'])}× {eerste['naam']}"
            else:
                label = eerste["naam"]
            na_succes(label, TOTAAL, geschreven)
        except SQLAlchemyError as e:
            engine().dispose()
            # Halverwege vastgelopen: wat er al in staat blijft staan en is via
            # undo in één keer terug te draaien. Stil doorgaan zou een halve
            # afspraak in de database achterlaten zonder dat iemand het ziet.
            rest = len(mandje) - len(geschreven)
            meld_fout(
                f"NIET volledig vastgelegd: {len(geschreven)} van {len(mandje)} "
                f"kaarten staan erin, {rest} niet. Je mandje staat er nog.",
                str(e)[:800])

# --- vangnet ---------------------------------------------------------------
with st.expander("Kaart staat er niet in → vrij invoeren"):
    st.text_input("Naam", key="vrij_naam", placeholder="naam kaart of product",
                  label_visibility="collapsed")
    # Code is optioneel, maar wél het enige haakje om deze regel later alsnog
    # aan de inventaris te koppelen — hij heeft geen item_id.
    st.text_input("Code", key="vrij_code",
                  placeholder="code, bijv. 173/195 of swsh260 (mag leeg)",
                  label_visibility="collapsed")
    if st.button("+ in het mandje", type="primary", width="stretch",
                 key="vrij_knop"):
        naam = (st.session_state.get("vrij_naam") or "").strip()
        if not naam:
            st.error("Vul een naam in.")
        else:
            voeg_toe(item_id=None, naam=naam,
                     code=(st.session_state.get("vrij_code") or "").strip())
            st.session_state["_leeg_vrij"] = True
            st.rerun()

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
                weg = verwijder_transacties(laatste["ids"])
                mijn.pop()
                st.session_state["undo_vraag"] = False
                st.session_state["tx_versie"] += 1
                st.session_state["bevestiging"] = (
                    f"↩ Verwijderd: {laatste['naam']} — €{laatste['bedrag']:,.2f}"
                    if weg else f"↩ {laatste['naam']} stond er al niet meer in.")
                st.rerun()
            except RuntimeError as e:
                st.session_state["undo_vraag"] = False
                meld_fout(f"NIET verwijderd: {laatste['naam']} is al van de "
                          f"voorraad afgeboekt. Corrigeer dit in de database.",
                          str(e))
            except SQLAlchemyError as e:
                engine().dispose()
                st.session_state["undo_vraag"] = False
                meld_fout(f"NIET verwijderd: {laatste['naam']}. Probeer opnieuw.",
                          str(e)[:800])
        if kol_nee.button("Nee, laten staan", key="undo_nee", width="stretch"):
            st.session_state["undo_vraag"] = False
            st.rerun()
    elif st.button(f"↩ Laatste invoer terugdraaien — €{geld(laatste['bedrag'])}"
                   + (f" ({len(laatste['ids'])} regels)"
                      if len(laatste["ids"]) > 1 else ""),
                   key="undo", width="stretch"):
        st.session_state["undo_vraag"] = True
        st.rerun()

# ------------------------------------------------------------------ laatste 10

recent = recent_voor_check

if recent is not None and not recent.empty:
    regels = []
    for _, r in recent.iterrows():
        bedrag = float(pd.to_numeric(r["bedrag"], errors="coerce") or 0)
        teken = "−" if str(r["type"]) == "inkoop" else "+"
        regels.append(f'{str(r["tijd"])[:5]} &nbsp; <b>{teken}€{geld(bedrag)}</b> '
                      f'&nbsp; {html.escape(str(r["ruwe_tekst"])[:38])}')
    st.markdown(f'<div class="tc-log">{"<br>".join(regels)}</div>',
                unsafe_allow_html=True)
