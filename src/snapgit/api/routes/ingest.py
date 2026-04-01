from __future__ import annotations

from datetime import datetime, timezone
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from snapgit.api.db import build_engine
from snapgit.domain.models import Asset, Blob, PipelineRun
from snapgit.ingest.dedup import sha256_bytes
from snapgit.ingest.service import choose_captured_at
from snapgit.index.service import run_index_stage
from snapgit.ocr.service import run_ocr_stage

router = APIRouter()

DEFAULT_PIPELINE_VERSION = "baseline-v1"
DEFAULT_CONFIG_HASH = "0" * 64


class IngestRequest(BaseModel):
    source_type: str
    path: str | None = None
    capture_time: str | None = None
    embedded_timestamp: str | None = None
    embedded_metadata_json: str | None = None
    ocr_text: str | None = None
    title: str | None = None


@router.post("/ingest", status_code=201)
def ingest(payload: IngestRequest) -> dict[str, int | str | None]:
    engine = build_engine()
    path_value = payload.path

    blob_payload, source_file_mtime, storage_path = _load_blob_payload(path_value)
    mime_type = _guess_mime_type(path_value)
    digest = sha256_bytes(blob_payload)
    ingested_at = _utc_now()

    captured_at = _resolve_captured_at(
        payload_capture_time=payload.capture_time,
        embedded_timestamp=payload.embedded_timestamp,
        source_file_mtime=source_file_mtime,
        ingested_at=ingested_at,
    )

    pipeline_run_id: int | None = None
    asset_id: int | None = None
    blob_id: int | None = None

    try:
        with Session(engine) as session:
            blob = session.scalars(select(Blob).where(Blob.sha256 == digest)).first()
            if blob is None:
                blob = Blob(
                    sha256=digest,
                    storage_path=storage_path,
                    mime_type=mime_type,
                    size_bytes=len(blob_payload),
                )
                session.add(blob)
                session.flush()

            asset = Asset(
                blob_id=blob.id,
                source_type=payload.source_type,
                captured_at=captured_at,
                source_file_mtime=source_file_mtime,
                embedded_metadata_json=payload.embedded_metadata_json,
                state="processing",
            )
            session.add(asset)
            session.flush()

            pipeline_run = PipelineRun(
                asset_id=asset.id,
                trigger_type=_resolve_trigger_type(payload.source_type),
                pipeline_version=DEFAULT_PIPELINE_VERSION,
                config_hash=DEFAULT_CONFIG_HASH,
                status="running",
            )
            session.add(pipeline_run)
            session.commit()

            blob_id = blob.id
            asset_id = asset.id
            pipeline_run_id = pipeline_run.id

        ocr_text = payload.ocr_text or _default_ocr_text(path_value)
        run_ocr_stage(
            engine,
            pipeline_run_id=pipeline_run_id,
            asset_id=asset_id,
            ocr_text=ocr_text,
        )
        run_index_stage(
            engine,
            pipeline_run_id=pipeline_run_id,
            asset_id=asset_id,
            title=payload.title or _default_title(path_value),
        )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE pipeline_runs
                    SET status = 'completed', finished_at = CURRENT_TIMESTAMP
                    WHERE id = :pipeline_run_id
                    """
                ),
                {"pipeline_run_id": pipeline_run_id},
            )
    except Exception as exc:
        if pipeline_run_id is not None:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE pipeline_runs
                        SET status = 'failed', finished_at = CURRENT_TIMESTAMP
                        WHERE id = :pipeline_run_id
                        """
                    ),
                    {"pipeline_run_id": pipeline_run_id},
                )
                if asset_id is not None:
                    connection.execute(
                        text(
                            """
                            UPDATE assets
                            SET state = 'failed', updated_at = CURRENT_TIMESTAMP
                            WHERE id = :asset_id
                            """
                        ),
                        {"asset_id": asset_id},
                    )
        raise HTTPException(status_code=500, detail=f"ingest failed: {exc}") from exc

    return {
        "asset_id": asset_id,
        "blob_id": blob_id,
        "pipeline_run_id": pipeline_run_id,
        "source_type": payload.source_type,
        "path": payload.path,
        "status": "completed",
    }


def _resolve_trigger_type(source_type: str) -> str:
    return "backfill" if source_type == "filesystem_backfill" else "ingest"


def _load_blob_payload(path_value: str | None) -> tuple[bytes, datetime | None, str]:
    if path_value:
        path = Path(path_value)
        if path.exists() and path.is_file():
            stat = path.stat()
            source_file_mtime = datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).replace(tzinfo=None)
            return path.read_bytes(), source_file_mtime, str(path.resolve())

    fallback = (path_value or "").encode("utf-8")
    return fallback, None, path_value or ""


def _guess_mime_type(path_value: str | None) -> str:
    if path_value:
        guessed, _ = mimetypes.guess_type(path_value)
        if guessed:
            return guessed
    return "application/octet-stream"


def _resolve_captured_at(
    *,
    payload_capture_time: str | None,
    embedded_timestamp: str | None,
    source_file_mtime: datetime | None,
    ingested_at: datetime,
) -> datetime | None:
    source_file_mtime_str = _to_utc_iso(source_file_mtime) if source_file_mtime else None
    ingested_at_str = _to_utc_iso(ingested_at)
    captured_at_str = choose_captured_at(
        payload_capture_time=payload_capture_time,
        embedded_timestamp=embedded_timestamp,
        source_file_mtime=source_file_mtime_str,
        ingested_at=ingested_at_str,
    )
    return _parse_iso_datetime(captured_at_str)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _to_utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _default_ocr_text(path_value: str | None) -> str:
    if not path_value:
        return "screenshot"
    name = Path(path_value).name.replace("_", " ").replace("-", " ")
    return f"screenshot {name}"


def _default_title(path_value: str | None) -> str | None:
    if not path_value:
        return None
    return Path(path_value).stem.replace("_", " ").replace("-", " ")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
