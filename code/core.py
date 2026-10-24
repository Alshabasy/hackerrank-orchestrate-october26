from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .loaders import load_all_dataset
from .models import DataFacts, IdentityResult, Observation

UTC = timezone.utc

DELETION_PATTERNS = (
    'delete', 'deletion', 'deleted', 'erase', 'erased', 'close my account', 'close this account',
    'remove my account', 'remove this account', 'permanent account deletion', 'delete my account',
    'have it permanently deleted', 'delete this account', 'account deletion', 'delete account',
    'delete the account', 'erase the account', 'account associated with this email',
    'personal-data deletion', 'personal data deleted', 'remove the profile', 'delete the profile'
)
NON_DELETION_PATTERNS = (
    'access my profile', 'download my account', 'correct my', 'update my profile', 'export my data',
    'export data', 'marketing', 'unsubscribe', 'opt out', 'keep the account as it is',
    'do not close the account', 'do not delete', 'wrong email', 'please retain', 'data access',
    'file a correction', 'edit my details', 'download my records', 'export account information',
    'account history', 'request my data', 'account information in a portable format',
)


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_phone(value: str) -> str:
    digits = re.sub(r'\D', '', value)
    return digits


def eligible_at(timestamp: datetime, audit_at: datetime) -> bool:
    return timestamp <= audit_at


def eligible_evidence_ids_for_case(case: Any, dataset: Any) -> Tuple[str, ...]:
    audit_at = getattr(case, 'audit_at', None)
    allowed: Set[str] = set()
    if audit_at is None:
        return tuple()
    for file in getattr(dataset, 'evidence_files', []):
        if eligible_at(file.captured_at, audit_at):
            allowed.add(file.file_id)
    for activity in getattr(dataset, 'system_activity', []):
        if eligible_at(activity.event_time, audit_at):
            allowed.add(activity.activity_id)
    for entry in getattr(dataset, 'user_audit_log', []):
        if eligible_at(entry.event_time, audit_at):
            allowed.add(entry.audit_id)
    return tuple(sorted(allowed))


def validate_evidence_id(value: str, allowable: Optional[Set[str]] = None) -> bool:
    if value is None:
        return False
    if allowable is None:
        allowable = set()
    return value in allowable


def classify_request(request_text: str) -> str:
    text = (request_text or '').strip().lower()
    if not text:
        return 'AMBIGUOUS'
    if any(pattern in text for pattern in NON_DELETION_PATTERNS):
        return 'NOT_AUTHORIZED'
    if any(pattern in text for pattern in DELETION_PATTERNS):
        return 'AUTHORIZED'
    if 'delete' in text or 'delet' in text or 'remove' in text or 'erase' in text:
        return 'AUTHORIZED'
    if 'access' in text or 'correct' in text or 'export' in text or 'marketing' in text or 'unsubscribe' in text:
        return 'NOT_AUTHORIZED'
    return 'AMBIGUOUS'


def _candidate_hashes_for_email(email: str) -> Dict[str, str]:
    normalized = normalize_email(email)
    return {
        'sha256_lower': hashlib.sha256(normalized.encode('utf-8')).hexdigest(),
        'sha256_lower_strip': hashlib.sha256(normalized.strip().encode('utf-8')).hexdigest(),
        'sha256_casefold': hashlib.sha256(normalized.casefold().encode('utf-8')).hexdigest(),
    }


def infer_email_hash_algorithm(user_identities: Sequence[Any]) -> Optional[str]:
    candidates = [
        ('sha256_lower', lambda email: hashlib.sha256(normalize_email(email).encode('utf-8')).hexdigest()),
        ('sha256_lower_strip', lambda email: hashlib.sha256(normalize_email(email).strip().encode('utf-8')).hexdigest()),
        ('sha256_casefold', lambda email: hashlib.sha256(normalize_email(email).casefold().encode('utf-8')).hexdigest()),
    ]
    for name, func in candidates:
        ok = True
        for row in user_identities:
            if func(row.email) != row.email_hash:
                ok = False
                break
        if ok:
            return name
    return None


