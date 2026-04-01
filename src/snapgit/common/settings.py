from pydantic import BaseModel


class Settings(BaseModel):
    app_env: str = "dev"
    database_url: str = "sqlite:///data/db/snapgit.db"
    worker_concurrency: int = 1
