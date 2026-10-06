"""Điểm khởi động ứng dụng FastAPI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from src.api import ws
from src.api.routes import router
from src.config import Settings, get_settings
from src.core.errors import SmartHomeError
from src.db.seed import run_seed
from src.iot.factory import get_bus
from src.observability.langsmith import configure_langsmith, tracing_enabled
from src.services.agent_runner import close_agent
from src.services.automation import engine

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def log_llm_runtime_config(runtime_settings: Settings) -> None:
    """Log model identity at startup without exposing credentials."""
    logger.info(
        "LLM runtime configured: provider=%s model=%s enabled=%s",
        runtime_settings.llm_provider,
        runtime_settings.model_name,
        runtime_settings.llm_available,
    )


def log_langsmith_runtime_config(runtime_settings: Settings) -> None:
    """Log tracing state without ever exposing the LangSmith credential."""
    logger.info(
        "LangSmith tracing: enabled=%s project=%s hide_inputs=%s hide_outputs=%s",
        tracing_enabled(runtime_settings),
        runtime_settings.langsmith_project,
        runtime_settings.langsmith_hide_inputs,
        runtime_settings.langsmith_hide_outputs,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Khởi động %s (%s)", settings.app_name, settings.app_env)
    configure_langsmith(settings)
    log_llm_runtime_config(settings)
    log_langsmith_runtime_config(settings)

    run_seed()

    bus = get_bus()
    await bus.start()
    # Nối IoT bus và automation engine vào WebSocket để đẩy realtime
    bus.subscribe(ws.on_device_state)
    engine.subscribe(ws.on_suggestion)
    engine.subscribe_sensors(ws.on_sensor_state)
    await engine.start()

    yield

    await engine.stop()
    await bus.stop()
    await close_agent()
    logger.info("Đã dừng")


app = FastAPI(
    title="Smart Home AI Agent",
    description=(
        "Trợ lý nhà thông minh hiểu tiếng Việt: lập kế hoạch đa bước, điều khiển thiết bị, "
        "xác nhận lệnh nhạy cảm (HITL), phát hiện xung đột và học thói quen."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(SmartHomeError)
async def smart_home_error_handler(_request: Request, exc: SmartHomeError) -> JSONResponse:
    """Quy lỗi nghiệp vụ về HTTP status tương ứng thay vì trả 500."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


class NoCacheStaticFiles(StaticFiles):
    """StaticFiles nhưng luôn yêu cầu trình duyệt kiểm tra lại (no-cache).

    frontend là file tĩnh được sửa liên tục khi phát triển; nếu để trình duyệt
    cache mạnh, người dùng reload vẫn thấy giao diện cũ. ``no-cache`` buộc
    revalidate mỗi lần nên vừa luôn mới, vừa không phải tải lại nếu chưa đổi.
    """

    async def get_response(self, path: str, scope: Scope):  # noqa: ANN201
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.include_router(router, prefix="/api/v1")
app.include_router(ws.router)


@app.get("/health", tags=["system"])
async def health() -> dict:
    return {"status": "ok", "env": settings.app_env}


# Frontend chính (3D dashboard)
app.mount("/", NoCacheStaticFiles(directory="frontend", html=True), name="frontend")
