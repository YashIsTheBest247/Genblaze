"""
Trending Pipeline Scheduler

Runs the Economic Times trending -> short-form video pipeline at fixed times of
day using APScheduler's BackgroundScheduler (jobs run in a worker thread, so the
blocking render pipeline never stalls the FastAPI event loop).

Schedule: TRENDS_SCHEDULE_HOURS in TRENDS_TIMEZONE — 08:00, 14:00 and 20:00
Asia/Kolkata by default, TRENDS_TOP_N videos per slot. A slot the container slept
through is caught up shortly after boot, within _SLOT_GRACE.

Flow per run:
    1. fetch + rank ET articles (heuristic), excluding already-processed links
    2. take the top N
    3. generate a short-form video for each (sequentially) via VideoGenerationService
    4. publish to YouTube when TRENDS_AUTO_PUBLISH or YOUTUBE_AUTO_UPLOAD is set
    5. mark those article links as processed (dedup), and stamp the run in B2 so
       a redeploy resumes the schedule instead of forgetting it
"""
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

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

# How long after boot an overdue run is allowed to start. Long enough for the
# app to finish coming up and answer health checks, short enough that a
# redeploy still produces videos the same day.
STARTUP_GRACE_MINUTES = 3

# How long after a slot a catch-up is still worth doing. Past this the news the
# slot would have covered is stale, and the next slot is close enough anyway.
_SLOT_GRACE = timedelta(hours=3)


def _missed_slot(hours: List[int], tz) -> Optional[datetime]:
    """
    The most recent scheduled slot that passed without a run, if any.

    Returns None when the last run already covers it, when no slot has passed
    today yet, or when the slot is too old to be worth catching up.
    """
    now = datetime.now(tz)
    passed = [
        now.replace(hour=h, minute=0, second=0, microsecond=0)
        for h in hours
        if now.replace(hour=h, minute=0, second=0, microsecond=0) <= now
    ]
    if not passed:
        return None
    slot = max(passed)
    if now - slot > _SLOT_GRACE:
        return None
    previous = trends_service.last_run_at()
    if previous is not None and datetime.fromtimestamp(previous, tz=timezone.utc) >= slot:
        return None
    return slot


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
        # Durably stamp the run, so a restart resumes the schedule instead of
        # restarting the interval from zero. Recorded even on failure: a run that
        # errors should not make the next boot treat the pipeline as overdue and
        # immediately retry the same broken thing in a loop.
        try:
            trends_service.record_run(last_run["ran_at"])
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Could not record the run timestamp: {exc}")
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

    # Fixed times of day, not "every N hours". An interval trigger fires the
    # first time a whole interval AFTER the process starts, so on a host that
    # redeploys more often than the interval it never came due at all — which is
    # why nothing had ever been published even though the scheduler reported
    # itself healthy on every boot. Wall-clock slots are also what a viewer
    # experiences as a schedule: the same times daily, not times that drift by
    # however long the last deploy took.
    hours = settings.trends_schedule_hours
    tz = ZoneInfo(settings.TRENDS_TIMEZONE)

    _scheduler = BackgroundScheduler(daemon=True, timezone=tz)
    _scheduler.add_job(
        run_pipeline_once,
        trigger=CronTrigger(hour=",".join(str(h) for h in hours), minute=0, timezone=tz),
        id=JOB_ID,
        max_instances=1,
        # A slot that arrives while the previous run is still going is dropped
        # rather than queued: renders take minutes, and stacking them up would
        # publish a backlog all at once hours later.
        coalesce=True,
        misfire_grace_time=int(_SLOT_GRACE.total_seconds()),
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "Trending scheduler started: %s daily at %s (%s), %d video(s) per slot, "
        "auto_publish=%s, youtube_auto_upload=%s.",
        len(hours), ", ".join(f"{h:02d}:00" for h in hours), settings.TRENDS_TIMEZONE,
        settings.TRENDS_TOP_N, settings.TRENDS_AUTO_PUBLISH, settings.YOUTUBE_AUTO_UPLOAD,
    )

    # Catch up a slot the container slept through. Without this, a deploy at
    # 08:05 would skip the 08:00 video entirely and wait until 14:00.
    missed = _missed_slot(hours, tz)
    if missed is not None:
        logger.info(
            "Slot %s was missed (last run %s); catching up shortly after boot.",
            missed.isoformat(),
            trends_service.last_run_at() or "never",
        )
        _scheduler.add_job(
            run_pipeline_once,
            trigger="date",
            run_date=datetime.now(tz) + timedelta(minutes=STARTUP_GRACE_MINUTES),
            id=f"{JOB_ID}_catchup",
            replace_existing=True,
        )
    elif settings.TRENDS_RUN_ON_STARTUP:
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
        "schedule_hours": settings.trends_schedule_hours,
        "timezone": settings.TRENDS_TIMEZONE,
        "top_n": settings.TRENDS_TOP_N,
        "auto_publish": settings.TRENDS_AUTO_PUBLISH,
        # Whether a scheduled render actually reaches YouTube: the per-run flag
        # above OR the global switch is enough. Reported here because the two
        # settings disagreeing is exactly the confusion worth surfacing.
        "publishes_to_youtube": bool(settings.TRENDS_AUTO_PUBLISH or settings.YOUTUBE_AUTO_UPLOAD),
        "youtube_auto_upload": settings.YOUTUBE_AUTO_UPLOAD,
        "next_run": next_run,
        # Survives restarts (kept in B2), unlike `last_run` which is in-process
        # only and reads as "never ran" after every redeploy.
        "last_run_at_persisted": trends_service.last_run_at(),
        "last_run": last_run,
    }
