"""
Trending News Service

Monitors Economic Times RSS feeds, normalizes articles, and ranks them with a
free, deterministic heuristic:

    score = (W_RECENCY * recency) + (W_TREND * cross_feed_overlap)

  - recency: newer articles score higher (linear decay over TRENDS_MAX_AGE_HOURS)
  - cross_feed_overlap: articles whose salient keywords appear across many other
    articles are "trending" (the same story surfacing in multiple feeds/sections)

A small JSON state file records article links we've already turned into videos so
the scheduler never regenerates the same story.
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import feedparser

from app.core.config import settings

logger = logging.getLogger(__name__)

W_RECENCY = 0.45
W_TREND = 0.55

# Minimal stopword list so keyword overlap reflects real topics, not filler words.
_STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "all", "any", "can", "had",
    "her", "was", "one", "our", "out", "day", "get", "has", "him", "his", "how",
    "man", "new", "now", "old", "see", "two", "way", "who", "boy", "did", "its",
    "let", "put", "say", "she", "too", "use", "with", "that", "this", "from",
    "have", "will", "your", "what", "when", "they", "them", "than", "then",
    "into", "over", "after", "amid", "says", "said", "more", "most", "such",
    "india", "indias", "economictimes", "rs", "cr", "crore", "lakh", "could",
    "would", "may", "set", "top", "big", "near", "high", "low", "year", "years",
}

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z']+")


@dataclass
class Article:
    title: str
    link: str
    summary: str
    source: str
    published_ts: Optional[float]  # epoch seconds (UTC), None if unknown
    score: float = 0.0
    recency: float = 0.0
    trend: float = 0.0
    keywords: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "link": self.link,
            "summary": self.summary,
            "source": self.source,
            "published_ts": self.published_ts,
            "score": round(self.score, 4),
            "recency": round(self.recency, 4),
            "trend": round(self.trend, 4),
            "keywords": self.keywords,
        }


def _clean_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _tokens(text: str) -> List[str]:
    return [
        tok.lower()
        for tok in _TOKEN_RE.findall(text or "")
        if len(tok) >= 4 and tok.lower() not in _STOPWORDS
    ]


def _entry_timestamp(entry) -> Optional[float]:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return time.mktime(parsed)
            except (OverflowError, ValueError):
                continue
    return None


def fetch_articles(feed_urls: Optional[List[str]] = None) -> List[Article]:
    """Fetch and normalize articles from the configured Economic Times feeds."""
    urls = feed_urls or settings.trends_feed_urls_list
    articles: List[Article] = []
    seen_links = set()

    for url in urls:
        try:
            parsed = feedparser.parse(url)
        except Exception as exc:  # noqa: BLE001 - one bad feed shouldn't break the run
            logger.warning(f"Failed to parse feed {url}: {exc}")
            continue

        source = _clean_html(getattr(parsed.feed, "title", "")) or url
        for entry in parsed.entries:
            link = (entry.get("link") or "").strip()
            title = _clean_html(entry.get("title", ""))
            if not link or not title or link in seen_links:
                continue
            seen_links.add(link)
            articles.append(
                Article(
                    title=title,
                    link=link,
                    summary=_clean_html(entry.get("summary", "")),
                    source=source,
                    published_ts=_entry_timestamp(entry),
                )
            )

    logger.info(f"Fetched {len(articles)} unique articles from {len(urls)} feeds.")
    return articles


def rank_articles(
    articles: List[Article],
    max_age_hours: Optional[float] = None,
    exclude_links: Optional[set] = None,
) -> List[Article]:
    """Score and rank articles by recency + cross-feed keyword overlap."""
    max_age = max_age_hours if max_age_hours is not None else settings.TRENDS_MAX_AGE_HOURS
    exclude = exclude_links or set()
    now = time.time()
    max_age_seconds = max_age * 3600.0

    # Keep fresh, unprocessed articles only.
    candidates: List[Article] = []
    for art in articles:
        if art.link in exclude:
            continue
        if art.published_ts is not None and (now - art.published_ts) > max_age_seconds:
            continue
        art.keywords = _tokens(f"{art.title} {art.summary}")
        candidates.append(art)

    if not candidates:
        return []

    # Document frequency of each keyword across the candidate set.
    doc_freq: dict = {}
    for art in candidates:
        for term in set(art.keywords):
            doc_freq[term] = doc_freq.get(term, 0) + 1

    raw_trend = []
    for art in candidates:
        # "Trending" = keywords shared with other articles (df - 1 per unique term).
        unique_terms = set(art.keywords)
        overlap = sum(doc_freq[t] - 1 for t in unique_terms)
        raw_trend.append(overlap)

    max_trend = max(raw_trend) or 1

    for art, overlap in zip(candidates, raw_trend):
        # Recency: 1.0 = just now, 0.0 = at/over max age. Unknown date -> neutral 0.5.
        if art.published_ts is None:
            art.recency = 0.5
        else:
            age = now - art.published_ts
            art.recency = max(0.0, 1.0 - (age / max_age_seconds))
        art.trend = overlap / max_trend
        # Surface a few of the most "trending" keywords for transparency.
        art.keywords = sorted(
            set(art.keywords), key=lambda t: doc_freq[t], reverse=True
        )[:6]
        art.score = (W_RECENCY * art.recency) + (W_TREND * art.trend)

    candidates.sort(key=lambda a: a.score, reverse=True)
    return candidates


def get_trending(
    top_n: Optional[int] = None,
    exclude_processed: bool = True,
) -> List[Article]:
    """Convenience: fetch + rank, optionally excluding already-processed links."""
    exclude = load_processed_links() if exclude_processed else set()
    ranked = rank_articles(fetch_articles(), exclude_links=exclude)
    if top_n:
        return ranked[:top_n]
    return ranked


# --- Scheduler + dedup state -----------------------------------------------
#
# Held in B2 as well as on local disk. The hosted filesystem is ephemeral, so a
# local-only record is erased by every redeploy — which both re-generated
# articles that had already been published and, worse, reset the scheduler's
# interval so the job never came due. B2 is the durable copy; the local file is
# the fast path and the fallback when B2 is not configured.

STATE_OBJECT_KEY = "flux/state/trends.json"


def _read_state() -> Dict[str, Any]:
    from app.services.storage_service import storage

    remote: Dict[str, Any] = {}
    try:
        if storage.available:
            remote = storage.read_json(STATE_OBJECT_KEY) or {}
    except Exception as exc:  # noqa: BLE001 - fall back to the local copy
        logger.warning(f"Could not read trends state from B2: {exc}")

    local: Dict[str, Any] = {}
    state_file = settings.TRENDS_STATE_FILE
    if state_file and state_file.exists():
        try:
            local = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Could not read trends state file: {exc}")

    # Whichever ran more recently wins; a fresh container has no local file at
    # all, so this is normally just the B2 copy.
    if float(remote.get("last_run_at") or 0) >= float(local.get("last_run_at") or 0):
        return remote or local
    return local


def _write_state(state: Dict[str, Any]) -> None:
    state_file = settings.TRENDS_STATE_FILE
    if state_file:
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Could not write trends state file: {exc}")

    from app.services.storage_service import storage

    try:
        if storage.available:
            storage.put_json(state, STATE_OBJECT_KEY)
    except Exception as exc:  # noqa: BLE001 - never fail a run over bookkeeping
        logger.warning(f"Could not persist trends state to B2: {exc}")


def load_processed_links() -> set:
    return set(_read_state().get("processed", []))


def last_run_at() -> Optional[float]:
    """Epoch seconds of the last completed pipeline run, or None if never."""
    value = _read_state().get("last_run_at")
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def record_run(when: Optional[float] = None) -> None:
    """Stamp a completed run so a restart cannot reset the schedule."""
    state = _read_state()
    state["last_run_at"] = when if when is not None else time.time()
    _write_state(state)


def mark_processed(links: List[str], keep_last: int = 500) -> None:
    """Record links as processed so they're never regenerated (bounded history)."""
    state = _read_state()
    existing = list(state.get("processed", []))
    # Preserve order roughly: old first, then newly added; trim to keep_last.
    combined = existing + [l for l in links if l not in existing]
    state["processed"] = combined[-keep_last:]
    _write_state(state)
