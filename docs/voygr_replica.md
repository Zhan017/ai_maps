# VOYGR Replica – Technical Specification (FastAPI + Postgres + PostGIS)

This document describes a full architecture and implementation plan to build a
VOYGR‑like product: an API that validates, enriches, and serves high‑fidelity
place (POI) data for AI apps, agents, and analytics.[web:2][web:9][web:14]

---

## 1. Product Goals and Scope

### 1.1. Core value proposition

- Provide an API for companies to validate and enrich their place records (POIs)
  at scale.[web:2][web:14]
- Deliver comprehensive, up‑to‑date information about places and local
  businesses, including foundational attributes (address, category, contacts,
  web presence) and operating data (hours, features, amenities, menus,
  prices).[web:2][web:9]
- Keep place data accurate and current at scale by confirming what’s live,
  detecting closures, rebrands, and moves, and adding rich context from web,
  social, news, and events.[web:2][web:11][web:14]
- Expose a “better maps API” for AI apps and agents, with richer attributes and
  real‑time place intelligence than standard mapping
  providers.[web:2][web:9][web:15]

### 1.2. Target use cases

- Validating large internal merchant or business lists (banks, payment
  processors, marketplaces).[web:2][web:14]
- Enriching transaction data, CRM records, and property datasets with canonical
  place data.[web:2]
- Powering local search, discovery, AR, and agent reasoning over real‑world
  places (“maps tag places — VOYGR understands”).[web:9][web:15]
- Ads measurement and attribution, site selection, sales territory
  planning.[web:2]

### 1.3. Non‑goals (for initial version)

- Real‑time routing or turn‑by‑turn navigation.
- Full consumer map UI.
- Global 100% coverage from day one (start with a geography + category focus).

---

## 2. High‑Level Architecture

### 2.1. Components

- **API Layer (FastAPI)**
  - Public REST/JSON API for ingestion, search, and retrieval.
  - Internal admin and moderation endpoints.

- **Core Database (Postgres + PostGIS)**
  - Canonical place store (normalized schema).
  - Spatial indexing and geospatial queries.

- **Search & Semantic Layer**
  - Text and semantic search (e.g., OpenSearch/Elasticsearch or pg\_trgm +
    embeddings).
  - Vector store for place embeddings (e.g., pgvector extension or external
    vector DB).

- **Data Ingestion & ETL**
  - Pipelines to ingest customer POIs.
  - Pipelines to ingest and normalize external sources (web, social,
    directories, open data).

- **Validation & Status Engine**
  - Multi‑source status detection (open, closed, moved, rebranded).
  - Confidence scoring and change‑detection.

- **Enrichment Engine**
  - Attribute extraction (hours, amenities, menus, prices, etc.).[web:2][web:9]
  - Schema for “infinite attributes” (flexible document structure).

- **Freshness Scheduler & Worker Queue**
  - Recrawl and revalidation schedule based on volatility and customer
    needs.[web:2][web:11]
  - Worker services running crawlers, scrapers, and classifiers.

- **Monitoring, Observability, and Admin UI**
  - Metrics, error tracking, and manual override tools.

### 2.2. Data flow

1. Customer sends a batch of places via API.
2. Matching engine tries to map each input to a canonical place.
3. Validation engine checks existence and operating status using external
   signals.[web:2][web:11]
4. Enrichment engine aggregates and normalizes attributes from trusted
   sources.[web:2][web:9]
5. Results are stored in Postgres/PostGIS and exposed via search/read APIs.
6. Scheduler periodically re‑checks places and updates records.

---

## 3. Data Model (Postgres + PostGIS)

### 3.1. Core tables

#### `places`

Canonical representation of a place.

- `id` (UUID, PK) – canonical place ID.
- `created_at`, `updated_at`.
- `primary_name` – normalized main name.
- `name_local` – local/alternate name.
- `brand_name` – chain/brand if applicable.
- `category_id` – FK to `place_categories`.
- `status` – enum: `open`, `temporarily_closed`, `permanently_closed`, `moved`,
  `rebranded`, `unverified`.
