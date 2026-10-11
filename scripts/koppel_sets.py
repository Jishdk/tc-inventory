#!/usr/bin/env python3
"""Sessie 2 — sets koppelen aan de inventaris.

    python scripts/koppel_sets.py            # matchen, CSV's schrijven, niets in de DB
    python scripts/koppel_sets.py --toepassen   # daarna: set-kolommen in `items` vullen

Drie bronnen, in deze volgorde:
1. De bestaande RapidAPI-verrijking (`set_code` = "MEG 161", `cardmarket_id`):
   afkorting uit set_code, naam en jaar uit de gecachete API-respons of uit de
   setlijst `data/_sets_tcggo.json` (178 EN sets, endpoint /pokemon/episodes).
2. Voor EN singles en slabs zónder verrijking: de RapidAPI-search op naam
   (gecachet in `data/_cmapi_rapid_cache.json`), gematcht op kaartnummer én
   settotaal — nooit alleen op nummer, want reverse/promo delen nummers.
   Eén kandidaat met passende naam = zeker; meerdere = alleen zeker bij een
   duidelijke naamwinnaar, anders twijfel met de kandidaten erbij.
3. Sealed en JP-codes: uit de code of productnaam afgeleid (CODE_SETS,
   NAAM_SETS) — status `afgeleid`. Geen bron = leeg (`geen_bron`).

Schrijft `data/set_koppeling.csv` (alles) en `data/set_twijfel.csv` (alleen
twijfel, met kandidaten). Met `--toepassen` gaan zeker + afgeleid de database
in (migratie 012 moet dan zijn toegepast); twijfel blijft leeg.
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from enrich_inventaris import (save_cache, cache, item_matches, HOST, KEY,  # noqa: E402
                               split_code, norm_num, fuzz_score)
import json as _json  # noqa: E402
import time  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402
from db import get_engine  # noqa: E402
from sqlalchemy import text  # noqa: E402

DATA = Path(__file__).resolve().parent.parent.parent / "data"
SETS = json.loads((DATA / "_sets_tcggo.json").read_text())
SET_PER_CODE: dict[str, list] = {}
for s in SETS:
    if s.get("code"):
        SET_PER_CODE.setdefault(s["code"].upper(), []).append(s)
SET_PER_NAAM = {s["name"].lower(): s for s in SETS if s.get("name")}

# Teamcodes en JP-setcodes die niet in de EN-setlijst staan. Jaar alleen waar
# zeker; anders None.
CODE_SETS = {
    "sv11b": ("sv11b", "Black Bolt (JP)", 2025),
    "sv11w": ("sv11w", "White Flare (JP)", 2025),
    "s12a": ("s12a", "VSTAR Universe (JP)", 2022),
    "sva8a": ("sv8a", "Terastal Festival ex (JP)", 2024),
    "sv8a": ("sv8a", "Terastal Festival ex (JP)", 2024),
    "m2": ("m2", "Inferno X (JP)", 2025),
    "m2a": ("m2a", "Mega Dream ex (JP)", 2025),
    "m6": ("m6", "Storm Emerald (JP)", None),
    "me01": ("MEG", "Mega Evolution", 2025),
    "me02": ("PFL", "Phantasmal Flames", 2025),
}
# Productnaam (kleine letters, deel) -> setnaam in de EN-setlijst
NAAM_SETS = [
    ("prismatic", "Prismatic Evolutions"), ("151", "151"),
    ("phantasamal flames", "Phantasmal Flames"), ("phantasmal flames", "Phantasmal Flames"),
    ("twillight masquerade", "Twilight Masquerade"), ("twilight masquerade", "Twilight Masquerade"),
    ("fusion strike", "Fusion Strike"), ("cosmic eclipse", "Cosmic Eclipse"),
    ("evolving skies", "Evolving Skies"), ("mega evolution", "Mega Evolution"),
    ("ascended heroes", "Ascended Heroes"), ("delta reign", "Delta Reign"),
    ("destined rivals", "Destined Rivals"), ("obsidian flames", "Obsidian Flames"),
    ("surging sparks", "Surging Sparks"), ("black bolt", "Black Bolt"),
    ("white flare", "White Flare"), ("journey together", "Journey Together"),
    ("celebrations", "Celebrations"),
]
# Woorden in onze naam die niet in de API-naam staan en de search vervuilen.
RUIS = {"holo", "cel", "fp", "oc", "1th", "1st", "edition", "metal", "sealed",
        "goldstar", "gold", "star", "lvx", "lv", "x", "comics", "promo", "30th",
        "mega", "m", "v", "vmax", "vstar", "gx", "ex", "lv.x"}


def zoekterm(naam: str) -> str:
    n = re.sub(r"\(.*?\)", " ", naam)
    n = n.replace("-holo", " ").replace("&", " & ")
    woorden = [w for w in re.split(r"[\s/]+", n) if w]
    kern = [w for w in woorden if w.lower().strip("'s") not in RUIS and w.lower() not in RUIS]
    return " ".join(kern) or naam


def set_info_uit_code(set_code: str | None, cardmarket_id):
    """(afk, naam, jaar) voor een item dat al verrijkt is."""
    if not set_code:
        return None, None, None
    afk = set_code.split()[0].upper()
    if cardmarket_id:
        for items in cache.values():
            for it in items or []:
                if it.get("cardmarket_id") == cardmarket_id and it.get("episode"):
                    ep = it["episode"]
                    return (ep.get("code") or afk).upper(), ep.get("name"), _jaar(ep)
    kand = SET_PER_CODE.get(afk) or []
    if len(kand) == 1:
        return afk, kand[0]["name"], _jaar(kand[0])
    return afk, (kand[0]["name"] if kand else None), (_jaar(kand[0]) if kand else None)


def _jaar(ep):
    d = (ep or {}).get("released_at") or ""
    return int(d[:4]) if d[:4].isdigit() else None


def zoek_api(naam: str, nummer: str | None, max_paginas: int = 5) -> list:
    """RapidAPI-search op naam, met `card_number` als filter (dat kent de API,
    en het scheelt de 20-per-pagina-limiet bij Charizard/Pikachu). Gecachet
    onder "naam#nummer"; pagina's worden doorgelopen tot alles binnen is."""
    sleutel = f"{naam}#{nummer or ''}"
    if sleutel in cache:
        return cache[sleutel]
    alles, pagina = [], 1
    while pagina <= max_paginas:
        q = {"search": naam, "page": pagina}
        if nummer:
            q["card_number"] = nummer
        req = urllib.request.Request(f"https://{HOST}/pokemon/cards?" + urllib.parse.urlencode(q),
                                     headers={"x-rapidapi-key": KEY, "x-rapidapi-host": HOST})
        for poging in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    d = _json.loads(resp.read().decode())
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and poging < 3:
                    time.sleep(30 * (poging + 1)); continue
                print(f"  ! {sleutel}: HTTP {e.code}"); d = {"data": []}; break
            except Exception as e:
                if poging < 3:
                    time.sleep(5); continue
                print(f"  ! {sleutel}: {e}"); d = {"data": []}; break
        data = d.get("data") if isinstance(d, dict) else d
        alles += data or []
        totaal = ((d.get("paging") or {}).get("total") or 1) if isinstance(d, dict) else 1
        time.sleep(0.25)
        if pagina >= totaal:
            break
        pagina += 1
    cache[sleutel] = alles
    if len(cache) % 25 == 0:
        save_cache()
    return alles


