from fastapi import APIRouter

router = APIRouter()


@router.get("/screenshots")
def list_screenshots_stub() -> dict[str, list[dict[str, str]]]:
    return {"items": []}

