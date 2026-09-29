"""Validated data shared by collection, analysis, and storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class SourceType(str, Enum):
    OFFICIAL = "Authoritative/Official"
    COMMERCIAL = "Marketing/Commercial"
    SOCIAL = "Social Media"
    OTHER = "Other"


SOURCE_TYPES = tuple(item.value for item in SourceType)


class EventPhase(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    ANNOUNCED = "announced"
    LAUNCHED = "launched"
    COMPLETED = "completed"
    OTHER = "other"


EVENT_PHASES = tuple(item.value for item in EventPhase)


def canonical_url(raw: str) -> str:
    """Drop tracking parameters while preserving meaningful URL parts."""
    parsed = urlsplit(raw.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Expected an HTTP(S) source URL")
    ignored = {"fbclid", "gclid", "mc_cid", "mc_eid"}
    query = urlencode([
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in ignored
    ])
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, query, ""))


def publisher_key(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Candidate:
    url: str
    title: str
    published_date: date
    publisher: str
    text: str = ""
    image_url: str | None = None
    source_hint: str = "news"

    @property
    def normalized_url(self) -> str:
        return canonical_url(self.url)


@dataclass(frozen=True)
class Analysis:
    relevant: bool
    event_title: str
    event_summary: str
    event_date: date
    source_type: str
    entities: tuple[str, ...]
    image_observation: str = ""
    image_analyzed: bool = False
    evidence: str = ""
    event_phase: str = EventPhase.OTHER.value

    def __post_init__(self) -> None:
        if self.source_type not in SOURCE_TYPES:
            raise ValueError(f"Invalid source type: {self.source_type}")
        if self.event_phase not in EVENT_PHASES:
            raise ValueError(f"Invalid event phase: {self.event_phase}")
        if self.relevant and (not self.event_title.strip() or not self.event_summary.strip()):
            raise ValueError("Relevant findings require an event title and summary")

    @property
    def embedding_text(self) -> str:
        return f"{self.event_title}. {self.event_summary}. Entities: {', '.join(self.entities)}"
