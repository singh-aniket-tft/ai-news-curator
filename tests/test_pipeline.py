from datetime import date

from news_curator.models import Analysis, Candidate, SourceType
from news_curator.pipeline import run_topic
from news_curator.storage import Store


class FakeAnalyzer:
    def analyze(self, topic, candidate):
        return Analysis(
            relevant=True,
            event_title="Agency approves battery rule",
            event_summary="The agency approved a battery recycling rule.",
            event_date=date(2026, 9, 25),
            source_type=SourceType.OTHER.value,
            entities=("agency", "battery"),
            evidence="Article describes the approval.",
        )


class FakeEmbedder:
    def embed(self, text):
        return [1.0, 0.0]


def article(url, publisher):
    return Candidate(url, "Battery rule approved", date(2026, 9, 25), publisher, "The agency approved the rule.")


def test_same_event_updates_one_row_and_distinct_publisher_count(tmp_path):
    store = Store(tmp_path / "events.db")
    types = (SourceType.OTHER.value,)
    topic_id = store.get_or_create_topic("battery", date(2026, 9, 20), types)
    first = article("https://paper-a.test/story?utm_source=feed", "Paper A")
    second = article("https://paper-b.test/other-words", "Paper B")
    same_publisher = article("https://paper-b.test/followup", "Paper B")
    items = [first, second, same_publisher]
    search = lambda topic, since, limit: items
    kwargs = dict(analyzer=FakeAnalyzer(), embedder=FakeEmbedder(), news_search=search, enrich=lambda x: x)

    summary = run_topic(store, topic_id, "battery", date(2026, 9, 20), types, **kwargs)
    assert (summary.new_events, summary.updated_events) == (1, 2)
    events = store.list_events(topic_id)
    assert len(events) == 1
    assert events[0]["mention_count"] == 1
    assert len(store.list_mentions(events[0]["id"])) == 3

    repeated = run_topic(store, topic_id, "battery", date(2026, 9, 20), types, **kwargs)
    assert repeated.analyzed == 0
    assert store.list_events(topic_id)[0]["mention_count"] == 1


def test_source_filter_skips_row_without_loading_embedding_model(tmp_path, monkeypatch):
    store = Store(tmp_path / "events.db")
    types = (SourceType.OFFICIAL.value,)
    topic_id = store.get_or_create_topic("battery", date(2026, 9, 20), types)
    search = lambda topic, since, limit: [article("https://paper-a.test/story", "Paper A")]
    def unexpected_embedding_model():
        raise AssertionError("Embedding model should load only for relevant findings")

    monkeypatch.setattr("news_curator.pipeline.LocalEmbedder", unexpected_embedding_model)
    kwargs = dict(analyzer=FakeAnalyzer(), news_search=search, enrich=lambda x: x)
    first = run_topic(store, topic_id, "battery", date(2026, 9, 20), types, **kwargs)
    second = run_topic(store, topic_id, "battery", date(2026, 9, 20), types, **kwargs)
    assert first.analyzed == 1
    assert second.analyzed == 0
    assert store.list_events(topic_id) == []


def test_failed_analysis_is_reported_without_counting_as_analyzed(tmp_path):
    store = Store(tmp_path / "events.db")
    types = (SourceType.OTHER.value,)
    topic_id = store.get_or_create_topic("battery", date(2026, 9, 20), types)

    class FailingAnalyzer:
        def analyze(self, topic, candidate):
            raise RuntimeError("Model unavailable")

    result = run_topic(
        store, topic_id, "battery", date(2026, 9, 20), types,
        analyzer=FailingAnalyzer(), news_search=lambda *args, **kwargs: [
            article("https://paper-a.test/story", "Paper A")
        ], enrich=lambda item: item,
    )
    assert (result.attempted, result.analyzed) == (1, 0)
    assert len(result.errors) == 1
    assert store.list_events(topic_id) == []


def test_new_report_moves_existing_event_to_top(tmp_path):
    store = Store(tmp_path / "events.db")
    topic_id = store.get_or_create_topic("battery", date(2026, 9, 20), (SourceType.OTHER.value,))
    first = Analysis(True, "Battery rule approved", "Agency approved a battery rule.",
                     date(2026, 9, 25), SourceType.OTHER.value, ("agency", "battery"),
                     event_phase="approved")
    second = Analysis(True, "Battery plant opens", "Company opened a battery plant.",
                      date(2026, 9, 26), SourceType.OTHER.value, ("company", "battery"),
                      event_phase="launched")
    first_id, _ = store.add_finding(topic_id, article("https://paper-a.test/rule", "Paper A"),
                                    first, [1.0, 0.0], "paper-a.test", None)
    second_id, _ = store.add_finding(topic_id, article("https://paper-b.test/plant", "Paper B"),
                                     second, [0.0, 1.0], "paper-b.test", None)
    assert [row["id"] for row in store.list_events(topic_id)] == [second_id, first_id]
    store.add_finding(topic_id, article("https://paper-c.test/rule", "Paper C"),
                      first, [1.0, 0.0], "paper-c.test", first_id)
    rows = store.list_events(topic_id)
    assert [row["id"] for row in rows] == [first_id, second_id]
    assert rows[0]["mention_count"] == 1


def test_social_posts_get_analysis_slots_alongside_news(tmp_path):
    store = Store(tmp_path / "events.db")
    types = (SourceType.OTHER.value, SourceType.SOCIAL.value)
    topic_id = store.get_or_create_topic("battery", date(2026, 9, 20), types)
    news = [article(f"https://paper-{n}.test/story", f"Paper {n}") for n in range(3)]
    social = [Candidate(f"https://bsky.app/profile/user{n}.test/post/abc{n}",
                        "Battery rule approved", date(2026, 9, 25), f"user{n}.test",
                        "Battery rule approved", source_hint="social") for n in range(3)]
    summary = run_topic(
        store, topic_id, "battery", date(2026, 9, 20), types, max_items=3,
        analyzer=FakeAnalyzer(), embedder=FakeEmbedder(),
        news_search=lambda *args, **kwargs: news,
        social_search=lambda *args, **kwargs: social,
        enrich=lambda x: x,
    )
    assert summary.analyzed == 3
    links = [mention["source_link"] for event in store.list_events(topic_id)
             for mention in store.list_mentions(event["id"])]
    assert any(link.startswith("https://bsky.app/") for link in links)
