-- 011: een beurs die meer dan één dag duurt
--
-- De app bepaalt met `events.event_date` of vandaag een beursdag is. Bij een
-- tweedaagse beurs klopt dat op dag 2 niet meer, en verschijnt het grijze
-- "valt buiten de beursdagen — invoer telt wél mee". De invoer is dan prima,
-- maar het terzijde zaait twijfel op precies het verkeerde moment.
--
-- Tot nu toe was `TC_EVENT_DAGEN` het enige antwoord, en dat is een secret: die
-- staat in de Streamlit-Cloud-UI en niet bij het event. Wie de beurs aanmaakt
-- kan nu meteen zeggen tot wanneer hij loopt.
--
-- NULL = eendaags, en dan gedraagt alles zich precies zoals voorheen.
-- `TC_EVENT_DAGEN` blijft voorrang houden: dat is het noodventiel voor een
-- beurs met een gat ertussen.

ALTER TABLE events ADD COLUMN IF NOT EXISTS eind_datum date;
