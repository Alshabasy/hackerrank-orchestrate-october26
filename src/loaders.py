from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .models import (
    AuditEntry,
    DeletionRequest,
    DeletionRule,
    EvidenceFile,
    SampleDeletionRequest,
    SampleOutputRow,
    SystemActivity,
    UserIdentity,
)


UTC = timezone.utc


def _parse_iso8601(raw: str, file_name: str, row_no: int, column: str) -> datetime:
    value = raw.strip()
    if not value:
        raise ValueError(f"{file_name}:{row_no}: {column} is empty")
    try:
        if value.endswith('Z'):
            return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(UTC)
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError as exc:
        raise ValueError(f"{file_name}:{row_no}: {column} has invalid timestamp {raw!r}") from exc


@dataclass(frozen=True)
class LoadedDataset:
    deletion_requests: List[DeletionRequest]
    deletion_rules: List[DeletionRule]
    evidence_files: List[EvidenceFile]
    system_activity: List[SystemActivity]
    user_audit_log: List[AuditEntry]
    user_identities: List[UserIdentity]
    sample_deletion_requests: List[SampleDeletionRequest]
    sample_output: List[SampleOutputRow]

    @property
    def deletion_request_by_case_id(self) -> Dict[str, DeletionRequest]:
        return {row.case_id: row for row in self.deletion_requests}

    @property
    def evidence_by_file_id(self) -> Dict[str, EvidenceFile]:
        return {row.file_id: row for row in self.evidence_files}

    @property
    def activity_by_id(self) -> Dict[str, SystemActivity]:
        return {row.activity_id: row for row in self.system_activity}

    @property
    def audit_by_id(self) -> Dict[str, AuditEntry]:
        return {row.audit_id: row for row in self.user_audit_log}

    @property
    def user_by_id(self) -> Dict[str, UserIdentity]:
        return {row.user_id: row for row in self.user_identities}


_ALLOWED_VALUES = {
    'deletion_status': {'COMPLETE', 'INCOMPLETE', 'NOT_APPLICABLE'},
    'reported_status': {'COMPLETE', 'PARTIALLY_COMPLETE', 'NOT_STARTED'},
    'required_action': {'DELETE', 'REDACT', 'ANONYMIZE', 'RETAIN'},
    'event_type': {
        'ANONYMIZE_RECORD', 'BLOCK_RESTORE', 'CHECK_DATA_FLOW', 'CHECK_RESTORE_ACCESS',
        'CLASSIFY_REQUEST', 'CREATE_EXPORT', 'DELETE_RECORD', 'DISABLE_DATA_FLOW',
        'INDEX_RECORD', 'LINK_RESOURCE_IDENTITY', 'REDACT_RECORD', 'REFRESH_CACHE',
        'RESTORE_BACKUP', 'RETAIN_RECORD', 'SEND_EMAIL', 'SYNC_RECORD', 'UPDATE_RECORD'
    },
    'reported_result': {'FAILED', 'PARTIAL', 'SUCCESS'},
    'field_name': {'email', 'email_hash', 'phone'},
    'phase': {'FINDING', 'REMEDIATION', 'VERIFICATION'},
    'operation_state': {
        'PRESENT', 'ACTIVE', 'DISABLED', 'BLOCKED', 'ABSENT', 'REDACTED', 'ANONYMIZED', 'PRESENT'
    },
}


