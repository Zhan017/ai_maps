"""OpenAI embedding wrapper + place-text serializer.

We build a short denormalized string per place (name, category, amenities,
vibe attrs, hours summary) and embed that. The same model is used for query
embeddings at search time.
"""
from __future__ import annotations

import hashlib
from typing import Iterable

from openai import OpenAI

from app.core.config import EMBEDDING_MODEL


def serialize_place(profile: dict) -> str:
    parts: list[str] = []
    if profile.get("primary_name"):
        parts.append(profile["primary_name"])
    if profile.get("category_name"):
        parts.append(profile["category_name"])
    if profile.get("formatted_address"):
        parts.append(profile["formatted_address"])

    amen = profile.get("amenities") or {}
    if amen.get("wifi"):
        parts.append("wifi")
    if amen.get("outdoor_seating"):
        parts.append("outdoor seating")
    if amen.get("pet_friendly"):
        parts.append("pet friendly")
    if amen.get("kids_friendly"):
        parts.append("kids friendly")
    if amen.get("vegan_options"):
        parts.append("vegan options")
    if amen.get("price_level"):
        parts.append(f"price {amen['price_level']}")
    if amen.get("parking") and amen["parking"] != "none":
        parts.append(f"{amen['parking']} parking")

    for attr in (profile.get("attributes") or []):
        if attr.get("namespace") in {"vibe", "audience"}:
            parts.append(str(attr.get("key", "")).replace("_", " "))

    hours = profile.get("hours") or []
    if hours:
        days_open = sorted({h["day_of_week"] for h in hours})
        if len(days_open) == 7:
            parts.append("open daily")
        else:
            parts.append("open " + ",".join(str(d) for d in days_open))

    return " | ".join(p for p in parts if p)


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def embed_one(client: OpenAI, text: str) -> list[float]:
    resp = client.embeddings.create(model=EMBEDDING_MODEL, input=text)
    return resp.data[0].embedding


def embed_many(client: OpenAI, texts: Iterable[str], batch_size: int = 256) -> list[list[float]]:
    out: list[list[float]] = []
    buf: list[str] = []
    for t in texts:
        buf.append(t)
        if len(buf) >= batch_size:
            resp = client.embeddings.create(model=EMBEDDING_MODEL, input=buf)
            out.extend([d.embedding for d in resp.data])
            buf = []
    if buf:
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=buf)
        out.extend([d.embedding for d in resp.data])
    return out
