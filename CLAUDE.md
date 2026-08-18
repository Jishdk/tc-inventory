# TC Inventory — Claude Code Context

Zie ook: `../CLAUDE.md` voor algemene Trader Collective context en brand.

## Wat doet deze map

Pipeline voor inventaris- en beursadministratie:
1. WhatsApp-beursexports parsen → transactie-CSV (`scripts/parse_whatsapp.py`).
2. Inventaris koppelen aan de Cardmarket-catalogus (identiteit, `scripts/enrich_inventaris.py`).
3. Alles in een Supabase-database + Streamlit-dashboard (`src/`, `app.py`).
4. Beursfoto's koppelen aan inventaris-items (match-voorstellen, `src/match_foto.py`).

## Database (Supabase, POC)

- Connectie via `SUPABASE_DB_URL` in `inventory/.env` (niet in git).
- **Let op:** de Supabase *direct connection* (`db.<ref>.supabase.co`) is IPv6-only en
  onbereikbaar vanaf deze WSL-machine. Gebruik de **session pooler**
  (`aws-0-<regio>.pooler.supabase.com:5432`, IPv4). `src/db.py` parst de DSN zelf
  (wachtwoord met speciale tekens) en dwingt `sslmode=require` af.
- Migraties: `python src/migrate.py` (draait `migrations/*.sql`, idempotent).
- Tabellen: `events`, `items` (720 rijen, v7-inventaris), `transactions` (246: 130 uit
  juli + 116 van Cardmaniacs), `price_points` (leeg, voor latere prijs-sync),
  `match_voorstellen` (Sessie A).
- `transactions.code` is toegevoegd in `003_transactions_code.sql` (13-08-2026) voor de
  vrije invoer van de beurs-app; bestaande rijen houden `NULL`.
- `004_items_v6_kolommen.sql` (15-08-2026) voegt `vorig_aantal`, `verkocht`, `status` en
  `vintage_modern` toe aan `items`. Voorraad-/verkoopwaarde slaan we niet op: formules.
- **Let op bij vervangen van `items`:** `transactions.item_id`, `price_points.item_id` én
  `match_voorstellen.voorgesteld_item_id` zijn foreign keys. Die laatste stond op 65 rijen
  gevuld en blokkeert een DELETE; de importer zet 'm op NULL (voorstelregels blijven).
- Imports: `src/import_inventory.py` (v5), `src/import_transactions.py` — idempotent via
  `bron_rij` (positie in bronbestand; er zijn dubbele naam+code-combinaties).
- **`src/import_inventory_v2.py`** — leest het v6-bestand (`inventaris_datav2.xlsx`) en
  *vervangt* de items-tabel in één transactie (`--dry-run` toont eerst wat er gebeurt).
  Prijzen zijn formules → `data_only=True`; codes als getal (46.0) worden "46"; rijen
  zonder naam en `[ place holder ]`-regels worden overgeslagen; `aantal` leeg = 0.
  Verrijking (`officiele_naam`, `set_code`, `cardmarket_id`, `match_status`) wordt op
  naam+code uit de oude tabel overgenomen — die zit niet in het Excel-bestand.
- Dashboard: `streamlit run app.py` → http://localhost:8501 (3 tabs: Overzicht,
  Inventaris, Transacties). Validatie tegen referentie verkoop €16.768 / inkoop €3.619.

## Beurs-app (snelle invoer op de telefoon)

`beurs_app.py` — losstaande Streamlit-app waarmee het team tijdens een beurs live
verkopen/inkopen vastlegt. Event: **Cardmaniacs Nijmegen 15-16 augustus 2026**
(`EVENT_NAAM` bovenin; beide dagen hangen aan dit ene event, `datum` = dag van invoer).

- `streamlit run beurs_app.py` (poort 8501/8502). Rookproef zonder DB:
  `python tests/test_beurs_app.py` — draait de app headless via `streamlit.testing`
  tegen een SQLite-schaduwschema (86 checks).
- Eén gedeelde gebruiker: `afzender` staat vast op `"TC"` (constante `AFZENDER`).
  Geen naamkeuze — bewust weggehaald, scheelt een scherm per invoer.
- UI is mobiel-first en leunt op Streamlit's `st-key-<key>`-classes voor CSS
  (zoekresultaten `pick_<id>`/`add_<id>`, `bedrag`, `vastleggen`). De tweede regel in
  een resultaatknop komt van `\n` + `white-space: pre-line`; het grijze deel is
  `:gray[…]`. Streamlit stapelt kolommen onder telefoonbreedte — daarom staat er een
  `flex-wrap: nowrap` op `stHorizontalBlock` (anders valt de rekenhulp uit elkaar).
- Schrijft **alleen** naar `transactions`; nooit afboeken op `items` (bewuste scope-grens).
- Vangnet "vrij invoeren": `item_id=NULL`, `flag='vrij ingevoerd'`, `ruwe_tekst` = getypte
  naam, plus een **los code-veld** (`173/195`, `swsh260`) dat in `transactions.code` landt.
  Zonder item_id is die code het enige haakje om de regel later alsnog te koppelen; leeg
  laten mag. Bij een gezochte kaart vullen we `code` met de set-code van het item.
- **Prijs in de app** = `comp_prijs`, en waar die leeg is `prijs_cm`, met " cm" achter het
  bedrag. Nodig sinds de v6-inventaris: alle 64 slabs en een deel van sealed/singles
  hebben alléén een cm-prijs (120 items) en toonden anders "€ ?" bij juist de dure
  voorraad. Voorinvullen bij verkoop en de %-rekenhulp gebruiken dezelfde werkprijs.
- **Dagtotaal** bovenaan: som per `type` over `datum = vandaag` binnen het event
  ("€X in · €Y uit · netto €Z"). Ververst na elke eigen invoer (`tx_versie` breekt de
  cache open); invoer van een collega komt binnen de TTL van 20 s mee.
- **Inkoop** kent twee manieren (`inkoop_modus`), verkoop blijft ongewijzigd snel:
  - *Per kaart* — als de kaart een `comp_prijs` heeft: snelknoppen 70/75/80 % plus een
    vrij %-veld. Bedrag = comp × % afgerond op hele euro's (half naar boven), daarna
    gewoon te overschrijven. Geen comp → geen rekenhulp, zelf intikken.
  - *Totaal* — kaarten (uit de database of vrij getypt, met optionele code) op een
    stapeltje: teller "Stapel: N kaarten", elke regel is zelf de verwijderknop, en één
    totaalbedrag onderaan. Dat wordt **één** transactie: `item_id=NULL`, `flag='stapel'`,
    `ruwe_tekst = "Stapel N kaarten: naam [code] · naam · …"`. Losse prijzen bewaren
    we niet — het totaal is leidend.
