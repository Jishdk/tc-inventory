#!/usr/bin/env python3
"""Parse WhatsApp-chatexports (beursweekend) naar een transactie-CSV.

Herbruikbaar na elke beurs. Voorbeeld:

    python parse_whatsapp.py \
        --channel inkoop=../../data/_inkoop \
        --channel sales=../../data/_sales \
        --dates 25-07-2026 26-07-2026 \
        --csv ../data/transacties_beurs_2026-07.csv \
        --summary ../data/transacties_samenvatting.txt

Elk kanaal wijst naar een map met de uitgepakte export (bevat één
"WhatsApp-chat met *.txt" + losse media) of direct naar het .txt-bestand.

Ondersteunde regelformats:
    [DD-MM-YYYY HH:MM:SS] Afzender: bericht
    DD-MM-YYYY HH:MM - Afzender: bericht
Vervolgregels zonder timestamp worden bij het vorige bericht gevoegd.
"""

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

# Onzichtbare richtingstekens die WhatsApp in exports stopt
INVISIBLE = dict.fromkeys(map(ord, "‎‏‪‫‬﻿"))

HEADER_RE = re.compile(
    r"^(?:\[(?P<d1>\d{2}-\d{2}-\d{4}) (?P<t1>\d{2}:\d{2})(?::\d{2})?\]\s"
    r"|(?P<d2>\d{2}-\d{2}-\d{4}) (?P<t2>\d{2}:\d{2}) - )"
    r"(?P<rest>.*)$"
)

# Timestamp die midden in een tekst geplakt zit ("...20 25-07-2026 11:14 - Ömer...")
EMBEDDED_TS_RE = re.compile(r"\s*\[?\d{2}-\d{2}-\d{4} \d{2}:\d{2}(?::\d{2})?\]?\s*-?\s")

ATTACHMENT_RE = re.compile(r"([\w()\- ]+?\.(?:jpe?g|png|webp|gif|pdf|mp4|opus|mp3))\s*\(bestand bijgevoegd\)")
MEDIA_OMITTED_RE = re.compile(r"<Media weggelaten>|<Media omitted>")
DELETED_RE = re.compile(r"Dit bericht is verwijderd|Je hebt dit bericht verwijderd|This message was deleted")

TRADE_RE = re.compile(r"\b(trade\w*|ruil\w*|erin|eruit|er in|er uit|in voor|uit voor)\b", re.IGNORECASE)

NUM = r"\d+(?:[.,]\d{1,2})?"
AMOUNT_PATTERNS = [
    re.compile(rf"€\s*({NUM})"),                      # €123 / € 123,50
    re.compile(rf"\b({NUM})\s*(?:euro|eur|eu)\b", re.IGNORECASE),
    re.compile(rf"\b({NUM})\s*cash\b", re.IGNORECASE),
    re.compile(rf"(?:\bvoor|\bplus|\+)\s*({NUM})(?![\w])", re.IGNORECASE),
]
# Los getal: niet vast aan letters (5x, 1te, WA0180) en niet na een grading-term
BARE_NUM_RE = re.compile(rf"(?<![\w,.€])({NUM})(?![\w])")
GRADE_BEFORE_RE = re.compile(r"\b(?:psa|cgc|bgs|ace)\s*$", re.IGNORECASE)


def clean(line: str) -> str:
    return line.translate(INVISIBLE).rstrip("\n")


def parse_chat(txt_path: Path):
    """Lever (datum, tijd, afzender, tekst) per bericht; vervolgregels samengevoegd."""
    messages = []
    current = None
    with open(txt_path, encoding="utf-8") as f:
        for raw in f:
            line = clean(raw)
            m = HEADER_RE.match(line)
            if m:
                if current:
                    messages.append(current)
                date = m.group("d1") or m.group("d2")
                time = m.group("t1") or m.group("t2")
                rest = m.group("rest")
                if ": " in rest:
                    sender, text = rest.split(": ", 1)
                    current = {"datum": date, "tijd": time, "afzender": sender.strip(), "tekst": text}
                else:
                    current = None  # systeembericht (groepsnaam, deelnemers, etc.)
            elif current is not None:
                current["tekst"] += "\n" + line
    if current:
        messages.append(current)

    # Merged timestamps: alles vanaf een ingebedde datum-tijd strippen
    for msg in messages:
        m = EMBEDDED_TS_RE.search(msg["tekst"])
        if m:
            msg["tekst"] = msg["tekst"][: m.start()]
    return messages


