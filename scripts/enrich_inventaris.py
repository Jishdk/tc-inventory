#!/usr/bin/env python3
"""Verrijk de inventaris met Cardmarket-identiteit (cardmarket_id, officiële
naam, set) via de RapidAPI "Cardmarket API TCG" (tcggopro) — alleen searches,
geen prijsvelden gebruikt.

    CMAPI_KEY (RapidAPI-key) in ~/projects/tc/.env
    python3 inventory/scripts/enrich_inventaris.py

Matching: kaartnummer uit onze code (vóór de slash, voorloopnullen gestript)
moet gelijk zijn aan card_number/card_code_number, en het settotaal (na de
slash) aan episode.cards_printed_total of cards_total — zo worden
reverse/promo/alt-art met hetzelfde nummer uit andere sets niet verwisseld.
Zoekresultaten worden gecachet in data/_cmapi_rapid_cache.json; een
afgebroken run kan gratis hervat worden.
"""

import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

import openpyxl
from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parent.parent.parent
XLSX = ROOT / "data" / "TC_Inventaris_v5.xlsx"
OUT_CSV = ROOT / "data" / "inventaris_verrijkt.csv"
RAPPORT = ROOT / "data" / "match_rapport.txt"
CACHE_FILE = ROOT / "data" / "_cmapi_rapid_cache.json"
HOST = "cardmarket-api-tcg.p.rapidapi.com"

calls = 0
limit_hit = False


def load_key():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("CMAPI_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    sys.exit("CMAPI_KEY niet gevonden in ~/projects/tc/.env")


KEY = load_key()
cache = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}


def save_cache():
    CACHE_FILE.write_text(json.dumps(cache))


def search(query):
    """Eén search-call; gecachet. Retry met backoff op 429, stop bij aanhoudend falen."""
    global calls, limit_hit
    q = query.strip()
    if q in cache:
        return cache[q]
    if limit_hit:
        return None
    url = f"https://{HOST}/pokemon/cards?" + urllib.parse.urlencode({"search": q})
    req = urllib.request.Request(url, headers={
        "x-rapidapi-key": KEY,
        "x-rapidapi-host": HOST,
    })
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            calls += 1
            items = data.get("data") if isinstance(data, dict) else data
            cache[q] = items or []
            if calls % 25 == 0:
                save_cache()
            time.sleep(0.25)
            return cache[q]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(30 * (attempt + 1))
                continue
            if e.code == 429:
                limit_hit = True
                print(f"\n!! Limiet blijft na retries (HTTP 429) na {calls} calls — "
                      f"rest wordt 'niet_verwerkt'; later opnieuw draaien.")
                return None
            calls += 1
            cache[q] = []
            return []
        except Exception as e:
            if attempt < 3:
                time.sleep(5)
                continue
            print(f"  ! netwerkfout op {q!r}: {e}")
            return []


def norm_num(s):
    """'173' <- '173/195'; '085' -> '85'; 'TG13' blijft; '21a' -> '21A'."""
    s = str(s).upper().replace(" ", "").split("/")[0]
    return re.sub(r"^([A-Z-]*)0*(\d)", r"\1\2", s)


def split_code(our_code):
    """('173', '195') / ('TG13', None) / ('SWSH260', None)

    Het totaal is alleen bruikbaar als beide delen puur numeriek zijn;
    bij subset-codes (TG30, GG70, SV122, RC32) is onze noemer het
    subset-totaal en niet het settotaal van de API — dan beslist de naam.
    Voorloopnullen worden gestript ('094' -> '94')."""
    parts = str(our_code).upper().replace(" ", "").split("/")
    num = norm_num(parts[0])
    total = None
    if len(parts) > 1 and parts[0].isdigit() and parts[1].isdigit():
        total = parts[1].lstrip("0") or None
    return num, total


def item_matches(our_code, it):
    num, total = split_code(our_code)
    cand = set()
    if it.get("card_number") not in (None, ""):
        cand.add(norm_num(it["card_number"]))
    ccn = str(it.get("card_code_number") or "")
    if ccn:
        cand.add(norm_num(ccn.split()[-1]))
        cand.add(norm_num(ccn))  # 'SWSH 260' -> 'SWSH260'
    if num not in cand:
        return False
    if total is None:
        return True
    ep = it.get("episode") or {}
    totals = {str(ep.get("cards_printed_total") or ""), str(ep.get("cards_total") or "")}
    return total in totals


def read_rows():
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    rows = []
    for r in wb["Inventaris"].iter_rows(min_row=2, values_only=True):
        if not r[0]:
            continue
        rows.append({
            "naam": str(r[0]).strip(),
            "code": str(r[1]).strip() if r[1] else "",
            "prijs": r[3] if isinstance(r[3], (int, float)) else "",
            "taal": str(r[6]).strip().upper() if r[6] else "",
            "categorie": str(r[12]).strip() if len(r) > 12 and r[12] else "",
        })
    return rows


def fuzz_score(our_naam, it):
    return fuzz.token_set_ratio(our_naam.lower(), (it.get("name") or "").lower())


