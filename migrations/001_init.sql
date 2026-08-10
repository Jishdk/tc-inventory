-- TC inventory POC — schema
-- Idempotent: enums via DO-block, tabellen/indexen met IF NOT EXISTS.
-- Ontworpen voor latere uitbreiding (transacties per beurs, prijshistorie),
-- nu simpel gevuld.

-- === enums ===
DO $$ BEGIN
    CREATE TYPE event_type AS ENUM ('beurs', 'inkoop', 'online', 'overig');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE transactie_kanaal AS ENUM ('inkoop', 'verkoop', 'trade');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE transactie_type AS ENUM ('verkoop', 'inkoop', 'trade');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- === events ===
CREATE TABLE IF NOT EXISTS events (
    id          bigserial PRIMARY KEY,
    name        text NOT NULL UNIQUE,
    event_date  date,
    location    text,
    type        event_type,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- === items ===
-- bron_rij = 1-based positie in TC_Inventaris_v5.xlsx; stabiele sleutel voor
-- idempotente import (er zijn dubbele naam+code-combinaties, positie niet).
CREATE TABLE IF NOT EXISTS items (
    id              bigserial PRIMARY KEY,
    bron_rij        int UNIQUE,
    onze_naam       text NOT NULL,
    officiele_naam  text,
    cardmarket_id   bigint,
    code            text,
    set_code        text,
    expansion       text,
    taal            text,
    categorie       text,
    promo           text,
    staat           text,
    grade           text,
    aantal          int NOT NULL DEFAULT 1,
    prijs_cm        numeric,
    comp_prijs      numeric,
    match_status    text,
    notitie         text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_items_categorie    ON items (categorie);
CREATE INDEX IF NOT EXISTS idx_items_set_code     ON items (set_code);
CREATE INDEX IF NOT EXISTS idx_items_match_status ON items (match_status);

-- === transactions ===
-- bron_rij = 1-based positie in de bron-CSV; uniek per event voor idempotentie.
CREATE TABLE IF NOT EXISTS transactions (
    id          bigserial PRIMARY KEY,
    event_id    bigint REFERENCES events (id),
    item_id     bigint REFERENCES items (id),
    bron_rij    int,
    datum       date NOT NULL,
    tijd        time,
    kanaal      transactie_kanaal NOT NULL,
    type        transactie_type NOT NULL,
    bedrag      numeric,
    afzender    text,
    media_file  text,
    flag        text,
    ruwe_tekst  text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (event_id, bron_rij)
);

CREATE INDEX IF NOT EXISTS idx_transactions_datum  ON transactions (datum);
CREATE INDEX IF NOT EXISTS idx_transactions_kanaal ON transactions (kanaal);

-- === price_points (nu leeg; voor latere prijs-sync) ===
CREATE TABLE IF NOT EXISTS price_points (
    id          bigserial PRIMARY KEY,
    item_id     bigint NOT NULL REFERENCES items (id),
    source      text,
    prijs       numeric,
    observed_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_price_points_item ON price_points (item_id);
