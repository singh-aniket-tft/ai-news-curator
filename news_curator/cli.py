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
    run = sub.add_parser("run", help="Run all saved active topics or one topic ID")
    run.add_argument("--topic-id", type=int)
    run.add_argument("--max-items", type=int, default=20)
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
        topics = store.list_topics(active_only=True)
        if args.topic_id:
            topics = [row for row in topics if row["id"] == args.topic_id]
        if not topics:
            print("No active topics found")
            return
        for row in topics:
            summary = run_topic(
                store, row["id"], row["keyword"], date.fromisoformat(row["start_date"]),
                tuple(json.loads(row["source_types_json"])), args.max_items,
            )
            print(json.dumps(summary.__dict__, indent=2))


if __name__ == "__main__":
    main()
