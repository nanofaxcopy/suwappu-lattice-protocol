"""Inference router — metered, stablecoin-billed model serving.

OpenAI-style surface (``/inference/v1/chat/completions``) so existing
client SDKs point at it with a base-URL change. The gateway runs the
configured model backend, meters the usage the backend reports, settles
the request against ``app.state.inference_market``
(``ltp.inference.InferenceMarket``), and returns the completion with a
billing block attached. Requests are JWT-protected under the gateway's
standard middleware (the paths are not in the unauthenticated list) —
the JWT subject is the paying customer.

The backend is deployment-injected at ``app.state.inference_backend``:
a callable ``(model_id, messages) -> (text, input_tokens,
output_tokens)``. It is the network's own model runtime — this router
never talks to a third-party inference API.
"""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...inference import InferenceError, InferenceReceipt, receipt_digest
from ..serializers import error_response


def _canonical_request_bytes(model_id: str, messages: list) -> bytes:
    """Deterministic bytes for the request digest: sorted-key compact JSON."""
    return json.dumps(
        {"model": model_id, "messages": messages},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/inference", tags=["inference"])


@router.get("/v1/models")
async def list_models(request: Request) -> JSONResponse:
    """Current model listings with per-million-token stablecoin prices."""
    market = request.app.state.inference_market
    if market is None:
        return JSONResponse(error_response(503, "inference market not available"), 503)
    return JSONResponse(
        {
            "models": [
                {
                    "id": pricing.model_id,
                    "input_micro_per_mtok": pricing.input_micro_per_mtok,
                    "output_micro_per_mtok": pricing.output_micro_per_mtok,
                }
                for pricing in market.models()
            ]
        }
    )


@router.post("/v1/chat/completions")
async def chat_completions(request: Request) -> JSONResponse:
    """Run one completion, meter it, and settle it in stablecoin.

    Request body (OpenAI-shaped subset): ``{"model": str, "messages":
    [{"role": str, "content": str}, ...]}``. The response carries the
    completion plus a ``billing`` block: metered tokens, the settled
    micro amount, and the request/response digests a customer can audit
    against a later LTP commitment.
    """
    market = request.app.state.inference_market
    backend = request.app.state.inference_backend
    if market is None or backend is None:
        return JSONResponse(error_response(503, "inference serving not available"), 503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(error_response(400, "invalid JSON body"), 400)

    model_id = body.get("model")
    messages = body.get("messages")
    if not isinstance(model_id, str) or not model_id:
        return JSONResponse(error_response(400, "missing model"), 400)
    if not isinstance(messages, list) or not messages:
        return JSONResponse(error_response(400, "missing messages"), 400)

    try:
        market.pricing_for(model_id)
    except InferenceError:
        return JSONResponse(error_response(404, f"model not listed: {model_id}"), 404)

    node_id = getattr(request.app.state, "inference_node_id", None) or "gateway"
    request_bytes = _canonical_request_bytes(model_id, messages)

    try:
        text, input_tokens, output_tokens = backend(model_id, messages)
    except Exception:
        logger.exception("inference backend failed for model %s", model_id)
        return JSONResponse(error_response(502, "model backend failed"), 502)

    receipt = InferenceReceipt(
        request_id=uuid.uuid4().hex,
        node_id=node_id,
        model_id=model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        request_digest=receipt_digest(request_bytes),
        response_digest=receipt_digest(text.encode("utf-8")),
    )
    due = market.quote(model_id, input_tokens, output_tokens)
    try:
        settlement = market.settle(receipt, due)
    except InferenceError as exc:
        # The completion ran but billing was refused — surface it as a
        # server-side billing fault, never as a silent free request.
        logger.error("inference settlement refused: %s", exc)
        return JSONResponse(error_response(500, "billing settlement failed"), 500)

    return JSONResponse(
        {
            "id": f"chatcmpl-{receipt.request_id}",
            "object": "chat.completion",
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
            "billing": {
                "settled_micro": settlement.revenue_micro,
                "provider_claim_micro": settlement.provider_claim_micro,
                "request_digest": receipt.request_digest,
                "response_digest": receipt.response_digest,
                "request_id": receipt.request_id,
            },
        }
    )


@router.get("/v1/stats")
async def inference_stats(request: Request) -> JSONResponse:
    """Aggregate serving stats: settled requests, revenue, tokens."""
    market = request.app.state.inference_market
    if market is None:
        return JSONResponse(error_response(503, "inference market not available"), 503)
    return JSONResponse(
        {
            "settled_requests": market.settled_count,
            "revenue_micro": market.revenue_micro(),
            "tokens_served": market.tokens_served(),
            "per_model": [
                {
                    "id": pricing.model_id,
                    "revenue_micro": market.revenue_micro(pricing.model_id),
                    "tokens_served": market.tokens_served(pricing.model_id),
                }
                for pricing in market.models()
            ],
        }
    )
