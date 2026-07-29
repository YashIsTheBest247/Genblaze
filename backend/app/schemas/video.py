"""
Video Generation Request/Response Models
"""
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class VideoGenerationRequest(BaseModel):
    """Request model for video generation"""
    topic: str = Field(..., description="The topic of the video", min_length=1, max_length=200)
    duration: int = Field(default=60, description="Duration in seconds", ge=10, le=300)
    key_points: List[str] = Field(default=[], description="Key points to cover in the video")
    style: str = Field(default="educational", description="Video style")
    publish_to_youtube: bool = Field(
        default=False,
        description="When true, the finished video is auto-published to YouTube."
    )
    privacy_status: Optional[Literal["unlisted", "public", "private"]] = Field(
        default=None,
        description=(
            "YouTube visibility for the published video. When omitted, falls back "
            "to YOUTUBE_PRIVACY_STATUS. It must default to None rather than a "
            "literal: the trending scheduler builds this request without a "
            "privacy_status, so a hardcoded default here silently overrode the "
            "configured setting and published every scheduled video unlisted."
        )
    )

    class Config:
        json_schema_extra = {
            "example": {
                "topic": "Introduction to Machine Learning",
                "duration": 60,
                "key_points": [
                    "What is machine learning",
                    "Types of machine learning",
                    "Real-world applications"
                ],
                "style": "educational",
                "publish_to_youtube": False
            }
        }


class VideoGenerationResponse(BaseModel):
    """Response model for video generation"""
    success: bool
    message: str
    video_path: Optional[str] = None
    video_filename: Optional[str] = None
    youtube_url: Optional[str] = None
    error: Optional[str] = None
    
    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "message": "Video generated successfully",
                "video_path": "/static/videos/introduction_to_machine_learning_1234567890.mp4",
                "video_filename": "introduction_to_machine_learning_1234567890.mp4"
            }
        }


class VideoListResponse(BaseModel):
    """
    One entry in the media library.

    `path` is a presigned Backblaze B2 URL when the video lives in the bucket
    (the normal case) and a local /static path only for videos rendered before
    B2 was configured.
    """
    name: str
    path: str
    thumbnail: Optional[str] = None
    # Storage + provenance detail, surfaced as badges in the library UI.
    storage: Literal["b2", "local"] = "local"
    topic: Optional[str] = None
    created_at: Optional[float] = None
    duration_sec: Optional[int] = None
    verified: bool = False
    manifest_hash: Optional[str] = None
    run_id: Optional[str] = None
    has_provenance: bool = False

    class Config:
        json_schema_extra = {
            "example": {
                "name": "RBI_repo_rate_decision_1234567890.mp4",
                "path": "https://s3.us-west-004.backblazeb2.com/flux-media/...",
                "thumbnail": "https://s3.us-west-004.backblazeb2.com/flux-media/...",
                "storage": "b2",
                "topic": "RBI holds repo rate at 6.5%",
                "verified": True,
                "manifest_hash": "94e3b2e0da986e1a...",
                "has_provenance": True,
            }
        }


class ProvenanceResponse(BaseModel):
    """Genblaze provenance record for one video."""
    name: str
    available: bool
    verified: bool = False
    canonical_hash: Optional[str] = None
    run_id: Optional[str] = None
    tenant_id: Optional[str] = None
    created_at: Optional[str] = None
    stages: List[dict] = Field(default_factory=list)
    assets: List[dict] = Field(default_factory=list)
    manifest: Optional[dict] = None
    verification: Optional[dict] = None
    manifest_url: Optional[str] = None
