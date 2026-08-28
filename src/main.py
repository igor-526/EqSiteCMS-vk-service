import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from api import vks_router
from containers import container
from core.exceptions import AppError
from settings import settings
from utils.configure_sentry import configure_sentry
from utils.database import close_database
from utils.observability import start_metrics_runtime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

configure_sentry()


@asynccontextmanager
async def lifespan(_: FastAPI):
    nats_client = container.nats_client()
    await nats_client.connect()
    await nats_client.setup()
    vk_consumer = container.vk_notification_consumer()
    await vk_consumer.start()

    metrics_runtime = start_metrics_runtime(environment=settings.environment)

    try:
        yield
    finally:
        await vk_consumer.stop()
        await nats_client.close()
        await close_database()
        if metrics_runtime is not None:
            metrics_runtime.close()


app = FastAPI(title=settings.app_title, debug=settings.debug, lifespan=lifespan)

Instrumentator().instrument(app)

app.include_router(vks_router)


@app.get("/health", tags=["Health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": exc.errors()})