- **Undo**: verwijdert na bevestiging de laatste transactie die *deze sessie* zelf heeft
  weggeschreven (`mijn_invoeren` houdt de eigen insert-id's bij). Nooit die van een
  collega, ook niet als die van hen recenter is.
- **Kleur per modus**: VASTLEGGEN en de actieve toggle zijn groen bij verkoop en TC-rood
  bij inkoop (`MODUS_KLEUR`). De modus staat al in `session_state` vóór het script draait,
  dus die CSS zit in hetzelfde `<style>`-blok als `BASIS_CSS` — één element, geen extra
  witruimte. Een uitgeschakelde knop blijft grijs (`:not(:disabled)`).
- Thema staat in `.streamlit/config.toml` (light vastgezet, TC-rood als `primaryColor`).
  Streamlit leest dat uit de werkmap van de app — op Streamlit Cloud is dat de repo-root.
- Schrijven kent géén automatische retry (dubbele boeking is erger dan een foutmelding);
  bij een fout blijft de invoer op het scherm staan. Lezen retryt één keer.
- Optionele pincode via secret/env `TC_BEURS_PIN` — zetten zodra de app publiek staat.

## Aansluiting met het Dashboard-tabblad (v6)

Gecontroleerd op 15-08-2026: **alle geldbedragen sluiten tot op de euro aan** op het
Dashboard in `inventaris_datav2.xlsx` — voorraadwaarde €102.748, verkoopwaarde €109.959,
marge €7.211, 1231 stuks, 22 verkocht, en de verdeling per categorie en taal.

Wat je moet weten om die getallen te lezen:

- **Verkoopwaarde** (kolom P) = `aantal × comp prijs`, en waar comp leeg is
  `aantal × cm prijs`. Precies dezelfde terugval als in de app. Puur de echte
  comp-prijzen zijn €75.680; de rest (€34.279) komt van rijen zonder comp.
- **Comp prijs** (kolom N) is een opslagladder op de cm-prijs (tot €350:
  `CEILING(cm × 1,15; 5)`), maar begint met `IF(OR(E="Slab"; E="Sealed"; M=""); "")`.
  Dus: **slabs worden nooit gecompt** (alle 64 hebben nog de formule), bij Sealed zijn er
  28 met de hand ingevuld, en singles onder €5 krijgen bewust geen comp.
- **Unieke items: dashboard 601, database 599.** Het dashboard telt
  `COUNTIF(A:A;"?*")-1`: dat telt de 3 `[ place holder ]`-regels mee (tekst) en mist de
  rij waar de naam per ongeluk het getal `46` is (`"?*"` matcht geen getallen). De import
  doet het omgekeerd. 601 − 3 + 1 = 599. Geen van die 5 rijen heeft een prijs, dus de
  bedragen blijven gelijk.
- **"Nog niet gecompt": dashboard 44, database 41** — zelfde 3 placeholders.
- ⏳ Open: bronrij 603 heeft als naam het getal `46` (code 118/086, cm €46, comp €56,
  uitverkocht). Echte kaartnaam onbekend; staat nu als "46" in de zoeklijst van de app.

## Kanaal-mapping (transactions)

Bron kent 2 WhatsApp-kanalen. `sales` → kanaal `verkoop`, `inkoop` → `inkoop`. Zo klopt
"som per kanaal" met de referenties (kanaal verkoop = €16.725, inkoop = €3.619; de €43
gat naar €16.768 zijn sticker-verkopen zonder leesbaar bedrag = flag CHECK BEDRAG).
De aard per regel (verkoop/inkoop/trade) staat in kolom `type`.

## Bestanden (`../data/`)

| Bestand | Beschrijving |
|---|---|
Opgeruimd op 17-08-2026: alles wat verouderd was staat in `../_archief/opruiming_2026-08-17/`
(niets verwijderd). Wat daar heen ging: `TC_Inventaris_v5.xlsx`, `inventaris_datav2.xlsx`
(v6), `inventaris_verrijkt.csv` (1e pass), `match_rapport.txt`, `twijfel_handmatig.csv`,
`_transacties_v2.csv`, de juli-zips `inkoop27072026.zip` / `sales27072026.zip`, en
`dubbel_van_archief/{_sales,_inkoop}` — die twee mappen waren byte-voor-byte dezelfde
138/108 bestanden als `_archief/_sales` en `_archief/_inkoop`.

| Bestand | Beschrijving |
|---|---|
| `TC_Inventaris_v7.xlsx` | **Actuele bron-inventaris v7** (sheet "Inventaris", 720 items) |
| `salestrade1516augustus2026.zip` + map | WhatsApp-export Sales & trades t/m 16-08 (308 media) |
| `inkoop1516augustus2026.zip` + map | WhatsApp-export Inkoop t/m 16-08 (137 media) |
| `backup/transactions_voor_opschoning.csv` | Transacties vóór de opschoning van 16-08 (246) |
| `backup/items_voor_opschoning.csv` | Items vóór de v7-import (599) |
| `dubbelingen_rapport.txt` | Voorstel dubbele beursverkopen — **verouderd**, kent alleen de 116 regels van 15-08 |
| `niet_gekoppelde_sales.csv` | Beursregels zonder `item_id`, handmatig te koppelen |
| `_sales_v2` / `_inkoop_v2` | Juli-media; `transactions.media_file` wijst hierheen — **niet verplaatsen** |
| `inventaris_verrijkt_v2.csv` | Verrijkte identiteit (na 2e pass): 367 zeker, 81 twijfel, 72 jp, 26 geen_match_naam, 18 zeker_prijs, 8 geen_match |
| `twijfel_singles.csv` / `twijfel_later.csv` | Handmatig na te lopen twijfelgevallen (15 singles-met-code / 66 rest) |
| `match_voorstellen_rapport.txt` | Meetrapport Sessie A (foto → match) |
| `_cmapi_rapid_cache.json` | Cache API-searches (bewaren) |
| `whatsapp-chats/` | Leeg; ooit bedoeld als losse chatmap |

De augustus-exports zijn geen superset van de juli-media: 84 bestanden uit `_sales_v2` en
42 uit `_inkoop_v2` zitten er niet in (WhatsApp exporteert maar een venster terug). Beide
paren mappen blijven dus nodig.

`inventory/data/`: transactie-CSV's (`_clean` is definitief) + `_vision_findings.json`
(121 handmatig/visueel gelezen beursfoto's, keyed op bestandsnaam — input voor match_foto).

## API (identiteit + prijzen)

RapidAPI "Cardmarket API TCG" (tcggopro), host `cardmarket-api-tcg.p.rapidapi.com`,
key `CMAPI_KEY` in `../.env` (Pro-tier 3.000/dag). Geen NL-prijsveld; wel
`lowest_near_mint` + DE/FR/ES/IT + `30d_average`. (`CMAPI_LIVE_KEY` = cardmarketapi.com
trial, niet meer gebruikt.)

## Beurscyclus — de vaste procedure na elke beurs

De Excel is de waarheid, de database is werkgeheugen. Een beurs loopt daarom in een
rondje: Excel erin → verkopen eraf → resultaat terug in de Excel. Na die laatste stap
is de Excel de eindstand van deze beurs én de beginstand van de volgende, en klopt hij
weer precies met de database.

```
   TC_Inventaris_v7.xlsx  (beginstand)
            |  1. importeren
            v
        items-tabel  ──── 2. beurs-app schrijft naar transactions
            |  3. markeren (dubbel / workaround / stuks)
            |  4. afboeken
            v
     items = beginstand - verkopen
            |  5. terugschrijven
            v
   TC_Inventaris_v7.xlsx  (eindstand = beginstand volgende beurs)
```

**0. Backup.** `transactions` + `items` naar `data/backup/` met een timestamp in de
naam, en een kopie van de Excel. Elke stap hieronder is te herhalen; alleen stap 5 niet,
en juist daarom is de backup daar het belangrijkst.

**1. Nieuwe kaarten in de Excel, dan importeren.**
`python src/import_inventory_v3.py --dry-run` en controleer dat er **`zonder tegenhanger
in v7: 0`** staat. Staat daar een getal, dan is er een kaart hernoemd of samengevoegd —
zoek uit welke en vraag het na vóór je iets in `HERNOEMD` zet. Daarna zonder `--dry-run`.

**2. Beurs.** De app schrijft naar `transactions`. Meer hoeft er tijdens de beurs niet.

**3. Opschonen — dit kost de meeste tijd, en hoort met de hand.**
- datums controleren: alles moet op een beursdag staan, niet op de dag van invoer;
- dubbele invoer markeren met `is_dubbel` + een reden in `opschoon_notitie`;
- `stuks` invullen waar één regel meerdere kaarten dekt;
- €0,01-regels markeren als `workaround-afboeking`;
- vrije invoer koppelen waar het eenduidig is, de rest markeren.

**4. Afboeken.** `python src/afboeken_sales.py --event N --dry-run`, kijken of het klopt,
dan `--uitvoeren`. Moest je stap 1 opnieuw doen, reset dan eerst `afgeboekt_op`:
`UPDATE transactions SET afgeboekt_op = NULL WHERE event_id = N;` — de import heeft de
voorraad immers teruggezet.

**5. Terugschrijven naar de Excel.** Neem `items.aantal` over in kolom Aantal, en zet
**Vorig aantal gelijk aan Aantal**. Daardoor wordt `Verkocht = MAX(vorig − huidig; 0)`
weer 0 en begint de volgende beurs schoon. Wat er verkocht is blijft bewaard in
`transactions` — de Excel hoeft dat niet ook te onthouden.

**6. Controleren.** Rij voor rij: Excel-Aantal == database-aantal, over álle kaarten.
Een totaal dat toevallig klopt zegt niets; deze controle vangt zowel een mislukte
koppeling als een dubbele afboeking.

### De twee dingen die hier echt fout kunnen gaan

⚠️ **Na stap 5 nooit meer afboeken over diezelfde beurs.** De afboeking zit dan in de
Excel verwerkt. `afgeboekt_op` opnieuw op NULL zetten en het script nog eens draaien
haalt dezelfde stuks er een tweede keer af, en dat is aan de standen niet te zien.
Vuistregel: `afgeboekt_op` reset je alleen tussen stap 1 en 4, nooit erna.

⚠️ **Bewerk de xlsx nooit met openpyxl-save.** Dat wist alle gecachete formulewaarden,
en zonder LibreOffice (staat niet op deze machine) krijgt niets ze terug — de importer
leest dan overal `comp_prijs = NULL`. Bewerk het bestand op XML-niveau (`zipfile` +
gerichte vervanging), laat de `<f>`-elementen staan en werk alleen de `<v>` bij. Zet
`fullCalcOnLoad="1"` in `<calcPr>` zodat Excel het Dashboard bijwerkt bij openen.
Let op dat sommige rijen in L, O, P en Q een **vaste waarde** hebben in plaats van een
formule — behandel beide gevallen.

Kleinere valkuilen, allemaal een keer misgegaan:

- **Item-id's verschuiven bij elke import** (de tabel wordt vervangen en hernummerd).
  Verwijs naar kaarten met naam+code, nooit met een id.
- De importer koppelt op naam+code **mét volgnummer**. Voeg je twee rijen samen, verleg
  dan eerst de transacties naar het item met de **laagste `bron_rij`**.
- `NULL LIKE '%x%'` is NULL, en `NOT NULL` is NULL. Filter dus met
  `coalesce(opschoon_notitie,'') LIKE …`, anders vallen alle regels zonder notitie weg.
- Staat er een code op een transactieregel, koppel dan alleen aan een item met exact die
  code. Een item *zonder* code is geen bevestiging — bij veertien Pikachu's pikt dat er
  willekeurig een uit.

### Terugschrijven, uitgevoerd op 18-08-2026

De afboeking van Cardmaniacs staat nu in `data/TC_Inventaris_v7.xlsx`: 763 kaarten,
**1407 stuks**, voorraadwaarde **€111.238,65**, verkoopwaarde €123.139,65. Op 116 rijen
ging het aantal omlaag (Prismatic booster 52 → 14, Snorlax 34 → 20, Mew 17 → 7). Vorig
aantal is overal gelijkgetrokken en Verkocht staat overal op 0. Rij voor rij
gecontroleerd tegen de database: **0 afwijkingen op 763 kaarten**.

De beginstand van vóór de beurs staat als
`TC_Inventaris_v7_beginstand_voor_Cardmaniacs.xlsx` naast het bestand, en in
`data/backup/TC_Inventaris_v7_20260818_2230_voor_afboekterugschrijf.xlsx`.

## Opschoning uitgevoerd (18-08-2026) — de grote sessie

De database is opgeschoond en de beursverkopen zijn afgeboekt. **Er is niets verwijderd:**
dubbele invoer staat er nog en is gemarkeerd. Backups vóór elke stap in `data/backup/`
(`*_20260817_2214.csv` en `*_20260818_2043_voor_afboeken.csv`), plus de vorige v7 als
`TC_Inventaris_v7_20260817_2214_voor_vervanging.xlsx`.

### Eindstand — opschoning afgerond 18-08-2026

| | |
|---|---|
| `items` | **763**, 1407 stuks na afboeken (1620 in v7) |
| voorraadwaarde (CM) | **€111.238,65** |
| verkoopwaarde (comp) | €123.139,65 |
| items zonder enige prijs | 64 |
| uitverkocht / **negatief** | 88 / **0** |
| `transactions` | 362 totaal; event 3 = **232** regels, 116 op 15-08 en 116 op 16-08 |
| gekoppeld | 214 van 362 |
| afgeboekt | **204 regels = 213 stuks over 118 kaarten** |
| netto omzet event 3 | verkoop **€12.524,08** (204 regels) · inkoop €2.240,00 (3 regels) |

De omzet is een eigenschap van `transactions` en verandert dus niet mee met een
v7-ronde of een koppeling — alleen de voorraadkant beweegt. Wat de omzet *wel*
verandert is een nieuwe dubbelmarkering: de €12.529,08 werd €12.524,08 toen Ho-oh
#307 als dubbele tik werd gemarkeerd.

**Database en v7 sluiten rij voor rij aan.** Gecontroleerd over alle 763 rijen:
`items.aantal` = v7-aantal − afgeboekte stuks, met **0 afwijkingen** (1620 − 213 = 1407).
Die reconciliatie is de beste eindcontrole na een ronde — hij vangt zowel een
mislukte koppeling als een dubbele afboeking.

### Omzet zonder voorraadmutatie — 16 regels, bewust

Niet elke verkoop hoort een kaart uit de voorraad te halen. Deze 16 regels tellen
**wel** mee in de omzet en dragen daarom geen `is_dubbel`, maar er hangt bewust geen
afboeking aan. Ze staan als referentie in `data/nog_te_koppelen.csv` — dat is geen
actielijst meer.

- `opschoon_notitie = 'inkoop al in v7 verwerkt'` — **3 inkopen, €2.240** (Pikachu
  Nagaba, Pikachu SVP 027, de Prismatic masterset van €2.100). Die voorraad is met de
  hand in v7 gezet; nog eens optellen zou dubbel zijn.
- `opschoon_notitie = 'niet gekoppeld - product niet als los item in v7'` — **13
  verkopen, €897**. Steeds hetzelfde patroon: er is een losse eenheid verkocht uit iets
  dat in v7 als grootverpakking staat (3× Pitch Black Pokémon Center à €125 terwijl v7
  alleen de *case* kent — die houden we; Mega Evolution Tin €20 tegen een *display* van
  €220), of het product staat er helemaal niet in (Shiny Treasures, Gem pack vol 4,
  Destined Rivals bundle, Politoed, Fuecoco, Charmleon promo). Wil je die voorraad wél
  kloppend hebben, dan is de route een nieuwe regel in v7 — niet een koppeling in de
  database.

### Duplicaten opgeruimd in v7 (18-08)

30 dubbele rijen samengevoegd: de rij met de hoogste prijs blijft, het aantal van de
dubbel gaat erbij op, en de dubbel wordt **leeggemaakt in plaats van verwijderd**.
793 → 763 kaarten. Bewust blijven staan: 6 taalverschillen (EN naast JP), 3
conditieverschillen en 2 paren met een groot prijsgat — dat zijn aparte kaarten.

Twee dingen die daarbij makkelijk misgaan:

- **De importer koppelt op naam+code mét volgnummer.** Smelt een paar samen tot één
  rij, dan bestaat het 2e voorkomen niet meer en verliezen de transacties die daaraan
  hingen hun kaart. Verleg die eerst naar het item met de **laagste `bron_rij`** — dat
  is het 1e voorkomen, en dat wordt de samengevoegde rij.
- **Een xlsx bewerken met openpyxl wist alle gecachete formulewaarden.** Zonder
  LibreOffice (staat niet op deze machine) kan niets ze terugrekenen en leest de
  importer overal `comp_prijs = NULL`. Bewerk het bestand daarom op XML-niveau
  (`zipfile` + gerichte tekstvervanging): formules, caches, opmaak en het
  Dashboard-tabblad blijven dan intact. Zet `fullCalcOnLoad="1"` in `<calcPr>` zodat
  Excel het Dashboard bijwerkt zodra het bestand opengaat.
- Rijen leegmaken in plaats van verwijderen scheelt het verschuiven van gedeelde
  formulebereiken (`<f t="shared" ref="L2:L343">`). Let op: sommige voorgevulde rijen
  hebben in L en Q een **vaste waarde** in plaats van een formule — die moeten ook
  leeg, anders blijft er "Op voorraad" hangen op een rij die niet meer bestaat.

### Koppelen van vrije invoer (18-08)

Van de 27 losse verkopen zijn er **9 automatisch gekoppeld** (€980) volgens drie regels:
exacte code, óf een naam die precies één item aanwijst, óf een expliciete afspraak met
het team. Tikfouten worden opgevangen door dubbele letters te laten vallen
(`twillight` → `twilight`) en met een `difflib`-drempel van 0,88.

Twee filters die het verschil maken tussen koppelen en gokken:

- **Staat er een code op de regel, dan moet het item exact die code hebben.** Een item
  *zonder* code telt niet als bevestiging — bij veertien Pikachu's pikt dat er willekeurig
  een uit (dat gebeurde met "Pikachu" → "Pikachu 160").
- **Onderscheidende woorden** (`promo`, `sleeved`, `psa`, `etb`, `case`, `display`,
  `bundle`, `tin`, …) in de vrije tekst die niet in de itemnaam staan, blokkeren de
  koppeling: een promo is niet de gewone kaart. Haal die woorden uit de **ruwe** tekst,
  niet uit de genormaliseerde — de normalisatie gooit "promo" er zelf al uit.

De overige **18 regels** (€937 verkoop + €2.240 inkoop) dragen
`opschoon_notitie = 'niet gekoppeld - handmatig'` en staan in
`data/nog_te_koppelen.csv`. Ze **tellen mee in de omzet** — het waren echte verkopen —
alleen de voorraadafboeking ontbreekt.

### v7 is de waarheid — de database is afgeleid

`items` = v7-voorraad **min** de afboeking. Die relatie is reproduceerbaar
(importeren → `afgeboekt_op` resetten → afboeken), dus koppelingen en verleggingen
hoeven **niet** terug in v7; die leven in `transactions` en overleven een import.
Voorraad*fouten* horen wél in v7, anders komen ze bij de volgende import gewoon terug.

De laatste twee negatieve standen zijn zo opgelost:

- **Ho-oh 140/195 (EN)** — v7 zei 1, er stonden 2 verkopen. #307 (51 s na #306, zelfde
  bedrag) is als dubbele tik gemarkeerd. Staat nu op 0.
- **Pikachu 173/165 (JP)** — v7 had een leeg aantal (= 0) terwijl er 1 verkocht was.
  Het aantal is **in de v7-Excel** op 1 gezet, niet in de database. Staat nu op 0.

⚠️ **De Tangela-koppeling hangt aan de rijvolgorde in v7.** Beide Tangela-rijen (EN rij
134, JP rij 477) hebben dezelfde naam+code, dus de importer onderscheidt ze alleen op
volgorde. De twee verkopen staan nu op de EN-rij (het 1e voorkomen). Verplaats of
hersorteer die rijen niet zonder daarna te controleren of de verkopen nog op de EN-kant
staan. Bij Moltres speelt dit niet: daar verschillen de namen ("… Articuno" tegen
"… Articuno GX").

### Nog open na de opschoning

De beursadministratie zelf is **af**: elke transactie is beoordeeld, er staat niets meer
negatief, en database en v7 sluiten rij voor rij aan. Wat er nog ligt is losser werk:

- **Producten die als losse eenheid in v7 moeten** als je die 13 verkopen (€897) alsnog
  van de voorraad wilt halen — zie de sectie hierboven.
- `data/twijfel_opschoning.csv` — de resterende vragen uit de eerste ronde.
- Sessie B: `match_voorstellen.voorgesteld_item_id` staat nog los.

Bruto stond er €13.879,21 aan verkoop; daar gaat €1.350 aan gemarkeerde dubbelingen af
en €0,13 aan workaround-regels.

### Nieuwe kolommen (`migrations/006_transactions_opschoning.sql`)

- `is_dubbel` (bool, default false) — per ongeluk twee keer ingevoerd. Telt niet mee in de
  omzet en wordt niet afgeboekt.
- `opschoon_notitie` (text) — waaróm een regel bijzonder is. Vaste waarden die de scripts
  kennen: `workaround-afboeking` en `aantal-correctie: N stuks`.
- `stuks` (int, NULL = 1) — hoeveel kaarten één regel dekt. Alleen gevuld na bevestiging.
- `afgeboekt_op` (uit 005, nu pas toegepast) — maakt afboeken herhaalbaar. Tweede run
  levert "0 regels" op; gecontroleerd.

⚠️ **`coalesce(opschoon_notitie,'') LIKE …`** gebruiken bij het filteren. `NULL LIKE …` is
NULL en `NOT NULL` is NULL, dus zonder coalesce vallen álle regels zonder notitie weg —
dat gaf hier eerst een omzet van €4.714 in plaats van €12.529.

### Wat er is gebeurd, stap voor stap

1. **v7 opnieuw geïmporteerd** uit `/mnt/c/Users/jishn/Documents/Projects/TraderCollective/`.
   794 items, 718/794 verrijking behouden, **alle 203 transactie-koppelingen overleefd**.
   Sluit tot op de cent aan op het Dashboard-tabblad.
   - `import_inventory_v3.py` kreeg een **`HERNOEMD`-tabel**: "Prismatic Booster Pack" is in
     v7 hernoemd naar "Prismatic booster", waardoor 36 verkopen hun koppeling verloren.
     Alleen invullen na bevestiging door het team — dit is geen gokwerk.
   - `tekst()` snapt nu datums: Excel had van Togepi's kaartcode `2026-12-09` gemaakt.
     Die schrijven we zichtbaar-fout weg in plaats van te raden tussen 9/12 en 12/9.
   - De `slab`→`Slab`-fix was niet meer nodig; die stond al goed in het nieuwe bestand.
2. **Datums**: 68 regels stonden op 17-08 — dat was de inhaalslag van dag 2, in één batch
   ingevoerd tussen 19:10 en 19:45. Verzet naar 16-08, met de reden in `opschoon_notitie`.
   Binnen event 3 staat nu **niets meer buiten 15/16 augustus**.
3. **11 regels gemarkeerd als dubbel** (€1.350). Zie hieronder wat wél en niet als bewijs telt.
4. **13 regels als `workaround-afboeking`** (€0,13): 12x Snorlax direct na de verkoop van
   €270 en 1x Magikarp. Geen omzet, wél afgeboekt — dat is precies de bedoeling.
5. **Mew #496 op `stuks = 9`**: 9x Mew 205/165 samen voor €205. Bedrag is het totaal, niet
   per stuk. `afboeken_sales.py` telt `coalesce(stuks,1)` en haalt er dus 9 van de voorraad.
6. **Afgeboekt** met `--uitvoeren`. 27 regels zonder `item_id` (€4.157) zijn niet afgeboekt
   en staan in `data/niet_gekoppelde_sales.csv` — niet geraden.

### Wat als bewijs telde voor een dubbeling

Tijdsafstand alleen zegt niets, en op 16-08 19:10–19:45 al helemaal niet: daar is alles in
één batch nagevoerd, dus 7 seconden tussen twee Prismatic-verkopen is normaal tempo. Wel
gemarkeerd:

- **Exact dezelfde seconde**: #272 Dewgong, #415 Pikachu.
- **Identieke vrije tekst binnen 2 s** — die typ je niet zo snel opnieuw: #324, #314.
- **Herhaalde reeks**: #296–#302 (15-08 10:37–10:40) is 's avonds om 21:10–21:12 opnieuw
  ingevoerd als #352–#358. Zeven kaarten in exact dezelfde volgorde. Zes bedragen identiek.
- **Pikachu 160/159**: #299 (€34) en #355 (€58) hangen aan hetzelfde item 4725. Jishnu heeft
  bevestigd dat **#355 de echte is** en #299 de dubbele. Let op de valstrik: €58 is exact de
  comp-prijs van dat item, dus het voorgevulde bedrag — dat maakte het lastig te lezen.

Bewust *niet* gemarkeerd, door Jishnu bevestigd als echt: de 36 Prismatic-verkopen, 3x
Pitch Black Pokémon Center à €125 en 3x Phanatasamal booster à €12.

### Negatieve voorraad is bewust

Vijf items staan onder nul. Dat is een melding, geen fout: het getal zegt hoeveel er meer
verkocht is dan v7 wist. Ophogen in de database zou afwijken van het Excel-bestand — en de
volgende import gooit dat toch weg, want `items` wordt volledig vervangen. Corrigeren doe
je in v7, dan opnieuw importeren, dan afboeken.

| Item | Stand | Verkocht |
|---|---|---|
| 5680 Prismatic booster | **−22** | 36 (v7 zegt nog steeds 14) |
| 5517 Moltres & Zapdos & Articuno SM210 | −1 | 2 |
| 5646 Tangela 178/165 (JP) | −1 | 2 |
| 5771 Pikachu 173/165 (JP) | −1 | 1 (v7 stond al op 0) |
| 5171 Ho-oh 140/195 | −1 | 2 |

De item-id's verschuiven bij elke import (de tabel wordt vervangen en hernummerd) —
verwijs naar kaarten met naam+code, niet met een id. Na de v7-ronde van 18-08 21:00
staan deze vijf nog steeds negatief: die aantallen zijn in v7 nog niet gecorrigeerd.

### Een verse v7 importeren en opnieuw afboeken

De volgorde is dwingend en `afgeboekt_op` moet ertussen gereset worden:

1. Backup (`transactions` + `items`) met timestamp naar `data/backup/`.
2. `cp` de nieuwe v7 uit `/mnt/c/Users/jishn/Documents/Projects/TraderCollective/`; zet de
   oude eerst apart in `data/backup/`.
3. `python src/import_inventory_v3.py --dry-run` → controleer "zonder tegenhanger in v7: 0".
   Staat daar een getal, dan is er een kaart hernoemd; zoek uit welke en vraag het na
   vóór je iets in `HERNOEMD` zet.
4. `python src/import_inventory_v3.py`.
5. **`UPDATE transactions SET afgeboekt_op = NULL WHERE event_id = 3;`** — de items-tabel is
   vervangen, dus geen enkele voorraadstand draagt nog een afboeking. Zonder deze reset
   slaat de volgende ronde alles over en blijft de voorraad op de v7-stand staan.
6. `python src/afboeken_sales.py --event 3 --dry-run`, dan `--uitvoeren`.

`is_dubbel`, `opschoon_notitie`, `stuks` en `afgeboekt_op` staan alle vier in
`transactions` en géén ervan in `items` — de items-vervanging raakt ze dus niet. Het enige
wat de importer aan `transactions` doet is `item_id` hernummeren.

### Open: `data/twijfel_opschoning.csv` (22 gevallen)

Niets daarvan is gemarkeerd of gewijzigd. Vier soorten: **7x v7 opschonen** (39 naam+code-
combinaties staan dubbel in v7, vaak één rij mét en één zonder prijs — o.a. Greninja
swsh144 en Blastoise 200/165 en 2/102; plus code `116/086` op twee namen en de
Togepi-datumcode), **6x koppelen** (vrije invoer die aan een bestaand item hoort, zoals
Mega Darkrai #380 waarvan de code exact matcht met item 5126), **5x voorraad te laag** (de
negatieve standen hierboven), **2x bedrag onbekend** en **2x dubbeling?**.

⏳ **Charmleon #388/#389 is bewust open gelaten.** Twee regels van €1,00 voor
"Charmleon promo 2x", vrij ingevoerd, 1 s uit elkaar, zonder code — en er is nergens een
bijbehorend totaalbedrag, dus dit is géén €0,01-workaroundpatroon. In v7 staat alleen
item 5122 Charmeleon 99/97, zonder promo-vlag en zonder prijs. Zonder de echte prijs is
elke keuze een gok, en beide regels hebben toch geen `item_id` — er wordt dus niets
afgeboekt zolang dit open staat. Alleen de omzet staat mogelijk €2,00 te laag.
Zelfde vraag bij #392 Jolteon Masterball €1,00.

## Vastgestelde staat op 17-08-2026 (voorbereiding, niets gewijzigd)

Alleen gelezen en opgeruimd. **Geen enkele databaserij aangeraakt.** Bewust nog niet
opgeschoond: Jishnu vult zondag 16-08 eerst compleet in de app in.

### Database

| | |
|---|---|
| `items` | **720** rijen, 1358 stuks, voorraadwaarde (cm × aantal) **€112.417,55**, verkoopwaarde €120.920,60 |
| items zonder enige prijs | 54 (tonen "€ ?" in de app) — 177 zonder comp-prijs |
| items met aantal ≤ 0 | 8, geen enkele negatief |
| `transactions` | **294** rijen (was 246 bij de vorige sessie) — 260 verkoop, 20 trade, 14 inkoop |
| event 1 (juli) | 130 rijen, 25/26-07 — ongewijzigd |
| event 3 (Cardmaniacs) | **164** rijen: 116 op 15-08, **48 op 16-08** |
| item_id gevuld | 150 van 294 |

**Er is niets half uitgevoerd van de vorige sessie.** De kolommen `is_dubbel` en
`opschoon_notitie` bestaan niet en zijn ook nooit bedacht; `afgeboekt_op` uit
`migrations/005_transactions_afgeboekt.sql` bestaat óók nog niet — die migratie is nog
niet op Supabase toegepast. Geen enkele rij draagt `flag = 'dubbel'`; de flags zijn
`vrij ingevoerd` (13), `CHECK BEDRAG` (8, juli) en `stapel` (1). Voorraad is nergens
afgeboekt: `items.aantal` staat nog precies op de v7-stand.

### De €0,01-regels zijn een aantal-workaround, geen test en geen dubbeling

De app kent geen aantal per regel. Bij meerdere exemplaren van dezelfde kaart is één keer
het **totaalbedrag** geboekt en zijn de resterende stuks als €0,01 ingevoerd, puur om ze
uit de voorraad te krijgen. Dus: **wel afboeken van de voorraad, niet meetellen als omzet.**

- 13 regels, samen €0,13, allemaal op 15-08:
  - **12× Snorlax** (item 3984, code PR-SV SVP 051) om 15:43–15:47, direct ná #338
    Snorlax **€270,00** om 15:43:00. Samen dus 13 stuks voor €270. Voorraad staat op 34.
  - **1× Magikarp** (item 3701, code PAL 203) om 21:25:33.
- `src/dubbelingen.py` behandelt alles onder €1 al apart (sectie E, "geen dubbeling") en
  laat het buiten het schrapvoorstel. Dat klopt met de bedoeling — niets aan te passen.
- `src/afboeken_sales.py` boekt ze wél af (1 stuk per verkoopregel met `item_id`). Ook
  goed. Let alleen op bij **rapportage**: het regel-aantal telt ze mee, de omzet niet.
- ⚠️ Op 16-08 staan drie regels van **exact €1,00** die er hetzelfde uitzien maar níét
  onder de €1-drempel vallen: #388 en #389 "Charmleon promo 2x" (vrij ingevoerd, 09:23:46
  en :47) en #392 "Jolteon Masterball" (09:57:00). Die belanden nu in de gewone
  dubbelingen-detectie. Zelf nalopen of dit dezelfde workaround is.

