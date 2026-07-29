"""
YouTube Publishing Service

Loads stored OAuth credentials (refreshing the access token via the saved
refresh_token when needed) and uploads finished videos with a resumable upload.
Privacy defaults to "private" via settings.YOUTUBE_PRIVACY_STATUS.
"""
import logging
from pathlib import Path
from typing import List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from app.core.config import settings

logger = logging.getLogger(__name__)

# Full youtube scope (covers videos.insert). Must match the stored token's scope.
SCOPES = ["https://www.googleapis.com/auth/youtube"]


class YouTubeServiceError(Exception):
    """Raised when YouTube publishing cannot proceed."""


def _load_credentials() -> Credentials:
    """
    Load credentials, preferring the YOUTUBE_TOKEN_JSON env var (for deployments
    like Render where secrets/ is gitignored and the filesystem is ephemeral),
    falling back to the secrets/youtube_token.json file for local dev.
    Refreshes the access token when expired and persists it back when possible.
    """
    import json

    token_file: Path = settings.YOUTUBE_TOKEN_FILE
    token_json = (settings.YOUTUBE_TOKEN_JSON or "").strip()

    if token_json:
        try:
            creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
        except Exception as exc:  # noqa: BLE001
            raise YouTubeServiceError(
                f"YOUTUBE_TOKEN_JSON env var is not valid token JSON: {exc}"
            ) from exc
    elif token_file and token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    else:
        raise YouTubeServiceError(
            f"No YouTube credentials found. Set YOUTUBE_TOKEN_JSON, or create "
            f"{token_file} via the OAuth flow (python authorize_youtube.py)."
        )

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            logger.info("YouTube access token expired; refreshing via refresh_token...")
            creds.refresh(Request())
            # Best-effort persist of the refreshed token (no-op on read-only/ephemeral FS).
            try:
                if token_file:
                    token_file.parent.mkdir(parents=True, exist_ok=True)
                    token_file.write_text(creds.to_json(), encoding="utf-8")
                    logger.info("Refreshed YouTube token saved to file.")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Could not persist refreshed token (continuing): {exc}")
        else:
            raise YouTubeServiceError(
                "Stored YouTube credentials are invalid and cannot be refreshed "
                "(missing refresh_token). Re-run the OAuth flow."
            )

    return creds


def readiness() -> dict:
    """
    Can this deployment publish to YouTube? Reported by /health.

    Deliberately offline — it inspects the stored credentials rather than
    calling Google, so the health endpoint stays fast and quota-free. A
    configured-but-revoked token still reports ready; the upload itself is
    where that surfaces.
    """
    import json

    token_json = (settings.YOUTUBE_TOKEN_JSON or "").strip()
    token_file: Path = settings.YOUTUBE_TOKEN_FILE
    source = None
    detail = None

    if token_json:
        source = "YOUTUBE_TOKEN_JSON"
        try:
            data = json.loads(token_json)
            if not data.get("refresh_token"):
                detail = "token has no refresh_token — it will stop working once it expires"
        except Exception as exc:  # noqa: BLE001
            return {"configured": True, "ready": False, "source": source,
                    "error": f"not valid token JSON: {exc}"}
    elif token_file and token_file.exists():
        source = str(token_file)
    else:
        return {
            "configured": False,
            "ready": False,
            "source": None,
            "error": "No YouTube credentials. Set YOUTUBE_TOKEN_JSON to publish.",
        }

    return {
        "configured": True,
        "ready": True,
        "source": source,
        "auto_upload": settings.YOUTUBE_AUTO_UPLOAD,
        "privacy_status": settings.YOUTUBE_PRIVACY_STATUS,
        "warning": detail,
    }


def _build_client():
    creds = _load_credentials()
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload_video(
    file_path: Path,
    title: str,
    description: str = "",
    tags: Optional[List[str]] = None,
    privacy_status: Optional[str] = None,
    category_id: Optional[str] = None,
) -> dict:
    """
    Upload a video file to YouTube and return {"video_id", "url"}.

    Raises YouTubeServiceError on any failure so the caller can log/handle it
    without crashing the whole pipeline.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise YouTubeServiceError(f"Video file not found: {file_path}")

    privacy = privacy_status or settings.YOUTUBE_PRIVACY_STATUS
    category = category_id or settings.YOUTUBE_CATEGORY_ID
    video_tags = tags if tags is not None else settings.youtube_tags_list

    # YouTube limits: title <= 100 chars, description <= 5000 chars.
    body = {
        "snippet": {
            "title": (title or "Untitled")[:100],
            "description": (description or "")[:5000],
            "tags": video_tags,
            "categoryId": category,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    try:
        youtube = _build_client()
        media = MediaFileUpload(str(file_path), chunksize=-1, resumable=True)
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        logger.info(f"Uploading '{file_path.name}' to YouTube (privacy={privacy})...")
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"YouTube upload progress: {int(status.progress() * 100)}%")

        video_id = response.get("id")
        url = f"https://youtu.be/{video_id}"
        logger.info(f"YouTube upload complete: {url}")
        return {"video_id": video_id, "url": url}

    except HttpError as exc:
        raise YouTubeServiceError(f"YouTube API error during upload: {exc}") from exc
    except YouTubeServiceError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface any client/transport error
        raise YouTubeServiceError(f"Unexpected error during YouTube upload: {exc}") from exc