def _validate_enum(value: str, allowed: set[str], file_name: str, row_no: int, column: str) -> str:
    if value not in allowed:
        raise ValueError(f"{file_name}:{row_no}: {column} has invalid value {value!r}; expected one of {sorted(allowed)}")
    return value


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with open(path, 'r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{path}: missing header row")
        rows = list(reader)
    return rows


def _assert_unique(values: Iterable[str], file_name: str, key_name: str) -> None:
    seen = set()
    duplicates = []
    for value in values:
        if value in seen:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        dup = sorted(set(duplicates))[:10]
        raise ValueError(f"{file_name}: duplicate {key_name} values detected: {dup}")


def _require_reference(reference: str, lookup: Dict[str, Any], file_name: str, row_no: int, column: str, parent_name: str) -> None:
    if reference not in lookup:
        raise ValueError(f"{file_name}:{row_no}: {column} references unknown {parent_name}: {reference!r}")


def load_deletion_requests(path: str | Path) -> List[DeletionRequest]:
    csv_path = Path(path)
    rows = _read_csv(csv_path)
    expected_headers = ['case_id', 'email', 'requested_at', 'reported_status', 'reported_at', 'audit_at', 'request_text']
    actual = list(rows[0].keys()) if rows else []
    if actual != expected_headers:
        raise ValueError(f"{csv_path}: unexpected headers {actual!r}; expected {expected_headers!r}")
    records: List[DeletionRequest] = []
    ids = []
    for idx, row in enumerate(rows, start=2):
        case_id = row['case_id']
        ids.append(case_id)
        records.append(
            DeletionRequest(
                case_id=case_id,
                email=row['email'],
                requested_at=_parse_iso8601(row['requested_at'], csv_path.name, idx, 'requested_at'),
                reported_status=_validate_enum(row['reported_status'], _ALLOWED_VALUES['reported_status'], csv_path.name, idx, 'reported_status'),
                reported_at=_parse_iso8601(row['reported_at'], csv_path.name, idx, 'reported_at'),
                audit_at=_parse_iso8601(row['audit_at'], csv_path.name, idx, 'audit_at'),
                request_text=row['request_text'],
            )
        )
    _assert_unique(ids, csv_path.name, 'case_id')
    return records


def load_deletion_rules(path: str | Path) -> List[DeletionRule]:
    csv_path = Path(path)
    rows = _read_csv(csv_path)
    expected_headers = ['rule_id', 'data_category', 'condition', 'required_action']
    actual = list(rows[0].keys()) if rows else []
    if actual != expected_headers:
        raise ValueError(f"{csv_path}: unexpected headers {actual!r}; expected {expected_headers!r}")
    records: List[DeletionRule] = []
    ids = []
    for idx, row in enumerate(rows, start=2):
        rule_id = row['rule_id']
        ids.append(rule_id)
        records.append(
            DeletionRule(
                rule_id=rule_id,
                data_category=row['data_category'],
                condition=row['condition'],
                required_action=_validate_enum(row['required_action'], _ALLOWED_VALUES['required_action'], csv_path.name, idx, 'required_action'),
            )
        )
    _assert_unique(ids, csv_path.name, 'rule_id')
    return records


def load_evidence_files(path: str | Path) -> List[EvidenceFile]:
    csv_path = Path(path)
    rows = _read_csv(csv_path)
    expected_headers = ['file_id', 'file_path', 'origin', 'captured_at']
    actual = list(rows[0].keys()) if rows else []
    if actual != expected_headers:
        raise ValueError(f"{csv_path}: unexpected headers {actual!r}; expected {expected_headers!r}")
    records: List[EvidenceFile] = []
    ids = []
    for idx, row in enumerate(rows, start=2):
        file_id = row['file_id']
        ids.append(file_id)
        records.append(
            EvidenceFile(
                file_id=file_id,
                file_path=row['file_path'],
                origin=row['origin'],
                captured_at=_parse_iso8601(row['captured_at'], csv_path.name, idx, 'captured_at'),
            )
        )
    _assert_unique(ids, csv_path.name, 'file_id')
    return records


def load_system_activity(path: str | Path) -> List[SystemActivity]:
    csv_path = Path(path)
    rows = _read_csv(csv_path)
    expected_headers = ['activity_id', 'event_time', 'event_type', 'source_system', 'target_system', 'resource_id', 'reported_result', 'details']
    actual = list(rows[0].keys()) if rows else []
    if actual != expected_headers:
        raise ValueError(f"{csv_path}: unexpected headers {actual!r}; expected {expected_headers!r}")
    records: List[SystemActivity] = []
    ids = []
    for idx, row in enumerate(rows, start=2):
        activity_id = row['activity_id']
        ids.append(activity_id)
        records.append(
            SystemActivity(
                activity_id=activity_id,
                event_time=_parse_iso8601(row['event_time'], csv_path.name, idx, 'event_time'),
                event_type=_validate_enum(row['event_type'], _ALLOWED_VALUES['event_type'], csv_path.name, idx, 'event_type'),
                source_system=row['source_system'],
                target_system=row['target_system'],
                resource_id=row['resource_id'],
                reported_result=_validate_enum(row['reported_result'], _ALLOWED_VALUES['reported_result'], csv_path.name, idx, 'reported_result'),
                details=row['details'],
            )
        )
    _assert_unique(ids, csv_path.name, 'activity_id')
    return records


def load_user_audit_log(path: str | Path) -> List[AuditEntry]:
    csv_path = Path(path)
    rows = _read_csv(csv_path)
    expected_headers = ['audit_id', 'user_id', 'event_time', 'field_name', 'old_value', 'new_value']
    actual = list(rows[0].keys()) if rows else []
    if actual != expected_headers:
        raise ValueError(f"{csv_path}: unexpected headers {actual!r}; expected {expected_headers!r}")
    records: List[AuditEntry] = []
    ids = []
    for idx, row in enumerate(rows, start=2):
        audit_id = row['audit_id']
        ids.append(audit_id)
        records.append(
            AuditEntry(
                audit_id=audit_id,
                user_id=row['user_id'],
                event_time=_parse_iso8601(row['event_time'], csv_path.name, idx, 'event_time'),
                field_name=_validate_enum(row['field_name'], _ALLOWED_VALUES['field_name'], csv_path.name, idx, 'field_name'),
                old_value=row['old_value'],
                new_value=row['new_value'],
            )
        )
    _assert_unique(ids, csv_path.name, 'audit_id')
    return records


def load_user_identities(path: str | Path) -> List[UserIdentity]:
    csv_path = Path(path)
    rows = _read_csv(csv_path)
    expected_headers = ['user_id', 'name', 'email', 'phone', 'email_hash', 'updated_at']
    actual = list(rows[0].keys()) if rows else []
    if actual != expected_headers:
        raise ValueError(f"{csv_path}: unexpected headers {actual!r}; expected {expected_headers!r}")
    records: List[UserIdentity] = []
    ids = []
    for idx, row in enumerate(rows, start=2):
        user_id = row['user_id']
        ids.append(user_id)
        records.append(
            UserIdentity(
                user_id=user_id,
                name=row['name'],
                email=row['email'],
                phone=row['phone'],
                email_hash=row['email_hash'],
                updated_at=_parse_iso8601(row['updated_at'], csv_path.name, idx, 'updated_at'),
            )
        )
    _assert_unique(ids, csv_path.name, 'user_id')
    return records


def load_sample_deletion_requests(path: str | Path) -> List[SampleDeletionRequest]:
    csv_path = Path(path)
    rows = _read_csv(csv_path)
    expected_headers = ['case_id', 'email', 'requested_at', 'reported_status', 'reported_at', 'audit_at', 'request_text']
    actual = list(rows[0].keys()) if rows else []
    if actual != expected_headers:
        raise ValueError(f"{csv_path}: unexpected headers {actual!r}; expected {expected_headers!r}")
    records: List[SampleDeletionRequest] = []
    ids = []
    for idx, row in enumerate(rows, start=2):
        case_id = row['case_id']
        ids.append(case_id)
        records.append(
            SampleDeletionRequest(
                case_id=case_id,
                email=row['email'],
                requested_at=_parse_iso8601(row['requested_at'], csv_path.name, idx, 'requested_at'),
                reported_status=_validate_enum(row['reported_status'], _ALLOWED_VALUES['reported_status'], csv_path.name, idx, 'reported_status'),
                reported_at=_parse_iso8601(row['reported_at'], csv_path.name, idx, 'reported_at'),
                audit_at=_parse_iso8601(row['audit_at'], csv_path.name, idx, 'audit_at'),
                request_text=row['request_text'],
            )
        )
    _assert_unique(ids, csv_path.name, 'case_id')
    return records


def load_sample_output(path: str | Path) -> List[SampleOutputRow]:
    csv_path = Path(path)
    rows = _read_csv(csv_path)
    expected_headers = ['case_id', 'deletion_status', 'steps_json']
    actual = list(rows[0].keys()) if rows else []
    if actual != expected_headers:
        raise ValueError(f"{csv_path}: unexpected headers {actual!r}; expected {expected_headers!r}")
    records: List[SampleOutputRow] = []
    ids = []
    for idx, row in enumerate(rows, start=2):
        case_id = row['case_id']
        ids.append(case_id)
        steps_json = row['steps_json']
        try:
            json.loads(steps_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{csv_path}:{idx}: steps_json is invalid JSON: {steps_json!r}") from exc
        records.append(
            SampleOutputRow(
                case_id=case_id,
                deletion_status=_validate_enum(row['deletion_status'], _ALLOWED_VALUES['deletion_status'], csv_path.name, idx, 'deletion_status'),
                steps_json=steps_json,
            )
        )
    _assert_unique(ids, csv_path.name, 'case_id')
    return records


def load_all_dataset(root: str | Path) -> LoadedDataset:
    base = Path(root)
    return LoadedDataset(
        deletion_requests=load_deletion_requests(base / 'deletion_requests.csv'),
        deletion_rules=load_deletion_rules(base / 'deletion_rules.csv'),
        evidence_files=load_evidence_files(base / 'evidence_files.csv'),
        system_activity=load_system_activity(base / 'system_activity.csv'),
        user_audit_log=load_user_audit_log(base / 'user_audit_log.csv'),
        user_identities=load_user_identities(base / 'user_identities.csv'),
        sample_deletion_requests=load_sample_deletion_requests(base / 'sample_deletion_requests.csv'),
        sample_output=load_sample_output(base / 'sample_output.csv'),
    )
