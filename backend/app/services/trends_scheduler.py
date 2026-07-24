"""
Trending Pipeline Scheduler

Runs the Economic Times trending -> short-form video pipeline on a fixed interval
using APScheduler's BackgroundScheduler (jobs run in a worker thread, so the
blocking render pipeline never stalls the FastAPI event loop).

Flow per run:
    1. fetch + rank ET articles (heuristic), excluding already-processed links
    2. take the top N
    3. generate a short-form video for each (sequentially) via VideoGenerationService
    4. optionally auto-publish to YouTube (TRENDS_AUTO_PUBLISH)
    5. mark those article links as processed (dedup)
"""
import logging
import re
import threading
from typing import List, Optional

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import settings
from app.schemas.video import VideoGenerationRequest
from app.services import trends_service

logger = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None
_run_lock = threading.Lock()

# Lightweight status surface for the /trends API.
last_run = {
    "ran_at": None,        # epoch seconds
    "generated": [],       # [{title, link, video_filename, published}]
    "considered": 0,
    "error": None,
}

JOB_ID = "et_trending_pipeline"


def _summary_to_key_points(summary: str, limit: int = 5) -> List[str]:
    if not summary:
        return []
    parts = re.split(r"(?<=[.!?])\s+", summary)
    return [p.strip() for p in parts if p.strip()][:limit]


def run_pipeline_once(top_n: Optional[int] = None, auto_publish: Optional[bool] = None) -> dict:
    """
    Execute one trending-pipeline run. Safe to call from the scheduler or the API.
    Returns a summary dict; never raises (errors are captured in last_run).
    """
    if not _run_lock.acquire(blocking=False):
        logger.info("Trending pipeline already running; skipping this trigger.")
        return {"skipped": True, "reason": "already_running"}

    import time as _time
    n = top_n or settings.TRENDS_TOP_N
    publish = settings.TRENDS_AUTO_PUBLISH if auto_publish is None else bool(auto_publish)
    generated: List[dict] = []
    last_run["error"] = None
    try:
        logger.info(f"Trending pipeline run started (top_n={n}).")

        # Surface the fetch/rank stages to the live pipeline UI.
        from app.services.video_service import render_status, set_stage
        render_status.update({"active": True, "error": None})
        set_stage("fetch")
        articles = trends_service.fetch_articles()
        set_stage("trend")
        excluded = trends_service.load_processed_links()
        articles = trends_service.rank_articles(articles, exclude_links=excluded)[:n]
        last_run["considered"] = len(articles)

        if not articles:
            logger.info("No new trending articles to process.")
            set_stage(None)
            render_status["active"] = False
            return {"generated": [], "considered": 0}

        # Imported lazily so the scheduler/API modules don't require the heavy
        # render dependencies (torch, moviepy, kokoro) just to report status.
        from app.services.video_service import VideoGenerationService
        service = VideoGenerationService()
        processed_links: List[str] = []

        for art in articles:
            try:
                request = VideoGenerationRequest(
                    topic=art.title[:200],
                    duration=settings.TRENDS_VIDEO_DURATION,
                    key_points=_summary_to_key_points(art.summary),
                    style="educational",
                    publish_to_youtube=publish,
                )
                video_filename = service._generate_video_filename(art.title)
                logger.info(f"Generating trending video for: {art.title!r}")
                service._generate_video_task(request, video_filename)
                generated.append(
                    {
                        "title": art.title,
                        "link": art.link,
                        "video_filename": video_filename,
                        "published": publish,
                    }
                )
                processed_links.append(art.link)
            except Exception as exc:  # noqa: BLE001 - one article shouldn't kill the run
                logger.error(f"Failed to generate video for {art.link}: {exc}", exc_info=True)
                # Still mark as processed so we don't retry a broken article forever.
                processed_links.append(art.link)

        if processed_links:
            trends_service.mark_processed(processed_links)

        logger.info(f"Trending pipeline run complete. Generated {len(generated)} video(s).")
        return {"generated": generated, "considered": len(articles)}

    except Exception as exc:  # noqa: BLE001
        logger.error(f"Trending pipeline run failed: {exc}", exc_info=True)
        last_run["error"] = str(exc)
        return {"error": str(exc)}
    finally:
        last_run["ran_at"] = _time.time()
        last_run["generated"] = generated
        _run_lock.release()


def start_scheduler() -> None:
    """Start the background scheduler if the feature is enabled."""
    global _scheduler

    if not settings.TRENDS_ENABLED:
        logger.info("Trending pipeline disabled (set TRENDS_ENABLED=true to activate).")
        return

    if _scheduler and _scheduler.running:
        logger.info("Trending scheduler already running.")
        return

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        run_pipeline_once,
        trigger="interval",
        hours=settings.TRENDS_INTERVAL_HOURS,
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        f"Trending scheduler started: every {settings.TRENDS_INTERVAL_HOURS}h, "
        f"top {settings.TRENDS_TOP_N}, auto_publish={settings.TRENDS_AUTO_PUBLISH}."
    )

    if settings.TRENDS_RUN_ON_STARTUP:
        logger.info("TRENDS_RUN_ON_STARTUP=true -> triggering an immediate run.")
        _scheduler.add_job(run_pipeline_once, id=f"{JOB_ID}_startup", replace_existing=True)


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Trending scheduler stopped.")
    _scheduler = None


def scheduler_status() -> dict:
    running = bool(_scheduler and _scheduler.running)
    next_run = None
    if running:
        job = _scheduler.get_job(JOB_ID)
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()
    return {
        "enabled": settings.TRENDS_ENABLED,
        "running": running,
        "interval_hours": settings.TRENDS_INTERVAL_HOURS,
        "top_n": settings.TRENDS_TOP_N,
        "auto_publish": settings.TRENDS_AUTO_PUBLISH,
        "next_run": next_run,
        "last_run": last_run,
    }