### WhatsApp-exports 15/16-08 (geparsed, níét geïmporteerd)

`data/salestrade1516augustus2026/` en `data/inkoop1516augustus2026/`, geparsed met
`scripts/parse_whatsapp.py --dates 15-08-2026 16-08-2026`:

| Kanaal | Dag | Berichten | Met foto | Met bedrag | Som |
|---|---|---|---|---|---|
| Sales & trades | 15-08 | 54 | 52 | 45 | €7.757 (44 verkoop + 10 trade) |
| Sales & trades | 16-08 | **103** | 92 | 92 | **€13.304** (80 verkoop + 16 trade) |
| Inkoop | 15-08 | 16 | 12 | 11 | €7.087 |
| Inkoop | 16-08 | **27** | 11 | 14 | **€7.595** |

**Het gat voor 16-08 is groot.** In de app staan 48 verkoopregels voor €1.298,70, tegen
96 WhatsApp-berichten met bedrag voor ±€13.304. De app-invoer stopt om 14:15, WhatsApp
loopt door tot 21:32, en alles boven €160 ontbreekt. **Inkoop 16-08 staat helemaal niet
in de app** (0 regels tegen 15 berichten). Voor 15-08 is het omgekeerd — 100 echte
app-regels tegen 54 WhatsApp-berichten — omdat de app per kaart boekt en WhatsApp per
klant/stapel. Die verhouding is dus geen maatstaf; het ontbreken van de dure kaarten en
van de hele middag/avond wél.

