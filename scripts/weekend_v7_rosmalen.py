r"""Weekendcyclus Cardmania XXL Rosmalen (10/11-10-2026) op `Inventory TC.xlsx`,
op XML-niveau zodat formules en caches intact blijven.

Het bestand komt uit Google Sheets en is gebaseerd op de zaterdagversie van
vóór de fixes van 10-10 (kop A1 weer `x\``, de vier samengevoegde rijen weer
los, Poliwrath/Prismatic SPC weer op 0). Daarom doet fase 1 die fixes opnieuw,
bovenop de weekendrijen.

    python scripts/weekend_v7_rosmalen.py opschonen BRON DOEL
    python scripts/weekend_v7_rosmalen.py terugschrijven BRON DOEL AANTALLEN.json ANALYSE.json

Fase 1 `opschonen` (vóór de import):
- kop A1 -> Naam; categorie Single/slab -> single/Slab; "Psa 9" -> "PSA 9";
- dubbele nieuwe rijen met gelijke taal/conditie/categorie/promo: Aantal +n op
  de bestaande rij, nieuwe rij leeg (SAMENVOEGEN);
- Poliwrath 24/165 en Prismatic SPC op 1 (zaterdag verkocht bij stand 0);
- Vorig aantal = Aantal op de nieuwe rijen (841+);
- Scizor 30th: kolom Set = "30th";
- flip-rijen (vrij ingevoerd in de app, nooit in de Excel) onderaan erbij;
- vaste waarden in L/O/P/Q terug naar de rijformule; Dashboard-caches herrekend.

Fase 2 `terugschrijven` (na het afboeken): Aantal = database, Vorig aantal =
Aantal, caches van L/O/P/Q en het Dashboard herrekend, en een extra tab
"Weekend Rosmalen" met de weekendanalyse.
"""
import json
import re
import sys
import zipfile
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from superclean_v7 import CEL_RE, REF_RE, comp_ladder, simuleer_dashboard, zet_cache  # noqa: E402
from opschonen_v7 import _cellen, _zet_cel, getal_cel, tekst_cel  # noqa: E402
from fix_v7_rosmalen_dag1 import FORMULES, formule_cel, rijwaarden  # noqa: E402
from correcties_v7_rosmalen_dag1 import leeg_met_cache  # noqa: E402

SHEET = "xl/worksheets/sheet2.xml"
DASH = "xl/worksheets/sheet1.xml"
KOLOMMEN = "ABCDEFGHIJKLMNOPQRS"

# bestaande rij -> (nieuwe rij, extra stuks). Alleen paren met gelijke taal,
# conditie, categorie en promo-status; sealed promo naast losse single blijft apart.
SAMENVOEGEN = {
    322: (842, 1),   # Eevee swsh087 (Sealed, beide)
    729: (858, 1),   # Mega Gengar 269/217
    682: (859, 2),   # Articuno 161/159
    110: (873, 1),   # Frogadier 089/086
    109: (882, 1),   # Alakazam 201/165
    127: (884, 1),   # Poliwhirl 176/165 EN
    757: (925, 1),   # Machamp 1st 4/102 EX
    758: (926, 1),   # Blastoise base 2/102
    674: (927, 1),   # Charizard 223/197
}
OP_VOORRAAD = {268: "Poliwrath", 698: "Prismatic SPC"}
NIEUW_VANAF = 841
SET_KOLOM = {841: "30th"}
CATEGORIE_FIX = {891: "single", 923: "Slab", 924: "Slab"}
GRADE_FIX = {889: "PSA 9"}
# naam, code, taal, categorie, staat, cm, notitie — aantal 1 vóór het afboeken
FLIPS = [
    ("Metang", "094/086", "EN", "single", "NM", 5.0,
     "flip 10-10-2026: vrij ingevoerd in de app (#715), nooit in de Excel gestaan"),
    ("Gwynn", "117/081", "EN", "single", "NM", 24.0,
     "flip 11-10-2026: vrij ingevoerd in de app (#746), nooit in de Excel gestaan"),
]