- `status_confidence` – numeric (0–1).
- `status_reason` – short description.
- `status_last_verified_at` – timestamp.
- `location` – `geometry(Point, 4326)` (PostGIS).
- `address_id` – FK to `place_addresses`.
- `primary_website_url`.
- `phone_number`, `email`.
- `country_code` (ISO), `time_zone`.
- `popularity_score` – numeric, aggregated metric.
- `source_priority` – text/JSON to track primary data source.

Indexes:

- GIST index on `location`.
- Btree index on `status`, `country_code`, `category_id`.
- Full‑text index on `primary_name` + address fields (optional).

#### `place_addresses`

- `id` (UUID, PK).
- `place_id` (FK).
- `formatted_address`.
- `street`, `house_number`, `city`, `state`, `postal_code`, `country_code`.
- `location` – `geometry(Point, 4326)` (in case of multiple location points).
- `is_primary` – boolean.

#### `place_categories`

- `id` (PK).
- `parent_id` – FK to `place_categories` for hierarchy.
- `code` – internal code (e.g., `coffee_shop`).
- `name`.
- `description`.

#### `place_sources`

Track data sources per place.

- `id` (PK).
- `place_id` (FK).
- `source_type` – enum: `customer`, `web`, `social`, `directory`, `open_data`,
  `manual`.
- `source_name` – e.g., `google_business`, `yelp`, `instagram`.
- `source_url`.
- `last_fetched_at`.
- `reliability_score` – numeric (0–1).
- `raw_payload` – JSONB (optional, for debug).
- `is_primary` – boolean.

#### `place_amenities`

Structured amenities.

- `place_id` (FK).
- `wifi` (boolean).
- `parking` (enum: `none`, `street`, `lot`, `garage`).
- `outdoor_seating` (boolean).
- `wheelchair_accessible` (boolean).
- `vegan_options` (boolean).
- `pet_friendly` (boolean).
- `kids_friendly` (boolean).
- `price_level` – enum (e.g., `$`, `$$`, `$$$`).
- Additional common structured fields.

#### `place_hours`

- `id` (PK).
- `place_id` (FK).
- `day_of_week` – 0–6.
- `open_time` – time.
- `close_time` – time.
- `is_overnight` – boolean.
- `is_holiday` – boolean.
- `valid_from`, `valid_to` – date range for temporary schedules.

#### `place_attributes`

Flexible “infinite attributes” store (key‑value).

- `id` (PK).
- `place_id` (FK).
- `namespace` – e.g., `operating`, `vibe`, `audience`, `menu`, `custom`.
- `key` – string (e.g., `has_vintage_pinball`, `popular_with_yc_founders`).
- `value_type` – enum: `string`, `number`, `boolean`, `json`.
- `value_string`, `value_number`, `value_boolean`, `value_json`.
- `source_id` (FK to `place_sources`).
- `last_updated_at`.

#### `place_status_history`

Track changes.

- `id` (PK).
- `place_id` (FK).
- `previous_status`, `new_status`.
- `changed_at`.
- `change_reason`.
- `source_id` (optional).

#### `place_relationships`

Graph edges between places and entities (for rebrands, moves, notable visitors,
etc.).

- `id` (PK).
- `from_place_id` (FK).
- `to_place_id` (FK) or `entity_id` (for non‑place entities if you model them).
- `relation_type` – e.g., `rebranded_from`, `moved_from`, `same_owner_as`,
  `frequented_by`.
- `confidence`.
- `source_id`.
- `last_updated_at`.

#### `customer_accounts` and `customer_place_refs`

To support multiple clients.

- `customer_accounts`
  - `id`, `name`, `api_key_hash`, `plan_tier`, etc.

- `customer_place_refs`
  - `id`.
  - `customer_id` (FK).
  - `customer_place_id` – their internal ID.
  - `place_id` (FK to `places`).
  - `created_at`, `last_synced_at`.
  - `status` – mapping status (e.g., `matched`, `unmatched`, `low_confidence`).

---

## 4. API Design (FastAPI)

### 4.1. Authentication

- API key authentication using headers: `X-API-Key`.
- Rate limiting per key (via middleware or API gateway).
- Optional JWT if you want user‑level auth.

### 4.2. Public API endpoints

#### 4.2.1. Batch validation & enrichment

`POST /v1/places:validate-enrich`

Request body:

