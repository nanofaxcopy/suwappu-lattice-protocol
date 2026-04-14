"""
JSON serialization helpers for gateway responses.

Extracted from rest_server.py, anchor_rest.py, and node_diagnostics.py
to share across FastAPI routers.
"""

from __future__ import annotations

import time as _time


def sth_to_dict(sth) -> dict:
    """Serialize SignedTreeHead to JSON-safe dict (binary -> hex)."""
    return {
        "sequence": sth.sequence,
        "tree_size": sth.tree_size,
        "timestamp": sth.timestamp,
        "root_hash": sth.root_hash.hex(),
        "operator_vk": sth.operator_vk.hex() if sth.operator_vk else "",
        "signature": sth.signature.hex() if sth.signature else "",
    }


def record_to_dict(record) -> dict:
    """Serialize CommitmentRecord to JSON-safe dict."""
    if hasattr(record, "to_dict"):
        d = record.to_dict()
        for key in ("signature", "shard_map_root", "content_hash"):
            val = d.get(key)
            if isinstance(val, bytes):
                d[key] = val.hex()
        return d
    return {
        "entity_id": record.entity_id,
        "sender_id": record.sender_id,
        "content_hash": record.content_hash,
        "shard_map_root": record.shard_map_root,
        "timestamp": record.timestamp,
        "shape": getattr(record, "shape", ""),
        "signature": record.signature.hex()
        if isinstance(record.signature, bytes)
        else str(record.signature),
        "predecessor": getattr(record, "predecessor", ""),
    }


def proof_to_dict(proof: dict) -> dict:
    """Serialize inclusion proof to JSON-safe dict."""
    result = {
        "entity_id": proof.get("entity_id", ""),
        "position": proof.get("position", -1),
    }
    root_hash = proof.get("root_hash", "")
    if isinstance(root_hash, bytes):
        result["root_hash"] = root_hash.hex()
    else:
        result["root_hash"] = str(root_hash)

    inc_proof = proof.get("inclusion_proof")
    if inc_proof:
        result["audit_path"] = [
            h.hex() if isinstance(h, bytes) else str(h)
            for h in getattr(inc_proof, "audit_path", [])
        ]
        result["leaf_index"] = getattr(inc_proof, "leaf_index", -1)
        result["tree_size"] = getattr(inc_proof, "tree_size", 0)
    return result


def error_response(code: int, message: str) -> dict:
    """Create an RFC 6962-style error response."""
    return {"error": message, "code": code}


def peer_to_dict(peer, public_mode: bool = False) -> dict:
    """Serialize PeerInfo to JSON-safe dict."""
    d = {
        "node_id": peer.node_id,
        "region": peer.region,
        "state": peer.state.name if hasattr(peer.state, "name") else str(peer.state),
        "last_seen": peer.last_seen,
    }
    if not public_mode:
        d["address"] = peer.address
    return d


def node_summary_to_dict(node_meta, public_mode: bool = False) -> dict:
    """Serialize commitment network node metadata to JSON-safe dict."""
    d = {
        "node_id": node_meta.get("node_id", ""),
        "region": node_meta.get("region", ""),
        "status": node_meta.get("status", ""),
        "shard_count": node_meta.get("shard_count", 0),
        "audit_passes": node_meta.get("audit_passes", 0),
        "strikes": node_meta.get("strikes", 0),
        "reputation_score": node_meta.get("reputation_score", 0.0),
    }
    if not public_mode:
        d["stake"] = node_meta.get("stake", 0)
        d["withheld_earnings"] = node_meta.get("withheld_earnings", 0.0)
        d["total_earnings"] = node_meta.get("total_earnings", 0.0)
    return d


def session_to_dict(session) -> dict:
    """Serialize TransferSession to JSON-safe dict."""
    now = _time.time()
    return {
        "entity_id": getattr(session, "entity_id", ""),
        "state": session.state.name if hasattr(session.state, "name") else str(session.state),
        "started_at": getattr(session, "started_at", 0.0),
        "phase_started_at": getattr(session, "phase_started_at", 0.0),
        "retry_count": getattr(session, "retry_count", 0),
        "error": getattr(session, "error", ""),
        "elapsed_seconds": round(now - getattr(session, "started_at", now), 3),
    }
