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
"""

from __future__ import annotations

import os
import sys
import unicodedata
from datetime import date, datetime
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

st.markdown("""
<style>
  /* Streamlit-balk weg: scheelt ruis én schermruimte op een telefoon */
  header[data-testid="stHeader"] {display: none;}
  .block-container {padding-top: 1.5rem; padding-bottom: 4rem; max-width: 34rem;}
  div[data-testid="stVerticalBlock"] {gap: .9rem;}

  /* knoppen: ruime tap-targets */
  .stButton > button {min-height: 3.25rem; border-radius: .75rem;}
  div[data-testid="stSegmentedControl"] button {min-height: 3.25rem;
      font-size: 1.05rem; font-weight: 600;}

  /* zoekbalk: prominent */
  .st-key-zoekterm input {font-size: 1.25rem !important; padding: .9rem .75rem !important;}

  /* zoekresultaten: naam vet, daaronder klein set + staat + prijs */
  .st-key-resultaten div[data-testid="stVerticalBlock"] {gap: .45rem;}
  [class*="st-key-pick_"] button {min-height: 3.5rem; padding: .6rem .9rem;}
  [class*="st-key-pick_"] button > div {width: 100%; justify-content: flex-start;}
  [class*="st-key-pick_"] button p {white-space: pre-line; text-align: left;
      line-height: 1.35; font-size: 1.05rem; font-weight: 600; margin: 0;}
  [class*="st-key-pick_"] button p span {font-size: .82rem; font-weight: 400;}

  /* bedrag: het getal is het belangrijkste op het scherm */
  .st-key-bedrag input {font-size: 2rem !important; font-weight: 700;
      text-align: right; padding: .6rem .75rem !important;}
  .st-key-bedrag [data-testid="stNumberInputContainer"]::before,
  .st-key-vrij_bedrag [data-testid="stNumberInputContainer"]::before {
      content: "€"; align-self: center; padding-left: .8rem; font-weight: 700;
      opacity: .45;}
  .st-key-bedrag [data-testid="stNumberInputContainer"]::before {font-size: 1.6rem;}

  /* VASTLEGGEN: grote knop onderaan */
  .st-key-vastleggen button {min-height: 4rem; font-size: 1.2rem; font-weight: 700;
      letter-spacing: .03em;}

  /* bevestiging: groot, kort, vervaagt vanzelf */
  @keyframes tc-fade {0%, 65% {opacity: 1;} 100% {opacity: 0; visibility: hidden;}}
  .tc-ok {animation: tc-fade 6s ease-in forwards; background: rgba(33,195,84,.14);
      border-left: .35rem solid rgb(33,195,84); border-radius: .6rem;
      padding: .9rem 1rem; font-size: 1.25rem; font-weight: 700; line-height: 1.3;
      margin-bottom: .5rem;}

  /* laatste invoeren: compacte regels in plaats van een tabel */
  .tc-log {font-size: .85rem; opacity: .7; line-height: 1.9;}
  .tc-log b {font-weight: 600;}