```json
{
    "places": [
        {
            "customer_place_id": "12345",
            "name": "Brew Lab Coffee",
            "address": "123 Market St, San Francisco, CA",
            "lat": 37.7901,
            "lng": -122.401,
            "category": "coffee_shop",
            "website": "https://brewlab.example",
            "phone": "+1-415-123-4567",
            "country_code": "US"
        }
    ],
    "options": {
        "enrich": true,
        "validate_status": true,
        "return_full_profile": false
    }
}
```

Response:

```json
{
    "results": [
        {
            "customer_place_id": "12345",
            "canonical_place_id": "uuid-...",
            "match_confidence": 0.96,
            "status": "open",
            "status_confidence": 0.93,
            "status_reason": "Active on website and social as of 2026-05-15",
            "status_last_verified_at": "2026-05-15T10:30:00Z",
            "attributes": {
                "primary_name": "Brew Lab Coffee",
                "formatted_address": "123 Market St, San Francisco, CA 94105, USA",
                "category": "coffee_shop",
                "country_code": "US",
                "location": {
                    "lat": 37.7901,
                    "lng": -122.401
                },
                "website": "https://brewlab.example",
                "phone": "+1-415-123-4567",
                "hours": [
                    {
                        "day_of_week": 1,
                        "open_time": "07:00",
                        "close_time": "18:00"
                    }
                ],
                "amenities": {
                    "wifi": true,
                    "outdoor_seating": true,
                    "price_level": "$$"
                }
            }
        }
    ]
}
```

#### 4.2.2. Place lookup by ID

`GET /v1/places/{place_id}`

- Returns full canonical profile, including infinite attributes.
- Query parameters: `include_sources`, `include_history`.

#### 4.2.3. Search places (structured + semantic)

`GET /v1/places:search`

Query parameters example:

- `q`: free‑text or natural‑language query (e.g.,
  `"Specialty coffee with Wi-Fi, popular with YC founders in SF"`).[web:2]
- `lat`, `lng`, `radius_m`.
- `category`.
- `open_now` (boolean).
- `country_code`.
- `limit`, `offset`.

Response example:

```json
{
    "results": [
        {
            "place_id": "uuid-...",
            "primary_name": "Brew Lab Coffee",
            "formatted_address": "123 Market St, San Francisco, CA 94105, USA",
            "location": { "lat": 37.7901, "lng": -122.401 },
            "distance_m": 230,
            "status": "open",
            "score": 0.87,
            "reasons": [
                "matches category coffee_shop",
                "has_wifi",
                "popular_with_yc_founders",
                "open_now"
            ],
            "freshness": {
                "profile_last_updated_at": "2026-05-15T10:30:00Z",
                "status_last_verified_at": "2026-05-15T10:30:00Z"
            }
        }
    ]
}
```

#### 4.2.4. Batch status check

`POST /v1/places:status`

- Accepts list of canonical `place_id`s, returns status + freshness only.
- Optimized for agents that just need “is this place live and open right now?”.

#### 4.2.5. Customer feedback / corrections

`POST /v1/places/{place_id}:feedback`

- Allows customers to submit corrections (e.g., “this place is closed”, “wrong
  address”).
- Payload stored for review and can trigger revalidation.

### 4.3. Admin / internal API

- `GET /internal/places/{place_id}/debug` – diagnostics (matching scores, source
  data).
- `POST /internal/places/{place_id}/override` – manual overrides of status,
  attributes.
- `POST /internal/recrawl/queue` – trigger recrawl for places.
- `GET /internal/metrics` – pipeline metrics.

---

## 5. Matching & Canonicalization

### 5.1. Matching algorithm

1. **Candidate generation**
   - Radius search in PostGIS around input coordinates or geocoded address.
   - Filter by country, city, and category where available.

2. **Scoring features**
   - Name similarity (token Jaccard, Levenshtein, or embeddings).
   - Address component similarity (street, house number, postal code).
   - Phone and website equality matches.
   - Distance score based on geodesic distance.
   - Category compatibility.

3. **Model**
   - Simple weighted linear model to start.
   - Optionally upgrade to ML model trained on labeled match / non‑match pairs.

4. **Decision**
   - If best score ≥ `HIGH_THRESHOLD`: accept as match.
   - If `LOW_THRESHOLD ≤ score < HIGH_THRESHOLD`: mark as low confidence; return
     but flag.
   - Else: create new `places` record (`status = unverified`).

