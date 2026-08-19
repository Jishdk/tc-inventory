-- Meerdere kaarten in één transactie: mandje en trades (19-08-2026)
--
-- De app boekte tot nu toe één kaart per regel. Vanaf de mandje-versie hoort een
-- verkoop of trade vaak bij méér kaarten: drie kaarten aan één klant, of een trade
-- waarbij er vier kaarten de deur uit gaan en er iets bij wordt gelegd.
--
-- We blijven per kaart één regel schrijven — dat is wat `afboeken_sales.py` nodig
-- heeft om de voorraad kloppend te krijgen (item_id + stuks per regel). Wat er
-- ontbrak is het haakje dat die regels bij elkaar houdt.
--
-- group_id = alle regels van één afspraak met één klant. NULL bij een losse
--            verkoop van één kaart: dat is nog steeds het normale geval en hoeft
--            geen groep. Bij een trade zit óók de cash-regel in dezelfde groep,
--            zodat je van elke uitgaande kaart terugvindt wat er tegenover stond.
--
-- Bewust text en geen uuid-kolom: de waarde komt als uuid4-string uit de app en
-- gaat zo ook de SQLite-schaduwdatabase van de tests in. Eén type minder dat per
-- database anders werkt; we doen er toch nooit uuid-rekenwerk mee.
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS group_id text;

-- Bestaande rijen houden NULL: die zijn allemaal los ingevoerd.
CREATE INDEX IF NOT EXISTS transactions_group_id_idx
    ON transactions (group_id) WHERE group_id IS NOT NULL;

-- Week de prijs af van de comp-prijs? Dan is er onderhandeld. Dat is geen fout en
-- geen dubbeling, maar het verklaart wél waarom de omzet lager uitvalt dan de
-- inventariswaarde deed vermoeden — en zonder vlag is dat achteraf niet te zien.
--
-- Los van `flag` gehouden: die kolom is vrije tekst met één waarde per regel
-- ('vrij ingevoerd', 'stapel', 'trade_cash'), en onderhandeld kan bij elk van die
-- gevallen voorkomen.
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS onderhandeld boolean NOT NULL DEFAULT false;
