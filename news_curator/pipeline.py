"""One pipeline shared by the UI and scheduled CLI."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from .dedup import Embedder, LocalEmbedder, find_event_match
from .models import Analysis, Candidate, SourceType, publisher_key
from .sources import bluesky_search, discover_news, enrich_article
from .storage import Store
from .vlm import GeminiAnalyzer


LOGGER = logging.getLogger(__name__)


class Analyzer(Protocol):
    def analyze(self, topic: str, candidate: Candidate) -> Analysis: ...


@dataclass
class RunSummary:
    topic_id: int
    discovered: int = 0
    analyzed: int = 0
    new_events: int = 0
    updated_events: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def publisher_identity(candidate: Candidate) -> str:
    if candidate.source_hint == "social":
        return "bsky:" + candidate.publisher.casefold().lstrip("@")
    return publisher_key(candidate.url)


def run_topic(
    store: Store,
    topic_id: int,
    topic: str,
    start_date: date,
    source_types: tuple[str, ...],
    max_items: int = 20,
    analyzer: Analyzer | None = None,
    embedder: Embedder | None = None,
    news_search=discover_news,
    social_search=bluesky_search,
    enrich=enrich_article,
) -> RunSummary:
    if max_items < 1 or max_items > 50:
        raise ValueError("max_items must be between 1 and 50")
    result = RunSummary(topic_id=topic_id)
    collected: list[Candidate] = []
    if any(source != SourceType.SOCIAL.value for source in source_types):
        try:
            collected.extend(news_search(topic, start_date, limit=max_items))
        except Exception as exc:
            result.errors.append(f"News search: {exc}")
    if SourceType.SOCIAL.value in source_types:
        try:
            collected.extend(social_search(topic, start_date, limit=min(max_items, 10)))
        except Exception as exc:
            result.errors.append(f"Social search: {exc}")
    result.discovered = len(collected)
    if not collected:
        store.mark_run(topic_id)
        return result
    analyzer = analyzer or GeminiAnalyzer()
    embedder = embedder or LocalEmbedder()
    unique_urls: set[str] = set()
    for raw in collected:
        if result.analyzed >= max_items:
            break
        try:
            normalized = raw.normalized_url
            if normalized in unique_urls or store.seen_url(topic_id, normalized):
                result.skipped += 1
                continue
            unique_urls.add(normalized)
            candidate = enrich(raw)
            if candidate.published_date < start_date:
                store.mark_processed(topic_id, normalized, "before_start_date")
                result.skipped += 1
                continue
            result.analyzed += 1
            analysis = analyzer.analyze(topic, candidate)
            if not analysis.relevant or analysis.source_type not in source_types:
                store.mark_processed(topic_id, normalized, "filtered")
                result.skipped += 1
                continue
            vector = embedder.embed(analysis.embedding_text)
            match_id = find_event_match(analysis, vector, store.event_candidates(topic_id))
            _, inserted = store.add_finding(
                topic_id, candidate, analysis, vector, publisher_identity(candidate), match_id,
            )
            store.mark_processed(topic_id, normalized, "stored")
            if inserted:
                if match_id is None:
                    result.new_events += 1
                else:
                    result.updated_events += 1
            else:
                result.skipped += 1
        except Exception as exc:
            LOGGER.exception("Failed to process %s", raw.url)
            result.errors.append(f"{raw.url}: {exc}")
    store.mark_run(topic_id)
    return result
