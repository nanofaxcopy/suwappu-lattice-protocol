"""DAG-BFT consensus engine (Specs D1a, D1b).

Design inspired by Mysten Labs' Mysticeti (arXiv:2310.14821, Babel et al.) —
an uncertified-DAG BFT protocol. This is our own implementation, not a port
of Mysten's code; class names avoid reusing their protocol's exact naming
(e.g. "Mysticeti-C") to prevent implying shared lineage or endorsement.
"""

from .adapter import DagBftAdapter
from .backend import ConsensusBackend, LocalConsensusBackend
from .bls_certificates import (
    DOMAIN_CONSENSUS_ACK,
    BLSCertificateManager,
    SignedCertificate,
)
from .commit_rule import (
    collect_causal_history,
    evaluate_direct_commit,
    evaluate_indirect_commit,
)
from .committee_sync import CommitteeSync
from .dag_store import DAGStore
from .engine import LocalDagBftEngine, to_ordered_batch
from .events import ConsensusEvent, ConsensusEventType
from .faults import FaultConfig, FaultType, PartitionConfig
from .message_bus import MessageBus
from .protocol import DagBftProtocol
from .types import (
    Block,
    Certificate,
    CommitDecision,
    EquivocationProof,
    RoundState,
)
from .validator_set import ValidatorInfo, ValidatorSet

__all__ = [
    # D1a: DAG data structures
    "Block",
    "Certificate",
    "CommitDecision",
    "EquivocationProof",
    "RoundState",
    # D1a: Storage
    "DAGStore",
    # D1a: Protocol
    "DagBftProtocol",
    # D1a: Commit rule
    "evaluate_direct_commit",
    "evaluate_indirect_commit",
    "collect_causal_history",
    # D1a: Engine
    "LocalDagBftEngine",
    "to_ordered_batch",
    # D1a: Fault injection
    "FaultType",
    "FaultConfig",
    "PartitionConfig",
    # D1a: Message bus
    "MessageBus",
    # D1b: Events
    "ConsensusEvent",
    "ConsensusEventType",
    # D1b: Validator Set
    "ValidatorInfo",
    "ValidatorSet",
    # D1b: BLS Certificates
    "DOMAIN_CONSENSUS_ACK",
    "BLSCertificateManager",
    "SignedCertificate",
    # D1b: Backend
    "ConsensusBackend",
    "LocalConsensusBackend",
    # D1b: Committee Sync
    "CommitteeSync",
    # D1b: Adapter
    "DagBftAdapter",
]
