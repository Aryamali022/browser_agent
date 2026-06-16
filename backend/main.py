import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.rest import router as rest_router
from api.websockets import router as websocket_router
from core.config import settings
from core.database import engine, Base
import models.base  # noqa: F401  (register tables before create_all)

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.PROJECT_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # extension origins are dynamic; API is localhost-only
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(websocket_router)
app.include_router(rest_router)


@app.get("/")
def read_root():
    return {"message": "Browser Agent API is running"}
