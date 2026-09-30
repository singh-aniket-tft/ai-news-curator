"""Candidate generation with local embeddings and conservative event matching."""

from __future__ import annotations

import json
import math
import re
from datetime import date
from typing import Protocol

from .models import Analysis


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class LocalEmbedder:
    """Small ONNX model; no third-party embedding API calls."""

    def __init__(self):
        from fastembed import TextEmbedding

        self.model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")

    def embed(self, text: str) -> list[float]:
        return list(next(self.model.embed([text])).astype(float))


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return numerator / denominator if denominator else 0.0


def _tokens(value: str) -> set[str]:
    cleaned = (part.casefold().strip(".,:;()[]{}\"'") for part in value.split())
    return {part for part in cleaned if len(part) > 2}


_PHASE_WORDS = {
    "proposed": r"\b(propos(?:e[ds]?|al)|plans?|draft(?:ed)?)\b",
    "approved": r"\b(approv(?:e[ds]?|al)|adopt(?:ed|s)?|authorized?|signed off)\b",
    "rejected": r"\b(reject(?:ed|s|ion)?|den(?:y|ied|ial)|block(?:ed|s)?)\b",
    "announced": r"\b(announc(?:e[ds]?|ement)|unveil(?:ed|s)?)\b",
    "launched": r"\b(launch(?:ed|es)?|releas(?:e[ds]?|ing)|rolled out)\b",
    "completed": r"\b(complet(?:e[ds]?|ion)|finish(?:ed|es)?|conclud(?:e[ds]?))\b",
}


def _phase(explicit: str, title: str, summary: str) -> str:
    if explicit and explicit != "other":
        return explicit
    for text in (title, summary):
        for phase, pattern in _PHASE_WORDS.items():
            if re.search(pattern, text, re.IGNORECASE):
                return phase
    return "other"


def find_event_match(analysis: Analysis, vector: list[float], events: list[dict]) -> int | None:
    """Merge paraphrases; reject merely related stories with different dates/entities."""
    best_id = None
    best_score = 0.0
    new_entities = _tokens(" ".join(analysis.entities))
    new_phase = _phase(analysis.event_phase, analysis.event_title, analysis.event_summary)
    for event in events:
        days = abs((analysis.event_date - date.fromisoformat(event["event_date"])).days)
        if days > 14:
            continue
        old_phase = _phase(event.get("event_phase", "other"), event["event_title"], event["event_summary"])
        if new_phase != "other" and old_phase != "other" and new_phase != old_phase:
            continue
        old_vector = json.loads(event["embedding_json"])
        score = cosine(vector, old_vector)
        old_tokens = _tokens(event["event_title"] + " " + event["event_summary"])
        entity_overlap = bool(new_entities & old_tokens)
        if entity_overlap and score >= 0.90:
            if score > best_score:
                best_id, best_score = int(event["id"]), score
    return best_id
