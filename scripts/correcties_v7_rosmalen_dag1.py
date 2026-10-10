"""Drie correcties op `Inventory TC.xlsx` na dag 1 van Cardmania XXL Rosmalen
(10-10-2026), op XML-niveau zodat formules en caches intact blijven.

1. Nieuwe rijen die een bestaande naam+code dupliceren en een extra exemplaar
   zijn: Aantal (en Vorig aantal) +N op de bestaande rij, nieuwe rij leeg.
   Niet samengevoegd: paren waar de nieuwe rij `Promo = Sealed` draagt en de
   bestaande een losse single is (Lucario, Mewtwo, Alakazam) — dat zijn twee
   verschillende producten.
2. Scizor 30th (rij 841): kolom Set op "30th", zodat de reprint te
   onderscheiden is van de vintage Scizor 108/115 op rij 384.
3. De poker-slabs (#719) staan niet in v7 — niets aangeraakt.

    python scripts/correcties_v7_rosmalen_dag1.py BRON DOEL
"""
import re
import sys
import zipfile
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from superclean_v7 import CEL_RE, REF_RE, simuleer_dashboard, zet_cache  # noqa: E402
from opschonen_v7 import _cellen, _zet_cel, getal_cel, tekst_cel  # noqa: E402
from fix_v7_rosmalen_dag1 import rijwaarden  # noqa: E402

SHEET = "xl/worksheets/sheet2.xml"
DASH = "xl/worksheets/sheet1.xml"

# bestaande rij -> (nieuwe rij, extra stuks)
SAMENVOEGEN = {322: (842, 1),    # Eevee swsh087
               729: (858, 1),    # Mega Gengar 269/217
               682: (859, 2),    # Articuno 161/159
               110: (873, 1)}    # Frogadier 089/086
SET_KOLOM = {841: "30th"}        # Scizor 30th


def leeg_met_cache(cel: str) -> str:
    """Waarde eruit; een formule blijft staan met een lege cache ("")."""
    attrs = re.match(r"<c\b([^>]*?)/?>", cel).group(1)
    attrs = re.sub(r'\st="[^"]*"', "", attrs).rstrip()
    f = re.search(r"<f\b[^>]*(?:/>|>.*?</f>)", cel, re.S)
    return f'<c{attrs} t="str">{f.group(0)}<v></v></c>' if f else f"<c{attrs}/>"


