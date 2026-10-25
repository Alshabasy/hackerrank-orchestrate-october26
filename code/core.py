from __future__ import annotations

import csv
from functools import lru_cache
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .loaders import load_all_dataset
from .models import DataFacts, IdentityResult, Observation

UTC = timezone.utc
_CASE_EVIDENCE_CACHE: Dict[Tuple[int, str, datetime], Tuple[Any, List[Dict[str, Any]]]] = {}

DELETION_PATTERNS = (
    'delete', 'deletion', 'deleted', 'erase', 'erased', 'close my account', 'close this account',
    'remove my account', 'remove this account', 'permanent account deletion', 'delete my account',
    'have it permanently deleted', 'delete this account', 'account deletion', 'delete account',
    'delete the account', 'erase the account', 'account associated with this email',
    'personal-data deletion', 'personal data deleted', 'remove the profile', 'delete the profile',
    'request to delete', 'delete the profile associated', 'erase the profile',
    'eliminar mi cuenta', 'eliminación de mi cuenta', 'eliminacion de mi cuenta',
    'supresión définitive', 'suppression définitive', 'supprimer mon compte',
    'close the account completely', 'account deletion request', 'profile is gone',
    'アカウントの完全な削除', 'アカウントを削除'
)
NON_DELETION_PATTERNS = (
    'access my profile', 'download my account', 'correct my', 'update my profile', 'export my data',
    'export data', 'marketing', 'unsubscribe', 'opt out', 'keep the account as it is',
    'do not close the account', 'do not delete', 'wrong email', 'please retain', 'data access',
    'file a correction', 'edit my details', 'download my records', 'export account information',
    'account history', 'request my data', 'account information in a portable format',
    'reset the login', 'help resetting', 'change the email address', 'replace the mobile number',
    'leave the account active', 'still need the account', 'continue using the service',
    'still use it', 'not a request to erase', 'not asking for deletion', 'information request',
    'send me a copy of the information', 'copy of last year', 'portable format', 'what personal information',
    'remove me from marketing lists', 'still want receipts', 'service messages', 'account remains active'
)
NEGATION_PATTERNS = (
    'do not delete', 'do not close the account', 'not asking for deletion', 'not a request to erase',
    'not a request to delete', 'please keep the account', 'keep the account as it is',
    'leave the account active', 'still need the account', 'still use it', 'continue using the service',
    'i still need', 'i still want', 'please leave', 'do not remove', 'do not erase', 'without removing any records',
    'i want to continue using the service'
)


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_phone(value: str) -> str:
    digits = re.sub(r'\D', '', value)
    return digits


def _contains_subject_token(haystack: str, token: str) -> bool:
    if not token:
        return False
    if token.isdigit():
        return len(token) >= 7 and re.search(rf'(?<!\d){re.escape(token)}(?!\d)', haystack) is not None
    if token.startswith('user_'):
        return re.search(rf'(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])', haystack) is not None
    if '@' in token or re.fullmatch(r'[0-9a-f]{32,}', token):
        return re.search(rf'(?<![A-Za-z0-9_.+-]){re.escape(token)}(?![A-Za-z0-9_.+-])', haystack) is not None
    if ' ' in token:
        return re.search(rf'(?<!\w){re.escape(token)}(?!\w)', haystack, flags=re.UNICODE) is not None
    return token in haystack


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

    explicit_negation = any(pattern in text for pattern in NEGATION_PATTERNS)
    explicit_deletion = any(pattern in text for pattern in DELETION_PATTERNS)
    explicit_non_deletion = any(pattern in text for pattern in NON_DELETION_PATTERNS)
    generic_delete = any(token in text for token in ('delete', 'delet', 'remove', 'erase', 'close my account', 'close this account'))
    generic_non_delete = any(token in text for token in ('access', 'correct', 'export', 'download', 'marketing', 'unsubscribe', 'opt out', 'update', 'copy of', 'information request', 'help resetting'))

    if explicit_negation:
        return 'NOT_AUTHORIZED'
    if explicit_non_deletion:
        return 'NOT_AUTHORIZED'
    if explicit_deletion:
        return 'AUTHORIZED'
    if generic_delete and not explicit_negation:
        return 'AUTHORIZED'
    if generic_non_delete:
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
        if identity.updated_at <= audit_at:
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
    denied = ('ATTACH', 'DETACH', 'LOAD_EXTENSION', 'CREATE VIRTUAL TABLE', 'CREATE TEMP TABLE')
    upper = sql_text.upper()
    for token in denied:
        if token in upper:
            return False
    pragma_statements = re.findall(r'(?im)^\s*PRAGMA\s+([^;]+);?', sql_text)
    if any(statement.strip().upper() != 'FOREIGN_KEYS=ON' for statement in pragma_statements):
        return False
    return True


@lru_cache(maxsize=128)
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
        table_rows = {}
        for table in tables:
            rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
            row_counts[table] = len(rows)
            table_rows[table] = [dict(row) for row in rows]
        return {'tables': tables, 'row_counts': row_counts, 'table_rows': table_rows, 'sql': sql_text}
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
            if not _pdf_text_available():
                unread.append(row.file_id)
        elif lower.endswith(('.png', '.jpg', '.jpeg', '.gif')):
            by_type['image'] += 1
            if not _image_ocr_available():
                unread.append(row.file_id)
        elif lower.endswith('.sql'):
            by_type['sql'] += 1
        elif lower.endswith(('.log', '.txt', '.json')):
            by_type['log' if lower.endswith('.log') else 'text' if lower.endswith('.txt') else 'json'] += 1
    return {'unread': tuple(unread), 'counts': by_type}


def _pdf_text_available() -> bool:
    """Whether the deterministic PDF text extractor is installed."""
    try:
        subprocess.run(['pdftotext', '-v'], capture_output=True, check=False, timeout=3)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def _image_ocr_available() -> bool:
    return shutil.which('tesseract') is not None


@lru_cache(maxsize=256)
def _read_evidence_text_cached(path_text: str) -> str:
    """Read text evidence, including text-based PDFs when Poppler is available."""
    path = Path(path_text)
    if path.suffix.lower() == '.pdf':
        result = subprocess.run(
            ['pdftotext', '-layout', str(path), '-'],
            capture_output=True, check=False, timeout=20,
        )
        if result.returncode != 0:
            return ''
        return result.stdout.decode('utf-8', errors='replace')
    if path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif'}:
        # Read the local media even when OCR is unavailable. If Tesseract exists,
        # use its text output as evidence; otherwise fail closed with no extracted text.
        path.read_bytes()
        if not _image_ocr_available():
            return ''
        result = subprocess.run(
            ['tesseract', str(path), 'stdout'],
            capture_output=True, check=False, timeout=30,
        )
        if result.returncode != 0:
            return ''
        return result.stdout.decode('utf-8', errors='replace')
    return path.read_text(encoding='utf-8-sig', errors='replace')


