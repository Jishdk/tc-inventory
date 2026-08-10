"""TC inventory POC — Streamlit dashboard.

Overzicht / Inventaris / Transacties. Alleen lezen; validatie tegen de
referentiewaarden verkoop €16.768 / inkoop €3.619.

    streamlit run app.py
"""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from db import get_engine  # noqa: E402

REF_VERKOOP = 16768.0
REF_INKOOP = 3619.0

st.set_page_config(page_title="TC Inventory", page_icon="🃏", layout="wide")
engine = get_engine()


@st.cache_data(ttl=60)
def q(sql: str) -> pd.DataFrame:
    return pd.read_sql(sql, engine)


items = q("SELECT * FROM items")
tx = q("SELECT * FROM transactions")

st.title("Trader Collective — Inventory POC")
tab_overzicht, tab_inv, tab_tx = st.tabs(["Overzicht", "Inventaris", "Transacties"])

# ---------------------------------------------------------------- Overzicht
with tab_overzicht:
    inv_waarde = float((items["aantal"] * items["prijs_cm"]).sum(skipna=True))
    verkoop_waarde = float((items["aantal"] * items["comp_prijs"]).sum(skipna=True))

    c1, c2, c3 = st.columns(3)
    c1.metric("Inventariswaarde (CM)", f"€{inv_waarde:,.0f}")
    c2.metric("Verkoopwaarde (comp)", f"€{verkoop_waarde:,.0f}")
    c3.metric("Aantal items", f"{len(items)}")

    st.subheader("Items per categorie")
    per_cat = (items.groupby("categorie", dropna=False)
               .agg(aantal_regels=("id", "count"),
                    som_aantal=("aantal", "sum"),
                    waarde_cm=("prijs_cm", lambda s: float((s).sum(skipna=True))))
               .reset_index())
    st.dataframe(per_cat, use_container_width=True, hide_index=True)

    st.subheader("Beurs-cashflow")
    in_som = float(tx.loc[tx["type"].isin(["verkoop", "trade"]), "bedrag"].sum(skipna=True))
    uit_som = float(tx.loc[tx["type"] == "inkoop", "bedrag"].sum(skipna=True))
    d1, d2, d3 = st.columns(3)
    d1.metric("Verkoop + trade in", f"€{in_som:,.0f}",
              help=f"referentie €{REF_VERKOOP:,.0f}")
    d2.metric("Inkoop uit", f"€{uit_som:,.0f}",
              help=f"referentie €{REF_INKOOP:,.0f}")
    d3.metric("Netto", f"€{in_som - uit_som:,.0f}")

    val = pd.DataFrame([
        {"post": "verkoop + trade", "database": in_som, "referentie": REF_VERKOOP,
         "verschil": REF_VERKOOP - in_som},
        {"post": "inkoop", "database": uit_som, "referentie": REF_INKOOP,
         "verschil": REF_INKOOP - uit_som},
    ])
    st.dataframe(val, use_container_width=True, hide_index=True)
    st.caption("Verschil bij verkoop = sticker-verkopen zonder leesbaar bedrag "
               "(CHECK BEDRAG), nog niet meegeteld. Inkoop hoort exact te kloppen.")

# ---------------------------------------------------------------- Inventaris
with tab_inv:
    st.subheader("Inventaris")
    f1, f2, f3, f4 = st.columns(4)
    cats = f1.multiselect("Categorie", sorted(items["categorie"].dropna().unique()))
    talen = f2.multiselect("Taal", sorted(items["taal"].dropna().unique()))
    sets = f3.multiselect("Set-code", sorted(items["set_code"].dropna().unique()))
    statuses = f4.multiselect("Match-status", sorted(items["match_status"].dropna().unique()))

    prijzen = items["prijs_cm"].dropna()
    lo, hi = (float(prijzen.min()), float(prijzen.max())) if not prijzen.empty else (0.0, 0.0)
    prijs_range = st.slider("Prijs cm (€)", lo, hi, (lo, hi)) if hi > lo else (lo, hi)

    view = items.copy()
    if cats:
        view = view[view["categorie"].isin(cats)]
    if talen:
        view = view[view["taal"].isin(talen)]
    if sets:
        view = view[view["set_code"].isin(sets)]
    if statuses:
        view = view[view["match_status"].isin(statuses)]
    view = view[(view["prijs_cm"].fillna(0) >= prijs_range[0])
                & (view["prijs_cm"].fillna(0) <= prijs_range[1])]

    st.caption(f"{len(view)} van {len(items)} items")
    st.dataframe(
        view[["onze_naam", "officiele_naam", "code", "set_code", "expansion",
              "taal", "categorie", "match_status", "aantal", "prijs_cm", "comp_prijs"]],
        use_container_width=True, hide_index=True)

# ---------------------------------------------------------------- Transacties
with tab_tx:
    st.subheader("Transacties")
    g1, g2, g3 = st.columns(3)
    kanalen = g1.multiselect("Kanaal", sorted(tx["kanaal"].dropna().unique()))
    typen = g2.multiselect("Type", sorted(tx["type"].dropna().unique()))
    datums = g3.multiselect("Datum", sorted(tx["datum"].astype(str).unique()))

    tview = tx.copy()
    if kanalen:
        tview = tview[tview["kanaal"].isin(kanalen)]
    if typen:
        tview = tview[tview["type"].isin(typen)]
    if datums:
        tview = tview[tview["datum"].astype(str).isin(datums)]

    st.caption(f"{len(tview)} van {len(tx)} transacties")
    st.dataframe(
        tview[["datum", "tijd", "kanaal", "type", "bedrag", "afzender", "ruwe_tekst"]],
        use_container_width=True, hide_index=True)

    st.subheader("Som per kanaal")
    per_kanaal = (tview.groupby("kanaal")
                  .agg(aantal=("id", "count"),
                       som_bedrag=("bedrag", lambda s: float(s.sum(skipna=True))))
                  .reset_index())
    st.dataframe(per_kanaal, use_container_width=True, hide_index=True)
