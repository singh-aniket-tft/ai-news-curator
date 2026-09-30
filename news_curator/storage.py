"""SQLite event history. Rows represent events; mentions retain evidence URLs."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .models import Analysis, Candidate, utc_now


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY,
    keyword TEXT NOT NULL,
    start_date TEXT NOT NULL,
    source_types_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_run_at TEXT,
    UNIQUE(keyword, start_date, source_types_json)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    event_title TEXT NOT NULL,
    event_summary TEXT NOT NULL,
    event_date TEXT NOT NULL,
    event_phase TEXT NOT NULL DEFAULT 'other',
    publication_date TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_link TEXT NOT NULL,
    mention_count INTEGER NOT NULL DEFAULT 0,
    last_seen_date TEXT NOT NULL,
    embedding_json TEXT NOT NULL,
    image_observation TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mentions (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    canonical_url TEXT NOT NULL,
    source_link TEXT NOT NULL,
    publisher_key TEXT NOT NULL,
    title TEXT NOT NULL,
    publication_date TEXT NOT NULL,
    source_type TEXT NOT NULL,
    image_url TEXT,
    image_analyzed INTEGER NOT NULL,
    image_observation TEXT NOT NULL DEFAULT '',
    image_text_json TEXT NOT NULL DEFAULT '[]',
    evidence TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    UNIQUE(event_id, canonical_url)
);
CREATE INDEX IF NOT EXISTS idx_events_topic ON events(topic_id, last_seen_date DESC);
CREATE INDEX IF NOT EXISTS idx_mentions_url ON mentions(canonical_url);
CREATE TABLE IF NOT EXISTS processed_urls (
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    canonical_url TEXT NOT NULL,
    disposition TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    PRIMARY KEY(topic_id, canonical_url)
);
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_config (
    id INTEGER PRIMARY KEY CHECK(id=1),
    model TEXT NOT NULL,
    requests_per_day INTEGER NOT NULL,
    requests_per_minute INTEGER NOT NULL,
    tokens_per_minute INTEGER NOT NULL,
    articles_per_topic INTEGER NOT NULL,
    articles_per_day INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vlm_requests (
    id INTEGER PRIMARY KEY,
    model TEXT NOT NULL,
    pacific_day TEXT NOT NULL,
    requested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vlm_requests_day ON vlm_requests(model, pacific_day);
CREATE INDEX IF NOT EXISTS idx_vlm_requests_time ON vlm_requests(model, requested_at);
"""


