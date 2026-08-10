#!/usr/bin/env python3
"""Test of CMAPI (RapidAPI "Cardmarket API TCG") bruikbare NL-low prijzen geeft.

Vergelijkt 20 kaarten uit TC_Inventaris_v5.xlsx (kolom "Prijs cm/ebay") met
lowest_near_mint + per-land velden uit de API. Max ~40 requests (gratis tier).

    CMAPI_KEY in ~/projects/tc/.env
    python3 inventory/scripts/cmapi_test.py

Responsestructuur (geverifieerd 28-07-2026): GET /pokemon/cards?search=...
geeft {"data": [...]} met per kaart prices.cardmarket.{lowest_near_mint,
lowest_near_mint_DE/FR/ES/IT, *_EU_only, 30d_average}; card_number is het
nummer zonder settotaal, het settotaal zit in episode.cards_printed_total.
"""

import csv
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent.parent
XLSX = ROOT / "data" / "TC_Inventaris_v5.xlsx"
OUT_CSV = ROOT / "data" / "cmapi_test.csv"
HOST = "cardmarket-api-tcg.p.rapidapi.com"
MAX_CALLS = 40

calls = 0


def load_key():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("CMAPI_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("CMAPI_KEY niet gevonden in ~/projects/tc/.env")


KEY = load_key()


def api_get(path, params=None):
    global calls
    if calls >= MAX_CALLS:
        raise RuntimeError(f"Request-budget ({MAX_CALLS}) bereikt")
    calls += 1
    url = f"https://{HOST}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "x-rapidapi-key": KEY,
        "x-rapidapi-host": HOST,
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    time.sleep(0.6)  # netjes binnen rate limit blijven
    return data


def select_cards():
    """20 kaarten met prijs+code: spreiding over prijsbanden en EN/JP."""
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    rows = []
    for r in wb["Inventaris"].iter_rows(min_row=2, values_only=True):
        naam, code, _, prijs, *_rest = (list(r[:7]) + [None] * 7)[:7]
        taal = (r[6] or "?") if len(r) >= 7 else "?"
        if naam and code and isinstance(prijs, (int, float)) and prijs > 0:
            rows.append({"naam": str(naam).strip(), "code": str(code).strip(),
                         "prijs": float(prijs), "taal": str(taal).strip().upper()})

    def band(r):
        return "cheap" if r["prijs"] < 10 else ("mid" if r["prijs"] <= 100 else "exp")

    def pick(pool, n):
        pool = sorted(pool, key=lambda r: r["prijs"])
        if len(pool) <= n:
            return pool
        step = len(pool) / n
        return [pool[int(i * step)] for i in range(n)]

    quota = [("cheap", "EN", 4), ("cheap", "JP", 2),
             ("mid", "EN", 5), ("mid", "JP", 2),
             ("exp", "EN", 5), ("exp", "JP", 2)]
    chosen, seen = [], set()
    for b, taal, n in quota:
        pool = [r for r in rows if band(r) == b and r["taal"] == taal
                and (r["naam"], r["code"]) not in seen]
        got = pick(pool, n)
        chosen.extend(got)
        seen.update((r["naam"], r["code"]) for r in got)
    if len(chosen) < 20:
        rest = [r for r in rows if (r["naam"], r["code"]) not in seen]
        chosen.extend(pick(rest, 20 - len(chosen)))
    return chosen[:20]


def strip0(s):
    return re.sub(r"^([A-Z-]*)0*(\d)", r"\1\2", s)


def norm_code(code):
    """'085/198' -> ('85','198'); 'TG13/TG30' -> ('TG13','TG30'); '085' -> ('85',None)"""
    code = str(code).upper().replace(" ", "")
    parts = code.split("/")
    a = strip0(parts[0])
    b = strip0(parts[1]) if len(parts) > 1 else None
    return a, b


def item_matches(our_code, it):
    """card_number moet kloppen; met settotaal in onze code ook het episodetotaal."""
    oa, ob = norm_code(our_code)
    cand = set()
    if it.get("card_number") not in (None, ""):
        cand.add(strip0(str(it["card_number"]).upper().replace(" ", "")))
    ccn = str(it.get("card_code_number") or "")
    if ccn:
        cand.add(strip0(ccn.split()[-1].upper()))
    if oa not in cand:
        return False
    if ob is None:
        return True
    ep = it.get("episode") or {}
    totals = {re.sub(r"\D", "", str(ep.get(k) or ""))
              for k in ("cards_printed_total", "cards_total")}
    return re.sub(r"\D", "", ob) in totals


def cm_prices(it):
    return ((it.get("prices") or {}).get("cardmarket")) or {}


def main():
    cards = select_cards()
    print("Geselecteerde 20 kaarten:")
    for c in cards:
        print(f"  {c['naam']:<28} {c['code']:<12} €{c['prijs']:>8.2f}  {c['taal']}")

    nl_field_seen = False
    country_suffixes = set()
    results = []

    for c in cards:
        row = {"onze_naam": c["naam"], "onze_code": c["code"], "onze_prijs": c["prijs"],
               "api_naam": "", "api_lowest_nm": "", "api_nl": "", "api_de": "",
               "api_fr": "", "api_es": "", "api_it": "", "api_30d_avg": "",
               "verschil_abs": "", "verschil_pct": "", "match_status": ""}
        match = None
        try:
            oa, _ = norm_code(c["code"])
            for query in (c["naam"], f"{c['naam']} {oa}"):
                data = api_get("/pokemon/cards", {"search": query})
                items = data.get("data") if isinstance(data, dict) else data
                for it in items or []:
                    if item_matches(c["code"], it):
                        match = it
                        break
                if match:
                    break
        except Exception as e:
            row["match_status"] = f"API FOUT: {e}"
            results.append(row)
            print(f"  ! {c['naam']}: {e}")
            continue

        if match is None:
            row["match_status"] = "GEEN MATCH"
            results.append(row)
            continue

        p = cm_prices(match)
        for k in p:
            m = re.match(r"lowest_near_mint_([A-Z]{2})$", k)
            if m:
                country_suffixes.add(m.group(1))
                if m.group(1) == "NL":
                    nl_field_seen = True

        row["api_naam"] = match.get("name") or ""
        g = lambda k: p.get(k) if p.get(k) not in (None, "") else ""
        row["api_lowest_nm"] = g("lowest_near_mint")
        row["api_nl"] = g("lowest_near_mint_NL")
        row["api_de"] = g("lowest_near_mint_DE")
        row["api_fr"] = g("lowest_near_mint_FR")
        row["api_es"] = g("lowest_near_mint_ES")
        row["api_it"] = g("lowest_near_mint_IT")
        row["api_30d_avg"] = g("30d_average")
        row["match_status"] = "MATCH"

        if row["api_lowest_nm"] != "":
            api_p = float(row["api_lowest_nm"])
            row["verschil_abs"] = round(api_p - c["prijs"], 2)
            row["verschil_pct"] = round((api_p - c["prijs"]) / c["prijs"] * 100, 1)
        results.append(row)

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)

    print(f"\n{'onze_naam':<24}{'code':<12}{'onze':>8}{'api_nm':>9}{'de':>8}{'fr':>8}"
          f"{'es':>8}{'it':>8}{'30d':>9}{'pct':>8}  status")
    for r in results:
        fmt = lambda v: f"{float(v):.2f}" if v not in ("", None) else "-"
        print(f"{r['onze_naam'][:23]:<24}{r['onze_code']:<12}{r['onze_prijs']:>8.2f}"
              f"{fmt(r['api_lowest_nm']):>9}{fmt(r['api_de']):>8}{fmt(r['api_fr']):>8}"
              f"{fmt(r['api_es']):>8}{fmt(r['api_it']):>8}{fmt(r['api_30d_avg']):>9}"
              f"{(str(r['verschil_pct']) + '%') if r['verschil_pct'] != '' else '-':>8}"
              f"  {r['match_status']}")

    matched = [r for r in results if r["match_status"] == "MATCH"]
    with_price = [r for r in matched if r["api_lowest_nm"] != ""]
    print("\n=== SAMENVATTING ===")
    print(f"Matches op code: {len(matched)}/20  (geen match: "
          f"{sum(1 for r in results if r['match_status'] == 'GEEN MATCH')}, "
          f"fouten: {sum(1 for r in results if r['match_status'].startswith('API FOUT'))})")
    print(f"NL-veld (lowest_near_mint_NL) in respons: {'JA' if nl_field_seen else 'NEE'}")
    print(f"Per-land velden gezien: {sorted(country_suffixes) or 'geen'}")

    if with_price:
        pcts = [abs(float(r["verschil_pct"])) for r in with_price]
        print(f"Gem. |verschil| onze_prijs vs lowest_near_mint: "
              f"{sum(pcts)/len(pcts):.1f}%  (n={len(pcts)})")
        closest = []
        for r in with_price:
            candidates = [float(r[k]) for k in
                          ("api_lowest_nm", "api_nl", "api_de", "api_fr", "api_es", "api_it")
                          if r[k] != ""]
            best = min(candidates, key=lambda p_: abs(p_ - r["onze_prijs"]))
            closest.append(abs(best - r["onze_prijs"]) / r["onze_prijs"] * 100)
        print(f"Gem. |verschil| vs dichtstbijzijnde per-land veld: "
              f"{sum(closest)/len(closest):.1f}%")
        off = [r for r in with_price if abs(float(r["verschil_pct"])) > 20]
        print(f"\nMeer dan 20% afwijking ({len(off)}):")
        for r in off:
            print(f"  {r['onze_naam']:<28} {r['onze_code']:<12} onze €{r['onze_prijs']:.2f} "
                  f"vs api €{float(r['api_lowest_nm']):.2f} ({r['verschil_pct']}%)")

    print(f"\nAPI-calls gebruikt: {calls}")
    print(f"CSV: {OUT_CSV}")


if __name__ == "__main__":
    main()
