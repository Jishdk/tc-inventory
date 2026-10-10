r"""Tussentijdse opschoning van `Inventory TC.xlsx` na dag 1 van Cardmania XXL
Rosmalen (10-10-2026). Het bestand kwam uit Google Sheets terug (gedeelde
formules opnieuw gevouwen, caches aanwezig, structuur schoon) met drie dingen
die de importer of de cyclus in de weg zitten:

1. Kop A1 was per ongeluk `x\`` geworden in plaats van `Naam` — de importer
   weigert dan het hele bestand ("Kolommen ontbreken").
2. De 37 nieuwe rijen (841–877) hebben `Vorig aantal = 0`; per cyclusafspraak
   is Vorig aantal aan het begin van een beurs gelijk aan Aantal.
3. Poliwrath 24/165 (rij 268) en Prismatic SPC (rij 698) stonden op 0 en zijn
   vandaag verkocht. Werkelijkheid wint: Aantal en Vorig aantal op 1, zodat de
   afboeking van zondag op 0 uitkomt in plaats van op −1.
4. In Verkocht (L), Voorraadwaarde (O), Verkoopwaarde (P) en Status (Q) staat
   op een deel van de rijen een vaste waarde in plaats van de rijformule —
   verouderde restanten (Blastoise: 540 bij 3 × 130). Zolang de Dashboard-caches
   gesimuleerd werden viel dat niet op; Google Sheets rekent SUM(O:O) echt uit
   en kwam daardoor €448 boven de database uit. De formule gaat terug in die
   cellen, met de juiste cache.

Werkt op XML-niveau (formules en caches blijven staan), herrekent de rij- en
Dashboard-caches die door 3 verschuiven, en zet fullCalcOnLoad aan.

    python scripts/fix_v7_rosmalen_dag1.py BRON DOEL
"""
import re
import sys
import zipfile
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from superclean_v7 import CEL_RE, REF_RE, simuleer_dashboard, zet_cache  # noqa: E402
from opschonen_v7 import _cellen, getal_cel, tekst_cel, _zet_cel  # noqa: E402

SHEET = "xl/worksheets/sheet2.xml"
DASH = "xl/worksheets/sheet1.xml"
NIEUW = range(841, 878)
OP_VOORRAAD = {268: "Poliwrath", 698: "Prismatic SPC"}


FORMULES = {
    "L": 'IF(OR($J{n}="",$K{n}=""),"",MAX($K{n}-$J{n},0))',
    "O": 'IF(OR($A{n}="",$M{n}=""),"",$J{n}*$M{n})',
    "P": 'IF($A{n}="","",IF($N{n}<>"",$J{n}*$N{n},IF($M{n}<>"",$J{n}*$M{n},"")))',
    "Q": 'IF($A{n}="","",IF($J{n}<=0,"Uitverkocht","Op voorraad"))',
}


def formule_cel(cel, ref, formule, waarde):
    """Een cel met een gewone (niet-gedeelde) formule plus cache."""
    attrs = re.match(r"<c\b([^>]*?)/?>", cel).group(1) if cel else f' r="{ref}"'
    attrs = re.sub(r'\st="[^"]*"', "", attrs).rstrip()
    f = formule.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    if isinstance(waarde, str):
        return f'<c{attrs} t="str"><f>{f}</f><v>{waarde}</v></c>'
    v = f"{waarde:g}" if waarde == int(waarde) else repr(round(waarde, 10))
    return f"<c{attrs}><f>{f}</f><v>{v}</v></c>"


def rijwaarden(j, k, m, n):
    """Wat de vier rijformules opleveren voor een rij mét naam."""
    getal = lambda x: isinstance(x, (int, float))  # noqa: E731
    l = max(k - j, 0) if getal(j) and getal(k) else ""
    o = j * m if getal(j) and getal(m) else ""
    p = (j * n if getal(j) and getal(n) else (j * m if getal(j) and getal(m) else ""))
    q = "Uitverkocht" if (not getal(j) or j <= 0) else "Op voorraad"
    return {"L": l, "O": o, "P": p, "Q": q}


