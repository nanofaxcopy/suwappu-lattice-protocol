"""
Cloud infrastructure abstractions for ETP.

Provides pluggable interfaces for:
  - KMS (Key Management Service) — key lifecycle in cloud HSMs
  - ScheduledTaskRunner — periodic task execution (CronJob, EventBridge)

Each interface has an in-memory implementation for testing and development.
Production implementations delegate to AWS KMS, GCP Cloud KMS, or
Kubernetes CronJobs.
"""

from .kms import KMSBackend, InMemoryKMSBackend
from .aws_kms import AWSKMSBackend
from .scheduler import ScheduledTaskRunner, InMemoryScheduler
from .queue import MessageQueue, InMemoryQueue
from .orchestrator import WorkflowOrchestrator, InMemoryOrchestrator, WorkflowStep, WorkflowResult
from .backup import BackupManager, InMemoryBackupManager, BackupMetadata, BackupSchedule, ETPBackupStrategy

__all__ = [
    "KMSBackend",
    "InMemoryKMSBackend",
    "AWSKMSBackend",
    "ScheduledTaskRunner",
    "InMemoryScheduler",
    "MessageQueue",
    "InMemoryQueue",
    "WorkflowOrchestrator",
    "InMemoryOrchestrator",
    "WorkflowStep",
    "WorkflowResult",
    "BackupManager",
    "InMemoryBackupManager",
    "BackupMetadata",
    "BackupSchedule",
    "ETPBackupStrategy",
]
