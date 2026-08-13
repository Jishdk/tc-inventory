-- Kaartcode op een transactie (beurs-app, vrije invoer).
--
-- Een vrij ingevoerde kaart heeft geen item_id; de code ("173/195", "swsh260")
-- is dan het enige identiteitshaakje om 'm later alsnog aan de inventaris te
-- koppelen. Bij een gezochte kaart vullen we 'm ook, zodat een export van
-- transactions voor beide even bruikbaar is.

ALTER TABLE transactions ADD COLUMN IF NOT EXISTS code text;

CREATE INDEX IF NOT EXISTS idx_transactions_code ON transactions (code);