def match_api(naam: str, code: str | None):
    """(status, kaart, kandidaten) via de RapidAPI-search op naam + nummer."""
    term = zoekterm(naam)
    if not code:
        items = zoek_api(term, None)
        if len(items) == 1 and fuzz_score(naam, items[0]) >= 60:
            return "zeker", items[0], items
        return ("twijfel" if items else "geen_match"), None, items[:6]
    nummer, _ = split_code(code)
    items = zoek_api(term, nummer)
    if not items and term != naam:
        items = zoek_api(naam, nummer)
    if not items:
        # alfanumerieke codes (SWSH229, TG13, dp39): zonder nummerfilter zoeken
        items = [it for it in zoek_api(term, None) if item_matches(code, it)]
    kand = [it for it in items if item_matches(code, it)]
    if not kand:
        return "geen_match", None, items[:5]
    # naamhint: "CEL" in onze naam -> Celebrations-kandidaat wint
    hint = [it for it in kand if "cel" in naam.lower().split() and
            "celebrations" in (it.get("episode") or {}).get("name", "").lower()]
    if len(hint) == 1:
        return "zeker", hint[0], kand
    if len(kand) == 1:
        return ("zeker" if fuzz_score(naam, kand[0]) >= 40 else "twijfel"), kand[0], kand
    scored = sorted(((fuzz_score(naam, it), it) for it in kand), key=lambda t: -t[0])
    if scored[0][0] >= 75 and scored[0][0] - scored[1][0] >= 10:
        return "zeker", scored[0][1], kand
    # alle kandidaten in dezelfde set (bv. normale + reverse) -> set is zeker
    sets = {(it.get("episode") or {}).get("code") for it in kand}
    if len(sets) == 1:
        return "zeker", scored[0][1], kand
    return "twijfel", None, kand