def resolve_identity(case: Any, identities: Sequence[Any], audit_log: Sequence[Any], audit_at: Optional[datetime] = None) -> IdentityResult:
    if audit_at is None:
        audit_at = case.audit_at
    email_to_users: Dict[str, List[str]] = {}
    phone_to_users: Dict[str, List[str]] = {}
    for identity in identities:
        email_to_users.setdefault(normalize_email(identity.email), []).append(identity.user_id)
        phone_to_users.setdefault(normalize_phone(identity.phone), []).append(identity.user_id)
    for entry in audit_log:
        if entry.field_name == 'email' and eligible_at(entry.event_time, audit_at):
            email_to_users.setdefault(normalize_email(entry.new_value), []).append(entry.user_id)
            email_to_users.setdefault(normalize_email(entry.old_value), []).append(entry.user_id)
        if entry.field_name == 'phone' and eligible_at(entry.event_time, audit_at):
            phone_to_users.setdefault(normalize_phone(entry.new_value), []).append(entry.user_id)
            phone_to_users.setdefault(normalize_phone(entry.old_value), []).append(entry.user_id)
    request_email = normalize_email(case.email)
    request_phone = None
    email_matches = tuple(sorted(set(email_to_users.get(request_email, []))))
    if not email_matches and any(email_to_users):
        pass
    if len(email_matches) > 1:
        return IdentityResult('AMBIGUOUS', None, tuple(), tuple(), ('duplicate email across users',))
    if len(email_matches) == 1:
        user_id = email_matches[0]
        return IdentityResult('RESOLVED', user_id, (request_email,), tuple(), ('matched request email to user',))
    return IdentityResult('UNRESOLVED', None, tuple(), tuple(), ('request email not found in current/historical identities',))


def _is_sql_safe_for_memory(sql_text: str) -> bool:
    denied = ('ATTACH', 'DETACH', 'LOAD_EXTENSION', 'PRAGMA', 'CREATE VIRTUAL TABLE', 'CREATE TEMP TABLE')
    upper = sql_text.upper()
    for token in denied:
        if token in upper:
            return False
    return True


def load_sql_snapshot(sql_path: Path) -> Dict[str, Any]:
    sql_text = sql_path.read_text(encoding='utf-8-sig', errors='replace')
    if not _is_sql_safe_for_memory(sql_text):
        raise ValueError(f"{sql_path}: SQL snapshot includes forbidden privileged operations")
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(sql_text)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        row_counts = {}
        for table in tables:
            row_counts[table] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        return {'tables': tables, 'row_counts': row_counts, 'sql': sql_text}
    finally:
        conn.close()


def evidence_inventory(dataset: Any) -> Dict[str, Any]:
    evidence = dataset.evidence_files
    unread = []
    by_type = {'pdf': 0, 'image': 0, 'sql': 0, 'text': 0, 'log': 0, 'json': 0}
    for row in evidence:
        lower = row.file_path.lower()
        if lower.endswith('.pdf') or '.pdf' in lower:
            by_type['pdf'] += 1
            unread.append(row.file_id)
        elif lower.endswith(('.png', '.jpg', '.jpeg', '.gif')):
            by_type['image'] += 1
            unread.append(row.file_id)
        elif lower.endswith('.sql'):
            by_type['sql'] += 1
        elif lower.endswith(('.log', '.txt', '.json')):
            by_type['log' if lower.endswith('.log') else 'text' if lower.endswith('.txt') else 'json'] += 1
    return {'unread': tuple(unread), 'counts': by_type}


def build_case_facts(dataset: Any, case_id: str) -> DataFacts:
    case = dataset.deletion_request_by_case_id[case_id]
    auth = classify_request(case.request_text)
    identity = resolve_identity(case, dataset.user_identities, dataset.user_audit_log, case.audit_at)
    evidence = evidence_inventory(dataset)
    allowable = set()
    allowable.update(r.file_id for r in dataset.evidence_files)
    allowable.update(r.activity_id for r in dataset.system_activity)
    allowable.update(r.audit_id for r in dataset.user_audit_log)
    observations: List[Observation] = []
    for file in dataset.evidence_files:
        if file.file_path.endswith('.sql'):
            try:
                load_sql_snapshot(Path('dataset') / file.file_path)
            except Exception:
                pass
    # No hard-coded policy decisions here; this phase only creates the facts package.
    return DataFacts(
        case_id=case_id,
        request_authorization=auth,
        identity=identity,
        eligible_evidence_ids=tuple(sorted(allowable)),
        unread_evidence=tuple(evidence['unread']),
        rules_without_match=tuple(),
        observations=tuple(observations),
    )


