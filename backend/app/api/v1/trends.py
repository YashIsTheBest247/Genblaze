"""
Trending Pipeline API Endpoints

Read-only insight into the Economic Times trending pipeline plus a manual trigger.
The pipeline itself runs automatically on a schedule (see trends_scheduler).
"""
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.services import trends_service
from app.services import trends_scheduler

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/status")
async def trends_status():
    """Scheduler status, next run time, and the last run's results."""
    return trends_scheduler.scheduler_status()


@router.get("/preview")
async def trends_preview(top_n: int = Query(10, ge=1, le=50)):
    """
    Preview the current ranked trending articles WITHOUT generating anything.
    Excludes articles already processed by the pipeline.
    """
    try:
        articles = trends_service.get_trending(top_n=top_n, exclude_processed=True)
        return {"count": len(articles), "articles": [a.to_dict() for a in articles]}
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to build trends preview: {exc}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Failed to fetch trends: {exc}")


@router.post("/run")
async def trends_run_now(
    background_tasks: BackgroundTasks,
    top_n: int = Query(None, ge=1, le=20),
    auto_publish: bool = Query(None, description="Override TRENDS_AUTO_PUBLISH for this run"),
):
    """
    Trigger one pipeline run immediately (in the background). Generation still
    happens asynchronously; poll /trends/status for results.
    """
    background_tasks.add_task(trends_scheduler.run_pipeline_once, top_n, auto_publish)
    return {"success": True, "message": "Trending pipeline run triggered."}
