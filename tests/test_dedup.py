import json
from datetime import date

from news_curator.dedup import find_event_match
from news_curator.models import Analysis, SourceType, canonical_url


def test_semantic_match_requires_event_compatibility():
    analysis = Analysis(True, "Agency approves battery rule", "Battery recycling rule approved.",
                        date(2026, 9, 25), SourceType.OTHER.value, ("agency", "battery"))
    existing = {"id": 7, "event_title": "Battery rule gets agency approval",
                "event_summary": "Agency signs off on battery recycling rule.",
                "event_date": "2026-09-24", "embedding_json": json.dumps([1.0, 0.0])}
    assert find_event_match(analysis, [0.9, 0.1], [existing]) == 7
    existing["event_date"] = "2026-08-01"
    assert find_event_match(analysis, [0.9, 0.1], [existing]) is None


def test_proposal_approval_and_rejection_stay_separate_despite_high_similarity():
    approved = Analysis(True, "Agency approves battery rule", "Battery recycling rule approved.",
                        date(2026, 9, 25), SourceType.OTHER.value, ("agency", "battery"),
                        event_phase="approved")
    for title, phase in (("Agency proposes battery rule", "proposed"),
                         ("Agency rejects battery rule", "rejected")):
        existing = {"id": 7, "event_title": title, "event_summary": "Agency battery recycling rule.",
                    "event_date": "2026-09-25", "event_phase": phase,
                    "embedding_json": json.dumps([1.0, 0.0])}
        assert find_event_match(approved, [1.0, 0.0], [existing]) is None


def test_phase_guard_works_for_older_rows_without_phase_metadata():
    approved = Analysis(True, "Agency approves battery rule", "Battery recycling rule approved.",
                        date(2026, 9, 25), SourceType.OTHER.value, ("agency", "battery"))
    proposed = {"id": 7, "event_title": "Agency proposes battery rule",
                "event_summary": "Agency proposes a rule on recycling batteries.",
                "event_date": "2026-09-25", "embedding_json": json.dumps([1.0, 0.0])}
    assert find_event_match(approved, [1.0, 0.0], [proposed]) is None


def test_canonical_url_drops_trackers():
    assert canonical_url("https://Example.com/a/?utm_source=x&b=2#section") == "https://example.com/a?b=2"
