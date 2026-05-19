-- VOYGR replica schema (Phase 1 + selected Phase 2/3)

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- ---------- enums ----------

DO $body$ BEGIN
    CREATE TYPE place_status AS ENUM (
        'open', 'temporarily_closed', 'permanently_closed',
        'moved', 'rebranded', 'unverified'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $body$;

DO $body$ BEGIN
    CREATE TYPE source_type AS ENUM (
        'customer', 'web', 'social', 'directory', 'open_data', 'manual'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $body$;

DO $body$ BEGIN
    CREATE TYPE parking_type AS ENUM ('none', 'street', 'lot', 'garage');
EXCEPTION WHEN duplicate_object THEN NULL; END $body$;

DO $body$ BEGIN
    CREATE TYPE price_level AS ENUM ('$', '$$', '$$$', '$$$$');
EXCEPTION WHEN duplicate_object THEN NULL; END $body$;

DO $body$ BEGIN
    CREATE TYPE customer_ref_status AS ENUM ('matched', 'unmatched', 'low_confidence');
EXCEPTION WHEN duplicate_object THEN NULL; END $body$;

DO $body$ BEGIN
    CREATE TYPE attribute_value_type AS ENUM ('string', 'number', 'boolean', 'json');
EXCEPTION WHEN duplicate_object THEN NULL; END $body$;

-- ---------- categories ----------

CREATE TABLE IF NOT EXISTS place_categories (
    id          SERIAL PRIMARY KEY,
    parent_id   INTEGER REFERENCES place_categories(id) ON DELETE SET NULL,
    code        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    description TEXT
);

-- ---------- places ----------

CREATE TABLE IF NOT EXISTS places (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    primary_name             TEXT NOT NULL,
    name_local               TEXT,
    brand_name               TEXT,
    category_id              INTEGER REFERENCES place_categories(id),
    status                   place_status NOT NULL DEFAULT 'unverified',
    status_confidence        NUMERIC(4,3) NOT NULL DEFAULT 0.5,
    status_reason            TEXT,
    status_last_verified_at  TIMESTAMPTZ,
    location                 geography(Point, 4326) NOT NULL,
    primary_website_url      TEXT,
    phone_number             TEXT,
    email                    TEXT,
    country_code             TEXT,
    time_zone                TEXT,
    popularity_score         NUMERIC(4,3) NOT NULL DEFAULT 0.0,
    source_priority          TEXT
);

CREATE INDEX IF NOT EXISTS idx_places_location ON places USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_places_status ON places (status);
CREATE INDEX IF NOT EXISTS idx_places_category ON places (category_id);
CREATE INDEX IF NOT EXISTS idx_places_country ON places (country_code);
CREATE INDEX IF NOT EXISTS idx_places_name_lower ON places (lower(primary_name));

-- ---------- addresses ----------

CREATE TABLE IF NOT EXISTS place_addresses (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    place_id          UUID NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    formatted_address TEXT,
    street            TEXT,
    house_number      TEXT,
    city              TEXT,
    state             TEXT,
    postal_code       TEXT,
    country_code      TEXT,
    location          geography(Point, 4326),
    is_primary        BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_place_addresses_place ON place_addresses (place_id);

-- ---------- sources ----------

CREATE TABLE IF NOT EXISTS place_sources (
    id                SERIAL PRIMARY KEY,
    place_id          UUID NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    source_type       source_type NOT NULL,
    source_name       TEXT NOT NULL,
    source_url        TEXT,
    last_fetched_at   TIMESTAMPTZ,
    reliability_score NUMERIC(4,3) NOT NULL DEFAULT 0.5,
    raw_payload       JSONB,
    is_primary        BOOLEAN NOT NULL DEFAULT FALSE,
    status_signal     TEXT
);

CREATE INDEX IF NOT EXISTS idx_place_sources_place ON place_sources (place_id);

-- ---------- amenities ----------

CREATE TABLE IF NOT EXISTS place_amenities (
    place_id              UUID PRIMARY KEY REFERENCES places(id) ON DELETE CASCADE,
    wifi                  BOOLEAN,
    parking               parking_type,
    outdoor_seating       BOOLEAN,
    wheelchair_accessible BOOLEAN,
    vegan_options         BOOLEAN,
    pet_friendly          BOOLEAN,
    kids_friendly         BOOLEAN,
    price_level           price_level
);

-- ---------- hours ----------

CREATE TABLE IF NOT EXISTS place_hours (
    id           SERIAL PRIMARY KEY,
    place_id     UUID NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    day_of_week  SMALLINT NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    open_time    TIME NOT NULL,
    close_time   TIME NOT NULL,
    is_overnight BOOLEAN NOT NULL DEFAULT FALSE,
    is_holiday   BOOLEAN NOT NULL DEFAULT FALSE,
    valid_from   DATE,
    valid_to     DATE
);

CREATE INDEX IF NOT EXISTS idx_place_hours_place ON place_hours (place_id);

-- ---------- flexible attributes ----------

CREATE TABLE IF NOT EXISTS place_attributes (
    id              SERIAL PRIMARY KEY,
    place_id        UUID NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    namespace       TEXT NOT NULL,
    key             TEXT NOT NULL,
    value_type      attribute_value_type NOT NULL,
    value_string    TEXT,
    value_number    NUMERIC,
    value_boolean   BOOLEAN,
    value_json      JSONB,
    source_id       INTEGER REFERENCES place_sources(id) ON DELETE SET NULL,
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (place_id, namespace, key)
);

CREATE INDEX IF NOT EXISTS idx_place_attributes_place ON place_attributes (place_id);
CREATE INDEX IF NOT EXISTS idx_place_attributes_ns_key ON place_attributes (namespace, key);

-- ---------- status history ----------

CREATE TABLE IF NOT EXISTS place_status_history (
    id              SERIAL PRIMARY KEY,
    place_id        UUID NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    previous_status place_status,
    new_status      place_status NOT NULL,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    change_reason   TEXT,
    source_id       INTEGER REFERENCES place_sources(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_place_status_history_place ON place_status_history (place_id);

-- ---------- customers ----------

CREATE TABLE IF NOT EXISTS customer_accounts (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,
    api_key_hash TEXT NOT NULL UNIQUE,
    plan_tier    TEXT NOT NULL DEFAULT 'free',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customer_place_refs (
    id                SERIAL PRIMARY KEY,
    customer_id       UUID NOT NULL REFERENCES customer_accounts(id) ON DELETE CASCADE,
    customer_place_id TEXT NOT NULL,
    place_id          UUID REFERENCES places(id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_synced_at    TIMESTAMPTZ,
    status            customer_ref_status NOT NULL DEFAULT 'unmatched',
    UNIQUE (customer_id, customer_place_id)
);

-- ---------- feedback ----------

CREATE TABLE IF NOT EXISTS place_feedback (
    id           SERIAL PRIMARY KEY,
    place_id     UUID NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    customer_id  UUID REFERENCES customer_accounts(id) ON DELETE SET NULL,
    kind         TEXT NOT NULL,
    note         TEXT,
    payload      JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_place_feedback_place ON place_feedback (place_id);

-- ---------- vectors ----------

CREATE TABLE IF NOT EXISTS places_vectors (
    place_id   UUID PRIMARY KEY REFERENCES places(id) ON DELETE CASCADE,
    embedding  vector(1536) NOT NULL,
    text_hash  TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ivfflat needs ANALYZE + data before search; index creation is idempotent
CREATE INDEX IF NOT EXISTS idx_places_vectors_embedding
    ON places_vectors USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
