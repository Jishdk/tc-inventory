# Terugdraaien van de beurs-app

Voor als de live app stuk is en het snel moet. Geschreven op 22-08-2026, toen v2
live ging.

| | |
|---|---|
| **Laatste stabiele commit** | `803367b` — *Notitie bij de derde Shining Magikarp (PSA 5 slab)* |
| Wat er nu live staat | `3893e0d` — *Beurs-app: huisstijl eroverheen* |
| Repo die de live app voedt | `github.com/Jishdk/tc-inventory`, branch **main** |

Streamlit Cloud herstart automatisch bij een push naar `main`. Alle commando's
hieronder gaan ervan uit dat je in `~/projects/tc/inventory` staat en op `main`
(`git checkout main`).

---

## 1. Snelste noodgreep — alleen de app terug (aanbevolen op de beursvloer)

Zet de vorige, bewezen app terug zonder de rest van de historie aan te raken.
Eén commit vooruit, dus niets wordt herschreven en niemand hoeft te force-pushen.

```bash
git checkout main
git pull
cp beurs_app_stable.py beurs_app.py
git checkout 803367b -- .streamlit/config.toml
git commit -am "Beurs-app tijdelijk terug op de stabiele versie"
git push origin main
```

`beurs_app_stable.py` is byte-voor-byte de app die vóór v2 live draaide —
gecontroleerd tegen `803367b`.

**Waarom die tweede regel met config.toml erbij hoort:** het donkere thema staat
niet in `beurs_app.py` maar in `.streamlit/config.toml`. Zet je alleen de app
terug, dan draait de oude app in het nieuwe donkere thema en ziet hij er
verkeerd uit.

Dit laat de database, de migraties en de documentatie staan. Dat is precies de
bedoeling: `transactions` heeft sinds v2 de kolommen `group_id` en
`onderhandeld`, en de oude app schrijft daar simpelweg niet in. Hij blijft
gewoon werken.

## 2. Alles terug naar de stand van vóór v2

Draait de hele merge terug met nieuwe commits erbovenop. Historie blijft intact,
dus dit kan zonder overleg.

```bash
git checkout main
git pull
git revert --no-commit 803367b..3893e0d
git commit -m "Beurs-app v2 teruggedraaid"
git push origin main
```

Let op: dit haalt ook `migrations/007` en de bijgewerkte `afboeken_sales.py`
weg uit de werkkopie. De **kolommen blijven in Supabase staan** — een revert in
git verandert niets aan de database. Dat is goed: ze zijn `NULL`-baar en storen
niets.

## 3. Harde terugzetting (laatste redmiddel)

Alleen als 1 en 2 niet volstaan. Dit **herschrijft de historie** van `main`;
iedereen met een kloon moet daarna opnieuw pullen.

```bash
git checkout main
git reset --hard 803367b
git push --force origin main
```

De v2-code raak je hiermee niet kwijt: die staat veilig op de branch
`beurs-app-v2` (`3893e0d`) en op de remote.

---

## Terug naar v2 nadat het is opgelost

```bash
git checkout main
git merge beurs-app-v2      # of: git revert van de revert-commit
git push origin main
```

## Wat je vooraf even wilt weten

- **De database is niet te "reverten" met git.** Migratie 007 is op 19-08-2026
  toegepast op Supabase. Wil je die echt weg (bijna nooit nodig):
  `ALTER TABLE transactions DROP COLUMN group_id, DROP COLUMN onderhandeld;`
  Dat gooit wél de groepskoppelingen weg van alles wat met v2 is ingevoerd.
- **Testen vóór je pusht** kan altijd: `python tests/test_beurs_app.py` draait
  headless tegen een SQLite-schaduwschema en raakt Supabase niet aan.
- **Lokaal naast elkaar draaien:**
  `streamlit run beurs_app.py` en
  `streamlit run beurs_app_stable.py --server.port 8502`.
