#!/usr/bin/env python3
"""Screenshots van beurs_app.py op telefoonformaat (390x844).

Draait de échte app op een losse poort tegen een SQLite-schaduwdatabase met
dezelfde fixture als `tests/test_beurs_app.py`, en schiet de schermen met
Playwright. Zo zijn de plaatjes reproduceerbaar zonder Supabase en zonder dat er
ook maar iets in de echte database belandt.

    python scripts/screenshots_beurs_app.py [--uit screenshots] [--prefix v3]

Twee dingen die op deze WSL-machine misgingen en hier zijn opgelost:

* **`libasound.so.2` ontbreekt.** Playwright's chromium start er niet zonder, en
  `playwright install-deps` vraagt sudo. Het script pakt het pakket eenmalig uit
  in een cachemap en zet `LD_LIBRARY_PATH` zelf goed — er is niets te installeren.
* **`src/db.py` verwacht een Postgres-DSN** en stopt op een sqlite-URL. De app
  draait in een eigen proces, dus monkeypatchen zoals de tests doen kan niet.
  In plaats daarvan komt er een `sitecustomize.py` op `PYTHONPATH` te staan die
  `db.get_engine` omzet vóórdat streamlit de app importeert — plus de
  date/time-adapters die Python 3.12 niet meer standaard levert.
"""

import argparse
import datetime
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

INVENTORY = Path(__file__).resolve().parent.parent
CACHE = Path(tempfile.gettempdir()) / "tc_shots_cache"
POORT = 8599


def zorg_voor_libasound() -> str | None:
    """Pakt libasound lokaal uit als het systeem het niet heeft. Geeft het pad."""
    if list(Path("/usr/lib/x86_64-linux-gnu").glob("libasound.so.2*")):
        return None
    doel = CACHE / "libs"
    lib = doel / "root/usr/lib/x86_64-linux-gnu"
    if not (lib / "libasound.so.2").exists():
        doel.mkdir(parents=True, exist_ok=True)
        print("libasound.so.2 ontbreekt — eenmalig uitpakken (geen sudo nodig)…")
        subprocess.run(["apt-get", "download", "libasound2t64"], cwd=doel, check=True,
                       stdout=subprocess.DEVNULL)
        deb = next(doel.glob("libasound2t64_*.deb"))
        subprocess.run(["dpkg-deb", "-x", str(deb), "root"], cwd=doel, check=True)
    return str(lib)


def sitecustomize() -> str:
    """Map met de sitecustomize die db.get_engine naar sqlite omzet."""
    map_ = CACHE / "pypatch"
    map_.mkdir(parents=True, exist_ok=True)
    (map_ / "sitecustomize.py").write_text(f'''\
"""Zet db.get_engine om naar de schaduwdatabase vóór streamlit de app laadt."""
import os, sys, sqlite3, datetime

# Python 3.12 levert geen adapters meer voor date/time; de app geeft die wél mee.
sqlite3.register_adapter(datetime.date, lambda d: d.isoformat())
sqlite3.register_adapter(datetime.time, lambda t: t.isoformat())
sqlite3.register_adapter(datetime.datetime, lambda d: d.isoformat(" "))

pad = os.environ.get("TC_SHOT_DB")
if pad:
    sys.path.insert(0, {str(INVENTORY / "src")!r})
    import db
    from sqlalchemy import create_engine
    _eng = create_engine("sqlite:///" + pad, connect_args={{"check_same_thread": False}})
    db.get_engine = lambda: _eng
''')
    return str(map_)


def start_app(db_pad: str, libpad: str | None, dagen: str | None = None) -> subprocess.Popen:
    env = {**os.environ}
    env["TC_SHOT_DB"] = db_pad
    # Alleen om de aanwezigheidscheck in de app te passeren; get_engine is gepatcht.
    env["SUPABASE_DB_URL"] = "postgresql://u:p@localhost:5432/x"
    env["TC_EVENT_NAAM"] = "Testbeurs screenshots"
    env["TC_EVENT_DATUM"] = "2026-08-29"
    # Eén dag (vandaag) tenzij `--dagen` iets anders zegt; met twee dagen laat
    # de balk bovenaan het weekend-totaal plus een regel per beursdag zien.
    env["TC_EVENT_DAGEN"] = dagen or datetime.date.today().isoformat()
    env["PYTHONPATH"] = sitecustomize() + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("TC_BEURS_PIN", None)
    if libpad:
        env["LD_LIBRARY_PATH"] = libpad + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "beurs_app.py",
         "--server.port", str(POORT), "--server.headless", "true",
         "--browser.gatherUsageStats", "false"],
        cwd=INVENTORY, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env)
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", POORT), 1).close()
            break
        except OSError:
            time.sleep(1)
    time.sleep(5)          # streamlit heeft na de socket nog even nodig
    return proc


