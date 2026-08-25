"""
Inference marketplace — the full economic loop, live, in one process.

Boots a real gateway (uvicorn + FastAPI) with the whole stack wired:
stablecoin ledger, prepaid billing, Merkle-committed receipts with
ML-DSA-65 signed tree heads, bridge-deposit crediting, and epoch
settlement paying the serving provider. Then walks the money:

  1. A bridge deposit lands and credits the customer's balance.
  2. The customer buys completions over HTTP (OpenAI-shaped API).
  3. Every bill is committed; the audit proof verifies end to end.
  4. Epoch settlement pays the GPU provider its operator share.
  5. The ledger proves solvency — every micro accounted for.

Usage:
    PYTHONPATH=. python examples/inference_marketplace.py

Note: uses the pre-stability inference surface (ltp.inference_service),
which is not yet re-exported from ltp.__init__.
"""

import json
import urllib.request

from src.ltp.bridge_deposits import DepositEvent
from src.ltp.inference_service import InferenceServiceConfig, build_inference_service
from src.ltp.merkle_log.proof import InclusionProof
from src.ltp.merkle_log.sth import SignedTreeHead


def http_json(url, payload=None, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers or {})
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def main():
    service = build_inference_service(
        InferenceServiceConfig(host="127.0.0.1", port=0, node_id="gpu-node-1")
    )
    service.start()
    base = service.url
    customer = {"x-suwappu-customer": "cust-alice"}
    print(f"gateway up at {base}\n")

    # 1. Bridge deposit -> customer balance.
    service.deposits.bind_address("0x" + "ab" * 20, "cust-alice")
    credited = service.deposits.process(
        [
            DepositEvent(
                tx_hash="0x" + "11" * 32,
                sender="0x" + "ab" * 20,
                amount_micro=5_000_000,  # $5 over the bridge
                confirmations=12,
            )
        ]
    )
    print(
        f"[deposit]  {credited[0].tx_hash[:10]}… credited "
        f"${credited[0].amount_micro / 1e6:.2f} to {credited[0].customer_id}"
    )
    balance = http_json(f"{base}/inference/v1/balance", headers=customer)
    print(f"[balance]  ${balance['balance_micro'] / 1e6:.2f} prepaid\n")

    # 2. Buy completions over the OpenAI-shaped API.
    for prompt in ("hello suwappu", "what secures my bill?"):
        completion = http_json(
            f"{base}/inference/v1/chat/completions",
            payload={"model": "suwappu-1", "messages": [{"role": "user", "content": prompt}]},
            headers=customer,
        )
        billing = completion["billing"]
        print(f"[serve]    {completion['choices'][0]['message']['content']!r}")
        print(
            f"[bill]     {billing['settled_micro']} micro, "
            f"leaf #{billing['commitment']['leaf_index']}, "
            f"balance ${billing['balance_after_micro'] / 1e6:.6f}"
        )

    # 3. Verify the last bill without trusting the gateway.
    bundle = http_json(f"{base}/inference/v1/receipts/{billing['request_id']}", headers=customer)
    sth = SignedTreeHead(
        sequence=bundle["sth"]["sequence"],
        tree_size=bundle["sth"]["tree_size"],
        timestamp=bundle["sth"]["timestamp"],
        root_hash=bytes.fromhex(bundle["sth"]["root_hash"]),
        operator_vk=bytes.fromhex(bundle["sth"]["operator_vk"]),
        signature=bytes.fromhex(bundle["sth"]["signature"]),
    )
    proof = InclusionProof(
        leaf_index=bundle["leaf_index"],
        tree_size=bundle["tree_size"],
        audit_path=[bytes.fromhex(node) for node in bundle["audit_path"]],
        root_hash=bytes.fromhex(bundle["root_hash"]),
    )
    assert sth.verify(), "STH signature must verify"
    assert proof.verify(bytes.fromhex(bundle["record"]), sth.root_hash)
    print(
        f"\n[audit]    ML-DSA-65 STH signature: VALID "
        f"(sequence {sth.sequence}, tree size {sth.tree_size})"
    )
    print("[audit]    Merkle inclusion proof:   VALID")

    # 4. Epoch settlement pays the provider.
    snapshot = service.settle_epoch()
    payout = snapshot.payouts.get("gpu-node-1", 0)
    print(
        f"\n[payout]   gpu-node-1 paid {payout} micro "
        f"(operator share of revenue), fully funded: {snapshot.fully_funded}"
    )

    # 5. Solvency: every micro accounted for.
    stats = http_json(f"{base}/inference/v1/stats", headers=customer)
    print(
        f"[stats]    {stats['settled_requests']} requests, "
        f"{stats['revenue_micro']} micro revenue, "
        f"{stats['tokens_served']} tokens served"
    )
    print(
        f"[solvency] ledger holds {service.ledger.total_held_micro} micro, "
        f"invariant: {'OK' if service.ledger.check_solvency() else 'BROKEN'}"
    )

    service.stop()


if __name__ == "__main__":
    main()
