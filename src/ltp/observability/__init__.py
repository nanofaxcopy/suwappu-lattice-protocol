"""
Observability infrastructure for ETP.

Provides Prometheus-compatible metrics collection, structured JSON logging,
and alert rule definitions.
"""

from .metrics import MetricsRegistry, Counter, Gauge, Histogram, MetricType
from .logging import StructuredLogger, CorrelationContext, JSONFormatter
from .endpoint import MetricsRequestHandler, ETPObservability
from .alerts import AlertSeverity, AlertCondition, AlertRule, AlertResult, AlertEvaluator
from .tls import TLSConfig, CertificateManager, InMemoryCertManager, NetworkPolicy, NetworkPolicyRegistry, ETPSecurityConfig

__all__ = [
    "MetricsRegistry",
    "Counter",
    "Gauge",
    "Histogram",
    "MetricType",
    "StructuredLogger",
    "CorrelationContext",
    "JSONFormatter",
    "MetricsRequestHandler",
    "ETPObservability",
    "AlertSeverity",
    "AlertCondition",
    "AlertRule",
    "AlertResult",
    "AlertEvaluator",
    "TLSConfig",
    "CertificateManager",
    "InMemoryCertManager",
    "NetworkPolicy",
    "NetworkPolicyRegistry",
    "ETPSecurityConfig",
]
