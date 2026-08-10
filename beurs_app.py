"""TC beurs — snelle invoer voor op de beursvloer.

Vier mensen leggen tegelijk op hun telefoon verkopen en inkopen vast. Elke invoer
is één losse INSERT in `transactions`, gekoppeld aan één event. Er wordt niets
afgeboekt uit `items` — alleen transacties vullen.

    streamlit run beurs_app.py

Ontwerpkeuzes:
- De items-tabel wordt één keer opgehaald en in cache gehouden; zoeken gebeurt
  lokaal. Zo is zoeken instant en kost het geen netwerk op een slechte beursvloer.
- Schrijven gebeurt zonder automatische retry (pool_pre_ping vangt dode
  connecties al af). Faalt het toch, dan blijft de invoer staan met een
  duidelijke foutmelding en een knop "probeer opnieuw" — nooit stil falen.
- Wie je bent staat in de URL (?wie=Kevin), zodat een refresh je keuze niet wist.
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

TEAM = ["Jishnu", "Mehdi", "Kevin", "Ömer"]
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

st.markdown("""
<style>
  .block-container {padding-top: 1.2rem; padding-bottom: 3rem;}
  .stButton > button {min-height: 3rem; font-size: 1.02rem; line-height: 1.25;}
  .stButton > button p {font-size: 1.02rem;}
  div[data-testid="stSegmentedControl"] button {min-height: 3rem; font-size: 1.05rem;}
  input[type="text"], input[type="number"] {font-size: 1.1rem !important;}
  .tc-bar {display:flex; justify-content:space-between; align-items:center;
           font-size:.9rem; opacity:.8; margin-bottom:.4rem;}
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


def schrijf_transactie(*, item_id, bedrag, tx_type, afzender, ruwe_tekst, flag=None) -> int:
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
            "afzender": afzender,
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
    st.session_state.setdefault("wie", st.query_params.get("wie"))
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


def na_succes(tekst: str):
    st.session_state["bevestiging"] = tekst
    st.session_state["fout"] = None
    st.session_state["tx_versie"] += 1
    st.session_state["_reset"] = True
    st.rerun()


init_state()

# ------------------------------------------------------------------ pincode


PIN = secret("TC_BEURS_PIN")
if PIN and not st.session_state.get("ontgrendeld"):
    st.title("🃏 TC Beurs")
    ingevoerd = st.text_input("Pincode", type="password", key="pin_invoer")
    if st.button("Open", type="primary", width="stretch"):
        if ingevoerd == PIN:
            st.session_state["ontgrendeld"] = True
            st.rerun()
        else:
            st.error("Verkeerde pincode.")
    st.stop()

# ------------------------------------------------------------------ wie ben je

if st.session_state["wie"] not in TEAM:
    st.title("🃏 TC Beurs")
    st.subheader("Wie ben je?")
    for naam in TEAM:
        if st.button(naam, key=f"wie_{naam}", width="stretch", type="primary"):
            st.session_state["wie"] = naam
            st.query_params["wie"] = naam
            st.rerun()
    st.caption(EVENT_NAAM)
    st.stop()

WIE = st.session_state["wie"]
VANDAAG = date.today()

kop, knop = st.columns([3, 1], vertical_alignment="center")
kop.markdown(f"**👤 {WIE}** · {VANDAAG.strftime('%d-%m-%Y')}")
if knop.button("wissel", key="wissel", width="stretch"):
    st.session_state["wie"] = None
    st.query_params.clear()
    st.rerun()

if VANDAAG not in BEURSDAGEN:
    st.warning(f"Let op: vandaag ({VANDAAG.strftime('%d-%m-%Y')}) is geen beursdag. "
               "Invoer komt wél in de database — dit is dus een test.", icon="🧪")

st.segmented_control("Wat leg je vast?", MODI, key="modus", width="stretch",
                     on_change=prefill_bedrag, label_visibility="collapsed",
                     required=True)
MODUS = st.session_state["modus"] or "VERKOOP"
TX_TYPE = "verkoop" if MODUS == "VERKOOP" else "inkoop"

# ------------------------------------------------------------------ bevestiging

if st.session_state["bevestiging"]:
    st.success(st.session_state["bevestiging"], icon="✅")

if st.session_state["fout"]:
    st.error(st.session_state["fout"]["melding"], icon="⚠️")
    st.caption("Je invoer staat hieronder nog — tik nogmaals op VASTLEGGEN "
               "zodra je weer verbinding hebt. Niets is verloren.")
    with st.expander("Technische details"):
        st.code(st.session_state["fout"]["detail"])

# ------------------------------------------------------------------ zoeken

try:
    items = laad_items()
except SQLAlchemyError as e:
    st.error("Geen verbinding met de database. Controleer je internet en ververs.", icon="🚫")
    with st.expander("Technische details"):
        st.code(str(e))
    st.stop()

sel = st.session_state["sel"]

if sel:
    st.markdown(f"### {sel['naam']}")
    onze = sel.get("onze_naam")
    st.caption(" · ".join(x for x in [
        sel.get("kenmerk"), sel.get("conditie"), sel.get("categorie"),
        f"bij ons: {onze}" if onze and onze != sel["naam"] else None] if x))
    if st.button("← andere kaart kiezen", key="deselect", width="stretch"):
        st.session_state["sel"] = None
        st.rerun()
