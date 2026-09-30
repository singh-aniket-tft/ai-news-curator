"""CLI entry point used by the local daily scheduler."""

from __future__ import annotations

import argparse
import json
import os
from datetime import date

from .models import SOURCE_TYPES
from .pipeline import run_topic
from .storage import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily AI news curator")
    parser.add_argument("--db", default=os.getenv("NEWS_CURATOR_DB", "news_curator.db"))
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="Save a topic without running it")
    add.add_argument("topic")
    add.add_argument("--since", required=True, help="YYYY-MM-DD")
    add.add_argument("--sources", nargs="+", choices=SOURCE_TYPES, default=list(SOURCE_TYPES))
    run = sub.add_parser("run", help="Run selected daily topics or one selected topic ID")
    run.add_argument("--daily", action="store_true", help="Use saved daily topic selection and quota settings")
    run.add_argument("--topic-id", type=int)
    run.add_argument("--max-items", type=int, default=20)
    run.add_argument("--max-total-items", type=int, default=20,
                     help="Maximum VLM analyses across all topics in this invocation")
    sub.add_parser("list", help="List saved topics")
    args = parser.parse_args()
    store = Store(args.db)
    if args.command == "add":
        topic_id = store.get_or_create_topic(args.topic, date.fromisoformat(args.since), tuple(args.sources))
        print(f"Saved topic #{topic_id}")
    elif args.command == "list":
        for row in store.list_topics():
            print(f"#{row['id']} {row['keyword']} since {row['start_date']} sources={row['source_types_json']}")
    else:
        config = store.get_daily_config() if args.daily else None
        if args.daily and not config:
            print("Daily run is not configured. Select topics and save daily settings in the app.")
            return
        topics = store.list_topics(active_only=True)
        if args.topic_id:
            topics = [row for row in topics if row["id"] == args.topic_id]
        if not topics:
            print("No topics selected for daily tracking")
            return
        max_items = config["articles_per_topic"] if config else args.max_items
        max_total = config["articles_per_day"] if config else args.max_total_items
        if config:
            remaining_requests = config["requests_per_day"] - store.used_vlm_requests_today(config["model"])
            max_total = min(max_total, remaining_requests)
            if max_total <= 0:
                print("Configured Gemini daily request quota has been reached")
                return
        if max_items < 1 or max_total < 1:
            parser.error("--max-total-items must be positive")
        topics.sort(key=lambda row: (row["last_run_at"] or "", row["id"]))
        analyzed_total = 0
        had_errors = False
        for row in topics:
            remaining = max_total - analyzed_total
            if remaining <= 0:
                break
            summary = run_topic(
                store, row["id"], row["keyword"], date.fromisoformat(row["start_date"]),
                tuple(json.loads(row["source_types_json"])), min(max_items, remaining),
            )
            analyzed_total += summary.analyzed
            had_errors = had_errors or bool(summary.errors)
            print(json.dumps(summary.__dict__, indent=2))
            if summary.quota_exhausted:
                break
        print(f"Analyzed {analyzed_total}/{max_total} items across selected topics")
        if had_errors:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