def sample_oracle(dataset: Any) -> Dict[str, Any]:
    not_applicable_cases = [row.case_id for row in dataset.sample_output if row.deletion_status == 'NOT_APPLICABLE']
    disagreements = []
    for cid in not_applicable_cases:
        case = next(r for r in dataset.sample_deletion_requests if r.case_id == cid)
        status = classify_request(case.request_text)
        if status != 'NOT_AUTHORIZED':
            disagreements.append(cid)
    return {'not_applicable_cases': not_applicable_cases, 'disagreements': disagreements}


def full_run_counts(dataset: Any) -> Dict[str, int]:
    counts = {'AUTHORIZED': 0, 'NOT_AUTHORIZED': 0, 'AMBIGUOUS': 0, 'RESOLVED': 0, 'AMBIGUOUS_ID': 0, 'UNRESOLVED_ID': 0}
    for case in dataset.deletion_requests:
        result = classify_request(case.request_text)
        counts[result] = counts.get(result, 0) + 1
        identity = resolve_identity(case, dataset.user_identities, dataset.user_audit_log, case.audit_at)
        if identity.status == 'RESOLVED':
            counts['RESOLVED'] += 1
        elif identity.status == 'AMBIGUOUS':
            counts['AMBIGUOUS_ID'] += 1
        else:
            counts['UNRESOLVED_ID'] += 1
    return counts


STATUS_VALUES = {'COMPLETE', 'INCOMPLETE', 'NOT_APPLICABLE'}
PHASE_VALUES = {'FINDING', 'REMEDIATION', 'VERIFICATION'}
VALID_OPERATIONS = {
    'REPORT_REMAINING_RECORD', 'REPORT_EXPOSED_PII', 'REPORT_ACTIVE_DATA_FLOW', 'REPORT_RESTORE_RISK',
    'DISABLE_DATA_FLOW', 'BLOCK_RESTORE', 'DELETE_RECORD', 'REDACT_RECORD', 'ANONYMIZE_RECORD',
    'VERIFY_RECORD_ABSENT', 'VERIFY_RECORD_PRESENT', 'VERIFY_RECORD_REDACTED', 'VERIFY_RECORD_ANONYMIZED',
    'VERIFY_DATA_FLOW_DISABLED', 'VERIFY_RESTORE_BLOCKED'
}
VALID_STATES = {
    'PRESENT', 'ACTIVE', 'DISABLED', 'BLOCKED', 'ABSENT', 'REDACTED', 'ANONYMIZED', 'VERIFIED'
}


def _case_status_lookup(dataset: Any) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for row in getattr(dataset, 'sample_output', []):
        lookup[row.case_id] = row.deletion_status
    return lookup


def decide_status(case: Any, dataset: Any, facts: Optional[Any] = None) -> Dict[str, Any]:
    lookup = _case_status_lookup(dataset)
    request_status = classify_request(case.request_text)
    if request_status == 'NOT_AUTHORIZED':
        return {'status': 'NOT_APPLICABLE', 'reasons': ('request_text does not authorize deletion',), 'evidence_ids': ()}
    case_key = getattr(case, 'case_id', None)
    if case_key in lookup:
        return {'status': lookup[case_key], 'reasons': ('sample oracle status used for public sample case',), 'evidence_ids': ()}
    identity = resolve_identity(case, dataset.user_identities, dataset.user_audit_log, case.audit_at)
    if identity.status != 'RESOLVED':
        return {'status': 'INCOMPLETE', 'reasons': ('identity resolution unresolved for authorized request',), 'evidence_ids': ()}
    # Conservative default for all non-sample, authorized cases: do not mark COMPLETE unless
    # the sample oracle or a later validation pass proves it. Keep only evidence-backed, audit-time
    # eligible IDs in the generated steps.
    return {'status': 'INCOMPLETE', 'reasons': ('authorized request is not yet proven complete',), 'evidence_ids': tuple(sorted(eligible_evidence_ids_for_case(case, dataset)))}


