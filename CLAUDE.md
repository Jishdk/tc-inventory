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
- Tabellen: `events`, `items` (599 rijen, v6-inventaris), `transactions` (130),
  `price_points` (leeg, voor latere prijs-sync), `match_voorstellen` (Sessie A).
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
| `inventaris_datav2.xlsx` | **Actuele bron-inventaris v6** (sheet "Inventaris", 599 items) |
| `TC_Inventaris_v5.xlsx` | Vorige bron-inventaris (sheet "Inventaris", 572 regels) |
| `items_backup_voor_import.csv` | Items-tabel zoals hij vóór de v6-import was (572 rijen) |
| `match_voorstellen_backup_voor_import.csv` | Match-voorstellen mét de oude item-koppeling |
| `inventaris_verrijkt_v2.csv` | Verrijkte identiteit (na 2e pass): 367 zeker, 81 twijfel, 72 jp, 26 geen_match_naam, 18 zeker_prijs, 8 geen_match |
| `twijfel_singles.csv` / `twijfel_later.csv` | Handmatig na te lopen twijfelgevallen (15 singles-met-code / 66 rest) |
| `match_voorstellen_rapport.txt` | Meetrapport Sessie A (foto → match) |
| `inkoop27072026.zip`, `sales27072026.zip` | WhatsApp-exports (v2 = mét media, uitgepakt in `_inkoop_v2`/`_sales_v2`) |
| `_cmapi_rapid_cache.json` | Cache API-searches (bewaren) |

`inventory/data/`: transactie-CSV's (`_clean` is definitief) + `_vision_findings.json`
(121 handmatig/visueel gelezen beursfoto's, keyed op bestandsnaam — input voor match_foto).

## API (identiteit + prijzen)

RapidAPI "Cardmarket API TCG" (tcggopro), host `cardmarket-api-tcg.p.rapidapi.com`,
key `CMAPI_KEY` in `../.env` (Pro-tier 3.000/dag). Geen NL-prijsveld; wel
`lowest_near_mint` + DE/FR/ES/IT + `30d_average`. (`CMAPI_LIVE_KEY` = cardmarketapi.com
trial, niet meer gebruikt.)

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