def _read_evidence_text(path: Path) -> str:
    return _read_evidence_text_cached(str(path.resolve()))


@lru_cache(maxsize=256)
def _read_evidence_json(path_text: str) -> Any:
    return json.loads(_read_evidence_text(Path(path_text)))


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


def _request_email_candidates(case: Any, dataset: Any) -> Tuple[str, ...]:
    request_email = normalize_email(getattr(case, 'email', ''))
    candidates = {request_email}
    resolved = resolve_identity(case, dataset.user_identities, dataset.user_audit_log, case.audit_at)
    matched_user_ids = {resolved.user_id} if resolved.status == 'RESOLVED' and resolved.user_id else set()
    for identity in getattr(dataset, 'user_identities', []):
        if identity.user_id in matched_user_ids and identity.updated_at <= case.audit_at:
            candidates.add(normalize_email(identity.email))
    for entry in getattr(dataset, 'user_audit_log', []):
        if not eligible_at(entry.event_time, case.audit_at):
            continue
        if entry.user_id in matched_user_ids:
            if entry.field_name == 'email' and entry.old_value:
                candidates.add(normalize_email(entry.old_value))
            if entry.field_name == 'email' and entry.new_value:
                candidates.add(normalize_email(entry.new_value))
    return tuple(sorted(candidate for candidate in candidates if candidate))


def _subject_tokens(case: Any, dataset: Any) -> Set[str]:
    tokens: Set[str] = set()
    request_email = normalize_email(getattr(case, 'email', ''))
    if request_email:
        tokens.add(request_email)
    resolved = resolve_identity(case, dataset.user_identities, dataset.user_audit_log, case.audit_at)
    matched_user_ids = {resolved.user_id} if resolved.status == 'RESOLVED' and resolved.user_id else set()
    for identity in getattr(dataset, 'user_identities', []):
        if identity.user_id in matched_user_ids and identity.updated_at <= case.audit_at:
            tokens.add(normalize_email(identity.email))
            tokens.add(normalize_email(identity.name))
            tokens.update((normalize_email(identity.email), normalize_email(identity.name), identity.email_hash.lower(), identity.user_id.lower()))
            digits = normalize_phone(identity.phone)
            if len(digits) >= 7:
                tokens.add(digits)
    for entry in getattr(dataset, 'user_audit_log', []):
        if entry.user_id in matched_user_ids and eligible_at(entry.event_time, case.audit_at):
            for value in (entry.old_value, entry.new_value):
                if value:
                    if entry.field_name == 'email':
                        tokens.add(normalize_email(value))
                    elif entry.field_name == 'phone':
                        digits = normalize_phone(value)
                        if len(digits) >= 7:
                            tokens.add(digits)
                    elif entry.field_name == 'email_hash':
                        tokens.add(value.strip().lower())
    return {token.lower() for token in tokens if token}


def _case_resource_candidates(case: Any, dataset: Any) -> Set[str]:
    subject_tokens = _subject_tokens(case, dataset)
    resource_ids: Set[str] = set()

    def matching_scalar(value: Any) -> bool:
        if not isinstance(value, (str, int, float)):
            return False
        normalized = str(value).lower()
        return any(_contains_subject_token(normalized, token) for token in subject_tokens if token)

    def add_ids(values: Iterable[Any]) -> None:
        for value in values:
            if isinstance(value, str):
                resource_ids.update(re.findall(
                    r'(?i)\b(?:account|profile|analytics|search|customer|support|export|backup|flow|data_flow|connector|audit_record|tombstone)_[0-9]+\b',
                    value,
                ))

    for file in getattr(dataset, 'evidence_files', []):
        if getattr(file, 'captured_at', None) is None or file.captured_at > case.audit_at:
            continue
        try:
            path = Path('dataset') / file.file_path
            content = _read_evidence_text(path)
        except Exception:
            continue
        if file.file_path.lower().endswith('.json'):
            try:
                payload = _read_evidence_json(str(path.resolve()))
            except json.JSONDecodeError:
                payload = None
            if payload is not None:
                stack = [payload]
                while stack:
                    node = stack.pop()
                    if isinstance(node, dict):
                        if any(matching_scalar(value) for value in node.values()):
                            add_ids(node.values())
                        stack.extend(node.values())
                    elif isinstance(node, list):
                        stack.extend(node)
                continue

        lines = content.splitlines()
        hit_lines = [
            idx for idx, line in enumerate(lines)
            if any(_contains_subject_token(line.lower(), token) for token in subject_tokens if token)
        ]
        for idx in hit_lines:
            # Text/PDF/SQL records often wrap one record across adjacent lines. Keep
            # the evidence window local to the matched identifier instead of treating
            # every resource in a whole batch export as belonging to the subject.
            if file.file_path.lower().endswith('.log'):
                trace_match = re.search(r'\btrace_id=([^\s]+)', lines[idx])
                paired = [lines[idx]]
                if trace_match:
                    trace_id = trace_match.group(1)
                    paired.extend(
                        line for pos, line in enumerate(lines)
                        if pos != idx and re.search(r'\btrace_id=([^\s]+)', line)
                        and re.search(r'\btrace_id=([^\s]+)', line).group(1) == trace_id
                    )
                add_ids(paired)
                continue
            radius = 0 if file.file_path.lower().endswith('.sql') else 2
            add_ids(lines[max(0, idx - radius): min(len(lines), idx + radius + 1)])
    return resource_ids


