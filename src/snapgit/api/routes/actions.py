from fastapi import APIRouter

router = APIRouter()


@router.post("/actions/reindex")
def reindex_stub() -> dict[str, str]:
    return {"status": "queued"}

