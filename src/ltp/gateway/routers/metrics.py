"""Prometheus metrics endpoint — unauthenticated (scraper access)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def prometheus_metrics(request: Request) -> Response:
    """Serve Prometheus text exposition format."""
    obs = request.app.state.observability
    if obs is not None:
        _, content_type, body = obs.metrics_handler.handle_metrics_request()
    else:
        # Fallback: empty metrics
        content_type = "text/plain; version=0.0.4; charset=utf-8"
        body = "# No metrics available\n"

    return Response(content=body, media_type=content_type)
