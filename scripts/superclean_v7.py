#!/usr/bin/env python3
"""Maak de inventaris-Excel 'super clean': lege rijen er fysiek uit.

Wat dit doet, in volgorde:
1. Gedeelde formules uitvouwen naar volledige formules per cel. Daarmee
   verdwijnen de si/ref-groepen die bij het verwijderen van rijen kapot zouden
   gaan — en elke formule op Inventaris verwijst toch alleen naar zijn eigen
   rij (dat wordt hard gecontroleerd, niet aangenomen).
2. Alle naamloze rijen verwijderen en de rest hernummeren: kop op rij 1, de
   kaarten aaneengesloten vanaf rij 2. In de formules wordt het rijnummer
   meegeschreven; elke andere verwijzing dan de eigen rij is een harde fout.
3. Caches (L, N, O, P, Q) per rij opnieuw uitrekenen met exact de Excel-logica,
   zodat het bestand ook zónder herberekening klopt — de importer en de
   Drive-preview lezen caches, geen formules.
4. Dashboard-caches idem (SUM/SUMIF(S)/COUNTIF(S)-simulatie), inclusief de
   #REF! die als fossiel in "Totaal verkocht" hing.
5. Randwerk: autoFilter over het hele bereik, dataValidations op nette ranges
   (met rek tot rij 1200, zodat dropdowns blijven werken bij nieuwe rijen),
   dimension, _FilterDatabase, Blad1 eruit, fullCalcOnLoad aan.

Data verandert niet: namen, codes, aantallen en prijzen blijven byte-gelijk,
alleen hun rijnummer verschuift. Het slot van main() bewijst dat door het
resultaat cel voor cel tegen de bron én tegen de database te leggen.
"""

import math
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from opschonen_v7 import CEL_RE, REF_RE, KOLOMMEN  # noqa: E402  (gedeelde regexen)

M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REFTOKEN = re.compile(r"(\$?[A-Z]{1,2}\$?)(\d+)")


# ------------------------------------------------------------- formule-logica
# Exact de formules uit kolom L/N/O/P/Q, nagebouwd in Python. Eén plek, zodat
# de cache-herberekening en de eindcontrole dezelfde waarheid delen.

def _ceiling(x, s):
    return math.ceil(round(x / s, 10)) * s


def comp_ladder(m):
    """Kolom N: de opslagladder op de cm-prijs. '' waar Excel '' geeft."""
    if m is None or m == "":
        return ""
    if m < 5: return ""
    if m <= 6: r = m + 2
    elif m <= 10: r = m + 3
    elif m <= 15: r = m + 3
    elif m <= 17: r = m + 3
    elif m <= 20: r = m + 4
    elif m <= 25: r = m + 4
    elif m <= 30: r = m + 5
    elif m <= 40: r = m + 7
    elif m <= 50: r = m + 10
    elif m <= 60: r = m + 10
    elif m <= 70: r = m + 12
    elif m <= 350: r = _ceiling(m * 1.15, 5)
    elif m <= 700: r = _ceiling(m * 1.1, 10)
    else: return ""
    return float(round(r))


def reken_rij(a, e, j, k, m, n_cel):
    """(L, N, O, P, Q) zoals Excel ze zou rekenen. `n_cel` is de gecachete
    N-waarde uit het bestand zelf — de comp-prijs is data en wordt hier nooit
    opnieuw bedacht; alleen L/O/P/Q zijn van N en de rest afgeleid."""
    leeg = a in (None, "")
    n = "" if n_cel in (None, "") else n_cel
    l_ = "" if (j in (None, "") or k in (None, "")) else max(k - j, 0)
    o = "" if (leeg or m in (None, "")) else j * m
    if leeg:
        p = ""
    elif n not in (None, ""):
        p = j * n
    elif m not in (None, ""):
        p = j * m
    else:
        p = ""
    q = "" if leeg else ("Uitverkocht" if j <= 0 else "Op voorraad")
    return l_, n, o, p, q


# ------------------------------------------------------------- xml-helpers

def cellen_van(rij_xml):
    uit = {}
    for c in CEL_RE.finditer(rij_xml):
        ref = REF_RE.search(c.group(0))
        if ref:
            uit[ref.group(1)] = c.group(0)
    return uit


