-- Inventaris v6 (inventaris_datav2.xlsx) heeft een paar velden die de v5-import
-- nog niet kende. Los toegevoegd zodat de import niets hoeft weg te gooien.
--
-- `Voorraadwaarde (CM)` en `Verkoopwaarde (comp)` slaan we bewust NIET op: dat
-- zijn formules (aantal × prijs) die we altijd kunnen herrekenen.
-- `Set` en `Laatst bijgewerkt` staan in het bronbestand leeg.

ALTER TABLE items ADD COLUMN IF NOT EXISTS vorig_aantal    int;
ALTER TABLE items ADD COLUMN IF NOT EXISTS verkocht        int;
ALTER TABLE items ADD COLUMN IF NOT EXISTS status          text;
ALTER TABLE items ADD COLUMN IF NOT EXISTS vintage_modern  text;

CREATE INDEX IF NOT EXISTS idx_items_status ON items (status);
