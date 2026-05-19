"""Embed every place and upsert into places_vectors.

Reads each place's full profile, serializes to text, batch-embeds, and writes
to the vector store. Idempotent: skips rows where text_hash matches.
"""
from __future__ import annotations

import os
import time

import psycopg
from openai import OpenAI

from app.core.config import DSN, EMBEDDING_MODEL
from app.db.queries import (
    fetch_amenities,
    fetch_attributes,
    fetch_hours,
    fetch_place_core,
)
from app.services.embeddings import embed_many, serialize_place, text_hash

BATCH = 256


def gather_text(conn, place_id: str) -> str | None:
    core = fetch_place_core(conn, place_id)
    if not core:
        return None
    profile = {
        "primary_name": core["primary_name"],
        "brand_name": core["brand_name"],
        "name_local": core["name_local"],
        "category_name": core["category_name"],
        "formatted_address": core["formatted_address"],
        "amenities": fetch_amenities(conn, place_id),
        "attributes": fetch_attributes(conn, place_id),
        "hours": fetch_hours(conn, place_id),
    }
    return serialize_place(profile)


def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set")
    client = OpenAI()

    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id::text FROM places ORDER BY id")
            all_ids = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT place_id::text, text_hash FROM places_vectors")
            existing = {r[0]: r[1] for r in cur.fetchall()}

        total = len(all_ids)
        print(f"Embedding {total} places with model {EMBEDDING_MODEL}...")

        pending: list[tuple[str, str, str]] = []  # (id, text, hash)
        t0 = time.time()
        processed = 0
        skipped = 0

        for pid in all_ids:
            text = gather_text(conn, pid)
            if not text:
                continue
            h = text_hash(text)
            if existing.get(pid) == h:
                skipped += 1
                continue
            pending.append((pid, text, h))
            if len(pending) >= BATCH:
                _flush(conn, client, pending)
                processed += len(pending)
                pending = []
                print(f"  {processed + skipped}/{total} done ({skipped} cached)")

        if pending:
            _flush(conn, client, pending)
            processed += len(pending)

        print(f"Embedded {processed} places, skipped {skipped} unchanged, "
              f"in {time.time() - t0:.1f}s")


def _flush(conn, client: OpenAI, pending: list[tuple[str, str, str]]) -> None:
    texts = [t for (_, t, _) in pending]
    vectors = embed_many(client, texts)
    rows = []
    for (pid, _, h), vec in zip(pending, vectors):
        vec_lit = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
        rows.append((pid, vec_lit, h))
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO places_vectors (place_id, embedding, text_hash, updated_at)
            VALUES (%s, %s::vector, %s, now())
            ON CONFLICT (place_id) DO UPDATE
            SET embedding = EXCLUDED.embedding,
                text_hash = EXCLUDED.text_hash,
                updated_at = now()
            """,
            rows,
        )


if __name__ == "__main__":
    main()
