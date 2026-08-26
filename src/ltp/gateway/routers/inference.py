"""Inference router — metered, stablecoin-billed model serving.

OpenAI-style surface (``/inference/v1/chat/completions``) so existing
client SDKs point at it with a base-URL change. The gateway runs the
configured model backend, meters the usage the backend reports, settles
the request against ``app.state.inference_market``
(``ltp.inference.InferenceMarket``), and returns the completion with a
billing block attached. Requests are JWT-protected under the gateway's
standard middleware (the paths are not in the unauthenticated list) —
the JWT subject is the paying customer, and billing is **prepaid**: the
metered quote is debited from the customer's ledger balance, with 402
both before serving (balance under the serve floor) and after metering
(balance under the amount due — the completion is withheld).

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

from ...inference import (
    InferenceError,
    InferenceReceipt,
    InsufficientBalance,
    receipt_digest,
)
from ..serializers import error_response


def _customer_id(request: Request) -> str:
    """Resolve the paying customer for this request.

    JWT subject when the gateway runs with auth enabled (the production
    posture); the ``x-suwappu-customer`` header as the dev/test
    fallback; ``anonymous`` otherwise.
    """
    claims = getattr(request.state, "jwt_claims", None)
    if claims is not None and getattr(claims, "sub", None):
        return claims.sub
    header = request.headers.get("x-suwappu-customer", "").strip()
    return header or "anonymous"


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

    # Resolve the request's model name against the operator-registered
    # listing and use the *listing's* id downstream. Two reasons, and
    # both matter: the request body is attacker-controlled, so echoing
    # it into a log line forges log entries (CodeQL py/log-injection),
    # and echoing it into an error body reflects unbounded input back to
    # the caller. Past this point `pricing.model_id` is an operator-set
    # value, so neither hazard survives.
    try:
        pricing = market.pricing_for(model_id)
    except InferenceError:
        return JSONResponse(error_response(404, "model not listed"), 404)
    model_id = pricing.model_id

    # Prepaid floor: don't burn model compute for a customer whose
    # balance couldn't plausibly cover the response. Output length is
    # unknown until the model runs; the floor bounds the exposure.
    customer_id = _customer_id(request)
    balance = market.ledger.customer_balance(customer_id)
    if balance < market.min_balance_to_serve_micro:
        return JSONResponse(
            {
                "error": "insufficient prepaid balance",
                "code": 402,
                "customer_id": customer_id,
                "balance_micro": balance,
                "minimum_micro": market.min_balance_to_serve_micro,
            },
            status_code=402,
        )

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

    # Commit the receipt to the Merkle log BEFORE settlement: with the
    # market's verifier wired to the log, an uncommitted receipt cannot
    # settle, so every bill has an inclusion proof from birth.
    receipt_log = getattr(request.app.state, "inference_receipt_log", None)
    commitment = None
    if receipt_log is not None:
        try:
            leaf_index = receipt_log.commit(receipt)
        except InferenceError as exc:
            logger.error("receipt commitment failed: %s", exc)
            return JSONResponse(error_response(500, "receipt commitment failed"), 500)
        sth = receipt_log.latest_sth  # published by commit()
        commitment = {
            "leaf_index": leaf_index,
            "sth_sequence": sth.sequence,
            "root_hash": sth.root_hash.hex(),
        }

    try:
        settlement = market.settle_prepaid(receipt, customer_id)
    except InsufficientBalance as exc:
        # The response ran longer than the remaining balance covers. The
        # completion is withheld — the customer pays for what they get,
        # and gets what they pay for. The serve floor bounds how often
        # this can happen.
        return JSONResponse(
            {
                "error": "insufficient prepaid balance for metered usage",
                "code": 402,
                "customer_id": customer_id,
                "balance_micro": exc.balance_micro,
                "due_micro": exc.due_micro,
            },
            status_code=402,
        )
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
                "customer_id": customer_id,
                "balance_after_micro": settlement.customer_balance_after_micro,
                "request_digest": receipt.request_digest,
                "response_digest": receipt.response_digest,
                "request_id": receipt.request_id,
                "commitment": commitment,
            },
        }
    )


@router.get("/v1/balance")
async def customer_balance(request: Request) -> JSONResponse:
    """The calling customer's prepaid balance and the serve floor.

    Deposits are credited ledger-side (bridged stablecoins in
    production); this endpoint is the read.
    """
    market = request.app.state.inference_market
    if market is None:
        return JSONResponse(error_response(503, "inference market not available"), 503)
    customer_id = _customer_id(request)
    return JSONResponse(
        {
            "customer_id": customer_id,
            "balance_micro": market.ledger.customer_balance(customer_id),
            "minimum_to_serve_micro": market.min_balance_to_serve_micro,
        }
    )


@router.get("/v1/receipts/{request_id}")
async def receipt_proof(request: Request, request_id: str) -> JSONResponse:
    """Audit bundle for one bill: record, inclusion proof, signed STH.

    A customer verifies their bill without trusting the gateway: check
    the ML-DSA STH signature, recompute the record's leaf hash up the
    audit path to the STH root, then recompute the request/response
    SHA3-256 digests inside the record against the bodies they hold.
    """
    receipt_log = getattr(request.app.state, "inference_receipt_log", None)
    if receipt_log is None:
        return JSONResponse(error_response(503, "receipt commitment log not available"), 503)
    try:
        bundle = receipt_log.proof(request_id)
    except InferenceError:
        return JSONResponse(
            error_response(404, f"no committed receipt for request: {request_id}"), 404
        )
    return JSONResponse(bundle)


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
