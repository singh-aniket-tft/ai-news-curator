# AI News Curator

A daily, event-level news tracker. Enter a topic, start date, and source types in a Streamlit UI; the app finds public articles and Bluesky posts, analyzes text and available images with a Gemini vision-language model, and maintains a deduplicated table.

## Run locally

Requires Python 3.11+ and a Gemini API key with access to `gemini-3.5-flash-lite` (or another image-capable structured-output Gemini model). The free tier is subject to the project's quotas shown in Google AI Studio.

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
export GEMINI_API_KEY="your-key"
.venv/bin/streamlit run app.py
```

Open **http://localhost:8501**. You can also enter a Gemini key in the sidebar instead of setting the environment variable. The key is not stored in SQLite. The app stores its history in `news_curator.db` in the project folder; set `NEWS_CURATOR_DB` to change this path.

To verify the core behavior:

```bash
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

## Daily run

The UI saves each topic and its filters. To schedule a topic, select it under **Daily tracking settings**, choose the per-topic and total article limits, and click **Save daily topic selection and limits**. You may select multiple topics. Newly saved topics are not scheduled until selected. The same pipeline runs from the command line:

```bash
.venv/bin/news-curator add "battery recycling" --since 2026-09-01
.venv/bin/news-curator list
.venv/bin/news-curator run --daily
```

On macOS, configure a **new** key for unattended runs and install the user LaunchAgent with one command in Terminal:

```bash
scripts/setup_daily_macos.sh
```

Setup prompts without echoing the key and writes a gitignored `.env` file with owner-only permissions. It installs `~/Library/LaunchAgents/com.aniket.ai-news-curator.plist` for **08:00 local time every day**. Check registration with `launchctl print gui/$(id -u)/com.aniket.ai-news-curator`. To trigger an immediate test, run `scripts/run_daily.sh`; read `logs/daily.stdout.log` and `logs/daily.stderr.log` for scheduled results. The Mac must be powered on; a scheduled launch missed during sleep runs after wake, while a launch missed when powered off waits for the next scheduled time. See [Apple's launchd scheduling guide](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html).

The daily script uses the topic selection and article limits saved in the UI. The displayed free-tier ceiling for this project is **500 requests/day, 15 requests/minute, and 250,000 input tokens/minute** for Gemini 3.5 Flash Lite. The article limit can be set up to 500, but one article can use more than one request if a retry occurs. The app records its own requests per Pacific day, reserves a slot before each SDK call, and waits when its 15-request rolling minute window is full. It stops at the local daily request cap. AI Studio is the source of truth for actual project usage, which can also include other apps or users; the app does not track input tokens locally. These values are specific to this project's model and quota, so check [Google's rate-limit documentation](https://ai.google.dev/gemini-api/docs/rate-limits) and your AI Studio dashboard if using a different project. Topics least recently run get priority if the total article cap is reached. The sidebar API key is session-only and does not reach the scheduler.

## Architecture

1. **Discover:** GDELT DOC search for public news; Bing News RSS if GDELT times out or rate-limits. Bluesky search supplies public social posts. The start date is applied to returned items.
2. **Enrich:** Trafilatura extracts article text, publication metadata, and an image URL. Blocked or unavailable pages fall back to search-result title and snippet. Image downloads are limited to 3 MB and JPEG, PNG, or WebP.
3. **Analyze:** Gemini receives a bounded excerpt and, when available, the image. A Pydantic response schema requires JSON fields for relevance, event title/summary/date, entities, source type, visual observation, and evidence. The four source categories are *Authoritative/Official*, *Marketing/Commercial*, *Social Media*, and *Other*. Selection is applied after classification.
4. **Deduplicate:** Canonical URLs prevent repeat VLM calls. A local ONNX `bge-small-en-v1.5` embedding compares event descriptions. A conservative similarity threshold, entity overlap, event-date window, and action-phase guard decide whether a new report joins an existing event. Proposing, approving, and rejecting the same policy remain separate events. No paid embedding API is used.
5. **Persist and display:** SQLite has separate `topics`, `events`, `mentions`, `processed_urls`, `daily_config`, and `vlm_requests` tables. Every source link is retained. The UI displays the required columns and sorts by last-seen date, then update time. It also lets reviewers inspect all reports and download CSV.

**Count rule:** `Mention Count` means additional distinct publishers beyond the representative source, so a new event starts at 0. A new URL from an existing publisher updates last seen but does not increase the count. Reprocessing the same URL changes neither. The event row's publication date is the earliest observed report date; its source link and type come from that earliest report. `Last Seen Date` is the date a new report was discovered.

## Prompt strategy and data handling

The system instruction tells Gemini to treat article text as evidence, never instructions; to use only supplied text and visible image content; to distinguish event date from publication date and proposal from approval or rejection; and to leave visual observations empty for generic images. The schema constrains categories, phases, and dates. The app removes email addresses and phone numbers from text sent to the model and does not send API keys or local files. Use only public topics and content; the Gemini free tier may use inputs and outputs to improve Google products.

## Scope and trade-offs

- The MVP is a **local app with an interactive live table**, which the assignment allows. SQLite remains durable on that computer. A hosted Streamlit app would need an external database because Community Cloud does not guarantee persistent local files.
- GDELT may return 429 responses. The RSS fallback keeps recent-news testing usable but is not exhaustive, and date backfills may be incomplete. Bluesky is the only social platform in this MVP.
- Article limits are ceilings, not guaranteed result counts. A single GDELT query returns at most 250 results and a single Bluesky query at most 100; this MVP does not paginate older source results.
- Source type is a classification of publisher role, not a factual trust score. Image analysis happens only when an image can be fetched. Some article pages block extraction.
- The local embedding model downloads on first use. Its English-focused model and fixed similarity thresholds can miss multilingual paraphrases or merge very similar events; the test suite covers the main update rules, not a full retrieval benchmark.
- A live Gemini run was verified locally on 2026-09-30: 12 source reports produced 9 event rows, with image analysis on 11 reports. A reviewer's own API key and available project quota are still required to reproduce the run.

## Production path

Move history to PostgreSQL, add a vector index and per-source ingestion workers, and use a hosted scheduler and queue for retries. Store immutable source evidence, version extraction prompts, evaluate deduplication on labeled event pairs, and add monitoring for source failures, quota use, and model drift. Add authentication and per-user topic ownership before hosting for multiple users.
