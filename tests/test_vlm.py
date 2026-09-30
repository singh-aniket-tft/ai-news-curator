from datetime import date
from types import SimpleNamespace

import pytest

from news_curator.models import Candidate
from news_curator.storage import QuotaExceeded
from news_curator.vlm import ExtractedEvent, GeminiAnalyzer, redact_contact_info


def test_vlm_sends_schema_and_image(monkeypatch):
    calls = []
    extracted = ExtractedEvent(
        relevant=True, event_title="Agency releases report", event_summary="Agency released a report.",
        event_date=date(2026, 9, 28), event_phase="launched", source_type="Authoritative/Official",
        entities=["Agency"], image_observation="Chart shows a rising line.",
        image_text=["Revenue +12%", "Q3 2026", "Contact jane@example.com"],
        evidence="Report was released.",
    )
    def generate_content(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(parsed=extracted)
    analyzer = object.__new__(GeminiAnalyzer)
    analyzer.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    analyzer.model = "gemini-3.5-flash-lite"
    monkeypatch.setattr("news_curator.vlm.download_image", lambda url: (b"fake-jpeg", "image/jpeg"))
    candidate = Candidate("https://agency.test/report", "Report", date(2026, 9, 28),
                          "Agency", "Public report text", "https://agency.test/chart.jpg")
    result = analyzer.analyze("topic", candidate)
    assert result.image_analyzed
    assert result.image_text == ("Revenue +12%", "Q3 2026", "Contact [email removed]")
    assert result.source_type == "Authoritative/Official"
    assert calls[0]["config"].response_mime_type == "application/json"
    assert len(calls[0]["contents"]) == 2


def test_vlm_discards_visual_claims_when_image_is_unavailable(monkeypatch):
    extracted = ExtractedEvent(
        relevant=True, event_title="Agency releases report", event_summary="Agency released a report.",
        event_date=date(2026, 9, 28), event_phase="launched", source_type="Authoritative/Official",
        entities=["Agency"], image_observation="A chart rises.", image_text=["Revenue +12%"],
        evidence="Report was released.",
    )
    analyzer = object.__new__(GeminiAnalyzer)
    calls = []

    def generate_content(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(parsed=extracted)

    analyzer.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    analyzer.model = "gemini-3.5-flash-lite"
    monkeypatch.setattr("news_curator.vlm.download_image", lambda url: None)
    candidate = Candidate("https://agency.test/report", "Report", date(2026, 9, 28),
                          "Agency", "Public report text", "https://agency.test/missing.png")
    result = analyzer.analyze("topic", candidate)
    assert not result.image_analyzed
    assert result.image_observation == ""
    assert result.image_text == ()
    assert len(calls[0]["contents"]) == 1


def test_contact_redaction():
    result = redact_contact_info("Contact jane@example.com or +1 555 123 4567")
    assert "jane@example.com" not in result
    assert "555" not in result


def test_provider_rate_limit_stops_after_counted_retries(monkeypatch):
    calls = []
    analyzer = object.__new__(GeminiAnalyzer)

    def generate_content(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    analyzer.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    analyzer.model = "gemini-3.5-flash-lite"
    analyzer.request_gate = lambda: calls.append("reserved")
    monkeypatch.setattr("news_curator.vlm.download_image", lambda url: None)
    monkeypatch.setattr("news_curator.vlm.time.sleep", lambda seconds: None)
    candidate = Candidate("https://agency.test/report", "Report", date(2026, 9, 28),
                          "Agency", "Public report text")
    with pytest.raises(QuotaExceeded):
        analyzer.analyze("topic", candidate)
    assert calls.count("reserved") == 3