5. **Canonical name and address selection**
   - Apply source reliability and recency weighting to decide primary name,
     address, and website.

---

## 6. Validation & Status Detection

### 6.1. External signal collection

- For each place, maintain a list of `place_sources` with `source_name`,
  `source_url`, and `raw_payload`.
- Sources: official website, major directories, social profiles, and relevant
  open data.
- Crawl or fetch data periodically using a scheduler and worker processes.

### 6.2. Status classifier

Inputs:

- Presence / absence of place on major directories.
- Explicit status labels (“permanently closed”, “temporarily closed”, “moved”).
- Last update timestamps.
- Website content (e.g., “We closed on…”, “We moved to…”).
- Activity signals (posts, events, menu updates).

Logic:

- Rules for clear signals (e.g., platform flag “permanently closed” with high
  reliability).
- ML/LLM classifier for ambiguous cases (text analysis from website and posts).
- Generate `status`, `status_confidence`, `status_reason`,
  `status_last_verified_at`.
- Log in `place_status_history`.

### 6.3. Rebrands and moves

- Compare new vs previous names and addresses.
- If name changes but website and social handles remain similar → `rebranded`.
- If address changes significantly but same brand and web properties → `moved`.
- Record graph edges in `place_relationships`.

---

## 7. Enrichment Engine

### 7.1. Source discovery

- Auto‑discover URLs based on name + address + website domain.
- Search queries to find official pages on major directories and social
  platforms.
- Cache discovered URLs in `place_sources`.

### 7.2. Crawling & scraping

- Implement crawlers with politeness (rate limiting, robots.txt checks).
- Extract:
  - Structured microdata (JSON‑LD, microdata, OpenGraph).
  - Unstructured HTML content.
  - API responses when available.

### 7.3. Attribute extraction

- **Foundational attributes**
  - Use JSON‑LD, meta tags, and contact pages for addresses, phones, emails,
    websites.

- **Hours and operating patterns**
  - Parse structured hours in microdata or HTML tables.
  - Fallback to LLM extraction for difficult formats.

- **Amenities and features**
  - Keyword and LLM extraction over text (“free Wi‑Fi”, “outdoor seating”, “pet
    friendly”).
  - Normalize into `place_amenities` and `place_attributes`.

- **Menus & prices**
  - Detect menu links.
  - Parse items, categories, price ranges; store in `place_attributes`
    (namespace `menu`).
  - Derive price_level (`$`, `$$`, etc.) based on median prices.

- **Context labels (“vibe”, audience)**
  - Use embeddings and classification to tag places (“popular with students”,
    “date night”, “startup crowd”).
  - Represent as `place_attributes` in namespace `vibe` or `audience`.
  - This enables queries like “places popular with YC founders in SF”.[web:2]

### 7.4. Conflict resolution

- Maintain `reliability_score` per source.
- Tune heuristics: official website > central directories > social > unverified
  blogs.
- Choose final value based on recency and reliability; keep alternatives in
  `place_attributes` or `place_sources.raw_payload`.

---

## 8. Freshness & Recrawl Scheduling

### 8.1. Scheduling model

- Category‑based recrawl intervals:
  - Restaurants, bars, cafes: days.
  - Retail shops: weeks.
  - Corporate offices, banks: months.
- Activity‑based adaptation:
  - If many changes detected recently, increase frequency.
  - If very stable, decrease.

Store in, e.g., `place_recrawl_plan`:

- `place_id`.
- `next_status_check_at`.
- `next_enrichment_at`.
- `priority`.

### 8.2. Worker architecture

- Use a queue system (e.g., Redis + RQ/Celery).
- Jobs: `fetch_source`, `parse_source`, `run_status_classifier`,
  `run_enrichment`.
- Workers run as separate Python processes.

### 8.3. Freshness metrics

- Compute internal KPIs:
  - Percentage of active places verified in last N days by category and country.
  - Mean time between updates per place.
- Store snapshots or compute through queries + metrics exporter (Prometheus).

---

## 9. Search & Semantic Layer

### 9.1. Structured search

- Use PostGIS for geo filters: `ST_DWithin(location, user_point, radius)`.
- Filter by category, status, opening hours:

Example query logic:

