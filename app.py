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
from news_curator.storage import Store
from news_curator.vlm import GeminiAnalyzer


st.set_page_config(page_title="AI News Curator", page_icon="🗞️", layout="wide")
st.title("AI News Curator")
st.caption("Track one event per row, with every independent report retained as evidence.")

db_path = os.getenv("NEWS_CURATOR_DB", "news_curator.db")
store = Store(db_path)

with st.sidebar:
    st.header("Run settings")
    st.write("Model:", os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"))
    st.write("Storage: local SQLite")
    session_key = st.text_input(
        "Gemini API key (optional if set in environment)", type="password",
        help="Used only for this session; it is not saved in the database.",
    )
    st.caption("This app sends public article excerpts and available images to Gemini. Do not enter private or sensitive topics.")

with st.form("research"):
    topic = st.text_input("Topic or keyword", placeholder="e.g. electric vehicle battery policy")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Search from", value=date.today() - timedelta(days=7), max_value=date.today())
    with col2:
        source_types = st.multiselect("Source types", SOURCE_TYPES, default=list(SOURCE_TYPES))
    max_items = st.slider("Maximum new articles/posts to analyze this run", 1, 50, 20)
    submitted = st.form_submit_button("Save topic and run now", type="primary")


def run_and_report(topic_id: int, keyword: str, since: date, types: tuple[str, ...]) -> None:
    if not session_key and not os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
        st.error("Set GEMINI_API_KEY before running live VLM analysis. See README.md.")
        return
    with st.status("Discovering and analyzing sources...", expanded=True) as status:
        try:
            summary = run_topic(
                store, topic_id, keyword, since, types, max_items,
                analyzer=GeminiAnalyzer(api_key=session_key or None),
            )
            st.write(
                f"Found {summary.discovered}; analyzed {summary.analyzed}; "
                f"created {summary.new_events} events; updated {summary.updated_events} events."
            )
            for error in summary.errors:
                st.warning(error)
            status.update(label="Run complete", state="complete")
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
    topic_by_label = {
        f"{row['keyword']} · since {row['start_date']} · {', '.join(json.loads(row['source_types_json']))}": row
        for row in topics
    }
    labels = list(topic_by_label)
    selected_id = st.session_state.get("topic_id")
    default_index = next((i for i, label in enumerate(labels) if topic_by_label[label]["id"] == selected_id), 0)
    selected_label = st.selectbox("Tracked topic", labels, index=default_index)
    selected_topic = topic_by_label[selected_label]
    daily_enabled = bool(selected_topic["active"])
    st.caption(f"Automatic daily tracking: {'on' if daily_enabled else 'paused'}")
    if st.button("Pause daily tracking" if daily_enabled else "Resume daily tracking"):
        store.set_active(selected_topic["id"], not daily_enabled)
        st.rerun()
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
