"""Gateway event query endpoints.

GET /gateway/events?status=X — List processed events by status
GET /gateway/events/{tx_hash} — Single event lookup by submission tx hash
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/gateway", tags=["gateway"])


def _serialize_record(rec: dict) -> dict:
    """Convert internal record to JSON-safe dict."""
    out = dict(rec)
    if isinstance(out.get("digest"), bytes):
        out["digest"] = out["digest"].hex()
    return out


@router.get("/events")
async def list_events(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by status"),
) -> JSONResponse:
    """List gateway-processed events, optionally filtered by status."""
    tracker = request.app.state.gateway_tracker

    if status:
        events = tracker.get_by_status(status)
    else:
        events = []
        for s in ("pending", "submitted", "confirmed", "finalized", "failed"):
            events.extend(tracker.get_by_status(s))

    return JSONResponse({
        "events": [_serialize_record(e) for e in events],
        "count": len(events),
    })


@router.get("/events/{tx_hash}")
async def lookup_event(request: Request, tx_hash: str) -> JSONResponse:
    """Look up a single event by its submission transaction hash."""
    tracker = request.app.state.gateway_tracker
    rec = tracker.lookup_by_tx_hash(tx_hash)
    if rec is None:
        return JSONResponse(
            {"error": f"no event found for tx_hash {tx_hash}"},
            status_code=404,
        )
    return JSONResponse(_serialize_record(rec))
