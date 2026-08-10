-- Sessie A: match-voorstellen (foto → inventaris). Append-only; bij herdraaien
-- eerst leegmaken en opnieuw vullen. Niets wordt hier afgeboekt.

DO $$ BEGIN
    CREATE TYPE match_zekerheid AS ENUM (
        'zeker', 'twijfel', 'niet_gevonden', 'foto_onbruikbaar', 'meerdere_kaarten'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS match_voorstellen (
    id                  bigserial PRIMARY KEY,
    transaction_id      bigint NOT NULL REFERENCES transactions (id),
    voorgesteld_item_id bigint REFERENCES items (id),
    zekerheid           match_zekerheid NOT NULL,
    methode             text,
    foto_leesbaar       boolean,
    gelezen_tekst       text,
    kandidaten          jsonb,
    reden               text,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mv_transaction ON match_voorstellen (transaction_id);
CREATE INDEX IF NOT EXISTS idx_mv_zekerheid   ON match_voorstellen (zekerheid);