- Parser-artefact: `IMG-20260816-WA0085.jpg … 465 voor @183382302552143` levert een bedrag
  van 183 biljoen op — `extract_amount` leest het telefoonnummer achter een @-mention.
  Het echte bedrag is €465. Als de inkoop-import er ooit komt: @-mentions eerst strippen.

### v7 is klaar voor een her-import — met twee waarschuwingen

`python src/import_inventory_v3.py --dry-run` draait schoon: 720 items gelezen, 720/720
verrijking overgenomen, en alle **150** `transactions.item_id`-koppelingen overleven het
(0 zonder tegenhanger). Kolomkoppen zijn ongewijzigd. Nieuwe kaarten toevoegen kan dus.

- **Type in de rijen 727 t/m 916.** Die staan voorgevuld klaar (Taal EN, Categorie single,
  Staat NM, Aantal 1) *inclusief* de formules voor Comp prijs, Voorraadwaarde,
  Verkoopwaarde en Status. De importer slaat ze over zolang kolom A leeg is. Vanaf rij 917
  houden de formules op — daar moet je ze zelf naar beneden doortrekken, anders komt de
  kaart zonder comp-prijs binnen.
- **Prijzen zijn formules; de importer leest gecachte waarden** (`data_only=True`). Sla het
  bestand dus op met Excel of LibreOffice, die herrekenen. Een editor die de formulecache
  niet bijwerkt levert `comp_prijs = NULL` op precies de rijen die je hebt aangeraakt.
