# AI News Curator

A Streamlit app that finds public news and Bluesky posts, extracts events from text and images with Gemini, and keeps one row per event. Users choose which saved topics run daily.

## Run locally

Requires Python 3.11+ and a Gemini API key with access to `gemini-3.5-flash-lite`. From the repository root:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/streamlit run app.py
```

Open the local URL printed by Streamlit (usually [http://localhost:8501](http://localhost:8501)). Enter your Gemini key in the sidebar, or set `GEMINI_API_KEY` in the terminal before starting Streamlit. The sidebar key lasts only for that UI session. Keep keys out of Git.

### Try the app

1. Enter a topic, choose a start date and source types, and set **Maximum new articles/posts to analyze this run** to a small number such as 3. Click **Save topic and run now**.
2. Select the saved topic under **Tracked topic**. The table shows publication date, VLM-classified source type, source link, mention count, and last seen date. You can download it as CSV.
3. Select an event under **Inspect an event and its source reports** to see the reports behind its row. If Gemini analyzed a relevant image, expand **Image findings** to see its observation and any clearly readable chart or infographic text. Use **View source image** to check the result. An ordinary article thumbnail may have no image text.

The app stores topics and events in local `news_curator.db`; set `NEWS_CURATOR_DB` before starting the app to use another path. The local embedding model downloads on first use, so the first analysis can take longer.

To run the automated checks:

```bash
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

## Daily run

Save topics in the UI, then choose one or more under **Daily tracking settings**. Set the per-topic and total article limits and click **Save daily topic selection and limits**. New topics are not scheduled until selected.

On macOS, install the 08:00 local-time scheduler from the repository root:

```bash
scripts/setup_daily_macos.sh
```

Setup prompts for your Gemini key if `.env` does not exist and stores it locally with owner-only permissions. The key in the UI sidebar is session-only; the scheduler reads `.env`. To test the selected topics immediately, use the script that loads `.env`:

```bash
scripts/run_daily.sh
```

Check the installed job with `launchctl print gui/$(id -u)/com.aniket.ai-news-curator`. Scheduled output goes to `logs/daily.stdout.log` and `logs/daily.stderr.log`. The Mac must be on for the job to run. On another operating system, schedule `.venv/bin/news-curator run --daily` with `GEMINI_API_KEY` set in that process's environment.

The tested project's Gemini 3.5 Flash Lite limits were **500 requests/day, 15 requests/minute, and 250,000 input tokens/minute**. Check [your own project limits in AI Studio](https://ai.google.dev/gemini-api/docs/rate-limits) before changing the UI settings. The app reserves a local request slot before each Gemini call and waits for its configured minute window. It does not track input tokens, Google SDK internal retries, or requests made elsewhere in the same project. Article limits are upper bounds, not a promise of that many results. Topics least recently run get priority when the total limit is reached.

## Architecture

1. **Discover:** Search GDELT for news, fall back to Bing News RSS when needed, and search Bluesky for public posts. Apply the user's start date.
2. **Enrich:** Extract article text, date, and image URL with Trafilatura. Use the search snippet when a page cannot be read. Download only JPEG, PNG, or WebP images up to 3 MB.
3. **Extract:** Send a bounded excerpt and available image to Gemini. A Pydantic schema requires JSON with relevance, event title/summary/date, action phase, source type, entities, visual observation, up to five legible image text snippets, and evidence. This uses the existing VLM request; images consume input tokens. Apply the chosen source types after classification.
4. **Deduplicate:** Skip previously processed URLs. Compare local `bge-small-en-v1.5` embeddings and require compatible entities, dates, and action phases before merging reports.
5. **Store:** SQLite keeps topics, events, source mentions, processed URLs, daily settings, and request counts. Visual observations and OCR snippets stay attached to their source mention. The UI shows the required columns, source evidence, and CSV export.

**Count rule:** `Mention Count` is the number of distinct publishers beyond the first, so a new event starts at 0. Another URL from the same publisher updates `Last Seen Date` but not the count; a repeated URL changes neither. Updated events move to the top of the table. Publication date, source link, and source type come from the earliest observed report.

## Prompt strategy and data handling

The system instruction treats retrieved content as evidence, not instructions. It asks the model to use only supplied text and visible image details, separate event date from publication date, and distinguish proposals from approvals and rejections. For charts and infographics, it extracts only clearly legible labels, dates, and values from the image; generic thumbnails return no image text. The schema constrains the output. Email addresses and phone numbers are removed from text before model calls and from visual text shown in the UI. Only public content should be submitted.

## Scope and trade-offs

- Local SQLite works for one computer. Hosting for multiple users needs persistent external storage and authentication.
- Search is not exhaustive: this version does not paginate GDELT or Bluesky, and Bing RSS has limited history. Some pages or images cannot be fetched, and a start date does not guarantee full historical backfill.
- OCR is model-extracted evidence, so readers should check the linked image. Reports processed before this feature are not reanalyzed automatically, to avoid extra requests.
- Source type describes the publisher's role, not a factual trust score. The English-focused embedding model and fixed threshold can miss paraphrases or merge similar events. It downloads on first use.

## Production path

Use PostgreSQL with a vector index, per-source ingestion workers, and a hosted scheduler. Add labeled deduplication evaluations, prompt versioning, quota monitoring, and per-user access control.
