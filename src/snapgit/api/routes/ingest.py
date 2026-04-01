from pydantic import BaseModel
from fastapi import APIRouter

router = APIRouter()


class IngestRequest(BaseModel):
    source_type: str


@router.post("/ingest", status_code=201)
def ingest_stub(payload: IngestRequest) -> dict[str, int | str]:
    return {"asset_id": 1, "source_type": payload.source_type}

