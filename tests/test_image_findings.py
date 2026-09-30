import json
from datetime import date

from news_curator.models import Analysis, Candidate, SourceType
from news_curator.storage import Store


def test_visual_evidence_stays_with_its_source_after_event_merge(tmp_path):
    store = Store(tmp_path / "events.db")
    topic_id = store.get_or_create_topic("battery policy", date(2026, 9, 20), (SourceType.OTHER.value,))
    first = Candidate(
        "https://paper-a.test/report", "Battery report", date(2026, 9, 25), "Paper A",
        image_url="https://paper-a.test/chart.png",
    )
    second = Candidate(
        "https://paper-b.test/report", "Battery report", date(2026, 9, 26), "Paper B",
        image_url="https://paper-b.test/graphic.png",
    )
    first_analysis = Analysis(
        True, "Battery rule approved", "The agency approved a battery rule.",
        date(2026, 9, 25), SourceType.OTHER.value, ("agency", "battery"),
        image_observation="The chart rises.", image_analyzed=True, image_text=("2025: 10", "2026: 20"),
    )
    second_analysis = Analysis(
        True, "Battery rule approved", "The agency approved a battery rule.",
        date(2026, 9, 25), SourceType.OTHER.value, ("agency", "battery"),
        image_observation="A timeline highlights approval.", image_analyzed=True,
        image_text=("Approved 2026",),
    )

    event_id, _ = store.add_finding(topic_id, first, first_analysis, [1.0, 0.0], "paper-a.test", None)
    store.add_finding(topic_id, second, second_analysis, [1.0, 0.0], "paper-b.test", event_id)

    assert len(store.list_events(topic_id)) == 1
    mentions = store.list_mentions(event_id)
    assert [json.loads(row["image_text_json"]) for row in mentions] == [
        ["2025: 10", "2026: 20"], ["Approved 2026"],
    ]
    assert [row["image_observation"] for row in mentions] == [
        "The chart rises.", "A timeline highlights approval.",
    ]
