from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from snapgit.api.db import build_engine
from snapgit.domain.models import Asset

from snapgit.search.service import search_asset_ids

router = APIRouter()


@router.get("/search")
def search(q: str = Query(min_length=1)) -> dict[str, str | int | list[dict[str, str | int | None]]]:
    engine = build_engine()
    asset_ids = search_asset_ids(engine, q)
    if not asset_ids:
        return {"query": q, "total": 0, "results": []}

    with Session(engine) as session:
        assets = session.scalars(select(Asset).where(Asset.id.in_(asset_ids))).all()

    by_id = {asset.id: asset for asset in assets}
    ordered_results = []
    for asset_id in asset_ids:
        asset = by_id.get(asset_id)
        if asset is None:
            continue
        ordered_results.append(
            {
                "asset_id": asset.id,
                "source_type": asset.source_type,
                "state": asset.state,
                "captured_at": _to_iso_or_none(asset.captured_at),
                "ingested_at": _to_iso_or_none(asset.ingested_at),
            }
        )

    return {"query": q, "total": len(ordered_results), "results": ordered_results}


def _to_iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