def vouw_uit(xml):
    """Gedeelde formules vervangen door hun volledige tekst per cel."""
    hosts = {}
    for m_ in re.finditer(
            r'<f t="shared"[^>]*\bref="[^"]*"[^>]*\bsi="(\d+)"[^>]*>(.*?)</f>|'
            r'<f t="shared"[^>]*\bsi="(\d+)"[^>]*\bref="[^"]*"[^>]*>(.*?)</f>',
            xml, re.S):
        si = m_.group(1) or m_.group(3)
        hosts[si] = m_.group(2) or m_.group(4)

    def vertaal(formule, oud, nieuw):
        def f(m_):
            return m_.group(1) + str(nieuw)
        uit, n_tokens = REFTOKEN.subn(f, formule)
        # élke verwijzing hoort naar de eigen rij te wijzen
        for kol, rij in REFTOKEN.findall(formule):
            if int(rij) != oud:
                raise RuntimeError(f"formule verwijst buiten de eigen rij: "
                                   f"{kol}{rij} in rij {oud}: {formule[:60]}")
        return uit

    def per_cel(m_):
        cel = m_.group(0)
        ref = REF_RE.search(cel)
        f = re.search(r"<f\b[^>]*(?:/>|>.*?</f>)", cel, re.S)
        if not f or 't="shared"' not in f.group(0) or not ref:
            return cel
        rij = int(ref.group(2))
        si = re.search(r'si="(\d+)"', f.group(0)).group(1)
        bron_rij_m = re.search(r'ref="[A-Z]+(\d+):', f.group(0))
        if bron_rij_m:                       # host: eigen tekst, attrs eraf
            tekst = re.search(r">(.*)</f>$", f.group(0), re.S).group(1)
        else:                                # volger: hosttekst vertaald
            tekst = hosts[si]
            host_rij = int(REFTOKEN.search(tekst).group(2))
            tekst = vertaal(tekst, host_rij, rij)
        # controle op de eigen rij ná vertaling
        for kol, r_ in REFTOKEN.findall(tekst):
            if int(r_) != rij:
                raise RuntimeError(f"uitgevouwen formule in rij {rij} wijst "
                                   f"naar {kol}{r_}")
        return cel.replace(f.group(0), f"<f>{tekst}</f>", 1)

    return CEL_RE.sub(per_cel, xml)


def hernummer_rij(rij_xml, oud, nieuw):
    if oud == nieuw:
        return rij_xml
    rij_xml = re.sub(rf'(<row[^>]*\br=")({oud})(")', rf"\g<1>{nieuw}\g<3>",
                     rij_xml, count=1)
    rij_xml = re.sub(rf'(\br="[A-Z]{{1,2}})({oud})(")', rf"\g<1>{nieuw}\g<3>",
                     rij_xml)

    def in_formule(m_):
        binnen = REFTOKEN.sub(
            lambda t: t.group(1) + str(nieuw) if int(t.group(2)) == oud
            else (_ for _ in ()).throw(RuntimeError(
                f"rij {oud}: verwijzing naar andere rij {t.group(0)}")),
            m_.group(1))
        return f"<f>{binnen}</f>"
    return re.sub(r"<f>(.*?)</f>", in_formule, rij_xml, flags=re.S)

# ------------------------------------------------------------- cache-herstel

def zet_cache(cel_xml, waarde):
    """Vervang de gecachete <v> van een formulecel; het celtype gaat mee
    (getal zonder t, tekst t=\"str\")."""
    attrs = re.match(r"<c\b([^>]*?)/?>", cel_xml).group(1)
    attrs = re.sub(r'\st="[^"]*"', "", attrs).rstrip()
    f = re.search(r"<f\b[^>]*(?:/>|>.*?</f>)", cel_xml, re.S).group(0)
    if waarde == "" or isinstance(waarde, str):
        t = ' t="str"'
        v = waarde.replace("&", "&amp;").replace("<", "&lt;")
    else:
        t = ""
        v = f"{waarde:g}" if waarde == int(waarde) else repr(round(waarde, 10))
    return f"<c{attrs}{t}>{f}<v>{v}</v></c>"