def lees(bron: Path):
    zin = zipfile.ZipFile(bron)
    stukken = {n: zin.read(n) for n in zin.namelist()}
    zin.close()
    return stukken


def schrijf(stukken, doel: Path):
    with zipfile.ZipFile(doel, "w", zipfile.ZIP_DEFLATED) as uit:
        for n, data in stukken.items():
            uit.writestr(n, data)


def controleer(rij, cellen_voor, nr):
    refs = [REF_RE.search(c.group(0)).group(1) for c in CEL_RE.finditer(rij)]
    if len(refs) != len(set(refs)) or refs != sorted(refs, key=lambda k: (len(k), k)):
        raise RuntimeError(f"rij {nr}: celstructuur kapot: {refs}")
    if set(cellen_voor) - set(refs):
        raise RuntimeError(f"rij {nr}: cellen kwijt")


def getal(v):
    return None if v in (None, "") else v


def herreken_dashboard(stukken, kol_data):
    """Caches van het Dashboard herrekenen tegen de (aangepaste) kolominhoud.
    Twee rondes: de tweede pakt de afgeleide cellen (E5-B5, …) mee."""
    for r in range(len(kol_data["A"])):
        if r == 0 or kol_data["A"][r] in (None, ""):
            continue
        j, k, m_, n_ = (kol_data[c][r] for c in "JKMN")
        u = rijwaarden(j or 0, k or 0, getal(m_), getal(n_))
        for kol in "LOPQ":
            kol_data[kol][r] = u[kol]
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
        if oud is None or abs(float(oud.group(1) or 0) - float(w)) > 1e-6:
            veranderd.append((ref.group(1) + ref.group(2), oud and oud.group(1), round(w, 2)))
        return zet_cache(cel, float(w))

    d_xml = CEL_RE.sub(per_cel, d_xml)
    d_xml = CEL_RE.sub(per_cel, d_xml)
    stukken[DASH] = d_xml.encode("utf-8")
    print("dashboard-caches gewijzigd:", veranderd)
    return cel_cache


def kolom_data(inv):
    kol = {}
    for i, letter in enumerate(KOLOMMEN, start=1):
        kol[letter] = [getal(inv.cell(r, i).value) for r in range(1, inv.max_row + 1)]
    return kol


def zet_filter(stukken, laatste):
    wbx = stukken["xl/workbook.xml"].decode("utf-8")
    wbx = re.sub(r"<calcPr[^>]*/>", '<calcPr fullCalcOnLoad="1"/>', wbx)
    wbx = re.sub(r'(_xlnm\._FilterDatabase">)[^<]*(</definedName>)',
                 rf"\g<1>Inventaris!$A$1:$S${laatste}\g<2>", wbx)
    stukken["xl/workbook.xml"] = wbx.encode("utf-8")
    xml = stukken[SHEET].decode("utf-8")
    xml = re.sub(r'<autoFilter ref="[^"]*"/>', f'<autoFilter ref="$A$1:$S${laatste}"/>', xml)
    stukken[SHEET] = xml.encode("utf-8")


# ---------------------------------------------------------------- fase 1

