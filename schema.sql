-- Vocabulary table for 泉質 (spring quality) types.
CREATE TABLE IF NOT EXISTS spa_quality_types (
    id   SERIAL PRIMARY KEY,
    name TEXT   NOT NULL UNIQUE
);

-- Main table: one row per onsen spring.
-- Upsert key: detail_url (unique per source site).
CREATE TABLE IF NOT EXISTS onsen_springs (
    id           SERIAL       PRIMARY KEY,
    detail_url   TEXT         NOT NULL UNIQUE,
    name         TEXT         NOT NULL,
    prefecture   TEXT         NOT NULL,
    city         TEXT         NOT NULL,
    sales_point  TEXT,
    region_slug  TEXT         NOT NULL,
    source_url   TEXT         NOT NULL,
    fetched_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Junction: many-to-many between springs and quality types.
CREATE TABLE IF NOT EXISTS onsen_spring_qualities (
    spring_id           INTEGER NOT NULL REFERENCES onsen_springs(id)      ON DELETE CASCADE,
    spa_quality_type_id INTEGER NOT NULL REFERENCES spa_quality_types(id)  ON DELETE RESTRICT,
    PRIMARY KEY (spring_id, spa_quality_type_id)
);

CREATE INDEX IF NOT EXISTS idx_springs_prefecture  ON onsen_springs (prefecture);
CREATE INDEX IF NOT EXISTS idx_springs_region_slug ON onsen_springs (region_slug);
CREATE INDEX IF NOT EXISTS idx_spring_q_type_id    ON onsen_spring_qualities (spa_quality_type_id);
