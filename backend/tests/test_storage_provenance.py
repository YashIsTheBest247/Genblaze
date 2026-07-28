"""
End-to-end test of the Backblaze B2 storage path and the Genblaze provenance
record, run against a mocked S3 server.

B2 is S3-compatible and Flux talks to it through genblaze-s3's boto3 client, so
`moto` exercises the real code path — upload, list, presign, read back, delete —
without needing live credentials.

Run:  venv/Scripts/python -m pytest tests/ -v
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BUCKET = "flux-test-bucket"


@pytest.fixture
def s3_backend(monkeypatch):
    """A genblaze-s3 backend wired to an in-memory S3 server."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

    from moto import mock_aws

    with mock_aws():
        import boto3

        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)

        from genblaze_s3 import S3StorageBackend

        yield S3StorageBackend(
            bucket=BUCKET,
            region="us-east-1",
            access_key_id="testing",
            secret_access_key="testing",
        )


@pytest.fixture
def storage(s3_backend, monkeypatch):
    """The app's storage service, backed by the mocked bucket."""
    from app.core.config import settings
    from app.services.storage_service import B2StorageService

    monkeypatch.setattr(settings, "B2_KEY_ID", "testing")
    monkeypatch.setattr(settings, "B2_APP_KEY", "testing")
    monkeypatch.setattr(settings, "B2_BUCKET", BUCKET)
    monkeypatch.setattr(settings, "B2_PREFIX", "flux")

    service = B2StorageService()
    service._backend = s3_backend
    return service


def _make_real_mp4(path: Path) -> None:
    """
    A genuine (tiny) MP4, not a byte blob.

    Manifest embedding writes into the container's box structure, so a fake
    payload would pass the upload tests while quietly invalidating the
    provenance tests.
    """
    import subprocess

    import imageio_ffmpeg

    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi",
         "-i", "color=c=navy:s=128x224:d=1", "-pix_fmt", "yuv420p",
         "-c:v", "libx264", "-preset", "ultrafast", str(path)],
        capture_output=True, timeout=120, check=True,
    )


@pytest.fixture
def render_artefacts(tmp_path):
    """Stand-ins for the files a real render produces."""
    video = tmp_path / "Test_Topic_1700000000.mp4"
    _make_real_mp4(video)
    thumb = tmp_path / "Test_Topic_1700000000.jpg"
    thumb.write_bytes(b"\xff\xd8\xff\xe0fake jpeg" * 16)
    srt = tmp_path / "Test_Topic.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nHello world\n", encoding="utf-8")
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"topic": "Test Topic", "audio_script": []}), encoding="utf-8")
    return video, thumb, srt, script


# --- storage ---------------------------------------------------------


def test_upload_render_puts_every_artefact(storage, render_artefacts):
    video, thumb, srt, script = render_artefacts
    stem = video.stem

    keys = storage.upload_render(
        stem=stem,
        video_path=video,
        thumbnail_path=thumb,
        srt_path=srt,
        script_path=script,
        manifest={"canonical_hash": "abc123", "run": {"run_id": "r-1"}},
        meta={"topic": "Test Topic", "duration_sec": 60, "verified": True},
    )

    assert keys["video"] == f"flux/library/{stem}/video.mp4"
    for artefact in ("video", "thumbnail", "srt", "script", "manifest", "meta"):
        assert keys[artefact] is not None, f"{artefact} was not uploaded"
        assert storage.backend.exists(keys[artefact])


def test_library_listing_is_newest_first_and_carries_metadata(storage, render_artefacts):
    video, thumb, _, _ = render_artefacts

    for index, topic in enumerate(["Older story", "Newer story"]):
        storage.upload_render(
            stem=f"story_{index}",
            video_path=video,
            thumbnail_path=thumb,
            meta={"topic": topic, "created_at": 1700000000 + index * 100, "verified": True},
        )

    items = storage.list_library()
    assert [i.stem for i in items] == ["story_1", "story_0"]  # newest first
    assert items[0].meta["topic"] == "Newer story"
    assert items[0].thumb_key is not None
    assert items[0].name == "story_1.mp4"


