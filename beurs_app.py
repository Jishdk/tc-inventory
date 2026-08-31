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
- VERKOOP en TRADE delen hetzelfde mandje. Het verschil zit aan het eind: bij
  een verkoop draagt elke kaart een prijs, bij een trade gaan de kaarten op €0
  de deur uit en staat het geld in één losse cash-regel van dezelfde groep.
  Wat er terugkomt leggen we niet per kaart vast — dat staat in de foto in
  WhatsApp, en die is op de tijdstempel terug te vinden.
"""

from __future__ import annotations

import html
import math
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

# Het event staat bewust *niet* in de code. Het stond hier hardcoded, en na de
# beurs van 29-08 bleek de invoer daardoor op het vorige event te zijn geboekt —
# met de datum van invoer in plaats van de beursdag. Zie `event()` hieronder voor
# de volgorde waarin het nu wordt bepaald.

AFZENDER = "TC"          # één gedeelde gebruiker
MODI = ["VERKOOP", "TRADE"]
PRIJS_MODI = ["Per kaart", "Totaalprijs"]
# Wat de klant bijlegt of van ons krijgt. De richting staat in de knop zelf: geen
# plus/min waar je op de beursvloer overheen kijkt en de trade omgekeerd inboekt.
CASH_ONTVANGEN = "WIJ ONTVANGEN €"
CASH_BIJLEGGEN = "WIJ LEGGEN BIJ €"
CASH_RICHTINGEN = [CASH_ONTVANGEN, CASH_BIJLEGGEN]
MAX_RESULTATEN = 10

# Hardlopers: één tik zet het product in het mandje met zijn eigen prijs erbij.
# `knop` is wat er op de knop staat — kort houden, het moet naast twee andere op
# een telefoon passen. `zoek` is waarmee we het échte item opzoeken, met dezelfde
# zoekfunctie als de zoekbalk. Optioneel `code` om te scheiden als één zoekterm
# meerdere gelijknamige kaarten raakt.
#
# Levert een zoekterm niet precies één item op, dan verschijnt de knop niet: een
# verkeerd product in het mandje is erger dan een knop die er niet is. Welke er
# afvielen staat in een grijze regel onder de rij, zodat een verouderde lijst
# opvalt in plaats van stil te verdwijnen.
SNELKNOPPEN = [
    {"knop": "Prismatic", "zoek": "prismatic booster"},
    {"knop": "Twilight", "zoek": "twilight masquerade booster"},
    {"knop": "Sleeved", "zoek": "sleeved booster"},
    {"knop": "Tins", "zoek": "tins"},
    {"knop": "Mega Dream", "zoek": "mega dream"},
    {"knop": "Black Bolt", "zoek": "black bolt"},
]
SNELKNOPPEN_PER_RIJ = 3   # drie naast elkaar past op 390 px; Streamlit wrapt niet zelf
KORTING = 10              # percentage van de "comp −10%"-knop

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
  /* ---- huisstijl-tokens ------------------------------------------------
     Dark-first, cinematisch, terughoudend. Alles hieronder rekent met deze
     variabelen, dus een kleurwijziging is één regel.

     Gold is bewust schaars: alleen graded/premium (de grade van een slab en de
     flits na het vastleggen). Rood is de hoofdactie en verder niets — daardoor
     is VASTLEGGEN het enige echt luide element op het scherm.

     Geen display-font. Dit is een interne app op een druk scherm; de systeem-
     UI-font leest onder tijdsdruk beter dan welke brand-letter ook. */
  :root {
      --page: #111318;
      --card: #1A1D26;
      --elev: #232733;
      --line: #2E3340;
      --text: #EDEFF4;
      --muted: #828A9C;
      /* Dezelfde rol als --muted, maar een tint lichter voor tekst op de
         elevated surface. #828A9C haalt daar 4.30 tegen de 4.5 die WCAG AA
         vraagt; op --page en --card haalt hij 4.84 en mag hij blijven. */
      --muted-elev: #9AA2B4;
      --red: #F20519;
      --red-line: rgba(242, 5, 25, .45);
      --gold: #F2CF63;
      --gold-soft: rgba(242, 207, 99, .10);
      --glow-red: 0 8px 26px -8px rgba(242, 5, 25, .70),
                  0 0 0 1px rgba(242, 5, 25, .35);
      --glow-gold: 0 8px 26px -10px rgba(242, 207, 99, .55),
                   0 0 0 1px rgba(242, 207, 99, .30);
      --r: 14px;
      --r-sm: 10px;
      /* cijfers tabulair: bedragen lijnen uit en verspringen niet tijdens tikken */
      --cijfers: ui-monospace, "SF Mono", "Cascadia Mono", "Roboto Mono",
                 Menlo, Consolas, monospace;
  }

  html, body, .stApp, [data-testid="stAppViewContainer"] {background: var(--page);}
  .stApp, body {color: var(--text);}

  /* Streamlit-balk weg: scheelt ruis én schermruimte op een telefoon */
  header[data-testid="stHeader"] {display: none;}
  .block-container {padding-top: 1.1rem; padding-bottom: 4rem; max-width: 34rem;}
  div[data-testid="stVerticalBlock"] {gap: .75rem;}

  /* ---- knoppen: ruime tap-targets op card-surface ---------------------- */
  .stButton > button {min-height: 3.25rem; border-radius: var(--r);
      background: var(--card); border: 1px solid var(--line);
      color: var(--text); font-weight: 600;}
  .stButton > button:hover {border-color: var(--muted); color: var(--text);}
  .stButton > button:disabled, .stButton > button:disabled:hover {
      background: var(--card); border-color: var(--line); color: #4C5364;}

  /* Segmented control: Streamlit 1.60 zet er data-testid="stButtonGroup" op,
     oudere versies "stSegmentedControl". Beide noemen overleeft een upgrade in
     welke richting dan ook — het kost één selector. */
  div[data-testid="stSegmentedControl"] button,
  div[data-testid="stButtonGroup"] button {min-height: 3.25rem;
      font-size: 1.05rem; font-weight: 700; background: var(--card);
      border: 1px solid var(--line); color: var(--muted);}

  /* Streamlit stapelt kolommen onder een telefoonbreedte; hier moeten ze juist
     náást elkaar blijven (de +/- stepper, ja/nee-vragen). */
  div[data-testid="stHorizontalBlock"] {flex-wrap: nowrap; gap: .5rem;}
  div[data-testid="stColumn"] {min-width: 0 !important;}

  /* ---- invoervelden ---------------------------------------------------- */
  .stTextInput input, .stNumberInput input {background: var(--card) !important;
      color: var(--text) !important;}
  [data-testid="stTextInputRootElement"],
  [data-testid="stNumberInputContainer"] {background: var(--card);
      border: 1px solid var(--line); border-radius: var(--r-sm);}
  .stTextInput input::placeholder {color: var(--muted); opacity: 1;}

  /* ---- dagtotaal: rustige balk op card-surface ------------------------- */
  .tc-dag {background: var(--card); border: 1px solid var(--line);
      border-radius: var(--r-sm); padding: .5rem .7rem; margin-bottom: .1rem;
      text-align: center; font-size: .9rem; color: var(--muted);
      line-height: 1.45;}
  .tc-dag b {font-weight: 700; color: var(--text);
      font-family: var(--cijfers); font-variant-numeric: tabular-nums;
      letter-spacing: -.02em;}

  /* ---- snelknoppen: kleine cards met het 4px-frame als accent ---------- */
  [class*="st-key-snel_"] button {min-height: 3.4rem; padding: .35rem .5rem;
      background: var(--card); border: 1px solid var(--line);
      border-left: 4px solid var(--elev); border-radius: var(--r-sm);}
  [class*="st-key-snel_"] button:hover {border-left-color: var(--red-line);}
  [class*="st-key-snel_"] button p {white-space: pre-line; margin: 0;
      line-height: 1.3; font-size: .92rem; font-weight: 700; color: var(--text);}
  /* !important omdat Streamlit :gray[] zelf op rgba(250,250,250,.6) zet en met
     die specificiteit wint — die tint zakt op deze donkere card naar ruim onder
     de leesbaarheidsdrempel. */
  [class*="st-key-snel_"] button p span {font-size: .82rem; font-weight: 600;
      color: var(--muted-elev) !important; font-family: var(--cijfers);
      font-variant-numeric: tabular-nums;}

  /* ---- zoekbalk: prominent -------------------------------------------- */
  .st-key-zoekterm input {font-size: 1.25rem !important;
      padding: .9rem .75rem !important;}

  /* ---- zoekresultaten: naam vet, daaronder klein set + staat + prijs ----
     st-key-resultaten zit óp het verticale blok, niet eromheen — een
     nakomeling-selector vindt hier dus niets. */
  div.st-key-resultaten {gap: .4rem;}
  [class*="st-key-pick_"] button {min-height: 3.5rem; padding: .6rem .9rem;
      background: var(--card); border: 1px solid var(--line);
      border-left: 4px solid var(--elev); border-radius: var(--r-sm);}
  [class*="st-key-pick_"] button:hover {border-left-color: var(--red-line);}
  [class*="st-key-pick_"] button > div {width: 100%; justify-content: flex-start;}
  [class*="st-key-pick_"] button p {
      white-space: pre-line; text-align: left;
      line-height: 1.35; font-size: 1.05rem; font-weight: 600; margin: 0;
      color: var(--text);}
  [class*="st-key-pick_"] button p span {font-size: .82rem; font-weight: 400;
      color: var(--muted) !important; font-variant-numeric: tabular-nums;}

  /* rekenregel onder het bedrag (3 x EUR 15 = EUR 45) */
  .tc-comp {font-size: .9rem; color: var(--muted); margin: 0;
      font-variant-numeric: tabular-nums;}

  /* ---- mandje ----------------------------------------------------------
     st-key-regel_ zit óp het verticale blok van de regel, niet eromheen. Het
     4px-frame links is het card-accent uit de huisstijl. */
  div[class*="st-key-regel_"] {gap: .15rem; background: var(--card);
      border: 1px solid var(--line); border-left: 4px solid var(--elev);
      border-radius: var(--r-sm); padding: .5rem .55rem .3rem;}
  .tc-regelnaam {font-size: 1.05rem; font-weight: 600; line-height: 1.3;
      color: var(--text); overflow: hidden; text-overflow: ellipsis;
      white-space: nowrap;}
  .tc-regelnaam span {font-size: .82rem; font-weight: 400; color: var(--muted);}
  /* Gold: alleen graded. Niet strooien — dit is het enige plekje waar een kaart
     zichzelf als premium mag aankondigen. */
  .tc-regelnaam span.tc-grade {color: var(--gold); font-weight: 700;}
  [class*="st-key-weg_"] button {min-height: 2.4rem; padding: 0;
      color: var(--muted); border-color: transparent; background: transparent;}
  [class*="st-key-weg_"] button:hover {color: var(--text);
      border-color: transparent; background: var(--elev);}

  /* stepper: klein en inline, zodat VASTLEGGEN op een telefoon boven de vouw
     blijft — een teller van drie grote blokken duwde 'm eruit */
  [class*="st-key-min_"] button, [class*="st-key-plus_"] button {
      min-height: 2.6rem; padding: 0; font-size: 1.15rem; font-weight: 700;
      background: var(--elev); border-color: var(--line);}
  .tc-stuks {text-align: center; font-size: 1.1rem; font-weight: 700;
      line-height: 2.6rem; color: var(--text); font-family: var(--cijfers);}
  .tc-inbegrepen {font-size: .85rem; color: var(--muted); text-align: right;
      line-height: 2.6rem; padding-right: .5rem;}
  .tc-teller {font-size: .78rem; font-weight: 700; color: var(--muted);
      margin: .2rem 0 -.25rem; letter-spacing: .08em; text-transform: uppercase;}
  .tc-totaal {text-align: right; font-size: 1rem; margin: -.2rem 0 .2rem;
      color: var(--muted); font-variant-numeric: tabular-nums;}
  .tc-totaal b {font-size: 1.4rem; color: var(--text);
      font-family: var(--cijfers); letter-spacing: -.03em;}

  /* ---- bedragen: tabulair, het getal is het belangrijkste -------------- */
  [class*="st-key-regelprijs_"] input {font-size: 1.45rem !important;
      font-weight: 700; text-align: right; padding: .35rem .6rem !important;
      font-family: var(--cijfers); font-variant-numeric: tabular-nums;
      letter-spacing: -.03em;}
  [class*="st-key-regelprijs_"] [data-testid="stNumberInputContainer"]::before,
  .st-key-mandje_totaal [data-testid="stNumberInputContainer"]::before,
  .st-key-cash_bedrag [data-testid="stNumberInputContainer"]::before {
      content: "€"; align-self: center; padding-left: .7rem; font-weight: 700;
      color: var(--muted);}
  /* +/- steppers van de getalvelden: te smal om te raken op een telefoon */
  [class*="st-key-regelprijs_"] [data-testid="stNumberInputContainer"] button,
  .st-key-mandje_totaal [data-testid="stNumberInputContainer"] button,
  .st-key-cash_bedrag [data-testid="stNumberInputContainer"] button {
      display: none;}
  .st-key-mandje_totaal input, .st-key-cash_bedrag input {
      font-size: 2rem !important; font-weight: 700; text-align: right;
      padding: .6rem .75rem !important; font-family: var(--cijfers);
      font-variant-numeric: tabular-nums; letter-spacing: -.03em;}
  .st-key-mandje_totaal [data-testid="stNumberInputContainer"]::before,
  .st-key-cash_bedrag [data-testid="stNumberInputContainer"]::before {
      font-size: 1.6rem;}

  /* presets: klein grut onder het mandje, mag niet met VASTLEGGEN concurreren */
  [class*="st-key-preset_"] button, .st-key-onderhandeld button {
      min-height: 2.5rem; padding: 0 .3rem; font-size: .88rem; font-weight: 600;
      background: var(--elev); border-color: var(--line);
      color: var(--muted-elev); border-radius: var(--r-sm);}
  [class*="st-key-preset_"] button p, .st-key-onderhandeld button p {
      color: var(--muted-elev) !important;}
  [class*="st-key-preset_"] button:hover, .st-key-onderhandeld button:hover {
      color: var(--text);}
  .st-key-onderhandeld button {font-size: .82rem;}
  .st-key-onderhandeld button p {white-space: nowrap;}

  .st-key-prijs_modus div[data-testid="stSegmentedControl"] button,
  .st-key-prijs_modus div[data-testid="stButtonGroup"] button {
      min-height: 2.4rem; font-size: .92rem; font-weight: 600;}

  /* trade: de twee cash-richtingen lezen als knoppen, niet als een instelling */
  .st-key-cash_richting div[data-testid="stSegmentedControl"] button,
  .st-key-cash_richting div[data-testid="stButtonGroup"] button {
      min-height: 3rem; font-size: .95rem; font-weight: 700;}

  /* ---- VASTLEGGEN: de enige echt luide knop op het scherm -------------- */
  .st-key-vastleggen button {
      min-height: 4rem; font-size: 1.2rem; font-weight: 800; letter-spacing: .06em;
      border-radius: var(--r);}
  /* Streamlit zet de <p> in een knop zelf op 14px/400; zonder deze regel is de
     hoofdknop optisch even zwaar als de rest, en telt wit-op-rood bovendien als
     kleine tekst waar WCAG een hogere contrasteis aan stelt. */
  .st-key-vastleggen button p {font-size: 1.2rem !important; font-weight: 800;
      letter-spacing: .06em;}

  /* ---- bevestiging: licht kort op met een gouden gloed, vervaagt dan ----
     en klapt daarna dicht, zodat er geen gat achterblijft midden op het scherm */
  @keyframes tc-fade {
      0% {box-shadow: var(--glow-gold);}
      14%, 65% {opacity: 1; box-shadow: none;}
      99% {opacity: 0; padding: .9rem 1rem; max-height: 8rem;}
      100% {opacity: 0; visibility: hidden; max-height: 0; padding: 0;
            margin: 0; border-width: 0;}}
  .tc-ok {animation: tc-fade 6s ease-in forwards; background: var(--gold-soft);
      border: 1px solid rgba(242, 207, 99, .30);
      border-left: 4px solid var(--gold); border-radius: var(--r-sm);
      padding: .85rem 1rem; font-size: 1.1rem; font-weight: 700; line-height: 1.35;
      margin-bottom: .5rem; overflow: hidden; color: var(--text);}

  /* ---- voorraad -------------------------------------------------------- */
  .tc-voorraad, .tc-laatste, .tc-op {font-size: .85rem; font-weight: 600;
      margin: .1rem 0;}
  /* "op voorraad" rustig, de laatste helderder, uitverkocht rood. Bewust géén
     goud voor de laatste: dat is een voorraadstand, geen premium-kaart. */
  .tc-voorraad {color: var(--muted);}
  .tc-laatste  {color: var(--text);}
  .tc-op       {color: #FF5A66;}

  /* dubbel-waarschuwing: opvalt, maar blokkeert niet */
  .tc-dubbel {background: var(--elev); border-left: 4px solid var(--red-line);
      border-radius: var(--r-sm); padding: .55rem .7rem; font-size: .9rem;
      margin: .4rem 0; color: var(--text);}
  .tc-inbegrepen {color: var(--muted-elev);}

  /* terzijdes (geen beursdag, snelknop zonder kaart): mag je over het hoofd zien */
  .tc-note {font-size: .74rem; color: var(--muted); opacity: .85;
      text-align: center; margin: -.2rem 0 0;}

  /* vangnet: lichte rand, het is een uitwijk en geen hoofdroute */
  [data-testid="stExpander"] details {border-color: var(--line);
      background: var(--card); border-radius: var(--r-sm);}
  [data-testid="stExpander"] summary {font-size: .9rem; color: var(--muted);}

  /* laatste invoeren: compacte regels in plaats van een tabel */
  .tc-log {font-size: .85rem; color: var(--muted); line-height: 1.95;
      font-variant-numeric: tabular-nums;}
  .tc-log b {font-weight: 700; color: var(--text); font-family: var(--cijfers);}
  .st-key-undo button, .st-key-undo_ja button, .st-key-undo_nee button {
      min-height: 2.6rem; font-size: .9rem;}

  /* nog een: duidelijk aanwezig, maar niet in de weg van VASTLEGGEN */
  .st-key-nogmaals_knop button {min-height: 2.9rem; font-size: 1rem;
      font-weight: 600; margin-top: -.3rem; border-color: var(--red-line);}

  [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {
      color: var(--muted);}
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
RETURNING id, name, event_date, location
""")