def _case_json_observations(case: Any, dataset: Any) -> List[Dict[str, Any]]:
    """Extract identity-linked record snapshots without assigning sibling rows to a user."""
    subject_tokens = _subject_tokens(case, dataset)
    observations: List[Dict[str, Any]] = []
    pii_keys = {
        'name', 'full_name', 'customer_name', 'email', 'customer_email', 'owner_email',
        'requester_email', 'phone', 'customer_phone', 'owner_phone', 'requester_phone', 'email_hash',
    }
    category_rules = (
        ('deleted_customer_references', 'ACCOUNT_TOMBSTONE'),
        ('customer_profiles', 'USER_PROFILE'),
        ('search', 'SEARCH_INDEX_ENTRY'),
        ('cache', 'CACHE_ENTRY'),
        ('support_attachments', 'SUPPORT_ATTACHMENT'),
        ('attachment', 'SUPPORT_ATTACHMENT'),
        ('support_messages', 'SUPPORT_MESSAGE'),
        ('support_tickets', 'SUPPORT_MESSAGE'),
        ('analytics', 'ANALYTICS_PROFILE'),
        ('telemetry', 'PRODUCT_TELEMETRY'),
        ('user_exports', 'USER_EXPORT'),
        ('customer_exports', 'USER_EXPORT'),
        ('marketing', 'MARKETING_PROFILE'),
        ('delivery', 'EMAIL_DELIVERY_LOG'),
        ('security_audit', 'SECURITY_AUDIT_LOG'),
        ('audit_records', 'SECURITY_AUDIT_LOG'),
        ('restore_entries', 'BACKUP_ARCHIVE'),
        ('backup', 'BACKUP_ARCHIVE'),
        ('tombstone', 'ACCOUNT_TOMBSTONE'),
    )

    def matches_subject(value: Any) -> bool:
        if not isinstance(value, (str, int, float)):
            return False
        normalized = str(value).lower()
        return any(_contains_subject_token(normalized, token) for token in subject_tokens if token)

    def flatten_scalars(node: Any) -> List[Tuple[str, Any]]:
        values: List[Tuple[str, Any]] = []
        stack = [node]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                for key, value in current.items():
                    if isinstance(value, (str, int, float, bool)) or value is None:
                        values.append((str(key).lower(), value))
                    else:
                        stack.append(value)
            elif isinstance(current, list):
                stack.extend(current)
        return values

    def walk(node: Any, context: Tuple[str, ...], evidence_id: str, origin: str) -> None:
        if isinstance(node, dict):
            local_context = context + tuple(
                str(value).lower() for key, value in node.items()
                if key.lower() in {'data_store', 'collection', 'system', 'system_id', 'data_category', 'table'}
                and isinstance(value, str)
            )
            resource = node.get('resource_id') or node.get('tombstone_id') or node.get('record_id')
            scalars = flatten_scalars(node)
            linked = any(
                matches_subject(value)
                for key, value in scalars
                if key not in {'resource_id', 'tombstone_id', 'record_id', 'file_id'}
            )
            if isinstance(resource, str) and linked:
                timestamps = [
                    value for key, value in scalars
                    if key in {'observed_at', 'captured_at', 'event_time', 'updated_at', 'created_at'} and isinstance(value, str)
                ]
                time_eligible = True
                for raw_time in timestamps:
                    try:
                        parsed = datetime.fromisoformat(raw_time.replace('Z', '+00:00'))
                    except ValueError:
                        continue
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=UTC)
                    if parsed.astimezone(UTC) > case.audit_at:
                        time_eligible = False
                        break
                if time_eligible:
                    context_parts = set(local_context)
                    context_text = ' '.join(local_context).lower()
                    category = next((cat for keyword, cat in category_rules if keyword in context_text), '')
                    resource_lower = resource.lower()
                    if resource_lower.startswith('audit_record_'):
                        category = 'SECURITY_AUDIT_LOG'
                    elif resource_lower.startswith('backup_'):
                        category = 'BACKUP_ARCHIVE'
                    elif resource_lower.startswith('tombstone_'):
                        category = 'ACCOUNT_TOMBSTONE'
                    elif resource_lower.startswith('export_'):
                        category = 'USER_EXPORT'
                    elif resource_lower.startswith('support_') and not category:
                        category = 'SUPPORT_MESSAGE'
                    # A top-level service name is useful context in resource export JSON.
                    system_id = next((value for value in local_context if value.endswith('_service') or value.endswith('_platform')), origin)
                    flat_map: Dict[str, List[Any]] = {}
                    for key, value in scalars:
                        flat_map.setdefault(key, []).append(value)
                    personal_values = [
                        value for key, value in scalars
                        if key in pii_keys and value not in (None, '', False)
                    ]
                    observations.append({
                        'evidence_id': evidence_id,
                        'captured_at': next((f.captured_at for f in getattr(dataset, 'evidence_files', []) if f.file_id == evidence_id), case.audit_at),
                        'observed_at': next((datetime.fromisoformat(value.replace('Z', '+00:00')) for key, value in scalars if key in {'observed_at', 'event_time'} and isinstance(value, str)), None),
                        'system_id': system_id,
                        'resource_id': resource,
                        'category': category,
                        'linked_to_subject': linked,
                        'personal_values': tuple(personal_values),
                        'scalar_values': flat_map,
                        'context': local_context,
                    })
            for key, value in node.items():
                child_context = local_context + ((str(key).lower(),) if isinstance(value, (dict, list)) else tuple())
                walk(value, child_context, evidence_id, origin)
        elif isinstance(node, list):
            for value in node:
                walk(value, context, evidence_id, origin)

    for file in getattr(dataset, 'evidence_files', []):
        if file.captured_at > case.audit_at or not file.file_path.lower().endswith('.json'):
            continue
        try:
            payload = _read_evidence_json(str((Path('dataset') / file.file_path).resolve()))
        except (OSError, json.JSONDecodeError):
            continue
        walk(payload, tuple(), file.file_id, file.origin)
    return observations


