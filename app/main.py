"""
FastAPI application entry point.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app.api.routes import auth, companies, search, tenders
from app.api.routes import dashboard, jobs, websocket
from app.config import settings
from app.database.mongodb import create_indexes, close_client
from app.jobs.scheduler import shutdown_scheduler, start_scheduler
from app.utils.logger import configure_logging
from app.utils.exceptions import AppError


app = FastAPI(title=settings.APP_NAME, debug=settings.DEBUG)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],  # lets the browser read the bid document filename
)

app.include_router(tenders.router, prefix="/api/v1")
app.include_router(companies.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")
app.include_router(dashboard.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(websocket.router)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    details = {}
    if getattr(exc, "field", None):
        details["field"] = exc.field
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "code": exc.code,
                "message": str(exc),
                "details": details,
            }
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    code = "PROCESSING_ERROR"
    if exc.status_code == 404:
        code = "NOT_FOUND"
    elif exc.status_code in (400, 422):
        code = "VALIDATION_ERROR"
    elif exc.status_code == 429:
        code = "RATE_LIMITED"
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": code,
                "message": str(exc.detail),
                "details": {},
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    field = None
    if exc.errors():
        loc = exc.errors()[0].get("loc", [])
        field = ".".join(str(item) for item in loc[1:]) if loc else None
    details = {"field": field} if field else {}
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Invalid request",
                "details": details,
            }
        },
    )


@app.on_event("startup")
async def on_startup() -> None:
    configure_logging(settings.LOG_LEVEL)
    await create_indexes()
    if settings.ENABLE_SCHEDULER:
        start_scheduler()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if settings.ENABLE_SCHEDULER:
        shutdown_scheduler()
    await close_client()


@app.get("/health")
async def health_check():
    return {"status": "ok"}