- **Volgorde is kritiek:** `Aantal` in v7 is de voorraad *vóór* het afboeken. Importeer je
  v7 opnieuw ná `afboeken_sales.py`, dan zet je de afboeking terug. Eerst v7, dan afboeken.
- Kleinigheid: 1 rij heeft categorie `slab` in plaats van `Slab`. Excel trekt zich daar
  niets van aan (`=` is hoofdletterongevoelig), de database wel — dat wordt een aparte
  categoriewaarde. Meenemen als je toch in het bestand zit.
- 24 naam+code-combinaties komen meer dan één keer voor (49 rijen). De importer telt het
  hoeveelste voorkomen mee, dus dat gaat goed — zet nieuwe dubbele regels wel *onder* de
  bestaande, niet ertussen.

### Signalen om straks naar te kijken (niet aangeraakt)

- 9 items zijn in de app vaker verkocht dan er op voorraad ligt. Grootste:
  **Prismatic Booster Pack** (item 4165) — voorraad 14, 20× verkocht. Verder 7 kaarten met
  voorraad 1 die 2× verkocht zijn (Dewgong, Tangela, Ho-oh, Wartortle, Pikachu, Moltres &
  Zapdos & Articuno) en 2 met voorraad 0 (Pikachu 4255, Twillight Masquerade booster 4256).
  Deels echte dubbelingen, deels voorraad die in v7 te laag staat — niet te raden.