def opschonen(bron: Path, doel: Path):
    wb = openpyxl.load_workbook(bron, data_only=True)
    inv = wb["Inventaris"]
    assert inv["A1"].value == "x`", inv["A1"].value
    for oud, (nieuw, extra) in SAMENVOEGEN.items():
        a, b = inv.cell(oud, 1).value, inv.cell(nieuw, 1).value
        assert a and b and a.strip().lower() == b.strip().lower(), (oud, nieuw, a, b)
        assert str(inv.cell(oud, 2).value).strip().lower() == str(inv.cell(nieuw, 2).value).strip().lower(), (oud, nieuw)
        assert (inv.cell(nieuw, 10).value or 0) == extra, (nieuw, inv.cell(nieuw, 10).value)
        for kol in (4, 5, 8):   # taal, categorie, staat
            va, vb = inv.cell(oud, kol).value, inv.cell(nieuw, kol).value
            assert (va or None) == (vb or None), (oud, nieuw, kol, va, vb)
        # promo-status: een nieuwe rij zonder vlag mag bij een gevlagde bestaande
        # (Eevee swsh087: bestaande "Sealed", nieuwe leeg — op 10-10 door Jishnu
        # als extra exemplaar bevestigd). Een nieuwe rij mét vlag moet matchen.
        pa, pb = inv.cell(oud, 6).value or None, inv.cell(nieuw, 6).value or None
        assert pb is None or pa == pb, (oud, nieuw, "promo", pa, pb)
    for r, naam in OP_VOORRAAD.items():
        assert inv.cell(r, 1).value == naam and (inv.cell(r, 10).value or 0) == 0, (r, naam)
    laatste = max(r for r in range(2, inv.max_row + 1) if inv.cell(r, 1).value not in (None, ""))
    print(f"laatste benoemde rij: {laatste}; flips komen op {laatste + 1}..{laatste + len(FLIPS)}")

    stukken = lees(bron)
    xml = stukken[SHEET].decode("utf-8")
    n2 = re.search(r'<c r="N2"[^>]*><f[^>]*>(.*?)</f>', xml, re.S).group(1)
    leeg = {n for n, _ in SAMENVOEGEN.values()}
    verslag, hersteld = [], {"L": 0, "O": 0, "P": 0, "Q": 0}

    def per_rij(m):
        nr = int(m.group(1))
        rij = origineel = m.group(0)
        cellen = _cellen(rij)
        naam = inv.cell(nr, 1).value
        if nr == 1:
            rij = _zet_cel(rij, "A1", tekst_cel(cellen.get("A"), "A1", "Naam"))
            controleer(rij, cellen, nr)
            return rij
        if naam in (None, ""):
            if nr in leeg:
                raise RuntimeError(f"rij {nr} zou leeg moeten worden maar heeft geen naam")
            return rij
        j, k = inv.cell(nr, 10).value or 0, inv.cell(nr, 11).value or 0
        if nr in leeg:
            for kol, cel in cellen.items():
                rij = rij.replace(cel, leeg_met_cache(cel), 1)
            verslag.append(f"rij {nr} {naam}: leeggemaakt (samengevoegd)")
            controleer(rij, cellen, nr)
            return rij
        if nr in SAMENVOEGEN:
            j = k = j + SAMENVOEGEN[nr][1]
            verslag.append(f"rij {nr} {naam}: aantal -> {j:g}")
        if nr in OP_VOORRAAD:
            j = k = 1
            verslag.append(f"rij {nr} {naam}: 0 -> 1 (zaterdag verkocht)")
        if nr >= NIEUW_VANAF and k != j:
            k = j
        if (j, k) != (inv.cell(nr, 10).value or 0, inv.cell(nr, 11).value or 0):
            rij = _zet_cel(rij, f"J{nr}", getal_cel(cellen.get("J"), f"J{nr}", f"{j:g}"))
            rij = _zet_cel(rij, f"K{nr}", getal_cel(cellen.get("K"), f"K{nr}", f"{k:g}"))
        if nr in SET_KOLOM:
            rij = _zet_cel(rij, f"C{nr}", tekst_cel(cellen.get("C"), f"C{nr}", SET_KOLOM[nr]))
            verslag.append(f"rij {nr} {naam}: Set = {SET_KOLOM[nr]!r}")
        if nr in CATEGORIE_FIX:
            rij = _zet_cel(rij, f"E{nr}", tekst_cel(cellen.get("E"), f"E{nr}", CATEGORIE_FIX[nr]))
            verslag.append(f"rij {nr} {naam}: categorie -> {CATEGORIE_FIX[nr]}")
        if nr in GRADE_FIX:
            rij = _zet_cel(rij, f"I{nr}", tekst_cel(cellen.get("I"), f"I{nr}", GRADE_FIX[nr]))
            verslag.append(f"rij {nr} {naam}: grading -> {GRADE_FIX[nr]}")
        m_, n_ = getal(inv.cell(nr, 13).value), getal(inv.cell(nr, 14).value)
        u = rijwaarden(j, k, m_, n_)
        cellen2 = _cellen(rij)
        for kol in "LOPQ":
            cel = cellen2.get(kol)
            if cel and "<f" in cel:
                if rij != origineel:
                    rij = _zet_cel(rij, f"{kol}{nr}", zet_cache(cel, u[kol]))
                continue
            rij = _zet_cel(rij, f"{kol}{nr}", formule_cel(cel, f"{kol}{nr}", FORMULES[kol].format(n=nr), u[kol]))
            hersteld[kol] += 1
        if rij != origineel:
            controleer(rij, cellen, nr)
        return rij

    xml = re.sub(r'<row\b[^>]*\br="(\d+)"[^>]*>.*?</row>', per_rij, xml, flags=re.S)

    # -- flip-rijen onderaan ---------------------------------------------------
    nieuwe_rijen = []
    for i, (naam, code, taal, cat, staat, cm, notitie) in enumerate(FLIPS, start=1):
        nr = laatste + i
        comp = comp_ladder(cm)
        u = rijwaarden(1, 1, cm, None if comp == "" else comp)
        n_f = re.sub(r"\$([A-Z])2\b", rf"$\g<1>{nr}", n2)
        cellen_xml = [
            tekst_cel(None, f"A{nr}", naam).replace("<c ", '<c s="53" ', 1),
            tekst_cel(None, f"B{nr}", code).replace("<c ", '<c s="53" ', 1),
            tekst_cel(None, f"D{nr}", taal).replace("<c ", '<c s="54" ', 1),
            tekst_cel(None, f"E{nr}", cat).replace("<c ", '<c s="54" ', 1),
            tekst_cel(None, f"H{nr}", staat).replace("<c ", '<c s="54" ', 1),
            f'<c r="J{nr}" s="55"><v>1</v></c>',
            f'<c r="K{nr}" s="55"><v>1</v></c>',
            formule_cel(None, f"L{nr}", FORMULES["L"].format(n=nr), u["L"]).replace("<c ", '<c s="55" ', 1),
            f'<c r="M{nr}" s="56"><v>{cm:g}</v></c>',
            (f'<c r="N{nr}" s="57"><f>{n_f}</f><v>{comp:g}</v></c>' if comp != ""
             else f'<c r="N{nr}" s="57" t="str"><f>{n_f}</f><v></v></c>'),
            formule_cel(None, f"O{nr}", FORMULES["O"].format(n=nr), u["O"]).replace("<c ", '<c s="57" ', 1),
            formule_cel(None, f"P{nr}", FORMULES["P"].format(n=nr), u["P"]).replace("<c ", '<c s="57" ', 1),
            formule_cel(None, f"Q{nr}", FORMULES["Q"].format(n=nr), u["Q"]).replace("<c ", '<c s="58" ', 1),
            tekst_cel(None, f"S{nr}", notitie).replace("<c ", '<c s="53" ', 1),
        ]
        nieuwe_rijen.append(f'<row r="{nr}">{"".join(cellen_xml)}</row>')
        verslag.append(f"rij {nr} {naam} [{code}]: flip-rij toegevoegd (cm {cm:g}, comp {comp})")
    # Google Sheets schrijft lege rijen (met opmaak) tot ver onder de lijst;
    # de flip-rij vervangt zo'n lege rij, of komt erachter als die er niet is.
    for i, rij_xml in enumerate(nieuwe_rijen, start=1):
        nr = laatste + i
        m_oud = re.search(rf'<row r="{nr}"[^>]*>.*?</row>|<row r="{nr}"[^>]*/>', xml, re.S)
        if m_oud:
            # een sjabloonrij (Categorie/Staat voorgevuld, formules, geen naam)
            # mag overschreven worden; een rij mét naam niet.
            if re.search(rf'<c r="A{nr}"[^>]*>(<v>|<is>)', m_oud.group(0)):
                raise RuntimeError(f"rij {nr} heeft al een naam")
            xml = xml.replace(m_oud.group(0), rij_xml, 1)
        else:
            vorige = re.search(rf'<row r="{nr - 1}"[^>]*>.*?</row>', xml, re.S).group(0)
            xml = xml.replace(vorige, vorige + rij_xml, 1)
    stukken[SHEET] = xml.encode("utf-8")
    print("\n".join(verslag))
    print(f"formules hersteld (vaste waarde -> rijformule): {hersteld}")

    # -- Dashboard ------------------------------------------------------------
    kol = kolom_data(inv)
    kol["A"][0] = "Naam"
    for oud, (nieuw, extra) in SAMENVOEGEN.items():
        kol["J"][oud - 1] = kol["K"][oud - 1] = (kol["J"][oud - 1] or 0) + extra
        for letter in KOLOMMEN:
            kol[letter][nieuw - 1] = None
    for r in OP_VOORRAAD:
        kol["J"][r - 1] = kol["K"][r - 1] = 1
    for r in range(NIEUW_VANAF, inv.max_row + 1):
        if kol["A"][r - 1]:
            kol["K"][r - 1] = kol["J"][r - 1]
    for r, c in CATEGORIE_FIX.items():
        kol["E"][r - 1] = c
    for naam, code, taal, cat, staat, cm, notitie in FLIPS:
        comp = comp_ladder(cm)
        for letter in KOLOMMEN:
            kol[letter].append(None)
        kol["A"][-1], kol["B"][-1], kol["D"][-1], kol["E"][-1], kol["H"][-1] = naam, code, taal, cat, staat
        kol["J"][-1] = kol["K"][-1] = 1
        kol["M"][-1], kol["N"][-1] = cm, (None if comp == "" else comp)
    herreken_dashboard(stukken, kol)
    zet_filter(stukken, laatste + len(FLIPS))
    schrijf(stukken, doel)
    print(f"geschreven: {doel}")


