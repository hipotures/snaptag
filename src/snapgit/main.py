from fastapi import FastAPI

from snapgit.api.routes.actions import router as actions_router
from snapgit.api.routes.ingest import router as ingest_router
from snapgit.api.routes.screenshots import router as screenshots_router
from snapgit.api.routes.search import router as search_router

app = FastAPI()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(ingest_router)
app.include_router(search_router)
app.include_router(screenshots_router)
app.include_router(actions_router)
