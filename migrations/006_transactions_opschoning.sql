-- Opschoning na Cardmaniacs Nijmegen (17-08-2026)
--
-- Het uitgangspunt van deze database is dat er nooit iets fysiek weg gaat. Een
-- verkeerd ingevoerde regel blijft staan en wordt gemarkeerd; wie later terugkijkt
-- ziet dan nog wát er die dag is ingetikt en waarom het niet meetelt.
--
-- is_dubbel        = per ongeluk twee keer ingevoerd. Telt niet mee in de omzet en
--                    wordt niet afgeboekt van de voorraad.
-- opschoon_notitie = waaróm een regel bijzonder is. Vrije tekst, maar met een paar
--                    vaste waarden die de scripts kennen:
--                      'workaround-afboeking'  -> geen omzet, wél afboeken. De app
--                                                 kent geen aantal per regel, dus is
--                                                 1x het totaalbedrag geboekt en de
--                                                 rest als EUR 0,01.
--                      'aantal-correctie: N stuks' -> één regel die N stuks voorstelt;
--                                                 het bedrag is het totaal, niet per stuk.
--
-- Let op het verschil met is_dubbel: een workaround-regel is géén dubbeling. Hij
-- hoort bij de voorraad-afboeking en moet daar dus in blijven meedoen.
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS is_dubbel boolean NOT NULL DEFAULT false;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS opschoon_notitie text;

-- Hoeveel stuks stelt deze regel voor? NULL = 1 (het normale geval). Alleen gevuld
-- waar het team heeft bevestigd dat één invoer meerdere kaarten dekt, zoals
-- "Mew 205" = 9 stuks voor EUR 205 samen.
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS stuks integer;

CREATE INDEX IF NOT EXISTS transactions_is_dubbel_idx
    ON transactions (is_dubbel) WHERE is_dubbel;