# ---------------------------------------------------------------- fase 2

def terugschrijven(bron: Path, doel: Path, aantallen_pad: Path, analyse_pad: Path):
    aantallen = {int(k): int(v) for k, v in json.loads(aantallen_pad.read_text()).items()}
    wb = openpyxl.load_workbook(bron, data_only=True)
    inv = wb["Inventaris"]
    stukken = lees(bron)
    xml = stukken[SHEET].decode("utf-8")
    gewijzigd = []

    def per_rij(m):
        nr = int(m.group(1))
        rij = m.group(0)
        if nr not in aantallen:
            return rij
        naam = inv.cell(nr, 1).value
        if naam in (None, ""):
            raise RuntimeError(f"rij {nr}: database kent een item, Excel heeft geen naam")
        cellen = _cellen(rij)
        j = aantallen[nr]
        if (inv.cell(nr, 10).value or 0) == j and (inv.cell(nr, 11).value or 0) == j:
            return rij
        rij = _zet_cel(rij, f"J{nr}", getal_cel(cellen.get("J"), f"J{nr}", f"{j:g}"))
        rij = _zet_cel(rij, f"K{nr}", getal_cel(cellen.get("K"), f"K{nr}", f"{j:g}"))
        u = rijwaarden(j, j, getal(inv.cell(nr, 13).value), getal(inv.cell(nr, 14).value))
        cellen2 = _cellen(rij)
        for kol in "LOPQ":
            if "<f" not in cellen2[kol]:
                raise RuntimeError(f"rij {nr}: {kol} is geen formule")
            rij = _zet_cel(rij, f"{kol}{nr}", zet_cache(cellen2[kol], u[kol]))
        gewijzigd.append((nr, naam, inv.cell(nr, 10).value, j))
        controleer(rij, cellen, nr)
        return rij

    xml = re.sub(r'<row\b[^>]*\br="(\d+)"[^>]*>.*?</row>', per_rij, xml, flags=re.S)
    stukken[SHEET] = xml.encode("utf-8")
    print(f"aantal gewijzigd op {len(gewijzigd)} rijen; grootste:",
          sorted(gewijzigd, key=lambda x: (x[2] or 0) - x[3], reverse=True)[:8])

    kol = kolom_data(inv)
    for nr, j in aantallen.items():
        kol["J"][nr - 1] = kol["K"][nr - 1] = j
    herreken_dashboard(stukken, kol)
    voeg_weekendtab_toe(stukken, json.loads(analyse_pad.read_text()))
    schrijf(stukken, doel)
    print(f"geschreven: {doel}")


