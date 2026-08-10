from enum import StrEnum


class FlowStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class RunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class NodeRunStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    RETRY_WAIT = "RETRY_WAIT"
    POLL_WAIT = "POLL_WAIT"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


NODE_TERMINAL_STATUSES = {
    NodeRunStatus.SUCCESS,
    NodeRunStatus.FAILED,
    NodeRunStatus.SKIPPED,
    NodeRunStatus.CANCELLED,
}


class RunTriggerType(StrEnum):
    MANUAL = "MANUAL"
    SCHEDULE = "SCHEDULE"
    RERUN = "RERUN"