def test_partial_upload_is_not_listed(storage, render_artefacts):
    """A prefix without video.mp4 is a failed render and must stay hidden."""
    video, _, _, _ = render_artefacts
    storage.upload_render(stem="good", video_path=video)
    storage.put_json({"topic": "orphan"}, storage.key_for("broken", "meta.json"))

    assert [i.stem for i in storage.list_library()] == ["good"]


def test_presigned_url_is_scoped_and_time_limited(storage, render_artefacts):
    video, _, _, _ = render_artefacts
    keys = storage.upload_render(stem="presign_me", video_path=video)

    url = storage.presign(keys["video"], expires_in=900)
    assert url and url.startswith("http")
    assert "presign_me" in url
    # Signed and time-limited. The exact query parameters differ between the
    # signature versions boto3 negotiates (SigV4 uses X-Amz-*), so assert on
    # what every version guarantees rather than pinning one.
    assert "Signature" in url
    assert "Expires" in url


def test_get_item_and_delete_removes_all_artefacts(storage, render_artefacts):
    video, thumb, srt, script = render_artefacts
    stem = "delete_me"
    storage.upload_render(
        stem=stem, video_path=video, thumbnail_path=thumb,
        srt_path=srt, script_path=script,
        manifest={"canonical_hash": "x"}, meta={"topic": "Bye"},
    )

    assert storage.get_item(stem) is not None
    assert storage.delete_video(stem) is True
    assert storage.get_item(stem) is None
    assert storage.list_library() == []


def test_unconfigured_storage_degrades_instead_of_raising(monkeypatch):
    from app.core.config import settings
    from app.services.storage_service import B2StorageService

    monkeypatch.setattr(settings, "B2_KEY_ID", "")
    monkeypatch.setattr(settings, "B2_APP_KEY", "")
    monkeypatch.setattr(settings, "B2_BUCKET", "")

    service = B2StorageService()
    assert service.available is False
    assert service.list_library() == []
    assert service.presign("flux/library/x/video.mp4") is None
    assert service.upload_render(stem="x", video_path=Path("nope.mp4"))["video"] is None


# --- provenance ------------------------------------------------------


@pytest.fixture
def artefacts_outside_temp():
    """
    Artefacts where a real render actually puts them — under the app tree, NOT
    under the system temp directory.

    This matters: Genblaze refuses file:// reads from outside temp, and pytest's
    `tmp_path` lives *inside* temp, so a tmp_path-based fixture passes while
    production fails. This fixture reproduces the real layout.
    """
    root = Path(__file__).resolve().parent.parent / "static" / "videos"
    root.mkdir(parents=True, exist_ok=True)
    video = root / "_pytest_outside_temp.mp4"
    thumb = root / "_pytest_outside_temp.jpg"
    _make_real_mp4(video)
    thumb.write_bytes(b"\xff\xd8\xff\xe0fake jpeg" * 16)
    try:
        yield video, thumb
    finally:
        video.unlink(missing_ok=True)
        thumb.unlink(missing_ok=True)


def test_ingest_works_for_artefacts_outside_the_temp_dir(artefacts_outside_temp):
    """
    Regression: a real render's files live under static/videos, which Genblaze's
    file:// allowlist rejects. record_render must stage them into temp itself.
    """
    from app.services.genblaze_service import genblaze

    video, thumb = artefacts_outside_temp
    manifest = genblaze.record_render(
        topic="Outside temp",
        artefacts=[(video, "final_video", "video/mp4"),
                   (thumb, "thumbnail", "image/jpeg")],
        stages=[{"stage": "assembly", "provider": "moviepy+ffmpeg", "model": None}],
    )

    assert manifest is not None, "ingest rejected artefacts outside the temp dir"
    assert manifest["_verified"] is True

    # The staged copies must hash to the real files, not to anything else.
    from app.services.genblaze_service import sha256_of

    by_role = {
        a["metadata"]["role"]: a
        for step in manifest["run"]["steps"] for a in step["assets"]
    }
    assert by_role["final_video"]["sha256"] == sha256_of(video)
    assert by_role["thumbnail"]["sha256"] == sha256_of(thumb)


def test_staging_directory_is_cleaned_up(artefacts_outside_temp):
    """The temp staging copies must not survive the call."""
    import tempfile

    from app.services.genblaze_service import genblaze

    video, _ = artefacts_outside_temp
    temp_root = Path(tempfile.gettempdir())
    before = set(temp_root.glob("flux-provenance-*"))

    genblaze.record_render(
        topic="Cleanup check",
        artefacts=[(video, "final_video", "video/mp4")],
        stages=[],
    )

    assert set(temp_root.glob("flux-provenance-*")) == before