def voeg_weekendtab_toe(stukken, blokken):
    """Een vierde tabblad met de weekendanalyse: platte cellen, geen formules.
    `blokken` is een lijst van [titel, [kop...], [rij...]] uit de analyse."""
    def cel(ref, w, s=None):
        st = f' s="{s}"' if s else ""
        if w is None or w == "":
            return ""
        if isinstance(w, (int, float)) and not isinstance(w, bool):
            return f'<c r="{ref}"{st}><v>{w}</v></c>'
        veilig = str(w).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f'<c r="{ref}"{st} t="inlineStr"><is><t xml:space="preserve">{veilig}</t></is></c>'

    rijen, r = [], 1
    def regel(waarden, s=None):
        nonlocal r
        cellen = "".join(cel(f"{KOLOMMEN[i]}{r}", w, s) for i, w in enumerate(waarden))
        rijen.append(f'<row r="{r}">{cellen}</row>')
        r += 1
    regel(["Weekend Rosmalen — Cardmania XXL, 10 en 11 oktober 2026 (event 27)"], s=5)
    regel([])
    for titel, kop, data in blokken:
        regel([titel], s=5)
        if kop:
            regel(kop, s=5)
        for d in data:
            regel(d)
        regel([])
    cols = '<cols><col min="1" max="1" width="44" customWidth="1"/><col min="2" max="8" width="16" customWidth="1"/></cols>'
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
             'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
             f'<sheetPr><tabColor rgb="FFF20519"/></sheetPr>{cols}<sheetData>{"".join(rijen)}</sheetData></worksheet>')
    stukken["xl/worksheets/sheet4.xml"] = sheet.encode("utf-8")
    wbx = stukken["xl/workbook.xml"].decode("utf-8")
    assert 'name="Weekend Rosmalen"' not in wbx
    wbx = wbx.replace("</sheets>", '<sheet state="visible" name="Weekend Rosmalen" sheetId="4" r:id="rId8"/></sheets>')
    stukken["xl/workbook.xml"] = wbx.encode("utf-8")
    rels = stukken["xl/_rels/workbook.xml.rels"].decode("utf-8")
    assert 'Id="rId8"' not in rels
    rels = rels.replace("</Relationships>", '<Relationship Id="rId8" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet4.xml"/></Relationships>')
    stukken["xl/_rels/workbook.xml.rels"] = rels.encode("utf-8")
    ct = stukken["[Content_Types].xml"].decode("utf-8")
    ct = ct.replace("</Types>", '<Override ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml" PartName="/xl/worksheets/sheet4.xml"/></Types>')
    stukken["[Content_Types].xml"] = ct.encode("utf-8")
    print(f"tab 'Weekend Rosmalen' toegevoegd ({r - 1} rijen)")


if __name__ == "__main__":
    if sys.argv[1] == "opschonen":
        opschonen(Path(sys.argv[2]), Path(sys.argv[3]))
    elif sys.argv[1] == "terugschrijven":
        terugschrijven(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), Path(sys.argv[5]))
    else:
        sys.exit(__doc__)