def decide(row, cands):
    """(status, winnaar): 1 kandidaat -> zeker mits naam enigszins past;
    meerdere -> zeker alleen bij duidelijke naam-winnaar."""
    if len(cands) == 1:
        return ("zeker", cands[0]) if fuzz_score(row["naam"], cands[0]) >= 40 \
            else ("twijfel", None)
    scored = sorted(((fuzz_score(row["naam"], it), it) for it in cands),
                    key=lambda t: -t[0])
    best, second = scored[0][0], scored[1][0]
    if best >= 75 and best - second >= 10:
        return "zeker", scored[0][1]
    return "twijfel", None


def kandidaten_str(items, row=None):
    ranked = items
    if row is not None:
        ranked = [it for _, it in sorted(((fuzz_score(row["naam"], it), it)
                                          for it in items), key=lambda t: -t[0])]
    return "; ".join(
        f"{it.get('cardmarket_id')}|{it.get('name')}|{it.get('card_code_number')}"
        for it in ranked[:5])


def main():
    rows = read_rows()
    met_code = sum(1 for r in rows if r["code"])
    jp = sum(1 for r in rows if r["taal"] == "JP")
    print(f"{len(rows)} rijen ({met_code} met code, {len(rows) - met_code} alleen naam, {jp} JP).")
    print(f"Geschat ~670 searches (max ~980); cache bevat al {len(cache)} queries.")

    results = []
    status_count = Counter()
    for i, row in enumerate(rows, 1):
        out = {"onze_naam": row["naam"], "onze_code": row["code"], "onze_taal": row["taal"],
               "onze_categorie": row["categorie"], "onze_prijs": row["prijs"],
               "match_status": "", "cardmarket_id": "", "officiele_naam": "",
               "set_code": "", "expansion": "", "kandidaten": ""}

        cands, pool, items = [], [], []
        if row["code"]:
            num, _ = split_code(row["code"])
            items = search(row["naam"])
            if items:
                pool = items
                cands = [it for it in items if item_matches(row["code"], it)]
            # fallbacks; JP niet — toch niet gedekt. De search blijkt ook op
            # (volledige) codes te werken: 'SWSH260' en '140/195' geven exacte hits.
            if items is not None and row["taal"] != "JP":
                for q in (f"{row['naam']} {num}", row["code"],
                          f"{row['naam']} {row['code']}"):
                    if cands or limit_hit:
                        break
                    items = search(q)
                    if items:
                        pool = pool or items
                        cands = [it for it in items if item_matches(row["code"], it)]
        else:
            items = search(row["naam"])
            pool = items or []

        if items is None and limit_hit:
            out["match_status"] = "niet_verwerkt"
        elif row["code"]:
            if cands:
                seen, uniq = set(), []
                for it in cands:
                    key = it.get("cardmarket_id") or it.get("id")
                    if key not in seen:
                        seen.add(key)
                        uniq.append(it)
                status, winner = decide(row, uniq)
                out["match_status"] = status
                if winner:
                    ep = winner.get("episode") or {}
                    out["cardmarket_id"] = winner.get("cardmarket_id", "")
                    out["officiele_naam"] = winner.get("name", "")
                    out["set_code"] = winner.get("card_code_number", "")
                    out["expansion"] = ep.get("name", "")
                else:
                    out["kandidaten"] = kandidaten_str(uniq, row)
            else:
                out["match_status"] = ("jp_niet_gedekt" if row["taal"] == "JP"
                                       else "geen_match")
        else:
            if pool:
                out["match_status"] = "twijfel"
                out["kandidaten"] = kandidaten_str(pool, row)
            else:
                out["match_status"] = ("jp_niet_gedekt" if row["taal"] == "JP"
                                       else "geen_match")

        status_count[out["match_status"]] += 1
        results.append(out)
        if i % 50 == 0:
            print(f"  ... {i}/{len(rows)} verwerkt ({calls} calls)")

    save_cache()

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)

    lines = ["Match-rapport inventaris -> Cardmarket (RapidAPI cardmarket-api-tcg)",
             "=" * 60,
             f"Totaal rijen: {len(results)}   API-calls deze run: {calls}"]
    for st, n in status_count.most_common():
        lines.append(f"  {st:<16} {n}")
    lines.append("")
    lines.append("TWIJFEL-gevallen (nalopen):")
    for r in results:
        if r["match_status"] == "twijfel":
            lines.append(f"  {r['onze_naam']} [{r['onze_code'] or '-'}] ({r['onze_taal']})"
                         f" -> {r['kandidaten'] or 'nummer+totaal matcht, naam past nergens'}")
    lines.append("")
    lines.append("GEEN MATCH / JP niet gedekt / niet verwerkt:")
    for r in results:
        if r["match_status"] in ("geen_match", "jp_niet_gedekt", "niet_verwerkt"):
            lines.append(f"  [{r['match_status']}] {r['onze_naam']} "
                         f"[{r['onze_code'] or '-'}] ({r['onze_taal']})")
    RAPPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\nKlaar: {status_count.most_common()}")
    print(f"CSV: {OUT_CSV}\nRapport: {RAPPORT}")


if __name__ == "__main__":
    main()
