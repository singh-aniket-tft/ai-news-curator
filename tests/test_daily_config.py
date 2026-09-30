from datetime import date

import pytest

from news_curator.models import SourceType
from news_curator.storage import QuotaExceeded, Store


def test_old_topics_need_explicit_daily_selection(tmp_path):
    store = Store(tmp_path / "events.db")
    topic_id = store.get_or_create_topic("battery", date(2026, 9, 20), (SourceType.OTHER.value,))
    with store.connect() as conn:
        conn.execute("UPDATE topics SET active=1 WHERE id=?", (topic_id,))
        conn.execute("DELETE FROM metadata WHERE key='explicit_daily_selection_v1'")
    reopened = Store(store.path)
    assert reopened.list_topics(active_only=True) == []
    reopened.configure_daily("gemini-3.5-flash-lite", 500, 15, 250000, 500, 500, {topic_id})
    assert [row["id"] for row in Store(store.path).list_topics(active_only=True)] == [topic_id]


def test_local_request_ledger_enforces_day_and_minute_limits(tmp_path):
    store = Store(tmp_path / "events.db")
    model = "gemini-3.5-flash-lite"
    assert store.reserve_vlm_request(model, 1, 1) == 0
    assert store.used_vlm_requests_today(model) == 1
    with pytest.raises(QuotaExceeded):
        store.reserve_vlm_request(model, 1, 1)

    other_model = "different-model"
    assert store.reserve_vlm_request(other_model, 2, 1) == 0
    assert store.reserve_vlm_request(other_model, 2, 1) > 0
    assert store.used_vlm_requests_today(other_model) == 1