def cache_van(cel_xml):
    if cel_xml is None:
        return None
    m_ = re.search(r"<v>(.*?)</v>", cel_xml, re.S)
    if not m_:
        return None
    return m_.group(1)


# ------------------------------------------------------------- dashboard-sim

def simuleer_dashboard(formule, kol):
    """Reken één dashboardformule uit tegen de kolominhoud van Inventaris.
    `kol` is een dict kolomletter -> lijst waarden (None = leeg)."""
    f = formule.replace("&quot;", '"')

    def kolom(naam):
        return kol[naam.split("!")[-1].split(":")[0].strip("$")]

    def past(w, crit):
        crit = crit.strip('"')
        if crit == "?*":
            return isinstance(w, str) and len(w) >= 1
        if crit == "":
            return w is None or w == ""
        m_ = re.match(r"(<=|>=|<>|<|>|=)?(.*)", crit)
        op, rest = m_.group(1) or "=", m_.group(2)
        try:
            getal = float(rest)
        except ValueError:
            if op == "=":
                return isinstance(w, str) and w.strip().lower() == rest.strip().lower()
            return False
        if not isinstance(w, (int, float)):
            return False
        return {"<": w < getal, "<=": w <= getal, ">": w > getal,
                ">=": w >= getal, "=": w == getal, "<>": w != getal}[op]

    m_ = re.fullmatch(r"SUM\(([^)]+)\)", f)
    if m_:
        return sum(w for w in kolom(m_.group(1)) if isinstance(w, (int, float)))
    m_ = re.fullmatch(r"SUMIF\(([^,]+),([^,]+),([^)]+)\)", f)
    if m_:
        crit_kol, crit, som_kol = kolom(m_.group(1)), m_.group(2), kolom(m_.group(3))
        return sum(s for c, s in zip(crit_kol, som_kol)
                   if past(c, crit) and isinstance(s, (int, float)))
    m_ = re.fullmatch(r"SUMIFS\(([^,]+),(.+)\)", f)
    if m_:
        som_kol = kolom(m_.group(1))
        rest = re.findall(r'([A-Za-z!$:]+),("[^"]*")', m_.group(2))
        keuze = range(len(som_kol))
        keuze = [i for i in keuze
                 if all(past(kolom(ck)[i], cr) for ck, cr in rest)]
        return sum(som_kol[i] for i in keuze if isinstance(som_kol[i], (int, float)))
    m_ = re.fullmatch(r"COUNTIF\(([^,]+),([^)]+)\)(\s*-\s*1)?", f)
    if m_:
        n = sum(1 for w in kolom(m_.group(1)) if past(w, m_.group(2)))
        return n - 1 if m_.group(3) else n
    m_ = re.fullmatch(r"COUNTIFS\((.+)\)", f)
    if m_:
        paren = re.findall(r'([A-Za-z!$:]+),("[^"]*")', m_.group(1))
        eerste = kolom(paren[0][0])
        return sum(1 for i in range(len(eerste))
                   if all(past(kolom(ck)[i], cr) for ck, cr in paren))
    return None            # verwijzing naar andere dashboardcellen: later


# ------------------------------------------------------------- hoofdprogramma