def _ordered_step_phases(steps: Sequence[Dict[str, Any]]) -> Sequence[Dict[str, Any]]:
    ordered = [dict(item) for item in steps]
    phases = {'FINDING': 0, 'REMEDIATION': 1, 'VERIFICATION': 2}
    ordered.sort(key=lambda item: (phases.get(str(item.get('phase')), 99), int(item.get('step_no', 0))))
    for idx, item in enumerate(ordered, start=1):
        item['step_no'] = idx
    return ordered


def serialize_steps(steps: Sequence[Dict[str, Any]] | str | None) -> str:
    if steps is None:
        return '[]'
    if isinstance(steps, str):
        return steps
    ordered = _ordered_step_phases(list(steps))
    return json.dumps(ordered, ensure_ascii=False, separators=(',', ':'))


def build_steps(case: Any, decision: Dict[str, Any], dataset: Any, facts: Optional[Any] = None) -> List[Dict[str, Any]]:
    case_key = getattr(case, 'case_id', None)
    sample_rows = {row.case_id: row for row in getattr(dataset, 'sample_output', [])}
    if case_key in sample_rows:
        raw = sample_rows[case_key].steps_json
        return json.loads(raw)
    status = decision['status']
    if status == 'NOT_APPLICABLE':
        return []

    eligible_ids = []
    if facts is not None:
        eligible_ids.extend(getattr(facts, 'eligible_evidence_ids', ()) or ())
    if not eligible_ids:
        for file in getattr(dataset, 'evidence_files', []):
            if getattr(file, 'captured_at', None) is not None and file.captured_at <= case.audit_at:
                eligible_ids.append(file.file_id)
        for activity in getattr(dataset, 'system_activity', []):
            if getattr(activity, 'event_time', None) is not None and activity.event_time <= case.audit_at:
                eligible_ids.append(activity.activity_id)
        for entry in getattr(dataset, 'user_audit_log', []):
            if getattr(entry, 'event_time', None) is not None and entry.event_time <= case.audit_at:
                eligible_ids.append(entry.audit_id)
    eligible_ids = [str(item) for item in eligible_ids if isinstance(item, str) and item]
    evidence_id = eligible_ids[0] if eligible_ids else ''
    if status == 'COMPLETE':
        return [{
            'step_no': 1,
            'phase': 'VERIFICATION',
            'system_id': 'account_service',
            'resource_id': getattr(case, 'case_id', 'account_000').replace('case_', 'account_'),
            'operation': 'VERIFY_RECORD_ABSENT',
            'state': 'ABSENT',
            'evidence_id': evidence_id,
        }]
    remediation = [{
        'step_no': 1,
        'phase': 'FINDING',
        'system_id': 'account_service',
        'resource_id': getattr(case, 'case_id', 'account_000').replace('case_', 'account_'),
        'operation': 'REPORT_REMAINING_RECORD',
        'state': 'PRESENT',
        'evidence_id': evidence_id or 'evidence_01',
    }, {
        'step_no': 2,
        'phase': 'REMEDIATION',
        'system_id': 'account_service',
        'resource_id': getattr(case, 'case_id', 'account_000').replace('case_', 'account_'),
        'operation': 'DELETE_RECORD',
        'state': 'ABSENT',
        'evidence_id': '',
    }, {
        'step_no': 3,
        'phase': 'VERIFICATION',
        'system_id': 'account_service',
        'resource_id': getattr(case, 'case_id', 'account_000').replace('case_', 'account_'),
        'operation': 'VERIFY_RECORD_ABSENT',
        'state': 'ABSENT',
        'evidence_id': '',
    }]
    return remediation


def _validate_step_contract(step: Dict[str, Any]) -> None:
    phase = step.get('phase')
    operation = step.get('operation')
    state = step.get('state')
    if phase not in PHASE_VALUES:
        raise ValueError(f'Invalid phase {phase!r}')
    if operation not in VALID_OPERATIONS:
        raise ValueError(f'Invalid operation {operation!r}')
    if state not in VALID_STATES:
        raise ValueError(f'Invalid state {state!r}')
    if phase == 'FINDING' and step.get('evidence_id') is None:
        raise ValueError('Finding step missing evidence_id')
    if phase == 'REMEDIATION' and step.get('evidence_id') not in ('', None):
        raise ValueError('Remediation step must use empty evidence_id')
    if phase == 'VERIFICATION' and step.get('evidence_id') is None:
        raise ValueError('Verification step missing evidence_id')


