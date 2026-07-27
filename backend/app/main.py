"""
FastAPI Video Generator - Main Application Entry Point
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api.v1 import router as api_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(settings.LOG_FILE)
    ]
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    logger.info("Starting FastAPI Video Generator...")
    logger.info(f"Environment: {settings.ENVIRONMENT}")

    # Ensure required directories exist
    settings.ensure_directories()

    # Start the trending pipeline scheduler (no-op unless TRENDS_ENABLED=true)
    from app.services.trends_scheduler import start_scheduler, shutdown_scheduler
    start_scheduler()

    yield

    shutdown_scheduler()
    logger.info("Shutting down FastAPI Video Generator...")


# Create FastAPI application
app = FastAPI(
    title=settings.APP_NAME,
    description="AI-powered educational video generation service",
    version=settings.VERSION,
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory=settings.STATIC_DIR), name="static")

# Include API routes
app.include_router(api_router, prefix="/api/v1")


# Built single-page app, when it has been bundled into the image. Serving the UI
# from the same origin as the API gives deployments ONE public URL and removes
# CORS from the equation entirely; the split Vercel + API deploy still works when
# this directory is absent.
SPA_DIR = settings.STATIC_DIR / "app"
SPA_INDEX = SPA_DIR / "index.html"


@app.get("/")
async def root():
    """Serve the bundled UI when present, otherwise describe the API."""
    if SPA_INDEX.exists():
        return FileResponse(SPA_INDEX)
    return {
        "service": settings.APP_NAME,
        "version": settings.VERSION,
        "docs": "/docs",
        "api": "/api/v1",
    }


@app.get("/health")
async def health_check():
    """
    Health check + readiness report.

    Always returns 200 so the platform's health probe passes even when an
    optional integration is unconfigured — `ready` tells you whether the app can
    actually render, and `checks` says exactly what is missing.
    """
    from app.services.genblaze_service import genblaze
    from app.services.storage_service import storage

    b2 = storage.status()
    checks = {
        "script_llm": bool(settings.GEMINI_API_KEY) or settings.SCRIPT_PROVIDER == "ollama",
        "backblaze_b2": b2["available"],
        "genblaze": genblaze.enabled,
        "genblaze_sink": genblaze.status()["sink"] is not None,
        "stock_images": bool(settings.PEXELS_API_KEY),
        "gmi_cloud": settings.gmi_configured,
    }
    return {
        "status": "healthy",
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        # Renders need a script model; everything else degrades gracefully.
        "ready": checks["script_llm"],
        "durable_storage": checks["backblaze_b2"],
        "checks": checks,
        "backblaze_b2": b2,
        "genblaze": genblaze.status(),
    }


# Mounted LAST so /health, /docs and /api/v1/* keep priority — this only catches
# the SPA's own asset requests (/assets/*, /favicon.svg, ...).
if SPA_DIR.exists():
    app.mount("/", StaticFiles(directory=SPA_DIR, html=True), name="spa")
    logger.info("Serving bundled UI from %s", SPA_DIR)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )
