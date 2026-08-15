"""Zoekt vermoedelijke dubbele beursverkopen en schrijft er een rapport over.

    python src/dubbelingen.py [--event 3] [--rapport PAD]

Verwijdert niets — dit is puur een leesopdracht. Het rapport is bedoeld om met de
hand na te lopen; pas daarna gaat `src/afboeken_sales.py` aan de slag.

Waarom de regels zijn zoals ze zijn: snelheid alleen zegt weinig. De kleinste
tussenpozen tussen twee *verschillende* kaarten zijn 1-2 seconden (het team tikt
een stapeltje achter elkaar in), dus "kort na elkaar" is op zichzelf geen bewijs.
Wat wél telt:

- **voorraad**: staat er maar 1 van die kaart in de inventaris en is hij twee keer
  verkocht, dan kan er per definitie één niet kloppen.
- **vrije invoer**: bij een vrij getypte naam moet iemand die naam intikken. Twee
  identieke regels binnen 20 seconden kunnen dus geen twee losse invoeren zijn.
  Een minuut later kan het wél echt een tweede boosterpakje zijn — die gaan naar
  de twijfellijst.
- **herhaalde reeks**: dezelfde kaarten in dezelfde volgorde, uren later opnieuw.
  Eén regel op zichzelf zou toeval kunnen zijn; een rij van drie of meer niet.

Twee losse verkopen van hetzelfde bedrag op verschillende momenten blijven staan.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import get_engine, INVENTORY  # noqa: E402

RAPPORT = INVENTORY.parent / "data" / "dubbelingen_rapport.txt"

DIRECT = timedelta(seconds=20)       # te snel om een naam opnieuw te typen
VENSTER = timedelta(minutes=15)      # nog steeds dezelfde klant aan de tafel
REEKS_MIN = 3                        # vanaf hoeveel opeenvolgende herhalingen


def sleutel(r) -> str:
    """Waarop twee regels 'dezelfde kaart' zijn. Vrije invoer heeft geen item_id;
    daar is de getypte naam het enige haakje."""
    if r["item_id"]:
        return f"item:{r['item_id']}"
    return f"tekst:{(r['ruwe_tekst'] or '').strip().lower()}"


def stip(r):
    return r["tijd"]


def verschil(a, b) -> timedelta:
    """Tijd tussen twee regels van dezelfde dag."""
    return abs(timedelta(hours=b["tijd"].hour, minutes=b["tijd"].minute, seconds=b["tijd"].second)
               - timedelta(hours=a["tijd"].hour, minutes=a["tijd"].minute, seconds=a["tijd"].second))


def haal_op(conn, event_id: int):
    return conn.execute(text("""
        SELECT t.id, t.datum, t.tijd, t.type, t.bedrag, t.item_id, t.code, t.flag,
               t.ruwe_tekst, i.onze_naam, i.aantal AS voorraad, i.categorie
        FROM transactions t
        LEFT JOIN items i ON i.id = t.item_id
        WHERE t.event_id = :e AND t.type = 'verkoop'
        ORDER BY t.datum, t.tijd, t.id
    """), {"e": event_id}).mappings().all()


def naam(r) -> str:
    return r["onze_naam"] or (r["ruwe_tekst"] or "?")


def regel(r) -> str:
    return (f"#{r['id']:<4} {r['datum']} {str(r['tijd'])[:8]}  € {float(r['bedrag']):>8.2f}  "
            f"{naam(r)[:40]:<40} {'[vrij]' if not r['item_id'] else ''}")


def paren(rijen):
    """Kandidaat-dubbelingen: zelfde kaart, zelfde bedrag, binnen het venster."""
    per = defaultdict(list)
    for r in rijen:
        per[(sleutel(r), float(r["bedrag"] or 0), r["datum"])].append(r)

    zeker, twijfel = [], []
    for (sl, bedrag, _), groep in per.items():
        groep.sort(key=stip)
        for a, b in zip(groep, groep[1:]):
            dt = verschil(a, b)
            if dt > VENSTER:
                continue
            voorraad = a["voorraad"]
            vrij = not a["item_id"]
            if voorraad is not None and voorraad <= 1:
                reden = f"er is er maar {voorraad} op voorraad"
                zeker.append((a, b, dt, reden))
            elif vrij and dt <= DIRECT:
                reden = "vrij getypte naam, twee keer binnen 20 seconden"
                zeker.append((a, b, dt, reden))
            else:
                reden = (f"voorraad {voorraad}" if voorraad is not None else "vrije invoer")
                twijfel.append((a, b, dt, reden))
    zeker.sort(key=lambda t: t[0]["id"])
    twijfel.sort(key=lambda t: t[0]["id"])
    return zeker, twijfel


def reeksen(rijen):
    """Dezelfde kaarten in dezelfde volgorde, later nog eens ingevoerd."""
    n = len(rijen)
    gevonden = []
    gebruikt = set()
    for i in range(n):
        for j in range(i + 1, n):
            if verschil(rijen[i], rijen[j]) <= VENSTER and rijen[i]["datum"] == rijen[j]["datum"]:
                continue  # dat vangen de paren al af
            lengte = 0
            while (i + lengte < j and j + lengte < n
                   and sleutel(rijen[i + lengte]) == sleutel(rijen[j + lengte])):
                lengte += 1
            if lengte >= REEKS_MIN and not ({r["id"] for r in rijen[j:j + lengte]} & gebruikt):
                gevonden.append((rijen[i:i + lengte], rijen[j:j + lengte]))
                gebruikt |= {r["id"] for r in rijen[j:j + lengte]}
    return gevonden


def centen(rijen):
    """Bedragen die geen echte verkoop kunnen zijn (proef-invoer, blijven hangen
    op de knop). Geen dubbeling, wel iets om over te beslissen."""
    return [r for r in rijen if float(r["bedrag"] or 0) < 1]


def teveel_verkocht(rijen):
    """Meer stuks verkocht dan er op voorraad staan — hard signaal, los van tijd."""
    per = defaultdict(list)
    for r in rijen:
        if r["item_id"]:
            per[r["item_id"]].append(r)
    uit = []
    for _, groep in per.items():
        voorraad = groep[0]["voorraad"]
        if voorraad is not None and len(groep) > voorraad:
            uit.append((groep, voorraad))
    uit.sort(key=lambda t: -len(t[0]))
    return uit


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--event", type=int, default=3)
    p.add_argument("--rapport", type=Path, default=RAPPORT)
    args = p.parse_args()

    engine = get_engine()
    with engine.connect() as conn:
        rijen = haal_op(conn, args.event)
        event = conn.execute(text("SELECT name FROM events WHERE id = :e"),
                             {"e": args.event}).scalar()

    cent = centen(rijen)
    # Regels onder de euro zijn geen verkoop; ze zouden de paren- en reeksanalyse
    # alleen maar volstampen. Ze krijgen hun eigen sectie.
    echt = [r for r in rijen if r not in cent]
    zeker, twijfel = paren(echt)
    herhaald = reeksen(echt)
    teveel = teveel_verkocht(echt)

    dagen = sorted({r["datum"] for r in rijen})
    uit = []
    uit.append(f"DUBBELINGEN-RAPPORT — {event}")
    uit.append(f"{len(rijen)} verkopen, dagen: {', '.join(str(d) for d in dagen)}")
    uit.append(f"totaal € {sum(float(r['bedrag'] or 0) for r in rijen):,.2f}")
    uit.append("")
    uit.append("Er is nog NIETS verwijderd. Dit is een voorstel om na te lopen.")
    uit.append("=" * 78)

    uit.append("")
    uit.append(f"A. ZEKER — {len(zeker)} paren ({len(zeker)} regels te veel)")
    uit.append("   Zelfde kaart, zelfde bedrag, vlak na elkaar, en de voorraad of de")
    uit.append("   vrije invoer sluit een tweede echte verkoop uit.")
    uit.append("")
    for a, b, dt, reden in zeker:
        uit.append(f"   houden  {regel(a)}")
        uit.append(f"   WEG     {regel(b)}")
        uit.append(f"           {int(dt.total_seconds())} s ertussen — {reden}")
        uit.append("")

    uit.append("=" * 78)
    uit.append("")
    uit.append(f"B. HERHAALDE REEKS — {len(herhaald)} reeks(en)")
    uit.append("   Dezelfde kaarten in dezelfde volgorde, later opnieuw ingevoerd.")
    uit.append("")
    for eerste, tweede in herhaald:
        uit.append(f"   {len(tweede)} regels, oorspronkelijk om {str(eerste[0]['tijd'])[:8]}, "
                   f"opnieuw om {str(tweede[0]['tijd'])[:8]}")
        for a, b in zip(eerste, tweede):
            gelijk = float(a["bedrag"] or 0) == float(b["bedrag"] or 0)
            uit.append(f"     houden  {regel(a)}")
            uit.append(f"     {'WEG    ' if gelijk else 'LET OP '} {regel(b)}"
                       + ("" if gelijk else "   << ander bedrag, geen zekere kopie"))
        uit.append("")

    uit.append("=" * 78)
    uit.append("")
    uit.append(f"C. TWIJFEL — {len(twijfel)} paren, NIET verwijderen zonder na te denken")
    uit.append("   Zelfde kaart en bedrag kort na elkaar, maar er is voorraad genoeg;")
    uit.append("   twee klanten die hetzelfde pakje kopen ziet er precies zo uit.")
    uit.append("")
    for a, b, dt, reden in twijfel:
        uit.append(f"   {regel(a)}")
        uit.append(f"   {regel(b)}")
        uit.append(f"           {int(dt.total_seconds())} s ertussen — {reden}")
        uit.append("")

    uit.append("=" * 78)
    uit.append("")
    uit.append(f"D. MEER VERKOCHT DAN OP VOORRAAD — {len(teveel)} kaart(en)")
    uit.append("   Los van tijdstippen: hier kan de telling sowieso niet kloppen.")
    uit.append("")
    for groep, voorraad in teveel:
        uit.append(f"   {naam(groep[0])} — voorraad {voorraad}, {len(groep)} keer verkocht:")
        for r in groep:
            uit.append(f"     {regel(r)}")
        uit.append("")

    uit.append("=" * 78)
    uit.append("")
    uit.append(f"E. BEDRAGEN ONDER € 1 — {len(cent)} regels (geen dubbeling, wel raar)")
    uit.append("   Ziet eruit als proef-invoer of een knop die is blijven hangen.")
    uit.append("")
    for r in cent:
        uit.append(f"   {regel(r)}")

    voorstel = [b["id"] for _, b, _, _ in zeker]
    voorstel += [b["id"] for eerste, tweede in herhaald for a, b in zip(eerste, tweede)
                 if float(a["bedrag"] or 0) == float(b["bedrag"] or 0)]
    voorstel = sorted(set(voorstel))
    bedrag = sum(float(r["bedrag"] or 0) for r in rijen if r["id"] in voorstel)

    uit.append("")
    uit.append("=" * 78)
    uit.append("")
    uit.append("SAMENVATTING")
    uit.append(f"   verkopen nu            : {len(rijen)}  (€ {sum(float(r['bedrag'] or 0) for r in rijen):,.2f})")
    uit.append(f"   voorstel om te schrappen: {len(voorstel)} regels (€ {bedrag:,.2f}) — A + B")
    uit.append(f"   id's                   : {', '.join(str(i) for i in voorstel)}")
    uit.append(f"   blijft over            : {len(rijen) - len(voorstel)} verkopen "
               f"(€ {sum(float(r['bedrag'] or 0) for r in rijen) - bedrag:,.2f})")
    uit.append("")
    uit.append(f"   apart te beslissen     : {len(cent)} regels onder € 1 (sectie E)")
    uit.append(f"   niet aangeraakt        : {len(twijfel)} twijfelparen (sectie C)")

    tekst = "\n".join(uit) + "\n"
    args.rapport.write_text(tekst, encoding="utf-8")
    print(tekst)
    print(f"\nGeschreven naar: {args.rapport}")


if __name__ == "__main__":
    main()
