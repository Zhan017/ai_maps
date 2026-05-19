# Database schema

All tables defined in [`app/db/schema.sql`](../app/db/schema.sql). Three
Postgres extensions: **postgis** (spatial), **pgvector** (semantic search),
**pgcrypto** (`gen_random_uuid()`).

## Entity-relationship diagram

```mermaid
erDiagram
    PLACE_CATEGORIES ||--o{ PLACE_CATEGORIES : "parent_id (self-FK)"
    PLACE_CATEGORIES ||--o{ PLACES : "categorizes"

    PLACES ||--o{ PLACE_ADDRESSES : "has"
    PLACES ||--o| PLACE_AMENITIES : "1:1"
    PLACES ||--o{ PLACE_HOURS : "weekly schedule"
    PLACES ||--o| PLACES_VECTORS : "1:1 embedding"
    PLACES ||--o{ PLACE_SOURCES : "ingested from"
    PLACES ||--o{ PLACE_ATTRIBUTES : "flexible KV"
    PLACES ||--o{ PLACE_STATUS_HISTORY : "audit log"
    PLACES ||--o{ PLACE_FEEDBACK : "customer reports"
    PLACES ||--o{ CUSTOMER_PLACE_REFS : "mapped to canonical"

    PLACE_SOURCES ||--o{ PLACE_ATTRIBUTES : "attributed to source"
    PLACE_SOURCES ||--o{ PLACE_STATUS_HISTORY : "status change cited source"

    CUSTOMER_ACCOUNTS ||--o{ CUSTOMER_PLACE_REFS : "owns mapping"
    CUSTOMER_ACCOUNTS ||--o{ PLACE_FEEDBACK : "submitted by"

    PLACE_CATEGORIES {
        serial id PK
        int parent_id FK "self-FK for hierarchy"
        text code UK "e.g. 'cafe', 'restaurant'"
        text name
        text description
    }

    PLACES {
        uuid id PK
        timestamptz created_at
        timestamptz updated_at
        text primary_name "canonical name"
        text name_local "local-language name"
        text brand_name "chain identifier"
        int category_id FK
        place_status status "enum: open|temp_closed|perm_closed|moved|rebranded|unverified"
        numeric status_confidence "0-1, from validation formula"
        text status_reason
        timestamptz status_last_verified_at
        geography location "geography(Point, 4326)"
        text primary_website_url
        text phone_number
        text email
        text country_code
        text time_zone
        numeric popularity_score "0-1"
        text source_priority
    }

    PLACE_ADDRESSES {
        uuid id PK
        uuid place_id FK
        text formatted_address
        text street
        text house_number
        text city
        text state
        text postal_code
        text country_code
        geography location "may differ slightly from places.location"
        boolean is_primary "exactly one TRUE per place"
    }

    PLACE_SOURCES {
        serial id PK
        uuid place_id FK
        source_type source_type "enum: customer|web|social|directory|open_data|manual"
        text source_name "e.g. 'official_site', '2gis_kz'"
        text source_url
        timestamptz last_fetched_at "drives freshness_factor"
        numeric reliability_score "0-1, drives weighted_reliability"
        jsonb raw_payload "for debug"
        boolean is_primary
        text status_signal "e.g. 'active', 'closed', 'permanently_closed'"
    }

    PLACE_AMENITIES {
        uuid place_id PK_FK "1:1 with places"
        boolean wifi
        parking_type parking "enum: none|street|lot|garage"
        boolean outdoor_seating
        boolean wheelchair_accessible
        boolean vegan_options
        boolean pet_friendly
        boolean kids_friendly
        price_level price_level "enum: $|$$|$$$|$$$$"
    }

    PLACE_HOURS {
        serial id PK
        uuid place_id FK
        smallint day_of_week "0-6"
        time open_time
        time close_time
        boolean is_overnight "TRUE when close < open"
        boolean is_holiday
        date valid_from "temporary schedules"
        date valid_to
    }

    PLACE_ATTRIBUTES {
        serial id PK
        uuid place_id FK
        text namespace "e.g. 'vibe', 'audience', 'menu'"
        text key "e.g. 'quiet workspace'"
        attribute_value_type value_type "enum: string|number|boolean|json"
        text value_string
        numeric value_number
        boolean value_boolean
        jsonb value_json
        int source_id FK "which source asserted this"
        timestamptz last_updated_at
    }

    PLACE_STATUS_HISTORY {
        serial id PK
        uuid place_id FK
        place_status previous_status
        place_status new_status
        timestamptz changed_at
        text change_reason
        int source_id FK "optional"
    }

    PLACES_VECTORS {
        uuid place_id PK_FK "1:1 with places"
        vector embedding "1536-dim, text-embedding-3-small"
        text text_hash "sha1 of serialized place text"
        timestamptz updated_at
    }

    CUSTOMER_ACCOUNTS {
        uuid id PK
        text name
        text api_key_hash UK "bcrypt"
        text plan_tier
        timestamptz created_at
    }

    CUSTOMER_PLACE_REFS {
        serial id PK
        uuid customer_id FK
        text customer_place_id "the customer's internal ID"
        uuid place_id FK "our canonical ID"
        timestamptz created_at
        timestamptz last_synced_at
        customer_ref_status status "enum: matched|unmatched|low_confidence"
    }

    PLACE_FEEDBACK {
        serial id PK
        uuid place_id FK
        uuid customer_id FK
        text kind "e.g. 'closed', 'wrong_address'"
        text note
        jsonb payload
        timestamptz created_at
    }
```