</style>
""", unsafe_allow_html=True)

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
    event_id, item_id, datum, tijd, kanaal, type, bedrag, afzender, flag, ruwe_tekst
) VALUES (
    :event_id, :item_id, :datum, :tijd, :kanaal, :type, :bedrag, :afzender,
    :flag, :ruwe_tekst
)
RETURNING id
""")


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
               grade, comp_prijs
        FROM items ORDER BY officiele_naam NULLS LAST, onze_naam
    """)
    df["comp_prijs"] = pd.to_numeric(df["comp_prijs"], errors="coerce")
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


@st.cache_data(ttl=20, show_spinner=False)
def laatste_transacties(eid: int, versie: int) -> pd.DataFrame:
    """`versie` telt op na elke eigen invoer en breekt zo de cache open — géén
    underscore-prefix, want die sluit een argument uit van de cache-sleutel."""
    return lees("""
        SELECT t.tijd, t.type, t.bedrag, t.afzender, t.ruwe_tekst, t.flag
        FROM transactions t WHERE t.event_id = :eid
        ORDER BY t.id DESC LIMIT 10
    """, {"eid": eid})


def schrijf_transactie(*, item_id, bedrag, tx_type, ruwe_tekst, flag=None) -> int:
    """Eén losse insert. Geen retry: een dubbel geboekte verkoop is erger dan
    een foutmelding waarna je zelf opnieuw tikt."""
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
        }).scalar()


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
    return treffers.sort_values(["_start", "naam", "comp_prijs"],
                                ascending=[True, True, False])


# ------------------------------------------------------------------ sessie


def init_state():
    st.session_state.setdefault("modus", "VERKOOP")
    st.session_state.setdefault("sel", None)          # gekozen item (dict)
    st.session_state.setdefault("bevestiging", None)
    st.session_state.setdefault("fout", None)
    st.session_state.setdefault("tx_versie", 0)       # tikt de lijst-cache aan
    if st.session_state.pop("_reset", False):
        st.session_state["sel"] = None
        st.session_state["zoekterm"] = ""
        st.session_state["bedrag"] = 0.0
        st.session_state["vrij_naam"] = ""
        st.session_state["vrij_bedrag"] = 0.0


def prefill_bedrag():
    """Bij verkoop de comp_prijs voorinvullen, bij inkoop leeg (0,00)."""
    sel = st.session_state.get("sel")
    prijs = 0.0
    if sel and st.session_state["modus"] == "VERKOOP" and sel.get("comp_prijs"):
        prijs = float(sel["comp_prijs"])
    st.session_state["bedrag"] = prijs


def kies_item(rij: dict):
    st.session_state["sel"] = rij
    st.session_state["bevestiging"] = None
    st.session_state["fout"] = None
    prefill_bedrag()


def na_succes(naam: str, bedrag: float):
    st.session_state["bevestiging"] = f"✓ {naam} — €{bedrag:,.2f}"
    st.session_state["fout"] = None
    st.session_state["tx_versie"] += 1
    st.session_state["_reset"] = True
    st.rerun()


def meld_fout(melding: str, detail: str):
    st.session_state["fout"] = {"melding": melding, "detail": detail}
    st.rerun()


init_state()

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

# ------------------------------------------------------------------ modus

st.segmented_control("Wat leg je vast?", MODI, key="modus", width="stretch",
                     on_change=prefill_bedrag, label_visibility="collapsed",
                     required=True)
MODUS = st.session_state["modus"] or "VERKOOP"
TX_TYPE = "verkoop" if MODUS == "VERKOOP" else "inkoop"

VANDAAG = date.today()
if VANDAAG not in BEURSDAGEN:
    st.caption(f"{VANDAAG.strftime('%d-%m-%Y')} is geen beursdag — invoer telt wél mee.")

# ------------------------------------------------------------------ meldingen

if st.session_state["bevestiging"]:
    st.markdown(f'<div class="tc-ok">{st.session_state["bevestiging"]}</div>',
                unsafe_allow_html=True)

if st.session_state["fout"]:
    st.error(st.session_state["fout"]["melding"], icon="⚠️")
    with st.expander("Details"):
        st.code(st.session_state["fout"]["detail"])

# ------------------------------------------------------------------ kaart kiezen

try:
    items = laad_items()
except SQLAlchemyError as e:
    st.error("Geen verbinding met de database.", icon="🚫")
    with st.expander("Details"):
        st.code(str(e))
    st.stop()

sel = st.session_state["sel"]

if sel:
    regels = [x for x in [sel.get("kenmerk"), sel.get("conditie")] if x]
    st.markdown(f"### {sel['naam']}")
    if regels:
        st.caption(" · ".join(regels))
    if st.button("✕ andere kaart", key="deselect"):
        st.session_state["sel"] = None
        st.rerun()
else:
    st.text_input("Zoek kaart", key="zoekterm", placeholder="zoek kaart",
                  label_visibility="collapsed")
    term = st.session_state.get("zoekterm", "")
    if len(term.strip()) >= 2:
        treffers = zoek(items, term)
        with st.container(key="resultaten"):
            for _, r in treffers.head(MAX_RESULTATEN).iterrows():
                prijs = f"€{r['comp_prijs']:,.2f}" if pd.notna(r["comp_prijs"]) else "€ ?"
                detail = " · ".join(x for x in [kenmerk(r), conditie(r), prijs] if x)
                # Zachte regelafbreking (\n) + CSS `white-space: pre-line` geeft de
                # tweede regel; :gray[] maakt er een span van die klein wordt gezet.
                if st.button(f"{r['naam']}\n:gray[{detail}]", key=f"pick_{r['id']}",
                             width="stretch"):
                    kies_item({"id": int(r["id"]), "naam": r["naam"],
                               "kenmerk": kenmerk(r), "conditie": conditie(r),
                               "comp_prijs": None if pd.isna(r["comp_prijs"])
                               else float(r["comp_prijs"])})
                    st.rerun()
            if treffers.empty:
                st.caption("Niets gevonden — gebruik *vrij invoeren* hieronder.")

# ------------------------------------------------------------------ vastleggen

st.number_input("Bedrag", min_value=0.0, step=0.50, format="%.2f", key="bedrag",
                label_visibility="collapsed")

if st.button(f"VASTLEGGEN — {MODUS}", type="primary", width="stretch",
             disabled=sel is None, key="vastleggen") and sel:
    bedrag = float(st.session_state["bedrag"])
    if bedrag <= 0:
        meld_fout("Vul een bedrag in.", "geen database-actie uitgevoerd")
    else:
        try:
            schrijf_transactie(item_id=sel["id"], bedrag=bedrag, tx_type=TX_TYPE,
                               ruwe_tekst=sel["naam"])
            na_succes(sel["naam"], bedrag)
        except SQLAlchemyError as e:
            engine().dispose()
            meld_fout(f"NIET vastgelegd: {sel['naam']} €{bedrag:,.2f}. "
                      "Je invoer staat er nog — probeer opnieuw.", str(e)[:800])

# ------------------------------------------------------------------ vangnet

with st.expander("Kaart staat er niet in → vrij invoeren"):
    st.text_input("Naam", key="vrij_naam", placeholder="naam kaart of product",
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
                schrijf_transactie(
                    item_id=None, bedrag=vb,
                    tx_type="verkoop" if vrij_type == "VERKOOP" else "inkoop",
                    ruwe_tekst=naam, flag="vrij ingevoerd")
                na_succes(naam, vb)
            except SQLAlchemyError as e:
                engine().dispose()
                meld_fout(f"NIET vastgelegd: {naam} €{vb:,.2f}. "
                          "Je invoer staat er nog — probeer opnieuw.", str(e)[:800])

# ------------------------------------------------------------------ laatste 10

try:
    recent = laatste_transacties(event_id(), st.session_state["tx_versie"])
except SQLAlchemyError:
    recent = None

if recent is not None and not recent.empty:
    regels = []
    for _, r in recent.iterrows():
        bedrag = float(pd.to_numeric(r["bedrag"], errors="coerce") or 0)
        # hele euro's kort, centen alleen tonen als ze er zijn
        geld = f"{bedrag:,.0f}" if bedrag == int(bedrag) else f"{bedrag:,.2f}"
        teken = "−" if r["type"] == "inkoop" else "+"
        regels.append(f'{str(r["tijd"])[:5]} &nbsp; <b>{teken}€{geld}</b> '
                      f'&nbsp; {str(r["ruwe_tekst"])[:38]}')
    st.markdown(f'<div class="tc-log">{"<br>".join(regels)}</div>',
                unsafe_allow_html=True)
