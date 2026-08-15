-- Wanneer is een verkoop van de voorraad afgeboekt? (16-08-2026)
--
-- Zonder dit stempel is afboeken niet te herhalen: draai je het script twee keer,
-- dan gaat de voorraad twee keer omlaag. Met dit stempel pakt een tweede ronde
-- alleen de regels op die er nog niet af zijn — bijvoorbeeld de invoer van dag 2
-- die later binnenkomt.
--
-- Bestaande rijen houden NULL: die zijn nog niet afgeboekt.
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS afgeboekt_op timestamptz;

-- 'dubbel' in flag betekent: per ongeluk twee keer ingevoerd, telt niet mee.
-- Geen constraint — flag is vrije tekst en heeft al andere waardes
-- ('vrij ingevoerd', 'stapel', 'CHECK BEDRAG').
CREATE INDEX IF NOT EXISTS transactions_afgeboekt_op_idx
    ON transactions (afgeboekt_op) WHERE afgeboekt_op IS NULL;
