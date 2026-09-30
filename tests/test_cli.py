import sys
from datetime import date

from news_curator import cli
from news_curator.models import SourceType
from news_curator.pipeline import RunSummary
from news_curator.storage import Store


def test_cli_caps_total_analyses_across_topics(tmp_path, monkeypatch, capsys):
    db = tmp_path / "events.db"
    store = Store(db)
    ids = [store.get_or_create_topic(word, date(2026, 9, 20), (SourceType.OTHER.value,))
           for word in ("battery", "solar", "wind")]
    store.configure_daily("gemini-3.5-flash-lite", 500, 15, 250000, 3, 5, set(ids))
    calls = []

    def fake_run(store, topic_id, topic, start_date, types, max_items):
        calls.append((topic_id, max_items))
        return RunSummary(topic_id=topic_id, attempted=max_items, analyzed=max_items)

    monkeypatch.setattr(cli, "run_topic", fake_run)
    monkeypatch.setattr(sys, "argv", ["news-curator", "--db", str(db), "run",
                                      "--max-items", "3", "--max-total-items", "5"])
    cli.main()
    assert calls == [(ids[0], 3), (ids[1], 2)]
    assert "Analyzed 5 items in 5/5 attempts" in capsys.readouterr().out


def test_daily_run_only_uses_explicit_selection_and_saved_limits(tmp_path, monkeypatch, capsys):
    db = tmp_path / "events.db"
    store = Store(db)
    ids = [store.get_or_create_topic(word, date(2026, 9, 20), (SourceType.OTHER.value,))
           for word in ("battery", "solar", "wind")]
    calls = []

    def fake_run(store, topic_id, topic, start_date, types, max_items):
        calls.append((topic_id, max_items))
        return RunSummary(topic_id=topic_id, attempted=max_items, analyzed=max_items)

    monkeypatch.setattr(cli, "run_topic", fake_run)
    monkeypatch.setattr(sys, "argv", ["news-curator", "--db", str(db), "run", "--daily"])
    cli.main()
    assert calls == []
    assert "No topics selected" in capsys.readouterr().out

    store.configure_daily("gemini-3.5-flash-lite", 500, 15, 250000, 3, 5, {ids[0], ids[2]})
    cli.main()
    assert calls == [(ids[0], 3), (ids[2], 2)]
    assert "Analyzed 5 items in 5/5 attempts" in capsys.readouterr().out