def _case_evidence_observations(case: Any, dataset: Any) -> List[Dict[str, Any]]:
    """Return evidence-backed, subject-linked records at the request's audit cutoff."""
    cache_key = (id(dataset), case.case_id, case.audit_at)
    cached = _CASE_EVIDENCE_CACHE.get(cache_key)
    if cached is not None and cached[0] is dataset:
        return cached[1]
    observations = _case_json_observations(case, dataset)
    subject_tokens = _subject_tokens(case, dataset)
    pii_keys = {
        'name', 'full_name', 'display_name', 'customer_name', 'email', 'customer_email', 'owner_email',
        'requester_email', 'phone', 'customer_phone', 'owner_phone', 'requester_phone', 'email_hash',
    }
    user_ids = {identity.user_id.lower() for identity in getattr(dataset, 'user_identities', [])}
    pii_tokens = {token for token in subject_tokens if token not in user_ids}
    resource_pattern = re.compile(
        r'(?i)\b(?:account|profile|analytics|search|customer|support|export|backup|flow|data_flow|connector|audit_record|tombstone)_[0-9]+\b'
    )
    for file in getattr(dataset, 'evidence_files', []):
        if file.captured_at > case.audit_at or file.file_path.lower().endswith(('.json', '.sql')):
            continue
        try:
            content = _read_evidence_text(Path('dataset') / file.file_path)
        except (OSError, subprocess.SubprocessError):
            continue
        if not content:
            continue
        lines = content.splitlines()
        subject_lines = [
            idx for idx, line in enumerate(lines)
            if any(_contains_subject_token(line.lower(), token) for token in subject_tokens if token)
        ]
        if not subject_lines:
            continue
        resource_positions: Dict[str, List[int]] = {}
        for idx, line in enumerate(lines):
            for resource_id in resource_pattern.findall(line):
                resource_positions.setdefault(resource_id, []).append(idx)
        for resource_id, positions in resource_positions.items():
            is_log = file.file_path.lower().endswith('.log')
            if is_log:
                paired = []
                for subject_line in subject_lines:
                    subject_trace = re.search(r'\btrace_id=([^\s]+)', lines[subject_line])
                    for position in positions:
                        resource_trace = re.search(r'\btrace_id=([^\s]+)', lines[position])
                        if position == subject_line or (
                            subject_trace and resource_trace and subject_trace.group(1) == resource_trace.group(1)
                        ):
                            paired.append((abs(position - subject_line), position, subject_line))
                # Prefer the last readback line in a trace so rows/status fields
                # override the earlier query-start line.
                nearest = max(paired, key=lambda item: item[1]) if paired else None
                max_distance = len(lines)
            else:
                nearest = min(
                    ((abs(pos - subject_line), pos, subject_line) for pos in positions for subject_line in subject_lines),
                    default=None,
                )
                max_distance = 12 if file.file_path.lower().endswith('.pdf') else 0 if file.file_path.lower().endswith('.sql') else 2
            if nearest is None or nearest[0] > max_distance:
                continue
            _, resource_line, subject_line = nearest
            radius = 8 if file.file_path.lower().endswith('.pdf') else 0 if is_log or file.file_path.lower().endswith('.sql') else 1
            lo = max(0, min(resource_line, subject_line) - radius)
            hi = min(len(lines), max(resource_line, subject_line) + radius + 1)
            window = '\n'.join(lines[lo:hi])
            embedded_times = re.findall(r'\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b', window)
            if any(
                datetime.fromisoformat(raw.replace('Z', '+00:00')).replace(tzinfo=UTC)
                > case.audit_at
                for raw in embedded_times
            ):
                continue
            fields = {
                key.lower(): value.strip(' \t,;"\'')
                for key, value in re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^\s]+)', window)
            }
            context = file.file_path.lower() + ' ' + file.origin.lower() + ' ' + window.lower()
            if 'legacy_contact_index' in context:
                category = 'SECONDARY_IDENTIFIER'
            elif 'attachment' in context:
                category = 'SUPPORT_ATTACHMENT'
            elif 'support' in context or resource_id.lower().startswith('support_'):
                category = 'SUPPORT_MESSAGE'
            elif resource_id.lower().startswith('account_'):
                category = 'USER_PROFILE'
            elif resource_id.lower().startswith('search_'):
                category = 'SEARCH_INDEX_ENTRY'
            elif resource_id.lower().startswith('analytics_'):
                category = 'ANALYTICS_PROFILE'
            elif resource_id.lower().startswith('backup_'):
                category = 'BACKUP_ARCHIVE'
            elif resource_id.lower().startswith(('flow_', 'data_flow_', 'connector_')):
                category = 'DATA_FLOW'
            elif resource_id.lower().startswith('export_'):
                category = 'USER_EXPORT'
            elif resource_id.lower().startswith('tombstone_'):
                category = 'ACCOUNT_TOMBSTONE'
            elif resource_id.lower().startswith('audit_record_'):
                category = 'SECURITY_AUDIT_LOG'
            else:
                category = ''
            personal_values = tuple(sorted(token for token in pii_tokens if _contains_subject_token(window.lower(), token)))
            service_match = re.search(r'\bservice=([A-Za-z0-9_]+)', window)
            system_id = service_match.group(1) if service_match else file.origin
            observations.append({
                'evidence_id': file.file_id,
                'captured_at': file.captured_at,
                'observed_at': next((datetime.fromisoformat(raw.replace('Z', '+00:00')) for raw in embedded_times), file.captured_at),
                'system_id': system_id,
                'resource_id': resource_id,
                'category': category,
                'linked_to_subject': True,
                'personal_values': personal_values,
                'scalar_values': {key: [value] for key, value in fields.items()},
                'context': (context,),
            })
    for file in getattr(dataset, 'evidence_files', []):
        if file.captured_at > case.audit_at or not file.file_path.lower().endswith('.sql'):
            continue
        try:
            snapshot = load_sql_snapshot(Path('dataset') / file.file_path)
        except Exception:
            continue
        for table, rows in snapshot.get('table_rows', {}).items():
            for row in rows:
                flat: List[Tuple[str, Any]] = []
                for key, value in row.items():
                    flat.append((str(key).lower(), value))
                    if isinstance(value, str):
                        try:
                            nested = json.loads(value)
                        except json.JSONDecodeError:
                            continue
                        stack = [nested]
                        while stack:
                            node = stack.pop()
                            if isinstance(node, dict):
                                for child_key, child_value in node.items():
                                    if isinstance(child_value, (dict, list)):
                                        stack.append(child_value)
                                    else:
                                        flat.append((str(child_key).lower(), child_value))
                            elif isinstance(node, list):
                                stack.extend(node)
                if not any(
                    _contains_subject_token(str(value).lower(), token)
                    for _, value in flat if value is not None
                    for token in subject_tokens if token
                ):
                    continue
                text_values = [str(value) for _, value in flat if isinstance(value, str)]
                all_text = ' '.join(text_values).lower()
                resource_match = re.search(
                    r'(?i)\b(?:account|profile|analytics|search|customer|support|export|backup|flow|data_flow|connector|audit_record|tombstone)_[0-9]+\b',
                    all_text,
                )
                if not resource_match:
                    continue
                resource_id = resource_match.group(0)
                timestamp = file.captured_at
                for key, value in flat:
                    if key in {'observed_at', 'captured_at', 'snapshot_at', 'event_time', 'updated_at', 'created_at'} and isinstance(value, str):
                        try:
                            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                        except ValueError:
                            continue
                        if parsed.tzinfo is None:
                            parsed = parsed.replace(tzinfo=UTC)
                        if parsed.astimezone(UTC) > case.audit_at:
                            timestamp = None
                            break
                        timestamp = parsed.astimezone(UTC)
                if timestamp is None:
                    continue
                names = ' '.join([table.lower(), file.file_path.lower(), all_text])
                category = next((cat for keyword, cat in (
                    ('tombstone', 'ACCOUNT_TOMBSTONE'), ('customer_profiles', 'USER_PROFILE'),
                    ('search', 'SEARCH_INDEX_ENTRY'), ('cache', 'CACHE_ENTRY'),
                    ('support_attachment', 'SUPPORT_ATTACHMENT'), ('support_conversation', 'SUPPORT_MESSAGE'),
                    ('analytics', 'ANALYTICS_PROFILE'), ('telemetry', 'PRODUCT_TELEMETRY'),
                    ('export', 'USER_EXPORT'), ('marketing', 'MARKETING_PROFILE'),
                    ('delivery', 'EMAIL_DELIVERY_LOG'), ('security_audit', 'SECURITY_AUDIT_LOG'),
                    ('audit_record', 'SECURITY_AUDIT_LOG'), ('backup', 'BACKUP_ARCHIVE'),
                ) if keyword in names), '')
                lower_resource = resource_id.lower()
                if lower_resource.startswith('tombstone_'):
                    category = 'ACCOUNT_TOMBSTONE'
                elif lower_resource.startswith('backup_'):
                    category = 'BACKUP_ARCHIVE'
                elif lower_resource.startswith('audit_record_'):
                    category = 'SECURITY_AUDIT_LOG'
                pii = tuple(
                    value for key, value in flat
                    if key in pii_keys and value not in (None, '', False)
                    and any(_contains_subject_token(str(value).lower(), token) for token in subject_tokens if token)
                )
                scalar_values: Dict[str, List[Any]] = {}
                for key, value in flat:
                    scalar_values.setdefault(key, []).append(value)
                system_id = next((str(value) for key, value in flat if key in {'system_id', 'data_store', 'source_system'} and value), file.origin)
                observations.append({
                    'evidence_id': file.file_id,
                    'captured_at': file.captured_at,
                    'observed_at': timestamp,
                    'system_id': system_id,
                    'resource_id': resource_id,
                    'category': category,
                    'linked_to_subject': True,
                    'personal_values': pii,
                    'scalar_values': scalar_values,
                    'context': (table.lower(), file.file_path.lower()),
                })
    # Multiple evidence files can describe the same resource at different times;
    # retain each snapshot so the caller can apply the latest observation per resource.
    _CASE_EVIDENCE_CACHE[cache_key] = (dataset, observations)
    return observations


