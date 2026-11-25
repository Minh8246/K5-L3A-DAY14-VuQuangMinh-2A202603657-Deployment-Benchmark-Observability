"""
Production AI Agent — Part 6 Final Project

    uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2

Endpoints
  GET  /health  liveness  — luôn 200 khi process còn sống (không phụ thuộc Redis)
  GET  /ready   readiness — 200 khi Redis kết nối được, 503 khi mất Redis hoặc đang tắt
  POST /ask     X-API-Key → rate limit (429) → cost guard (402) → LLM → ghi chi phí
"""
import json
import logging
import signal
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging — mỗi dòng log là 1 JSON object
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            entry["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("agent")
    log.handlers = [handler]
    log.setLevel(settings.log_level)
    log.propagate = False  # không để root logger in lại dưới dạng text
    return log


logger = _setup_logging()


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"extra_fields": fields})


# ─────────────────────────────────────────────────────────
# Graceful shutdown
# ─────────────────────────────────────────────────────────
_state = {"shutting_down": False, "in_flight": 0}
_lock = threading.Lock()


def _install_sigterm_hook() -> None:
    """
    Đánh dấu "đang tắt" để /ready trả 503 (load balancer ngừng gửi request mới),
    rồi GỌI LẠI handler cũ của uvicorn — uvicorn mới là bên ngừng nhận kết nối,
    chờ request đang chạy xong rồi thoát với exit code 0.
    """
    previous = signal.getsignal(signal.SIGTERM)

    def _on_sigterm(signum, frame):
        _state["shutting_down"] = True
        log_event("sigterm_received", in_flight=_state["in_flight"])
        if callable(previous):
            previous(signum, frame)

    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError):  # không phải main thread / nền tảng không hỗ trợ
        pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    _install_sigterm_hook()
    log_event("startup", environment=settings.environment, port=settings.port,
              redis_ok=redis_healthy(), rate_limit_per_minute=settings.rate_limit_per_minute)
    yield
    # Tới đây uvicorn đã ngừng nhận kết nối và chờ xong các request đang chạy.
    _state["shutting_down"] = True
    log_event("shutdown", in_flight=_state["in_flight"])
    try:
        r.close()
    except Exception:  # noqa: BLE001 — đang tắt, lỗi đóng kết nối không quan trọng
        pass


app = FastAPI(title="Production AI Agent", version="1.0.0", lifespan=lifespan,
              docs_url=None if settings.environment == "production" else "/docs",
              redoc_url=None)


# ─────────────────────────────────────────────────────────
# Middleware: request id + log mỗi request + đếm request đang chạy
# ─────────────────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    start = time.perf_counter()
    with _lock:
        _state["in_flight"] += 1
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        with _lock:
            _state["in_flight"] -= 1
        log_event("request", request_id=request_id, method=request.method, path=request.url.path,
                  status=status_code, ms=round((time.perf_counter() - start) * 1000, 1))


# ─────────────────────────────────────────────────────────
# Xử lý lỗi: mã lỗi rõ ràng, không lộ stack trace ra client
# ─────────────────────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(p) for p in e["loc"][1:]), "msg": e["msg"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "Invalid request body", "errors": errors})


@app.exception_handler(redis.RedisError)
async def redis_error(request: Request, exc: redis.RedisError):
    log_event("redis_unavailable", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Service temporarily unavailable"},
                        headers={"Retry-After": "5"})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    logger.error("unhandled_exception", exc_info=exc,
                 extra={"extra_fields": {"path": request.url.path}})
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=128)


@app.get("/")
def root():
    return {"service": "production-ai-agent", "endpoints": ["/health", "/ready", "POST /ask"]}


@app.get("/health")
def health():
    """Liveness: process còn sống là 200 — không kiểm tra Redis để platform không restart oan."""
    return {"status": "ok", "environment": settings.environment}


@app.get("/ready")
def ready():
    """Readiness: chỉ nhận traffic khi Redis kết nối được và chưa nhận SIGTERM."""
    if _state["shutting_down"]:
        return JSONResponse(status_code=503, content={"status": "shutting_down"})
    if not redis_healthy():
        return JSONResponse(status_code=503, content={"status": "not_ready", "redis": "unreachable"})
    return {"status": "ready", "redis": "ok"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    check_rate_limit(api_key)  # 429
    check_budget()             # 402

    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="question must not be blank")

    answer = llm_ask(question)
    cost = record_cost(estimate_tokens(question), estimate_tokens(answer))
    log_event("ask", user_id=body.user_id, q_len=len(question), a_len=len(answer),
              cost_usd=round(cost, 8))
    return {"question": question, "answer": answer, "cost_usd": round(cost, 8)}