else:
    st.text_input("Zoek kaart", key="zoekterm", placeholder="typ een paar letters…",
                  label_visibility="collapsed")
    term = st.session_state.get("zoekterm", "")
    if len(term.strip()) >= 2:
        treffers = zoek(items, term)
        if treffers.empty:
            st.info("Geen kaart gevonden. Gebruik hieronder 'vrij invoeren'.")
        else:
            for _, r in treffers.head(MAX_RESULTATEN).iterrows():
                naam = r["naam"] if len(r["naam"]) <= 38 else r["naam"][:37] + "…"
                prijs = f"€{r['comp_prijs']:.2f}" if pd.notna(r["comp_prijs"]) else "€ ?"
                label = " · ".join(x for x in [naam, kenmerk(r), conditie(r), prijs] if x)
                if st.button(label, key=f"pick_{r['id']}", width="stretch"):
                    kies_item({"id": int(r["id"]), "naam": r["naam"],
                               "onze_naam": r["onze_naam"], "kenmerk": kenmerk(r),
                               "conditie": conditie(r), "categorie": r["categorie"],
                               "comp_prijs": None if pd.isna(r["comp_prijs"])
                               else float(r["comp_prijs"])})
                    st.rerun()
            if len(treffers) > MAX_RESULTATEN:
                st.caption(f"{len(treffers)} treffers — typ meer letters voor minder.")
    elif term.strip():
        st.caption("Typ minstens 2 letters.")

# ------------------------------------------------------------------ vastleggen

bedrag = st.number_input(f"Bedrag ({MODUS.lower()}) in €", min_value=0.0, step=0.50,
                         format="%.2f", key="bedrag")

vastleggen = st.button(f"VASTLEGGEN — {MODUS}", type="primary", width="stretch",
                       disabled=sel is None,
                       key="vastleggen")
if sel is None:
    st.caption("Kies eerst een kaart, of gebruik 'vrij invoeren' hieronder.")

if vastleggen and sel:
    if bedrag <= 0:
        st.session_state["fout"] = {"melding": "Bedrag is €0,00 — vul een bedrag in.",
                                    "detail": "geen database-actie uitgevoerd"}
        st.rerun()
    else:
        try:
            schrijf_transactie(item_id=sel["id"], bedrag=float(bedrag), tx_type=TX_TYPE,
                               afzender=WIE, ruwe_tekst=sel["naam"])
            na_succes(f"{sel['naam']} — €{bedrag:.2f} {MODUS.lower()} vastgelegd door {WIE}")
        except SQLAlchemyError as e:
            engine().dispose()
            st.session_state["fout"] = {
                "melding": f"Opslaan mislukt — {sel['naam']} €{bedrag:.2f} "
                           f"{MODUS.lower()} is NIET vastgelegd.",
                "detail": str(e)[:800]}
            st.rerun()

# ------------------------------------------------------------------ vangnet

with st.expander("🆘 Kaart staat er niet in → vrij invoeren"):
    st.text_input("Naam van de kaart / het product", key="vrij_naam",
                  placeholder="bijv. Umbreon VMAX Alt Art JP")
    st.number_input("Bedrag in €", min_value=0.0, step=0.50, format="%.2f",
                    key="vrij_bedrag")
    vrij_type = st.segmented_control("Soort", MODI, key="vrij_modus", default=MODUS,
                                     required=True, width="stretch") or MODUS
    if st.button("VASTLEGGEN (vrij)", type="primary", width="stretch", key="vrij_knop"):
        naam = (st.session_state.get("vrij_naam") or "").strip()
        vb = float(st.session_state.get("vrij_bedrag") or 0)
        if not naam:
            st.error("Vul een naam in.")
        elif vb <= 0:
            st.error("Vul een bedrag in.")
        else:
            try:
                schrijf_transactie(
                    item_id=None, bedrag=vb,
                    tx_type="verkoop" if vrij_type == "VERKOOP" else "inkoop",
                    afzender=WIE, ruwe_tekst=naam, flag="vrij ingevoerd")
                na_succes(f"{naam} — €{vb:.2f} {vrij_type.lower()} vastgelegd door {WIE} "
                          "(vrij ingevoerd)")
            except SQLAlchemyError as e:
                engine().dispose()
                st.session_state["fout"] = {
                    "melding": f"Opslaan mislukt — {naam} €{vb:.2f} is NIET vastgelegd.",
                    "detail": str(e)[:800]}
                st.rerun()

# ------------------------------------------------------------------ laatste 10

st.divider()
rij1, rij2 = st.columns([3, 1], vertical_alignment="center")
rij1.markdown("**Laatste 10 van deze beurs**")
if rij2.button("ververs", key="ververs", width="stretch"):
    st.session_state["tx_versie"] += 1
    laad_items.clear()
    st.rerun()

try:
    recent = laatste_transacties(event_id(), st.session_state["tx_versie"])
except SQLAlchemyError:
    st.caption("Lijst kon niet worden opgehaald (verbinding). Invoeren werkt mogelijk wel.")
else:
    if recent.empty:
        st.caption("Nog niets vastgelegd.")
    else:
        toon = recent.rename(columns={"ruwe_tekst": "kaart"})
        toon["bedrag"] = pd.to_numeric(toon["bedrag"], errors="coerce").map(
            lambda v: f"€{v:.2f}" if pd.notna(v) else "")
        toon["tijd"] = toon["tijd"].astype(str).str.slice(0, 5)
        st.dataframe(toon[["tijd", "type", "bedrag", "afzender", "kaart", "flag"]],
                     hide_index=True, width="stretch")

st.caption(f"{EVENT_NAAM} · event #{event_id()}")