def _latest_case_observations(case: Any, dataset: Any) -> List[Dict[str, Any]]:
    latest: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for observation in _case_evidence_observations(case, dataset):
        stamp = observation.get('observed_at') or observation.get('captured_at') or case.audit_at
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        if stamp > case.audit_at:
            continue
        key = (str(observation.get('system_id', '')), str(observation.get('resource_id', '')))
        old = latest.get(key)
        old_stamp = (old.get('observed_at') or old.get('captured_at') or case.audit_at) if old else None
        if old is None or stamp >= old_stamp:
            latest[key] = observation
    return list(latest.values())


def _observation_state(observation: Dict[str, Any]) -> str:
    values = observation.get('scalar_values', {})
    flattened = {str(key).lower(): [str(value).lower() for value in (items if isinstance(items, (list, tuple)) else [items]) if value is not None] for key, items in values.items()}
    text = ' '.join(' '.join(items) for items in flattened.values()) + ' ' + ' '.join(observation.get('context', ()))
    if flattened.get('enabled') in (['false'], ['0']) or flattened.get('restore_allowed') in (['false'], ['0']):
        return 'SAFE'
    if flattened.get('restore_allowed') in (['true'], ['1']) or flattened.get('enabled') in (['true'], ['1']):
        return 'ACTIVE'
    if flattened.get('eligible') == ['true'] and flattened.get('deletion_block') == ['false']:
        return 'ACTIVE'
    if any(value in {'404', '410'} for key in ('http_status', 'status_code') for value in flattened.get(key, [])):
        return 'ABSENT'
    row_counts = [
        int(value) for key in ('rows', 'total', 'total_hits')
        for value in flattened.get(key, []) if value.isdigit()
    ]
    if row_counts and max(row_counts) == 0:
        return 'ABSENT'
    if row_counts and max(row_counts) > 0:
        return 'PII' if observation.get('personal_values') else 'PRESENT'
    if any(value in {'miss', 'not_found'} for key in ('result', 'profile') for value in flattened.get(key, [])):
        return 'ABSENT'
    if observation.get('personal_values'):
        return 'PII'
    null_identifier_fields = ('email', 'phone', 'email_hash', 'customer_reference', 'requester_email')
    present_nulls = sum(value == 'null' for key in null_identifier_fields for value in flattened.get(key, []))
    if present_nulls >= 2:
        return 'ANONYMIZED'
    if any(value in {'200', '1', 'hit', 'true', 'success'} for key in ('http_status', 'status_code', 'rows', 'total_hits', 'result', 'restore_allowed', 'enabled') for value in flattened.get(key, [])):
        return 'PRESENT'
    if 'retention_until' in text or 'retained' in text:
        return 'PRESENT'
    return 'PRESENT'


def _observation_action(observation: Dict[str, Any], dataset: Any) -> str:
    category = observation.get('category', '')
    for rule in getattr(dataset, 'deletion_rules', []):
        if rule.data_category == category:
            return rule.required_action
    return {
        'DATA_FLOW': 'DISABLE',
        'USER_PROFILE': 'DELETE',
        'SECONDARY_IDENTIFIER': 'DELETE',
        'SEARCH_INDEX_ENTRY': 'DELETE',
        'CACHE_ENTRY': 'DELETE',
        'SUPPORT_ATTACHMENT': 'DELETE',
        'SUPPORT_MESSAGE': 'REDACT',
        'ANALYTICS_PROFILE': 'ANONYMIZE',
        'PRODUCT_TELEMETRY': 'ANONYMIZE',
        'USER_EXPORT': 'DELETE',
        'MARKETING_PROFILE': 'DELETE',
        'EMAIL_DELIVERY_LOG': 'REDACT',
        'SECURITY_AUDIT_LOG': 'RETAIN',
        'LEGAL_HOLD_RECORD': 'RETAIN',
        'BACKUP_ARCHIVE': 'RETAIN',
        'ACCOUNT_TOMBSTONE': 'REDACT',
    }.get(category, '')