- 27 paren "zelfde kaart, zelfde bedrag binnen 60 s" in event 3, waarvan 13 de Snorlax- en
  Magikarp-workaround. De rest zit vooral op boosterpakjes (Prismatic €15,
  Phantasamal Flames €12/€14) — daar liggen er tientallen van, dus dat is waarschijnlijk
  gewoon volume. Twee opvallende: #414/#415 Pikachu €33 op **dezelfde seconde** (11:57:52)
  en #402/#403 Poliwhirl €14 met 1 seconde ertussen — dat ruikt naar een dubbele tik.
- `data/dubbelingen_rapport.txt` dateert van 15-08 en kent de 48 regels van 16-08 niet.
  Opnieuw draaien nadat de invoer compleet is.

## Opschoning na de beurs (16-08-2026)

Drie stappen: v7 importeren, verkopen ontdubbelen, verkopen afboeken. **Stap 1 is klaar,
stap 2 ligt bij Jishnu, stap 3 wacht daarop.**

- ✅ **Backups vooraf**: `data/backup/transactions_voor_opschoning.csv` (246 rijen) en
  `data/backup/items_voor_opschoning.csv` (599 rijen).
- ✅ **v7 geïmporteerd** — `src/import_inventory_v3.py` met `data/TC_Inventaris_v7.xlsx`:
  **720 items**, 1358 stuks, voorraadwaarde €112.417,55, verkoopwaarde €120.920,60.
  Sluit tot op de cent aan op het Dashboard-tabblad. Overgeslagen: 3 placeholders,
  192 voorgevulde lege regels (naamloos, Aantal 1) en 74 lege rijen.
  Dashboard telt 722 unieke items: dat is 720 + 3 placeholders − 1 rij met een getal
  als naam (`"?*"` matcht geen getallen) — zelfde rekensom als bij v6.
