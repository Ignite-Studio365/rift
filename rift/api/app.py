from contextlib import asynccontextmanager
from pathlib import Path
import torch
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import logging

from rift.core.config import settings
from rift.core.database import ping, close
from rift.core.exceptions import AppError
from rift.api.routes import auth, videos, jobs, billing, admin

log = logging.getLogger("rift.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=getattr(logging, settings.ENV == "production" and "INFO" or "DEBUG"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    log.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION} [{settings.ENV}]")
    log.info(f"GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU only'}")

    if not await ping():
        log.error("Database connection failed on startup!")
    else:
        log.info("Database connected")
        from rift.core.database import engine
        from rift.models import User, Upload, Job, Payment, APIKey
        from rift.core.database import Base
        async with engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("Database tables ready")

    if settings.SENTRY_DSN:
        import sentry_sdk
        sentry_sdk.init(dsn=settings.SENTRY_DSN, traces_sample_rate=0.1,
                        environment=settings.ENV, release=settings.APP_VERSION)

    yield

    log.info("Shutting down...")
    await close()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url="/api/docs" if settings.ENV != "production" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.ENV != "production" else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        import uuid, time
        rid = str(uuid.uuid4())
        request.state.request_id = rid
        t = time.perf_counter()
        response = await call_next(request)
        ms = round((time.perf_counter() - t) * 1000, 2)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Response-Time"] = f"{ms}ms"
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(Exception)
    async def generic_handler(request: Request, exc: Exception):
        log.error(f"Unhandled: {exc}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "INTERNAL_ERROR",
                                                        "message": "An unexpected error occurred"})

    PREFIX = "/api/v1"
    app.include_router(auth.router, prefix=PREFIX)
    app.include_router(videos.router, prefix=PREFIX)
    app.include_router(jobs.router, prefix=PREFIX)
    app.include_router(billing.router, prefix=PREFIX)
    app.include_router(admin.router, prefix=PREFIX)

    @app.get("/api/health")
    async def health():
        from datetime import datetime, timezone
        db_ok = await ping()
        redis_ok = False
        try:
            import redis
            r = redis.from_url(settings.REDIS_URL)
            r.ping()
            redis_ok = True
        except Exception:
            pass
        return {
            "status": "healthy" if db_ok else "degraded",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
            "database": db_ok, "redis": redis_ok,
            "version": settings.APP_VERSION,
        }

    @app.get("/api/v1/system")
    async def system_info():
        import psutil, cv2, sys
        gpu = {"available": False}
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            gpu = {"available": True, "name": props.name,
                   "memory_gb": round(props.total_memory / 1e9, 2),
                   "allocated_mb": round(torch.cuda.memory_allocated() / 1e6, 1),
                   "cuda": torch.version.cuda}
        ram = psutil.virtual_memory()
        return {
            "gpu": gpu,
            "cpu": {"cores": psutil.cpu_count(), "percent": psutil.cpu_percent(0.1)},
            "ram": {"total_gb": round(ram.total / 1e9, 2), "percent": ram.percent},
            "torch": torch.__version__, "cv2": cv2.__version__, "python": sys.version.split()[0],
        }

    static_dir = Path(__file__).parent.parent / "web" / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = create_app()