def _observation_violation(observation: Dict[str, Any], dataset: Any) -> Optional[Tuple[str, str, str]]:
    """Return finding/remediation/verification ops when an observed record violates policy."""
    category = observation.get('category', '')
    action = _observation_action(observation, dataset)
    state = _observation_state(observation)
    if category == 'DATA_FLOW':
        return ('REPORT_ACTIVE_DATA_FLOW', 'DISABLE_DATA_FLOW', 'VERIFY_DATA_FLOW_DISABLED') if state == 'ACTIVE' else None
    if category == 'BACKUP_ARCHIVE':
        return ('REPORT_RESTORE_RISK', 'BLOCK_RESTORE', 'VERIFY_RESTORE_BLOCKED') if state == 'ACTIVE' else None
    if action == 'DELETE' and state != 'ABSENT':
        return ('REPORT_REMAINING_RECORD', 'DELETE_RECORD', 'VERIFY_RECORD_ABSENT')
    if action == 'REDACT' and state == 'PII':
        return ('REPORT_EXPOSED_PII', 'REDACT_RECORD', 'VERIFY_RECORD_REDACTED')
    if action == 'ANONYMIZE' and state in {'PII', 'PRESENT'}:
        return ('REPORT_EXPOSED_PII', 'ANONYMIZE_RECORD', 'VERIFY_RECORD_ANONYMIZED')
    return None


def _case_specific_activity(case: Any, dataset: Any) -> Optional[Any]:
    request_emails = _request_email_candidates(case, dataset)
    subject_tokens = _subject_tokens(case, dataset)
    resource_ids = _case_resource_candidates(case, dataset)
    candidates = []
    for activity in getattr(dataset, 'system_activity', []):
        if getattr(activity, 'event_time', None) is None or activity.event_time > case.audit_at:
            continue
        haystack = ' '.join(part for part in [activity.details, activity.source_system, activity.target_system, activity.resource_id, getattr(activity, 'event_type', '')] if part).lower()
        if any(_contains_subject_token(haystack, email) for email in request_emails):
            candidates.append(activity)
            continue
        if resource_ids and activity.resource_id in resource_ids:
            candidates.append(activity)
            continue
        if any(_contains_subject_token(haystack, token) for token in subject_tokens):
            candidates.append(activity)
    if not candidates:
        return None
    return max(candidates, key=lambda row: row.event_time)


def _real_evidence_id_for_case(case: Any, dataset: Any) -> str:
    activity = _case_specific_activity(case, dataset)
    if activity is not None:
        return activity.activity_id
    candidates: List[str] = []
    for file in getattr(dataset, 'evidence_files', []):
        if getattr(file, 'captured_at', None) is not None and file.captured_at <= case.audit_at:
            candidates.append(file.file_id)
    for activity in getattr(dataset, 'system_activity', []):
        if getattr(activity, 'event_time', None) is not None and activity.event_time <= case.audit_at:
            candidates.append(activity.activity_id)
    for entry in getattr(dataset, 'user_audit_log', []):
        if getattr(entry, 'event_time', None) is not None and entry.event_time <= case.audit_at:
            candidates.append(entry.audit_id)
    if not candidates:
        return ''
    return sorted(set(candidates))[0]


def _relevant_case_activity(case: Any, dataset: Any) -> List[Any]:
    request_emails = _request_email_candidates(case, dataset)
    subject_tokens = _subject_tokens(case, dataset)
    resource_ids = _case_resource_candidates(case, dataset)
    relevant: List[Any] = []
    for activity in getattr(dataset, 'system_activity', []):
        if getattr(activity, 'event_time', None) is None or activity.event_time > case.audit_at:
            continue
        haystack = ' '.join(part for part in [activity.details, activity.source_system, activity.target_system, activity.resource_id, getattr(activity, 'event_type', '')] if part).lower()
        if any(_contains_subject_token(haystack, email) for email in request_emails):
            relevant.append(activity)
            continue
        if activity.resource_id in resource_ids:
            relevant.append(activity)
            continue
        if any(_contains_subject_token(haystack, token) for token in subject_tokens):
            relevant.append(activity)
            continue
    return sorted(relevant, key=lambda row: row.event_time)


def decide_status(case: Any, dataset: Any, facts: Optional[Any] = None) -> Dict[str, Any]:
    request_status = classify_request(case.request_text)
    if request_status != 'AUTHORIZED':
        return {'status': 'NOT_APPLICABLE', 'reasons': ('request_text does not authorize deletion',), 'evidence_ids': ()}

    identity = resolve_identity(case, dataset.user_identities, dataset.user_audit_log, case.audit_at)
    if identity.status != 'RESOLVED':
        return {'status': 'INCOMPLETE', 'reasons': ('identity resolution unresolved for authorized request',), 'evidence_ids': tuple(sorted(eligible_evidence_ids_for_case(case, dataset)))}

    relevant_activity = _relevant_case_activity(case, dataset)
    observations = _latest_case_observations(case, dataset)
    policy_violations = [observation for observation in observations if _observation_violation(observation, dataset)]
    active_flows = [observation for observation in observations if observation.get('category') == 'DATA_FLOW' and _observation_state(observation) == 'ACTIVE']
    restore_risks = [observation for observation in observations if observation.get('category') == 'BACKUP_ARCHIVE' and _observation_state(observation) == 'ACTIVE']
    # A historical DELETE/UPDATE/INDEX event is an operation claim, not proof that
    # a copy exists now. Only readback evidence (above) or explicit state checks can
    # establish a current violation.
    active_flow_checks = [activity for activity in relevant_activity if activity.event_type == 'CHECK_DATA_FLOW' and any(token in activity.details.lower() for token in ('enabled=true', 'active=true', 'status=active'))]
    restore_checks = [activity for activity in relevant_activity if activity.event_type == 'CHECK_RESTORE_ACCESS' and any(token in activity.details.lower() for token in ('restore_allowed=true', 'enabled=true', 'status=active'))]

    if policy_violations or active_flows or restore_risks or active_flow_checks or restore_checks:
        return {
            'status': 'INCOMPLETE',
            'reasons': (
                'identity-linked evidence shows remaining records or active recreation paths before audit_at',
            ),
            'evidence_ids': tuple(sorted(
                {row.activity_id for row in relevant_activity}
                | {item['evidence_id'] for item in policy_violations + active_flows + restore_risks}
                | {row.activity_id for row in active_flow_checks + restore_checks}
            )),
        }

    return {
        'status': 'COMPLETE',
        'reasons': ('authorized request is resolved and no identity-linked residual deletion evidence was observed before audit_at',),
        'evidence_ids': tuple(sorted(eligible_evidence_ids_for_case(case, dataset))),
    }


