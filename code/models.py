from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class DeletionRequest:
    case_id: str
    email: str
    requested_at: datetime
    reported_status: str
    reported_at: datetime
    audit_at: datetime
    request_text: str


@dataclass(frozen=True)
class DeletionRule:
    rule_id: str
    data_category: str
    condition: str
    required_action: str


@dataclass(frozen=True)
class EvidenceFile:
    file_id: str
    file_path: str
    origin: str
    captured_at: datetime


@dataclass(frozen=True)
class SystemActivity:
    activity_id: str
    event_time: datetime
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str
    details: str


@dataclass(frozen=True)
class AuditEntry:
    audit_id: str
    user_id: str
    event_time: datetime
    field_name: str
    old_value: str
    new_value: str


@dataclass(frozen=True)
class UserIdentity:
    user_id: str
    name: str
    email: str
    phone: str
    email_hash: str
    updated_at: datetime


@dataclass(frozen=True)
class SampleDeletionRequest:
    case_id: str
    email: str
    requested_at: datetime
    reported_status: str
    reported_at: datetime
    audit_at: datetime
    request_text: str


@dataclass(frozen=True)
class SampleOutputRow:
    case_id: str
    deletion_status: str
    steps_json: str


@dataclass(frozen=True)
class IdentityResult:
    status: str
    user_id: Optional[str]
    email_matches: Tuple[str, ...]
    phone_matches: Tuple[str, ...]
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class Observation:
    file_id: str
    location: str
    identifiers: Tuple[str, ...]
    raw_values: Dict[str, Any]
    evidence_type: str


@dataclass(frozen=True)
class DataFacts:
    case_id: str
    request_authorization: str
    identity: IdentityResult
    eligible_evidence_ids: Tuple[str, ...]
    unread_evidence: Tuple[str, ...]
    rules_without_match: Tuple[str, ...]
    observations: Tuple[Observation, ...]

