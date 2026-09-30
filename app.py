"""Streamlit interface for interactive tracking and reviewer testing."""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import date, timedelta

import streamlit as st

from news_curator.models import SOURCE_TYPES
from news_curator.pipeline import run_topic
from news_curator.quota import make_request_gate
from news_curator.storage import Store
from news_curator.vlm import GeminiAnalyzer


st.set_page_config(page_title="AI News Curator", page_icon="🗞️", layout="wide")
st.title("AI News Curator")
st.caption("Track one event per row, with every independent report retained as evidence.")

db_path = os.getenv("NEWS_CURATOR_DB", "news_curator.db")
store = Store(db_path)
daily_config = store.get_daily_config()
used_requests = store.used_vlm_requests_today(daily_config["model"])

with st.sidebar:
    st.header("Run settings")
    st.write("Model:", os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"))
    st.write("Storage: local SQLite")
    session_key = st.text_input(
        "Gemini API key (optional if set in environment)", type="password",
        help="Used only for this session; it is not saved in the database.",
    )
    st.caption("This app sends public article excerpts and available images to Gemini. Do not enter private or sensitive topics.")
    st.caption(
        f"App-recorded Gemini requests today: {used_requests}/{daily_config['requests_per_day']} "
        f"(Pacific day). Other use of the same Google project is not included."
    )

with st.form("research"):
    topic = st.text_input("Topic or keyword", placeholder="e.g. electric vehicle battery policy")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Search from", value=date.today() - timedelta(days=7), max_value=date.today())
    with col2:
        source_types = st.multiselect("Source types", SOURCE_TYPES, default=list(SOURCE_TYPES))
    max_items = st.number_input(
        "Maximum new articles/posts to analyze this run", min_value=1,
        max_value=daily_config["requests_per_day"], value=min(20, daily_config["requests_per_day"]),
        step=1,
    )
    submitted = st.form_submit_button("Save topic and run now", type="primary")


def run_and_report(topic_id: int, keyword: str, since: date, types: tuple[str, ...]) -> None:
    if not session_key and not os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
        st.error("Set GEMINI_API_KEY before running live VLM analysis. See README.md.")
        return
    available = daily_config["requests_per_day"] - store.used_vlm_requests_today(daily_config["model"])
    if available <= 0:
        st.error("The configured Gemini daily request budget is exhausted. It resets at midnight Pacific time.")
        return
    with st.status("Discovering and analyzing sources...", expanded=True) as status:
        try:
            summary = run_topic(
                store, topic_id, keyword, since, types, min(max_items, available),
                analyzer=GeminiAnalyzer(
                    api_key=session_key or None, model=daily_config["model"],
                    request_gate=make_request_gate(store, daily_config),
                ),
            )
            st.write(
                f"Found {summary.discovered}; analyzed {summary.analyzed}; "
                f"created {summary.new_events} events; updated {summary.updated_events} events."
            )
            for error in summary.errors:
                st.warning(error)
            status.update(label="Stopped at quota" if summary.quota_exhausted else "Run complete",
                          state="error" if summary.quota_exhausted else "complete")
        except Exception as exc:
            status.update(label="Run failed", state="error")
            st.error(str(exc))


if submitted:
    if not topic.strip():
        st.error("Enter a topic.")
    elif not source_types:
        st.error("Select at least one source type.")
    else:
        topic_id = store.get_or_create_topic(topic, start_date, tuple(source_types))
        st.session_state["topic_id"] = topic_id
        run_and_report(topic_id, topic, start_date, tuple(source_types))

topics = store.list_topics()
if topics:
    st.subheader("Daily tracking settings")
    daily_labels = {
        f"#{row['id']} · {row['keyword']} · since {row['start_date']} · "
        f"{', '.join(json.loads(row['source_types_json']))}": row["id"]
        for row in topics
    }
    selected_ids = {row["id"] for row in topics if row["active"]}
    selected_labels = [label for label, topic_id in daily_labels.items() if topic_id in selected_ids]
    with st.form("daily_settings"):
        chosen_labels = st.multiselect(
            "Topics to track automatically every day", list(daily_labels), default=selected_labels,
            help="Only these topics are processed by the 08:00 daily scheduler.",
        )
        quota_col, minute_col = st.columns(2)
        with quota_col:
            requests_per_day = st.number_input(
                "Gemini free requests/day", min_value=1, max_value=500,
                value=min(500, daily_config["requests_per_day"]), step=1,
            )
            articles_per_day = st.number_input(
                "Maximum articles/posts across selected topics per daily run",
                min_value=1, max_value=500, value=min(500, daily_config["articles_per_day"]), step=1,
            )
        with minute_col:
            requests_per_minute = st.number_input(
                "Gemini requests/minute", min_value=1, max_value=15,
                value=min(15, daily_config["requests_per_minute"]), step=1,
            )
            articles_per_topic = st.number_input(
                "Maximum articles/posts per selected topic", min_value=1, max_value=500,
                value=min(500, daily_config["articles_per_topic"]), step=1,
            )
        st.caption(
            "This project's AI Studio screenshot shows 500 requests/day, 15 requests/minute, "
            "and 250,000 input tokens/minute. Each retry also consumes a request. "
            "These are local caps; check AI Studio if this project is shared."
        )
        save_daily = st.form_submit_button("Save daily topic selection and limits")
    if save_daily:
        try:
            store.configure_daily(
                daily_config["model"], requests_per_day, requests_per_minute,
                daily_config["tokens_per_minute"], articles_per_topic, articles_per_day,
                {daily_labels[label] for label in chosen_labels},
            )
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    topic_by_label = {
        f"#{row['id']} · {row['keyword']} · since {row['start_date']} · "
        f"{', '.join(json.loads(row['source_types_json']))}": row
        for row in topics
    }
    labels = list(topic_by_label)
    selected_id = st.session_state.get("topic_id")
    default_index = next((i for i, label in enumerate(labels) if topic_by_label[label]["id"] == selected_id), 0)
    selected_label = st.selectbox("Tracked topic", labels, index=default_index)
    selected_topic = topic_by_label[selected_label]
    st.caption(f"Automatic daily tracking: {'selected' if selected_topic['active'] else 'not selected'}")
    if st.button("Run selected topic again"):
        run_and_report(
            selected_topic["id"], selected_topic["keyword"],
            date.fromisoformat(selected_topic["start_date"]),
            tuple(json.loads(selected_topic["source_types_json"])),
        )
    event_rows = store.list_events(selected_topic["id"])
    st.subheader("Event tracking table")
    if event_rows:
        display = [
            {
                "Event": row["event_title"],
                "Publication Date": row["publication_date"],
                "Source Type": row["source_type"],
                "Source Link": row["source_link"],
                "Mention Count": row["mention_count"],
                "Last Seen Date": row["last_seen_date"],
            }
            for row in event_rows
        ]
        st.dataframe(
            display, width="stretch", hide_index=True,
            column_config={"Source Link": st.column_config.LinkColumn("Source Link", display_text="Open source")},
        )
        csv_buffer = io.StringIO()
        writer = csv.DictWriter(csv_buffer, fieldnames=list(display[0]))
        writer.writeheader()
        writer.writerows(display)
        st.download_button("Download table as CSV", csv_buffer.getvalue(), "news_events.csv", "text/csv")

        event_by_label = {f"{row['event_title']} · {row['event_date']} · #{row['id']}": row for row in event_rows}
        detail_label = st.selectbox("Inspect an event and its source reports", list(event_by_label))
        selected_event = event_by_label[detail_label]
        st.write(selected_event["event_summary"])
        if selected_event["image_observation"]:
            st.info("Visual evidence: " + selected_event["image_observation"])
        for mention in store.list_mentions(selected_event["id"]):
            st.markdown(
                f"- [{mention['title'] or mention['source_link']}]({mention['source_link']}) "
                f"— {mention['publisher_key']}, {mention['publication_date']}"
            )
    else:
        st.info("No matching events yet. Run the topic to populate the table.")
else:
    st.info("Enter a topic above to create your first tracking list.")
