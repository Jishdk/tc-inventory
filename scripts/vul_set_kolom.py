"""Kolom Set (C) in de v7-Excel vullen vanuit `items.set_naam` — op XML-niveau,
formules en caches blijven staan. Rijen zonder set blijven leeg; een cel die al
een andere waarde heeft wordt niet overschreven (dan staat er iets wat het team
zelf heeft ingevuld, en dat wint).

    python scripts/vul_set_kolom.py BRON DOEL
"""
import re
import sys
import zipfile
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from opschonen_v7 import _cellen, _zet_cel, tekst_cel  # noqa: E402
from superclean_v7 import CEL_RE, REF_RE  # noqa: E402
from db import get_engine  # noqa: E402
from sqlalchemy import text  # noqa: E402

SHEET = "xl/worksheets/sheet2.xml"


def main(bron: Path, doel: Path):
    with get_engine().connect() as c:
        sets = {r[0]: r[1] for r in c.execute(text(
            "SELECT bron_rij, set_naam FROM items WHERE set_naam IS NOT NULL"))}
    inv = openpyxl.load_workbook(bron, data_only=True)["Inventaris"]
    zin = zipfile.ZipFile(bron)
    stukken = {n: zin.read(n) for n in zin.namelist()}
    zin.close()
    xml = stukken[SHEET].decode("utf-8")
    teller = {"gevuld": 0, "al_gelijk": 0, "anders_gelaten": []}

    def per_rij(m):
        nr = int(m.group(1))
        if nr not in sets:
            return m.group(0)
        rij = m.group(0)
        if inv.cell(nr, 1).value in (None, ""):
            raise RuntimeError(f"rij {nr}: set in de database maar geen naam in de Excel")
        huidig = inv.cell(nr, 3).value
        if huidig not in (None, ""):
            if str(huidig).strip().lower() == sets[nr].strip().lower():
                teller["al_gelijk"] += 1
                return rij
            # een afkorting van de teamnaam ("30th" -> "30th Celebration") mag
            # de volledige setnaam worden; iets anders laten we staan
            if not sets[nr].strip().lower().startswith(str(huidig).strip().lower()):
                teller["anders_gelaten"].append((nr, huidig, sets[nr]))
                return rij
        cellen = _cellen(rij)
        rij = _zet_cel(rij, f"C{nr}", tekst_cel(cellen.get("C"), f"C{nr}", sets[nr]))
        refs = [REF_RE.search(x.group(0)).group(1) for x in CEL_RE.finditer(rij)]
        if len(refs) != len(set(refs)) or refs != sorted(refs, key=lambda k: (len(k), k)):
            raise RuntimeError(f"rij {nr}: celstructuur kapot: {refs}")
        teller["gevuld"] += 1
        return rij

    xml = re.sub(r'<row\b[^>]*\br="(\d+)"[^>]*>.*?</row>', per_rij, xml, flags=re.S)
    stukken[SHEET] = xml.encode("utf-8")
    with zipfile.ZipFile(doel, "w", zipfile.ZIP_DEFLATED) as uit:
        for n, data in stukken.items():
            uit.writestr(n, data)
    print(f"Set gevuld op {teller['gevuld']} rijen, al gelijk {teller['al_gelijk']}, "
          f"anders gelaten {len(teller['anders_gelaten'])}: {teller['anders_gelaten'][:5]}")
    print(f"geschreven: {doel}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
