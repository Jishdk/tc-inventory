-- 012: sets aan de inventaris (sessie 2, 11-10-2026)
--
-- `set_code` bestond al uit de RapidAPI-verrijking ("MEG 161": afkorting plus
-- kaartnummer) maar was alleen gevuld voor EN singles en zegt niets leesbaars.
-- Zoeken op set ("evolving skies", "obsidian", "sv11b") wil een afkorting en
-- een naam per item, en het Dashboard/Excel een kolom Set.
--
-- set_afk    : setafkorting zoals de API hem kent (MEG, EVS, OBF) of de JP-code
--              uit onze eigen inventaris (sv11b, s12a).
-- set_naam   : leesbare naam ("Evolving Skies", "Black Bolt (JP)"); dit is wat
--              in de Excel-kolom Set staat en wat de importer vanaf nu leest.
-- set_jaar   : jaar van uitgave, voor sorteren/filteren later.
-- set_status : hoe de koppeling tot stand kwam — zeker (API, nummer én
--              settotaal), afgeleid (uit code of productnaam), twijfel (niet
--              geraden; kandidaten in data/set_twijfel.csv), geen_match,
--              geen_bron (JP/CN/KR zonder bron), geen_code.
--
-- Allemaal additief; de live app op main leest ze niet en merkt er niets van.

ALTER TABLE items ADD COLUMN IF NOT EXISTS set_afk    text;
ALTER TABLE items ADD COLUMN IF NOT EXISTS set_naam   text;
ALTER TABLE items ADD COLUMN IF NOT EXISTS set_jaar   smallint;
ALTER TABLE items ADD COLUMN IF NOT EXISTS set_status text;
