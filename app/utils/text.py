"""Text normalization helpers used by matching."""
from __future__ import annotations

import re

_WS = re.compile(r"\s+")
_NON_ALPHANUM = re.compile(r"[^a-z0-9 ]+")


def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    s = name.lower().strip()
    s = _NON_ALPHANUM.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s


def tokenize(text: str | None) -> list[str]:
    return normalize_name(text).split() if text else []


def normalize_phone(phone: str | None) -> str:
    if not phone:
        return ""
    return re.sub(r"\D+", "", phone)


def normalize_website(url: str | None) -> str:
    if not url:
        return ""
    s = url.lower().strip()
    s = re.sub(r"^https?://", "", s)
    s = s.split("/")[0]
    s = re.sub(r"^www\.", "", s)
    return s
