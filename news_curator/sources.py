"""Public news and social discovery with bounded article enrichment."""

from __future__ import annotations

import logging
import html as html_lib
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree

import requests
import trafilatura

from .models import Candidate, canonical_url


LOGGER = logging.getLogger(__name__)
HTTP_TIMEOUT_SECONDS = 12
MAX_HTML_BYTES = 1_500_000
HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update({"User-Agent": "AI-News-Curator/0.1"})


def parse_date(value: str | None, fallback: date | None = None) -> date:
    if value:
        try:
            if len(value) >= 14 and value[:14].isdigit():
                return datetime.strptime(value[:14], "%Y%m%d%H%M%S").date()
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                pass
    if fallback is not None:
        return fallback
    return datetime.now(timezone.utc).date()


def _get_json(url: str, params: dict[str, object]) -> dict:
    response = HTTP_SESSION.get(url, params=params, timeout=HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def gdelt_search(topic: str, start_date: date, limit: int = 20) -> list[Candidate]:
    """Search GDELT DOC 2.0; article date is later corrected from page metadata."""
    now = datetime.now(timezone.utc)
    params = {
        "query": topic.strip(),
        "mode": "artlist",
        "format": "json",
        "sort": "datedesc",
        "maxrecords": min(max(limit * 3, 20), 250),
        "startdatetime": start_date.strftime("%Y%m%d000000"),
        "enddatetime": now.strftime("%Y%m%d%H%M%S"),
    }
    payload = _get_json("https://api.gdeltproject.org/api/v2/doc/doc", params)
    results: list[Candidate] = []
    seen: set[str] = set()
    for item in payload.get("articles", []):
        url = item.get("url", "")
        try:
            normalized = canonical_url(url)
        except ValueError:
            continue
        if normalized in seen:
            continue
        published = parse_date(item.get("seendate"))
        if published < start_date:
            continue
        seen.add(normalized)
        results.append(Candidate(
            url=url,
            title=item.get("title", "").strip(),
            published_date=published,
            publisher=item.get("domain") or urlsplit(url).hostname or "unknown",
            image_url=item.get("socialimage") or None,
            source_hint="news",
        ))
        if len(results) >= limit:
            break
    return results


def bing_rss_search(topic: str, start_date: date, limit: int = 20) -> list[Candidate]:
    """Backup public RSS feed; extract the original publisher URL from Bing links."""
    response = HTTP_SESSION.get(
        "https://www.bing.com/news/search",
        params={"q": topic.strip(), "format": "rss"}, timeout=HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    results: list[Candidate] = []
    seen: set[str] = set()
    for item in root.findall("./channel/item"):
        title = html_lib.unescape(item.findtext("title") or "").strip()
        link = html_lib.unescape(item.findtext("link") or "").strip()
        original = parse_qs(urlsplit(link).query).get("url", [link])[0]
        try:
            normalized = canonical_url(original)
            published = parsedate_to_datetime(item.findtext("pubDate") or "").date()
        except (ValueError, TypeError):
            continue
        if published < start_date or normalized in seen:
            continue
        seen.add(normalized)
        source = next((child.text for child in item if child.tag.endswith("}Source")), None)
        image = next((child.text for child in item if child.tag.endswith("}Image")), None)
        results.append(Candidate(
            url=original, title=title, published_date=published,
            publisher=source or urlsplit(original).hostname or "unknown",
            text=html_lib.unescape(item.findtext("description") or ""),
            image_url=image, source_hint="news",
        ))
        if len(results) >= limit:
            break
    return results


def discover_news(topic: str, start_date: date, limit: int = 20) -> list[Candidate]:
    try:
        found = gdelt_search(topic, start_date, limit)
        if found:
            return found
    except (requests.RequestException, ValueError) as exc:
        LOGGER.warning("GDELT unavailable; using RSS fallback: %s", exc)
    return bing_rss_search(topic, start_date, limit)


def bluesky_search(topic: str, start_date: date, limit: int = 10) -> list[Candidate]:
    """Search public Bluesky posts and filter dates locally for API compatibility."""
    payload = _get_json(
        "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts",
        {"q": topic.strip(), "sort": "latest", "limit": min(max(limit * 3, 25), 100)},
    )
    results: list[Candidate] = []
    for item in payload.get("posts", []):
        record = item.get("record") or {}
        text = (record.get("text") or "").strip()
        handle = (item.get("author") or {}).get("handle") or "unknown"
        uri = item.get("uri", "")
        if not text or "/" not in uri:
            continue
        published = parse_date(record.get("createdAt") or item.get("indexedAt"))
        if published < start_date:
            continue
        image_url = None
        for image in (item.get("embed") or {}).get("images") or []:
            image_url = image.get("fullsize") or image.get("thumb")
            if image_url:
                break
        results.append(Candidate(
            url=f"https://bsky.app/profile/{handle}/post/{uri.rsplit('/', 1)[-1]}",
            title=text[:160],
            published_date=published,
            publisher=handle,
            text=text,
            image_url=image_url,
            source_hint="social",
        ))
        if len(results) >= limit:
            break
    return results


def enrich_article(candidate: Candidate) -> Candidate:
    """Extract public page text, original publication date, and image if available."""
    if candidate.source_hint == "social":
        return candidate
    try:
        response = HTTP_SESSION.get(candidate.url, timeout=HTTP_TIMEOUT_SECONDS, stream=True)
        response.raise_for_status()
        if "html" not in response.headers.get("content-type", "").lower():
            return candidate
        data = response.raw.read(MAX_HTML_BYTES + 1)
        if len(data) > MAX_HTML_BYTES:
            return candidate
        html = data.decode(response.encoding or "utf-8", errors="replace")
        text = trafilatura.extract(html, url=candidate.url, include_comments=False) or candidate.text
        metadata = trafilatura.extract_metadata(html)
        publication = parse_date(getattr(metadata, "date", None), candidate.published_date)
        image = getattr(metadata, "image", None) or candidate.image_url
        return Candidate(
            url=candidate.url,
            title=getattr(metadata, "title", None) or candidate.title,
            published_date=publication,
            publisher=candidate.publisher,
            text=text[:5000],
            image_url=image,
            source_hint=candidate.source_hint,
        )
    except (requests.RequestException, ValueError, TypeError) as exc:
        LOGGER.info("Article extraction unavailable for %s: %s", candidate.url, exc)
        return candidate


def download_image(url: str | None) -> tuple[bytes, str] | None:
    if not url or urlsplit(url).scheme not in {"https", "http"}:
        return None
    try:
        response = HTTP_SESSION.get(url, timeout=HTTP_TIMEOUT_SECONDS, stream=True)
        response.raise_for_status()
        mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if mime not in {"image/jpeg", "image/png", "image/webp"}:
            return None
        data = response.raw.read(3_000_001)
        if len(data) > 3_000_000 or len(data) < 100:
            return None
        return data, mime
    except requests.RequestException:
        return None