- Filter `status = 'open'`.
- Filter `country_code = 'US'`.
- Filter by `open_now`: join with `place_hours` and check current day/time in
  place’s time zone.

### 9.2. Semantic search

- Precompute embeddings for:
  - Place descriptions (aggregated text from website, menus, reviews,
    attributes).
  - Attribute strings (e.g., `["specialty coffee", "wifi", "quiet workspace"]`).
- Store vector in `places_vectors` table (pgvector) or external store.
- For query `q`, compute query embedding and run ANN search to get candidate
  places.
- Combine semantic score with structured filters (distance, category, status,
  popularity).

### 9.3. Agent‑friendly responses

- Include machine‑interpretable `reasons` for ranking.
- Include `freshness` object with timestamps per key attribute.
- Support `fields` parameter to limit payload for performance.

---

## 10. Infrastructure & DevOps

### 10.1. Services

- `api` – FastAPI app.
- `workers` – background jobs.
- `crawler` – external‑facing fetcher (behind proxy if needed).
- `search` – search/semantic service (can be part of workers initially).

### 10.2. Environments

- `dev` – local (Docker Compose).
- `staging` – integration testing, external sandbox API keys.
- `prod` – multi‑AZ deployment (Kubernetes or VM‑based).

### 10.3. Observability

- Logging: structured logs (JSON) with request IDs and customer IDs.
- Metrics: Prometheus + Grafana (request latency, error rate, worker throughput,
  recrawl backlog).
- Tracing: OpenTelemetry.

### 10.4. Security & compliance

- API key management and rotation.
- TLS everywhere.
- Basic PII handling for contact details.
- Robots.txt respect and compliance with terms of use for external sources.

---

## 11. Implementation Roadmap

### Phase 1 – Core MVP

- Implement `places`, `place_addresses`, `place_categories`,
  `customer_accounts`, `customer_place_refs`.
- Build `POST /v1/places:validate-enrich` minimal version:
  - Deterministic + fuzzy matching.
  - Mark status as `unverified` initially.
- Build `GET /v1/places/{place_id}` and basic `GET /v1/places:search` (name +
  distance).
- Add admin endpoints for manual corrections.

### Phase 2 – Validation & Enrichment

- Add `place_sources`, `place_amenities`, `place_hours`, `place_attributes`,
  `place_status_history`, `place_relationships`.
- Introduce crawler and ETL pipeline for a small set of sources (website + one
  directory).
- Implement status classifier and assign `status`, `status_confidence`,
  `status_reason`.
- Extract hours and basic amenities; surface them via API.
- Add recrawl scheduler and workers.

### Phase 3 – Semantic search & “infinite attributes”

- Add vector store and embeddings.
- Aggregate place text (site snippets, attributes) and compute embeddings.
- Implement semantic search blending with structured filters.
- Expand attribute extraction to menus, prices, vibe/audience.
- Provide richer `reasons` and `freshness` metadata for agents.

### Phase 4 – Hardening & scaling

- Optimize indexes, query performance, and caching.
- Improve ML models for matching and status classification.
- Expand geographic footprint and category coverage.
- Add rate limiting, billing, usage analytics.

---

## 12. FastAPI Project Structure (Example)

```text
app/
  main.py
  api/
    v1/
      endpoints/
        places.py
        search.py
        status.py
        feedback.py
  core/
    config.py
    security.py
  db/
    base.py
    session.py
    models/
      places.py
      addresses.py
      categories.py
      sources.py
      attributes.py
      customers.py
  services/
    matching.py
    validation.py
    enrichment.py
    search.py
    recrawl.py
  workers/
    tasks.py
  utils/
    geo.py
    timezones.py
    scraping.py
tests/
  ...
```

---

## 13. Key Technical Decisions & Tradeoffs

- **Postgres + PostGIS** is ideal for canonical store and geo queries; you can
  keep search in Postgres initially and move to a dedicated search engine as
  complexity grows.
- **pgvector** allows keeping semantic search in the same DB; external vector
  DBs can be introduced later.
- **Flexible attributes** via `place_attributes` let you mirror VOYGR’s
  “infinite, queryable place profiles” without constant schema
  migrations.[web:2]
- **Worker‑based pipelines** are required to support continuous validation and
  enrichment at scale, aligning with VOYGR’s promise of fresh place
  data.[web:2][web:11][web:15]
