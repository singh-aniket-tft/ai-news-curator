from datetime import date
from io import BytesIO
from types import SimpleNamespace

from news_curator.models import Candidate
from news_curator.sources import enrich_article


def test_enrichment_keeps_rss_snippet_when_html_has_no_extractable_text(monkeypatch):
    class Response:
        headers = {"content-type": "text/html"}
        raw = BytesIO(b"<html><body><p>Navigation only</p></body></html>")
        encoding = "utf-8"

        def raise_for_status(self):
            return None

    monkeypatch.setattr("news_curator.sources.HTTP_SESSION.get", lambda *args, **kwargs: Response())
    monkeypatch.setattr("news_curator.sources.trafilatura.extract", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "news_curator.sources.trafilatura.extract_metadata",
        lambda *args, **kwargs: SimpleNamespace(title=None, date=None, image=None),
    )
    candidate = Candidate(
        "https://example.com/story", "Battery story", date(2026, 9, 29),
        "Example", text="The RSS summary reports a battery update.",
    )
    assert enrich_article(candidate).text == candidate.text
