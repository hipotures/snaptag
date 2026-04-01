from fastapi import APIRouter

router = APIRouter()


@router.get("/search")
def search_stub(q: str) -> dict[str, str | list[dict[str, str]]]:
    return {"query": q, "results": []}

