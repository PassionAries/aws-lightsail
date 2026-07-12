import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.routers import auth, catalog, credentials, instances, metrics, users
from app.seed import seed_admin
from app.services.collector import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        seed_admin(db)
    finally:
        db.close()
    start_scheduler()
    logger.info("Lightsail Manager API 已启动")
    yield
    stop_scheduler()
    logger.info("Lightsail Manager API 已停止")


app = FastAPI(title="Lightsail Manager", version="1.0.0", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(credentials.router, prefix="/api")
app.include_router(catalog.router, prefix="/api")
app.include_router(instances.router, prefix="/api")
app.include_router(metrics.router, prefix="/api")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