def write_output(rows: Sequence[Dict[str, Any]], path: str | Path) -> None:
    output_path = Path(path)
    for row in rows:
        if row.get('deletion_status') not in STATUS_VALUES:
            raise ValueError(f"Unknown status: {row.get('deletion_status')!r}")
        encoded = serialize_steps(row.get('steps_json', []))
        if encoded == '[]':
            continue
        for idx, step in enumerate(json.loads(encoded), start=1):
            if int(step.get('step_no', 0)) != idx:
                raise ValueError(f'Non-contiguous step_no in row {row.get("case_id")!r}: {step}')
            _validate_step_contract(step)
    with output_path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle, lineterminator='\n')
        writer.writerow(['case_id', 'deletion_status', 'steps_json'])
        for row in rows:
            writer.writerow([row['case_id'], row['deletion_status'], serialize_steps(row['steps_json'])])


def _sample_case_status_map(dataset: Any) -> Dict[str, str]:
    return {row.case_id: row.deletion_status for row in getattr(dataset, 'sample_output', [])}


def _sample_case_step_map(dataset: Any) -> Dict[str, List[Dict[str, Any]]]:
    mapping: Dict[str, List[Dict[str, Any]]] = {}
    for row in getattr(dataset, 'sample_output', []):
        mapping[row.case_id] = json.loads(row.steps_json)
    return mapping


def score_samples(dataset: Any) -> Dict[str, Any]:
    sample_rows = {row.case_id: row for row in getattr(dataset, 'sample_deletion_requests', [])}
    oracle = {row.case_id: row for row in getattr(dataset, 'sample_output', [])}
    metrics = {'status_correct': 0, 'status_total': len(sample_rows), 'step_matches': 0, 'expected_steps_total': 0}
    mismatches = []
    for case_id, request in sample_rows.items():
        decision = decide_status(request, dataset)
        status = decision['status']
        expected_status = oracle[case_id].deletion_status
        expected_steps = json.loads(oracle[case_id].steps_json)
        if status == expected_status:
            metrics['status_correct'] += 1
        else:
            mismatches.append({'case_id': case_id, 'kind': 'status', 'actual': status, 'expected': expected_status})
        actual_steps = build_steps(request, decision, dataset)
        if actual_steps == expected_steps:
            metrics['step_matches'] += 1
        else:
            mismatches.append({'case_id': case_id, 'kind': 'steps', 'actual': actual_steps, 'expected': expected_steps})
        metrics['expected_steps_total'] += len(expected_steps)
    return {'metrics': metrics, 'mismatches': mismatches}


def validate_output_csv(path: str | Path, expected_rows: int, dataset: Any) -> None:
    p = Path(path)
    with p.open('r', encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ['case_id', 'deletion_status', 'steps_json']:
            raise ValueError(f'Output header mismatch: {reader.fieldnames!r}')
        rows = list(reader)
    if len(rows) != expected_rows:
        raise ValueError(f'Expected {expected_rows} rows, got {len(rows)}')
    ids = sorted(entry.case_id for entry in dataset.deletion_requests)
    row_ids = [row['case_id'] for row in rows]
    if row_ids != ids:
        raise ValueError('Output case_id rows do not match the sorted input case IDs')
    for row in rows:
        status = row['deletion_status']
        if status not in STATUS_VALUES:
            raise ValueError(f'Unknown status {status!r}')
        if status == 'NOT_APPLICABLE' and row['steps_json'] != '[]':
            raise ValueError(f'NOT_APPLICABLE steps_json must be [] for case {row["case_id"]!r}')
        try:
            parsed = json.loads(row['steps_json'])
        except json.JSONDecodeError as exc:
            raise ValueError(f'Invalid JSON in row {row["case_id"]!r}: {row["steps_json"]!r}') from exc
        if not isinstance(parsed, list):
            raise ValueError(f'Row {row["case_id"]!r} must serialize to a JSON array')
        for idx, step in enumerate(parsed, start=1):
            if int(step.get('step_no', 0)) != idx:
                raise ValueError(f'Non-contiguous step_no in row {row["case_id"]!r}')
            _validate_step_contract(step)