def test_manifest_records_the_generation_chain_and_verifies(render_artefacts):
    from app.services.genblaze_service import genblaze

    video, thumb, srt, script = render_artefacts
    stages = [
        {"stage": "script", "provider": "gemini", "model": "gemini-2.5-flash"},
        {"stage": "visuals", "provider": "genblaze", "model": "imagen-3.0-fast-generate-001"},
        {"stage": "narration", "provider": "kokoro", "model": "kokoro-82m"},
    ]

    manifest = genblaze.record_render(
        topic="Test Topic",
        artefacts=[
            (video, "final_video", "video/mp4"),
            (thumb, "thumbnail", "image/jpeg"),
            (srt, "captions", "application/x-subrip"),
            (script, "script", "application/json"),
        ],
        stages=stages,
        extra={"requested_duration_sec": 60},
    )

    assert manifest is not None
    assert manifest["_verified"] is True
    assert len(manifest["canonical_hash"]) == 64

    steps = manifest["run"]["steps"]
    assets = [a for step in steps for a in step["assets"]]
    assert {a["metadata"]["role"] for a in assets} == {
        "final_video", "thumbnail", "captions", "script"
    }
    # Every artefact must carry a real content hash — that is what makes the
    # manifest verifiable rather than merely descriptive.
    assert all(len(a["sha256"]) == 64 for a in assets)

    recorded_stages = steps[0]["metadata"]["stages"]
    assert [s["stage"] for s in recorded_stages] == ["script", "visuals", "narration"]

    report = genblaze.verify_manifest(manifest)
    assert report["verified"] is True
    assert report["hash_valid"] is True
    assert report["assets_missing_sha256"] == []


def test_tampering_with_the_manifest_breaks_verification(render_artefacts):
    from app.services.genblaze_service import genblaze

    video, _, _, _ = render_artefacts
    manifest = genblaze.record_render(
        topic="Test Topic",
        artefacts=[(video, "final_video", "video/mp4")],
        stages=[{"stage": "script", "provider": "gemini", "model": "gemini-2.5-flash"}],
    )
    assert manifest["_verified"] is True

    # Rewrite the recorded model without recomputing the canonical hash.
    tampered = json.loads(json.dumps(manifest))
    tampered["run"]["steps"][0]["metadata"]["stages"][0]["model"] = "some-other-model"

    assert genblaze.verify_manifest(tampered)["verified"] is False


def test_manifest_round_trips_through_the_mp4_container(render_artefacts, tmp_path):
    """The file carries its own provenance once it leaves the bucket."""
    from app.services.genblaze_service import genblaze

    video, _, _, _ = render_artefacts
    manifest = genblaze.record_render(
        topic="Test Topic",
        artefacts=[(video, "final_video", "video/mp4")],
        stages=[{"stage": "assembly", "provider": "moviepy+ffmpeg", "model": None}],
    )

    assert genblaze.embed_manifest(video, manifest) is True

    extracted = genblaze.extract_manifest(video)
    assert extracted is not None
    assert extracted["canonical_hash"] == manifest["canonical_hash"]


def test_provenance_survives_a_full_upload_and_read_back(storage, render_artefacts):
    """The manifest the UI renders is the one that came back out of B2."""
    from app.services.genblaze_service import genblaze

    video, thumb, _, _ = render_artefacts
    manifest = genblaze.record_render(
        topic="Test Topic",
        artefacts=[(video, "final_video", "video/mp4")],
        stages=[{"stage": "script", "provider": "gemini", "model": "gemini-2.5-flash"}],
    )

    stem = "round_trip"
    storage.upload_render(
        stem=stem, video_path=video, thumbnail_path=thumb,
        manifest=manifest,
        meta={"topic": "Test Topic", "verified": manifest["_verified"]},
    )

    fetched = storage.read_json(storage.key_for(stem, "manifest.json"))
    assert fetched["canonical_hash"] == manifest["canonical_hash"]
    assert genblaze.verify_manifest(fetched)["verified"] is True