def extract_amount(text: str):
    """Bedrag volgens prioriteit: €-notatie, euro/eu-suffix, cash, voor/plus/+, los getal."""
    for pat in AMOUNT_PATTERNS:
        m = pat.search(text)
        if m and not GRADE_BEFORE_RE.search(text[: m.start(1)]):
            return m.group(1)
    candidates = [
        m.group(1)
        for m in BARE_NUM_RE.finditer(text)
        if not GRADE_BEFORE_RE.search(text[: m.start(1)])
    ]
    return candidates[-1] if candidates else None


def process_channel(name: str, source: Path, dates: set[str], media_dir: Path):
    if source.is_dir():
        txts = sorted(source.glob("*.txt"))
        if not txts:
            sys.exit(f"Geen .txt-export gevonden in {source}")
        txt_path = txts[0]
    else:
        txt_path = source

    rows = []
    seen = set()
    for msg in parse_chat(txt_path):
        if msg["datum"] not in dates:
            continue
        text = msg["tekst"].strip()
        if DELETED_RE.search(text):
            continue

        # Dedup: identieke tijd + afzender + tekst (dubbel doorgestuurde foto's)
        key = (msg["datum"], msg["tijd"], msg["afzender"], text)
        if key in seen:
            continue
        seen.add(key)

        att = ATTACHMENT_RE.search(text)
        media_file = ""
        if att:
            fname = att.group(1).strip()
            candidate = media_dir / fname
            media_file = str(candidate) if candidate.exists() else fname
        is_media = bool(att) or bool(MEDIA_OMITTED_RE.search(text))

        # Bestandsnamen en media-placeholder weghalen vóór bedrag-extractie
        body = ATTACHMENT_RE.sub(" ", text)
        body = MEDIA_OMITTED_RE.sub(" ", body).strip()

        amount = extract_amount(body)
        if amount is None and not is_media:
            continue  # chatter zonder bedrag en zonder media

        if name == "inkoop":
            tx_type = "inkoop"
        elif TRADE_RE.search(body):
            tx_type = "trade"
        else:
            tx_type = "verkoop"

        rows.append({
            "datum": msg["datum"],
            "tijd": msg["tijd"],
            "kanaal": name,
            "afzender": msg["afzender"],
            "type": tx_type,
            "bedrag": amount.replace(",", ".") if amount else "",
            "media_file": media_file,
            "flag": "" if amount else "CHECK BEDRAG",
            "ruwe_tekst": " ".join(text.split()),
        })
    return rows


def build_summary(rows):
    stats = defaultdict(lambda: {"n": 0, "som": 0.0, "check": 0})
    for r in rows:
        s = stats[(r["kanaal"], r["datum"])]
        s["n"] += 1
        if r["bedrag"]:
            s["som"] += float(r["bedrag"])
        if r["flag"]:
            s["check"] += 1

    lines = ["Samenvatting transacties beursweekend", "=" * 45]
    for (kanaal, datum) in sorted(stats):
        s = stats[(kanaal, datum)]
        lines.append(
            f"{kanaal:<8} {datum}: {s['n']:>3} transacties, som €{s['som']:>10.2f}, "
            f"CHECK BEDRAG: {s['check']}"
        )
    totaal = sum(s["n"] for s in stats.values())
    som = sum(s["som"] for s in stats.values())
    checks = sum(s["check"] for s in stats.values())
    lines.append("-" * 45)
    lines.append(f"Totaal: {totaal} transacties, som €{som:.2f}, CHECK BEDRAG: {checks}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channel", action="append", required=True, metavar="NAAM=PAD",
                    help="kanaalnaam=pad naar uitgepakte export (map of .txt); meermaals te gebruiken")
    ap.add_argument("--dates", nargs="+", required=True, metavar="DD-MM-YYYY",
                    help="alleen berichten van deze datums")
    ap.add_argument("--csv", required=True, help="pad voor de output-CSV")
    ap.add_argument("--summary", required=True, help="pad voor het samenvattingsbestand")
    args = ap.parse_args()

    rows = []
    for spec in args.channel:
        if "=" not in spec:
            sys.exit(f"--channel verwacht NAAM=PAD, kreeg: {spec}")
        name, path = spec.split("=", 1)
        source = Path(path).expanduser()
        media_dir = source if source.is_dir() else source.parent
        rows.extend(process_channel(name, source, set(args.dates), media_dir))

    rows.sort(key=lambda r: (r["datum"].split("-")[::-1], r["tijd"], r["kanaal"]))

    csv_path = Path(args.csv).expanduser()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "datum", "tijd", "kanaal", "afzender", "type",
            "bedrag", "media_file", "flag", "ruwe_tekst",
        ])
        writer.writeheader()
        writer.writerows(rows)

    summary = build_summary(rows)
    Path(args.summary).expanduser().write_text(summary + "\n", encoding="utf-8")
    print(summary)
    print(f"\nCSV: {csv_path} ({len(rows)} regels)")


if __name__ == "__main__":
    main()
