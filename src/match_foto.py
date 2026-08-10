"""Sessie A: maak per verkoop/trade-transactie een MATCH-VOORSTEL tegen de
inventaris (items), op basis van de foto-waarnemingen in data/_vision_findings.json
(handmatig/visueel ingevuld) + prijs-tiebreak. Inkoop-transacties worden apart en
simpel weggeschreven als 'nieuw item later'.

NIETS wordt afgeboekt; alleen match_voorstellen wordt (leeggemaakt en) gevuld.

    python3 inventory/scripts/match_foto.py      # of vanuit src/: python3 match_foto.py
"""

import json
import os
import re
from collections import Counter, defaultdict

from sqlalchemy import text
from rapidfuzz import fuzz

from db import get_engine, INVENTORY

ROOT = INVENTORY.parent
FINDINGS = json.loads((INVENTORY / "data" / "_vision_findings.json").read_text())

NAAM_DREMPEL = 70          # minimale fuzzy-score om als kandidaat te tellen
ZEKER_NAAM = 85            # naam duidelijk één kaart
PRIJS_MARGE = 0.30         # bedrag mag ~30% van comp_prijs afwijken voor 'zeker'


def basename(mf):
    return os.path.basename(mf) if mf else None


def real_file(mf):
    return bool(mf) and os.path.exists(ROOT / mf)


def norm_num(s):
    s = str(s).upper().replace(" ", "").split("/")[0]
    return re.sub(r"^([A-Z-]*)0*(\d)", r"\1\2", s)


def kern(naam):
    return re.sub(r"[\s\-]+[\d/#][\w/]*$", "", naam or "").strip() or (naam or "")


def load_items(conn):
    rows = conn.execute(text(
        "SELECT id, onze_naam, officiele_naam, code, set_code, comp_prijs, prijs_cm, "
        "categorie, taal FROM items")).mappings().all()
    return [dict(r) for r in rows]


def naam_score(gelezen, it):
    g = kern(gelezen).lower()
    best = 0
    for veld in (it["officiele_naam"], it["onze_naam"]):
        if veld:
            best = max(best, fuzz.token_set_ratio(g, veld.lower()))
    return best


def code_match(gelezen_code, it):
    if not gelezen_code:
        return False
    want = norm_num(gelezen_code)
    for c in (it["code"], it["set_code"]):
        if c and norm_num(str(c).split()[-1]) == want:
            return True
    return False


def zoek_kandidaten(naam, code, items):
    scored = []
    for it in items:
        s = naam_score(naam, it)
        cm = code_match(code, it)
        # code-match telt alleen als de naam óók enigszins lijkt — anders is een
        # los kaartnummer een toevallige collisie (reverse/promo/andere set)
        if s >= NAAM_DREMPEL or (cm and s >= 50):
            scored.append((cm and s >= 50, s, it))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return scored


def kand_json(scored, limit=5):
    return json.dumps([
        {"item_id": it["id"], "naam": it["officiele_naam"] or it["onze_naam"],
         "code": it["code"], "comp_prijs": float(it["comp_prijs"]) if it["comp_prijs"] else None}
        for _, _, it in scored[:limit]
    ], ensure_ascii=False)


def prijs_ok(bedrag, comp):
    if bedrag is None or comp in (None, 0):
        return None
    return abs(float(bedrag) - float(comp)) <= PRIJS_MARGE * float(comp)


def match_single(naam, code, bedrag, items, methode, sealed=False):
    """Geeft dict met velden voor één leesbare (single) kaart."""
    scored = zoek_kandidaten(naam, code, items)
    if not scored:
        return {"zekerheid": "niet_gevonden", "voorgesteld_item_id": None,
                "kandidaten": None, "methode": methode,
                "reden": ("sealed product niet in items" if sealed
                          else f"naam '{naam}' lijkt op geen enkel item (< {NAAM_DREMPEL}%)")}

    top_cm, top_s, top_it = scored[0]
    comp = top_it["comp_prijs"]
    pok = prijs_ok(bedrag, comp)
    # uniek sterk: 1 duidelijke naam-winnaar of unieke code-match
    tweede_s = scored[1][1] if len(scored) > 1 else 0
    uniek = (top_s >= ZEKER_NAAM and top_s - tweede_s >= 10) or \
            (top_cm and sum(1 for cm, _, _ in scored if cm) == 1)

    if sealed:
        return {"zekerheid": "twijfel", "voorgesteld_item_id": top_it["id"],
                "kandidaten": kand_json(scored), "methode": methode,
                "reden": f"sealed product; beste naam-match {top_s}%, handmatig bevestigen"}

    if uniek and pok is True:
        return {"zekerheid": "zeker", "voorgesteld_item_id": top_it["id"],
                "kandidaten": None, "methode": methode,
                "reden": f"naam {top_s}% + bedrag EUR{bedrag} ~ comp EUR{comp}"}
    # twijfel met beste gok
    if bedrag is None:
        pr = "geen bedrag om te verifieren"
    elif pok is False:
        pr = f"bedrag EUR{bedrag} wijkt af van comp EUR{comp}"
    else:
        pr = "meerdere naam-kandidaten"
    return {"zekerheid": "twijfel", "voorgesteld_item_id": top_it["id"],
            "kandidaten": kand_json(scored), "methode": methode,
            "reden": f"naam {top_s}%, {pr}"}


