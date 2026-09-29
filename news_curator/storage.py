"""SQLite event history. Rows represent events; mentions retain evidence URLs."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from .models import Analysis, Candidate, utc_now


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY,
    keyword TEXT NOT NULL,
    start_date TEXT NOT NULL,
    source_types_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
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
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
            if "event_phase" not in columns:
                conn.execute("ALTER TABLE events ADD COLUMN event_phase TEXT NOT NULL DEFAULT 'other'")

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

    def set_active(self, topic_id: int, active: bool) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE topics SET active=? WHERE id=?", (int(active), topic_id))

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
                "publication_date, source_type, image_url, image_analyzed, evidence, first_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id, url, candidate.url, publisher_identity, candidate.title,
                    candidate.published_date.isoformat(), analysis.source_type, candidate.image_url,
                    int(analysis.image_analyzed), analysis.evidence, now,
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
                "image_url, image_analyzed, evidence FROM mentions WHERE event_id=? "
                "ORDER BY publication_date ASC, id ASC",
                (event_id,),
            ).fetchall()
        return [dict(row) for row in rows]