# Zonder TC_EVENT_NAAM boeken we op het laatst aangemaakte event. Op event_date
# sorteren en pas daarna op id: twee beurzen op dezelfde dag komen dan alsnog in
# de volgorde binnen waarin ze zijn aangemaakt.
HUIDIG_EVENT = text("""
SELECT id, name, event_date, location FROM events
ORDER BY event_date DESC, id DESC LIMIT 1
""")

INSERT_TX = text("""
INSERT INTO transactions (
    event_id, item_id, datum, tijd, kanaal, type, bedrag, afzender, flag,
    ruwe_tekst, code, stuks, group_id, onderhandeld
) VALUES (
    :event_id, :item_id, :datum, :tijd, :kanaal, :type, :bedrag, :afzender,
    :flag, :ruwe_tekst, :code, :stuks, :group_id, :onderhandeld
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


def _datums(waarde: str | None) -> set[date]:
    """"2026-08-29, 2026-08-30" -> {date(...), date(...)}. Stil bij rommel:
    de beursdagen sturen alleen een grijs terzijde aan, geen invoer."""
    dagen = set()
    for stuk in (waarde or "").split(","):
        try:
            dagen.add(date.fromisoformat(stuk.strip()))
        except ValueError:
            pass
    return dagen


@st.cache_resource(show_spinner="Event ophalen…")
def event() -> dict:
    """Op welk event boeken we vandaag?

    1. Staat `TC_EVENT_NAAM` in de secrets/env, dan is dat het event. Het wordt
       aangemaakt als het nog niet bestaat, met `TC_EVENT_DATUM` (default vandaag)
       en `TC_EVENT_LOCATIE`. Zo kost een nieuwe beurs één secret, geen deploy.
    2. Anders: het meest recente event in de database. Wie een beurs voorbereidt
       door 'm alvast aan te maken, hoeft dus niets in te stellen.

    De beursdagen komen uit `TC_EVENT_DAGEN` (komma-gescheiden) en vallen anders
    terug op de datum van het event zelf.
    """
    naam = (secret("TC_EVENT_NAAM") or "").strip()
    with engine().begin() as conn:
        if naam:
            datum = _datums(secret("TC_EVENT_DATUM")) or {date.today()}
            rij = conn.execute(UPSERT_EVENT, {
                "name": naam, "event_date": min(datum),
                "location": (secret("TC_EVENT_LOCATIE") or "").strip() or None,
            }).one()
        else:
            rij = conn.execute(HUIDIG_EVENT).one_or_none()
            if rij is None:
                return {}
    dagen = _datums(secret("TC_EVENT_DAGEN")) or {rij.event_date}
    return {"id": rij.id, "naam": rij.name, "datum": rij.event_date,
            "locatie": rij.location, "dagen": dagen}


def event_id() -> int:
    return event()["id"]


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
                       code=None, stuks=1, group_id=None, onderhandeld=False) -> int:
    """Eén losse insert. Geen retry: een dubbel geboekte verkoop is erger dan
    een foutmelding waarna je zelf opnieuw tikt.

    `code` is de kaartcode ("173/195", "EVS 215"). Bij een vrij ingevoerde kaart
    is dat het enige haakje om 'm later alsnog aan de inventaris te koppelen.

    `bedrag` is altijd het TOTAAL van de regel en `stuks` hoeveel kaarten dat
    dekt — dus 3 pakjes van €15 wordt bedrag 45, stuks 3. `afboeken_sales.py`
    haalt er `coalesce(stuks, 1)` van de voorraad af. Dit vervangt de oude
    workaround waarbij het restant als regels van €0,01 werd geboekt.

    `group_id` bindt de regels van één afspraak aan elkaar. NULL bij een losse
    verkoop van één kaart — dat is nog steeds het normale geval.

    `onderhandeld` zegt dat de prijs afweek van de comp-prijs. Dat is geen fout en
    geen dubbeling, maar het verklaart later waarom de omzet lager uitvalt dan de
    inventariswaarde deed vermoeden."""
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
            "onderhandeld": bool(onderhandeld),
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
    """Bedrag in nl-NL-notatie: punt voor duizendtallen, komma voor centen.

    Altijd twee decimalen. Geld met wisselend aantal decimalen leest onrustig in
    een kolom, en de cijfers staan op het scherm in een tabulair font waar juist
    díe uitlijning het werk doet: 1.215,00 onder 12,00.

    Python kent alleen de Amerikaanse vorm, dus we wisselen de scheidingstekens
    om via een tussenteken — anders vervangt de tweede replace ook de eerste."""
    return f"{float(bedrag):,.2f}".replace(",", "~").replace(".", ",").replace("~", ".")


def toon_treffers(df: pd.DataFrame, term: str, prefix: str, container_key: str,
                  leeg_tekst: str):
    """Zoekresultaten als knoppen; geeft de aangeklikte rij terug (of None)."""
    treffers = zoek(df, term)
    with st.container(key=container_key):
        for _, r in treffers.head(MAX_RESULTATEN).iterrows():
            # "cm" erachter als het geen comp-prijs is: anders lijkt een
            # Cardmarket-prijs op een afgesproken verkoopprijs.
            prijs = (f"€ {geld(r['prijs'])}" + (" cm" if r["prijs_bron"] == "cm" else "")
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


def zoek_snelknoppen(df: pd.DataFrame) -> tuple[list, list]:
    """Elke snelknop naar precies één item, of niet.

    Geeft (gevonden, mislukt) terug: gevonden is een lijst van (config, item), en
    mislukt bevat de knoppen met nul of meerdere treffers plus dat aantal. We
    raden nooit — bij "tins" kan er zomaar een tweede tin bijkomen, en dan hoort
    de knop te verdwijnen tot iemand de lijst bijwerkt."""
    gevonden, mislukt = [], []
    for conf in SNELKNOPPEN:
        treffers = zoek(df, conf["zoek"])
        code = (conf.get("code") or "").strip().lower()
        if code and not treffers.empty:
            treffers = treffers[treffers.apply(
                lambda r: kenmerk(r).lower() == code, axis=1)]
        if len(treffers) == 1:
            gevonden.append((conf, als_dict(treffers.iloc[0])))
        else:
            mislukt.append((conf, len(treffers)))
    return gevonden, mislukt


def als_dict(r) -> dict:
    return {"id": int(r["id"]), "naam": r["naam"], "kenmerk": kenmerk(r),
            "conditie": conditie(r), "staat": r["staat"], "grade": r["grade"],
            "aantal": int(r["aantal"]),
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
    # prijs_modus en cash_richting krijgen hun beginwaarde via `default=` bij de
    # widget zelf, niet hier. Allebei verschijnen ze pas later op het scherm (bij
    # de tweede kaart, of in TRADE), en een waarde die dán al in session_state
    # staat komt niet bij de browser aan: het knoppenpaar staat leeg terwijl de
    # app denkt dat er iets gekozen is. Met `default=` klopt het wel. Headless is
    # dit alleen te zien aan `proto.default` — daar staat een test op.
    st.session_state.setdefault("dubbel_vraag", None)  # wacht op "toch vastleggen"
    # None = leiden uit de bedragen, True/False = met de hand overruled
    st.session_state.setdefault("onderhandeld_keuze", None)
    st.session_state.setdefault("nogmaals", None)     # wat er net is vastgelegd
    # Is dit mandje via "nog een" gevuld? Dan is de herhaling gewild en hoeft de
    # dubbel-check er niet naar te vragen.
    st.session_state.setdefault("bewust_herhaald", False)

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
        st.session_state["cash_bedrag"] = 0.0
        st.session_state["binnen_notitie"] = ""
        # Weghalen in plaats van terugzetten, zodat `default=` weer aan bod komt.
        # De richting hoort niet te blijven hangen: anders boekt de trade daarna
        # ongemerkt de verkeerde kant op.
        st.session_state["onderhandeld_keuze"] = None
        st.session_state["bewust_herhaald"] = False
        st.session_state.pop("prijs_modus", None)
        st.session_state.pop("cash_richting", None)
        st.session_state["dubbel_vraag"] = None


def vergeet_regel(rid: int):
    """Haalt de widget-state van één mandje-regel weg. Zonder dit blijft het
    aantal van een verwijderde kaart hangen en duikt het op bij een volgende
    regel die toevallig hetzelfde nummer krijgt."""
    for sleutel in (f"stuks_{rid}", f"regelprijs_{rid}"):
        st.session_state.pop(sleutel, None)


def voeg_toe(*, item_id, naam, kenmerk="", conditie="", staat=None, grade=None,
             voorraad=0, prijs=None, prijs_bron=None, code=None):
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
        "conditie": conditie, "staat": staat, "grade": grade,
        "voorraad": int(voorraad), "prijs": prijs,
        "prijs_bron": prijs_bron, "code": code,
        "bedrag": float(prijs) if prijs else 0.0,
    })
    st.session_state["bevestiging"] = None
    st.session_state["fout"] = None
    st.session_state["dubbel_vraag"] = None
    st.session_state["bewust_herhaald"] = False
    st.session_state["_leeg_zoek"] = True


def regel_stuks(rid: int) -> int:
    return max(1, int(st.session_state.get(f"stuks_{rid}", 1)))


def zet_prijzen(deel: float | None):
    """Alle regels op hun eigen comp-prijs, of op een deel daarvan.

    Werkt over het hele mandje in plaats van per regel: dat kost één rij op het
    scherm in plaats van één per kaart, en "alles op comp −10%" is precies wat er
    bij het afdingen gebeurt. Regels zonder bekende prijs laten we met rust — daar
    valt niets vanaf te halen.

    `deel=None` betekent handmatig: leegmaken, dan tik je het zelf in."""
    for regel in st.session_state["mandje"]:
        if deel is None:
            regel["bedrag"] = 0.0
        elif regel["prijs"]:
            # Hele euro's, half naar boven: op een beursvloer reken je met briefjes
            # en munten, niet met centen.
            regel["bedrag"] = float(math.floor(regel["prijs"] * deel + 0.5))
        else:
            continue
        # Ook de widget-state bijwerken. Zolang het veld op het scherm staat wint
        # zijn eigen state van de `value=` bij het opbouwen, dus een nieuw bedrag
        # alleen in het mandje-record zetten zou niets zichtbaar doen. Dit draait
        # als callback, dus vóór de widgets van de volgende run bestaan.
        st.session_state[f"regelprijs_{regel['rid']}"] = regel["bedrag"]
    st.session_state["dubbel_vraag"] = None


def wijkt_af() -> bool:
    """Staat er ergens een ander bedrag dan de comp-prijs? Dan is er onderhandeld.

    Afgeleid in plaats van gevraagd: dit is exact te bepalen uit wat er op het
    scherm staat, en een vlag die je moet onthouden aan te zetten is een vlag die
    vergeten wordt. De knop eronder kan het alsnog overrulen."""
    return any(regel["prijs"] and abs(float(regel["bedrag"]) - float(regel["prijs"])) > 0.005
               for regel in st.session_state["mandje"])


def is_onderhandeld() -> bool:
    keuze = st.session_state.get("onderhandeld_keuze")
    return wijkt_af() if keuze is None else bool(keuze)


def stuks_bij(rid: int, stap: int):
    """+/- knop. Minimaal 1: een verkoop van nul kaarten bestaat niet."""
    st.session_state[f"stuks_{rid}"] = max(1, regel_stuks(rid) + stap)
    st.session_state["dubbel_vraag"] = None


def haal_weg(rid: int):
    st.session_state.setdefault("_weg_rids", []).append(rid)
    st.session_state["dubbel_vraag"] = None


def cash_bedrag() -> float:
    """Het cash-verschil van een trade, mét teken: positief als wij geld krijgen,
    negatief als wij bijleggen. Zo is de som over een dag meteen wat er netto aan
    contant geld bij is gekomen."""
    bedrag = float(st.session_state.get("cash_bedrag") or 0)
    if st.session_state.get("cash_richting") == CASH_BIJLEGGEN:
        return -bedrag
    return bedrag


def mandje_totaal() -> float:
    """Wat er in totaal de database in gaat.

    Per kaart: de som van prijs × aantal per regel. Totaalprijs: het ene bedrag
    dat met de klant is afgesproken — dan wordt er niets vermenigvuldigd, want
    dat bedrag geldt al voor de hele stapel."""
    mandje = st.session_state["mandje"]
    if st.session_state.get("prijs_modus") == "Totaalprijs" and len(mandje) > 1:
        return float(st.session_state.get("mandje_totaal") or 0)
    return sum(float(r["bedrag"]) * regel_stuks(r["rid"]) for r in mandje)


def na_succes(naam: str, bedrag: float, tx_ids: list[int] | None = None,
              herhaal: list[dict] | None = None):
    st.session_state["bevestiging"] = f"✓ Vastgelegd — {naam} · € {geld(bedrag)}"
    # Wat er net wegging, om het met één tik nog eens te kunnen doen. Vijf keer
    # hetzelfde pakje aan vijf klanten is op een beurs eerder regel dan
    # uitzondering, en dan is opnieuw zoeken puur tikwerk.
    st.session_state["nogmaals"] = herhaal or None
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
# De hoofdactie is rood en verder is er niets roods op het scherm: daardoor is
# VASTLEGGEN het enige element dat om aandacht vraagt. De modus zie je aan de
# toggle bovenin, niet meer aan de kleur van de knop — een blauwe of groene
# hoofdknop zou de rode hiërarchie uit de huisstijl doorbreken.
#
# De actieve keuze in een toggle is gevuld en licht; welke modus je hebt zie je
# aan wélke knop oplicht, niet aan een kleur. Goud blijft daarmee gereserveerd
# voor waar het iets betekent: de grade van een slab en de flits na het
# vastleggen. Een derde accentkleur zou dat verwateren.

st.markdown(f"""<style>
{BASIS_CSS}
  /* :not(:disabled) — een uitgeschakelde knop moet dof blijven, anders lijkt
     'ie klaar voor gebruik terwijl er nog geen kaart in het mandje ligt. */
  .st-key-vastleggen button:not(:disabled) {{
      background: var(--red) !important; border-color: var(--red) !important;
      color: #fff !important; box-shadow: var(--glow-red);}}
  .st-key-vastleggen button:not(:disabled):hover {{
      background: #FF1F31 !important; border-color: #FF1F31 !important;}}

  /* actieve keuze in een toggle: gevuld, licht, met het modus-accent als lijn */
  .st-key-modus button[aria-checked="true"],
  .st-key-prijs_modus button[aria-checked="true"],
  .st-key-cash_richting button[aria-checked="true"] {{
      background: var(--elev) !important; color: var(--text) !important;
      border-color: var(--text) !important;}}

  /* De onderhandeld-vlag is een status, geen actie: een rode tint in plaats van
     de volle primary-kleur, zodat hij niet met VASTLEGGEN concurreert. */
  .st-key-onderhandeld button[data-testid="stBaseButton-primary"] {{
      background: rgba(242, 5, 25, .14) !important;
      border-color: var(--red-line) !important; color: #FF8A93 !important;}}
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
    EVENT = event()
except SQLAlchemyError as e:
    # Platte database: dezelfde melding als bij het laden van de kaarten. Dit is
    # het eerste dat de app doet, dus zonder dit onderscheid zou een dode
    # verbinding zich voordoen als een ontbrekend event.
    st.error("Geen verbinding met de database.", icon="🚫")
    with st.expander("Details"):
        st.code(str(e))
    st.stop()
if not EVENT:
    st.error("Geen event gevonden. Maak er een aan in de database, of zet "
             "`TC_EVENT_NAAM` in de secrets/omgeving.", icon="📍")
    st.stop()

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
    regel = (f'Vandaag: verkoop <b>€ {geld(verkoop)}</b> &nbsp;·&nbsp; '
             f'trades: <b>{n_trades}</b>')
    if n_trades:
        regel += f' (cash {cash_teken}€ {geld(abs(cash))})'
    # Inkoop kan de app niet meer boeken; wat er uit een oudere versie of uit de
    # WhatsApp-import staat, laten we wél zien — anders lijkt de dag onvolledig.
    inkoop = totalen.get("inkoop", {}).get("som", 0.0)
    if inkoop:
        regel += f' &nbsp;·&nbsp; inkoop <b>€ {geld(inkoop)}</b>'
    st.markdown(f'<div class="tc-dag">{regel}</div>', unsafe_allow_html=True)

# Op welk event boeken we? Klein en grijs, maar wél in beeld: op 29-08 liep alles
# ongemerkt naar het vorige event omdat je nergens kon zien waar het heen ging.
st.markdown(f'<div class="tc-note">boekt op: {EVENT["naam"]}</div>',
            unsafe_allow_html=True)

# ------------------------------------------------------------------ modus

if len(MODI) > 1:
    st.segmented_control("Wat leg je vast?", MODI, key="modus", width="stretch",
                         label_visibility="collapsed", required=True)
MODUS = st.session_state["modus"] or "VERKOOP"
TRADE = MODUS == "TRADE"
TX_TYPE = "trade" if TRADE else "verkoop"

if VANDAAG not in EVENT["dagen"]:
    # Klein en grijs: het is een terzijde, geen waarschuwing — de invoer telt gewoon.
    st.markdown(f'<div class="tc-note">{VANDAAG.strftime("%d-%m-%Y")} valt buiten de '
                f'beursdagen — invoer telt wél mee</div>', unsafe_allow_html=True)

# ------------------------------------------------------------------ meldingen

if st.session_state["bevestiging"]:
    st.markdown(f'<div class="tc-ok">{st.session_state["bevestiging"]}</div>',
                unsafe_allow_html=True)

# Nog een: dezelfde kaart tegen dezelfde prijs, zonder opnieuw te zoeken. Alleen
# zolang het mandje leeg is — met iets in het mandje zou de knop er een tweede
# stapel bovenop gooien en dat is niet wat "nog een" betekent.
_nogmaals = st.session_state.get("nogmaals")
if _nogmaals and not st.session_state["mandje"]:
    _label = (_nogmaals[0]["naam"] if len(_nogmaals) == 1
              else f"{len(_nogmaals)} kaarten")
    if st.button(f"↻ Nog een — {_label[:34]}", key="nogmaals_knop", width="stretch"):
        for regel in _nogmaals:
            voeg_toe(item_id=regel["id"], naam=regel["naam"],
                     kenmerk=regel["kenmerk"], conditie=regel["conditie"],
                     staat=regel.get("staat"), grade=regel.get("grade"),
                     voorraad=regel["voorraad"], prijs=regel["prijs"],
                     prijs_bron=regel["prijs_bron"], code=regel["code"])
            # De prijs van zojuist, niet de comp: dat is de gangbare prijs
            # geworden. Aantal begint weer op 1 — "nog een" is er één.
            st.session_state["mandje"][-1]["bedrag"] = regel["bedrag"]
        # Deze herhaling is gewild: de dubbel-check hoeft er niet naar te vragen.
        # Anders kost vijf keer hetzelfde pakje vier keer "ja, toch vastleggen",
        # en dan is de knop zijn nut kwijt.
        st.session_state["bewust_herhaald"] = True
        st.rerun()

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

# --- snelknoppen: de producten waar tientallen van liggen -------------------
# Boven de zoekbalk, want voor deze paar is zoeken de omweg. Drie per rij:
# Streamlit's kolommen wrappen niet, en op 390 px is dat de breedte waarop de
# tekst nog leesbaar blijft.
snel_gevonden, snel_mislukt = zoek_snelknoppen(items)
for begin in range(0, len(snel_gevonden), SNELKNOPPEN_PER_RIJ):
    rij = snel_gevonden[begin:begin + SNELKNOPPEN_PER_RIJ]
    kolommen = st.columns(SNELKNOPPEN_PER_RIJ, vertical_alignment="center")
    for kolom, (conf, item) in zip(kolommen, rij):
        # Prijs op de knop: dan weet je vóór het tikken of hij nog goed staat.
        prijs = f"€ {geld(item['prijs'])}" if item["prijs"] else "€ ?"
        with kolom:
            if st.button(f"{conf['knop']}\n:gray[{prijs}]", key=f"snel_{item['id']}",
                         width="stretch"):
                voeg_toe(item_id=item["id"], naam=item["naam"],
                         kenmerk=item["kenmerk"], conditie=item["conditie"],
                         staat=item["staat"], grade=item["grade"],
                         voorraad=item["aantal"], prijs=item["prijs"],
                         prijs_bron=item["prijs_bron"], code=item["kenmerk"])
                st.rerun()

if snel_mislukt:
    # Klein en grijs: het is een signaal voor wie de lijst beheert, geen
    # waarschuwing voor wie staat te verkopen.
    uitleg = ", ".join(f"{c['knop']} ({n} treffers)" for c, n in snel_mislukt)
    st.markdown(f'<div class="tc-note">Snelknop zonder eenduidige kaart: {uitleg}'
                f'</div>', unsafe_allow_html=True)

st.text_input("Zoek kaart", key="zoekterm", placeholder="zoek kaart",
              label_visibility="collapsed")
term = st.session_state.get("zoekterm", "")
if len(term.strip()) >= 2:
    gekozen = toon_treffers(items, term, "pick_", "resultaten",
                            "Niets gevonden — gebruik *vrij invoeren* hieronder.")
    if gekozen is not None:
        r = als_dict(gekozen)
        voeg_toe(item_id=r["id"], naam=r["naam"], kenmerk=r["kenmerk"],
                 conditie=r["conditie"], staat=r["staat"], grade=r["grade"],
                 voorraad=r["aantal"], prijs=r["prijs"],
                 prijs_bron=r["prijs_bron"], code=r["kenmerk"])
        st.rerun()

# Bij een trade hangt het geld aan de afspraak, niet aan de losse kaart; de
# prijskeuze hoort daar dus niet.
TOTAALPRIJS = (not TRADE and len(mandje) > 1
               and st.session_state.get("prijs_modus") == "Totaalprijs")

if len(mandje) > 1 or (TRADE and mandje):
    kop = "Onze kaarten eruit" if TRADE else "Mandje"
    st.markdown(f'<div class="tc-teller">{kop}: <b>{len(mandje)}</b> '
                f'{"kaart" if len(mandje) == 1 else "kaarten"}</div>',
                unsafe_allow_html=True)

for regel in mandje:
    rid = regel["rid"]
    with st.container(key=f"regel_{rid}"):
        # Naam links, weg-knop rechts. Daaronder de bedieningsregel: het aantal
        # als kleine stepper náást de prijs, zodat een mandje van twee kaarten
        # nog steeds boven de vouw past op een telefoon.
        kol_naam, kol_weg = st.columns([6, 1], vertical_alignment="center")
        # Set-code en staat gedempt; de grade van een slab in goud. Dat is het
        # enige plekje waar de huisstijl-goudkleur mag opduiken — een PSA 10 is
        # nu eenmaal iets anders dan een losse NM-kaart, en die ene accentkleur
        # zegt dat sneller dan tekst.
        detail = " · ".join(x for x in [regel["kenmerk"], regel.get("staat")] if x)
        kop_html = f'<div class="tc-regelnaam">{html.escape(regel["naam"])}'
        if detail:
            kop_html += f'<span> · {html.escape(detail)}</span>'
        if regel.get("grade"):
            kop_html += (f'<span class="tc-grade"> · '
                         f'{html.escape(str(regel["grade"]))}</span>')
        kol_naam.markdown(kop_html + "</div>", unsafe_allow_html=True)
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
            if TRADE:
                # Een geruilde kaart heeft geen verkoopprijs: wat ertegenover
                # stond is de hele afspraak, niet deze ene kaart.
                st.markdown('<div class="tc-inbegrepen">eruit</div>',
                            unsafe_allow_html=True)
            elif TOTAALPRIJS:
                # Bij een afgesproken totaalprijs zegt een prijs per regel niets
                # meer; hij wordt straks naar rato berekend.
                st.markdown('<div class="tc-inbegrepen">in totaal</div>',
                            unsafe_allow_html=True)
            else:
                regel["bedrag"] = st.number_input(
                    "Prijs", min_value=0.0, step=0.50, format="%.2f",
                    value=float(regel["bedrag"]), key=f"regelprijs_{rid}",
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
               "hieronder als hij er niet in staat."
               + (" Dit zijn de kaarten die **wij** weggeven." if TRADE else ""))

# --- prijs-presets en de onderhandeld-vlag ---------------------------------
# Eén rij voor het hele mandje in plaats van een rij per kaart: dat scheelt op een
# telefoon precies het verschil tussen VASTLEGGEN boven of onder de vouw. Bij één
# kaart is het letterlijk "comp / comp −10% / handmatig" uit de opdracht.
TE_COMPEN = not TRADE and not TOTAALPRIJS and any(r["prijs"] for r in mandje)

if TE_COMPEN:
    kol_comp, kol_min, kol_hand, kol_flag = st.columns(
        [0.85, 1, 1.25, 1.95], vertical_alignment="center")
    kol_comp.button("comp", key="preset_comp", width="stretch",
                    on_click=zet_prijzen, args=(1.0,),
                    help="alles terug op de comp-prijs")
    kol_min.button(f"−{KORTING}%", key="preset_korting", width="stretch",
                   on_click=zet_prijzen, args=(1 - KORTING / 100,),
                   help=f"alles op comp min {KORTING}%, afgerond op hele euro's")
    kol_hand.button("handmatig", key="preset_hand", width="stretch",
                    on_click=zet_prijzen, args=(None,),
                    help="bedragen leegmaken en zelf intikken")
    with kol_flag:
        # Staat standaard goed: hij is aan zodra een bedrag van de comp afwijkt.
        # De tik is er voor het geval dat níét klopt — een prijs die toevallig op
        # comp uitkomt maar wel bevochten is, of andersom.
        aan = is_onderhandeld()
        if st.button("✓ onderhandeld" if aan else "onderhandeld",
                     key="onderhandeld", width="stretch",
                     type="primary" if aan else "secondary",
                     help="wijkt de prijs af van comp? staat vanzelf goed, "
                          "maar is hier te overrulen"):
            st.session_state["onderhandeld_keuze"] = not aan
            st.rerun()

# --- prijs (verkoop) of cash + binnenkomst (trade) -------------------------
if TRADE:
    # Twee knoppen in plaats van een plus/min: op een beursvloer wil je in één
    # oogopslag zien welke kant het geld op gaat, niet een teken voor een getal.
    st.segmented_control("Cash", CASH_RICHTINGEN, key="cash_richting",
                         default=CASH_ONTVANGEN, width="stretch",
                         label_visibility="collapsed", required=True)
    st.number_input("Cash", min_value=0.0, step=5.0, format="%.2f",
                    key="cash_bedrag", label_visibility="collapsed")
    # Wat er terugkomt tellen we niet per kaart: dat kost op een beurs te veel
    # tijd en de foto in WhatsApp is het echte bewijs. Eén regel tekst plus de
    # tijdstempel is genoeg om ze later bij elkaar te zoeken.
    st.text_input("Binnenkomend", key="binnen_notitie",
                  placeholder="wat komt er binnen? bv: 5 kaarten, zie WA-foto",
                  label_visibility="collapsed")
elif len(mandje) > 1:
    # Pas vanaf twee kaarten een keuze: bij één kaart is de prijs van die kaart
    # al het totaal, en zou de knop alleen maar ruimte kosten.
    st.segmented_control("Prijs", PRIJS_MODI, key="prijs_modus",
                         default=PRIJS_MODI[0], width="stretch",
                         label_visibility="collapsed", required=True)
    if TOTAALPRIJS:
        st.number_input("Afgesproken totaalbedrag", min_value=0.0, step=0.50,
                        format="%.2f", key="mandje_totaal",
                        label_visibility="collapsed")

CASH = cash_bedrag() if TRADE else 0.0
TOTAAL = CASH if TRADE else mandje_totaal()
STUKS_TOTAAL = sum(regel_stuks(r["rid"]) for r in mandje)

if TRADE and mandje:
    teken = "−" if CASH < 0 else "+"
    st.markdown(f'<div class="tc-totaal">{STUKS_TOTAAL} kaarten eruit '
                f'&nbsp;·&nbsp; cash <b>{teken}€ {geld(abs(CASH))}</b></div>',
                unsafe_allow_html=True)
elif len(mandje) > 1 and TOTAAL > 0:
    st.markdown(f'<div class="tc-totaal">{STUKS_TOTAAL} kaarten &nbsp;·&nbsp; '
                f'totaal <b>€ {geld(TOTAAL)}</b></div>', unsafe_allow_html=True)

# --- dubbel-waarschuwing ---------------------------------------------------
# Alleen bij één verkochte kaart: daar zit de dubbele tik, en daar is een
# vergelijking op kaart + bedrag ook betrouwbaar. Een mandje of een trade typ je
# niet per ongeluk twee keer.
vraag = st.session_state.get("dubbel_vraag")
if vraag:
    st.markdown(f'<div class="tc-dubbel">Net al ingevoerd: {vraag["naam"]} '
                f'€ {geld(vraag["bedrag"])} om {vraag["tijd"]} '
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

if TRADE:
    knoplabel = f"VASTLEGGEN — TRADE ({len(mandje)} eruit)"
elif len(mandje) > 1:
    knoplabel = f"VASTLEGGEN — {len(mandje)} KAARTEN"
else:
    knoplabel = f"VASTLEGGEN — {MODUS}"
klik = st.button(knoplabel, type="primary", width="stretch",
                 disabled=not mandje or bool(vraag), key="vastleggen")

if (klik or bevestigd) and mandje:
    # Een trade mag op €0 sluiten — dat is een gelijke ruil. Een verkoop niet:
    # daar is een bedrag van nul altijd een vergeten invoer.
    if not TRADE and TOTAAL <= 0:
        meld_fout("Vul een bedrag in.", "geen database-actie uitgevoerd")
    else:
        eerste = mandje[0]
        al_gezien = (lijkt_dubbel(recent_voor_check, eerste["id"], TOTAAL,
                                  eerste["naam"])
                     if klik and not TRADE and len(mandje) == 1
                     and not st.session_state.get("bewust_herhaald") else None)
        if al_gezien:
            st.session_state["dubbel_vraag"] = {
                "naam": eerste["naam"], "bedrag": TOTAAL, **al_gezien}
            st.rerun()

        # Eén group_id zodra er meer dan één regel is, en bij een trade altijd:
        # daar hoort de cash-regel bij de kaarten. Bij een losse verkoop laten we
        # 'm leeg — dat is nog steeds het normale geval en een groep van één zegt
        # niets extra's.
        groep = uuid.uuid4().hex if TRADE or len(mandje) > 1 else None
        if TRADE:
            # De kaarten gaan op €0 de deur uit: het geld van een trade hangt aan
            # de afspraak, niet aan een losse kaart. Wel item_id + stuks, zodat
            # afboeken_sales.py ze gewoon van de voorraad haalt.
            bedragen = [0.0] * len(mandje)
        elif TOTAALPRIJS:
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
                    onderhandeld=(not TRADE and is_onderhandeld()),
                    flag=("vrij ingevoerd" if regel["id"] is None else
                          "mandje-totaal" if TOTAALPRIJS else
                          "mandje" if groep and not TRADE else None)))
            if TRADE:
                # Eén cash-regel per trade, ook bij €0: die regel is het anker van
                # de afspraak en draagt wat er terugkwam. Zonder zou een gelijke
                # ruil alleen als uitgaande kaarten in de database staan.
                notitie = (st.session_state.get("binnen_notitie") or "").strip()
                geschreven.append(schrijf_transactie(
                    item_id=None, bedrag=CASH, tx_type="trade",
                    ruwe_tekst=notitie or "trade — binnenkomst niet omschreven",
                    flag="trade_cash", stuks=1, group_id=groep))

            if TRADE:
                label = f"trade — {STUKS_TOTAAL} kaarten eruit"
            elif len(mandje) > 1:
                label = f"{len(mandje)} kaarten"
            elif regel_stuks(eerste["rid"]) > 1:
                label = f"{regel_stuks(eerste['rid'])}× {eerste['naam']}"
            else:
                label = eerste["naam"]
            # Alleen bij verkoop: een trade tweemaal doen betekent dezelfde
            # kaarten nóg eens weggeven, en de cash en de binnenkomst horen er
            # dan toch bij getypt te worden.
            herhaal = None if TRADE else [
                {"id": r["id"], "naam": r["naam"], "kenmerk": r["kenmerk"],
                 "conditie": r["conditie"], "staat": r.get("staat"),
                 "grade": r.get("grade"), "voorraad": r["voorraad"],
                 "prijs": r["prijs"], "prijs_bron": r["prijs_bron"],
                 "code": r["code"], "bedrag": float(r["bedrag"])}
                for r in mandje]
            na_succes(label, TOTAAL, geschreven, herhaal)
        except SQLAlchemyError as e:
            engine().dispose()
            # Halverwege vastgelopen: wat er al in staat blijft staan en is via
            # undo in één keer terug te draaien. Stil doorgaan zou een halve
            # afspraak in de database achterlaten zonder dat iemand het ziet.
            totaal_regels = len(mandje) + (1 if TRADE else 0)
            rest = totaal_regels - len(geschreven)
            meld_fout(
                f"NIET volledig vastgelegd: {len(geschreven)} van {totaal_regels} "
                f"regels staan erin, {rest} niet. Je mandje staat er nog.",
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
                   f"€ {geld(laatste['bedrag'])}?", icon="↩️")
        kol_ja, kol_nee = st.columns(2)
        if kol_ja.button("Ja, verwijderen", key="undo_ja", type="primary",
                         width="stretch"):
            try:
                weg = verwijder_transacties(laatste["ids"])
                mijn.pop()
                st.session_state["undo_vraag"] = False
                st.session_state["tx_versie"] += 1
                st.session_state["bevestiging"] = (
                    f"↩ Verwijderd: {laatste['naam']} — € {geld(laatste['bedrag'])}"
                    if weg else f"↩ {laatste['naam']} stond er al niet meer in.")
                # Wat je net hebt teruggedraaid wil je niet met één tik terug.
                st.session_state["nogmaals"] = None
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
    elif st.button(f"↩ Laatste invoer terugdraaien — € {geld(laatste['bedrag'])}"
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
        # Inkoop ging eruit; een trade waarbij wij bijleggen staat negatief in de
        # database. Beide horen met een min op het scherm, niet als "+€-50".
        if str(r["type"]) == "inkoop" or bedrag < 0:
            teken, bedrag = "−", abs(bedrag)
        else:
            teken = "+"
        # Ruilpijl vóór de tekst: in een lijst met verkopen wil je een trade
        # kunnen herkennen zonder de bedragen te hoeven lezen.
        merk = "⇄ " if str(r["type"]) == "trade" else ""
        regels.append(f'{str(r["tijd"])[:5]} &nbsp; <b>{teken}€ {geld(bedrag)}</b> '
                      f'&nbsp; {merk}{html.escape(str(r["ruwe_tekst"])[:38])}')
    st.markdown(f'<div class="tc-log">{"<br>".join(regels)}</div>',
                unsafe_allow_html=True)