def afgeleid(naam: str, code: str | None):
    c = (code or "").strip().lower()
    if c in CODE_SETS:
        return CODE_SETS[c]
    n = naam.lower()
    for sleutel, setnaam in NAAM_SETS:
        if sleutel in n:
            s = SET_PER_NAAM.get(setnaam.lower())
            if s:
                return s["code"], s["name"], _jaar(s)
            return None, setnaam, None
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--toepassen", action="store_true")
    args = p.parse_args()
    eng = get_engine()
    with eng.connect() as c:
        items = c.execute(text("""SELECT id, bron_rij, onze_naam, officiele_naam, code, set_code,
            cardmarket_id, taal, categorie, grade FROM items ORDER BY bron_rij""")).mappings().all()
    uit, twijfel = [], []
    for it in items:
        naam, code = it["onze_naam"].strip(), (it["code"] or "").strip() or None
        rec = {"bron_rij": it["bron_rij"], "naam": naam, "code": code, "taal": it["taal"],
               "categorie": it["categorie"], "status": "", "set_afk": None, "set_naam": None,
               "set_jaar": None, "officiele_naam": it["officiele_naam"], "cardmarket_id": it["cardmarket_id"],
               "set_code": it["set_code"], "kandidaten": ""}
        if it["set_code"]:
            rec["set_afk"], rec["set_naam"], rec["set_jaar"] = set_info_uit_code(it["set_code"], it["cardmarket_id"])
            rec["status"] = "zeker" if rec["set_naam"] else "zeker_zonder_naam"
        else:
            afg = afgeleid(naam, code)
            if afg:
                rec["set_afk"], rec["set_naam"], rec["set_jaar"] = afg
                rec["status"] = "afgeleid"
            elif it["taal"] == "EN" and it["categorie"] in ("single", "Slab"):
                status, kaart, kand = match_api(naam, code)
                rec["status"] = status
                if status == "geen_match" and kand:
                    rec["status"] = "geen_match_wel_naam"
                rec["kandidaten"] = "; ".join(
                    f"{k.get('name')} {k.get('card_code_number')} [{(k.get('episode') or {}).get('name')}]"
                    for k in kand[:6])
                if status == "zeker" and kaart:
                    ep = kaart.get("episode") or {}
                    rec.update(set_afk=(ep.get("code") or "").upper() or None, set_naam=ep.get("name"),
                               set_jaar=_jaar(ep), officiele_naam=kaart.get("name"),
                               cardmarket_id=kaart.get("cardmarket_id"),
                               set_code=f"{ep.get('code')} {kaart.get('card_number')}" if ep.get("code") else None)
                elif status == "twijfel" or (status == "geen_match" and kand):
                    twijfel.append(rec)
            else:
                rec["status"] = "geen_bron"
        uit.append(rec)
    save_cache()
    velden = list(uit[0].keys())
    with (DATA / "set_koppeling.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, velden); w.writeheader(); w.writerows(uit)
    with (DATA / "set_twijfel.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, velden); w.writeheader(); w.writerows(twijfel)
    from collections import Counter
    print("status:", Counter(r["status"] for r in uit))
    per = Counter((r["categorie"], r["taal"], bool(r["set_naam"])) for r in uit)
    for (cat, taal), _ in sorted(Counter((r["categorie"], r["taal"]) for r in uit).items()):
        n = per[(cat, taal, True)] + per[(cat, taal, False)]
        print(f"  {cat:7} {taal:3} {per[(cat, taal, True)]:>4}/{n:<4} met set")
    print(f"met set: {sum(1 for r in uit if r['set_naam'])}/{len(uit)}; twijfel: {len(twijfel)}; "
          f"CSV: {DATA / 'set_koppeling.csv'}, {DATA / 'set_twijfel.csv'}")

    if args.toepassen:
        with eng.begin() as c:
            n = 0
            for r in uit:
                if r["status"] in ("zeker", "zeker_zonder_naam", "afgeleid"):
                    c.execute(text("""UPDATE items SET set_afk=:afk, set_naam=:naam, set_jaar=:jaar,
                        set_status=:st, officiele_naam=COALESCE(officiele_naam, :off),
                        cardmarket_id=COALESCE(cardmarket_id, :cm), set_code=COALESCE(set_code, :sc)
                        WHERE bron_rij=:rij"""),
                              {"afk": r["set_afk"], "naam": r["set_naam"], "jaar": r["set_jaar"],
                               "st": r["status"], "off": r["officiele_naam"], "cm": r["cardmarket_id"],
                               "sc": r["set_code"], "rij": r["bron_rij"]})
                    n += 1
                else:
                    c.execute(text("UPDATE items SET set_status=:st WHERE bron_rij=:rij"),
                              {"st": r["status"], "rij": r["bron_rij"]})
            print(f"toegepast op {n} items")


if __name__ == "__main__":
    main()