def main(bron: Path, doel: Path):
    wb = openpyxl.load_workbook(bron, data_only=True)
    inv = wb["Inventaris"]
    assert inv["A1"].value == "x`", inv["A1"].value
    for r, naam in OP_VOORRAAD.items():
        assert inv.cell(r, 1).value == naam and (inv.cell(r, 10).value or 0) == 0, (r, naam)
    for r in NIEUW:
        assert inv.cell(r, 1).value and (inv.cell(r, 11).value or 0) == 0, r

    zin = zipfile.ZipFile(bron)
    stukken = {n: zin.read(n) for n in zin.namelist()}
    zin.close()
    xml = stukken[SHEET].decode("utf-8")
    geraakt = []
    hersteld = {"L": 0, "O": 0, "P": 0, "Q": 0}

    def per_rij(m):
        nr = int(m.group(1))
        rij = origineel = m.group(0)
        cellen = _cellen(rij)
        if nr == 1:
            rij = _zet_cel(rij, "A1", tekst_cel(cellen.get("A"), "A1", "Naam"))
        elif nr in NIEUW:
            j = float(re.search(r"<v>([^<]*)</v>", cellen["J"]).group(1))
            rij = _zet_cel(rij, f"K{nr}", getal_cel(cellen.get("K"), f"K{nr}", f"{j:g}"))
        elif nr in OP_VOORRAAD:
            m_cm = float(re.search(r"<v>([^<]*)</v>", cellen["M"]).group(1))
            n_comp = float(re.search(r"<v>([^<]*)</v>", cellen["N"]).group(1))
            rij = _zet_cel(rij, f"J{nr}", getal_cel(cellen.get("J"), f"J{nr}", "1"))
            rij = _zet_cel(rij, f"K{nr}", getal_cel(cellen.get("K"), f"K{nr}", "1"))
            rij = _zet_cel(rij, f"O{nr}", zet_cache(cellen["O"], m_cm))
            rij = _zet_cel(rij, f"P{nr}", zet_cache(cellen["P"], n_comp))
            rij = _zet_cel(rij, f"Q{nr}", zet_cache(cellen["Q"], "Op voorraad"))
        if nr >= 2 and inv.cell(nr, 1).value not in (None, ""):
            j, k = inv.cell(nr, 10).value, inv.cell(nr, 11).value
            if nr in NIEUW:
                k = j
            if nr in OP_VOORRAAD:
                j = k = 1
            j, k = (0 if j in (None, "") else j), (0 if k in (None, "") else k)
            m, n = inv.cell(nr, 13).value, inv.cell(nr, 14).value
            m, n = (None if m == "" else m), (None if n == "" else n)
            uitkomst = rijwaarden(j, k, m, n)
            cellen = _cellen(rij)
            for kol in "LOPQ":
                cel = cellen.get(kol)
                if cel and "<f" in cel:
                    continue
                rij = _zet_cel(rij, f"{kol}{nr}", formule_cel(
                    cel, f"{kol}{nr}", FORMULES[kol].format(n=nr), uitkomst[kol]))
                hersteld[kol] += 1
            if nr not in NIEUW and nr not in OP_VOORRAAD and rij == origineel:
                return rij
        elif nr != 1:
            return rij
        refs = [REF_RE.search(c.group(0)).group(1) for c in CEL_RE.finditer(rij)]
        if len(refs) != len(set(refs)) or refs != sorted(refs, key=lambda k: (len(k), k)):
            raise RuntimeError(f"rij {nr}: celstructuur kapot na bewerking: {refs}")
        if set(cellen) - set(refs):
            raise RuntimeError(f"rij {nr}: cellen kwijt")
        geraakt.append(nr)
        return rij

    xml = re.sub(r'<row\b[^>]*\br="(\d+)"[^>]*>.*?</row>', per_rij, xml, flags=re.S)
    stukken[SHEET] = xml.encode("utf-8")
    print(f"rijen bewerkt: {len(geraakt)} ({geraakt[:3]} … {geraakt[-3:]})")
    print(f"formules hersteld (vaste waarde -> rijformule): {hersteld}")

    # -- Dashboard-caches herrekenen op de nieuwe kolominhoud -----------------
    kol_data = {}
    for kol_idx in range(1, 20):
        letter = openpyxl.utils.get_column_letter(kol_idx)
        waarden = [inv.cell(r, kol_idx).value for r in range(1, inv.max_row + 1)]
        kol_data[letter] = waarden
    kol_data["A"][0] = "Naam"
    for r in OP_VOORRAAD:
        kol_data["J"][r - 1] = 1; kol_data["K"][r - 1] = 1
        kol_data["O"][r - 1] = kol_data["M"][r - 1]
        kol_data["P"][r - 1] = kol_data["N"][r - 1]
        kol_data["Q"][r - 1] = "Op voorraad"
    for r in NIEUW:
        kol_data["K"][r - 1] = kol_data["J"][r - 1]
    for r in range(2, inv.max_row + 1):
        if kol_data["A"][r - 1] in (None, ""):
            continue
        j, k, m, n = (kol_data[c][r - 1] for c in "JKMN")
        u = rijwaarden(0 if j in (None, "") else j, 0 if k in (None, "") else k,
                       None if m == "" else m, None if n == "" else n)
        for kol in "LOPQ":
            kol_data[kol][r - 1] = u[kol]
    for k, v in kol_data.items():
        kol_data[k] = [None if w == "" else w for w in v]

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
    print("dashboard-caches gewijzigd:", [(c, o, w) for c, o, w in veranderd])

    wbx = stukken["xl/workbook.xml"].decode("utf-8")
    wbx = re.sub(r"<calcPr[^>]*/>", '<calcPr fullCalcOnLoad="1"/>', wbx)
    stukken["xl/workbook.xml"] = wbx.encode("utf-8")
    assert "xl/calcChain.xml" not in stukken

    with zipfile.ZipFile(doel, "w", zipfile.ZIP_DEFLATED) as uit:
        for n, data in stukken.items():
            uit.writestr(n, data)
    print(f"geschreven: {doel}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