def strip_amount_text(ruwe):
    """Haal media-placeholder, bestandsnaam en losse bedragen uit ruwe_tekst,
    laat mogelijke kaartnaam over."""
    t = re.sub(r"IMG-\d{8}-WA\d+\.\w+ \(bestand bijgevoegd\)", " ", ruwe or "")
    t = re.sub(r"<Media weggelaten>", " ", t)
    t = re.sub(r"€|euro|eur|eu\b|cash|totaal|voor|plus|\+", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\d+([.,]\d+)?", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def main():
    engine = get_engine()
    with engine.begin() as conn:
        items = load_items(conn)
        txs = conn.execute(text(
            "SELECT id, kanaal, type, bedrag, media_file, flag, ruwe_tekst "
            "FROM transactions ORDER BY id")).mappings().all()

        conn.execute(text("TRUNCATE match_voorstellen RESTART IDENTITY"))

        rows = []
        for t in txs:
            bn = basename(t["media_file"])
            find = FINDINGS.get(bn) if bn else None
            heeft_foto = real_file(t["media_file"])

            if t["type"] == "inkoop":
                leesbaar = bool(find["foto_leesbaar"]) if find else False
                gelezen = find["gelezen_tekst"] if find else None
                rows.append({
                    "transaction_id": t["id"], "voorgesteld_item_id": None,
                    "zekerheid": "niet_gevonden", "methode": "inkoop - nieuw item",
                    "foto_leesbaar": leesbaar, "gelezen_tekst": gelezen,
                    "kandidaten": None,
                    "reden": "inkoop: nieuwe aankoop, wordt later als nieuw item aangemaakt"})
                continue

            # verkoop/trade
            if not heeft_foto:
                naam = strip_amount_text(t["ruwe_tekst"])
                if len(naam) >= 3:
                    res = match_single(naam, None, t["bedrag"], items, "geen foto")
                    res["reden"] = "geen foto; " + res["reden"]
                else:
                    res = {"zekerheid": "niet_gevonden", "voorgesteld_item_id": None,
                           "kandidaten": None, "methode": "geen foto",
                           "reden": "geen foto en geen leesbare kaartnaam in tekst"}
                rows.append({"transaction_id": t["id"], "foto_leesbaar": False,
                             "gelezen_tekst": naam or None, **res})
                continue

            # met foto -> gebruik vision-findings
            if not find:
                rows.append({"transaction_id": t["id"], "voorgesteld_item_id": None,
                             "zekerheid": "foto_onbruikbaar", "methode": "foto gelezen",
                             "foto_leesbaar": False, "gelezen_tekst": None,
                             "kandidaten": None, "reden": "foto aanwezig maar geen waarneming vastgelegd"})
                continue

            gelezen = find["gelezen_tekst"]
            if not find["foto_leesbaar"]:
                rows.append({"transaction_id": t["id"], "voorgesteld_item_id": None,
                             "zekerheid": "foto_onbruikbaar", "methode": "foto gelezen",
                             "foto_leesbaar": False, "gelezen_tekst": gelezen,
                             "kandidaten": None, "reden": "kaart op foto niet leesbaar (te vaag/afstand/glare)"})
                continue

            if (find.get("n_kaarten") or 1) > 1:
                rows.append({"transaction_id": t["id"], "voorgesteld_item_id": None,
                             "zekerheid": "meerdere_kaarten", "methode": "foto gelezen",
                             "foto_leesbaar": True, "gelezen_tekst": gelezen,
                             "kandidaten": None,
                             "reden": f"{find['n_kaarten']} kaarten op 1 foto; som-bedrag hoort bij de hele stapel, geen 1-op-1"})
                continue

            sealed = "sealed" in (gelezen or "").lower()
            res = match_single(find.get("kaart_naam"), find.get("kaart_code"),
                               t["bedrag"], items,
                               "foto gelezen (sealed product)" if sealed else "naam+prijs",
                               sealed=sealed)
            rows.append({"transaction_id": t["id"], "foto_leesbaar": True,
                         "gelezen_tekst": gelezen, **res})

        conn.execute(text("""
            INSERT INTO match_voorstellen
              (transaction_id, voorgesteld_item_id, zekerheid, methode,
               foto_leesbaar, gelezen_tekst, kandidaten, reden)
            VALUES
              (:transaction_id, :voorgesteld_item_id, :zekerheid, :methode,
               :foto_leesbaar, :gelezen_tekst, CAST(:kandidaten AS jsonb), :reden)
        """), rows)

    rapport(engine)


def rapport(engine):
    with engine.connect() as conn:
        data = conn.execute(text("""
            SELECT mv.zekerheid, mv.methode, mv.foto_leesbaar, t.type, t.kanaal,
                   t.bedrag, t.media_file, t.flag, t.ruwe_tekst, mv.gelezen_tekst,
                   mv.reden, t.id AS tx
            FROM match_voorstellen mv JOIN transactions t ON t.id = mv.transaction_id
        """)).mappings().all()

    vt = [r for r in data if r["type"] != "inkoop"]
    ink = [r for r in data if r["type"] == "inkoop"]

    def euro(rows):
        return sum(float(r["bedrag"]) for r in rows if r["bedrag"] is not None)

    lines = []
    p = lines.append
    p("=" * 64)
    p("MEETRAPPORT — Sessie A: foto -> match-voorstel (niets afgeboekt)")
    p("=" * 64)
    p(f"Verkoop/trade-transacties verwerkt : {len(vt)}")
    p(f"Inkoop-transacties (apart)         : {len(ink)}")
    p("")

    p("Verdeling zekerheid (verkoop/trade):")
    cnt = Counter(r["zekerheid"] for r in vt)
    som = defaultdict(float)
    for r in vt:
        if r["bedrag"] is not None:
            som[r["zekerheid"]] += float(r["bedrag"])
    for z in ["zeker", "twijfel", "niet_gevonden", "foto_onbruikbaar", "meerdere_kaarten"]:
        n = cnt.get(z, 0)
        pct = 100 * n / len(vt) if vt else 0
        p(f"  {z:<18} {n:>3}  ({pct:4.1f}%)   som EUR {som[z]:>8.2f}")
    p("")

    echte = [r for r in vt if r["media_file"] and os.path.exists(ROOT / r["media_file"])]
    geen = [r for r in vt if not (r["media_file"] and os.path.exists(ROOT / r["media_file"]))]
    p(f"Foto's (verkoop/trade):")
    p(f"  met echte foto            : {len(echte)}")
    p(f"  zonder (<Media weggelaten>): {len(geen)}")
    leesbaar = sum(1 for r in echte if r["foto_leesbaar"])
    p(f"  van de foto's leesbaar    : {leesbaar}")
    p(f"  van de foto's onbruikbaar : {len(echte) - leesbaar}")
    p("")

    p(f"Bedragen: verkoop/trade totaal EUR {euro(vt):.2f}  |  inkoop totaal EUR {euro(ink):.2f}")
    zeker_som = som['zeker']
    onzeker_som = euro(vt) - zeker_som
    p(f"  zeker te koppelen  : EUR {zeker_som:.2f}")
    p(f"  nog onzeker        : EUR {onzeker_som:.2f}")
    p("")

    p("Top-10 duurste verkoop/trade-transacties + status:")
    top = sorted([r for r in vt if r["bedrag"] is not None],
                 key=lambda r: float(r["bedrag"]), reverse=True)[:10]
    for r in top:
        txt = (r["gelezen_tekst"] or r["ruwe_tekst"] or "")[:52]
        p(f"  tx{r['tx']:<4} EUR{float(r['bedrag']):>7.2f}  {r['zekerheid']:<16} {txt}")
    p("")
    p("Inkoop-transacties (voorbereiding nieuw item):")
    for r in ink:
        b = f"EUR{float(r['bedrag']):.2f}" if r["bedrag"] is not None else "EUR   ?"
        txt = (r["gelezen_tekst"] or r["ruwe_tekst"] or "")[:50]
        p(f"  tx{r['tx']:<4} {b:>9}  foto_leesbaar={str(r['foto_leesbaar']):<5} {txt}")

    verslag = "\n".join(lines)
    (ROOT / "data" / "match_voorstellen_rapport.txt").write_text(verslag + "\n", encoding="utf-8")
    print(verslag)
    print(f"\nRapport: {ROOT / 'data' / 'match_voorstellen_rapport.txt'}")


if __name__ == "__main__":
    main()