class QuotaExceeded(RuntimeError):
    """The locally configured Gemini request budget is exhausted."""


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
            if "event_phase" not in columns:
                conn.execute("ALTER TABLE events ADD COLUMN event_phase TEXT NOT NULL DEFAULT 'other'")
            mention_columns = {row["name"] for row in conn.execute("PRAGMA table_info(mentions)")}
            if "image_observation" not in mention_columns:
                conn.execute("ALTER TABLE mentions ADD COLUMN image_observation TEXT NOT NULL DEFAULT ''")
            if "image_text_json" not in mention_columns:
                conn.execute("ALTER TABLE mentions ADD COLUMN image_text_json TEXT NOT NULL DEFAULT '[]'")
            migrated = conn.execute(
                "SELECT 1 FROM metadata WHERE key='explicit_daily_selection_v1'"
            ).fetchone()
            if not migrated:
                # Earlier versions automatically scheduled every saved test topic.
                conn.execute("UPDATE topics SET active=0")
                conn.execute(
                    "INSERT INTO metadata(key, value) VALUES ('explicit_daily_selection_v1', 'done')"
                )
            conn.execute(
                "INSERT OR IGNORE INTO daily_config(id, model, requests_per_day, requests_per_minute, "
                "tokens_per_minute, articles_per_topic, articles_per_day, updated_at) "
                "VALUES (1, ?, 500, 15, 250000, 20, 20, ?)",
                (os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"), utc_now().isoformat()),
            )

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def get_or_create_topic(self, keyword: str, start_date: date, source_types: tuple[str, ...]) -> int:
        clean_keyword = " ".join(keyword.strip().split()).casefold()
        if not clean_keyword:
            raise ValueError("Topic must not be empty")
        types_json = json.dumps(sorted(set(source_types)))
        now = utc_now().isoformat()
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO topics(keyword, start_date, source_types_json, created_at) VALUES (?, ?, ?, ?)",
                (clean_keyword, start_date.isoformat(), types_json, now),
            )
            row = conn.execute(
                "SELECT id FROM topics WHERE keyword=? AND start_date=? AND source_types_json=?",
                (clean_keyword, start_date.isoformat(), types_json),
            ).fetchone()
        return int(row["id"])

    def list_topics(self, active_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM topics" + (" WHERE active=1" if active_only else "") + " ORDER BY created_at DESC"
        with self.connect() as conn:
            rows = conn.execute(query).fetchall()
        return [dict(row) for row in rows]

    def mark_run(self, topic_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE topics SET last_run_at=? WHERE id=?", (utc_now().isoformat(), topic_id))

    def get_daily_config(self) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM daily_config WHERE id=1").fetchone()
        if row is None:
            raise RuntimeError("Daily settings are missing from the database")
        return dict(row)

    def configure_daily(
        self, model: str, requests_per_day: int, requests_per_minute: int,
        tokens_per_minute: int, articles_per_topic: int, articles_per_day: int,
        selected_topic_ids: set[int],
    ) -> None:
        if min(requests_per_day, requests_per_minute, tokens_per_minute, articles_per_topic, articles_per_day) < 1:
            raise ValueError("Quota and article limits must be positive")
        if articles_per_topic > articles_per_day or articles_per_day > requests_per_day:
            raise ValueError("Article limits must not exceed the configured free request quota")
        with self.connect() as conn:
            existing_ids = {int(row["id"]) for row in conn.execute("SELECT id FROM topics")}
            if not selected_topic_ids <= existing_ids:
                raise ValueError("An unknown topic was selected")
            conn.execute("UPDATE topics SET active=0")
            if selected_topic_ids:
                marks = ",".join("?" for _ in selected_topic_ids)
                conn.execute(f"UPDATE topics SET active=1 WHERE id IN ({marks})", tuple(selected_topic_ids))
            conn.execute(
                "INSERT INTO daily_config(id, model, requests_per_day, requests_per_minute, "
                "tokens_per_minute, articles_per_topic, articles_per_day, updated_at) "
                "VALUES (1, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET model=excluded.model, "
                "requests_per_day=excluded.requests_per_day, requests_per_minute=excluded.requests_per_minute, "
                "tokens_per_minute=excluded.tokens_per_minute, articles_per_topic=excluded.articles_per_topic, "
                "articles_per_day=excluded.articles_per_day, updated_at=excluded.updated_at",
                (model, requests_per_day, requests_per_minute, tokens_per_minute,
                 articles_per_topic, articles_per_day, utc_now().isoformat()),
            )

    def used_vlm_requests_today(self, model: str) -> int:
        pacific_day = utc_now().astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS used FROM vlm_requests WHERE model=? AND pacific_day=?",
                (model, pacific_day),
            ).fetchone()
        return int(row["used"])

    def reserve_vlm_request(self, model: str, requests_per_day: int, requests_per_minute: int) -> float:
        """Reserve one SDK request; return seconds to wait if the minute is full."""
        now = utc_now()
        pacific_day = now.astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()
        cutoff = (now - timedelta(seconds=60)).isoformat()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            used = conn.execute(
                "SELECT COUNT(*) AS used FROM vlm_requests WHERE model=? AND pacific_day=?",
                (model, pacific_day),
            ).fetchone()["used"]
            if used >= requests_per_day:
                raise QuotaExceeded("Configured Gemini daily request quota has been reached")
            recent = conn.execute(
                "SELECT requested_at FROM vlm_requests WHERE model=? AND requested_at>? "
                "ORDER BY requested_at ASC",
                (model, cutoff),
            ).fetchall()
            if len(recent) >= requests_per_minute:
                oldest = datetime.fromisoformat(recent[0]["requested_at"])
                return max(0.1, 60 - (now - oldest).total_seconds() + 0.1)
            conn.execute(
                "INSERT INTO vlm_requests(model, pacific_day, requested_at) VALUES (?, ?, ?)",
                (model, pacific_day, now.isoformat()),
            )
        return 0.0

    def seen_url(self, topic_id: int, url: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM processed_urls WHERE topic_id=? AND canonical_url=? LIMIT 1",
                (topic_id, url),
            ).fetchone()
        return row is not None

    def mark_processed(self, topic_id: int, url: str, disposition: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO processed_urls(topic_id, canonical_url, disposition, processed_at) "
                "VALUES (?, ?, ?, ?)",
                (topic_id, url, disposition, utc_now().isoformat()),
            )

    def event_candidates(self, topic_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, event_title, event_summary, event_date, event_phase, embedding_json "
                "FROM events WHERE topic_id=? ORDER BY last_seen_date DESC LIMIT 500",
                (topic_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_finding(
        self,
        topic_id: int,
        candidate: Candidate,
        analysis: Analysis,
        embedding: list[float],
        publisher_identity: str,
        match_id: int | None,
    ) -> tuple[int, bool]:
        """Return (event id, inserted). Repeated URLs leave counts and recency unchanged."""
        now = utc_now().isoformat()
        today = now[:10]
        url = candidate.normalized_url
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT e.id FROM mentions m JOIN events e ON e.id=m.event_id "
                "WHERE e.topic_id=? AND m.canonical_url=?",
                (topic_id, url),
            ).fetchone()
            if existing:
                return int(existing["id"]), False
            if match_id is None:
                cur = conn.execute(
                    "INSERT INTO events(topic_id, event_title, event_summary, event_date, event_phase, publication_date, "
                    "source_type, source_link, mention_count, last_seen_date, embedding_json, "
                    "image_observation, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)",
                    (
                        topic_id, analysis.event_title, analysis.event_summary, analysis.event_date.isoformat(),
                        analysis.event_phase, candidate.published_date.isoformat(), analysis.source_type, candidate.url,
                        today, json.dumps(embedding), analysis.image_observation, now, now,
                    ),
                )
                event_id = int(cur.lastrowid)
            else:
                row = conn.execute("SELECT * FROM events WHERE id=? AND topic_id=?", (match_id, topic_id)).fetchone()
                if row is None:
                    raise ValueError("Matched event does not belong to topic")
                event_id = int(row["id"])
                earlier = candidate.published_date.isoformat() < row["publication_date"]
                conn.execute(
                    "UPDATE events SET publication_date=?, source_type=?, source_link=?, "
                    "last_seen_date=?, updated_at=?, image_observation=? WHERE id=?",
                    (
                        candidate.published_date.isoformat() if earlier else row["publication_date"],
                        analysis.source_type if earlier else row["source_type"],
                        candidate.url if earlier else row["source_link"],
                        today, now,
                        analysis.image_observation or row["image_observation"],
                        event_id,
                    ),
                )
            conn.execute(
                "INSERT INTO mentions(event_id, canonical_url, source_link, publisher_key, title, "
                "publication_date, source_type, image_url, image_analyzed, image_observation, "
                "image_text_json, evidence, first_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id, url, candidate.url, publisher_identity, candidate.title,
                    candidate.published_date.isoformat(), analysis.source_type, candidate.image_url,
                    int(analysis.image_analyzed), analysis.image_observation,
                    json.dumps(analysis.image_text), analysis.evidence, now,
                ),
            )
            conn.execute(
                "UPDATE events SET mention_count=(SELECT MAX(COUNT(DISTINCT publisher_key)-1, 0) "
                "FROM mentions WHERE event_id=?) WHERE id=?",
                (event_id, event_id),
            )
        return event_id, True

    def list_events(self, topic_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, event_title, event_summary, event_date, publication_date, source_type, "
                "source_link, mention_count, last_seen_date, image_observation "
                "FROM events WHERE topic_id=? ORDER BY last_seen_date DESC, updated_at DESC, id DESC",
                (topic_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_mentions(self, event_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT source_link, publisher_key, title, publication_date, source_type, "
                "image_url, image_analyzed, image_observation, image_text_json, evidence "
                "FROM mentions WHERE event_id=? "
                "ORDER BY publication_date ASC, id ASC",
                (event_id,),
            ).fetchall()
        return [dict(row) for row in rows]
