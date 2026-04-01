from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from snapgit.api.db import build_engine
from snapgit.domain.models import Asset

router = APIRouter()


@router.get("/screenshots")
def list_screenshots(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, int | list[dict[str, str | int | None]]]:
    engine = build_engine()
    with Session(engine) as session:
        assets = session.scalars(
            select(Asset)
            .order_by(desc(Asset.created_at), desc(Asset.id))
            .limit(limit)
            .offset(offset)
        ).all()

    items = [
        {
            "asset_id": asset.id,
            "source_type": asset.source_type,
            "state": asset.state,
            "captured_at": _to_iso_or_none(asset.captured_at),
            "ingested_at": _to_iso_or_none(asset.ingested_at),
        }
        for asset in assets
    ]
    return {"limit": limit, "offset": offset, "count": len(items), "items": items}


def _to_iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