> If the diagram doesn't render in your viewer, it's a [Mermaid ER
> diagram](https://mermaid.js.org/syntax/entityRelationshipDiagram.html) —
> GitHub renders this natively in Markdown.

---

## Design philosophy

Three principles drive the schema:

1. **One canonical place per real-world place.** `places.id` is the only
   stable identity. Every other table either describes the place
   (`addresses`, `hours`, `amenities`) or records *who said what about it*
   (`sources`, `status_history`, `feedback`). Customers map their internal
   IDs to ours via `customer_place_refs` — they never alter the canonical
   row directly.

2. **Provenance is a first-class concern.** `place_sources` exists because
   in the VOYGR product space, *which directory said this place was open
   on which date* is more important than the place attributes themselves.
   Both `place_attributes` and `place_status_history` carry a `source_id`
   so every claim is traceable.

3. **Flexibility through `place_attributes`.** Hours and amenities have
   typed columns because they're predictable. Everything else
   ("popular_with_yc_founders", "has_vintage_pinball") lives in
   `place_attributes` as `{namespace, key, value_*}` with a foreign key
   back to the source. No schema migrations needed for new attributes.

---

## Table-by-table walkthrough

### `place_categories` — hierarchical taxonomy

```
id PK · parent_id FK→self · code UNIQUE · name · description
```

**What it does**: a 2-level tree. Three root nodes (`food_and_drink`,
`services`, `tourism`); leaves are the actual categories used everywhere
else (`cafe`, `restaurant`, `bar`, `fast_food`, `pharmacy`, `bank`, `atm`,
`attraction`, `museum`, `hotel`, `viewpoint`, `guest_house`).

**Why a self-FK instead of two tables**: simpler queries, supports
arbitrary depth if needed, and matches how OSM/Foursquare/Yelp all model
their category trees.

**Why `code` is the join key everywhere**: stable, human-readable, won't
re-number if categories are renamed. The matching engine compares against
`code`; the seed maps from OSM tags to `code`.

**Indexes**: PK on `id`, UNIQUE on `code`.

---

### `places` — the canonical entity

```
id UUID PK · primary_name · category_id FK
status · status_confidence · status_reason · status_last_verified_at
location geography(Point, 4326)
primary_website_url · phone_number · email
country_code · time_zone · popularity_score
created_at · updated_at
```

**The center of everything**. UUID PK so external systems can hold
references without leaking serial counts.

**Why `geography` not `geometry`**: `geography(Point, 4326)` lives on the
sphere; `ST_Distance(a, b)` returns **meters** by default with no
projection math. With `geometry` you'd be in degrees and would have to
either project to a meters CRS (e.g., 3857) or cast on every distance
call. For city-scale projects either works; for a *global* place index
geography is correct.

**Why `status_confidence` lives here, not on `place_status_history`**:
this is the *current* confidence; history shows the trail. Reading
`places.status_confidence` is the fast path; reading
`place_status_history` is the audit path.

**Indexes**:
- `idx_places_location` — GIST on `location`. Makes `ST_DWithin`,
  `ST_Distance` fast for radius queries.
- `idx_places_status`, `idx_places_category`, `idx_places_country` — btree
  singles for filter selectivity.
- `idx_places_name_lower` — btree on `lower(primary_name)`, for fallback
  name-only candidate generation when the matcher gets no coordinates.

**Constraints worth noting**: `location` is NOT NULL — every place must
have coordinates. `status` defaults to `unverified`. `category_id` is
nullable (no FK constraint enforcement on category — categories may exist
that haven't been mapped yet).

---

### `place_addresses` — separated for moves and disagreements

```
id UUID PK · place_id FK · formatted_address · street · house_number
city · state · postal_code · country_code · location · is_primary
```

**Why a separate table**: addresses can change without the place identity
changing (building renumbering, street renaming, moves within the same
business). Also, multiple sources may disagree about the address — the
schema supports multiple `place_addresses` rows per place, with one
flagged `is_primary = true`.

**Cardinality**: many-to-one with `places`. In the current seed every
place has exactly one address with `is_primary = true`.

**Why `location` is duplicated here**: in production with multi-source
ingestion, sources may report slightly different coordinates for the
same place. `places.location` is the canonical chosen location;
`place_addresses.location` is what each source individually reported.

**Indexes**: btree on `place_id` for the typical join.

---

### `place_sources` — the provenance backbone

```
id SERIAL PK · place_id FK · source_type ENUM · source_name · source_url
last_fetched_at · reliability_score · raw_payload JSONB
is_primary · status_signal
```

**This is the key table** for the "is this place still open?" problem.
Each row says: *at `last_fetched_at`, source `source_name` (with
`reliability_score`) saw place X and reported `status_signal`*. The
validation engine reads `place_sources` to compute
`places.status_confidence` via a documented formula:

```
status_confidence
  = 0.4 · source_agreement      # frac of sources whose status_signal isn't 'closed'
  + 0.4 · weighted_reliability  # mean reliability among agreeing sources
  + 0.2 · freshness_factor      # exp(-min_days_since_last_fetched / 30)
```

**Why `reliability_score` is per-source not per-source-type**: a specific
2GIS scrape can be more or less reliable than the generic 2GIS catalog
entry depending on how it was fetched.

**`status_signal` values** (free-text by design, but seeded as):
`"active"`, `"closed"`, `"permanently_closed"`. New sources can introduce
new signals without an enum migration.

**`raw_payload` JSONB**: original API response, kept for debugging and
reprocessing. Empty in the current seed; in production every fetcher
would dump the raw response here.

**Indexes**: btree on `place_id` for the validation engine's per-place
fetch. No index on `last_fetched_at` because the freshness scheduler
will scan with a different access pattern (filtered by `next_check_at`,
not by source).

---

### `place_amenities` — 1:1 typed booleans

```
place_id UUID PK_FK · wifi · parking · outdoor_seating · wheelchair_accessible
vegan_options · pet_friendly · kids_friendly · price_level
```

**Why 1:1**: amenities are the *most* common queryable attributes
("has wifi", "outdoor seating"). Keeping them as actual columns lets the
search layer filter via `JOIN` instead of an `EXISTS` subquery against
`place_attributes`. Hot-path optimization.

**Why `place_id` is the PK (not a separate `id`)**: enforces 1:1 — one
amenities row per place. `ON DELETE CASCADE` keeps it consistent when
places are removed.

**Why most fields are nullable**: NULL means "we don't know" (vs `false`
which means "we know it doesn't have wifi"). This matters for `open_now`
+ `wifi` queries — we don't want to surface a cafe just because its
`wifi` field is NULL.

---

### `place_hours` — weekly schedule with overnight handling

```
id PK · place_id FK · day_of_week 0-6 · open_time · close_time
is_overnight · is_holiday · valid_from · valid_to
```

**Why a row per (place, day_of_week)** instead of one wide row with 7
ranges: places can have multiple ranges per day (split shifts), holiday
overrides, temporary schedules. The flexible schema handles all of them
without a migration.

**`is_overnight = TRUE`**: convention for `close_time < open_time` (bar
opens 17:00, closes 02:00 next day). The `open_now` check in
`app/services/search.py` handles this:

```sql
(NOT h.is_overnight AND h.open_time <= NOW_TIME AND NOW_TIME < h.close_time)
  OR (h.is_overnight AND (NOW_TIME >= h.open_time OR NOW_TIME < h.close_time))
```

**`valid_from`/`valid_to`** are seeded as NULL — they exist for future
"hours change next Monday" support.

**Indexes**: btree on `place_id` for per-place fetch.

---

### `place_attributes` — the "infinite attributes" store

```
id PK · place_id FK · namespace · key · value_type
value_string · value_number · value_boolean · value_json
source_id FK · last_updated_at
UNIQUE (place_id, namespace, key)
```

**The flexible KV table**. Lets you record arbitrary facts about places
without schema migrations. Examples from the seed:

- `(namespace='vibe', key='quiet workspace', value_boolean=true)`
- `(namespace='audience', key='popular with students', value_boolean=true)`

**The four `value_*` columns + `value_type` enum**: one is filled per
row matching `value_type`. This pattern (one-of typed columns vs single
JSONB) trades storage for queryability — you can index on `value_string`
or `value_boolean` columns separately, which JSONB filtering can't match
for performance.

**`UNIQUE (place_id, namespace, key)`**: prevents duplicates and allows
`INSERT … ON CONFLICT … DO UPDATE` upserts when re-ingesting.

**`source_id` FK to `place_sources`**: traceability. Every attribute
claim can be traced to which source asserted it. `ON DELETE SET NULL` so
deleting a source orphans the attribute but doesn't lose the value.

**Indexes**: btree on `place_id`, composite on `(namespace, key)` for
queries like "find all places with `vibe.quiet workspace = true`".

---

### `place_status_history` — append-only audit log

```
id PK · place_id FK · previous_status · new_status
changed_at · change_reason · source_id FK
```

**What it does**: every time `places.status` flips, a row gets written
here (by `validation._persist()` in `app/services/validation.py`).

**Why we keep it**: the answer to "how do you know this place closed?"
is "here's the row in `place_status_history` with the source ID that
triggered the change."

**Why `previous_status` is nullable**: the first status row for a place
has no previous status.

**Indexes**: btree on `place_id`. No index on `changed_at` yet because
queries are typically scoped to one place.

---

### `places_vectors` — semantic search

```
place_id UUID PK_FK · embedding vector(1536) · text_hash · updated_at
```

**1:1 with places** so the search SQL can join cleanly.

**Why dimension 1536**: matches OpenAI's `text-embedding-3-small`. If we
switched to `text-embedding-3-large` (3072) or BGE-base (768) we'd need
a new table or a migration.

**Why `text_hash`**: idempotency. `scripts/build_embeddings.py` serializes
each place to text (`"name | category | address | amenities | vibe |
hours summary"`), hashes the text, and skips re-embedding if the hash
matches. Re-running on unchanged data is a no-op.

**Index**: `ivfflat (embedding vector_cosine_ops) WITH (lists = 100)`.
- `ivfflat` = inverted-file-flat, partitions vectors into clusters for
  approximate-NN search
- `lists = 100` ≈ √N at our scale (1158 places); rule of thumb for
  pgvector ivfflat
- `vector_cosine_ops` = cosine distance operator; matched in SQL by
  `embedding <=> query_vector`
- At 1M+ vectors we'd switch to HNSW (`hnsw` access method)

**Why `places_vectors` not `place_vectors`**: pluralization mismatch
with the rest of the schema, kept to match the spec at `docs/voygr_replica.md`
§3.

---

### `customer_accounts` and `customer_place_refs` — multi-tenancy

**`customer_accounts`**: API key per customer, stored as bcrypt hash.
Currently bypassed in demo mode (see `app/core/security.py`); set
`REQUIRE_API_KEY=1` to enforce.

**`customer_place_refs`**: when a customer calls `:validate-enrich` with
their own `customer_place_id`, this table records the mapping from their
ID to our canonical UUID. So Customer A can refer to "store_4823" forever
and we always know which canonical place that means.

`UNIQUE (customer_id, customer_place_id)` enforces one mapping per
customer-side ID. `ON CONFLICT … DO UPDATE` re-runs are idempotent.

`status` enum (`matched`/`unmatched`/`low_confidence`) lets the customer
see at a glance which of their places landed in our canonical store.

---

### `place_feedback` — customer-submitted corrections

```
id PK · place_id FK · customer_id FK · kind · note · payload JSONB · created_at
```

When a customer submits via `POST /v1/places/{id}:feedback`, the payload
lands here. Production would trigger a revalidation job for the place.

**Why `kind` is text not enum**: feedback types evolve (`closed`,
`wrong_address`, `wrong_name`, `wrong_hours`, etc.). No enum migration
needed when new feedback shapes appear.

`customer_id` is nullable + `ON DELETE SET NULL` so deleting a customer
doesn't erase historical feedback data.

---

## Enums summary

| Enum | Values | Used in |
|---|---|---|
| `place_status` | `open`, `temporarily_closed`, `permanently_closed`, `moved`, `rebranded`, `unverified` | `places.status`, `place_status_history.{previous,new}_status` |
| `source_type` | `customer`, `web`, `social`, `directory`, `open_data`, `manual` | `place_sources.source_type` |
| `parking_type` | `none`, `street`, `lot`, `garage` | `place_amenities.parking` |
| `price_level` | `$`, `$$`, `$$$`, `$$$$` | `place_amenities.price_level` |
| `customer_ref_status` | `matched`, `unmatched`, `low_confidence` | `customer_place_refs.status` |
| `attribute_value_type` | `string`, `number`, `boolean`, `json` | `place_attributes.value_type` |

All enum DDLs use `DO $body$ … EXCEPTION WHEN duplicate_object` so
re-running the schema script is idempotent. The `$body$` dollar-quote tag
(rather than `$$`) is necessary because `price_level` enum literals
contain `$$$$` which would otherwise be parsed as nested dollar-quotes.

---

## How tables get filled (data flow)

```
                     scripts/fetch_osm.py
                              │
                              ▼
                  data/osm_places.json (~1k rows)
                              │
                              ▼
                    scripts/seed.py --source osm
                              │
                ┌─────────────┼─────────────┬─────────────┬─────────────┐
                ▼             ▼             ▼             ▼             ▼
            places    place_addresses   place_sources  place_hours   place_amenities
                                                                          + place_attributes
                              │
                              ▼
                  scripts/build_embeddings.py
                              │
                              ▼
                       places_vectors

At request time:

  POST /v1/places:validate-enrich
        │
        ├─→ matching.match()       → reads places, place_addresses, place_categories
        ├─→ validation.classify()  → reads place_sources, writes places, place_status_history
        ├─→ enrichment.full_profile() → reads everything
        └─→ writes customer_place_refs

  GET /v1/places:search
        │
        ├─→ structured: reads places + amenities + hours + categories
        └─→ semantic: also reads places_vectors

  POST /v1/places/{id}:feedback
        │
        └─→ writes place_feedback
```

---

## Why some "normal" tables aren't here yet

Per the spec at `docs/voygr_replica.md`, two tables exist in the design
but aren't created in this schema:

- **`place_relationships`** — graph edges for rebrand/move/same-owner
  relations. Not built because the demo doesn't need it; matching would
  use it for brand-aware false-positive rejection (mentioned in
  `docs/entity_resolution.md` failure modes).
- **`place_recrawl_plan`** — the freshness scheduler's queue (next check
  time per place). Not built because there's no scheduler / worker
  process yet.

Both are noted in the roadmap.