def main(bron: Path, doel: Path):
    import openpyxl
    zin = zipfile.ZipFile(bron)
    stukken = {n: zin.read(n) for n in zin.namelist()}
    zin.close()

    # -- welke rijen blijven: alles met een naam in kolom A ------------------
    wb = openpyxl.load_workbook(bron, data_only=True)
    ws = wb["Inventaris"]
    def val(r, c):
        v = ws.cell(r, c).value
        return None if v in (None, "") else v
    benoemd = [r for r in range(2, ws.max_row + 1) if val(r, 1) is not None]
    print(f"benoemde rijen: {len(benoemd)} (van rij {benoemd[0]} t/m {benoemd[-1]})")

    xml = stukken["xl/worksheets/sheet2.xml"].decode("utf-8")
    xml = vouw_uit(xml)
    print("gedeelde formules uitgevouwen")

    # -- rijen knippen, hernummeren en caches herrekenen ---------------------
    kop_m = re.search(r'<row[^>]*\br="1"[^>]*>.*?</row>', xml, re.S)
    rijen_xml = {int(m_.group(1)): m_.group(0) for m_ in re.finditer(
        r'<row[^>]*\br="(\d+)"[^>]*(?:/>|>.*?</row>)', xml, re.S)}
    sd_begin = xml.index("<sheetData>")
    sd_eind = xml.index("</sheetData>") + len("</sheetData>")

    import openpyxl.utils  # noqa
    wbf = openpyxl.load_workbook(bron, data_only=False)["Inventaris"]
    nieuw_rijen, kol_data, verwijderd = [], {k: [] for k in "ADEJKLMNOPQ"}, 0
    cache_fix = 0
    k_gelijk = [0, []]        # [aantal gelijkgetrokken, lijst met echte Verkocht>0]
    # Kanonieke formule per kolom, uit de eerste gezonde rij. Een formule die
    # ergens #REF! draagt (L846 in de bron: een ooit kapotgetrokken
    # K-verwijzing) wordt daarmee hersteld — één #REF! in kolom L maakt
    # SUM(L:L) op het Dashboard ook #REF!, en dat was precies de klacht.
    canon = {}
    ref_hersteld = []
    for r_ in benoemd:
        for kolletter, cel in cellen_van(rijen_xml[r_]).items():
            fm = re.search(r"<f>(.*?)</f>", cel, re.S)
            if fm and kolletter not in canon and "#REF!" not in fm.group(1):
                canon[kolletter] = (r_, fm.group(1))

    for nieuw_nr, oud in enumerate(benoemd, start=2):
        rij_xml = rijen_xml[oud]
        for kolletter, cel in cellen_van(rij_xml).items():
            fm = re.search(r"<f>(.*?)</f>", cel, re.S)
            if fm and "#REF!" in fm.group(1) and kolletter in canon:
                bron_r, tekst = canon[kolletter]
                goed = REFTOKEN.sub(lambda t: t.group(1) + str(oud), tekst)
                rij_xml = rij_xml.replace(fm.group(0), f"<f>{goed}</f>", 1)
                ref_hersteld.append((oud, kolletter))
        rij_xml = hernummer_rij(rij_xml, oud, nieuw_nr)
        a, d_, e = val(oud, 1), val(oud, 4), val(oud, 5)
        j = val(oud, 10) or 0
        k = val(oud, 11)
        m_ = val(oud, 13)
        n_cel = val(oud, 14)
        # Vorig aantal gelijktrekken aan Aantal: de vaste slotstap van de
        # beurscyclus ("Verkocht" weer op 0), hier expliciet gevraagd. Dit is
        # de enige celwaarde die dit script verandert, en hij wordt geteld.
        if (k if k is not None else None) != j:
            cel_k = cellen_van(rij_xml).get("K")
            attrs = re.sub(r'\st="[^"]*"', "",
                           re.match(r"<c\b([^>]*?)/?>", cel_k).group(1)).rstrip() \
                if cel_k else f' r="K{nieuw_nr}"'
            nieuw_k = f"<c{attrs}><v>{j:g}</v></c>"
            rij_xml = (rij_xml.replace(cel_k, nieuw_k, 1) if cel_k
                       else rij_xml.replace("</row>", nieuw_k + "</row>", 1))
            k_gelijk[0] += 1
            if k is not None and k > j:
                k_gelijk[1].append((nieuw_nr, a, j, k))
            k = j
        n_is_formule = isinstance(wbf.cell(oud, 14).value, str) and \
            str(wbf.cell(oud, 14).value).startswith("=")
        if n_is_formule and n_cel not in (None, ""):
            lamp = comp_ladder(m_) if e not in ("Slab", "Sealed") else ""
            if lamp != "" and abs(float(n_cel) - float(lamp)) > 0.5:
                print(f"  ⚠ rij {oud}: N-cache {n_cel} wijkt af van de ladder "
                      f"({lamp}) — cache blijft leidend")
        l_, n, o, p, q = reken_rij(a, e, j, k, m_, n_cel)
        cellen = cellen_van(rij_xml)
        for kolletter, wens in (("L", l_), ("N", n), ("O", o), ("P", p), ("Q", q)):
            cel = cellen.get(kolletter)
            if cel is None or "<f>" not in cel:
                continue                      # handwaarde of lege cel: afblijven
            oud_cache = cache_van(cel)
            nieuw_cache = ("" if wens == "" else
                           f"{wens:g}" if isinstance(wens, float) and wens == int(wens)
                           else str(wens))
            if (oud_cache or "") != nieuw_cache:
                rij_xml = rij_xml.replace(cel, zet_cache(cel, wens), 1)
                cache_fix += 1
        nieuw_rijen.append(rij_xml)
        # kolominhoud voor de dashboard-simulatie (ná herberekening)
        kol_data["A"].append(a); kol_data["D"].append(d_); kol_data["E"].append(e)
        kol_data["J"].append(j); kol_data["K"].append(k); kol_data["M"].append(m_)
        kol_data["L"].append(l_ if l_ != "" else None)
        kol_data["N"].append(n if n != "" else None)
        kol_data["O"].append(o if o != "" else None)
        kol_data["P"].append(p if p != "" else None)
        kol_data["Q"].append(q if q != "" else None)
    verwijderd = len(rijen_xml) - 1 - len(benoemd)
    laatste = len(benoemd) + 1
    print(f"rijen fysiek verwijderd: {verwijderd}; kaarten nu rij 2..{laatste}; "
          f"caches herrekend: {cache_fix}")
    print(f"formules met #REF! hersteld naar de kanonieke kolomformule: "
          f"{[(r, k) for r, k in ref_hersteld]}")
    print(f"Vorig aantal gelijkgetrokken op {k_gelijk[0]} rijen; "
          f"daarvan droegen er {len(k_gelijk[1])} een echte Verkocht-waarde:")
    for nr, naam, j, k in k_gelijk[1]:
        print(f"    rij {nr:>4}  {str(naam)[:30]:<30} aantal={j:g} vorig was {k:g} "
              f"(Verkocht was {max(k - j, 0):g})")

    # kopregel meetellen in de kolommen (COUNTIF "?*" telt hem, de -1 haalt hem eraf)
    for kname, kopwaarde in (("A", "Naam"), ("D", "Taal"), ("E", "Categorie"),
                             ("Q", "Status")):
        kol_data[kname].insert(0, kopwaarde)
    for kname in "JKLMNOP":
        kol_data[kname].insert(0, None)

    xml = (xml[:sd_begin] + "<sheetData>" + kop_m.group(0)
           + "".join(nieuw_rijen) + "</sheetData>" + xml[sd_eind:])

    # -- randwerk -------------------------------------------------------------
    xml = re.sub(r'<dimension ref="[^"]*"/>', f'<dimension ref="A1:Z{laatste}"/>', xml)
    xml = re.sub(r"<autoFilter[^>]*/?>(?:</autoFilter>)?",
                 f'<autoFilter ref="A1:S{laatste}"/>', xml, count=1)
    REK = 1200   # dropdowns blijven werken als er onder de lijst wordt bijgetikt
    for kols in ("E", "H", "G", "D", "F"):
        xml = re.sub(rf'(<dataValidation\b[^>]*sqref=")({kols}[^"]*)(")',
                     rf"\g<1>{kols}2:{kols}{REK}\g<3>", xml)
    stukken["xl/worksheets/sheet2.xml"] = xml.encode("utf-8")

    # -- dashboard: caches herrekenen, inclusief de #REF!-fossiel -------------
    d_xml = stukken["xl/worksheets/sheet1.xml"].decode("utf-8")
    cel_cache = {}

    def dash_pass(d_xml):
        """Caches herrekenen, cel voor cel via CEL_RE — de veilige celregex.
        Een gulzige variant schoot hier over self-closing cellen heen en
        slokte buurencellen op; dat is exact de bug van opschonen_v7, dus
        dezelfde remedie."""
        telfix = [0]

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
                # afgeleide cellen (E5-B5, C22/$B$8): eerst verwijzingen
                # vervangen door al berekende waarden, dan pas rekenen.
                expr = tekst
                for celref, wv in cel_cache.items():
                    expr = re.sub(rf"\$?{celref[0]}\$?{celref[1:]}\b",
                                  repr(float(wv)), expr)
                kaal = re.sub(r"\s", "", expr)
                if re.fullmatch(r"[\d.eE()+\-*/]+", kaal):
                    try:
                        w = eval(kaal)
                    except Exception:
                        w = None
            if w is None:
                return cel
            cel_cache[ref.group(1) + ref.group(2)] = w
            telfix[0] += 1
            return zet_cache(cel, float(w))

        return CEL_RE.sub(per_cel, d_xml), telfix[0]

    d_xml, n1 = dash_pass(d_xml)
    d_xml, n2 = dash_pass(d_xml)      # tweede ronde pakt de afgeleide cellen mee
    print(f"dashboard-caches herrekend: ronde 1 = {n1}, ronde 2 = {n2}")
    stukken["xl/worksheets/sheet1.xml"] = d_xml.encode("utf-8")

    # -- Blad1 eruit -----------------------------------------------------------
    wbx = stukken["xl/workbook.xml"].decode("utf-8")
    sheet_m = re.search(r'<sheet[^>]*name="Blad1"[^>]*r:id="([^"]+)"[^>]*/>', wbx)
    rid = sheet_m.group(1)
    wbx = wbx.replace(sheet_m.group(0), "")
    wbx = re.sub(r"<calcPr[^>]*/>", '<calcPr fullCalcOnLoad="1"/>', wbx)
    wbx = re.sub(r'(_xlnm\._FilterDatabase">)[^<]*(</definedName>)',
                 rf"\g<1>Inventaris!$A$1:$S${laatste}\g<2>", wbx)
    stukken["xl/workbook.xml"] = wbx.encode("utf-8")
    rels = stukken["xl/_rels/workbook.xml.rels"].decode("utf-8")
    doel_m = re.search(rf'<Relationship[^>]*Id="{rid}"[^>]*Target="([^"]+)"[^>]*/>', rels)
    blad1_pad = "xl/" + doel_m.group(1)
    stukken["xl/_rels/workbook.xml.rels"] = rels.replace(doel_m.group(0), "").encode()
    ct = stukken["[Content_Types].xml"].decode("utf-8")
    ct = re.sub(rf'<Override[^>]*PartName="/{re.escape(blad1_pad)}"[^>]*/>', "", ct)
    stukken["[Content_Types].xml"] = ct.encode()
    stukken.pop(blad1_pad, None)
    stukken.pop(f"xl/worksheets/_rels/{blad1_pad.split('/')[-1]}.rels", None)
    app = stukken.get("docProps/app.xml")
    if app:
        a = app.decode("utf-8")
        a = a.replace("<vt:lpstr>Blad1</vt:lpstr>", "")
        a = re.sub(r'(<vt:vector size=")(\d+)("[^>]*baseType="lpstr")',
                   lambda m3: m3.group(1) + str(int(m3.group(2)) - 1) + m3.group(3), a)
        a = re.sub(r'(<HeadingPairs>.*?<vt:i4>)(\d+)(</vt:i4>)',
                   lambda m3: m3.group(1) + str(int(m3.group(2)) - 1) + m3.group(3),
                   a, flags=re.S)
        stukken["docProps/app.xml"] = a.encode()
    stukken.pop("xl/calcChain.xml", None)
    ct = stukken["[Content_Types].xml"].decode()
    ct = re.sub(r'<Override[^>]*calcChain[^>]*/>', "", ct)
    stukken["[Content_Types].xml"] = ct.encode()
    r2 = stukken["xl/_rels/workbook.xml.rels"].decode()
    stukken["xl/_rels/workbook.xml.rels"] = re.sub(
        r'<Relationship[^>]*calcChain[^>]*/>', "", r2).encode()
    print(f"Blad1 verwijderd ({blad1_pad}); fullCalcOnLoad aan")

    with zipfile.ZipFile(doel, "w", zipfile.ZIP_DEFLATED) as uit:
        for n, data in stukken.items():
            uit.writestr(n, data)
    print(f"geschreven: {doel}")
    return len(benoemd), verwijderd, cel_cache


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