class Scherm:
    """Dun laagje boven Playwright met de dingen die deze app nodig heeft."""

    def __init__(self, pagina, uit: Path, prefix: str):
        self.pg, self.uit, self.prefix = pagina, uit, prefix
        self.metingen: list[tuple[str, int | None]] = []

    def shot(self, naam: str):
        self.pg.wait_for_timeout(1000)
        self.pg.screenshot(path=str(self.uit / f"{self.prefix}-{naam}.png"))
        print("  ", f"{self.prefix}-{naam}")

    def typ(self, term: str):
        """fill() alleen is niet genoeg: Streamlit stuurt de waarde pas op Enter."""
        veld = self.pg.get_by_placeholder("zoek kaart")
        veld.fill(term)
        veld.press("Enter")
        self.pg.wait_for_timeout(2200)

    def klik(self, key: str, n: int = 0):
        # Eerst in beeld brengen: Streamlit hergebruikt DOM-knopen bij een rerun,
        # waardoor een knop die net nog zichtbaar was buiten de viewport kan
        # hangen. Playwright noemt dat "not visible" en wacht zich suf.
        # `visible=true`: Streamlit laat bij een rerun oude knopen in de DOM staan.
        # Zonder dit filter pakt nth(0) zo'n verborgen restant en wacht Playwright
        # zich suf op iets dat nooit meer zichtbaar wordt.
        knop = self.pg.locator(
            f'[class*="st-key-{key}"] button:visible').nth(n)
        knop.scroll_into_view_if_needed()
        self.pg.wait_for_timeout(300)
        knop.click()
        self.pg.wait_for_timeout(2000)

    def zoek_kijk(self, term: str):
        """De zoekbalk van de ZOEK-modus (eigen key, zelfde placeholder)."""
        veld = self.pg.locator('[class*="st-key-kijk_zoekterm"] input').first
        veld.fill(term)
        veld.press("Enter")
        self.pg.wait_for_timeout(2200)

    def kies(self, term: str, n: int = 0):
        self.typ(term)
        self.klik("pick_", n)

    def tel_zichtbaar(self, wat: str):
        """Hoeveel ZOEK-kaarten staan er volledig binnen de 844 px?

        De vouwmeting van VASTLEGGEN zegt niets over dit scherm: daar is de
        vraag niet waar de knop eindigt, maar hoeveel je ziet zonder te vegen."""
        n = self.pg.evaluate("""() => {
            const h = window.innerHeight;
            return [...document.querySelectorAll('.tc-kijk')]
                .filter(e => e.getBoundingClientRect().bottom <= h).length;
        }""")
        self.metingen.append((f"{wat}: {n} kaarten compleet in beeld", None))
        print(f"    {n} kaarten compleet in beeld — {wat}")

    def vouw(self, wat: str):
        """Waar eindigt VASTLEGGEN? De kernflow moet binnen 844 px blijven."""
        el = self.pg.query_selector('[class*="st-key-vastleggen"] button')
        box = el.bounding_box() if el else None
        px = round(box["y"] + box["height"]) if box else None
        self.metingen.append((wat, px))
        print(f"    VASTLEGGEN eindigt op {px} px — {wat}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--uit", default=str(INVENTORY / "screenshots"))
    p.add_argument("--prefix", default="zoek")
    p.add_argument("--dagen", default=None,
                   help="beursdagen als komma-gescheiden ISO-datums (default: vandaag)")
    args = p.parse_args()
    uit = Path(args.uit)
    uit.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(INVENTORY))
    sys.path.insert(0, str(INVENTORY / "tests"))
    import test_beurs_app as T

    engine = T.maak_engine()
    db_pad = str(engine.url.database)
    engine.dispose()      # de server opent zelf; geen tweede handle op hetzelfde bestand

    libpad = zorg_voor_libasound()
    if libpad:
        os.environ["LD_LIBRARY_PATH"] = libpad + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")

    proc = start_app(db_pad, libpad, args.dagen)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chromium")
            pagina = browser.new_page(viewport={"width": 390, "height": 844},
                                      device_scale_factor=2)
            pagina.goto(f"http://127.0.0.1:{POORT}", wait_until="networkidle")
            pagina.wait_for_timeout(5000)
            scherm = Scherm(pagina, uit, args.prefix)
            draaiboek(scherm)
            browser.close()
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=20)

    print("\nmetingen (390x844):")
    for wat, px in scherm.metingen:
        if px is None:          # een telling in plaats van een vouwmeting
            print(f"        -  {wat}")
            continue
        merk = "" if px <= 844 else "   <-- onder de vouw"
        print(f"   {px:>4} px  {wat}{merk}")


def draaiboek(s: Scherm):
    """Wat er geschoten wordt. Pas dit aan als er een scherm bijkomt.

    De vouw-metingen gaan over VASTLEGGEN: de kernflow moet binnen 844 px
    blijven. In ZOEK bestaat die knop niet — daar meten we niets, en dat is
    precies de bedoeling van die modus."""
    print("screenshots — kernflow verkoop")
    s.shot("1-start"); s.vouw("leeg mandje")
    s.typ("umbreon"); s.shot("2-zoeken")
    s.klik("pick_"); s.shot("3-een-kaart"); s.vouw("1 kaart + presetrij")
    s.kies("charizard"); s.vouw("2 kaarten")
    s.klik("vastleggen"); s.shot("4-vastgelegd")

    print("screenshots — ZOEK")
    s.klik("modus", 2)                       # VERKOOP, TRADE, ZOEK
    s.shot("5-zoek-leeg")
    s.zoek_kijk("umbreon"); s.shot("6-zoek-resultaten")
    # Een slab: alleen een cm-prijs, dus met de cm-badge en de uitleg erbij.
    s.zoek_kijk("pika van gogh"); s.shot("7-zoek-cm-prijs")
    # "bl" raakt drie kaarten die het scheidingswerk laten zien: dezelfde naam
    # met een PSA-grade naast een losse GD, plus een product op nul voorraad.
    s.zoek_kijk("bl"); s.shot("8-zoek-gelijknamig-en-uitverkocht")
    # Browsen: een langere lijst dan verkoop ooit toont. Meteen de maat nemen —
    # hoeveel kaarten staan er compleet in beeld zonder te scrollen?
    s.zoek_kijk("st"); s.shot("9-zoek-lijst"); s.tel_zichtbaar("ZOEK, 5 treffers")

    print("screenshots — terug naar verkoop")
    s.pg.mouse.wheel(0, -4000); s.pg.wait_for_timeout(800)
    s.klik("modus", 0)
    s.shot("10-terug-in-verkoop"); s.vouw("leeg mandje na ZOEK")


if __name__ == "__main__":
    main()