- **Waarom een v3 naast v2:** sinds de beurs hangen er 104 transacties met een foreign
  key aan `items`. De DELETE+INSERT van v2 loopt daarop stuk, en zou alle verkopen hun
  kaartkoppeling kosten. v3 zet de nieuwe rijen erin, *hernummert* `transactions.item_id`,
  `price_points.item_id` en `match_voorstellen.voorgesteld_item_id` naar de nieuwe id's,
  en ruimt dan pas de oude op — alles in één transactie. Alle 104 koppelingen zijn
  behouden (0 verloren; gecontroleerd tegen de backup-CSV).
  - Koppelen gaat op naam+code mét volgnummer: die sleutel is niet uniek (23 combinaties
    komen 2x voor in v7), anders klappen dubbele regels op één id samen.
  - `bron_rij` is UNIQUE en oud en nieuw gebruiken dezelfde rijnummers → de oude rijen
    krijgen eerst `bron_rij = NULL` (ze verdwijnen toch aan het eind van de transactie).
  - `RETURNING` levert bij een executemany geen rijen; de nieuwe id's komen via `bron_rij`.
- ⏳ **Ontdubbelen ligt stil op verzoek** — `data/dubbelingen_rapport.txt`
  (`python src/dubbelingen.py`, leest alleen). Voorstel: 10 van de 113 verkopen zijn
  dubbel (€1.288). Jishnu wil eerst met het team overleggen; er is **niets gewijzigd**.
  Zijn keuzes voor straks: **markeren met `flag = 'dubbel'`, niet verwijderen**, en de
  13 cent-regels blijven staan en worden **wel** afgeboekt — die zijn geen dubbeling maar
  een aantal-workaround, zie de sectie van 17-08 hierboven.
- ⏳ **Afboeken staat klaar, niet uitgevoerd** — `src/afboeken_sales.py` (dry-run tenzij
  `--uitvoeren`) en `migrations/005_transactions_afgeboekt.sql` (kolom `afgeboekt_op`,
  nog **niet** toegepast op Supabase). Zonder dat stempel is afboeken niet te herhalen.
  Het script slaat `flag = 'dubbel'` over, boekt 1 stuk per verkoopregel af, laat
  `vorig_aantal` met rust en verwijdert nooit een item (aantal mag naar 0 of negatief).
  Inkopen blijven buiten schot: die zitten al in het v7-bestand.
- `data/niet_gekoppelde_sales.csv` — 12 regels zonder `item_id` (9 verkopen à €1.030 en
  3 inkopen, waaronder de stapel van €2.100). Handmatig te koppelen; niet raden.

### Wat het dubbelingen-rapport gebruikt als bewijs

Snelheid alleen zegt niets: de kleinste tussenpoos tussen twee *verschillende* kaarten is
1-2 seconden (het team tikt een stapeltje achter elkaar in), mediaan 29 s. Wel bewijs:

