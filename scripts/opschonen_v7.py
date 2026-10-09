#!/usr/bin/env python3
"""Opschonen van TC_Inventaris_v7.xlsx op XML-niveau.

Drie bewerkingen, allemaal op het tabblad Inventaris:

1. **Rij leegmaken** — de naam en alle losse waarden eruit, de formules blijven
   staan. Leegmaken en niet verwijderen, want een verwijderde rij verschuift de
   gedeelde formulebereiken (`<f t="shared" ref="L2:L343">`) en dat sloopt het
   Dashboard.
2. **Aantal zetten** — kolom J op een vaste waarde.
3. **Notitie schrijven** — kolom S, als `inlineStr`. Dat scheelt gerommel met
   `sharedStrings.xml` en de tellers `count`/`uniqueCount` daarin; Excel en
   openpyxl lezen het allebei gewoon.

⚠️ Waarom geen openpyxl-save: dat wist alle gecachete formulewaarden, en zonder
LibreOffice (staat niet op deze machine) krijgt niets ze terug — de importer
leest dan overal `comp_prijs = NULL`. Daarom `zipfile` + gerichte vervanging:
formules, caches, opmaak en het Dashboard-tabblad blijven intact.

Bij het leegmaken van een formulecel blijft `<f>` staan en gaat alleen de
gecachete `<v>` eruit. De formules beginnen met `IF($A="";"";…)`, dus zodra de
naam weg is leveren ze vanzelf "" op — en `fullCalcOnLoad` laat Excel dat
uitrekenen zodra het bestand opengaat.
"""

import re
import shutil
import zipfile
from pathlib import Path

SHEET = "xl/worksheets/sheet2.xml"      # tabblad Inventaris
KOLOMMEN = [chr(c) for c in range(ord("A"), ord("S") + 1)]


def _cellen(rij_xml: str) -> dict:
    """{kolomletter: volledige <c .../>-tekst} voor één rij."""
    uit = {}
    for m in re.finditer(r'<c\b[^>]*\br="([A-Z]+)(\d+)"[^>]*(?:/>|>.*?</c>)',
                         rij_xml, re.S):
        uit[m.group(1)] = m.group(0)
    return uit


def _attrs_zonder_t(cel: str) -> str:
    """De attributen van een <c>, zonder `t` — het type hoort bij de waarde die
    we eruit halen, en een achtergebleven t="s" zonder <v> maakt Excel boos."""
    kop = re.match(r"<c\b([^>]*?)/?>", cel).group(1)
    return re.sub(r'\st="[^"]*"', "", kop).rstrip()


def leeg_cel(cel: str) -> str:
    """Waarde eruit. Heeft de cel een formule, dan blijft die staan."""
    f = re.search(r"<f\b[^>]*(?:/>|>.*?</f>)", cel, re.S)
    attrs = _attrs_zonder_t(cel)
    return f"<c{attrs}>{f.group(0)}</c>" if f else f"<c{attrs}/>"


def getal_cel(cel: str | None, ref: str, waarde) -> str:
    attrs = _attrs_zonder_t(cel) if cel else f' r="{ref}"'
    return f"<c{attrs}><v>{waarde}</v></c>"


def tekst_cel(cel: str | None, ref: str, tekst: str) -> str:
    veilig = (tekst.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    attrs = _attrs_zonder_t(cel) if cel else f' r="{ref}"'
    return f'<c{attrs} t="inlineStr"><is><t xml:space="preserve">{veilig}</t></is></c>'


def _zet_cel(rij_xml: str, ref: str, nieuw: str) -> str:
    """Vervang de cel, of voeg 'm op de juiste plek in de kolomvolgorde toe."""
    kol = re.match(r"([A-Z]+)", ref).group(1)
    bestaand = _cellen(rij_xml)
    if kol in bestaand:
        return rij_xml.replace(bestaand[kol], nieuw, 1)
    later = [k for k in bestaand
             if KOLOMMEN.index(k) > KOLOMMEN.index(kol)] if kol in KOLOMMEN else []
    if later:
        eerste = min(later, key=KOLOMMEN.index)
        return rij_xml.replace(bestaand[eerste], nieuw + bestaand[eerste], 1)
    return rij_xml.replace("</row>", nieuw + "</row>", 1)


def bewerk(bron: Path, doel: Path, *, leeg: set, aantallen: dict, notities: dict):
    """Pas de drie bewerkingen toe en schrijf een nieuw bestand."""
    zin = zipfile.ZipFile(bron)
    xml = zin.read(SHEET).decode("utf-8")
    geraakt = {"leeg": 0, "aantal": 0, "notitie": 0}

    def per_rij(m):
        rij_xml, nr = m.group(0), int(m.group(1))
        if nr in leeg:
            for kol, cel in _cellen(rij_xml).items():
                if kol in KOLOMMEN:
                    rij_xml = rij_xml.replace(cel, leeg_cel(cel), 1)
            geraakt["leeg"] += 1
            return rij_xml
        if nr in aantallen:
            rij_xml = _zet_cel(rij_xml, f"J{nr}",
                               getal_cel(_cellen(rij_xml).get("J"), f"J{nr}",
                                         aantallen[nr]))
            geraakt["aantal"] += 1
        if nr in notities:
            rij_xml = _zet_cel(rij_xml, f"S{nr}",
                               tekst_cel(_cellen(rij_xml).get("S"), f"S{nr}",
                                         notities[nr]))
            geraakt["notitie"] += 1
        return rij_xml

    xml = re.sub(r'<row\b[^>]*\br="(\d+)"[^>]*>.*?</row>', per_rij, xml, flags=re.S)

    # Excel het Dashboard laten herrekenen zodra het bestand opengaat.
    wb = zin.read("xl/workbook.xml").decode("utf-8")
    if "<calcPr" in wb:
        wb = re.sub(r"<calcPr[^>]*/>", '<calcPr fullCalcOnLoad="1"/>', wb)
    else:
        wb = wb.replace("</workbook>", '<calcPr fullCalcOnLoad="1"/></workbook>')

    doel.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(doel, "w", zipfile.ZIP_DEFLATED) as uit:
        for item in zin.infolist():
            if item.filename == SHEET:
                uit.writestr(item, xml)
            elif item.filename == "xl/workbook.xml":
                uit.writestr(item, wb)
            elif item.filename == "xl/calcChain.xml":
                # De calcChain verwijst naar cellen die we net hebben geleegd.
                # Excel bouwt 'm opnieuw op als hij ontbreekt; laten staan geeft
                # een reparatiemelding.
                continue
            else:
                uit.writestr(item, zin.read(item.filename))
    zin.close()
    return geraakt


def verwijder_calcchain_ref(doel: Path):
    """De [Content_Types] en de rels mogen niet naar een weggelaten calcChain
    blijven wijzen."""
    zin = zipfile.ZipFile(doel)
    stukken = {n: zin.read(n) for n in zin.namelist()}
    zin.close()
    ct = stukken["[Content_Types].xml"].decode()
    ct = re.sub(r'<Override[^>]*calcChain[^>]*/>', "", ct)
    stukken["[Content_Types].xml"] = ct.encode()
    r = stukken["xl/_rels/workbook.xml.rels"].decode()
    r = re.sub(r'<Relationship[^>]*calcChain[^>]*/>', "", r)
    stukken["xl/_rels/workbook.xml.rels"] = r.encode()
    with zipfile.ZipFile(doel, "w", zipfile.ZIP_DEFLATED) as uit:
        for n, data in stukken.items():
            uit.writestr(n, data)