def main(bron: Path, doel: Path):
    wb = openpyxl.load_workbook(bron, data_only=True)
    inv = wb["Inventaris"]
    for oud, (nieuw, extra) in SAMENVOEGEN.items():
        a, b = inv.cell(oud, 1).value, inv.cell(nieuw, 1).value
        assert a and b and a.strip().lower() == b.strip().lower(), (oud, nieuw, a, b)
        assert str(inv.cell(oud, 2).value).lower() == str(inv.cell(nieuw, 2).value).lower()
        assert (inv.cell(nieuw, 10).value or 0) == extra, (nieuw, inv.cell(nieuw, 10).value)
    for r, s in SET_KOLOM.items():
        assert inv.cell(r, 1).value == "Scizor 30th" and inv.cell(r, 3).value in (None, "")

    zin = zipfile.ZipFile(bron)
    stukken = {n: zin.read(n) for n in zin.namelist()}
    zin.close()
    xml = stukken[SHEET].decode("utf-8")
    leeg = {n for n, _ in SAMENVOEGEN.values()}
    verslag = []

    def per_rij(m):
        nr = int(m.group(1))
        rij = m.group(0)
        cellen = _cellen(rij)
        if nr in SAMENVOEGEN:
            j = float(re.search(r"<v>([^<]*)</v>", cellen["J"]).group(1))
            nieuw_j = j + SAMENVOEGEN[nr][1]
            rij = _zet_cel(rij, f"J{nr}", getal_cel(cellen.get("J"), f"J{nr}", f"{nieuw_j:g}"))
            rij = _zet_cel(rij, f"K{nr}", getal_cel(cellen.get("K"), f"K{nr}", f"{nieuw_j:g}"))
            m_, n_ = inv.cell(nr, 13).value, inv.cell(nr, 14).value
            u = rijwaarden(nieuw_j, nieuw_j, None if m_ in ("", None) else m_,
                           None if n_ in ("", None) else n_)
            for kol in "LOPQ":
                rij = _zet_cel(rij, f"{kol}{nr}", zet_cache(cellen[kol], u[kol]))
            verslag.append(f"rij {nr} {inv.cell(nr, 1).value}: aantal {j:g} -> {nieuw_j:g}")
        elif nr in leeg:
            for kol, cel in cellen.items():
                rij = rij.replace(cel, leeg_met_cache(cel), 1)
            verslag.append(f"rij {nr} {inv.cell(nr, 1).value}: leeggemaakt")
        elif nr in SET_KOLOM:
            rij = _zet_cel(rij, f"C{nr}", tekst_cel(cellen.get("C"), f"C{nr}", SET_KOLOM[nr]))
            verslag.append(f"rij {nr} {inv.cell(nr, 1).value}: Set = {SET_KOLOM[nr]!r}")
        else:
            return rij
        refs = [REF_RE.search(c.group(0)).group(1) for c in CEL_RE.finditer(rij)]
        if len(refs) != len(set(refs)) or refs != sorted(refs, key=lambda k: (len(k), k)):
            raise RuntimeError(f"rij {nr}: celstructuur kapot: {refs}")
        if set(cellen) - set(refs):
            raise RuntimeError(f"rij {nr}: cellen kwijt")
        return rij

    xml = re.sub(r'<row\b[^>]*\br="(\d+)"[^>]*>.*?</row>', per_rij, xml, flags=re.S)
    stukken[SHEET] = xml.encode("utf-8")
    print("\n".join(verslag))

    # -- Dashboard-caches herrekenen -------------------------------------------
    kol_data = {}
    for kol_idx in range(1, 20):
        letter = openpyxl.utils.get_column_letter(kol_idx)
        kol_data[letter] = [None if (w := inv.cell(r, kol_idx).value) == "" else w
                            for r in range(1, inv.max_row + 1)]
    for oud, (nieuw, extra) in SAMENVOEGEN.items():
        kol_data["J"][oud - 1] = kol_data["K"][oud - 1] = (kol_data["J"][oud - 1] or 0) + extra
        for letter in "ABCDEFGHIJKLMNOPQRS":
            kol_data[letter][nieuw - 1] = None
    for r in range(2, inv.max_row + 1):
        if kol_data["A"][r - 1] in (None, ""):
            continue
        j, k, m_, n_ = (kol_data[c][r - 1] for c in "JKMN")
        u = rijwaarden(j or 0, k or 0, m_, n_)
        for kol in "LOPQ":
            kol_data[kol][r - 1] = u[kol]

    d_xml = stukken[DASH].decode("utf-8")
    cel_cache, veranderd = {}, []

    def per_cel(m2):
        cel = m2.group(0)
        ref = REF_RE.search(cel)
        f = re.search(r"<f[^>]*>(.*?)</f>", cel, re.S)
        if not f or not ref:
            return cel
        tekst = (f.group(1).replace("&quot;", '"').replace("&lt;", "<")
                 .replace("&gt;", ">").replace("&amp;", "&"))
        w = simuleer_dashboard(tekst, kol_data)
        if w is None:
            expr = tekst
            for celref, wv in cel_cache.items():
                expr = re.sub(rf"\$?{celref[0]}\$?{celref[1:]}\b", repr(float(wv)), expr)
            kaal = re.sub(r"\s", "", expr)
            if re.fullmatch(r"[\d.eE()+\-*/]+", kaal):
                try:
                    w = eval(kaal)
                except Exception:
                    w = None
        if w is None:
            return cel
        cel_cache[ref.group(1) + ref.group(2)] = w
        oud = re.search(r"<v>([^<]*)</v>", cel)
        if oud is None or abs(float(oud.group(1)) - float(w)) > 1e-6:
            veranderd.append((ref.group(1) + ref.group(2), oud and oud.group(1), w))
        return zet_cache(cel, float(w))

    d_xml = CEL_RE.sub(per_cel, d_xml)
    d_xml = CEL_RE.sub(per_cel, d_xml)
    stukken[DASH] = d_xml.encode("utf-8")
    print("dashboard-caches gewijzigd:", veranderd)
    assert 'fullCalcOnLoad="1"' in stukken["xl/workbook.xml"].decode()

    with zipfile.ZipFile(doel, "w", zipfile.ZIP_DEFLATED) as uit:
        for n, data in stukken.items():
            uit.writestr(n, data)
    print(f"geschreven: {doel}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