- **voorraad 1 en twee keer verkocht** — kan per definitie niet.
- **vrije invoer twee keer binnen 20 s** — die naam is niet zo snel opnieuw te typen.
- **herhaalde reeks**: om 21:10–21:12 zijn 7 kaarten opnieuw ingevoerd in exact dezelfde
  volgorde als om 10:37–10:40. Eén regel kan toeval zijn, zeven niet.

Bewust *niet* als dubbeling geteld: losse boosterpakjes (Prismatic €15, Twilight €10) en
3× Mega Dream €95 binnen 80 s — daar ligt 14 tot 32 stuks van, twee klanten met hetzelfde
pakje ziet er precies zo uit.

- ~~⚠️ Alle 116 beursregels staan op 15-08; de avondinvoer van 21:13–21:25 lijkt dag 2.~~
  **Achterhaald (17-08).** Op 16-08 zijn er tijdens dag 2 zelf 48 regels bijgekomen
  (07:59–14:15). De avondbatch van 15-08 om 21:10–21:25 *kán* geen dag 2 zijn — dag 2 was
  toen nog niet geweest. Het is een inhaalslag van dag 1: de duurdere kaarten
  (Pikachu €700, Umbreon €390, Mega Darkrai €300) plus de inkoopstapel van €2.100.
- ✅ Beurs-app gecontroleerd op de nieuwe tabel: `tests/test_beurs_app.py` groen, en live
  tegen Supabase 720 items geladen, zoeken/prijzen/dagtotaal werken (54 items hebben geen
  comp én geen cm-prijs en tonen "€ ?").

## Fase-status (15-08-2026 — beursdag Nijmegen)

- ✅ v6-inventaris geïmporteerd: **599 items** (500 single, 64 Slab, 32 Sealed, 3 zonder
  categorie), 592 op voorraad + 7 uitverkocht. Voorraadwaarde (cm × aantal) €102.747.
  Overgeslagen: 2 naamloze rijen en 3 `[ place holder ]`-regels.
- ✅ 478 van de 599 items hebben hun verrijking behouden (officiele_naam/set_code/
  cardmarket_id op naam+code overgenomen); 323 hebben nu een officiële naam.
- ✅ Backups vóór de vervanging: `data/items_backup_voor_import.csv` en
  `data/match_voorstellen_backup_voor_import.csv`.
- ✅ Beurs-app draait op de nieuwe tabel; sealed komt met prijs terug. Eén fix nodig:
  terugval op `prijs_cm` (zie hierboven), anders stond er "€ ?" bij alle slabs.
- ⏳ Sessie B: `match_voorstellen.voorgesteld_item_id` is losgekoppeld (staat in de
  backup-CSV met naam/code, dus opnieuw te koppelen tegen de nieuwe item-id's).

## Fase-status (13-08-2026, tweede ronde)

- ✅ Vrije invoer heeft een eigen code-veld (→ `transactions.code`, migratie 003 is op
  Supabase toegepast; 130 rijen ongemoeid).
- ✅ Stapel-modus zichtbaar gemaakt: teller, lijst met verwijderregels, codes mee in
  `ruwe_tekst`. UI opgeschoond: kleur per modus, rustiger terzijdes, brand-thema.
- ✅ `tests/test_beurs_app.py` draait nu 110 checks + een scenario-verslag (de vier
  vragen uit de opdracht, met de weggeschreven databaserij erbij).
- Bekend en bedoeld: het dagtotaal leest uit een cache van 20 s. Je eigen invoer breekt
  die cache open (direct bij), invoer van een collega volgt binnen 20 s.

## Fase-status (13-08-2026)

- ✅ Beurs-app uitgebreid: live dagtotaal, inkoop-rekenhulp (%-knoppen) + stapel op
  totaalbedrag, en undo van de eigen laatste invoer. Tests: 86 checks groen.
- ✅ Screenshots op telefoonformaat in `screenshots/` (1-start, 2-inkoop-rekenhulp,
  3-inkoop-percentage, 4-inkoop-stapel, 5-undo).
- Bewust *niet* gebouwd: bod-/voorstel-status (de deal is rond bij invoer), afboeken op
  `items` (wacht op de nieuwe database) en een winkelmandje voor verkoop.

## Fase-status (10-08-2026)

- ✅ `beurs_app.py` + `tests/test_beurs_app.py` gebouwd; 45 checks groen tegen het
  SQLite-schaduwschema én end-to-end getest tegen Supabase (~0,4 s per invoer).
  Testregels weer verwijderd; `transactions` staat weer op 130.
- ✅ Event `Cardmaniacs Nijmegen 15-16 augustus 2026` bestaat (id 3, 15-08, Nijmegen).
- ⏳ Deploy naar Streamlit Community Cloud (repo staat klaar; secrets `SUPABASE_DB_URL`
  + `TC_BEURS_PIN` in de Cloud-UI zetten).
- Was het project gepauzeerd (pooler: `tenant/user ... not found`)? Zie `../CLAUDE.md`
  noot bij de DB — hervatten in het Supabase-dashboard lost het op.

## Fase-status (30-07-2026)

- ✅ Beurstransacties juli geparsed, gevalideerd, in database.
- ✅ Inventaris-identiteit: 367/572 zeker gematcht; twijfels gesplitst voor review.
- ✅ Supabase-database + Streamlit-dashboard (POC) draaien.
- ✅ **Sessie A opnieuw gedraaid met nieuwe exports (mét media):** 112/119 verkoop/trade
  hebben nu een foto (was 32). Van €16.725 verkoop/trade is €997 zeker te koppelen (was
  €163). Rest: 43 meerdere-kaarten-stapels/trades + 46 twijfel (veel sealed JP product
  en slabs die niet in de singles-inventaris staan). Niets afgeboekt.
- ⏳ **Sessie B (nog te doen):** mens reviewt de match_voorstellen (twijfel + meerdere).
  Nodig: stapels/trades splitsen, en een plek voor sealed product + graded slabs buiten
  de singles-inventaris.
- ⏳ 81 inventaris-twijfels handmatig nalopen; JP (72) + CN (2) apart oplossen.
- ⏳ Prijs-sync (`price_points`) nog niet gebouwd.

## Let op

- Git: **privé** repo github.com/Jishdk/tc-inventory (sinds 10-08-2026, voor de
  Streamlit-Cloud-deploy van `beurs_app.py`). `data/`, `*.xlsx`, `*.csv` en `.env` staan
  in `.gitignore` — code wel in git, bedrijfsdata blijft lokaal.
  Oude bestanden 1e matching-poging in `../_archief/`.
- Secrets alleen in `.env` (RapidAPI + Supabase), nooit committen.
- `data/_inkoop_v2` / `_sales_v2` zijn uitgepakte media (niet in git); `transactions.media_file`
  wijst ernaar. Opnieuw uitpakken uit de zips kan altijd.
