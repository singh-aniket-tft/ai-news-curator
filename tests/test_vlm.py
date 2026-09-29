from datetime import date
from types import SimpleNamespace

from news_curator.models import Candidate
from news_curator.vlm import ExtractedEvent, GeminiAnalyzer, redact_contact_info


def test_vlm_sends_schema_and_image(monkeypatch):
    calls = []
    extracted = ExtractedEvent(
        relevant=True, event_title="Agency releases report", event_summary="Agency released a report.",
        event_date=date(2026, 9, 28), event_phase="launched", source_type="Authoritative/Official",
        entities=["Agency"], image_observation="Chart shows a rising line.", evidence="Report was released.",
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
    assert result.source_type == "Authoritative/Official"
    assert calls[0]["config"].response_mime_type == "application/json"
    assert len(calls[0]["contents"]) == 2


def test_contact_redaction():
    result = redact_contact_info("Contact jane@example.com or +1 555 123 4567")
    assert "jane@example.com" not in result
    assert "555" not in result
