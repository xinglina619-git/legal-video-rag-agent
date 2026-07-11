from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db import init_db
from app.routers import authorities, cases, materials, reports
from app.services.observability import configure_langsmith_environment


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_langsmith_environment(get_settings())
    init_db()
    yield


app = FastAPI(title="Legal Dispute Intake and Analysis Agent", version="0.3.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(materials.router)
app.include_router(cases.router)
app.include_router(authorities.router)
app.include_router(reports.router)
