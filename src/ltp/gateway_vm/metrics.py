"""Gateway VM metrics — Prometheus-compatible counters and histograms."""

from __future__ import annotations

from ..observability.metrics import MetricsRegistry


def create_gateway_metrics(registry: MetricsRegistry) -> dict:
    """Register all gateway VM metrics and return a lookup dict.

    Follows the pattern from create_etp_metrics() in observability/metrics.py.
    """
    return {
        "etp_gateway_events_observed": registry.counter(
            "etp_gateway_events_observed",
            "Total bridge events observed on source chain",
        ),
        "etp_gateway_events_accepted": registry.counter(
            "etp_gateway_events_accepted",
            "Events that passed all validation checks",
        ),
        "etp_gateway_events_rejected": registry.counter(
            "etp_gateway_events_rejected",
            "Events that failed validation (labeled by reason)",
        ),
        "etp_gateway_anchor_latency": registry.histogram(
            "etp_gateway_anchor_latency",
            "Seconds from event observation to devnet anchor",
        ),
        "etp_gateway_finality_wait": registry.histogram(
            "etp_gateway_finality_wait",
            "Seconds spent waiting for source chain finality",
        ),
        "etp_gateway_replay_rejections": registry.counter(
            "etp_gateway_replay_rejections",
            "Replay attempts detected and rejected",
        ),
    }