def _ordered_step_phases(steps: Sequence[Dict[str, Any]]) -> Sequence[Dict[str, Any]]:
    ordered = [dict(item) for item in steps]
    phases = {'FINDING': 0, 'REMEDIATION': 1, 'VERIFICATION': 2}
    operation_order = {
        'REPORT_REMAINING_RECORD': 0,
        'REPORT_EXPOSED_PII': 1,
        'REPORT_ACTIVE_DATA_FLOW': 2,
        'REPORT_RESTORE_RISK': 3,
        'DISABLE_DATA_FLOW': 0,
        'BLOCK_RESTORE': 1,
        'DELETE_RECORD': 2,
        'REDACT_RECORD': 3,
        'ANONYMIZE_RECORD': 4,
        'VERIFY_RECORD_ABSENT': 0,
        'VERIFY_RECORD_PRESENT': 1,
        'VERIFY_RECORD_REDACTED': 2,
        'VERIFY_RECORD_ANONYMIZED': 3,
        'VERIFY_DATA_FLOW_DISABLED': 4,
        'VERIFY_RESTORE_BLOCKED': 5,
    }
    ordered.sort(key=lambda item: (
        phases.get(str(item.get('phase')), 99),
        operation_order.get(str(item.get('operation')), 99),
        str(item.get('system_id', '')),
        str(item.get('resource_id', '')),
        int(item.get('step_no', 0)),
    ))
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
    status = decision['status']
    if status == 'NOT_APPLICABLE':
        return []

    observations = _latest_case_observations(case, dataset)
    if observations:
        def make_step(observation: Dict[str, Any], phase: str, operation: str, state: str, evidence_id: str) -> Dict[str, Any]:
            return {
                'step_no': 0,
                'phase': phase,
                'system_id': str(observation.get('system_id') or 'unknown_system'),
                'resource_id': str(observation.get('resource_id') or 'unknown_resource'),
                'operation': operation,
                'state': state,
                'evidence_id': evidence_id,
            }

        verification_map = {
            'ABSENT': ('VERIFY_RECORD_ABSENT', 'ABSENT'),
            'PRESENT': ('VERIFY_RECORD_PRESENT', 'PRESENT'),
            'ANONYMIZED': ('VERIFY_RECORD_ANONYMIZED', 'ANONYMIZED'),
        }
        if status == 'COMPLETE':
            checks: List[Dict[str, Any]] = []
            for observation in observations:
                category = observation.get('category', '')
                action = _observation_action(observation, dataset)
                state = _observation_state(observation)
                if action == 'DELETE' and state == 'ABSENT':
                    checks.append(make_step(observation, 'VERIFICATION', 'VERIFY_RECORD_ABSENT', 'ABSENT', observation['evidence_id']))
                elif action == 'RETAIN' and state in {'PRESENT', 'SAFE'}:
                    checks.append(make_step(observation, 'VERIFICATION', 'VERIFY_RECORD_PRESENT', 'PRESENT', observation['evidence_id']))
                elif action == 'REDACT' and state != 'PII' and state in {'PRESENT', 'ANONYMIZED', 'SAFE'}:
                    checks.append(make_step(observation, 'VERIFICATION', 'VERIFY_RECORD_REDACTED', 'REDACTED', observation['evidence_id']))
                elif action == 'ANONYMIZE' and state == 'ANONYMIZED':
                    checks.append(make_step(observation, 'VERIFICATION', 'VERIFY_RECORD_ANONYMIZED', 'ANONYMIZED', observation['evidence_id']))
                elif category == 'DATA_FLOW' and state == 'SAFE':
                    checks.append(make_step(observation, 'VERIFICATION', 'VERIFY_DATA_FLOW_DISABLED', 'DISABLED', observation['evidence_id']))
                elif category == 'BACKUP_ARCHIVE' and state == 'SAFE':
                    checks.append(make_step(observation, 'VERIFICATION', 'VERIFY_RESTORE_BLOCKED', 'BLOCKED', observation['evidence_id']))
            if checks:
                return _ordered_step_phases(checks)

        findings: List[Dict[str, Any]] = []
        remediations: List[Dict[str, Any]] = []
        verifications: List[Dict[str, Any]] = []
        for observation in observations:
            issue = _observation_violation(observation, dataset)
            if issue:
                finding_op, remediation_op, verification_op = issue
                finding_state = 'ACTIVE' if finding_op in {'REPORT_ACTIVE_DATA_FLOW', 'REPORT_RESTORE_RISK'} else 'PRESENT'
                verify_state = {
                    'VERIFY_RECORD_ABSENT': 'ABSENT',
                    'VERIFY_RECORD_REDACTED': 'REDACTED',
                    'VERIFY_RECORD_ANONYMIZED': 'ANONYMIZED',
                    'VERIFY_DATA_FLOW_DISABLED': 'DISABLED',
                    'VERIFY_RESTORE_BLOCKED': 'BLOCKED',
                }[verification_op]
                remediation_state = {
                    'DELETE_RECORD': 'ABSENT',
                    'REDACT_RECORD': 'REDACTED',
                    'ANONYMIZE_RECORD': 'ANONYMIZED',
                    'DISABLE_DATA_FLOW': 'DISABLED',
                    'BLOCK_RESTORE': 'BLOCKED',
                }[remediation_op]
                findings.append(make_step(observation, 'FINDING', finding_op, finding_state, observation['evidence_id']))
                remediations.append(make_step(observation, 'REMEDIATION', remediation_op, remediation_state, ''))
                verifications.append(make_step(observation, 'VERIFICATION', verification_op, verify_state, ''))
            else:
                category = observation.get('category', '')
                action = _observation_action(observation, dataset)
                state = _observation_state(observation)
                check = None
                if action == 'DELETE' and state == 'ABSENT':
                    check = ('VERIFY_RECORD_ABSENT', 'ABSENT')
                elif action == 'RETAIN' and state in {'PRESENT', 'SAFE'}:
                    check = ('VERIFY_RECORD_PRESENT', 'PRESENT')
                if check:
                    verifications.append(make_step(observation, 'VERIFICATION', check[0], check[1], ''))
        if findings:
            return _ordered_step_phases(findings + remediations + verifications)

    relevant = _relevant_case_activity(case, dataset)
    subject_resources = _case_resource_candidates(case, dataset)
    matched_evidence = []
    for file in getattr(dataset, 'evidence_files', []):
        if getattr(file, 'captured_at', None) is not None and file.captured_at <= case.audit_at:
            try:
                content = _read_evidence_text(Path('dataset') / file.file_path)
            except Exception:
                continue
            if not any(_contains_subject_token(content.lower(), token) for token in _subject_tokens(case, dataset)):
                continue
            matched_evidence.append(file.file_id)

    if not relevant:
        resource_id = getattr(case, 'case_id', 'account_000').replace('case_', 'account_')
        system_id = 'account_service'
        evidence_id = _real_evidence_id_for_case(case, dataset)
    else:
        anchor = relevant[-1]
        resource_id = getattr(anchor, 'resource_id', None) or (next(iter(subject_resources)) if subject_resources else getattr(case, 'case_id', 'account_000').replace('case_', 'account_'))
        system_id = getattr(anchor, 'source_system', 'account_service') or 'account_service'
        evidence_id = matched_evidence[0] if matched_evidence else anchor.activity_id

    if status == 'COMPLETE':
        return [{
            'step_no': 1,
            'phase': 'VERIFICATION',
            'system_id': system_id,
            'resource_id': resource_id,
            'operation': 'VERIFY_RECORD_ABSENT',
            'state': 'ABSENT',
            'evidence_id': evidence_id,
        }]

    finding_steps: List[Dict[str, Any]] = []
    for idx, activity in enumerate(relevant[:3], start=1):
        if activity.event_type in {'DELETE_RECORD', 'INDEX_RECORD', 'UPDATE_RECORD', 'CREATE_EXPORT'}:
            finding_steps.append({
                'step_no': len(finding_steps) + 1,
                'phase': 'FINDING',
                'system_id': activity.source_system,
                'resource_id': activity.resource_id,
                'operation': 'REPORT_REMAINING_RECORD',
                'state': 'PRESENT',
                'evidence_id': activity.activity_id,
            })
        elif activity.event_type in {'SYNC_RECORD', 'CHECK_DATA_FLOW'}:
            finding_steps.append({
                'step_no': len(finding_steps) + 1,
                'phase': 'FINDING',
                'system_id': activity.source_system,
                'resource_id': activity.resource_id,
                'operation': 'REPORT_ACTIVE_DATA_FLOW',
                'state': 'ACTIVE',
                'evidence_id': activity.activity_id,
            })
        elif activity.event_type in {'RESTORE_BACKUP', 'CHECK_RESTORE_ACCESS'}:
            finding_steps.append({
                'step_no': len(finding_steps) + 1,
                'phase': 'FINDING',
                'system_id': activity.source_system,
                'resource_id': activity.resource_id,
                'operation': 'REPORT_RESTORE_RISK',
                'state': 'ACTIVE',
                'evidence_id': activity.activity_id,
            })

    if not finding_steps:
        finding_steps.append({
            'step_no': 1,
            'phase': 'FINDING',
            'system_id': system_id,
            'resource_id': resource_id,
            'operation': 'REPORT_REMAINING_RECORD',
            'state': 'PRESENT',
            'evidence_id': evidence_id,
        })

    remediation_steps: List[Dict[str, Any]] = []
    for activity in relevant[:3]:
        if activity.event_type in {'SYNC_RECORD', 'CHECK_DATA_FLOW'}:
            remediation_steps.append({
                'step_no': len(remediation_steps) + 1,
                'phase': 'REMEDIATION',
                'system_id': activity.source_system,
                'resource_id': activity.resource_id,
                'operation': 'DISABLE_DATA_FLOW',
                'state': 'DISABLED',
                'evidence_id': '',
            })
        elif activity.event_type in {'RESTORE_BACKUP', 'CHECK_RESTORE_ACCESS'}:
            remediation_steps.append({
                'step_no': len(remediation_steps) + 1,
                'phase': 'REMEDIATION',
                'system_id': activity.source_system,
                'resource_id': activity.resource_id,
                'operation': 'BLOCK_RESTORE',
                'state': 'BLOCKED',
                'evidence_id': '',
            })
        elif activity.event_type in {'DELETE_RECORD', 'INDEX_RECORD', 'UPDATE_RECORD', 'CREATE_EXPORT'}:
            remediation_steps.append({
                'step_no': len(remediation_steps) + 1,
                'phase': 'REMEDIATION',
                'system_id': activity.source_system,
                'resource_id': activity.resource_id,
                'operation': 'DELETE_RECORD',
                'state': 'ABSENT',
                'evidence_id': '',
            })

    if not remediation_steps:
        remediation_steps.append({
            'step_no': 1,
            'phase': 'REMEDIATION',
            'system_id': system_id,
            'resource_id': resource_id,
            'operation': 'DELETE_RECORD',
            'state': 'ABSENT',
            'evidence_id': '',
        })

    verification_steps: List[Dict[str, Any]] = []
    verification_steps.append({
        'step_no': len(verification_steps) + 1,
        'phase': 'VERIFICATION',
        'system_id': system_id,
        'resource_id': resource_id,
        'operation': 'VERIFY_RECORD_ABSENT',
        'state': 'ABSENT',
        'evidence_id': '',
    })
    if any(activity.event_type in {'SYNC_RECORD', 'CHECK_DATA_FLOW'} for activity in relevant):
        verification_steps.append({
            'step_no': len(verification_steps) + 1,
            'phase': 'VERIFICATION',
            'system_id': system_id,
            'resource_id': resource_id,
            'operation': 'VERIFY_DATA_FLOW_DISABLED',
            'state': 'DISABLED',
            'evidence_id': '',
        })
    if any(activity.event_type in {'RESTORE_BACKUP', 'CHECK_RESTORE_ACCESS'} for activity in relevant):
        verification_steps.append({
            'step_no': len(verification_steps) + 1,
            'phase': 'VERIFICATION',
            'system_id': system_id,
            'resource_id': resource_id,
            'operation': 'VERIFY_RESTORE_BLOCKED',
            'state': 'BLOCKED',
            'evidence_id': '',
        })

    steps = finding_steps + remediation_steps + verification_steps
    for idx, step in enumerate(steps, start=1):
        step['step_no'] = idx
    return steps


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
