"""Health check router — unauthenticated (K8s probes)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(request: Request) -> JSONResponse:
    """Liveness/readiness probe."""
    health_fn = request.app.state.health_fn
    if health_fn is None:
        return JSONResponse({"status": "ok"})
    try:
        data = health_fn()
    except Exception:
        return JSONResponse({"status": "error"}, status_code=503)
    return JSONResponse(data)
